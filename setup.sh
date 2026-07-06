#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }

# ── Check uv ──
if ! command -v uv &>/dev/null; then
    log "uv not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── Install dependencies ──
log "Installing dependencies with uv..."
uv sync

# ── Playwright browsers ──
log "Installing Playwright Chromium..."
uv run playwright install chromium 2>/dev/null || uv run playwright install

# ── Data directories ──
mkdir -p "$SCRIPT_DIR/data"
mkdir -p "$SCRIPT_DIR/data/logs"
mkdir -p "$SCRIPT_DIR/data/screenshots"
mkdir -p "$SCRIPT_DIR/data/raw_responses"

# ── Config check ──
if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    warn "config.yaml not found — create one before starting the bot"
fi

log "Setup complete. Run ./start.sh to start the bot."
