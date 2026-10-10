import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
import aiosqlite
import random

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8636562924:AAHkgsDbnSmusMp2EXOLNCjUI8nU-uHlNWs"
WEBAPP_URL = "https://hagere-bingo-bot.onrender.com"
ADMIN_IDS = [349952871]
DB_FILE = "bingo.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

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
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        asyncio.create_task(dp.start_polling(reset_webhook=True))
        logging.info("Bot started successfully.")
    except Exception as e:
        logging.error(f"Startup error: {e}")
    yield
    session = await bot.get_session()
    await session.close()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

@dp.message_handler(commands=['admin'])
async def admin_cmd(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(
            text="👑 Open Admin Dashboard", 
            web_app=types.WebAppInfo(url=f"{WEBAPP_URL}/admin")
        ))
        await message.answer("👑 **Admin Access Granted**\nTap below to open your control panel:", reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message.answer("⚠️ Unauthorized access.")

@dp.message_handler(commands=['deposit'])
async def deposit_cmd(message: types.Message):
    await message.answer(
        "💳 **Deposit Funds**\n\n"
        "1️⃣ Send ETB via **Telebirr** or **CBE Birr** to: `0911234567`\n"
        "2️⃣ Open the Mini App and click **+ Deposit Account** to enter your Reference ID.",
        parse_mode="Markdown"
    )

@dp.message_handler(commands=['start', 'play'])
async def start_cmd(message: types.Message):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (?, 500, 0)",
            (message.from_user.id,)
        )
        await db.commit()

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text="🎮 Play Bingo (1-200 Tickets)", 
        web_app=types.WebAppInfo(url=WEBAPP_URL)
    ))
    await message.answer(
        "👋 Welcome to **Hagere Bingo Live**!\n\nTap below to open the Mini App and pick your ticket:", 
        reply_markup=keyboard, 
        parse_mode="Markdown"
    )

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

@app.post("/api/deposit")
async def process_deposit(req: DepositRequest):
    user_id = req.user_id if req.user_id else 349952871
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO deposits (user_id, amount, payment_method, tx_ref, status) VALUES (?, ?, ?, ?, 'completed')",
            (user_id, req.amount, req.payment_method, req.tx_ref)
        )
        await db.execute(
            "UPDATE users SET balance = balance + ?, total_deposits = total_deposits + ? WHERE user_id = ?",
            (req.amount, req.amount, user_id)
        )
        await db.commit()
        
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            new_balance = (await cursor.fetchone())[0]

    return JSONResponse({
        "status": "success",
        "message": f"Successfully deposited {req.amount} ETB via {req.payment_method}!",
        "new_balance": new_balance
    })

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
