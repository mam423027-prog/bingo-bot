import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import HTTPException

import main


TEST_TOKEN = "test-token-never-use-in-production"


def signed_init_data(user_id=12345, auth_date=None, extra_pairs=None):
    auth_date = int(time.time()) if auth_date is None else auth_date
    pairs = [
        ("auth_date", str(auth_date)),
        ("query_id", "test-query"),
        ("user", json.dumps({"id": user_id}, separators=(",", ":"))),
    ]
    if extra_pairs:
        pairs.extend(extra_pairs)
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(pairs))
    secret = hmac.new(b"WebAppData", TEST_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs + [("hash", digest)])


class TelegramInitDataTests(unittest.TestCase):
    def test_accepts_valid_signed_identity(self):
        with patch.object(main, "BOT_TOKEN", TEST_TOKEN):
            identity = main.verify_telegram_init_data(signed_init_data(user_id=12345))
        self.assertEqual(identity["user_id"], 12345)

    def test_rejects_tampered_data(self):
        with patch.object(main, "BOT_TOKEN", TEST_TOKEN):
            with self.assertRaises(HTTPException) as ctx:
                main.verify_telegram_init_data(signed_init_data().replace("test-query", "tampered"))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_rejects_duplicate_fields(self):
        with patch.object(main, "BOT_TOKEN", TEST_TOKEN):
            with self.assertRaises(HTTPException) as ctx:
                main.verify_telegram_init_data(signed_init_data(extra_pairs=[("auth_date", str(int(time.time())))]))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_rejects_expired_data(self):
        with patch.object(main, "BOT_TOKEN", TEST_TOKEN):
            with self.assertRaises(HTTPException) as ctx:
                main.verify_telegram_init_data(signed_init_data(auth_date=int(time.time()) - 3601))
        self.assertEqual(ctx.exception.status_code, 401)

    def test_fails_closed_when_token_missing(self):
        with patch.object(main, "BOT_TOKEN", ""):
            with self.assertRaises(HTTPException) as ctx:
                main.verify_telegram_init_data(signed_init_data())
        self.assertEqual(ctx.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
