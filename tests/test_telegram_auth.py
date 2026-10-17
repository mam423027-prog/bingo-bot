import hashlib
import hmac
import json
import os
import time
import unittest
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


if __name__ == "__main__":
    unittest.main()
