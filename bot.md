# Bot Commands

## General

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + main menu |
| `/mainmenu` | Open main menu with inline buttons |
| `/help` | List all available commands |
| `/ping` | Check bot latency |
| `/health` | Bot status, uptime, recent checks/events |

## Monitoring

| Command | Description |
|---------|-------------|
| `/add <username>` | Start monitoring an account |
| `/remove <username>` | Stop monitoring an account |
| `/status` | All monitored accounts with last check time |
| `/accounts` | List all monitored accounts |
| `/check <username>` | Manual check (updates DB, may trigger notification) |
| `/test <username>` | Test an account without adding to monitoring |

## Admin Only

| Command | Description |
|---------|-------------|
| `/adduser <username>` | Allow a user to use the bot |
| `/removeuser <username>` | Revoke a user's access |
| `/addadmin <username>` | Promote a user to admin |
| `/removeadmin <username>` | Demote an admin (can't remove last one) |
| `/listusers` | List all admins and allowed users |
| `/setcookie` | Upload `cookies.txt` file for Instagram auth |
| `/backup` | Zip data folder and send via Telegram |

## Access Control

- **Admins**: Full access. Can manage users, upload cookies, create backups.
- **Allowed Users**: Can use all monitoring commands. Cannot manage users or access admin features.
- **Everyone else**: Denied with a message to contact admin.

## Notifications

The bot sends notifications on status transitions:

- **ACTIVE → MISSING**: Account may be banned/deleted. Includes screenshot, status, and time in previous state.
- **MISSING → ACTIVE**: Account restored. Includes profile data (followers, following, posts).

Screenshots are cropped to the profile header area (dark mode, Pixel 7 viewport).

## Status Types

| Status | Meaning |
|--------|---------|
| 🟢 ACTIVE | Account is visible and accessible |
| 🔴 MISSING | Account not found or banned |
| 🟡 SUSPECT | curl_cffi says MISSING but Playwright says ACTIVE (disagreement) |
| ⚪ UNKNOWN | Could not determine status |
| ⚫ ERROR | Request failed |
| 🟠 RATE_LIMITED | Too many requests, retrying with backoff |

## How Checks Work

1. **Primary check**: curl_cffi hits Instagram's API (`/api/v1/users/web_profile_info/`)
2. If status is MISSING → **Playwright verification**: opens account in headless Chrome
3. If both agree → status confirmed
4. If they disagree → status set to SUSPECT
5. If status changed from last check → transition notification sent

## Data Storage

All monitored accounts, check history, and events are stored in SQLite (`data/monitor.db`). Old data (checks, events, screenshots, raw responses) is cleaned up after 7 days.

## Files

| File | Purpose |
|------|---------|
| `data/monitor.db` | SQLite database |
| `data/cookies.json` | Instagram cookies for Playwright |
| `data/screenshots/` | Profile screenshots organized by date |
| `data/raw_responses/` | API response logs |
| `data/logs/bot.log` | Bot logs |
