import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from book_sales.credentials import load_google_service_account


class GoogleServiceAccountSecretTests(unittest.TestCase):
    def setUp(self):
        self.credentials = {
            "type": "service_account",
            "project_id": "test-project",
            "private_key": "test-private-key",
            "client_email": "sync@example.test",
            "token_uri": "https://oauth2.googleapis.com/token",
        }

    def test_loads_credentials_from_streamlit_secret_table(self):
        credentials = load_google_service_account(
            {"google_service_account": self.credentials}
        )

        self.assertEqual(credentials, self.credentials)

    def test_returns_none_when_secret_is_absent(self):
        self.assertIsNone(load_google_service_account({}))

    def test_rejects_non_table_secret(self):
        with self.assertRaisesRegex(ValueError, "must be a TOML table"):
            load_google_service_account({"google_service_account": "invalid"})

    def test_reports_required_fields_missing_from_secret(self):
        with self.assertRaisesRegex(ValueError, "private_key"):
            load_google_service_account(
                {"google_service_account": {"type": "service_account"}}
            )

    def test_rejects_non_service_account_type(self):
        self.credentials["type"] = "user"

        with self.assertRaisesRegex(ValueError, "must be .service_account."):
            load_google_service_account(
                {"google_service_account": self.credentials}
            )


if __name__ == "__main__":
    unittest.main()
