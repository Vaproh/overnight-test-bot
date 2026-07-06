# Instagram Ban Account Monitor

A Telegram bot that monitors Instagram accounts for visibility changes (ACTIVE/MISSING) with profile card evidence, per-user monitoring, and real-time notifications.

## Features

- **Per-user monitoring** — each user sees only their own added accounts; admins see everything
- **Dual-layer checking** — curl_cffi API check + Playwright browser verification for MISSING accounts
- **Profile cards** — generates aesthetic profile cards via external service
- **Transition alerts** — notifies on ACTIVE→MISSING (ban) and MISSING→ACTIVE (restoration)
- **Multi-user access** — admin + allowed user roles stored in SQLite
- **Inline menu** — Telegram keyboard buttons for quick access
- **Auto-restart** — survives crashes via tmux shell loop
- **Changelog system** — admins can broadcast updates to all users
- **Report system** — users can report issues to admins

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- tmux
- Telegram bot token (from @BotFather)
- Proxy (recommended to avoid IP bans)

## Quick Start

```bash
# 1. Clone and setup
git clone <repo-url> && cd instagram-ban-account
cp config.example.yaml config.yaml  # edit with your tokens
just setup

# 2. Start
just start

# 3. Watch live
just logs

# 4. Stop
just stop
```

## Commands

| Command | Description |
|---------|-------------|
| `just setup` | One-time setup: uv venv, deps, Playwright, data dirs |
| `just start` | Start all services in tmux session `ig-monitor` |
| `just stop` | Stop all services |
| `just logs` | Attach to tmux session |
| `just logtail` | Tail the bot log file |
| `just check <username>` | One-time check on an account |
| `just lint` | Syntax check all Python files |
| `just clean` | Remove data directory contents |
| `just reinstall` | Remove venv and reinstall |

## Configuration

Edit `config.yaml`:

```yaml
telegram_token: "your-bot-token"
check_interval: 60
request_timeout: 30

proxy:
  enabled: true
  server: "gw.dataimpulse.com:823"
  username: "user"
  password: "pass"

retry:
  attempts: 3
  backoff_seconds: [5, 15, 45]

playwright:
  enabled: true
  headless: true
  timeout: 30000

screenshot_service_url: "http://localhost:8080"

checker_service_url: "http://localhost:8081"

log_level: "INFO"

admins:
  - "your_telegram_username"
```

See `config.example.yaml` for all available options.

## Project Structure

```
├── bot/
│   ├── __init__.py
│   ├── __main__.py       # Entry point for `python -m bot`
│   ├── main.py           # Wires config, db, monitor, telegram together
│   ├── config.py         # YAML config loader → Config dataclass
│   ├── database.py       # SQLite: accounts, checks, events, admins, reports
│   ├── checker.py        # curl_cffi checks + HTTP calls to checker service
│   ├── monitor.py        # Check loop, state tracking, transition notifications
│   ├── telegram.py       # Bot commands, access control, inline menu, notifications
│   └── logger.py         # Logging setup
├── checker.py            # Standalone FastAPI profile checker service
├── proxy_wrapper.py      # Local HTTP proxy wrapper for DataImpulse auth
├── config.example.yaml   # Example config (copy to config.yaml)
├── pyproject.toml        # UV/Python project config
├── justfile              # Task runner commands
├── start.sh              # Start services in tmux
├── stop.sh               # Stop services
├── bot.md                # Bot command reference
├── LICENSE               # MIT License
└── README.md             # This file
```

## Architecture

```
                    ┌─────────────┐
                    │  Telegram   │
                    │   Bot API   │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  Bot Core   │
                    │ (telegram.py│
                    │  monitor.py)│
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────▼──────┐    │    ┌──────▼──────┐
       │  curl_cffi  │    │    │  Checker    │
       │  (primary)  │    │    │  Service    │
       └─────────────┘    │    │  (:8081)    │
                          │    └──────┬──────┘
                   ┌──────▼──────┐    │
                   │   Proxy     │    │
                   │  Wrapper    │    │
                   │   (:8888)   │    │
                   └─────────────┘    │
                                      │
                               ┌──────▼──────┐
                               │  Playwright  │
                               │  (verify)    │
                               └─────────────┘
```

## Data Directory (gitignored)

```
data/
├── monitor.db            # SQLite database
├── screenshots/          # Profile screenshots by date
├── raw_responses/        # API response logs
└── logs/
    └── bot.log           # Bot logs
```

## Bot Commands

See `bot.md` for the full command reference with examples and behavior details.

## License

[MIT](LICENSE)
