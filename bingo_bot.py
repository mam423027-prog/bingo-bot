import asyncio
import logging
import os
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required")

DB_PATH = os.getenv("DATABASE_PATH", str(Path(__file__).resolve().parent / "bingo.db"))
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip().rstrip("/")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)


def main_menu_keyboard():
    rows = []
    if WEBAPP_URL.startswith("https://"):
        rows.append([InlineKeyboardButton(
            text="🎮 Open Hagere Bingo",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )])
    rows.append([InlineKeyboardButton(text="💰 Check Balance", callback_data="check_balance")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ensure_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (?, 0, 0)",
            (user_id,)
        )
        await db.commit()


@router.message(CommandStart())
async def cmd_start(message: Message):
    if not message.from_user:
        return
    await ensure_user(message.from_user.id)
    if WEBAPP_URL.startswith("https://"):
        message_text = (
            "Welcome to Hagere Bingo!\\n\\n"
            "Tap “Open Hagere Bingo” to play. Deposits are credited only after an administrator "
            "checks your payment receipt and transaction reference."
        )
    else:
        message_text = (
            "Welcome to Hagere Bingo!\\n\\n"
            "The game is not configured yet. Please try again later."
        )
    await message.answer(message_text, reply_markup=main_menu_keyboard())


@router.message(Command("balance"))
async def balance_command(message: Message):
    if not message.from_user:
        return
    await ensure_user(message.from_user.id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (message.from_user.id,)) as cursor:
            row = await cursor.fetchone()
    await message.answer("Your current balance is {} ETB.".format(row[0] if row else 0))


@router.callback_query(F.data == "check_balance")
async def process_check_balance(callback: CallbackQuery):
    user_id = callback.from_user.id
    await ensure_user(user_id)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
    await callback.answer("Balance: {} ETB".format(row[0] if row else 0), show_alert=True)


async def main():
    # main.py owns the database schema; it must initialize the shared DB before the bot starts.
    if not Path(DB_PATH).is_file():
        raise RuntimeError(
            "Shared database is not initialized. Start the web service once before starting the bot service."
        )
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
