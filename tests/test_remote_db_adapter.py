"""Offline tests for the DB-API compatibility wrapper used by Turso."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pyh.app import Application
from pyh.config import Config
from pyh.db import _RemoteConnection, ROOT
from tests.helpers import WSGIClient


class RemoteDatabaseAdapterTests(unittest.TestCase):
    def setUp(self):
        # An ordinary SQLite connection stands in for the DB-API remote driver.
        # It exercises the wrapper without requiring cloud credentials.
        self.raw = sqlite3.connect(":memory:")
        self.db = _RemoteConnection(self.raw)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass

    def test_rows_support_column_names_and_indexes(self):
        self.db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, name TEXT)")
        self.db.execute("INSERT INTO sample(name) VALUES(?)", ("Plan",))
        row = self.db.execute("SELECT id, name FROM sample").fetchone()
        self.assertEqual(row["name"], "Plan")
        self.assertEqual(row[1], "Plan")
        self.assertIn("id", row.keys())

    def test_script_parser_preserves_semicolons_inside_literals(self):
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE sample(id INTEGER PRIMARY KEY, note TEXT);
            INSERT INTO sample(note) VALUES('retain; this');
        """)
        row = self.db.execute("SELECT note FROM sample").fetchone()
        self.assertEqual(row["note"], "retain; this")

    def test_full_schema_can_be_applied_through_adapter(self):
        migration = ROOT / "migrations" / "001_init.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        self.db.execute("INSERT INTO app_settings(key,value) VALUES(?,?)", ("adapter_test", "ok"))
        row = self.db.execute("SELECT value FROM app_settings WHERE key=?", ("adapter_test",)).fetchone()
        self.assertEqual(row["value"], "ok")

    def test_integrity_errors_keep_sqlite_error_type(self):
        self.db.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, email TEXT UNIQUE)")
        self.db.execute("INSERT INTO sample(email) VALUES(?)", ("a@example.com",))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO sample(email) VALUES(?)", ("a@example.com",))

    def test_signup_session_is_shared_between_simulated_function_instances(self):
        # A file-backed fake remote DB lets us model two separate app/function
        # instances using one shared provider database without cloud credentials.
        with tempfile.TemporaryDirectory(prefix="pyh-remote-test-") as temp:
            database_file = str(Path(temp) / "shared.sqlite3")
            fake_driver = types.SimpleNamespace(
                connect=lambda url, auth_token: sqlite3.connect(database_file)
            )
            config = type("RemoteTestConfig", (Config,), {
                "APP_ENV": "development",
                "APP_URL": "http://localhost:8000",
                "SECRET_KEY": "test-remote-key-long-enough-for-a-test-123456",
                "DATABASE_PATH": str(Path(temp) / "should-not-be-used.sqlite3"),
                "STORAGE_DIR": str(Path(temp) / "uploads"),
                "TURSO_DATABASE_URL": "libsql://test.turso.io",
                "TURSO_AUTH_TOKEN": "test-token",
                "COOKIE_SECURE": False,
                "TRIAL_DAYS": 7,
                "PUBLIC_DEMO_ENABLED": False,
            })
            with patch.dict(sys.modules, {"turso_serverless": fake_driver}), \
                    patch.object(Config, "TURSO_DATABASE_URL", "libsql://test.turso.io"), \
                    patch.object(Config, "TURSO_AUTH_TOKEN", "test-token"):
                app_one = Application(config=config)
                client = WSGIClient(app_one)
                result = client.post("/signup", {
                    "full_name": "Remote Test Owner",
                    "email": "remote-owner@example.com",
                    "role": "homeowner",
                    "units": "metric",
                    "password": "a-very-strong-password-123",
                    "confirm_password": "a-very-strong-password-123",
                }, csrf_path="/signup")
                self.assertEqual(result.status, 303, result.text[:500])

                app_two = Application(config=config)
                resumed = client.get("/onboarding")
                self.assertEqual(resumed.status, 200, resumed.text[:500])
                self.assertIn("Remote Test Owner", resumed.text)
                self.assertFalse(Path(config.DATABASE_PATH).exists())


if __name__ == "__main__":
    unittest.main()
