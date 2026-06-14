"""Monitoring loop with state tracking and notifications."""

import logging
import random
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from .checker import check_account, verify_with_playwright, capture_profile_screenshot
from .config import Config
from .database import Database

logger = logging.getLogger("monitor.loop")

CLEANUP_INTERVAL = 86400


class Monitor:
    def __init__(self, config: Config, db: Database, notify_fn: Optional[Callable] = None, notify_photo_fn: Optional[Callable] = None):
        self.config = config
        self.db = db
        self.notify_fn = notify_fn
        self.notify_photo_fn = notify_photo_fn
        self.notify_to_chat_ids: Optional[Callable] = None
        self.notify_photo_to_chat_ids: Optional[Callable] = None
        self.running = False
        self.start_time = None
        self._last_cleanup = time.time()

    def start(self):
        self.running = True
        self.start_time = datetime.now(timezone.utc)

        self._run_startup_verification()

        accounts = self.db.get_all_accounts()
        logger.info(f"Monitor started, checking {len(accounts)} accounts every {self.config.check_interval}s")

        self._run_loop()

    def stop(self):
        self.running = False
        logger.info("Monitor stopping...")

    def _run_startup_verification(self):
        if not self.config.test_accounts:
            return

        logger.info(f"Startup verification: testing {len(self.config.test_accounts)} accounts")
        all_pass = True

        for username in self.config.test_accounts:
            logger.info(f"  Verifying @{username}...")
            try:
                result = check_account(username, self.config)
                status = result["classification"]
                latency = result.get("latency_ms", 0)
                logger.info(f"  @{username}: {status} ({latency:.0f}ms)")
            except Exception as e:
                logger.error(f"  @{username}: ERROR - {e}")
                all_pass = False

        if all_pass:
            logger.info("Startup verification: ALL PASS")
        else:
            logger.warning("Startup verification: SOME FAILURES - check logs")

    def _run_loop(self):
        while self.running:
            accounts = self.db.get_all_accounts()
            if not accounts:
                logger.debug("No accounts to monitor, waiting...")
                self._interruptible_sleep(5)
                continue

            try:
                self._check_all_accounts(accounts)
            except Exception as e:
                logger.error(f"Error in check cycle: {e}")

            self._maybe_cleanup()

            if not self.running:
                break

            interval = random.uniform(
                max(30, self.config.check_interval - 15),
                self.config.check_interval + 15,
            )
            logger.info(f"Sleeping {interval:.1f}s until next check cycle")
            self._interruptible_sleep(interval)

    def _maybe_cleanup(self):
        now = time.time()
        if now - self._last_cleanup < CLEANUP_INTERVAL:
            return
        self._last_cleanup = now
        try:
            stats = self.db.cleanup_old_data(
                days=7,
                raw_dir=self.config.raw_responses_dir,
                screenshots_dir=self.config.screenshots_dir,
            )
            logger.info(f"Cleanup: deleted {stats['checks']} checks, {stats['events']} events, {stats['files']} files")
        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

    def _check_all_accounts(self, accounts):
        now = datetime.now(timezone.utc).isoformat()
        logger.info(f"--- Check cycle started at {now} ---")

        for account in accounts:
            if not self.running:
                break
            try:
                self._check_single_account(account["username"])
            except Exception as e:
                logger.error(f"Error checking {account['username']}: {e}")

        logger.info("--- Check cycle complete ---")

    def _check_single_account(self, username: str):
        logger.info(f"Checking {username}")

        result = check_account(username, self.config)

        if result["classification"] == "MISSING":
            result = verify_with_playwright(username, result, self.config)

        account_id = self.db.get_or_create_account(username)
        old_status = self.db.get_account_status(username)
        new_status = result["classification"]

        self.db.update_account_status(account_id, new_status)

        check_data = {
            "account_id": account_id,
            "timestamp": result.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "status": new_status,
            "status_code": result.get("status_code"),
            "latency_ms": result.get("latency_ms", 0),
            "response_size": result.get("response_size", 0),
            "response_hash": result.get("response_hash", ""),
            "raw_response_path": result.get("raw_response_path", ""),
            "verification_status": result.get("verification_status"),
            "error_message": result.get("error_message"),
            "retry_count": result.get("retry_count", 0),
        }
        self.db.save_check(check_data)

        is_transition = old_status is not None and old_status != new_status
        if is_transition:
            logger.info(f"TRANSITION: {username} {old_status} -> {new_status}")
            self._handle_transition(account_id, username, old_status or "UNKNOWN", new_status, result)

        logger.info(
            f"  {username}: {new_status} "
            f"(latency={result.get('latency_ms', 0):.0f}ms, "
            f"status_code={result.get('status_code', 'N/A')})"
        )

    def _handle_transition(self, account_id: int, username: str, old_status: str, new_status: str, result: dict):
        should_notify = False
        verification_result = None

        if old_status == "ACTIVE" and new_status == "MISSING":
            should_notify = True
            verification_result = result.get("verification_status", "unverified")
        elif old_status == "MISSING" and new_status == "ACTIVE":
            should_notify = True
            verification_result = "restored"

        event_data = {
            "account_id": account_id,
            "old_status": old_status,
            "new_status": new_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "verification_result": verification_result,
            "notification_sent": 0,
        }
        event_id = self.db.save_event(event_data)

        if should_notify and self.notify_fn:
            try:
                screenshot_path = None
                screenshot_error = None
                profile_data = {}

                if new_status == "ACTIVE":
                    screenshot_data = capture_profile_screenshot(username, self.config, new_status.lower())
                    screenshot_path = screenshot_data.get("screenshot_path")
                    profile_data = screenshot_data.get("profile_data", {})
                    screenshot_error = screenshot_data.get("error")

                account_info = self.db.get_account_by_id(account_id)
                last_change = account_info.get("last_change") if account_info else None
                duration = self._calc_duration(last_change)

                caption = self._format_notification(
                    username, old_status, new_status,
                    verification_result or "unverified",
                    duration, profile_data,
                )

                chat_ids = self.db.get_notification_chat_ids_for_account(account_id)
                if not chat_ids:
                    chat_ids = self.db.get_admin_chat_ids()

                if screenshot_path and self.notify_photo_to_chat_ids:
                    self.notify_photo_to_chat_ids(chat_ids, screenshot_path, caption)
                elif screenshot_path and self.notify_photo_fn:
                    self.notify_photo_fn(screenshot_path, caption)
                elif screenshot_error:
                    error_reasons = {
                        "profile_unavailable": "Profile deactivated or doesn't exist",
                        "service_down": "SS service down (Camofox offline)",
                        "timeout": "Page load timed out",
                        "rate_limited": "SS service rate limited",
                        "connection_refused": "SS service unreachable",
                    }
                    reason = error_reasons.get(screenshot_error, "Screenshot unavailable")
                    fallback = (
                        f"{caption}\n\n"
                        f"⚠️ {reason}\n"
                        f"🔗 <a href=\"https://www.instagram.com/{username}/\">Open profile</a>"
                    )
                    if self.notify_to_chat_ids:
                        self.notify_to_chat_ids(chat_ids, fallback)
                    else:
                        self.notify_fn(fallback)
                else:
                    if self.notify_to_chat_ids:
                        self.notify_to_chat_ids(chat_ids, caption)
                    else:
                        self.notify_fn(caption)

                self.db.conn.execute(
                    "UPDATE events SET notification_sent = 1 WHERE id = ?", (event_id,)
                )
                self.db.conn.commit()
                logger.info(f"Notification sent for {username} to {len(chat_ids)} recipients")
            except Exception as e:
                logger.error(f"Failed to send notification for {username}: {e}")

    def _format_notification(
        self, username: str, old_status: str, new_status: str,
        verification: str, duration: str, profile_data: dict,
    ) -> str:
        STATUS_EMOJI = {
            "ACTIVE": "🟢",
            "MISSING": "🔴",
            "SUSPECT": "🟡",
            "UNKNOWN": "⚪",
            "ERROR": "⚫",
        }

        emoji = STATUS_EMOJI.get(new_status, "⚪")
        divider = f"{emoji}━━━━━━━━━━━━━━━━━━━━{emoji}"

        if new_status == "MISSING":
            alert = "🔴 ACCOUNT MISSING"
            desc = "Account may be banned, deleted, or deactivated."
        elif new_status == "ACTIVE":
            alert = "🟢 ACCOUNT RESTORED"
            desc = "Account is back online."
        else:
            alert = "⚠️ STATUS CHANGE"
            desc = ""

        lines = [
            divider,
            "",
            alert,
            "",
            f"{emoji} <b>@{username}</b>",
            f"    {old_status} → {new_status}",
        ]

        if new_status == "ACTIVE" and profile_data:
            stats = []
            if profile_data.get("followers"):
                stats.append(f"👥 {profile_data['followers']}")
            if profile_data.get("following"):
                stats.append(f"➡️ {profile_data['following']}")
            if profile_data.get("posts"):
                stats.append(f"📝 {profile_data['posts']}")
            if stats:
                lines.append(f"    {' · '.join(stats)}")

        if duration and duration != "unknown":
            lines.extend(["", f"⏱ Monitored for {duration}"])

        if desc:
            lines.extend(["", desc])

        lines.extend(["", divider])

        return "\n".join(lines)

    def _calc_duration(self, created_at: Optional[str]) -> str:
        if not created_at:
            return "unknown"
        try:
            start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - start
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            if hours > 0:
                return f"{hours}h {minutes}m"
            return f"{minutes}m"
        except Exception:
            return "unknown"

    def _interruptible_sleep(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end and self.running:
            time.sleep(min(1.0, max(0.1, end - time.time())))

    def check_single(self, username: str) -> dict:
        result = check_account(username, self.config)
        if result["classification"] == "MISSING":
            result = verify_with_playwright(username, result, self.config)

        account_id = self.db.get_or_create_account(username)
        old_status = self.db.get_account_status(username)
        new_status = result["classification"]

        self.db.update_account_status(account_id, new_status)

        check_data = {
            "account_id": account_id,
            "timestamp": result.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "status": new_status,
            "status_code": result.get("status_code"),
            "latency_ms": result.get("latency_ms", 0),
            "response_size": result.get("response_size", 0),
            "response_hash": result.get("response_hash", ""),
            "raw_response_path": result.get("raw_response_path", ""),
            "verification_status": result.get("verification_status"),
            "error_message": result.get("error_message"),
            "retry_count": result.get("retry_count", 0),
        }
        self.db.save_check(check_data)

        is_transition = old_status is not None and old_status != new_status
        if is_transition:
            self._handle_transition(account_id, username, old_status or "UNKNOWN", new_status, result)

        return {
            "username": username,
            "status": new_status,
            "old_status": old_status,
            "transition": is_transition,
            "latency_ms": result.get("latency_ms", 0),
            "status_code": result.get("status_code"),
        }

    def get_uptime(self) -> str:
        if not self.start_time:
            return "not started"
        delta = datetime.now(timezone.utc) - self.start_time
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours}h {minutes}m {seconds}s"
