import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types
import aiosqlite

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8636562924:AAHkgsDbnSmusMp2EXOLNCjUI8nU-uHlNWs"
WEBAPP_URL = "https://hagere-bingo-bot.onrender.com"
ADMIN_IDS = [349952871]
DB_FILE = "bingo.db"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

class TicketPurchase(BaseModel):
    user_id: int
    count: int
    tickets: list[int] = []

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
        # Initialize default game state if not exists
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
        logging.info("Bot started successfully with SQLite DB Persistence.")
    except Exception as e:
        logging.error(f"Startup error: {e}")
    yield
    session = await bot.get_session()
    await session.close()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# --- Telegram Bot Handlers ---
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

@dp.message_handler(commands=['start', 'play'])
async def start_cmd(message: types.Message):
    # Ensure user exists in database
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

# --- Web & API Routes ---
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/admin", response_class=HTMLResponse)
async def serve_admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

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

@app.get("/api/user/{user_id}")
async def get_user_profile(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 500
    return JSONResponse({"balance": balance})

@app.post("/api/buy")
async def buy_tickets(purchase: TicketPurchase):
    async with aiosqlite.connect(DB_FILE) as db:
        # Get user balance
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (purchase.user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                balance = 500
                await db.execute("INSERT INTO users (user_id, balance) VALUES (?, 500)", (purchase.user_id,))
            else:
                balance = row[0]

        cost = purchase.count * 10
        if balance < cost:
            return JSONResponse({"status": "error", "message": f"Insufficient balance! Required: {cost} ETB"}, status_code=400)

        # Get current round
        async with db.execute("SELECT value FROM game_state WHERE key='round_id'") as cursor:
            round_id = (await cursor.fetchone())[0]

        # Deduct balance & record tickets
        new_balance = balance - cost
        await db.execute("UPDATE users SET balance = ?, total_deposits = total_deposits + ? WHERE user_id = ?", (new_balance, cost, purchase.user_id))

        for t_num in purchase.tickets:
            await db.execute(
                "INSERT INTO tickets (round_id, user_id, ticket_number) VALUES (?, ?, ?)",
                (round_id, purchase.user_id, t_num)
            )

        # Update prize pool (+80% of ticket sales)
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
    
    return JSONResponse({
        "status": "success",
        "message": f"Draw completed for Round #{old_round}. Round #{new_round} is now OPEN!",
        "new_round": new_round
    })
