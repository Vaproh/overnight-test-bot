# Instagram Ban Account Monitor

default:
    @just --list

# One-time setup: uv venv, deps, playwright, data dirs
setup:
    #!/usr/bin/env bash
    set -e
    if ! command -v uv &>/dev/null; then
        echo "[!] uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    echo "[+] Creating venv and installing dependencies..."
    uv sync
    echo "[+] Installing Playwright Chromium..."
    uv run playwright install chromium
    mkdir -p data data/logs data/screenshots data/raw_responses
    echo "[+] Setup complete. Run: just start"

# Start all services in tmux
start:
    ./start.sh

# Stop all services
stop:
    ./stop.sh

# Attach to tmux session
logs:
    tmux attach -t ig-monitor

# Tail bot log file
logtail:
    tail -f data/logs/bot.log

# Check status of monitored accounts
status:
    uv run python -m bot.cli status

# Run a one-time check on an account
check username:
    uv run python -m bot.cli check {{username}}

# Lint with ruff
lint:
    uv run ruff check .

# Format with ruff
fmt:
    uv run ruff format .

# Clean data, caches, and build artifacts (keeps config)
clean:
    #!/usr/bin/env bash
    rm -rf data/logs/* data/screenshots/* data/raw_responses/* data/monitor.db
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    rm -rf .ruff_cache .pytest_cache
    echo "[+] Cleaned"

# Remove venv and reinstall
reinstall:
    rm -rf .venv .ruff_cache
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    just setup
