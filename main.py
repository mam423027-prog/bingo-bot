import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, types

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = "8636562924:AAHkgsDbnSmusMp2EXOLNCjUI8nU-uHlNWs"
WEBAPP_URL = "https://hagere-bingo-bot.onrender.com"
ADMIN_IDS = [349952871]

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# --- In-Memory Game State ---
game_state = {
    "round_id": 1025,
    "prize_pool": 1500,
    "ticket_price": 10,
    "total_users": 142,
    "pending_withdrawals": 850,
    "total_tickets_sold": 150,
    "status": "open" # open, drawing, finished
}

class TicketPurchase(BaseModel):
    count: int

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        asyncio.create_task(dp.start_polling(reset_webhook=True))
        logging.info("Bot started successfully with Game Engine.")
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
    return templates.TemplateResponse("index.html", {"request": request, "round_id": game_state["round_id"], "prize_pool": game_state["prize_pool"]})

@app.get("/admin", response_class=HTMLResponse)
async def serve_admin(request: Request):
    return templates.TemplateResponse("admin.html", {"request": request})

@app.get("/api/admin/stats")
async def get_admin_stats():
    return JSONResponse({
        "total_users": game_state["total_users"],
        "total_deposits": game_state["total_tickets_sold"] * game_state["ticket_price"],
        "pending_withdrawals": game_state["pending_withdrawals"],
        "active_game_id": game_state["round_id"],
        "prize_pool": game_state["prize_pool"]
    })

@app.post("/api/buy")
async def buy_tickets(purchase: TicketPurchase):
    if game_state["status"] != "open":
        return JSONResponse({"status": "error", "message": "Game round is currently closed for draw."}, status_code=400)
    
    cost = purchase.count * game_state["ticket_price"]
    game_state["total_tickets_sold"] += purchase.count
    game_state["prize_pool"] += int(cost * 0.8) # 80% goes to prize pool
    
    return JSONResponse({
        "status": "success", 
        "message": f"Successfully purchased {purchase.count} tickets for {cost} ETB!",
        "new_prize_pool": game_state["prize_pool"]
    })

@app.post("/api/admin/start-draw")
async def start_draw():
    game_state["status"] = "drawing"
    # Advance to next round after draw initiation
    old_round = game_state["round_id"]
    game_state["round_id"] += 1
    game_state["prize_pool"] = 1000 # Base reset pool for new round
    game_state["status"] = "open"
    
    return JSONResponse({
        "status": "success",
        "message": f"Draw completed for Round #{old_round}. Round #{game_state['round_id']} is now OPEN!",
        "new_round": game_state["round_id"]
    })
