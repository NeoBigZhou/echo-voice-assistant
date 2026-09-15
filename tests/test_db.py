import hashlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app.db as db


class ApiKeyTests(unittest.TestCase):
    def test_key_is_only_returned_at_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_file = f"{tmp}/echo.db"
            with patch.object(db, "DATA_DIR", tmp), patch.object(db, "DB_FILE", db_file):
                db.init()
                token = db.add_api_key("phone")
                listed = db.list_api_keys()
                verified = db.verify_api_key(token)
                rejected = db.verify_api_key(token + "bad")

        self.assertTrue(token.startswith("echo_"))
        self.assertNotIn("token", listed[0])
        self.assertNotIn("token_hash", listed[0])
        self.assertNotIn("token_hint", listed[0])
        self.assertTrue(verified)
        self.assertIsNone(rejected)

    def test_v1_plaintext_key_is_migrated_without_rotation(self):
        legacy_token = "echo_" + "a" * 48
        with tempfile.TemporaryDirectory() as tmp:
            db_file = f"{tmp}/echo.db"
            conn = sqlite3.connect(db_file)
            conn.executescript("""
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT DEFAULT '');
                INSERT INTO meta(key,value) VALUES('schema_version','1');
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT DEFAULT '',
                    token TEXT UNIQUE,
                    scopes TEXT DEFAULT '[\"read\"]',
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT '',
                    last_used_at TEXT DEFAULT ''
                );
            """)
            conn.execute("INSERT INTO api_keys(name,token) VALUES(?,?)", ("legacy", legacy_token))
            conn.commit()
            conn.close()

            with patch.object(db, "DATA_DIR", tmp), patch.object(db, "DB_FILE", db_file):
                db.init()
                conn = sqlite3.connect(db_file)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(api_keys)")}
                stored_hash = conn.execute("SELECT token_hash FROM api_keys").fetchone()[0]
                version = conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
                conn.close()
                verified = db.verify_api_key(legacy_token)

        self.assertNotIn("token", columns)
        self.assertIn("token_hash", columns)
        self.assertEqual(stored_hash, hashlib.sha256(legacy_token.encode()).hexdigest())
        self.assertGreaterEqual(int(version), 2)
        self.assertTrue(verified)


if __name__ == "__main__":
    unittest.main()
