# Bingo Bot

FastAPI application served by Uvicorn. The Docker image is configured to listen on Render's `PORT` environment variable (default 10000).

## Deployment (Render)

1. Create a Render Web Service from this repository and select Docker. The included `render.yaml` can be used as a Blueprint.
2. Set `BOT_TOKEN` to the newly rotated Telegram bot token. Store it only in Render's environment settings, never in GitHub.
3. Confirm `ADMIN_TELEGRAM_IDS=349952871` (or your intended comma-separated admin IDs).
4. Enter the payment destination account numbers in Render's private `TELEBIRR_ACCOUNT` and `CBEBIRR_ACCOUNT` environment variables; do not commit account numbers to this public repository.
5. Keep `DATABASE_PATH=/var/data/bingo.db` and ensure the persistent disk is mounted at `/var/data`.
6. Configure the Telegram Mini App to use the HTTPS URL of the deployed service.
7. Verify `/health` returns HTTP 200 after deployment.

## Security and payments

- API identity is derived from Telegram Web App `initData`, whose signature and age are verified server-side using `BOT_TOKEN`.
- Admin API operations require a valid Telegram identity listed in `ADMIN_TELEGRAM_IDS`.
- Deposits are not credited automatically. The current endpoint records an unverified request and returns an unavailable response until payment verification is implemented. Never credit a balance based solely on a transaction reference supplied by a browser.
- On first startup after this security upgrade, existing balances and historical completed deposits are quarantined as unverified audit data because the previous version credited client-supplied references without payment verification. Reconcile those records manually before any restoration.
- Do not set `ALLOW_UNAUTHENTICATED_DEV=true` on a public deployment.
- The admin HTML may be served publicly, but its data and draw operations are protected by server-side authorization.

## Local development

Install dependencies with `pip install -r requirements.txt`, set `BOT_TOKEN` and `ADMIN_TELEGRAM_IDS`, then run:

```sh
uvicorn main:app --host 0.0.0.0 --port 10000
```

For local-only API testing without Telegram, set `ALLOW_UNAUTHENTICATED_DEV=true` and leave `BOT_TOKEN` unset. Do not use that mode on an internet-facing service.
