# Bot Commands

## General

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + main menu with inline buttons |
| `/mainmenu` | Open main menu |
| `/help` | List all available commands |
| `/ping` | Bot latency + uptime + account count |
| `/health` | Bot status, uptime, recent checks |

## Monitoring

| Command | Description |
|---------|-------------|
| `/add <username>` | Add account to monitor. Captures initial screenshot if ACTIVE. |
| `/remove <username>` | Remove account from monitoring |
| `/status` | All monitored accounts with status, last check, and check count |
| `/accounts` | Quick list of all monitored accounts |
| `/check <username>` | Manual check — updates DB, may trigger transition notification |
| `/test <username>` | Test an account without adding to monitor. Shows screenshot + profile data. |

## Screenshot & Proxy

| Command | Description |
|---------|-------------|
| `/screenshot` | Screenshot service health (Camofox status, latency) |
| `/proxy` | Proxy traffic stats (used/remaining, cost) |

## Admin Only

| Command | Description |
|---------|-------------|
| `/adduser <username>` | Allow a user to use the bot |
| `/removeuser <username>` | Revoke a user's access |
| `/addadmin <username>` | Promote a user to admin |
| `/removeadmin <username>` | Demote an admin (can't remove last one) |
| `/listusers` | List all admins and allowed users |
| `/setcookie` | Upload `cookies.txt` file for Instagram auth |
| `/backup` | Zip data/ folder and send via Telegram |
| `/changelog` | View recent changelogs |
| `/changelog add <msg>` | Add a changelog and broadcast to all users |

## Access Control

- **Admins**: Full access. See all monitors, manage users, upload cookies, create backups, add changelogs.
- **Allowed Users**: Use monitoring commands. See only their own added accounts.
- **Everyone else**: Denied with a message to contact admin.

Access is checked on every command. Chat IDs are captured on first use for notifications.

## Per-User Monitoring

Each user can only see the accounts they added. Admins see everything.

- `/add` tracks `added_by` — the Telegram username who added the account.
- `/status`, `/accounts`, `/check`, `/remove` filter by the user's added accounts.
- Admins see all accounts regardless of who added them.
- If a user tries to add an account already monitored by someone else → told to ask an admin.
- If a user tries to remove an account they don't own → told to ask an admin.
- Duplicate adds trigger a recheck instead of an error.

## Status Types

| Status | Emoji | Meaning |
|--------|-------|---------|
| ACTIVE | 🟢 | Account is visible and accessible |
| MISSING | 🔴 | Account not found, banned, or deactivated |
| SUSPECT | 🟡 | curl_cffi says MISSING but Playwright says ACTIVE |
| UNKNOWN | ⚪ | Could not determine status |
| ERROR | ⚫ | Request failed |
| RATE_LIMITED | 🟠 | Too many requests, retrying with backoff |

## How Checks Work

1. **curl_cffi** hits Instagram's API (`/api/v1/users/web_profile_info/`)
2. If status is MISSING → **Playwright** opens the profile in headless Chrome for verification
3. If both agree → status confirmed
4. If they disagree → status set to SUSPECT
5. If status changed from last check → transition event recorded
6. If transition is ACTIVE→MISSING or MISSING→ACTIVE → notification sent

Check intervals are randomized (config ± 15s) to prevent pattern detection.

## Notifications

Transitions trigger notifications to **all admins + the user who added the account**.

- **ACTIVE → MISSING**: Alert with screenshot (if available), duration in previous state, profile link
- **MISSING → ACTIVE**: Restoration alert with profile data (followers, following, posts)

Notifications include a visual status badge and structured data for instant readability.

## Inline Menu

`/mainmenu` shows a keyboard with quick-access buttons:

```
➕ Add    ➖ Remove   🔍 Check
📡 Status  📊 Proxy   🏓 Ping
🧪 Test   🏥 Health   📸 SS Svc
📋 Changelog

[Admin only rows]
👥 Add User    👥⛔ Remove User
🔑 Set Cookie  💾 Backup
```

## Changelog

Admins can broadcast updates to all users:

- `/changelog` — view last 5 changelogs
- `/changelog add <message>` — save to DB + broadcast to all admins and users

## Data Storage

All data is in SQLite (`data/monitor.db`). Old data (checks, events, screenshots, raw responses) is cleaned up after 7 days.

### Tables

| Table | Purpose |
|-------|---------|
| `accounts` | Monitored usernames, status, `added_by`, check history |
| `checks` | Individual check results (status, latency, HTTP code) |
| `events` | Status transitions (old→new, notification sent) |
| `admins` | Admin usernames + chat IDs |
| `allowed_users` | Allowed user usernames + chat IDs |
| `changelogs` | Changelog entries (message, author, timestamp) |
| `settings` | Key-value settings |

## Files

| File | Purpose |
|------|---------|
| `data/monitor.db` | SQLite database |
| `data/cookies.json` | Instagram cookies for Playwright |
| `data/screenshots/` | Profile screenshots organized by date |
| `data/raw_responses/` | API response logs |
| `data/logs/bot.log` | Bot logs |
