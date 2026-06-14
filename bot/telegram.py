"""Telegram bot interface."""

import logging
import os
import time as _time
import zipfile
from datetime import datetime, timezone
from typing import Optional

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

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

ADMIN_NOTIFY = "@vaproh"


class TelegramBot:
    def __init__(self, config: Config, db: Database, monitor: Monitor):
        self.config = config
        self.db = db
        self.monitor = monitor
        self.app: Optional[Application] = None
        self._admin_notified_cookies = False

    def _get_username(self, update: Update) -> str:
        user = update.effective_user
        if user:
            return user.username or ""
        return ""

    def _check_access(self, update: Update) -> bool:
        username = self._get_username(update)
        if not username:
            return False
        return self.db.is_admin(username) or self.db.is_allowed_user(username)

    def _is_admin(self, update: Update) -> bool:
        return self.db.is_admin(self._get_username(update))

    def _deny(self, update):
        return update.message.reply_text(
            f"⛔ <b>Access Denied</b>\n\n"
            f"You don't have access to this bot. Contact admin: {ADMIN_NOTIFY}",
            parse_mode="HTML",
        )

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
        self.app.add_handler(CommandHandler("ping", self.cmd_ping))
        self.app.add_handler(CommandHandler("proxy", self.cmd_proxy))
        self.app.add_handler(CommandHandler("mainmenu", self.cmd_mainmenu))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("addadmin", self.cmd_addadmin))
        self.app.add_handler(CommandHandler("removeadmin", self.cmd_removeadmin))
        self.app.add_handler(CommandHandler("adduser", self.cmd_adduser))
        self.app.add_handler(CommandHandler("removeuser", self.cmd_removeuser))
        self.app.add_handler(CommandHandler("setcookie", self.cmd_setcookie))
        self.app.add_handler(CommandHandler("backup", self.cmd_backup))
        self.app.add_handler(CommandHandler("listusers", self.cmd_listusers))

        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        return self.app

    async def post_init(self, application: Application):
        commands = [
            BotCommand("start", "📸 Start the bot"),
            BotCommand("mainmenu", "📋 Main menu"),
            BotCommand("help", "📖 Show available commands"),
            BotCommand("status", "📡 All monitored accounts"),
            BotCommand("add", "➕ Add account to monitor"),
            BotCommand("remove", "➖ Remove account"),
            BotCommand("check", "🔍 Manual check an account"),
            BotCommand("test", "🧪 Test account (no monitor)"),
            BotCommand("proxy", "📊 Proxy traffic stats"),
            BotCommand("ping", "🏓 Check bot latency"),
            BotCommand("health", "🏥 Bot health & uptime"),
            BotCommand("setcookie", "🍪 Upload cookies (admin)"),
            BotCommand("backup", "💾 Backup data (admin)"),
            BotCommand("adduser", "👥 Allow user (admin)"),
            BotCommand("removeuser", "👥⛔ Remove user (admin)"),
            BotCommand("addadmin", "🔑 Add admin (admin)"),
            BotCommand("removeadmin", "🔑⛔ Remove admin (admin)"),
            BotCommand("listusers", "📋 List users (admin)"),
        ]
        await application.bot.set_my_commands(commands)

    def _build_main_menu(self, is_admin: bool) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("➕ Add Account", callback_data="menu:add"),
                InlineKeyboardButton("➖ Remove", callback_data="menu:remove"),
                InlineKeyboardButton("🔍 Check", callback_data="menu:check"),
            ],
            [
                InlineKeyboardButton("📡 Status", callback_data="menu:status"),
                InlineKeyboardButton("📊 Proxy", callback_data="menu:proxy"),
                InlineKeyboardButton("🏓 Ping", callback_data="menu:ping"),
            ],
            [
                InlineKeyboardButton("🧪 Test", callback_data="menu:test"),
                InlineKeyboardButton("🏥 Health", callback_data="menu:health"),
            ],
        ]
        if is_admin:
            rows.append([
                InlineKeyboardButton("👥 Add User", callback_data="menu:adduser"),
                InlineKeyboardButton("👥⛔ Remove User", callback_data="menu:removeuser"),
            ])
            rows.append([
                InlineKeyboardButton("🔑 Set Cookie", callback_data="menu:setcookie"),
                InlineKeyboardButton("💾 Backup", callback_data="menu:backup"),
            ])
        return InlineKeyboardMarkup(rows)

    async def _handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        if not self._check_access(update):
            await query.edit_message_text(
                f"⛔ <b>Access Denied</b>\n\nContact admin: {ADMIN_NOTIFY}",
                parse_mode="HTML",
            )
            return

        data = query.data
        is_admin = self._is_admin(update)

        if data == "menu:add":
            await query.edit_message_text(
                "📝 <b>Add Account</b>\n\nSend: <code>/add username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:remove":
            await query.edit_message_text(
                "📝 <b>Remove Account</b>\n\nSend: <code>/remove username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:check":
            await query.edit_message_text(
                "🔍 <b>Check Account</b>\n\nSend: <code>/check username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:test":
            await query.edit_message_text(
                "🧪 <b>Test Account</b>\n\nSend: <code>/test username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:status":
            await self._handle_status_callback(query)
        elif data == "menu:proxy":
            await self._handle_proxy_callback(query)
        elif data == "menu:ping":
            start = _time.time()
            latency = (_time.time() - start) * 1000
            await query.edit_message_text(
                f"🏓 <b>Pong!</b> {latency:.0f}ms\n"
                f"📡 Monitoring: {len(self.db.get_all_accounts())} accounts\n"
                f"⏱ Uptime: {self.monitor.get_uptime()}",
                parse_mode="HTML",
            )
        elif data == "menu:health":
            accounts = self.db.get_all_accounts()
            await query.edit_message_text(
                f"🏥 <b>Health Report</b>\n\n"
                f"⏱ Uptime: {self.monitor.get_uptime()}\n"
                f"📡 Accounts: {len(accounts)}",
                parse_mode="HTML",
            )
        elif data == "menu:adduser":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text(
                "📝 <b>Add User</b>\n\nSend: <code>/adduser username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:removeuser":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text(
                "📝 <b>Remove User</b>\n\nSend: <code>/removeuser username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:setcookie":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text(
                "🍪 <b>Upload Cookies</b>\n\nSend a file named <code>cookies.txt</code> with /setcookie",
                parse_mode="HTML",
            )
        elif data == "menu:backup":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text("💾 Creating backup...", parse_mode="HTML")
            await self._do_backup(query.message, update)

    async def _handle_status_callback(self, query):
        accounts = self.db.get_all_accounts()
        if not accounts:
            await query.edit_message_text(
                "📡 <b>Status</b>\n\nNo accounts monitored.\nUse /add to add one.",
                parse_mode="HTML",
            )
            return
        lines = ["📡 <b>All Monitored Accounts</b>\n"]
        for a in accounts:
            emoji = STATUS_EMOJI.get(a["status"], "⚪")
            last = a.get("last_check") or "never"
            if last != "never":
                try:
                    dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    last = dt.strftime("%H:%M UTC")
                except Exception:
                    pass
            lines.append(f"{emoji} @{a['username']} — {a['status']} (last: {last})")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")

    async def _handle_proxy_callback(self, query):
        if not self.config.proxy.enabled:
            await query.edit_message_text("⚠️ Proxy is not enabled.", parse_mode="HTML")
            return
        try:
            import requests
            from requests.auth import HTTPBasicAuth
            resp = requests.get(
                "https://gw.dataimpulse.com:777/api/stats",
                auth=HTTPBasicAuth(self.config.proxy.username, self.config.proxy.password),
                timeout=10,
            )
            data = resp.json()
            if data.get("status") != "ok":
                await query.edit_message_text(f"❌ API Error: {data.get('message')}", parse_mode="HTML")
                return
            total = data.get("total_traffic", 0)
            used = data.get("traffic_used", 0)
            left = data.get("traffic_left", 0)

            def fmt(b):
                if abs(b) >= 1073741824:
                    return f"{b / 1073741824:.2f} GB"
                elif abs(b) >= 1048576:
                    return f"{b / 1048576:.2f} MB"
                return f"{b / 1024:.2f} KB"

            pct = (used / total * 100) if total > 0 else 0
            bar_len = 20
            filled = int(bar_len * pct / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            cost = (used / 1073741824) * 1.2

            text = (
                "📊 <b>Proxy Stats</b>\n\n"
                f"📦 Total: {fmt(total)}\n"
                f"📤 Used: {fmt(used)} ({pct:.1f}%)\n"
                f"📥 Remaining: {fmt(left)}\n\n"
                f"<code>[{bar}]</code> {pct:.1f}%\n\n"
                f"💰 Rate: $1.2/GB\n"
                f"💸 Cost: ${cost:.2f}"
            )
            await query.edit_message_text(text, parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"❌ Error: <code>{e}</code>", parse_mode="HTML")

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        text = (
            "📸 <b>Instagram Monitor Bot</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "Monitors Instagram accounts for visibility changes.\n\n"
            "<b>How to use:</b>\n"
            "• /add username — Start monitoring\n"
            "• /remove username — Stop monitoring\n"
            "• /status — View all monitored accounts\n"
            "• /test username — Test without monitoring\n"
            "• /mainmenu — Open main menu\n\n"
            "<b>Need help?</b> /help\n"
            "━━━━━━━━━━━━━━━━━━━"
        )
        kb = self._build_main_menu(self._is_admin(update))
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    async def cmd_mainmenu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        kb = self._build_main_menu(self._is_admin(update))
        await update.message.reply_text(
            "📋 <b>Main Menu</b>\n━━━━━━━━━━━━━━━━━━━",
            parse_mode="HTML",
            reply_markup=kb,
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        text = (
            "📖 <b>Commands</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "📸 /start — Start the bot\n"
            "📋 /mainmenu — Main menu\n"
            "📖 /help — Show this message\n"
            "🏥 /health — Bot status & uptime\n"
            "📡 /status — All monitored accounts\n"
            "📡 /accounts — List monitored accounts\n"
            "➕ /add <code>username</code> — Add an account\n"
            "➖ /remove <code>username</code> — Remove an account\n"
            "🔍 /check <code>username</code> — Manual check\n"
            "🧪 /test <code>username</code> — Test account (no monitor)\n"
            "🏓 /ping — Check bot latency\n"
            "📊 /proxy — Proxy traffic stats"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

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
        if not self._check_access(update):
            await self._deny(update)
            return

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

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        accounts = self.db.get_all_accounts()
        if not accounts:
            await update.message.reply_text(
                "📡 <b>Status</b>\n\nNo accounts monitored.\nUse /add to add one.",
                parse_mode="HTML",
            )
            return

        lines = ["📡 <b>All Monitored Accounts</b>", "━━━━━━━━━━━━━━━━━━━", ""]
        for a in accounts:
            emoji = STATUS_EMOJI.get(a["status"], "⚪")
            last = a.get("last_check") or "never"
            if last != "never":
                try:
                    dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    last = dt.strftime("%H:%M UTC")
                except Exception:
                    pass
            count = a.get("check_count", 0)
            lines.append(f"{emoji} @{a['username']} — {a['status']} (last: {last}, checks: {count})")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /add <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        existing = self.db.get_account_status(username)
        if existing is not None:
            await update.message.reply_text(
                f"⚠️ @{username} is already being monitored.",
                parse_mode="HTML",
            )
            return

        self.db.get_or_create_account(username)
        await update.message.reply_text(
            f"✅ <b>@{username}</b> added.\n🔍 Checking status...",
            parse_mode="HTML",
        )

        try:
            from .checker import check_account, capture_profile_screenshot

            result = check_account(username, self.config)
            status = result["classification"]

            profile_data = {}
            screenshot_path = None
            if status == "ACTIVE":
                screenshot_data = capture_profile_screenshot(username, self.config, "add")
                screenshot_path = screenshot_data.get("screenshot_path")
                profile_data = screenshot_data.get("profile_data", {})

            emoji = STATUS_EMOJI.get(status, "⚪")
            divider = f"{emoji}━━━━━━━━━━━━━━━━━━━━{emoji}"

            lines = [
                divider,
                "",
                f"{emoji} @{username}",
                "",
                f"<b>Status:</b> {status}",
            ]

            if status == "ACTIVE" and profile_data:
                if profile_data.get("followers"):
                    lines.append(f"<b>Followers:</b> {profile_data['followers']}")
                if profile_data.get("following"):
                    lines.append(f"<b>Following:</b> {profile_data['following']}")
                if profile_data.get("posts"):
                    lines.append(f"<b>Posts:</b> {profile_data['posts']}")

            lines.extend(["", divider])
            caption = "\n".join(lines)

            if screenshot_path and os.path.exists(screenshot_path):
                with open(screenshot_path, "rb") as f:
                    await update.message.reply_photo(
                        photo=f,
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
        if not self._check_access(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /remove <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        existing = self.db.get_account_status(username)
        if existing is None:
            await update.message.reply_text(
                f"❌ @{username} is not being monitored.",
                parse_mode="HTML",
            )
            return

        self.db.remove_account(username)
        await update.message.reply_text(
            f"🗑 <b>@{username}</b> removed from monitoring.",
            parse_mode="HTML",
        )

    async def cmd_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

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
                f"⚡ <b>Transition:</b> {transition}"
            )
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Error checking @{username}:\n<code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

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

            if screenshot_path and os.path.exists(screenshot_path):
                with open(screenshot_path, "rb") as f:
                    await update.message.reply_photo(
                        photo=f,
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

    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        start = _time.time()
        msg = await update.message.reply_text("🏓 Pinging...")
        latency = (_time.time() - start) * 1000

        await msg.edit_text(
            f"🏓 <b>Pong!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"⚡ <b>Bot Latency:</b> {latency:.0f}ms\n"
            f"🟢 <b>Status:</b> Online\n"
            f"📡 <b>Monitoring:</b> {len(self.db.get_all_accounts())} accounts\n"
            f"⏱ <b>Uptime:</b> {self.monitor.get_uptime()}",
            parse_mode="HTML",
        )

    async def cmd_proxy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not self.config.proxy.enabled:
            await update.message.reply_text(
                "⚠️ Proxy is not enabled in config.",
                parse_mode="HTML",
            )
            return

        await update.message.reply_text("🔄 Fetching proxy stats...")

        try:
            import requests
            from requests.auth import HTTPBasicAuth

            resp = requests.get(
                "https://gw.dataimpulse.com:777/api/stats",
                auth=HTTPBasicAuth(self.config.proxy.username, self.config.proxy.password),
                timeout=10,
            )
            data = resp.json()

            if data.get("status") != "ok":
                await update.message.reply_text(
                    f"❌ API Error: {data.get('message', 'Unknown error')}",
                    parse_mode="HTML",
                )
                return

            total_bytes = data.get("total_traffic", 0)
            used_bytes = data.get("traffic_used", 0)
            left_bytes = data.get("traffic_left", 0)

            def fmt_bytes(b):
                if abs(b) >= 1073741824:
                    return f"{b / 1073741824:.2f} GB"
                elif abs(b) >= 1048576:
                    return f"{b / 1048576:.2f} MB"
                elif abs(b) >= 1024:
                    return f"{b / 1024:.2f} KB"
                return f"{b} B"

            if total_bytes > 0:
                pct_used = (used_bytes / total_bytes) * 100
                bar_len = 20
                filled = int(bar_len * pct_used / 100)
                bar = "█" * filled + "░" * (bar_len - filled)
            else:
                pct_used = 0
                bar = "░" * 20

            rate_per_gb = 1.2
            used_gb = used_bytes / 1073741824
            cost = used_gb * rate_per_gb

            text = (
                "📊 <b>Proxy Stats</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 <b>Total Traffic:</b> {fmt_bytes(total_bytes)}\n"
                f"📤 <b>Used:</b> {fmt_bytes(used_bytes)} ({pct_used:.1f}%)\n"
                f"📥 <b>Remaining:</b> {fmt_bytes(left_bytes)}\n\n"
                f"<code>[{bar}]</code> {pct_used:.1f}%\n\n"
                f"💰 <b>Rate:</b> ${rate_per_gb:.1f}/GB\n"
                f"💸 <b>Cost So Far:</b> ${cost:.2f}"
            )

            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Failed to fetch proxy stats:\n<code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_addadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /addadmin <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        if self.db.add_admin(username):
            await update.message.reply_text(f"✅ <b>@{username}</b> is now an admin.", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ @{username} is already an admin.", parse_mode="HTML")

    async def cmd_removeadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /removeadmin <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        current_user = self._get_username(update)
        if username == current_user:
            await update.message.reply_text("❌ You can't remove yourself.", parse_mode="HTML")
            return

        if self.db.remove_admin(username):
            await update.message.reply_text(f"🗑 <b>@{username}</b> removed from admins.", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Can't remove — must have at least one admin.", parse_mode="HTML")

    async def cmd_adduser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /adduser <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        if self.db.add_allowed_user(username):
            await update.message.reply_text(f"✅ <b>@{username}</b> can now use the bot.", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ @{username} already has access.", parse_mode="HTML")

    async def cmd_removeuser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "📝 <b>Usage:</b> /removeuser <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        if self.db.remove_allowed_user(username):
            await update.message.reply_text(f"🗑 <b>@{username}</b> removed from allowed users.", parse_mode="HTML")
        else:
            await update.message.reply_text(f"❌ @{username} wasn't in allowed users.", parse_mode="HTML")

    async def cmd_listusers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        admins = self.db.get_all_admins()
        users = self.db.get_all_allowed_users()

        lines = [
            "👥 <b>Access Control</b>",
            "━━━━━━━━━━━━━━━━━━━",
            "",
            "🔑 <b>Admins:</b>",
        ]
        for a in admins:
            lines.append(f"  • @{a}")

        lines.append("")
        lines.append("👤 <b>Allowed Users:</b>")
        if users:
            for u in users:
                lines.append(f"  • @{u}")
        else:
            lines.append("  (none)")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_setcookie(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        if not update.message.document:
            await update.message.reply_text(
                "🍪 <b>Upload Cookies</b>\n\n"
                "Send a file named <code>cookies.txt</code> with this command.",
                parse_mode="HTML",
            )
            return

        doc = update.message.document
        if doc.file_name != "cookies.txt":
            await update.message.reply_text(
                "❌ File must be named <code>cookies.txt</code>",
                parse_mode="HTML",
            )
            return

        try:
            file = await doc.get_file()
            content = await file.download_as_bytearray()
            text_content = content.decode("utf-8")

            import json
            cookies = json.loads(text_content)

            if not isinstance(cookies, list):
                await update.message.reply_text("❌ Invalid cookie format. Must be a JSON array.", parse_mode="HTML")
                return

            cookies_path = self.config.instagram_auth.cookies_path
            os.makedirs(os.path.dirname(cookies_path) if os.path.dirname(cookies_path) else ".", exist_ok=True)
            with open(cookies_path, "w") as f:
                json.dump(cookies, f, indent=2)

            self.config.instagram_auth.enabled = True
            self._admin_notified_cookies = False

            await update.message.reply_text(
                f"✅ <b>Cookies saved!</b>\n\n"
                f"📦 {len(cookies)} cookies loaded\n"
                f"📁 {cookies_path}\n"
                f"🔄 Instagram auth enabled",
                parse_mode="HTML",
            )
        except json.JSONDecodeError:
            await update.message.reply_text("❌ Invalid JSON in cookies file.", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Failed to save cookies:\n<code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_backup(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        msg = await update.message.reply_text("💾 Creating backup...")
        await self._do_backup(msg, update)

    async def _do_backup(self, msg, update):
        try:
            data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
            if not os.path.exists(data_dir):
                await msg.edit_text("❌ No data directory found.")
                return

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
            zip_path = os.path.join(data_dir, f"backup-{ts}.zip")

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, dirs, files in os.walk(data_dir):
                    for fname in files:
                        if fname.startswith("backup-"):
                            continue
                        fpath = os.path.join(root, fname)
                        arcname = os.path.relpath(fpath, data_dir)
                        zf.write(fpath, arcname)

            size_mb = os.path.getsize(zip_path) / 1048576

            await msg.edit_text(f"✅ Backup ready ({size_mb:.1f} MB). Sending...")

            chat_id = update.effective_chat.id
            with open(zip_path, "rb") as f:
                await self.app.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=f"instagram-monitor-backup-{ts}.zip",
                    caption=f"💾 <b>Backup</b>\n📦 Size: {size_mb:.1f} MB",
                    parse_mode="HTML",
                )

            os.remove(zip_path)
        except Exception as e:
            await msg.edit_text(f"❌ Backup failed:\n<code>{e}</code>", parse_mode="HTML")

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

    def notify_admin(self, message: str):
        if not self.app or not self.app.bot:
            return
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            async def _send():
                admins = self.db.get_all_admins()
                for admin in admins:
                    try:
                        chat = await self.app.bot.get_chat(f"@{admin}")
                        if chat:
                            await self.app.bot.send_message(
                                chat_id=chat.id,
                                text=message,
                                parse_mode="HTML",
                            )
                    except Exception:
                        pass

            if loop and loop.is_running():
                asyncio.ensure_future(_send())
            elif loop:
                loop.run_until_complete(_send())
            else:
                asyncio.run(_send())
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")
