# Instagram Ban Account Monitor

A lightweight Telegram bot that monitors Instagram accounts for visibility changes (ACTIVE/MISSING) with screenshot evidence and notifications.

## How It Works

1. You add accounts via Telegram `/add username`
2. Bot checks each account every ~60 seconds using Instagram's API
3. If an account goes from ACTIVE → MISSING, you get a screenshot + notification
4. If it comes back (MISSING → ACTIVE), you get a restoration notification

## Requirements

- Python 3.10+
- tmux
- A Telegram bot token (from @BotFather)
- Proxy (optional but recommended to avoid IP bans)

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

## Configuration

Edit `config.yaml`:

```yaml
telegram_token: "your-bot-token"
check_interval: 60          # seconds between checks
proxy:
  enabled: true
  server: "host:port"
  username: "user"
  password: "pass"
test_accounts:              # verified once at startup only
  - "some_account"
admins:
  - "your_telegram_username"
```

## Project Structure

```
bot/
├── main.py        # Entry point, wires everything together
├── config.py      # YAML config loader
├── database.py    # SQLite (accounts, checks, events, admins)
├── checker.py     # curl_cffi API check + Playwright screenshots
├── monitor.py     # Check loop, transitions, notifications
├── telegram.py    # Bot commands, access control, inline menu
└── logger.py      # Logging setup
```

## Data (gitignored)

```
data/
├── monitor.db           # SQLite database
├── cookies.json         # Instagram cookies (optional)
├── screenshots/         # Profile screenshots by date
├── raw_responses/       # API response logs
└── logs/                # Bot logs
```

## License

Private. Do not distribute.
