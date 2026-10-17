import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import aiosqlite
import random

logging.basicConfig(level=logging.INFO)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "bingo.db")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

# Manual payment mode: no Telebirr merchant API credentials or private key are used.

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass

manager = ConnectionManager()

class UserAuth(BaseModel):
    user_id: int = 349952871

class TicketPurchase(BaseModel):
    user_id: int = 349952871
    count: int
    tickets: list[int] = []

class DepositRequest(BaseModel):
    user_id: int = 349952871
    amount: int
    payment_method: str
    tx_ref: str

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 500,
                total_deposits INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_state (
                key TEXT PRIMARY KEY,
                value INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id INTEGER,
                user_id INTEGER,
                ticket_number INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                payment_method TEXT,
                tx_ref TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        async with db.execute("SELECT value FROM game_state WHERE key = 'round_id'") as cursor:
            row = await cursor.fetchone()
            if not row:
                await db.execute("INSERT INTO game_state (key, value) VALUES ('round_id', 1025)")
                await db.execute("INSERT INTO game_state (key, value) VALUES ('prize_pool', 1500)")
                await db.execute("INSERT INTO game_state (key, value) VALUES ('ticket_price', 10)")
                await db.execute("INSERT INTO game_state (key, value) VALUES ('pending_withdrawals', 850)")
                await db.commit()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logging.info(f"Database initialized successfully at absolute path: {DB_FILE}")
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
async def get_admin_stats():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total_users = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            round_id = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='prize_pool'") as cursor:
            prize_pool = (await cursor.fetchone())[0]
        async with db.execute("SELECT value FROM game_state WHERE key='pending_withdrawals'") as cursor:
            pending_withdrawals = (await cursor.fetchone())[0]
        async with db.execute("SELECT SUM(total_deposits) FROM users") as cursor:
            row = await cursor.fetchone()
            total_deposits = row[0] if row[0] else 0

    return JSONResponse({
        "total_users": total_users,
        "total_deposits": total_deposits,
        "pending_withdrawals": pending_withdrawals,
        "active_game_id": round_id,
        "prize_pool": prize_pool
    })

@app.post("/api/user")
async def get_user_profile(auth: UserAuth):
    user_id = auth.user_id if auth.user_id else 349952871
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (?, 500, 0)",
            (user_id,)
        )
        await db.commit()
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 500
            
    return JSONResponse({"balance": balance, "user_id": user_id})

@app.post("/api/telebirr/create-order")
async def create_telebirr_order(req: DepositRequest):
    # Automatic checkout is intentionally unavailable in manual-payment mode.
    # Players should submit a manual deposit request for admin review instead.
    raise HTTPException(
        status_code=410,
        detail="Automatic Telebirr checkout is disabled. Use the manual deposit request workflow."
    )

@app.post("/api/deposit")
async def process_deposit(req: DepositRequest):
    # Never credit a balance based on a client-submitted reference. This endpoint
    # remains disabled until a server-side Telebirr verification flow is available.
    raise HTTPException(
        status_code=403,
        detail="Deposits require verified payment confirmation. Manual client-submitted deposits are disabled."
    )

@app.post("/api/buy")
async def buy_tickets(purchase: TicketPurchase):
    user_id = purchase.user_id if purchase.user_id else 349952871
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 500

        cost = purchase.count * 10
        if balance < cost:
            return JSONResponse({"status": "error", "message": f"Insufficient balance! Required: {cost} ETB"}, status_code=400)

        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            round_id = (await cursor.fetchone())[0]

        new_balance = balance - cost
        await db.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))

        for t_num in purchase.tickets:
            await db.execute(
                "INSERT INTO tickets (round_id, user_id, ticket_number) VALUES (?, ?, ?)",
                (round_id, user_id, t_num)
            )

        async with db.execute("SELECT value FROM game_state WHERE key='prize_pool'") as cursor:
            current_pool = (await cursor.fetchone())[0]
        
        new_pool = current_pool + int(cost * 0.8)
        await db.execute("UPDATE game_state SET value = ? WHERE key='prize_pool'", (new_pool,))
        await db.commit()

    return JSONResponse({
        "status": "success", 
        "message": f"Successfully purchased {purchase.count} tickets!",
        "new_balance": new_balance,
        "new_prize_pool": new_pool
    })

@app.post("/api/admin/start-draw")
async def start_draw():
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            old_round = (await cursor.fetchone())[0]
        
        new_round = old_round + 1
        await db.execute("UPDATE game_state SET value = ? WHERE key='round_id'", (new_round,))
        await db.execute("UPDATE game_state SET value = 1000 WHERE key='prize_pool'", ())
        await db.commit()

    drawn_number = random.randint(1, 75)
    await manager.broadcast({"type": "number_drawn", "number": drawn_number, "round_id": new_round})
    
    return JSONResponse({
        "status": "success",
        "message": f"Draw started for Round #{new_round}. Number {drawn_number} broadcasted!",
        "new_round": new_round
    })
