import os
import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from aiogram import Bot, Dispatcher, types

# Setup logging
logging.basicConfig(level=logging.INFO)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
WEBAPP_URL = "https://hagere-bingo-bot.onrender.com"
ADMIN_IDS = [349952871]  # Mohammed Nasir's Admin Telegram ID

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
app = FastAPI()

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
        "Tap the button below to open the Mini App, pick your ticket (1 to 200), and join active games!",
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
        await message.answer("⚠️ Unauthorized access. This command is restricted to platform admins.")

# --- FastAPI Web Routes ---
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

# --- Helper Function for Admin Alerts ---
async def notify_admin(text: str):
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Failed to alert admin {admin_id}: {e}")

