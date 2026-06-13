"""Telegram bot interface."""

import logging
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import Config
from .database import Database
from .monitor import Monitor

logger = logging.getLogger("monitor.telegram")


class TelegramBot:
    def __init__(self, config: Config, db: Database, monitor: Monitor):
        self.config = config
        self.db = db
        self.monitor = monitor
        self.app: Optional[Application] = None

    def build(self) -> Application:
        self.app = Application.builder().token(self.config.telegram_token).build()

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("health", self.cmd_health))
        self.app.add_handler(CommandHandler("accounts", self.cmd_accounts))
        self.app.add_handler(CommandHandler("add", self.cmd_add))
        self.app.add_handler(CommandHandler("remove", self.cmd_remove))
        self.app.add_handler(CommandHandler("check", self.cmd_check))

        return self.app

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Instagram Monitor Bot\n"
            "Status: Running\n"
            f"Accounts: {len(self.config.accounts)}\n"
            f"Interval: {self.config.check_interval}s"
        )

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        accounts = self.db.get_all_accounts()
        recent_checks = self.db.get_recent_checks(limit=5)
        recent_events = self.db.get_recent_events(limit=5)

        lines = [
            "Health Report",
            f"Uptime: {self.monitor.get_uptime()}",
            f"Monitored accounts: {len(accounts)}",
            "",
            "Recent Checks:",
        ]
        for c in recent_checks[:5]:
            lines.append(f"  {c['username']}: {c['status']} ({c['latency_ms']:.0f}ms)")

        if recent_events:
            lines.append("")
            lines.append("Recent Events:")
            for e in recent_events[:5]:
                lines.append(f"  {e['username']}: {e['old_status']} -> {e['new_status']}")

        await update.message.reply_text("\n".join(lines))

    async def cmd_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        accounts = self.db.get_all_accounts()
        if not accounts:
            await update.message.reply_text("No accounts being monitored.")
            return

        lines = ["Monitored Accounts:"]
        for a in accounts:
            lines.append(f"{a['status']}\n  @{a['username']}")

        await update.message.reply_text("\n".join(lines))

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /add <username>")
            return

        username = context.args[0].lstrip("@")
        if username in self.config.accounts:
            await update.message.reply_text(f"@{username} is already being monitored.")
            return

        self.config.accounts.append(username)
        self.db.get_or_create_account(username)
        await update.message.reply_text(f"Added @{username} to monitoring.")

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /remove <username>")
            return

        username = context.args[0].lstrip("@")
        if username not in self.config.accounts:
            await update.message.reply_text(f"@{username} is not being monitored.")
            return

        self.config.accounts.remove(username)
        self.db.remove_account(username)
        await update.message.reply_text(f"Removed @{username} from monitoring.")

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /check <username>")
            return

        username = context.args[0].lstrip("@")
        await update.message.reply_text(f"Checking @{username}...")

        try:
            result = self.monitor.check_single(username)
            lines = [
                f"Check Result for @{username}:",
                f"Status: {result['status']}",
                f"Previous: {result.get('old_status', 'N/A')}",
                f"Transition: {'Yes' if result['transition'] else 'No'}",
                f"Latency: {result['latency_ms']:.0f}ms",
                f"Status Code: {result.get('status_code', 'N/A')}",
            ]
            await update.message.reply_text("\n".join(lines))
        except Exception as e:
            await update.message.reply_text(f"Error checking @{username}: {e}")

    def notify(self, message: str):
        if not self.app or not self.app.bot:
            logger.warning("Telegram bot not initialized, cannot send notification")
            return

        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._send_notification(message))
            else:
                loop.run_until_complete(self._send_notification(message))
        except Exception as e:
            logger.error(f"Failed to queue notification: {e}")

    async def _send_notification(self, message: str):
        try:
            await self.app.bot.send_message(
                chat_id=self.config.telegram_chat_id,
                text=message,
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
