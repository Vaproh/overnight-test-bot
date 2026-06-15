#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
SESSION="ig-monitor"
LOG_DIR="$SCRIPT_DIR/data/logs"
LOG_FILE="$LOG_DIR/bot.log"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[x]${NC} $1"; }

# ── Pre-flight ──
if [ ! -d "$VENV_DIR" ]; then
    err "Virtual environment not found. Run ./setup.sh first."
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    err "config.yaml not found."
    exit 1
fi

mkdir -p "$LOG_DIR"

# ── Kill existing processes on proxy/checker ports ──
for port in 8888 8081; do
    pid=$(lsof -ti:$port 2>/dev/null)
    if [ -n "$pid" ]; then
        warn "Killing process on port $port (pid $pid)"
        kill $pid 2>/dev/null
        sleep 1
    fi
done

# ── Kill existing tmux session ──
if tmux has-session -t "$SESSION" 2>/dev/null; then
    warn "Stopping existing session '$SESSION'..."
    tmux kill-session -t "$SESSION"
fi

# ── Start all services in tmux ──
log "Starting services in tmux session '$SESSION'..."
tmux new-session -d -s "$SESSION" "
    cd $SCRIPT_DIR

    # 1. Local proxy wrapper (adds auth for upstream DataImpulse)
    echo \"[\$(date '+%H:%M:%S')] Starting proxy wrapper on :8888...\"
    $VENV_DIR/bin/python proxy_wrapper.py &
    PROXY_PID=\$!
    sleep 1

    # 2. Checker service (Playwright-based profile checker)
    echo \"[\$(date '+%H:%M:%S')] Starting checker service on :8081...\"
    $VENV_DIR/bin/python -m uvicorn checker:app --host 0.0.0.0 --port 8081 &
    CHECKER_PID=\$!
    sleep 2

    # 3. Monitor bot (main loop)
    while true; do
        echo \"[\$(date '+%H:%M:%S')] Bot starting...\"
        $VENV_DIR/bin/python -m bot 2>&1 | tee -a $LOG_FILE
        EXIT_CODE=\$?
        echo \"[\$(date '+%H:%M:%S')] Bot exited with code \$EXIT_CODE. Restarting in 5s...\"
        sleep 5
    done
"

sleep 2
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo ""
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}  All services running in tmux!${NC}"
    echo -e ""
    echo -e "  Session:    ${CYAN}$SESSION${NC}"
    echo -e "  Attach:     ${CYAN}tmux attach -t $SESSION${NC}"
    echo -e "  Logs:       ${CYAN}$LOG_FILE${NC}"
    echo -e "  Stop:       ${CYAN}./stop.sh${NC}"
    echo -e ""
    echo -e "  Services:"
    echo -e "    Proxy:    ${CYAN}:8888${NC} (DataImpulse auth wrapper)"
    echo -e "    Checker:  ${CYAN}:8081${NC} (Playwright profile checker)"
    echo -e "    Bot:      ${CYAN}monitor${NC} (state tracking & notifications)"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
else
    err "Failed to start tmux session."
    exit 1
fi
