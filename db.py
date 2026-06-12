import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_tables()
        self._migrate()

    def _init_tables(self):
        cursor = self.conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                last_status TEXT DEFAULT 'UNKNOWN',
                last_checked_at TEXT,
                last_success_at TEXT,
                last_error_at TEXT,
                missing_since TEXT,
                restored_at TEXT,
                check_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS checks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                transport TEXT DEFAULT 'unknown',
                backend TEXT DEFAULT 'unknown',
                proxy_enabled INTEGER NOT NULL DEFAULT 0,
                user_agent TEXT,
                status_code INTEGER,
                latency_ms REAL,
                success INTEGER NOT NULL DEFAULT 0,
                classification TEXT NOT NULL,
                raw_response_path TEXT,
                raw_response_blob TEXT,
                headers_blob TEXT,
                error_message TEXT,
                exception_type TEXT,
                traceback TEXT,
                retry_count INTEGER DEFAULT 0,
                response_size INTEGER DEFAULT 0,
                response_hash TEXT,
                screenshot_path TEXT,
                curl_stderr TEXT,
                curl_exit_code INTEGER,
                verified INTEGER,
                verification_transport TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                check_id INTEGER,
                old_status TEXT,
                new_status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                reason TEXT,
                screenshot_path TEXT,
                FOREIGN KEY (account_id) REFERENCES accounts(id),
                FOREIGN KEY (check_id) REFERENCES checks(id)
            );

            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                run_id TEXT,
                mode TEXT,
                requests_total INTEGER DEFAULT 0,
                requests_success INTEGER DEFAULT 0,
                requests_failed INTEGER DEFAULT 0,
                active_count INTEGER DEFAULT 0,
                missing_count INTEGER DEFAULT 0,
                unknown_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                latency_avg REAL DEFAULT 0,
                latency_min REAL DEFAULT 0,
                latency_max REAL DEFAULT 0,
                transition_count INTEGER DEFAULT 0,
                verify_mismatch_count INTEGER DEFAULT 0,
                transports_blob TEXT
            );

            CREATE TABLE IF NOT EXISTS checkpoint (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_processed_index INTEGER DEFAULT 0,
                last_checkpoint_time TEXT,
                run_id TEXT,
                mode TEXT,
                counters_blob TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_checks_account_id ON checks(account_id);
            CREATE INDEX IF NOT EXISTS idx_checks_timestamp ON checks(timestamp);
            CREATE INDEX IF NOT EXISTS idx_checks_classification ON checks(classification);
            CREATE INDEX IF NOT EXISTS idx_events_account_id ON events(account_id);
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
        """)
        self.conn.commit()

    def _migrate(self):
        cursor = self.conn.cursor()
        for col, typedef in [
            ("backend", "TEXT DEFAULT 'unknown'"),
            ("curl_stderr", "TEXT"),
            ("curl_exit_code", "INTEGER"),
            ("transport", "TEXT DEFAULT 'unknown'"),
            ("verified", "INTEGER"),
            ("verification_transport", "TEXT"),
        ]:
            try:
                cursor.execute(f"SELECT {col} FROM checks LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute(f"ALTER TABLE checks ADD COLUMN {col} {typedef}")
        for col, typedef in [
            ("verify_mismatch_count", "INTEGER DEFAULT 0"),
            ("transports_blob", "TEXT"),
        ]:
            try:
                cursor.execute(f"SELECT {col} FROM metrics LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute(f"ALTER TABLE metrics ADD COLUMN {col} {typedef}")
        self.conn.commit()

    def get_or_create_account(self, username: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM accounts WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return row["id"]
        cursor.execute("INSERT INTO accounts (username) VALUES (?)", (username,))
        self.conn.commit()
        return cursor.lastrowid

    def update_account_status(self, account_id: int, status: str, is_error: bool = False):
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.cursor()
        if is_error:
            cursor.execute("""
                UPDATE accounts 
                SET last_error_at = ?, error_count = error_count + 1, updated_at = ?
                WHERE id = ?
            """, (now, now, account_id))
        else:
            cursor.execute("""
                UPDATE accounts 
                SET last_status = ?, last_checked_at = ?, last_success_at = ?, 
                    check_count = check_count + 1, updated_at = ?
                WHERE id = ?
            """, (status, now, now, now, account_id))
        self.conn.commit()

    def set_missing_since(self, account_id: int, since: str):
        self.conn.execute(
            "UPDATE accounts SET missing_since = ? WHERE id = ? AND missing_since IS NULL",
            (since, account_id)
        )
        self.conn.commit()

    def clear_missing_since(self, account_id: int):
        self.conn.execute(
            "UPDATE accounts SET missing_since = NULL, restored_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_id,)
        )
        self.conn.commit()

    def get_account_status(self, username: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT last_status FROM accounts WHERE username = ?", (username,))
        row = cursor.fetchone()
        return row["last_status"] if row else None

    def was_account_checked(self, account_id: int) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("SELECT last_checked_at FROM accounts WHERE id = ?", (account_id,))
        row = cursor.fetchone()
        return row is not None and row["last_checked_at"] is not None

    def save_check(self, check_data: Dict[str, Any]) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO checks (
                account_id, run_id, timestamp, mode, transport, backend, proxy_enabled, user_agent,
                status_code, latency_ms, success, classification, raw_response_path,
                raw_response_blob, headers_blob, error_message, exception_type,
                traceback, retry_count, response_size, response_hash, screenshot_path,
                curl_stderr, curl_exit_code, verified, verification_transport
            ) VALUES (
                :account_id, :run_id, :timestamp, :mode, :transport, :backend, :proxy_enabled, :user_agent,
                :status_code, :latency_ms, :success, :classification, :raw_response_path,
                :raw_response_blob, :headers_blob, :error_message, :exception_type,
                :traceback, :retry_count, :response_size, :response_hash, :screenshot_path,
                :curl_stderr, :curl_exit_code, :verified, :verification_transport
            )
        """, check_data)
        self.conn.commit()
        return cursor.lastrowid

    def save_event(self, event_data: Dict[str, Any]) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO events (account_id, check_id, old_status, new_status, timestamp, reason, screenshot_path)
            VALUES (:account_id, :check_id, :old_status, :new_status, :timestamp, :reason, :screenshot_path)
        """, event_data)
        self.conn.commit()
        return cursor.lastrowid

    def save_metrics(self, metrics_data: Dict[str, Any]) -> int:
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO metrics (
                timestamp, run_id, mode, requests_total, requests_success, requests_failed,
                active_count, missing_count, unknown_count, error_count,
                latency_avg, latency_min, latency_max,
                transition_count, verify_mismatch_count, transports_blob
            ) VALUES (
                :timestamp, :run_id, :mode, :requests_total, :requests_success, :requests_failed,
                :active_count, :missing_count, :unknown_count, :error_count,
                :latency_avg, :latency_min, :latency_max,
                :transition_count, :verify_mismatch_count, :transports_blob
            )
        """, metrics_data)
        self.conn.commit()
        return cursor.lastrowid

    def save_checkpoint(self, data: Dict[str, Any]):
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO checkpoint (id, last_processed_index, last_checkpoint_time, run_id, mode, counters_blob)
            VALUES (1, :last_processed_index, :last_checkpoint_time, :run_id, :mode, :counters_blob)
        """, data)
        self.conn.commit()

    def load_checkpoint(self) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM checkpoint WHERE id = 1")
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None

    def get_all_accounts(self) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM accounts ORDER BY id")
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        if self.conn:
            self.conn.close()
