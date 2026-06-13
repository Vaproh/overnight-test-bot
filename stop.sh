#!/usr/bin/env bash
SESSION="ig-monitor"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo -e "${GREEN}[+]${NC} Killing tmux session '$SESSION'..."
    tmux kill-session -t "$SESSION"
    echo -e "${GREEN}[+]${NC} Bot stopped"
else
    echo -e "${YELLOW}[!]${NC} No tmux session '$SESSION' found. Bot is not running."
fi
