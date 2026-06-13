"""Telegram bot interface."""

import logging
from datetime import datetime, timezone
from typing import Optional

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .config import Config
from .database import Database
from .monitor import Monitor

logger = logging.getLogger("monitor.telegram")

STATUS_EMOJI = {
    "ACTIVE": "🟢",
    "MISSING": "🔴",
    "SUSPECT": "🟡",
    "UNKNOWN": "⚪",
    "ERROR": "⚫",
    "RATE_LIMITED": "🟠",
}


class TelegramBot:
    def __init__(self, config: Config, db: Database, monitor: Monitor):
        self.config = config
        self.db = db
        self.monitor = monitor
        self.app: Optional[Application] = None

    def build(self) -> Application:
        self.app = Application.builder().token(self.config.telegram_token).build()

        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("health", self.cmd_health))
        self.app.add_handler(CommandHandler("accounts", self.cmd_accounts))
        self.app.add_handler(CommandHandler("add", self.cmd_add))
        self.app.add_handler(CommandHandler("remove", self.cmd_remove))
        self.app.add_handler(CommandHandler("check", self.cmd_check))
        self.app.add_handler(CommandHandler("test", self.cmd_test))

        return self.app

    async def post_init(self, application: Application):
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show available commands"),
            BotCommand("health", "Bot health & uptime"),
            BotCommand("accounts", "List monitored accounts"),
            BotCommand("add", "Add account — /add username"),
            BotCommand("remove", "Remove account — /remove username"),
            BotCommand("check", "Manual check — /check username"),
            BotCommand("test", "Test account (no monitor) — /test username"),
        ]
        await application.bot.set_my_commands(commands)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "📸 *Instagram Monitor*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 *Status:* Running\n"
            f"📡 *Monitoring:* {len(self.config.accounts)} accounts\n"
            f"⏱ *Interval:* {self.config.check_interval}s\n\n"
            "Use /help to see commands."
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "📖 *Commands*\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "/start — Start the bot\n"
            "/help — Show this message\n"
            "/health — Bot status & uptime\n"
            "/accounts — List monitored accounts\n"
            "/add `username` — Add an account\n"
            "/remove `username` — Remove an account\n"
            "/check `username` — Manual check\n"
            "/test `username` — Test account (no monitor)\n"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        accounts = self.db.get_all_accounts()
        recent_checks = self.db.get_recent_checks(limit=5)
        recent_events = self.db.get_recent_events(limit=5)

        lines = [
            "🏥 *Health Report*",
            "━━━━━━━━━━━━━━━━━━━",
            "",
            f"⏱ *Uptime:* {self.monitor.get_uptime()}",
            f"📡 *Accounts:* {len(accounts)}",
        ]

        if recent_checks:
            lines.append("")
            lines.append("📋 *Recent Checks:*")
            for c in recent_checks[:5]:
                emoji = STATUS_EMOJI.get(c["status"], "⚪")
                lines.append(f"  {emoji} @{c['username']} — {c['status']} ({c['latency_ms']:.0f}ms)")

        if recent_events:
            lines.append("")
            lines.append("🔔 *Recent Events:*")
            for e in recent_events[:5]:
                old_e = STATUS_EMOJI.get(e["old_status"], "⚪")
                new_e = STATUS_EMOJI.get(e["new_status"], "⚪")
                lines.append(f"  {old_e}→{new_e} @{e['username']}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        accounts = self.db.get_all_accounts()
        if not accounts:
            await update.message.reply_text(
                "📭 *No accounts monitored*\n\nUse /add `username` to add one.",
                parse_mode="Markdown",
            )
            return

        lines = [
            "📡 *Monitored Accounts*",
            "━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for a in accounts:
            emoji = STATUS_EMOJI.get(a["status"], "⚪")
            lines.append(f"{emoji} @{a['username']}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 *Usage:* /add `username`",
                parse_mode="Markdown",
            )
            return

        username = context.args[0].lstrip("@")
        if username in self.config.accounts:
            await update.message.reply_text(
                f"⚠️ @{username} is already being monitored.",
                parse_mode="Markdown",
            )
            return

        self.config.accounts.append(username)
        self.db.get_or_create_account(username)
        await update.message.reply_text(
            f"✅ *@{username}* added to monitoring.",
            parse_mode="Markdown",
        )

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 *Usage:* /remove `username`",
                parse_mode="Markdown",
            )
            return

        username = context.args[0].lstrip("@")
        if username not in self.config.accounts:
            await update.message.reply_text(
                f"❌ @{username} is not being monitored.",
                parse_mode="Markdown",
            )
            return

        self.config.accounts.remove(username)
        self.db.remove_account(username)
        await update.message.reply_text(
            f"🗑 *@{username}* removed from monitoring.",
            parse_mode="Markdown",
        )

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 *Usage:* /check `username`",
                parse_mode="Markdown",
            )
            return

        username = context.args[0].lstrip("@")
        await update.message.reply_text(f"🔍 Checking @{username}...")

        try:
            result = self.monitor.check_single(username)
            emoji = STATUS_EMOJI.get(result["status"], "⚪")
            transition = "⚡ Yes" if result["transition"] else "— No"

            text = (
                f"📸 *@{username}*\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"{emoji} *Status:* {result['status']}\n"
                f"📊 *Previous:* {result.get('old_status', 'N/A')}\n"
                f"⚡ *Transition:* {transition}\n"
                f"🏎 *Latency:* {result['latency_ms']:.0f}ms\n"
                f"🔢 *HTTP:* {result.get('status_code', 'N/A')}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error checking @{username}:\n`{e}`",
                parse_mode="Markdown",
            )

    async def cmd_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 *Usage:* /test `username`",
                parse_mode="Markdown",
            )
            return

        username = context.args[0].lstrip("@")
        await update.message.reply_text(f"🧪 Testing @{username}...")

        try:
            from .checker import check_account, capture_profile_screenshot

            result = check_account(username, self.config)
            emoji = STATUS_EMOJI.get(result["classification"], "⚪")

            profile_data = {}
            screenshot_path = None
            if result["classification"] == "ACTIVE":
                screenshot_data = capture_profile_screenshot(username, self.config, "test")
                screenshot_path = screenshot_data.get("screenshot_path")
                profile_data = screenshot_data.get("profile_data", {})

            caption = (
                f"🧪 *Test Result*\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"📸 *@{username}*\n\n"
                f"{emoji} *Status:* {result['classification']}\n"
                f"🏎 *Latency:* {result.get('latency_ms', 0):.0f}ms\n"
                f"🔢 *HTTP:* {result.get('status_code', 'N/A')}"
            )

            if profile_data:
                if profile_data.get("followers"):
                    caption += f"\n👥 *Followers:* {profile_data['followers']}"
                if profile_data.get("following"):
                    caption += f"\n➡️ *Following:* {profile_data['following']}"
                if profile_data.get("posts"):
                    caption += f"\n📝 *Posts:* {profile_data['posts']}"

            if screenshot_path:
                await update.message.reply_photo(
                    photo=open(screenshot_path, "rb"),
                    caption=caption,
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(caption, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error testing @{username}:\n`{e}`",
                parse_mode="Markdown",
            )

    def notify(self, message: str):
        if not self.app or not self.app.bot:
            logger.warning("Telegram bot not initialized, cannot send notification")
            return

        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                asyncio.ensure_future(self._send_notification(message))
            elif loop:
                loop.run_until_complete(self._send_notification(message))
            else:
                asyncio.run(self._send_notification(message))
        except Exception as e:
            logger.error(f"Failed to queue notification: {e}")

    async def _send_notification(self, message: str):
        try:
            chat_id = self.config.telegram_chat_id
            if not chat_id:
                logger.warning("No telegram_chat_id configured, skipping notification")
                return
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    def notify_photo(self, photo_path: str, caption: str):
        if not self.app or not self.app.bot:
            logger.warning("Telegram bot not initialized, cannot send photo")
            return

        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                asyncio.ensure_future(self._send_photo(photo_path, caption))
            elif loop:
                loop.run_until_complete(self._send_photo(photo_path, caption))
            else:
                asyncio.run(self._send_photo(photo_path, caption))
        except Exception as e:
            logger.error(f"Failed to queue photo: {e}")

    async def _send_photo(self, photo_path: str, caption: str):
        try:
            chat_id = self.config.telegram_chat_id
            if not chat_id:
                logger.warning("No telegram_chat_id configured, skipping photo")
                return

            import os
            if not os.path.exists(photo_path):
                logger.warning(f"Screenshot not found: {photo_path}")
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=caption,
                    parse_mode="Markdown",
                )
                return

            with open(photo_path, "rb") as photo:
                await self.app.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"Failed to send Telegram photo: {e}")
