#!/usr/bin/env bash
SESSION="ig-monitor"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if tmux has-session -t "$SESSION" 2>/dev/null; then
    # Find the python process and send SIGTERM for clean shutdown
    PANE_PID=$(tmux list-panes -t "$SESSION" -F "#{pane_pid}" 2>/dev/null | head -1)
    if [ -n "$PANE_PID" ]; then
        # Walk the process tree to find the python process
        for PID in $(pgrep -P "$PANE_PID" 2>/dev/null) $(pgrep -P "$(pgrep -P "$PANE_PID" 2>/dev/null)" 2>/dev/null); do
            if ps -p "$PID" -o command= 2>/dev/null | grep -q "python -m bot"; then
                echo -e "${GREEN}[+]${NC} Sending SIGTERM to bot (PID $PID)..."
                kill -TERM "$PID" 2>/dev/null
                sleep 3
                break
            fi
        done
    fi
    echo -e "${GREEN}[+]${NC} Killing tmux session '$SESSION'..."
    tmux kill-session -t "$SESSION" 2>/dev/null
    echo -e "${GREEN}[+]${NC} Bot stopped"
else
    echo -e "${YELLOW}[!]${NC} No tmux session '$SESSION' found. Bot is not running."
fi
