"""SQLite database for the production monitoring bot."""

import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'UNKNOWN',
                previous_status TEXT,
                last_check TEXT,
                last_change TEXT,
                check_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                status TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                response_size INTEGER DEFAULT 0,
                response_hash TEXT,
                raw_response_path TEXT,
                verification_status TEXT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                verification_result TEXT,
                notification_sent INTEGER DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS allowed_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_checks_account_id ON checks(account_id);
            CREATE INDEX IF NOT EXISTS idx_checks_timestamp ON checks(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_account_id ON events(account_id);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
        """)
        self.conn.commit()

    def seed_admins(self, usernames: List[str]):
        for u in usernames:
            self.conn.execute(
                "INSERT OR IGNORE INTO admins (username) VALUES (?)", (u,)
            )
        self.conn.commit()

    def is_admin(self, username: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM admins WHERE username = ?", (username,))
        return cur.fetchone() is not None

    def is_allowed_user(self, username: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM allowed_users WHERE username = ?", (username,))
        return cur.fetchone() is not None

    def add_admin(self, username: str) -> bool:
        try:
            self.conn.execute("INSERT INTO admins (username) VALUES (?)", (username,))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_admin(self, username: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM admins")
        count = cur.fetchone()["cnt"]
        if count <= 1:
            return False
        cur.execute("DELETE FROM admins WHERE username = ?", (username,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_all_admins(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT username FROM admins ORDER BY id")
        return [row["username"] for row in cur.fetchall()]

    def add_allowed_user(self, username: str) -> bool:
        try:
            self.conn.execute("INSERT INTO allowed_users (username) VALUES (?)", (username,))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_allowed_user(self, username: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM allowed_users WHERE username = ?", (username,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_all_allowed_users(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT username FROM allowed_users ORDER BY id")
        return [row["username"] for row in cur.fetchall()]

    def cleanup_old_data(self, days: int = 7, raw_dir: str = "", screenshots_dir: str = "") -> dict:
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        stats = {"checks": 0, "events": 0, "files": 0}

        cur = self.conn.cursor()
        cur.execute("DELETE FROM checks WHERE timestamp < ?", (cutoff,))
        stats["checks"] = cur.rowcount
        cur.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        stats["events"] = cur.rowcount
        self.conn.commit()

        if raw_dir and os.path.exists(raw_dir):
            stats["files"] += self._cleanup_dir(raw_dir, days)
        if screenshots_dir and os.path.exists(screenshots_dir):
            stats["files"] += self._cleanup_dir(screenshots_dir, days)

        return stats

    def _cleanup_dir(self, directory: str, days: int) -> int:
        from datetime import timedelta
        import time as _time
        deleted = 0
        cutoff = _time.time() - (days * 86400)
        for root, dirs, files in os.walk(directory, topdown=False):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    if os.path.getmtime(fpath) < cutoff:
                        os.remove(fpath)
                        deleted += 1
                except Exception:
                    pass
            for dname in dirs:
                dpath = os.path.join(root, dname)
                try:
                    if not os.listdir(dpath):
                        os.rmdir(dpath)
                except Exception:
                    pass
        return deleted

    def get_or_create_account(self, username: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return int(row["id"])
        cursor.execute("INSERT INTO accounts (username) VALUES (?)", (username,))
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_account_status(self, username: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT status FROM accounts WHERE username = ?", (username,))
        row = cursor.fetchone()
        return row["status"] if row else None

    def get_account_by_id(self, account_id: int) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def update_account_status(self, account_id: int, new_status: str):
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.cursor()

        cursor.execute("SELECT status FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        old_status = row["status"] if row else None

        if old_status != new_status:
            cursor.execute("""
                UPDATE accounts
                SET status = ?, previous_status = ?, last_check = ?, last_change = ?, check_count = check_count + 1, updated_at = ?
                WHERE id = ?
            """, (new_status, old_status, now, now, now, account_id))
        else:
            cursor.execute("""
                UPDATE accounts
                SET last_check = ?, check_count = check_count + 1, updated_at = ?
                WHERE id = ?
            """, (now, now, account_id))
        self.conn.commit()

    def save_check(self, check_data: Dict[str, Any]) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO checks (
                account_id, timestamp, status, status_code, latency_ms,
                response_size, response_hash, raw_response_path,
                verification_status, error_message, retry_count
            ) VALUES (
                :account_id, :timestamp, :status, :status_code, :latency_ms,
                :response_size, :response_hash, :raw_response_path,
                :verification_status, :error_message, :retry_count
            )
        """, check_data)
        self.conn.commit()
        return cursor.lastrowid or 0

    def save_event(self, event_data: Dict[str, Any]) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO events (account_id, old_status, new_status, timestamp, verification_result, notification_sent)
            VALUES (:account_id, :old_status, :new_status, :timestamp, :verification_result, :notification_sent)
        """, event_data)
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_all_accounts(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM accounts ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_events(self, limit: int = 10) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT e.*, a.username
            FROM events e
            JOIN accounts a ON e.account_id = a.id
            ORDER BY e.timestamp DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_checks(self, limit: int = 20) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT c.*, a.username
            FROM checks c
            JOIN accounts a ON c.account_id = a.id
            ORDER BY c.timestamp DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def remove_account(self, username: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE username = ?", (username,))
        row = cursor.fetchone()
        if not row:
            return False
        account_id = row["id"]
        cursor.execute("DELETE FROM checks WHERE account_id = ?", (account_id,))
        cursor.execute("DELETE FROM events WHERE account_id = ?", (account_id,))
        cursor.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        self.conn.commit()
        return True

    def get_setting(self, key: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("""
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
        """, (key, value, now))
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
