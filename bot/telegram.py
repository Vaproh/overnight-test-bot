"""Telegram bot interface with per-user monitoring and scannable messages."""

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
        is_authorized = self.db.is_admin(username) or self.db.is_allowed_user(username)
        if is_authorized and update.effective_chat:
            if self.db.is_admin(username):
                self.db.update_admin_chat_id(username, update.effective_chat.id)
            else:
                self.db.update_allowed_user_chat_id(username, update.effective_chat.id)
        return is_authorized

    def _is_admin(self, update: Update) -> bool:
        return self.db.is_admin(self._get_username(update))

    def _deny(self, update):
        return update.message.reply_text(
            f"⛔ <b>Access Denied</b>\n\n"
            f"You don't have access to this bot. Contact admin: {ADMIN_NOTIFY}",
            parse_mode="HTML",
        )

    def _get_accounts_for_user(self, update: Update):
        username = self._get_username(update)
        if self.db.is_admin(username):
            return self.db.get_all_accounts()
        return self.db.get_accounts_for_non_admin(username)

    def _fmt_time_ago(self, ts: Optional[str]) -> str:
        if not ts:
            return "never"
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - dt
            secs = int(delta.total_seconds())
            if secs < 60:
                return f"{secs}s ago"
            if secs < 3600:
                return f"{secs // 60}m ago"
            if secs < 86400:
                return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
            return f"{secs // 86400}d ago"
        except Exception:
            return "unknown"

    def _fmt_duration(self, ts: Optional[str]) -> str:
        if not ts:
            return "unknown"
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - dt
            secs = int(delta.total_seconds())
            if secs < 60:
                return f"{secs}s"
            if secs < 3600:
                return f"{secs // 60}m"
            if secs < 86400:
                return f"{secs // 3600}h {(secs % 3600) // 60}m"
            return f"{secs // 86400}d {(secs % 86400) // 3600}h"
        except Exception:
            return "unknown"

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
        self.app.add_handler(CommandHandler("screenshot", self.cmd_screenshot))
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
        self.app.add_handler(CommandHandler("changelog", self.cmd_changelog))
        self.app.add_handler(CommandHandler("logs", self.cmd_logs))

        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        return self.app

    async def post_init(self, application: Application):
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("mainmenu", "Main menu"),
            BotCommand("help", "Show available commands"),
            BotCommand("status", "All monitored accounts"),
            BotCommand("add", "Add account to monitor"),
            BotCommand("remove", "Remove account"),
            BotCommand("check", "Manual check an account"),
            BotCommand("test", "Test account (no monitor)"),
            BotCommand("proxy", "Proxy traffic stats"),
            BotCommand("ping", "Check bot latency"),
            BotCommand("health", "Bot health & uptime"),
            BotCommand("screenshot", "Screenshot service status"),
            BotCommand("setcookie", "Upload cookies (admin)"),
            BotCommand("backup", "Backup data (admin)"),
            BotCommand("adduser", "Allow user (admin)"),
            BotCommand("removeuser", "Remove user (admin)"),
            BotCommand("addadmin", "Add admin (admin)"),
            BotCommand("removeadmin", "Remove admin (admin)"),
            BotCommand("listusers", "List users (admin)"),
            BotCommand("changelog", "View updates"),
            BotCommand("logs", "View error logs (admin)"),
        ]
        await application.bot.set_my_commands(commands)

    def _build_main_menu(self, is_admin: bool) -> InlineKeyboardMarkup:
        rows = [
            [
                InlineKeyboardButton("➕ Add", callback_data="menu:add"),
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
                InlineKeyboardButton("📸 SS Svc", callback_data="menu:screenshot"),
            ],
            [
                InlineKeyboardButton("📋 Changelog", callback_data="menu:changelog"),
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
            rows.append([
                InlineKeyboardButton("📋 Logs", callback_data="menu:logs"),
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
                "➕ <b>Add Account</b>\n\n"
                "Send: <code>/add username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:remove":
            accounts = self._get_accounts_for_user(update)
            if not accounts:
                await query.edit_message_text(
                    "📭 <b>Nothing to remove</b>",
                    parse_mode="HTML",
                )
                return
            lines = ["➖ <b>Send /remove username</b>\n"]
            for a in accounts:
                emoji = STATUS_EMOJI.get(a["status"], "⚪")
                lines.append(f"{emoji} @{a['username']}")
            await query.edit_message_text("\n".join(lines), parse_mode="HTML")
        elif data == "menu:check":
            accounts = self._get_accounts_for_user(update)
            if not accounts:
                await query.edit_message_text(
                    "📭 <b>No accounts to check</b>",
                    parse_mode="HTML",
                )
                return
            lines = ["🔍 <b>Send /check username</b>\n"]
            for a in accounts:
                emoji = STATUS_EMOJI.get(a["status"], "⚪")
                lines.append(f"{emoji} @{a['username']}")
            await query.edit_message_text("\n".join(lines), parse_mode="HTML")
        elif data == "menu:test":
            await query.edit_message_text(
                "🧪 <b>Test Account</b>\n\n"
                "Send: <code>/test username</code>\n\n"
                "Checks status without adding to monitor.",
                parse_mode="HTML",
            )
        elif data == "menu:status":
            await self._handle_status_callback(query, update)
        elif data == "menu:proxy":
            await self._handle_proxy_callback(query)
        elif data == "menu:ping":
            accounts = self._get_accounts_for_user(update)
            msg_text = (
                f"🏓 <b>Pong!</b>\n\n"
                f"📡 {len(accounts)} accounts monitored\n"
                f"⏱ Uptime: {self.monitor.get_uptime()}"
            )
            await query.edit_message_text(msg_text, parse_mode="HTML")
        elif data == "menu:health":
            accounts = self._get_accounts_for_user(update)
            active = sum(1 for a in accounts if a["status"] == "ACTIVE")
            missing = sum(1 for a in accounts if a["status"] == "MISSING")
            other = len(accounts) - active - missing
            await query.edit_message_text(
                f"🏥 <b>Health</b>\n\n"
                f"⏱ Uptime: {self.monitor.get_uptime()}\n"
                f"🟢 {active} active · 🔴 {missing} missing · ⚪ {other} other",
                parse_mode="HTML",
            )
        elif data == "menu:screenshot":
            await self._handle_screenshot_callback(query)
        elif data == "menu:adduser":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text(
                "👥 <b>Add User</b>\n\nSend: <code>/adduser username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:removeuser":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text(
                "👥 <b>Remove User</b>\n\nSend: <code>/removeuser username</code>",
                parse_mode="HTML",
            )
        elif data == "menu:setcookie":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text(
                "🔑 <b>Upload Cookies</b>\n\n"
                "Send a file named <code>cookies.txt</code> with /setcookie",
                parse_mode="HTML",
            )
        elif data == "menu:changelog":
            changelogs = self.db.get_changelogs(limit=5)
            if not changelogs:
                await query.edit_message_text(
                    "📋 <b>Changelog</b>\n\nNo changelogs yet.",
                    parse_mode="HTML",
                )
                return
            lines = ["📋 <b>Recent Updates</b>\n━━━━━━━━━━━━━━━━━━━"]
            for cl in changelogs:
                try:
                    dt = datetime.fromisoformat(cl["created_at"].replace("Z", "+00:00"))
                    time_str = dt.strftime("%b %d %H:%M")
                except Exception:
                    time_str = cl["created_at"][:16]
                lines.append(f"\n<b>{time_str}</b> — @{cl['author']}\n{cl['message']}")
            lines.append("\n💡 <code>/changelog add &lt;msg&gt;</code> — Admin only")
            await query.edit_message_text("\n".join(lines), parse_mode="HTML")
        elif data == "menu:backup":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await query.edit_message_text("💾 Creating backup...", parse_mode="HTML")
            await self._do_backup(query.message, update)
        elif data == "menu:logs":
            if not is_admin:
                await query.edit_message_text("⛔ Admin only.", parse_mode="HTML")
                return
            await self._handle_logs_callback(query)

    async def _handle_status_callback(self, query, update):
        accounts = self._get_accounts_for_user(update)
        if not accounts:
            await query.edit_message_text(
                "📭 <b>No accounts monitored</b>\n\nUse /add to add one.",
                parse_mode="HTML",
            )
            return

        active = sum(1 for a in accounts if a["status"] == "ACTIVE")
        missing = sum(1 for a in accounts if a["status"] == "MISSING")
        suspect = sum(1 for a in accounts if a["status"] == "SUSPECT")
        other = len(accounts) - active - missing - suspect

        lines = [
            f"📡 <b>{len(accounts)} Accounts Monitored</b>",
            f"🟢 {active} active · 🔴 {missing} missing · 🟡 {suspect} suspect · ⚪ {other} other",
            "━━━━━━━━━━━━━━━━━━━",
            "",
        ]
        for a in accounts:
            emoji = STATUS_EMOJI.get(a["status"], "⚪")
            last = self._fmt_time_ago(a.get("last_check"))
            count = a.get("check_count", 0)
            lines.append(f"{emoji} <b>@{a['username']}</b>")
            lines.append(f"    {a['status']} · checked {last} · {count}x")
            lines.append("")

        await query.edit_message_text("\n".join(lines).rstrip(), parse_mode="HTML")

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
                "📊 <b>Proxy Stats</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 Total: {fmt(total)}\n"
                f"📤 Used: {fmt(used)} ({pct:.1f}%)\n"
                f"📥 Left: {fmt(left)}\n\n"
                f"<code>[{bar}]</code> {pct:.1f}%\n\n"
                f"💰 ${cost:.2f} used @ $1.2/GB"
            )
            await query.edit_message_text(text, parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"❌ Error: <code>{e}</code>", parse_mode="HTML")

    async def _handle_screenshot_callback(self, query):
        service_url = self.config.screenshot_service_url
        if not service_url:
            await query.edit_message_text(
                "⚠️ Screenshot service not configured.",
                parse_mode="HTML",
            )
            return
        try:
            import requests as _requests
            start = _time.time()
            resp = _requests.get(f"{service_url.rstrip('/')}/health", timeout=5)
            latency = (_time.time() - start) * 1000

            if resp.status_code == 200:
                data = resp.json()
                camofox = data.get("camofox", False)
                status = data.get("status", "unknown")
                emoji = "🟢" if camofox else "🔴"
                camofox_text = "Online" if camofox else "Offline"
                await query.edit_message_text(
                    f"📸 <b>Screenshot Service</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{emoji} {status} · Camofox: {camofox_text}\n"
                    f"⚡ {latency:.0f}ms\n"
                    f"🔗 <code>{service_url}</code>",
                    parse_mode="HTML",
                )
            else:
                await query.edit_message_text(
                    f"🔴 <b>SS Service Unhealthy</b>\n\nHTTP {resp.status_code}",
                    parse_mode="HTML",
                )
        except _requests.exceptions.ConnectionError:
            await query.edit_message_text(
                f"🔴 <b>SS Service Offline</b>\n\n<code>{service_url}</code>",
                parse_mode="HTML",
            )
        except _requests.exceptions.Timeout:
            await query.edit_message_text(
                f"🔴 <b>SS Service Timeout</b>\n\n<code>{service_url}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            await query.edit_message_text(
                f"🔴 <b>Error:</b> <code>{e}</code>",
                parse_mode="HTML",
            )

    async def _handle_logs_callback(self, query):
        lines = ["📋 <b>Recent Errors</b>\n━━━━━━━━━━━━━━━━━━━\n"]

        db_errors = self.db.get_recent_errors(limit=8)
        if db_errors:
            for err in db_errors:
                try:
                    dt = datetime.fromisoformat(err["timestamp"].replace("Z", "+00:00"))
                    time_str = dt.strftime("%b %d %H:%M")
                except Exception:
                    time_str = err["timestamp"][:16]
                msg = (err.get("error_message") or "unknown")[:80]
                lines.append(f"⚫ <b>@{err['username']}</b> — {time_str}")
                lines.append(f"    <code>{msg}</code>")
                lines.append("")
        else:
            lines.append("✅ No check errors recorded.\n")

        log_path = os.path.join(self.config.logs_dir, "monitor.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                error_lines = [l.rstrip() for l in all_lines if "ERROR" in l][-6:]
                if error_lines:
                    lines.append("━━━━━━━━━━━━━━━━━━━")
                    lines.append("<b>Log tail (ERROR):</b>")
                    lines.append("")
                    for l in error_lines:
                        lines.append(f"<code>{l[:120]}</code>")
            except Exception:
                lines.append("⚠️ Could not read log file.")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n\n<i>...truncated</i>"
        await query.edit_message_text(text, parse_mode="HTML")

    # ── Commands ───────────────────────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        accounts = self._get_accounts_for_user(update)
        active = sum(1 for a in accounts if a["status"] == "ACTIVE")
        missing = sum(1 for a in accounts if a["status"] == "MISSING")

        text = (
            "📸 <b>Instagram Monitor</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"📡 {len(accounts)} accounts · "
            f"🟢 {active} active · 🔴 {missing} missing\n\n"
            "• /add <code>username</code> — start monitoring\n"
            "• /remove <code>username</code> — stop monitoring\n"
            "• /status — view all accounts\n"
            "• /test <code>username</code> — test without monitoring\n"
            "• /mainmenu — menu with buttons\n"
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
            "➕ /add <code>username</code> — monitor account\n"
            "➖ /remove <code>username</code> — stop monitoring\n"
            "📡 /status — all monitored accounts\n"
            "🔍 /check <code>username</code> — manual check\n"
            "🧪 /test <code>username</code> — test (no monitor)\n"
            "📸 /screenshot — SS service status\n"
            "📊 /proxy — proxy traffic\n"
            "🏓 /ping — bot latency\n"
            "🏥 /health — bot status\n"
            "📋 /changelog — view updates\n"
            "📋 /logs — error logs (admin)\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🔑 Admins can manage users & cookies"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        accounts = self._get_accounts_for_user(update)
        active = sum(1 for a in accounts if a["status"] == "ACTIVE")
        missing = sum(1 for a in accounts if a["status"] == "MISSING")
        suspect = sum(1 for a in accounts if a["status"] == "SUSPECT")
        other = len(accounts) - active - missing - suspect

        recent_checks = self.db.get_recent_checks(limit=3)

        lines = [
            "🏥 <b>Health</b>\n━━━━━━━━━━━━━━━━━━━\n",
            f"⏱ Uptime: {self.monitor.get_uptime()}",
            f"📡 {len(accounts)} accounts · 🟢 {active} · 🔴 {missing} · 🟡 {suspect} · ⚪ {other}",
        ]

        if recent_checks:
            lines.append("\n<b>Last checks:</b>")
            for c in recent_checks:
                emoji = STATUS_EMOJI.get(c["status"], "⚪")
                lines.append(f"  {emoji} @{c['username']} {c['status']} ({c['latency_ms']:.0f}ms)")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_accounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        accounts = self._get_accounts_for_user(update)
        if not accounts:
            await update.message.reply_text(
                "📭 <b>No accounts monitored</b>\n\nUse /add <code>username</code> to add one.",
                parse_mode="HTML",
            )
            return

        active = sum(1 for a in accounts if a["status"] == "ACTIVE")
        missing = sum(1 for a in accounts if a["status"] == "MISSING")

        lines = [
            f"📡 <b>{len(accounts)} Accounts</b>",
            f"🟢 {active} active · 🔴 {missing} missing",
            "━━━━━━━━━━━━━━━━━━━\n",
        ]
        for a in accounts:
            emoji = STATUS_EMOJI.get(a["status"], "⚪")
            last = self._fmt_time_ago(a.get("last_check"))
            lines.append(f"{emoji} @{a['username']}  ·  {last}")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        accounts = self._get_accounts_for_user(update)
        if not accounts:
            await update.message.reply_text(
                "📭 <b>No accounts monitored</b>\n\nUse /add <code>username</code> to add one.",
                parse_mode="HTML",
            )
            return

        active = sum(1 for a in accounts if a["status"] == "ACTIVE")
        missing = sum(1 for a in accounts if a["status"] == "MISSING")
        suspect = sum(1 for a in accounts if a["status"] == "SUSPECT")
        other = len(accounts) - active - missing - suspect

        lines = [
            f"📡 <b>{len(accounts)} Accounts Monitored</b>",
            f"🟢 {active} active · 🔴 {missing} missing · 🟡 {suspect} suspect · ⚪ {other} other",
            "━━━━━━━━━━━━━━━━━━━\n",
        ]
        for a in accounts:
            emoji = STATUS_EMOJI.get(a["status"], "⚪")
            last = self._fmt_time_ago(a.get("last_check"))
            count = a.get("check_count", 0)
            lines.append(f"{emoji} <b>@{a['username']}</b>")
            lines.append(f"    {a['status']} · checked {last} · {count}x")
            lines.append("")

        await update.message.reply_text("\n".join(lines).rstrip(), parse_mode="HTML")

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "➕ <b>Usage:</b> /add <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        added_by = self._get_username(update)
        is_admin = self.db.is_admin(added_by)

        existing = self.db.get_account_status(username)
        if existing is not None:
            existing_owner = self.db.get_account_added_by(username)
            if existing_owner and existing_owner != added_by and not is_admin:
                await update.message.reply_text(
                    f"🔒 <b>@{username}</b> is already monitored by @{existing_owner}.\n\n"
                    f"Ask an admin to reassign it.",
                    parse_mode="HTML",
                )
                return
            else:
                await update.message.reply_text(
                    f"🔄 <b>@{username}</b> already monitored — rechecking...",
                    parse_mode="HTML",
                )
                await self._do_check_and_reply(update, username)
                return

        self.db.get_or_create_account(username, added_by)
        status_msg = await update.message.reply_text(
            f"✅ <b>@{username}</b> added to your monitor\n🔍 Checking status...",
            parse_mode="HTML",
        )

        await self._do_check_and_reply(update, username, status_msg)

    async def _do_check_and_reply(self, update: Update, username: str, status_msg=None):
        try:
            from .checker import check_account, capture_profile_screenshot

            import asyncio as _asyncio
            loop = _asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, check_account, username, self.config
            )
            status = result["classification"]
            emoji = STATUS_EMOJI.get(status, "⚪")

            profile_data = {}
            screenshot_path = None
            screenshot_error = None
            if status == "ACTIVE":
                if status_msg:
                    await status_msg.edit_text(
                        f"{emoji} <b>@{username}</b> — {status}\n📸 Capturing screenshot...",
                        parse_mode="HTML",
                    )
                screenshot_data = await loop.run_in_executor(
                    None, capture_profile_screenshot, username, self.config, "add"
                )
                screenshot_path = screenshot_data.get("screenshot_path")
                profile_data = screenshot_data.get("profile_data", {})
                screenshot_error = screenshot_data.get("error")

            caption = self._format_profile_card(username, status, profile_data)

            if screenshot_path and os.path.exists(screenshot_path):
                with open(screenshot_path, "rb") as f:
                    await update.message.reply_photo(
                        photo=f,
                        caption=caption,
                        parse_mode="HTML",
                    )
                if status_msg:
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
            elif status == "ACTIVE" and screenshot_error:
                error_reasons = {
                    "profile_unavailable": "Profile deactivated or doesn't exist",
                    "service_down": "SS service down (Camofox offline)",
                    "timeout": "Page load timed out",
                    "rate_limited": "SS service rate limited",
                    "connection_refused": "SS service unreachable",
                    "invalid_username": "Invalid username",
                }
                reason = error_reasons.get(screenshot_error, "Screenshot unavailable")
                fallback = (
                    f"{caption}\n\n"
                    f"⚠️ {reason}\n"
                    f"🔗 <a href=\"https://www.instagram.com/{username}/\">Open profile</a>"
                )
                if status_msg:
                    await status_msg.edit_text(fallback, parse_mode="HTML")
                else:
                    await update.message.reply_text(fallback, parse_mode="HTML")
            else:
                if status_msg:
                    await status_msg.edit_text(caption, parse_mode="HTML")
                else:
                    await update.message.reply_text(caption, parse_mode="HTML")
        except Exception as e:
            text = f"⚠️ Check failed: <code>{e}</code>"
            if status_msg:
                await status_msg.edit_text(text, parse_mode="HTML")
            else:
                await update.message.reply_text(text, parse_mode="HTML")

    def _format_profile_card(self, username: str, status: str, profile_data: dict = None) -> str:
        emoji = STATUS_EMOJI.get(status, "⚪")
        divider = f"{emoji}━━━━━━━━━━━━━━━━━━━━{emoji}"

        lines = [
            divider,
            "",
            f"{emoji} <b>@{username}</b>",
            f"    {status}",
        ]

        if profile_data:
            stats = []
            if profile_data.get("followers"):
                stats.append(f"👥 {profile_data['followers']}")
            if profile_data.get("following"):
                stats.append(f"➡️ {profile_data['following']}")
            if profile_data.get("posts"):
                stats.append(f"📝 {profile_data['posts']}")
            if stats:
                lines.append(f"    {' · '.join(stats)}")

        lines.extend(["", divider])
        return "\n".join(lines)

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "➖ <b>Usage:</b> /remove <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        added_by = self._get_username(update)
        is_admin = self.db.is_admin(added_by)

        existing_owner = self.db.get_account_added_by(username)
        if existing_owner and existing_owner != added_by and not is_admin:
            await update.message.reply_text(
                f"🔒 <b>@{username}</b> is monitored by @{existing_owner}.\n"
                f"Ask an admin to remove it.",
                parse_mode="HTML",
            )
            return

        existing = self.db.get_account_status(username)
        if existing is None:
            await update.message.reply_text(
                f"❌ <b>@{username}</b> is not being monitored.",
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
                "🔍 <b>Usage:</b> /check <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        status_msg = await update.message.reply_text(
            f"🔍 <b>Checking @{username}...</b>",
            parse_mode="HTML",
        )

        try:
            import asyncio as _asyncio
            loop = _asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, self.monitor.check_single, username
            )
            emoji = STATUS_EMOJI.get(result["status"], "⚪")
            transition = "⚡ Yes" if result["transition"] else "— No"

            text = (
                f"🔍 <b>@{username}</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"{emoji} <b>{result['status']}</b>\n"
                f"Previous: {result.get('old_status', 'N/A')}\n"
                f"Transition: {transition}\n"
                f"Latency: {result.get('latency_ms', 0):.0f}ms"
            )
            await status_msg.edit_text(text, parse_mode="HTML")
        except Exception as e:
            await status_msg.edit_text(
                f"❌ Check failed: <code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_test(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "🧪 <b>Usage:</b> /test <code>username</code>\n\n"
                "Checks status without adding to monitor.",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        status_msg = await update.message.reply_text(
            f"🧪 <b>Testing @{username}...</b>",
            parse_mode="HTML",
        )

        try:
            from .checker import check_account, capture_profile_screenshot

            import asyncio as _asyncio
            loop = _asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, check_account, username, self.config
            )
            status = result["classification"]
            emoji = STATUS_EMOJI.get(status, "⚪")

            profile_data = {}
            screenshot_path = None
            screenshot_error = None
            if status == "ACTIVE":
                await status_msg.edit_text(
                    f"🧪 <b>@{username}</b> — {status}\n📸 Capturing screenshot...",
                    parse_mode="HTML",
                )
                screenshot_data = await loop.run_in_executor(
                    None, capture_profile_screenshot, username, self.config, "test"
                )
                screenshot_path = screenshot_data.get("screenshot_path")
                profile_data = screenshot_data.get("profile_data", {})
                screenshot_error = screenshot_data.get("error")

            divider = f"{emoji}━━━━━━━━━━━━━━━━━━━━{emoji}"
            lines = [
                divider,
                "",
                f"🧪 <b>Test Result</b>",
                "",
                f"{emoji} <b>@{username}</b> — {status}",
                f"🏎 {result.get('latency_ms', 0):.0f}ms · HTTP {result.get('status_code') or 'N/A'}",
            ]

            if profile_data:
                stats = []
                if profile_data.get("followers"):
                    stats.append(f"👥 {profile_data['followers']}")
                if profile_data.get("following"):
                    stats.append(f"➡️ {profile_data['following']}")
                if profile_data.get("posts"):
                    stats.append(f"📝 {profile_data['posts']}")
                if stats:
                    lines.append(f"📊 {' · '.join(stats)}")

            lines.extend(["", divider])
            caption = "\n".join(lines)

            if screenshot_path and os.path.exists(screenshot_path):
                with open(screenshot_path, "rb") as f:
                    await update.message.reply_photo(
                        photo=f,
                        caption=caption,
                        parse_mode="HTML",
                    )
                await status_msg.delete()
            elif status == "ACTIVE" and screenshot_error:
                error_reasons = {
                    "profile_unavailable": "Profile deactivated or doesn't exist",
                    "service_down": "SS service down (Camofox offline)",
                    "timeout": "Page load timed out",
                    "rate_limited": "SS service rate limited",
                    "connection_refused": "SS service unreachable",
                    "invalid_username": "Invalid username",
                }
                reason = error_reasons.get(screenshot_error, "Screenshot unavailable")
                fallback = (
                    f"{caption}\n\n"
                    f"⚠️ {reason}\n"
                    f"🔗 <a href=\"https://www.instagram.com/{username}/\">Open profile</a>"
                )
                await status_msg.edit_text(fallback, parse_mode="HTML")
            else:
                await status_msg.edit_text(caption, parse_mode="HTML")
        except Exception as e:
            await status_msg.edit_text(
                f"❌ Test failed: <code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_ping(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        start = _time.time()
        msg = await update.message.reply_text("🏓 ...")
        latency = (_time.time() - start) * 1000

        accounts = self._get_accounts_for_user(update)
        await msg.edit_text(
            f"🏓 <b>Pong!</b>\n\n"
            f"⚡ Latency: {latency:.0f}ms\n"
            f"📡 {len(accounts)} accounts · ⏱ {self.monitor.get_uptime()}",
            parse_mode="HTML",
        )

    async def cmd_screenshot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        service_url = self.config.screenshot_service_url
        if not service_url:
            await update.message.reply_text(
                "⚠️ <b>SS Service not configured</b>\n\n"
                "Set <code>screenshot_service_url</code> in config.yaml",
                parse_mode="HTML",
            )
            return

        msg = await update.message.reply_text("📸 Checking SS service...")

        try:
            import requests as _requests
            start = _time.time()
            resp = _requests.get(f"{service_url.rstrip('/')}/health", timeout=5)
            latency = (_time.time() - start) * 1000

            if resp.status_code == 200:
                data = resp.json()
                camofox = data.get("camofox", False)
                status = data.get("status", "unknown")
                emoji = "🟢" if camofox else "🔴"
                camofox_text = "Online" if camofox else "Offline"
                await msg.edit_text(
                    f"📸 <b>SS Service</b>\n"
                    "━━━━━━━━━━━━━━━━━━━\n\n"
                    f"{emoji} {status} · Camofox: {camofox_text}\n"
                    f"⚡ {latency:.0f}ms\n"
                    f"🔗 <code>{service_url}</code>",
                    parse_mode="HTML",
                )
            else:
                await msg.edit_text(
                    f"🔴 <b>SS Unhealthy</b>\n\nHTTP {resp.status_code}",
                    parse_mode="HTML",
                )
        except _requests.exceptions.ConnectionError:
            await msg.edit_text(
                f"🔴 <b>SS Offline</b>\n\n<code>{service_url}</code>",
                parse_mode="HTML",
            )
        except _requests.exceptions.Timeout:
            await msg.edit_text(
                f"🔴 <b>SS Timeout</b>\n\n<code>{service_url}</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            await msg.edit_text(
                f"🔴 <b>Error:</b> <code>{e}</code>",
                parse_mode="HTML",
            )

    async def cmd_proxy(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not self.config.proxy.enabled:
            await update.message.reply_text(
                "⚠️ Proxy is not enabled.",
                parse_mode="HTML",
            )
            return

        msg = await update.message.reply_text("📊 Fetching proxy stats...")

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
                await msg.edit_text(
                    f"❌ API Error: {data.get('message', 'Unknown')}",
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

            cost = (used_bytes / 1073741824) * 1.2

            text = (
                "📊 <b>Proxy Stats</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 Total: {fmt_bytes(total_bytes)}\n"
                f"📤 Used: {fmt_bytes(used_bytes)} ({pct_used:.1f}%)\n"
                f"📥 Left: {fmt_bytes(left_bytes)}\n\n"
                f"<code>[{bar}]</code> {pct_used:.1f}%\n\n"
                f"💰 ${cost:.2f} @ $1.2/GB"
            )

            await msg.edit_text(text, parse_mode="HTML")
        except Exception as e:
            await msg.edit_text(
                f"❌ Failed: <code>{e}</code>",
                parse_mode="HTML",
            )

    # ── Admin Commands ─────────────────────────────────────────

    async def cmd_addadmin(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "🔑 <b>Usage:</b> /addadmin <code>username</code>",
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
                "🔑 <b>Usage:</b> /removeadmin <code>username</code>",
                parse_mode="HTML",
            )
            return

        username = context.args[0].lstrip("@")
        current_user = self._get_username(update)
        if username == current_user:
            await update.message.reply_text("❌ Can't remove yourself.", parse_mode="HTML")
            return

        if self.db.remove_admin(username):
            await update.message.reply_text(f"🗑 <b>@{username}</b> removed from admins.", parse_mode="HTML")
        else:
            await update.message.reply_text("❌ Must have at least one admin.", parse_mode="HTML")

    async def cmd_adduser(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update):
            await self._deny(update)
            return

        if not context.args:
            await update.message.reply_text(
                "👥 <b>Usage:</b> /adduser <code>username</code>",
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
                "👥 <b>Usage:</b> /removeuser <code>username</code>",
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
            "━━━━━━━━━━━━━━━━━━━\n",
            "🔑 <b>Admins:</b>",
        ]
        for a in admins:
            lines.append(f"  • @{a}")

        lines.append("\n👤 <b>Users:</b>")
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
                "🔑 <b>Upload Cookies</b>\n\n"
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
                await update.message.reply_text("❌ Invalid cookie format. Must be JSON array.", parse_mode="HTML")
                return

            cookies_path = self.config.instagram_auth.cookies_path
            os.makedirs(os.path.dirname(cookies_path) if os.path.dirname(cookies_path) else ".", exist_ok=True)
            with open(cookies_path, "w") as f:
                json.dump(cookies, f, indent=2)

            self.config.instagram_auth.enabled = True
            self._admin_notified_cookies = False

            await update.message.reply_text(
                f"✅ <b>Cookies saved</b>\n\n"
                f"📦 {len(cookies)} cookies loaded\n"
                f"📁 {cookies_path}\n"
                f"🔄 Instagram auth enabled",
                parse_mode="HTML",
            )
        except json.JSONDecodeError:
            await update.message.reply_text("❌ Invalid JSON in cookies file.", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text(
                f"❌ Failed: <code>{e}</code>",
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
                    caption=f"💾 <b>Backup</b> — {size_mb:.1f} MB",
                    parse_mode="HTML",
                )

            os.remove(zip_path)
        except Exception as e:
            await msg.edit_text(f"❌ Backup failed: <code>{e}</code>", parse_mode="HTML")

    async def cmd_changelog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not context.args:
            changelogs = self.db.get_changelogs(limit=5)
            if not changelogs:
                await update.message.reply_text(
                    "📋 <b>Changelog</b>\n\nNo changelogs yet.",
                    parse_mode="HTML",
                )
                return

            lines = ["📋 <b>Recent Updates</b>\n━━━━━━━━━━━━━━━━━━━"]
            for cl in changelogs:
                try:
                    dt = datetime.fromisoformat(cl["created_at"].replace("Z", "+00:00"))
                    time_str = dt.strftime("%b %d %H:%M")
                except Exception:
                    time_str = cl["created_at"][:16]
                lines.append(f"\n<b>{time_str}</b> — @{cl['author']}\n{cl['message']}")
            lines.append("\n💡 <code>/changelog add &lt;msg&gt;</code> — Admin only")
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            return

        if not self._is_admin(update):
            await update.message.reply_text(
                "⛔ Only admins can add changelogs.",
                parse_mode="HTML",
            )
            return

        if context.args[0].lower() == "add":
            if len(context.args) < 2:
                await update.message.reply_text(
                    "📝 <b>Usage:</b> /changelog add <code>message</code>",
                    parse_mode="HTML",
                )
                return

            message = " ".join(context.args[1:])
            author = self._get_username(update)
            self.db.add_changelog(message, author)

            await update.message.reply_text(
                f"✅ <b>Changelog added</b>\n\n{message}",
                parse_mode="HTML",
            )

            chat_ids = self.db.get_all_recipient_chat_ids()
            sender_chat_id = update.effective_chat.id
            broadcast = (
                f"📢 <b>New Update</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n\n"
                f"{message}\n\n"
                f"— @{author}"
            )
            for cid in chat_ids:
                if cid != sender_chat_id:
                    try:
                        await self.app.bot.send_message(
                            chat_id=cid,
                            text=broadcast,
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
        else:
            await update.message.reply_text(
                "📝 <b>Usage:</b>\n"
                "/changelog — View updates\n"
                "/changelog add <code>message</code> — Add update (admin)",
                parse_mode="HTML",
            )

    async def cmd_logs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._check_access(update):
            await self._deny(update)
            return

        if not self._is_admin(update):
            await update.message.reply_text(
                "⛔ Only admins can view logs.",
                parse_mode="HTML",
            )
            return

        lines = ["📋 <b>Recent Errors</b>\n━━━━━━━━━━━━━━━━━━━\n"]

        db_errors = self.db.get_recent_errors(limit=8)
        if db_errors:
            for err in db_errors:
                try:
                    dt = datetime.fromisoformat(err["timestamp"].replace("Z", "+00:00"))
                    time_str = dt.strftime("%b %d %H:%M")
                except Exception:
                    time_str = err["timestamp"][:16]
                msg = (err.get("error_message") or "unknown")[:80]
                lines.append(f"⚫ <b>@{err['username']}</b> — {time_str}")
                lines.append(f"    <code>{msg}</code>")
                lines.append("")
        else:
            lines.append("✅ No check errors recorded.\n")

        log_path = os.path.join(self.config.logs_dir, "monitor.log")
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    all_lines = f.readlines()
                error_lines = [l.rstrip() for l in all_lines if "ERROR" in l][-6:]
                if error_lines:
                    lines.append("━━━━━━━━━━━━━━━━━━━")
                    lines.append("<b>Log tail (ERROR):</b>")
                    lines.append("")
                    for l in error_lines:
                        lines.append(f"<code>{l[:120]}</code>")
            except Exception:
                lines.append("⚠️ Could not read log file.")

        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[:3900] + "\n\n<i>...truncated</i>"
        await update.message.reply_text(text, parse_mode="HTML")

    # ── Notifications ──────────────────────────────────────────

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

            coro = self._send_notification(message)
            if loop and loop.is_running():
                asyncio.ensure_future(coro)
            elif loop:
                loop.run_until_complete(coro)
            else:
                asyncio.run(coro)
        except Exception as e:
            logger.error(f"Failed to queue notification: {e}")

    def notify_to_chat_ids(self, chat_ids: list, message: str):
        if not self.app or not self.app.bot:
            return

        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            coro = self._send_to_chat_ids(chat_ids, message)
            if loop and loop.is_running():
                asyncio.ensure_future(coro)
            elif loop:
                loop.run_until_complete(coro)
            else:
                asyncio.run(coro)
        except Exception as e:
            logger.error(f"Failed to queue notification: {e}")

    async def _send_to_chat_ids(self, chat_ids: list, message: str):
        for chat_id in chat_ids:
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Failed to send to {chat_id}: {e}")

    async def _send_notification(self, message: str):
        chat_ids = self.db.get_admin_chat_ids()
        if not chat_ids:
            logger.warning("No admin chat_ids known, skipping notification")
            return
        for chat_id in chat_ids:
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode="HTML",
                )
            except Exception as e:
                logger.error(f"Failed to send notification to {chat_id}: {e}")

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

            coro = self._send_photo(photo_path, caption)
            if loop and loop.is_running():
                asyncio.ensure_future(coro)
            elif loop:
                loop.run_until_complete(coro)
            else:
                asyncio.run(coro)
        except Exception as e:
            logger.error(f"Failed to queue photo: {e}")

    def notify_photo_to_chat_ids(self, chat_ids: list, photo_path: str, caption: str):
        if not self.app or not self.app.bot:
            return

        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            coro = self._send_photo_to_chat_ids(chat_ids, photo_path, caption)
            if loop and loop.is_running():
                asyncio.ensure_future(coro)
            elif loop:
                loop.run_until_complete(coro)
            else:
                asyncio.run(coro)
        except Exception as e:
            logger.error(f"Failed to queue photo: {e}")

    async def _send_photo_to_chat_ids(self, chat_ids: list, photo_path: str, caption: str):
        for chat_id in chat_ids:
            try:
                if not os.path.exists(photo_path):
                    await self.app.bot.send_message(
                        chat_id=chat_id,
                        text=caption,
                        parse_mode="HTML",
                    )
                    continue
                with open(photo_path, "rb") as photo:
                    await self.app.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=caption,
                        parse_mode="HTML",
                    )
            except Exception as e:
                logger.error(f"Failed to send photo to {chat_id}: {e}")

    async def _send_photo(self, photo_path: str, caption: str):
        chat_ids = self.db.get_admin_chat_ids()
        if not chat_ids:
            logger.warning("No admin chat_ids known, skipping photo")
            return

        for chat_id in chat_ids:
            try:
                if not os.path.exists(photo_path):
                    await self.app.bot.send_message(
                        chat_id=chat_id,
                        text=caption,
                        parse_mode="HTML",
                    )
                    continue

                with open(photo_path, "rb") as photo:
                    await self.app.bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=caption,
                        parse_mode="HTML",
                    )
            except Exception as e:
                logger.error(f"Failed to send photo to {chat_id}: {e}")

    def notify_admin(self, message: str):
        if not self.app or not self.app.bot:
            return
        self.notify(message)

    def shutdown_notify(self, message: str):
        if not self.app or not self.app.bot:
            return
        chat_ids = self.db.get_admin_chat_ids()
        if not chat_ids:
            return
        import requests as _requests
        token = self.config.telegram_token
        for chat_id in chat_ids:
            try:
                _requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                    timeout=5,
                )
            except Exception:
                pass
