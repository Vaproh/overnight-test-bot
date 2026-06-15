#!/usr/bin/env bash
SESSION="ig-monitor"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Kill tmux session ──
if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo -e "${GREEN}[+]${NC} Killing tmux session '$SESSION'..."
    tmux kill-session -t "$SESSION" 2>/dev/null
    echo -e "${GREEN}[+]${NC} Bot stopped"
else
    echo -e "${YELLOW}[!]${NC} No tmux session '$SESSION' found."
fi

# ── Kill orphaned processes on proxy/checker ports ──
for port in 8888 8081; do
    pid=$(lsof -ti:$port 2>/dev/null)
    if [ -n "$pid" ]; then
        echo -e "${YELLOW}[!]${NC} Killing orphaned process on port $port (pid $pid)"
        kill $pid 2>/dev/null
    fi
done

echo -e "${GREEN}[+]${NC} All services stopped"
