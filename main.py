import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8636562924:AAHb4elV7z3z85uE9hTScby3uBu8LA-WPWw"
WEBAPP_URL = "https://hagere-bingo-bot.onrender.com"
ADMIN_IDS = [349952871]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Clearing previous webhook/polling connections...")
    try:
        # Delete any existing webhook to prevent conflicts
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Start clean polling in background
        asyncio.create_task(dp.start_polling(reset_webhook=True))
        logging.info("Bot polling started cleanly.")
    except Exception as e:
        logging.error(f"Polling startup error ignored: {e}")

    yield
    
    # Close bot session cleanly on shutdown
    session = await bot.get_session()
    await session.close()

app = FastAPI(lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# --- Telegram Bot Commands ---
@dp.message_handler(commands=['start', 'play'])
async def start_cmd(message: types.Message):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(
        text="🎮 Play Bingo (1-200 Tickets)", 
        web_app=types.WebAppInfo(url=WEBAPP_URL)
    ))
    await message.answer(
        "👋 Welcome to **Hagere Bingo Live**!\n\n"
        "Tap below to open the Mini App, pick your ticket (1 to 200), and join active games!", 
        reply_markup=keyboard, 
        parse_mode="Markdown"
    )

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

@dp.message_handler(commands=['help'])
async def help_cmd(message: types.Message):
    await message.answer(
        "❓ **Hagere Bingo Help & Rules**\n\n"
        "1. Tap /play to open the Mini App.\n"
        "2. Pick an open ticket (1 to 200).\n"
        "3. Mark called numbers and hit **BINGO!** to claim the prize pool.\n"
        "4. Deposit/Withdraw via Telebirr or CBE inside the wallet menu.",
        parse_mode="Markdown"
    )

# --- Web Routes ---
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/admin", response_class=HTMLResponse)
async def serve_admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/api/admin/stats")
async def get_admin_stats():
    return JSONResponse({
        "total_users": 128,
        "total_deposits": 14500,
        "pending_withdrawals": 1200,
        "active_game_id": 1024
    })
