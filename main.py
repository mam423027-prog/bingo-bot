import asyncio
import json
import random
import time
from pathlib import Path
from typing import List, Optional

import aiosqlite
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn

# --- CONFIGURATION ---
BOT_TOKEN = "8636562924:AAHb4elV7z3z85uE9hTScby3uBu8LA-WPWw"  # Replace with your actual BotFather token
WEBAPP_URL = "https://continue-jewellery-book-cons.trycloudflare.com"
DB_PATH = "bingo.db"
ADMIN_ID = 349952871

# Local Payment Account Details
PAYMENT_INFO = {
    "telebirr": {
        "name": "Telebirr",
        "number": "0912345678",
        "account_holder": "Mohammed Nasir"
    },
    "cbe": {
        "name": "CBE Birr / Commercial Bank of Ethiopia",
        "number": "1000123456789",
        "account_holder": "Mohammed Nasir"
    }
}

# --- APPLICATION SETUP ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

WAITING_TIME = 20
CALL_INTERVAL = 3

# --- FSM STATES ---
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_balance_change = State()
    waiting_for_broadcast = State()

class DepositStates(StatesGroup):
    waiting_for_amount = State()
    waiting_for_tx_ref = State()

class WithdrawStates(StatesGroup):
    waiting_for_method = State()
    waiting_for_account = State()
    waiting_for_amount = State()

# --- DATABASE INITIALIZATION ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance REAL DEFAULT 100.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS game_rounds (
                round_id INTEGER PRIMARY KEY AUTOINCREMENT,
                drawn_numbers TEXT DEFAULT '[]',
                status TEXT DEFAULT 'waiting',
                start_time INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                round_id INTEGER,
                matrix TEXT,
                status TEXT DEFAULT 'active'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                type TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS deposits (
                deposit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                method TEXT,
                tx_ref TEXT,
                status TEXT DEFAULT 'pending',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                withdraw_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount REAL,
                method TEXT,
                account TEXT,
                status TEXT DEFAULT 'pending',
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        async with db.execute("SELECT round_id FROM game_rounds WHERE status IN ('waiting', 'active')") as cursor:
            if not await cursor.fetchone():
                now = int(time.time())
                await db.execute("INSERT INTO game_rounds (drawn_numbers, status, start_time) VALUES ('[]', 'waiting', ?)", (now + WAITING_TIME,))
        await db.commit()

def generate_bingo_card() -> List[List[int]]:
    cols = [
        random.sample(range(1, 16), 5),
        random.sample(range(16, 31), 5),
        random.sample(range(31, 46), 5),
        random.sample(range(46, 61), 5),
        random.sample(range(61, 76), 5)
    ]
    matrix = []
    for r in range(5):
        row = []
        for c in range(5):
            if r == 2 and c == 2: row.append(0)
            else: row.append(cols[c][r])
        matrix.append(row)
    return matrix

async def register_user_db(user_id: int, username: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)) as cursor:
            if not await cursor.fetchone():
                await db.execute(
                    "INSERT INTO users (user_id, username, balance) VALUES (?, ?, 100.0)",
                    (user_id, username or f"user_{user_id}")
                )
                await db.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, 100.0, 'welcome_bonus')", (user_id,))
                await db.commit()

# --- TIMED GAME LOOP ENGINE ---
async def game_loop():
    while True:
        await asyncio.sleep(1)
        now = int(time.time())
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT round_id, drawn_numbers, status, start_time FROM game_rounds WHERE status IN ('waiting', 'active') ORDER BY round_id DESC LIMIT 1") as cursor:
                row = await cursor.fetchone()
                if not row:
                    await db.execute("INSERT INTO game_rounds (drawn_numbers, status, start_time) VALUES ('[]', 'waiting', ?)", (now + WAITING_TIME,))
                    await db.commit()
                    continue

                round_id, drawn_json, status, start_time = row
                
                if status == 'waiting':
                    if now >= start_time:
                        await db.execute("UPDATE game_rounds SET status = 'active' WHERE round_id = ?", (round_id,))
                        await db.commit()
                
                elif status == 'active':
                    drawn = json.loads(drawn_json)
                    if len(drawn) < 75:
                        remaining = [n for n in range(1, 76) if n not in drawn]
                        drawn.append(random.choice(remaining))
                        await db.execute("UPDATE game_rounds SET drawn_numbers = ? WHERE round_id = ?", (json.dumps(drawn), round_id))
                        await db.commit()
                        await asyncio.sleep(CALL_INTERVAL - 1)
                    else:
                        await db.execute("UPDATE game_rounds SET status = 'finished' WHERE round_id = ?", (round_id,))
                        await db.execute("INSERT INTO game_rounds (drawn_numbers, status, start_time) VALUES ('[]', 'waiting', ?)", (now + WAITING_TIME,))
                        await db.commit()

# --- API MODELS ---
class BuyTicketReq(BaseModel):
    user_id: int
    username: Optional[str] = None
    quantity: int = 1

class ClaimBingoReq(BaseModel):
    user_id: int
    ticket_id: int

# --- API ENDPOINTS ---
@app.get("/")
async def serve_miniapp(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/user/{user_id}")
async def get_user(user_id: int, username: Optional[str] = None):
    await register_user_db(user_id, username)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, username FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return {"user_id": user_id, "balance": row[0], "username": row[1]}

@app.get("/api/game/current")
async def get_current_game():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT round_id, drawn_numbers, status, start_time FROM game_rounds WHERE status IN ('waiting', 'active') ORDER BY round_id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                round_id, drawn_json, status, start_time = row
                time_left = max(0, start_time - now)
                return {
                    "round_id": round_id,
                    "drawn_numbers": json.loads(drawn_json),
                    "status": status,
                    "time_left": time_left
                }
            return {"status": "none"}

@app.post("/api/ticket/buy")
async def buy_ticket(req: BuyTicketReq):
    await register_user_db(req.user_id, req.username)
    cost = req.quantity * 10.0

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (req.user_id,)) as cursor:
            balance = (await cursor.fetchone())[0]

        if balance < cost:
            return {"error": f"Insufficient balance! {req.quantity} card(s) cost ETB {cost:.2f}."}

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (cost, req.user_id))
        await db.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'ticket_purchase')", (req.user_id, -cost))

        async with db.execute("SELECT round_id FROM game_rounds WHERE status IN ('waiting', 'active') ORDER BY round_id DESC LIMIT 1") as cursor:
            round_id = (await cursor.fetchone())[0]

        created_tickets = []
        for _ in range(req.quantity):
            matrix = generate_bingo_card()
            c = await db.execute(
                "INSERT INTO tickets (user_id, round_id, matrix) VALUES (?, ?, ?)",
                (req.user_id, round_id, json.dumps(matrix))
            )
            created_tickets.append({"ticket_id": c.lastrowid, "matrix": matrix})

        await db.commit()
        return {"tickets": created_tickets}

@app.post("/api/ticket/claim")
async def claim_bingo(req: ClaimBingoReq):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT matrix, round_id, status FROM tickets WHERE ticket_id = ?", (req.ticket_id,)) as cursor:
            t_row = await cursor.fetchone()
            if not t_row or t_row[2] != 'active':
                return {"won": False, "reason": "Invalid or already claimed ticket."}

            matrix, round_id = json.loads(t_row[0]), t_row[1]

        async with db.execute("SELECT drawn_numbers FROM game_rounds WHERE round_id = ?", (round_id,)) as cursor:
            drawn_set = set(json.loads((await cursor.fetchone())[0]))
            drawn_set.add(0)

        lines = []
        for r in range(5): lines.append([matrix[r][c] for c in range(5)])
        for c in range(5): lines.append([matrix[r][c] for r in range(5)])
        lines.append([matrix[i][i] for i in range(5)])
        lines.append([matrix[i][4 - i] for i in range(5)])

        if any(all(num in drawn_set for num in line) for line in lines):
            prize = 50.0
            await db.execute("UPDATE tickets SET status = 'claimed' WHERE ticket_id = ?", (req.ticket_id,))
            await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (req.user_id,))
            await db.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'bingo_win')", (req.user_id, prize))
            await db.commit()
            return {"won": True, "prize": prize}
        else:
            return {"won": False, "reason": "No valid Bingo line found yet."}

# --- BOT KEYBOARDS ---
def get_admin_dashboard_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Platform Stats", callback_data="admin_stats"),
         InlineKeyboardButton(text="🔍 Lookup User", callback_data="admin_user_search")],
        [InlineKeyboardButton(text="💳 Adjust Balance", callback_data="admin_adjust_bal"),
         InlineKeyboardButton(text="📜 Recent Logs", callback_data="admin_logs")],
        [InlineKeyboardButton(text="📢 Broadcast Message", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🎮 Launch Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])

# --- BOT HANDLERS ---
@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await register_user_db(message.from_user.id, message.from_user.username)
    
    if message.from_user.id == ADMIN_ID:
        text = f"👑 **Welcome Admin {message.from_user.first_name}!**\n\nUse the control panel below to manage your Bingo platform:"
        await message.answer(text, parse_mode="Markdown", reply_markup=get_admin_dashboard_kb())
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Deposit Funds", callback_data="cmd_deposit_start"),
             InlineKeyboardButton(text="💸 Withdraw Funds", callback_data="cmd_withdraw_start")],
            [InlineKeyboardButton(text="🎮 Open Bingo Mini App", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
        await message.answer(f"👋 Welcome {message.from_user.first_name}!\nTap below to play, deposit, or withdraw:", reply_markup=kb)

@router.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Unauthorized access.")
    await message.answer("🛠️ **Admin Control Dashboard**", parse_mode="Markdown", reply_markup=get_admin_dashboard_kb())

# --- USER DEPOSIT FLOW ---
@router.message(Command("deposit"))
@router.callback_query(F.data == "cmd_deposit_start")
async def cmd_deposit(event: types.Message | types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Telebirr", callback_data="dep_telebirr"),
         InlineKeyboardButton(text="🏦 CBE Birr / Bank", callback_data="dep_cbe")]
    ])
    msg = "💳 **Choose your deposit method:**"
    if isinstance(event, types.CallbackQuery):
        await event.message.answer(msg, parse_mode="Markdown", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(msg, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("dep_"))
async def process_deposit_choice(call: types.CallbackQuery, state: FSMContext):
    method_key = call.data.replace("dep_", "")
    info = PAYMENT_INFO.get(method_key)
    
    await state.update_data(payment_method=info['name'])
    await state.set_state(DepositStates.waiting_for_amount)
    
    msg = (
        f"📱 **{info['name']} Deposit**\n\n"
        f"▪️ **Account/Number:** `{info['number']}`\n"
        f"▪️ **Account Holder:** `{info['account_holder']}`\n\n"
        "👉 **Step 1:** Transfer the funds to the account above.\n"
        "👉 **Step 2:** Send the **amount in ETB** you transferred:"
    )
    await call.message.edit_text(msg, parse_mode="Markdown")

@router.message(DepositStates.waiting_for_amount)
async def process_deposit_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        if amount <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Please enter a valid positive number for the amount.")

    await state.update_data(deposit_amount=amount)
    await state.set_state(DepositStates.waiting_for_tx_ref)
    await message.answer("✍️ **Step 3:** Send the **Transaction Reference Number** (e.g., `FT24029...` or `TX12345`):")

@router.message(DepositStates.waiting_for_tx_ref)
async def process_deposit_tx_ref(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("deposit_amount")
    method = data.get("payment_method")
    tx_ref = message.text.strip()
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO deposits (user_id, amount, method, tx_ref) VALUES (?, ?, ?, ?)",
            (user_id, amount, method, tx_ref)
        )
        deposit_id = cursor.lastrowid
        await db.commit()

    await state.clear()
    await message.answer("✅ **Deposit Request Submitted!**\nYour transaction is under admin verification. Balance will be updated upon approval.")

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"app_dep_{deposit_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"rej_dep_{deposit_id}")
        ]
    ])
    
    admin_msg = (
        "📥 **NEW DEPOSIT REQUEST**\n\n"
        f"👤 **User ID:** `{user_id}` (@{message.from_user.username or 'N/A'})\n"
        f"💵 **Amount:** `{amount:.2f} ETB`\n"
        f"💳 **Method:** `{method}`\n"
        f"🧾 **Tx Reference:** `{tx_ref}`\n"
        f"🆔 **Deposit ID:** `{deposit_id}`"
    )
    await bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown", reply_markup=admin_kb)

@router.callback_query(F.data.startswith("app_dep_"))
async def approve_deposit(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    deposit_id = int(call.data.replace("app_dep_", ""))

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, amount, status FROM deposits WHERE deposit_id = ?", (deposit_id,)) as c:
            row = await c.fetchone()
            if not row or row[2] != 'pending':
                return await call.answer("⚠️ Transaction already processed.", show_alert=True)

            user_id, amount, _ = row

        await db.execute("UPDATE deposits SET status = 'approved' WHERE deposit_id = ?", (deposit_id,))
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'deposit')", (user_id, amount))
        await db.commit()

    await call.message.edit_text(f"{call.message.text}\n\n✅ **APPROVED by Admin**", parse_mode="Markdown")
    try:
        await bot.send_message(user_id, f"🎉 **Deposit Approved!**\n`{amount:.2f} ETB` has been added to your wallet.", parse_mode="Markdown")
    except Exception:
        pass

@router.callback_query(F.data.startswith("rej_dep_"))
async def reject_deposit(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    deposit_id = int(call.data.replace("rej_dep_", ""))

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, status FROM deposits WHERE deposit_id = ?", (deposit_id,)) as c:
            row = await c.fetchone()
            if not row or row[1] != 'pending':
                return await call.answer("⚠️ Transaction already processed.", show_alert=True)

            user_id = row[0]

        await db.execute("UPDATE deposits SET status = 'rejected' WHERE deposit_id = ?", (deposit_id,))
        await db.commit()

    await call.message.edit_text(f"{call.message.text}\n\n❌ **REJECTED by Admin**", parse_mode="Markdown")
    try:
        await bot.send_message(user_id, "❌ **Deposit Rejected.**\nYour transaction reference could not be verified.", parse_mode="Markdown")
    except Exception:
        pass

# --- USER WITHDRAWAL FLOW ---
@router.message(Command("withdraw"))
@router.callback_query(F.data == "cmd_withdraw_start")
async def cmd_withdraw(event: types.Message | types.CallbackQuery, state: FSMContext):
    user_id = event.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as c:
            row = await c.fetchone()
            balance = row[0] if row else 0.0

    if balance <= 0:
        msg = f"⚠️ Your current balance is `0.00 ETB`. You need funds to request a withdrawal."
        if isinstance(event, types.CallbackQuery):
            await event.message.answer(msg, parse_mode="Markdown")
            return await event.answer()
        return await event.answer(msg, parse_mode="Markdown")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Telebirr", callback_data="wdr_Telebirr"),
         InlineKeyboardButton(text="🏦 CBE Birr / Bank", callback_data="wdr_CBE")]
    ])
    msg = f"💸 **Withdrawal Request**\n\nYour Available Balance: `{balance:.2f} ETB`\n\nChoose payout method:"
    if isinstance(event, types.CallbackQuery):
        await event.message.answer(msg, parse_mode="Markdown", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(msg, parse_mode="Markdown", reply_markup=kb)

@router.callback_query(F.data.startswith("wdr_"))
async def process_withdraw_method(call: types.CallbackQuery, state: FSMContext):
    method = call.data.replace("wdr_", "")
    await state.update_data(withdraw_method=method)
    await state.set_state(WithdrawStates.waiting_for_account)
    await call.message.edit_text(f"📱 Selected Method: **{method}**\n\n👉 Send your **Phone/Account Number** to receive the payout:")

@router.message(WithdrawStates.waiting_for_account)
async def process_withdraw_account(message: types.Message, state: FSMContext):
    account_num = message.text.strip()
    await state.update_data(withdraw_account=account_num)
    
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as c:
            balance = (await c.fetchone())[0]

    await state.set_state(WithdrawStates.waiting_for_amount)
    await message.answer(f"💵 Max available: `{balance:.2f} ETB`\n\n👉 Send the **amount in ETB** you wish to withdraw:")

@router.message(WithdrawStates.waiting_for_amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        amount = float(message.text.strip())
        if amount <= 0: raise ValueError
    except ValueError:
        return await message.answer("❌ Please enter a valid positive number for amount.")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as c:
            balance = (await c.fetchone())[0]

    if amount > balance:
        return await message.answer(f"❌ Insufficient balance! Your current balance is `{balance:.2f} ETB`.")

    data = await state.get_data()
    method = data.get("withdraw_method")
    account = data.get("withdraw_account")

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO withdrawals (user_id, amount, method, account) VALUES (?, ?, ?, ?)",
            (user_id, amount, method, account)
        )
        withdraw_id = cursor.lastrowid
        await db.commit()

    await state.clear()
    await message.answer("✅ **Withdrawal Request Submitted!**\nYour payout request is being processed by Admin.")

    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve & Paid", callback_data=f"app_wdr_{withdraw_id}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"rej_wdr_{withdraw_id}")
        ]
    ])
    
    admin_msg = (
        "📤 **NEW WITHDRAWAL REQUEST**\n\n"
        f"👤 **User ID:** `{user_id}` (@{message.from_user.username or 'N/A'})\n"
        f"💵 **Amount:** `{amount:.2f} ETB`\n"
        f"💳 **Method:** `{method}`\n"
        f"📱 **Account/Phone:** `{account}`\n"
        f"🆔 **Withdrawal ID:** `{withdraw_id}`"
    )
    await bot.send_message(ADMIN_ID, admin_msg, parse_mode="Markdown", reply_markup=admin_kb)

@router.callback_query(F.data.startswith("app_wdr_"))
async def approve_withdrawal(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    withdraw_id = int(call.data.replace("app_wdr_", ""))

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, amount, status, account, method FROM withdrawals WHERE withdraw_id = ?", (withdraw_id,)) as c:
            row = await c.fetchone()
            if not row or row[2] != 'pending':
                return await call.answer("⚠️ Request already processed.", show_alert=True)

            user_id, amount, _, account, method = row

        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as c:
            user_bal = (await c.fetchone())[0]

        if user_bal < amount:
            return await call.answer("❌ User no longer has enough balance!", show_alert=True)

        await db.execute("UPDATE withdrawals SET status = 'approved' WHERE withdraw_id = ?", (withdraw_id,))
        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (amount, user_id))
        await db.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'withdrawal')", (user_id, -amount))
        await db.commit()

    await call.message.edit_text(f"{call.message.text}\n\n✅ **APPROVED & DEDUCTED by Admin**", parse_mode="Markdown")
    try:
        await bot.send_message(user_id, f"🎉 **Withdrawal Processed!**\n`{amount:.2f} ETB` sent to your {method} account (`{account}`).", parse_mode="Markdown")
    except Exception:
        pass

@router.callback_query(F.data.startswith("rej_wdr_"))
async def reject_withdrawal(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    withdraw_id = int(call.data.replace("rej_wdr_", ""))

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, status FROM withdrawals WHERE withdraw_id = ?", (withdraw_id,)) as c:
            row = await c.fetchone()
            if not row or row[1] != 'pending':
                return await call.answer("⚠️ Request already processed.", show_alert=True)

            user_id = row[0]

        await db.execute("UPDATE withdrawals SET status = 'rejected' WHERE withdraw_id = ?", (withdraw_id,))
        await db.commit()

    await call.message.edit_text(f"{call.message.text}\n\n❌ **REJECTED by Admin**", parse_mode="Markdown")
    try:
        await bot.send_message(user_id, "❌ **Withdrawal Rejected.**\nPlease contact support if you think this is an error.", parse_mode="Markdown")
    except Exception:
        pass

# Admin Callback Handlers
@router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*), SUM(balance) FROM users") as c1:
            u_count, total_bal = await c1.fetchone()
        async with db.execute("SELECT COUNT(*) FROM tickets") as c2:
            t_count = (await c2.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM game_rounds WHERE status = 'finished'") as c3:
            r_count = (await c3.fetchone())[0]

    stats_text = (
        "📊 **Platform Overview Statistics**\n\n"
        f"👥 **Total Registered Users:** `{u_count}`\n"
        f"💰 **Total In-App Circulation:** `{total_bal or 0.0:.2f} ETB`\n"
        f"🎟️ **Total Tickets Sold:** `{t_count}`\n"
        f"🏁 **Completed Rounds:** `{r_count}`"
    )
    await call.message.edit_text(stats_text, parse_mode="Markdown", reply_markup=get_admin_dashboard_kb())

@router.callback_query(F.data == "admin_user_search")
async def cb_user_search_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_user_id)
    await call.message.answer("🔍 Send the **Numeric User ID** you want to inspect:")
    await call.answer()

@router.message(AdminStates.waiting_for_user_id)
async def process_user_search(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try:
        target_id = int(message.text.strip())
    except ValueError:
        return await message.answer("❌ Invalid User ID format. Please send a numeric ID.")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username, balance, created_at FROM users WHERE user_id = ?", (target_id,)) as c:
            row = await c.fetchone()

    if not row:
        await message.answer(f"❌ User `{target_id}` not found in database.", parse_mode="Markdown")
    else:
        info = (
            f"👤 **User Account File**\n\n"
            f"🆔 **ID:** `{target_id}`\n"
            f"👤 **Username:** `@{row[0]}`\n"
            f"💵 **Balance:** `{row[1]:.2f} ETB`\n"
            f"📅 **Joined:** `{row[2]}`"
        )
        await message.answer(info, parse_mode="Markdown")
    
    await state.clear()

@router.callback_query(F.data == "admin_adjust_bal")
async def cb_adjust_bal_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_balance_change)
    await call.message.answer("💳 Send the **User ID** and **Amount** separated by space.\nExample: `349952871 50` (to add ETB 50) or `349952871 -20` (to deduct ETB 20):", parse_mode="Markdown")
    await call.answer()

@router.message(AdminStates.waiting_for_balance_change)
async def process_balance_adjust(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    parts = message.text.strip().split()
    if len(parts) != 2:
        return await message.answer("❌ Format wrong. Send: `<user_id> <amount>`")

    try:
        target_id = int(parts[0])
        amount = float(parts[1])
    except ValueError:
        return await message.answer("❌ Invalid numbers provided.")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as c:
            row = await c.fetchone()
            if not row:
                await state.clear()
                return await message.answer("❌ User not found.")

        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, target_id))
        await db.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, 'admin_adjustment')", (target_id, amount))
        await db.commit()

        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (target_id,)) as c:
            new_bal = (await c.fetchone())[0]

    await message.answer(f"✅ Balance updated for User `{target_id}`!\nNew Balance: `{new_bal:.2f} ETB`", parse_mode="Markdown")
    await state.clear()

@router.callback_query(F.data == "admin_logs")
async def cb_admin_logs(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_ID: return
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, amount, type, timestamp FROM transactions ORDER BY tx_id DESC LIMIT 5") as c:
            rows = await c.fetchall()

    if not rows:
        log_text = "📜 No transactions recorded yet."
    else:
        log_text = "📜 **Last 5 Ledger Transactions:**\n\n"
        for r in rows:
            sign = "+" if r[1] > 0 else ""
            log_text += f"👤 `{r[0]}` | {sign}{r[1]:.2f} ETB ({r[2]}) | `{r[3]}`\n"

    await call.message.edit_text(log_text, parse_mode="Markdown", reply_markup=get_admin_dashboard_kb())

@router.callback_query(F.data == "admin_broadcast")
async def cb_broadcast_start(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_broadcast)
    await call.message.answer("📢 Send the message text you want to broadcast to **ALL users**:")
    await call.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    text_to_send = message.text

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as c:
            users = await c.fetchall()

    sent, failed = 0, 0
    for u in users:
        try:
            await bot.send_message(u[0], f"📢 **Announcement from Admin:**\n\n{text_to_send}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await message.answer(f"✅ **Broadcast Finished!**\n\n📤 Sent: `{sent}`\n❌ Failed: `{failed}`", parse_mode="Markdown")
    await state.clear()

async def main():
    await init_db()
    asyncio.create_task(game_loop())
    
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(dp.start_polling(bot))
    
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
