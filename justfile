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

# Syntax check all Python files
lint:
    @for f in bot/*.py checker.py proxy_wrapper.py; do \
        python3 -m py_compile "$f" && echo "✓ $f" || echo "✗ $f"; \
    done

# Clean data directory (keeps config)
clean:
    rm -rf data/logs/* data/screenshots/* data/raw_responses/* data/monitor.db
    echo "[+] Data cleaned"

# Remove venv and reinstall
reinstall:
    rm -rf .venv
    just setup
