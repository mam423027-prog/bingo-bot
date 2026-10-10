import hashlib
import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl

import aiosqlite
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "bingo.db")))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {item.strip() for item in os.getenv("ADMIN_TELEGRAM_IDS", "").split(",") if item.strip()}
ALLOW_DEV_AUTH = os.getenv("ALLOW_UNAUTHENTICATED_DEV", "false").lower() == "true"


class ConnectionManager:
    def __init__(self):
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        stale = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


manager = ConnectionManager()


class TicketPurchase(BaseModel):
    count: int = Field(ge=1, le=50)
    tickets: list[int] = Field(min_length=1, max_length=50)


class DepositRequest(BaseModel):
    amount: int = Field(ge=10, le=1_000_000)
    payment_method: str = Field(min_length=1, max_length=40)
    tx_ref: str = Field(min_length=1, max_length=200)


def verify_telegram_init_data(init_data: str) -> dict:
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Telegram authentication is not configured.")
    if not init_data or len(init_data) > 8192:
        raise HTTPException(status_code=401, detail="Missing or invalid Telegram authentication data.")

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
        received_hash = parsed.pop("hash")
        auth_date = int(parsed["auth_date"])
    except (ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Invalid Telegram authentication data.")

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise HTTPException(status_code=401, detail="Telegram authentication signature is invalid.")
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > 86400:
        raise HTTPException(status_code=401, detail="Telegram authentication data has expired.")

    try:
        user = json.loads(parsed.get("user", "{}"))
        user_id = int(user["id"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Telegram user identity is missing.")
    return {"user_id": user_id, "user": user}


def authenticated_user(init_data: str | None) -> dict:
    if ALLOW_DEV_AUTH and not BOT_TOKEN and not init_data:
        return {"user_id": 349952871, "user": {"id": 349952871}}
    return verify_telegram_init_data(init_data or "")


def require_admin(init_data: str | None) -> dict:
    identity = authenticated_user(init_data)
    if str(identity["user_id"]) not in ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return identity


async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                total_deposits INTEGER NOT NULL DEFAULT 0 CHECK(total_deposits >= 0)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_state (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                ticket_number INTEGER NOT NULL,
                UNIQUE(round_id, ticket_number)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL CHECK(amount > 0),
                payment_method TEXT NOT NULL,
                tx_ref TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(payment_method, tx_ref)
            )
        """)
        defaults = {
            "round_id": 1,
            "prize_pool": 0,
            "ticket_price": 10,
            "pending_withdrawals": 0,
        }
        for key, value in defaults.items():
            await db.execute(
                "INSERT OR IGNORE INTO game_state (key, value) VALUES (?, ?)",
                (key, value),
            )
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialized at %s", DB_FILE)
    yield


app = FastAPI(title="Bingo Bot API", lifespan=lifespan)


@app.get("/health")
async def health():
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:
        logger.exception("Health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/admin", response_class=HTMLResponse)
async def serve_admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def read_game_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_users = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            round_id = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='prize_pool'") as cursor:
            prize_pool = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='pending_withdrawals'") as cursor:
            pending_withdrawals = (await cursor.fetchone())[0]
        async with db.execute("SELECT COALESCE(SUM(total_deposits), 0) FROM users") as cursor:
            total_deposits = (await cursor.fetchone())[0]
    return {
        "total_users": total_users,
        "total_deposits": total_deposits,
        "pending_withdrawals": pending_withdrawals,
        "active_game_id": round_id,
        "prize_pool": prize_pool,
    }


@app.get("/api/lobby/stats")
async def get_lobby_stats():
    stats = await read_game_stats()
    return {"active_game_id": stats["active_game_id"], "prize_pool": stats["prize_pool"]}


@app.get("/api/admin/stats")
async def get_admin_stats(x_telegram_init_data: str | None = Header(default=None)):
    require_admin(x_telegram_init_data)
    return JSONResponse(await read_game_stats())


@app.post("/api/user")
async def get_user_profile(x_telegram_init_data: str | None = Header(default=None)):
    identity = authenticated_user(x_telegram_init_data)
    user_id = identity["user_id"]
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (?, 0, 0)",
            (user_id,),
        )
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    return {"balance": row[0], "user_id": user_id}


@app.post("/api/deposit")
async def process_deposit(
    req: DepositRequest,
    x_telegram_init_data: str | None = Header(default=None),
):
    # Never credit a balance based only on a client-provided transaction reference.
    # Integrate and verify an official payment-provider callback before enabling credits.
    identity = authenticated_user(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (?, 0, 0)",
            (identity["user_id"],),
        )
        await db.execute(
            """INSERT OR IGNORE INTO deposits
               (user_id, amount, payment_method, tx_ref, status)
               VALUES (?, ?, ?, ?, 'pending')""",
            (identity["user_id"], req.amount, req.payment_method, req.tx_ref),
        )
        await db.commit()
    raise HTTPException(
        status_code=503,
        detail="Deposits are temporarily unavailable until payment verification is configured. No funds were credited.",
    )


@app.post("/api/buy")
async def buy_tickets(
    purchase: TicketPurchase,
    x_telegram_init_data: str | None = Header(default=None),
):
    identity = authenticated_user(x_telegram_init_data)
    user_id = identity["user_id"]
    if purchase.count != len(purchase.tickets):
        raise HTTPException(status_code=400, detail="Ticket count must match the selected ticket list.")
    if len(set(purchase.tickets)) != len(purchase.tickets):
        raise HTTPException(status_code=400, detail="Duplicate ticket numbers are not allowed.")
    if any(ticket < 1 or ticket > 200 for ticket in purchase.tickets):
        raise HTTPException(status_code=400, detail="Ticket numbers must be between 1 and 200.")

    cost = purchase.count * 10
    async with aiosqlite.connect(DB_FILE, timeout=10) as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (?, 0, 0)",
                (user_id,),
            )
            async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
                round_row = await cursor.fetchone()
            round_id = round_row[0]
            placeholders = ",".join("?" for _ in purchase.tickets)
            async with db.execute(
                f"SELECT ticket_number FROM tickets WHERE round_id=? AND ticket_number IN ({placeholders})",
                [round_id, *purchase.tickets],
            ) as cursor:
                sold = [row[0] for row in await cursor.fetchall()]
            if sold:
                raise HTTPException(status_code=409, detail=f"These tickets are already sold: {sold}")
            async with db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)) as cursor:
                balance = (await cursor.fetchone())[0]
            if balance < cost:
                raise HTTPException(status_code=400, detail=f"Insufficient balance. Required: {cost} ETB.")
            new_balance = balance - cost
            await db.execute("UPDATE users SET balance=? WHERE user_id=?", (new_balance, user_id))
            await db.executemany(
                "INSERT INTO tickets (round_id, user_id, ticket_number) VALUES (?, ?, ?)",
                [(round_id, user_id, ticket) for ticket in purchase.tickets],
            )
            async with db.execute("SELECT value FROM game_state WHERE key='prize_pool'") as cursor:
                pool_row = await cursor.fetchone()
            new_pool = pool_row[0] + int(cost * 0.8)
            await db.execute("UPDATE game_state SET value=? WHERE key='prize_pool'", (new_pool,))
            await db.commit()
        except Exception:
            await db.rollback()
            raise

    return {
        "status": "success",
        "message": f"Successfully purchased {purchase.count} tickets.",
        "new_balance": new_balance,
        "new_prize_pool": new_pool,
    }


@app.post("/api/admin/start-draw")
async def start_draw(x_telegram_init_data: str | None = Header(default=None)):
    require_admin(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            old_round = (await cursor.fetchone())[0]
        new_round = old_round + 1
        await db.execute("UPDATE game_state SET value=? WHERE key='round_id'", (new_round,))
        await db.execute("UPDATE game_state SET value=1000 WHERE key='prize_pool'")
        await db.commit()

    import random
    drawn_number = random.randint(1, 75)
    await manager.broadcast({"type": "number_drawn", "number": drawn_number, "round_id": new_round})
    return {
        "status": "success",
        "message": f"Draw started for Round #{new_round}. Number {drawn_number} broadcasted.",
        "new_round": new_round,
    }
