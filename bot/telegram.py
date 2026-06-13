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
            "📸 <b>Instagram Monitor</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"🟢 <b>Status:</b> Running\n"
            f"📡 <b>Monitoring:</b> {len(self.config.accounts)} accounts\n"
            f"⏱ <b>Interval:</b> {self.config.check_interval}s\n\n"
            "Use /help to see commands."
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (
            "📖 <b>Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "/start — Start the bot\n"
            "/help — Show this message\n"
            "/health — Bot status & uptime\n"
            "/accounts — List monitored accounts\n"
            "/add <code>username</code> — Add an account\n"
            "/remove <code>username</code> — Remove an account\n"
            "/check <code>username</code> — Manual check\n"
            "/test <code>username</code> — Test account (no monitor)\n"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        accounts = self.db.get_all_accounts()
        recent_checks = self.db.get_recent_checks(limit=5)
        recent_events = self.db.get_recent_events(limit=5)

        lines = [
            "🏥 <b>Health Report</b>",
            "━━━━━━━━━━━━━━━━━━━",
            "",
            f"⏱ <b>Uptime:</b> {self.monitor.get_uptime()}",
            f"📡 <b>Accounts:</b> {len(accounts)}",
        ]

        if recent_checks:
            lines.append("")
            lines.append("📋 <b>Recent Checks:</b>")
            for c in recent_checks[:5]:
                emoji = STATUS_EMOJI.get(c["status"], "⚪")
                lines.append(f"  {emoji} @{c['username']} — {c['status']} ({c['latency_ms']:.0f}ms)")

        if recent_events:
            lines.append("")
            lines.append("🔔 <b>Recent Events:</b>")
            for e in recent_events[:5]:
                old_e = STATUS_EMOJI.get(e["old_status"], "⚪")
                new_e = STATUS_EMOJI.get(e["new_status"], "⚪")
                lines.append(f"  {old_e}→{new_e} @{e['username']}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        accounts = self.db.get_all_accounts()
        if not accounts:
            await update.message.reply_text(
                "📭 <b>No accounts monitored</b>\n\nUse /add <code>username</code> to add one.",
                parse_mode="HTML",
            )
            return

        lines = [
            "📡 <b>Monitored Accounts</b>",
            "━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for a in accounts:
            emoji = STATUS_EMOJI.get(a["status"], "⚪")
            lines.append(f"{emoji} @{a['username']}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /add <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        if username in self.config.accounts:
            await update.message.reply_text(
                f"⚠️ @{username} is already being monitored.",
                parse_mode="HTML",
            )
            return

        self.config.accounts.append(username)
        self.db.get_or_create_account(username)
        await update.message.reply_text(
            f"✅ <b>@{username}</b> added to monitoring.\n🔍 Checking status...",
            parse_mode="HTML",
        )

        try:
            from .checker import check_account, capture_profile_screenshot

            result = check_account(username, self.config)
            emoji = STATUS_EMOJI.get(result["classification"], "⚪")

            profile_data = {}
            screenshot_path = None
            if result["classification"] == "ACTIVE":
                screenshot_data = capture_profile_screenshot(username, self.config, "add")
                screenshot_path = screenshot_data.get("screenshot_path")
                profile_data = screenshot_data.get("profile_data", {})

            caption = (
                f"{emoji} <b>@{username}</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"<b>Status:</b> {result['classification']}\n"
                f"🏎 <b>Latency:</b> {result.get('latency_ms', 0):.0f}ms\n"
                f"🔢 <b>HTTP:</b> {result.get('status_code', 'N/A')}"
            )

            if profile_data:
                if profile_data.get("followers"):
                    caption += f"\n👥 <b>Followers:</b> {profile_data['followers']}"
                if profile_data.get("following"):
                    caption += f"\n➡️ <b>Following:</b> {profile_data['following']}"
                if profile_data.get("posts"):
                    caption += f"\n📝 <b>Posts:</b> {profile_data['posts']}"

            if screenshot_path:
                await update.message.reply_photo(
                    photo=open(screenshot_path, "rb"),
                    caption=caption,
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(caption, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"⚠️ Added but check failed:\n<code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /remove <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        if username not in self.config.accounts:
            await update.message.reply_text(
                f"❌ @{username} is not being monitored.",
                parse_mode="HTML",
            )
            return

        self.config.accounts.remove(username)
        self.db.remove_account(username)
        await update.message.reply_text(
            f"🗑 <b>@{username}</b> removed from monitoring.",
            parse_mode="HTML",
        )

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /check <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        await update.message.reply_text(f"🔍 Checking @{username}...")

        try:
            result = self.monitor.check_single(username)
            emoji = STATUS_EMOJI.get(result["status"], "⚪")
            transition = "⚡ Yes" if result["transition"] else "— No"

            text = (
                f"📸 <b>@{username}</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"{emoji} <b>Status:</b> {result['status']}\n"
                f"📊 <b>Previous:</b> {result.get('old_status', 'N/A')}\n"
                f"⚡ <b>Transition:</b> {transition}\n"
                f"🏎 <b>Latency:</b> {result['latency_ms']:.0f}ms\n"
                f"🔢 <b>HTTP:</b> {result.get('status_code', 'N/A')}"
            )
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error checking @{username}:\n<code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /test <code>username</code>",
                parse_mode="HTML",
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
                f"🧪 <b>Test Result</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"📸 <b>@{username}</b>\n\n"
                f"{emoji} <b>Status:</b> {result['classification']}\n"
                f"🏎 <b>Latency:</b> {result.get('latency_ms', 0):.0f}ms\n"
                f"🔢 <b>HTTP:</b> {result.get('status_code', 'N/A')}"
            )

            if profile_data:
                if profile_data.get("followers"):
                    caption += f"\n👥 <b>Followers:</b> {profile_data['followers']}"
                if profile_data.get("following"):
                    caption += f"\n➡️ <b>Following:</b> {profile_data['following']}"
                if profile_data.get("posts"):
                    caption += f"\n📝 <b>Posts:</b> {profile_data['posts']}"

            if screenshot_path:
                await update.message.reply_photo(
                    photo=open(screenshot_path, "rb"),
                    caption=caption,
                    parse_mode="HTML",
                )
            else:
                await update.message.reply_text(caption, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error testing @{username}:\n<code>{e}</code>",
                parse_mode="HTML",
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
                parse_mode="HTML",
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
                    parse_mode="HTML",
                )
                return

            with open(photo_path, "rb") as photo:
                await self.app.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode="HTML",
                )
        except Exception as e:
            logger.error(f"Failed to send Telegram photo: {e}")
