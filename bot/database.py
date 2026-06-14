"""SQLite database for the production monitoring bot."""

import os
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
        self._migrate()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'UNKNOWN',
                previous_status TEXT,
                last_check TIMESTAMP,
                last_change TIMESTAMP,
                check_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                response_size INTEGER,
                response_hash TEXT,
                raw_response_path TEXT,
                verification_status TEXT,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                old_status TEXT,
                new_status TEXT NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                verification_result TEXT,
                notification_sent INTEGER DEFAULT 0,
                FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                chat_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS allowed_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS changelogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                author TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def _migrate(self):
        cursor = self.conn.cursor()

        cursor.execute("PRAGMA table_info(admins)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "chat_id" not in columns:
            cursor.execute("ALTER TABLE admins ADD COLUMN chat_id INTEGER")
            self.conn.commit()

        cursor.execute("PRAGMA table_info(allowed_users)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "chat_id" not in columns:
            cursor.execute("ALTER TABLE allowed_users ADD COLUMN chat_id INTEGER")
            self.conn.commit()

        cursor.execute("PRAGMA table_info(accounts)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "added_by" not in columns:
            cursor.execute("ALTER TABLE accounts ADD COLUMN added_by TEXT")
            self.conn.commit()

        cursor.execute("PRAGMA table_info(accounts)")
        columns = {row["name"] for row in cursor.fetchall()}
        if "down_since" not in columns:
            cursor.execute("ALTER TABLE accounts ADD COLUMN down_since TEXT")
            self.conn.commit()

    def update_admin_chat_id(self, username: str, chat_id: int):
        self.conn.execute(
            "UPDATE admins SET chat_id = ? WHERE username = ?", (chat_id, username)
        )
        self.conn.commit()

    def get_admin_chat_ids(self) -> List[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT chat_id FROM admins WHERE chat_id IS NOT NULL")
        return [row["chat_id"] for row in cur.fetchall()]

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

    def update_allowed_user_chat_id(self, username: str, chat_id: int):
        self.conn.execute(
            "UPDATE allowed_users SET chat_id = ? WHERE username = ?", (chat_id, username)
        )
        self.conn.commit()

    def add_changelog(self, message: str, author: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO changelogs (message, author) VALUES (?, ?)",
            (message, author),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_changelogs(self, limit: int = 10) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM changelogs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in cur.fetchall()]

    def get_all_recipient_chat_ids(self) -> List[int]:
        cur = self.conn.cursor()
        cur.execute("SELECT chat_id FROM admins WHERE chat_id IS NOT NULL")
        admin_ids = [row["chat_id"] for row in cur.fetchall()]
        cur.execute("SELECT chat_id FROM allowed_users WHERE chat_id IS NOT NULL")
        user_ids = [row["chat_id"] for row in cur.fetchall()]
        return list(set(admin_ids + user_ids))

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

    def get_or_create_account(self, username: str, added_by: str = "") -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            if added_by:
                cursor.execute("UPDATE accounts SET added_by = ? WHERE id = ? AND (added_by IS NULL OR added_by = '')", (added_by, row["id"]))
                self.conn.commit()
            return int(row["id"])
        cursor.execute("INSERT INTO accounts (username, added_by) VALUES (?, ?)", (username, added_by))
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

        VISIBLE = {"ACTIVE", "SUSPECT"}
        INVISIBLE = {"MISSING", "ERROR", "RATE_LIMITED"}

        if old_status != new_status:
            down_since = None
            if old_status in VISIBLE and new_status in INVISIBLE:
                down_since = now
            elif old_status in INVISIBLE and new_status in VISIBLE:
                down_since = None
            elif old_status in INVISIBLE and new_status in INVISIBLE:
                cursor.execute("SELECT down_since FROM accounts WHERE id = ?", (account_id,))
                ds_row = cursor.fetchone()
                down_since = ds_row["down_since"] if ds_row else None

            if down_since is not None:
                cursor.execute("""
                    UPDATE accounts
                    SET status = ?, previous_status = ?, last_check = ?, last_change = ?, down_since = ?, check_count = check_count + 1, updated_at = ?
                    WHERE id = ?
                """, (new_status, old_status, now, now, down_since, now, account_id))
            else:
                cursor.execute("""
                    UPDATE accounts
                    SET status = ?, previous_status = ?, last_check = ?, last_change = ?, down_since = NULL, check_count = check_count + 1, updated_at = ?
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

    def get_accounts_for_user(self, username: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM accounts WHERE added_by = ? OR added_by IS NULL ORDER BY id",
            (username,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_accounts_for_non_admin(self, username: str) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM accounts WHERE added_by = ? ORDER BY id",
            (username,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_account_added_by(self, username: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT added_by FROM accounts WHERE username = ?", (username,))
        row = cursor.fetchone()
        return row["added_by"] if row else None

    def get_notification_chat_ids_for_account(self, account_id: int) -> List[int]:
        cursor = self.conn.cursor()

        cursor.execute("SELECT chat_id FROM admins WHERE chat_id IS NOT NULL")
        admin_ids = {row["chat_id"] for row in cursor.fetchall()}

        cursor.execute("SELECT added_by FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        added_by = row["added_by"] if row else None

        owner_ids = set()
        if added_by:
            cursor.execute(
                "SELECT chat_id FROM admins WHERE username = ? AND chat_id IS NOT NULL",
                (added_by,),
            )
            owner_ids.update(row["chat_id"] for row in cursor.fetchall() if row["chat_id"])
            cursor.execute(
                "SELECT chat_id FROM allowed_users WHERE username = ? AND chat_id IS NOT NULL",
                (added_by,),
            )
            owner_ids.update(row["chat_id"] for row in cursor.fetchall() if row["chat_id"])

        return list(admin_ids | owner_ids)

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

    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT c.*, a.username
            FROM checks c
            JOIN accounts a ON c.account_id = a.id
            WHERE c.error_message IS NOT NULL AND c.error_message != ''
            ORDER BY c.timestamp DESC
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
