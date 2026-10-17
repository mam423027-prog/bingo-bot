import hashlib
import hmac
import json
import os
import time
import unittest
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import HTTPException

import main


class TelegramInitDataTests(unittest.TestCase):
    def setUp(self):
        self.old_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.token = "unit-test-token-not-a-real-secret"
        os.environ["TELEGRAM_BOT_TOKEN"] = self.token

    def tearDown(self):
        if self.old_token is None:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        else:
            os.environ["TELEGRAM_BOT_TOKEN"] = self.old_token

    def signed_init_data(self, auth_date=None):
        fields = {
            "auth_date": str(auth_date or int(time.time())),
            "query_id": "test-query",
            "user": json.dumps({"id": 123456, "first_name": "Test"}, separators=(",", ":")),
        }
        check_string = "\n".join("{}={}".format(k, v) for k, v in sorted(fields.items()))
        secret_key = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        return urlencode(fields)

    def test_accepts_valid_signed_init_data(self):
        result = main.verify_telegram_init_data(self.signed_init_data())
        self.assertEqual(result["user_id"], 123456)

    def test_rejects_tampered_init_data(self):
        data = self.signed_init_data().replace("Test", "Other")
        with self.assertRaises(HTTPException) as error:
            main.verify_telegram_init_data(data)
        self.assertEqual(error.exception.status_code, 401)

    def test_rejects_expired_init_data(self):
        data = self.signed_init_data(auth_date=int(time.time()) - 90000)
        with self.assertRaises(HTTPException) as error:
            main.verify_telegram_init_data(data)
        self.assertEqual(error.exception.status_code, 401)

    def test_rejects_missing_token(self):
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        with self.assertRaises(HTTPException) as error:
            main.verify_telegram_init_data(self.signed_init_data())
        self.assertEqual(error.exception.status_code, 401)


class ManualWithdrawalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_db = main.DB_FILE
        self.old_receipt_dir = main.RECEIPT_DIR
        self.old_admin = os.environ.get("ADMIN_TELEGRAM_ID")
        self.old_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.token = "withdrawal-test-token"
        os.environ["TELEGRAM_BOT_TOKEN"] = self.token
        os.environ["ADMIN_TELEGRAM_ID"] = "123456"
        main.DB_FILE = str(Path(self.tempdir.name) / "test.sqlite3")
        main.RECEIPT_DIR = Path(self.tempdir.name) / "receipts"
        await main.init_db()
        async with main.aiosqlite.connect(main.DB_FILE) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id, balance, total_deposits) VALUES (123456, 100, 0)")
            await db.execute("UPDATE users SET balance=100 WHERE user_id=123456")
            await db.commit()

    async def asyncTearDown(self):
        main.DB_FILE = self.old_db
        main.RECEIPT_DIR = self.old_receipt_dir
        if self.old_admin is None:
            os.environ.pop("ADMIN_TELEGRAM_ID", None)
        else:
            os.environ["ADMIN_TELEGRAM_ID"] = self.old_admin
        if self.old_token is None:
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        else:
            os.environ["TELEGRAM_BOT_TOKEN"] = self.old_token
        self.tempdir.cleanup()

    def signed_init_data(self):
        fields = {
            "auth_date": str(int(time.time())),
            "query_id": "withdrawal-test",
            "user": json.dumps({"id": 123456, "first_name": "Test"}, separators=(",", ":")),
        }
        check_string = "\\n".join("{}={}".format(k, v) for k, v in sorted(fields.items()))
        secret_key = hmac.new(b"WebAppData", self.token.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        return urlencode(fields)

    async def test_rejected_withdrawal_refunds_reserved_balance(self):
        init_data = self.signed_init_data()
        result = await main.request_withdrawal(
            amount=50, payment_method="Telebirr", destination_account="0912345678",
            x_telegram_init_data=init_data
        )
        self.assertEqual(result["status"], "pending")
        async with main.aiosqlite.connect(main.DB_FILE) as db:
            async with db.execute("SELECT id FROM withdrawals") as cursor:
                withdrawal_id = (await cursor.fetchone())[0]
            async with db.execute("SELECT balance FROM users WHERE user_id=123456") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 50)
        rejected = await main.reject_withdrawal(withdrawal_id, x_telegram_init_data=init_data)
        self.assertEqual(rejected["status"], "rejected")
        async with main.aiosqlite.connect(main.DB_FILE) as db:
            async with db.execute("SELECT balance FROM users WHERE user_id=123456") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 100)

    async def test_paid_withdrawal_is_not_refunded_and_cannot_be_reviewed_twice(self):
        init_data = self.signed_init_data()
        await main.request_withdrawal(
            amount=50, payment_method="CBE Birr", destination_account="1000123456",
            x_telegram_init_data=init_data
        )
        async with main.aiosqlite.connect(main.DB_FILE) as db:
            async with db.execute("SELECT id FROM withdrawals") as cursor:
                withdrawal_id = (await cursor.fetchone())[0]
            async with db.execute("SELECT balance FROM users WHERE user_id=123456") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 50)
        result = await main.mark_withdrawal_paid(withdrawal_id, payout_ref="TEST-PAYOUT-1", x_telegram_init_data=init_data)
        self.assertEqual(result["status"], "paid")
        with self.assertRaises(HTTPException) as error:
            await main.reject_withdrawal(withdrawal_id, x_telegram_init_data=init_data)
        self.assertEqual(error.exception.status_code, 409)
        async with main.aiosqlite.connect(main.DB_FILE) as db:
            async with db.execute("SELECT balance FROM users WHERE user_id=123456") as cursor:
                self.assertEqual((await cursor.fetchone())[0], 50)

    async def test_rejects_withdrawal_above_available_balance(self):
        with self.assertRaises(HTTPException) as error:
            await main.request_withdrawal(
                amount=150, payment_method="Telebirr", destination_account="0912345678",
                x_telegram_init_data=self.signed_init_data()
            )
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
