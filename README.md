# Instagram Ban Account Monitor

A Telegram bot that monitors Instagram accounts for visibility changes (ACTIVE/MISSING) with screenshot evidence, per-user monitoring, and real-time notifications.

## Features

- **Per-user monitoring** — each user sees only their own added accounts; admins see everything
- **Dual-layer checking** — curl_cffi API check + Playwright browser verification for MISSING accounts
- **Screenshot evidence** — captures profile screenshots via external Camofox-based service
- **Transition alerts** — notifies on ACTIVE→MISSING (ban) and MISSING→ACTIVE (restoration)
- **Multi-user access** — admin + allowed user roles stored in SQLite
- **Inline menu** — Telegram keyboard buttons for quick access
- **Auto-restart** — survives crashes via tmux shell loop
- **Changelog system** — admins can broadcast updates to all users

## How It Works

1. User adds accounts via `/add username`
2. Bot checks each account on a random interval (~60s ± 15s)
3. curl_cffi hits Instagram's API; if MISSING, Playwright opens the profile in headless Chrome
4. If statuses disagree → marked as SUSPECT
5. On ACTIVE→MISSING transition → screenshot captured + notification sent
6. On MISSING→ACTIVE transition → restoration notification with profile data
7. Notifications go to all admins + the user who added the account

## Requirements

- Python 3.10+
- tmux
- Telegram bot token (from @BotFather)
- Proxy (recommended to avoid IP bans)
- Screenshot service (optional, Camofox-based)

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url> && cd instagram-ban-account
cp config.yaml.example config.yaml  # edit with your tokens
./setup.sh

# 2. Start
./start.sh

# 3. Watch live
tmux attach -t ig-monitor

# 4. Stop
./stop.sh
```

## Scripts

| Script | Description |
|--------|-------------|
| `setup.sh` | One-time setup: venv, pip deps, Playwright browsers, data dirs |
| `start.sh` | Start bot in tmux session `ig-monitor` with auto-restart on crash |
| `stop.sh` | Send SIGTERM to bot process, then kill tmux session |

## Configuration

Edit `config.yaml`:

```yaml
telegram_token: "your-bot-token"
check_interval: 60          # seconds between checks
request_timeout: 30         # HTTP timeout in seconds

proxy:
  enabled: true
  server: "host:port"
  username: "user"
  password: "pass"

retry:
  attempts: 3
  backoff_seconds: [5, 15, 45]

playwright:
  enabled: true             # MISSING account verification
  headless: true
  timeout: 30000

screenshot_service_url: "http://localhost:8080"  # Camofox service

instagram_auth:
  enabled: false
  cookies_path: "./data/cookies.json"

log_level: "INFO"

test_accounts:              # verified once at startup only, never monitored
  - "some_account"

admins:
  - "your_telegram_username"
```

### Config Reference

| Field | Default | Description |
|-------|---------|-------------|
| `telegram_token` | — | Bot token from BotFather |
| `check_interval` | 300 | Seconds between check cycles |
| `request_timeout` | 30 | HTTP request timeout |
| `proxy.enabled` | false | Enable proxy for requests |
| `proxy.server` | — | Proxy host:port |
| `proxy.username` | — | Proxy auth username |
| `proxy.password` | — | Proxy auth password |
| `retry.attempts` | 3 | Max retry attempts per check |
| `retry.backoff_seconds` | [5,15,45] | Backoff between retries |
| `playwright.enabled` | true | Verify MISSING accounts with browser |
| `playwright.headless` | true | Run Chrome headless |
| `playwright.timeout` | 30000 | Browser timeout (ms) |
| `screenshot_service_url` | — | Camofox screenshot service URL |
| `instagram_auth.enabled` | false | Use cookies for Playwright |
| `instagram_auth.cookies_path` | ./data/cookies.json | Path to cookies.json |
| `database_path` | ./data/monitor.db | SQLite database path |
| `raw_responses_dir` | ./data/raw_responses | API response logs |
| `logs_dir` | ./data/logs | Log file directory |
| `screenshots_dir` | ./data/screenshots | Screenshot storage |
| `log_level` | INFO | Logging level |
| `test_accounts` | [] | Startup-only verification accounts |
| `admins` | ["vaproh"] | Admin usernames (seeded to DB) |
| `user_agent` | Instagram Android | User-Agent for API requests |

## Project Structure

```
├── bot/
│   ├── __init__.py
│   ├── __main__.py       # Entry point for `python -m bot`
│   ├── main.py           # Wires config, db, monitor, telegram together
│   ├── config.py         # YAML config loader → Config dataclass
│   ├── database.py       # SQLite: accounts, checks, events, admins, changelogs
│   ├── checker.py        # curl_cffi API check + Playwright verification + screenshots
│   ├── monitor.py        # Check loop, state tracking, transition notifications
│   ├── telegram.py       # Bot commands, access control, inline menu, notifications
│   └── logger.py         # Logging setup
├── config.yaml           # Production config (gitignored)
├── requirements.txt      # Python dependencies
├── setup.sh              # One-time setup
├── start.sh              # Start in tmux with auto-restart
├── stop.sh               # Graceful shutdown
├── bot.md                # Bot command reference
└── README.md             # This file
```

## Data Directory (gitignored)

```
data/
├── monitor.db            # SQLite database
├── cookies.json          # Instagram cookies (optional)
├── screenshots/          # Profile screenshots by date
│   └── 2025-01-15/
│       └── username_add_123456.png
├── raw_responses/        # API response logs
└── logs/
    └── bot.log           # Bot logs
```

## Commands

See `bot.md` for the full command reference with examples and behavior details.

## License

Private. Do not distribute.
