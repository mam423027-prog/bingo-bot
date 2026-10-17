import os
import asyncio
import logging
import hashlib
import hmac
import json
import time
import secrets
from pathlib import Path
from urllib.parse import parse_qsl
from contextlib import asynccontextmanager

import aiosqlite
import random
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException, Header, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = os.getenv("DATABASE_PATH", str(BASE_DIR / "bingo.db"))
RECEIPT_DIR = Path(os.getenv("RECEIPT_DIR", str(BASE_DIR / "private_receipts")))
MAX_RECEIPT_BYTES = 5 * 1024 * 1024
ALLOWED_PAYMENT_METHODS = {"Telebirr", "CBE Birr"}
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()

class TicketPurchase(BaseModel):
    count: int = Field(ge=1, le=50)
    tickets: list[int] = Field(min_length=1, max_length=50)

def verify_telegram_init_data(init_data: str) -> dict:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not token or not init_data:
        raise HTTPException(status_code=401, detail="Open this page from the Telegram bot to continue.")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
        received_hash = pairs.pop("hash", None)
        if not received_hash:
            raise ValueError("missing hash")
        auth_date = int(pairs.get("auth_date", "0"))
        if auth_date <= 0 or abs(time.time() - auth_date) > 86400:
            raise HTTPException(status_code=401, detail="Telegram session expired. Reopen the app from Telegram.")
        data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            raise ValueError("invalid hash")
        user = json.loads(pairs["user"])
        user_id = int(user["id"])
        if user_id <= 0:
            raise ValueError("invalid user")
        return {"user_id": user_id, "user": user}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Telegram session.")

def get_user_id(init_data: str | None) -> int:
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication required.")
    return verify_telegram_init_data(init_data)["user_id"]

def require_admin(init_data: str | None) -> int:
    user_id = get_user_id(init_data)
    admin_id = os.getenv("ADMIN_TELEGRAM_ID", "").strip()
    if not admin_id or str(user_id) != admin_id:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user_id

async def ensure_user(db, user_id: int):
    await db.execute(
        "INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (?, 0, 0)",
        (user_id,)
    )

async def init_db():
    Path(DB_FILE).parent.mkdir(parents=True, exist_ok=True)
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0,
            total_deposits INTEGER NOT NULL DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS game_state (
            key TEXT PRIMARY KEY, value INTEGER)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, round_id INTEGER,
            user_id INTEGER, ticket_number INTEGER)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL, payment_method TEXT NOT NULL,
            tx_ref TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            receipt_path TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT)""")
        async with db.execute("PRAGMA table_info(deposits)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        for column, declaration in (
            ("receipt_path", "TEXT"), ("created_at", "TEXT"),
            ("reviewed_at", "TEXT")
        ):
            if column not in columns:
                await db.execute(f"ALTER TABLE deposits ADD COLUMN {column} {declaration}")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_tx_ref ON deposits(tx_ref)")
        defaults = {
            "round_id": 1025, "prize_pool": 1500, "ticket_price": 10,
            "pending_withdrawals": 0
        }
        for key, value in defaults.items():
            await db.execute("INSERT OR IGNORE INTO game_state (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logging.info("Database initialized at %s", DB_FILE)
    yield

app = FastAPI(lifespan=lifespan)

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

@app.get("/api/admin/stats")
async def get_admin_stats(x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    # Only authenticated Telegram users can read admin metrics; public lobby data is exposed separately.
    require_admin(x_telegram_init_data)
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
    return JSONResponse({"total_users": total_users, "total_deposits": total_deposits,
        "pending_withdrawals": pending_withdrawals, "active_game_id": round_id, "prize_pool": prize_pool})

@app.get("/api/lobby")
async def get_lobby_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            round_id = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='prize_pool'") as cursor:
            prize_pool = (await cursor.fetchone())[0]
    return {"active_game_id": round_id, "prize_pool": prize_pool}

@app.post("/api/user")
async def get_user_profile(x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    user_id = get_user_id(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        await ensure_user(db, user_id)
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]
    return {"balance": balance, "user_id": user_id}

@app.post("/api/telebirr/create-order")
async def create_telebirr_order():
    raise HTTPException(status_code=410, detail="Automatic Telebirr checkout is disabled. Use the manual deposit request workflow.")

@app.post("/api/deposit")
async def process_deposit(
    amount: int = Form(...),
    payment_method: str = Form(...),
    tx_ref: str = Form(...),
    receipt: UploadFile = File(...),
    x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")
):
    user_id = get_user_id(x_telegram_init_data)
    if amount < 10 or amount > 100000:
        raise HTTPException(status_code=400, detail="Deposit amount must be between 10 and 100,000 ETB.")
    if payment_method not in ALLOWED_PAYMENT_METHODS:
        raise HTTPException(status_code=400, detail="Unsupported payment method.")
    tx_ref = tx_ref.strip()
    if not tx_ref or len(tx_ref) > 120:
        raise HTTPException(status_code=400, detail="Enter a valid transaction reference.")
    content = await receipt.read(MAX_RECEIPT_BYTES + 1)
    if len(content) > MAX_RECEIPT_BYTES:
        raise HTTPException(status_code=413, detail="Receipt image must be 5 MB or smaller.")
    ext_by_signature = None
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        ext_by_signature = ".png"
    elif content.startswith(b"\xff\xd8\xff"):
        ext_by_signature = ".jpg"
    elif content.startswith((b"GIF87a", b"GIF89a")):
        ext_by_signature = ".gif"
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        ext_by_signature = ".webp"
    if not ext_by_signature:
        raise HTTPException(status_code=400, detail="Upload a valid PNG, JPEG, GIF, or WebP receipt image.")
    safe_name = secrets.token_hex(24) + ext_by_signature
    target = RECEIPT_DIR / safe_name
    try:
        target.write_bytes(content)
        async with aiosqlite.connect(DB_FILE) as db:
            await ensure_user(db, user_id)
            await db.execute(
                "INSERT INTO deposits (user_id, amount, payment_method, tx_ref, status, receipt_path) VALUES (?, ?, ?, ?, 'pending', ?)",
                (user_id, amount, payment_method, tx_ref, str(target))
            )
            await db.commit()
    except aiosqlite.IntegrityError:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="That transaction reference has already been submitted.")
    except Exception:
        target.unlink(missing_ok=True)
        logging.exception("Failed to save deposit request")
        raise HTTPException(status_code=500, detail="Could not submit deposit. Please try again.")
    return {"status": "pending", "message": "Deposit request submitted for admin review. Your balance will update only after approval."}

@app.get("/api/admin/deposits")
async def list_deposits(x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    require_admin(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""SELECT id, user_id, amount, payment_method, tx_ref, status, created_at
            FROM deposits ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 200""") as cursor:
            rows = await cursor.fetchall()
    return {"deposits": [dict(row) for row in rows]}

@app.get("/api/admin/deposits/{deposit_id}/receipt")
async def get_deposit_receipt(deposit_id: int, x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    require_admin(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT receipt_path FROM deposits WHERE id=?", (deposit_id,)) as cursor:
            row = await cursor.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    path = Path(row[0]).resolve()
    if RECEIPT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return FileResponse(path)

@app.post("/api/admin/deposits/{deposit_id}/approve")
async def approve_deposit(deposit_id: int, x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    require_admin(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT user_id, amount, status FROM deposits WHERE id=?", (deposit_id,)) as cursor:
            row = await cursor.fetchone()
        if not row:
            await db.rollback()
            raise HTTPException(status_code=404, detail="Deposit request not found.")
        user_id, amount, status = row
        if status != "pending":
            await db.rollback()
            raise HTTPException(status_code=409, detail="This deposit has already been reviewed.")
        await ensure_user(db, user_id)
        await db.execute("UPDATE deposits SET status='approved', reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (deposit_id,))
        await db.execute("UPDATE users SET balance=balance+?, total_deposits=total_deposits+? WHERE user_id=?", (amount, amount, user_id))
        await db.commit()
    return {"status": "approved", "message": "Deposit approved and balance credited once."}

@app.post("/api/admin/deposits/{deposit_id}/reject")
async def reject_deposit(deposit_id: int, x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    require_admin(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("UPDATE deposits SET status='rejected', reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (deposit_id,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Pending deposit request not found.")
    return {"status": "rejected", "message": "Deposit request rejected."}

@app.post("/api/buy")
async def buy_tickets(purchase: TicketPurchase, x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    user_id = get_user_id(x_telegram_init_data)
    if len(set(purchase.tickets)) != len(purchase.tickets) or purchase.count != len(purchase.tickets):
        raise HTTPException(status_code=400, detail="Ticket count does not match selected tickets.")
    if any(t < 1 or t > 200 for t in purchase.tickets):
        raise HTTPException(status_code=400, detail="Ticket numbers must be between 1 and 200.")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("BEGIN IMMEDIATE")
        await ensure_user(db, user_id)
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='ticket_price'") as cursor:
            price_row = await cursor.fetchone()
        ticket_price = price_row[0] if price_row else 10
        cost = purchase.count * ticket_price
        if balance < cost:
            await db.rollback()
            raise HTTPException(status_code=400, detail=f"Insufficient balance. Required: {cost} ETB.")
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            round_id = (await cursor.fetchone())[0]
        placeholders = ",".join("?" for _ in purchase.tickets)
        async with db.execute(f"SELECT ticket_number FROM tickets WHERE round_id=? AND ticket_number IN ({placeholders})", [round_id, *purchase.tickets]) as cursor:
            existing = {row[0] for row in await cursor.fetchall()}
        if existing:
            await db.rollback()
            raise HTTPException(status_code=409, detail="One or more tickets are already sold in this round.")
        await db.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (cost, user_id))
        for ticket in purchase.tickets:
            await db.execute("INSERT INTO tickets (round_id, user_id, ticket_number) VALUES (?, ?, ?)", (round_id, user_id, ticket))
        async with db.execute("SELECT value FROM game_state WHERE key='prize_pool'") as cursor:
            pool_row = await cursor.fetchone()
        new_pool = (pool_row[0] if pool_row else 0) + int(cost * 0.8)
        await db.execute("UPDATE game_state SET value=? WHERE key='prize_pool'", (new_pool,))
        await db.commit()
    return {"status": "success", "message": f"Successfully purchased {purchase.count} tickets!", "new_balance": balance - cost, "new_prize_pool": new_pool}

@app.post("/api/admin/start-draw")
async def start_draw(x_telegram_init_data: str | None = Header(default=None, alias="X-Telegram-Init-Data")):
    require_admin(x_telegram_init_data)
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            row = await cursor.fetchone()
        old_round = row[0] if row else 1025
        new_round = old_round + 1
        await db.execute("UPDATE game_state SET value=? WHERE key='round_id'", (new_round,))
        await db.execute("UPDATE game_state SET value=1000 WHERE key='prize_pool'")
        await db.commit()
    drawn_number = random.randint(1, 75)
    await manager.broadcast({"type": "number_drawn", "number": drawn_number, "round_id": new_round})
    return {"status": "success", "message": f"Draw started for Round #{new_round}. Number {drawn_number} broadcasted!", "new_round": new_round}
