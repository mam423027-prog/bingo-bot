# Hagere Bingo Bot

Hagere Bingo uses a FastAPI web app and an Aiogram Telegram bot. The web app starts the bot polling task in the same process so both use one SQLite database and one persistent disk. Do not deploy `bingo_bot.py` as a separate Render service.

## Render web service

- **Build command:** `pip install -r requirements.txt`
- **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Attach a persistent disk mounted at `/var/data`.
- Use a new database file path for the current schema. If an older `bingo.db` was created by the legacy bot, do not point this version at that old database without a reviewed migration.

Set these environment variables in Render (never commit secret values):

- `TELEGRAM_BOT_TOKEN`: the rotated Telegram bot token.
- `ADMIN_TELEGRAM_ID`: the numeric Telegram user ID allowed to review deposits and start draws.
- `WEBAPP_URL`: the public HTTPS URL of this Render web service, e.g. `https://your-service.onrender.com`.
- `DATABASE_PATH`: `/var/data/hagere_bingo.sqlite3`
- `RECEIPT_DIR`: `/var/data/private_receipts`

After setting `WEBAPP_URL`, configure the bot's Telegram menu button or use the bot's `/start` message to open the web app. Open the app from Telegram; API requests use Telegram Web App `initData` authentication.

## Manual deposit workflow

Players select Telebirr or CBE Birr, enter the transaction reference, and upload an image receipt (maximum 5 MB). Each request stays pending until an administrator checks the payment in the actual payment account and approves it in the admin dashboard. The backend credits the wallet only on approval and rejects repeated transaction references.

This repository does **not** perform automatic Telebirr API checkout. Only provide payment account details that the administrator has verified and approved; do not use sample account numbers.

## Before production

1. Keep the pull request open until the branch has been reviewed and tested.
2. Set the environment variables and attach persistent storage before testing deposits.
3. Verify the app opens from Telegram and that a non-admin account cannot access admin endpoints.
4. Test a pending deposit, receipt viewing, approval, rejection, duplicate transaction reference, and wallet balance changes using a small test payment.
5. Confirm the database and receipt files survive a Render restart.
6. Do not treat a successful deployment as proof that payments, game rules, or prize accounting have been audited.
