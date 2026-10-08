import asyncio
import json
import random
import logging
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
import aiosqlite

# Enable logging
logging.basicConfig(level=logging.INFO)

# Configuration
BOT_TOKEN = "8636562924:AAHb4elV7z3z85uE9hTScby3uBu8LA-WPWw"  # Replace with your token from @BotFather
DB_PATH = "bingo.db"

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# --- Helper Functions ---

async def init_db():
    """Ensure essential tables exist on startup."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            balance REAL DEFAULT 100.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS game_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_price REAL DEFAULT 10.0,
            status TEXT DEFAULT 'WAITING',
            drawn_numbers TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            game_id INTEGER NOT NULL,
            card_numbers TEXT NOT NULL,
            status TEXT DEFAULT 'ACTIVE',
            FOREIGN KEY (user_id) REFERENCES users(telegram_id),
            FOREIGN KEY (game_id) REFERENCES game_rounds(id)
        );
        """)
        await db.commit()

def generate_bingo_card():
    """Generate a standard 5x5 Bingo matrix (B: 1-15, I: 16-30, N: 31-45, G: 46-60, O: 61-75)."""
    card = {
        'B': random.sample(range(1, 16), 5),
        'I': random.sample(range(16, 31), 5),
        'N': random.sample(range(31, 46), 5),
        'G': random.sample(range(46, 61), 5),
        'O': random.sample(range(61, 76), 5)
    }
    card['N'][2] = "FREE"  # Center free space
    return card

# --- Keyboards ---

def main_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Buy Ticket (10 Coins)", callback_data="buy_ticket")],
        [InlineKeyboardButton(text="💰 Check Balance", callback_data="check_balance")],
        [InlineKeyboardButton(text="📜 My Tickets", callback_data="my_tickets")]
    ])

# --- Handlers ---

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (telegram_id, username) VALUES (?, ?) ON CONFLICT(telegram_id) DO UPDATE SET username = ?",
            (user_id, username, username)
        )
        await db.commit()

    await message.answer(
        f"👋 Welcome to Telegram Bingo, **{username}**!\n\n"
        "Get ready to play! You have been credited **100 welcome coins**.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

@router.callback_query(F.data == "check_balance")
async def process_check_balance(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            balance = row[0] if row else 0.0

    await callback.answer()
    await callback.message.edit_text(
        f"💵 **Your Current Balance:** `{balance:.2f}` Coins",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

@router.callback_query(F.data == "buy_ticket")
async def process_buy_ticket(callback: CallbackQuery):
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        # Check balance
        async with db.execute("SELECT balance FROM users WHERE telegram_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if not row or row[0] < 10.0:
                await callback.answer("❌ Insufficient balance! You need 10 coins.", show_alert=True)
                return

        # Fetch or create active game round
        async with db.execute("SELECT id FROM game_rounds WHERE status = 'WAITING' LIMIT 1") as cursor:
            game = await cursor.fetchone()
            if game:
                game_id = game[0]
            else:
                cursor = await db.execute("INSERT INTO game_rounds (ticket_price) VALUES (10.0)")
                game_id = cursor.lastrowid

        # Generate ticket and deduct balance
        card = generate_bingo_card()
        card_json = json.dumps(card)

        await db.execute("UPDATE users SET balance = balance - 10.0 WHERE telegram_id = ?", (user_id,))
        await db.execute(
            "INSERT INTO tickets (user_id, game_id, card_numbers) VALUES (?, ?, ?)",
            (user_id, game_id, card_json)
        )
        await db.commit()

    # Format output card for user
    formatted_card = (
        f"🎯 **Bingo Ticket Purchased for Game #{game_id}!**\n\n"
        f"` B  |  I  |  N  |  G  |  O `\n"
        f"`---------------------------`\n"
    )
    for i in range(5):
        row_str = f"{card['B'][i]:2} | {card['I'][i]:2} | {str(card['N'][i]):4} | {card['G'][i]:2} | {card['O'][i]:2}"
        formatted_card += f"`{row_str}`\n"

    await callback.answer("✅ Ticket purchased successfully!")
    await callback.message.edit_text(
        formatted_card,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

@router.callback_query(F.data == "my_tickets")
async def process_my_tickets(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, game_id, status FROM tickets WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (user_id,)
        ) as cursor:
            tickets = await cursor.fetchall()

    if not tickets:
        msg = "🎟️ You have no active or recent tickets."
    else:
        msg = "🎟️ **Your Recent Tickets:**\n\n"
        for t in tickets:
            msg += f"• **Ticket #{t[0]}** | Game #{t[1]} | Status: `{t[2]}`\n"

    await callback.answer()
    await callback.message.edit_text(msg, parse_mode="Markdown", reply_markup=main_menu_keyboard())

# --- Startup Runner ---

async def main():
    await init_db()
    print("🚀 Bingo Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

