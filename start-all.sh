#!/bin/bash

SESSION_NAME="web-app"

# Check if tmux session already exists
tmux has-session -t $SESSION_NAME 2>/dev/null

if [ $? == 0 ]; then
  echo "Session '$SESSION_NAME' is already running!"
  echo "To view it, run: tmux attach -t $SESSION_NAME"
  echo "To restart it, run ./stop-all.sh first."
  exit 0
fi

echo "Creating new tmux session: $SESSION_NAME"

# Create a new detached session with one window
tmux new-session -d -s $SESSION_NAME -n 'services'

# Pane 0: Start Backend
tmux send-keys -t $SESSION_NAME:0.0 "cd ~/dev/openbull" C-m
tmux send-keys -t $SESSION_NAME:0.0 "ulimit -n 65535" C-m
tmux send-keys -t $SESSION_NAME:0.0 "source .venv/bin/activate" C-m
tmux send-keys -t $SESSION_NAME:0.0 "uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload" C-m

# Split the window horizontally for the frontend
tmux split-window -h -t $SESSION_NAME:0

# Pane 1: Start Frontend
tmux send-keys -t $SESSION_NAME:0.1 "cd ~/dev/openbull/frontend" C-m
tmux send-keys -t $SESSION_NAME:0.1 "npm run dev -- --host 0.0.0.0" C-m

echo "=================================================="
echo " Backend and Frontend are running in the background."
echo " The backend file limit has been safely increased."
echo ""
echo " View the live logs using:  tmux attach -t $SESSION_NAME"
echo " Detach from the logs by pressing:  Ctrl+b then d"
echo "=================================================="
