#!/bin/bash

SESSION_NAME="web-app"

# 1. Kill the tmux session if it exists
tmux has-session -t $SESSION_NAME 2>/dev/null
if [ $? == 0 ]; then
  echo "Stopping tmux session: $SESSION_NAME"
  
  # Send Ctrl+C to both panes to allow graceful shutdown
  tmux send-keys -t $SESSION_NAME:0.0 C-c
  tmux send-keys -t $SESSION_NAME:0.1 C-c
  sleep 2
  
  tmux kill-session -t $SESSION_NAME
  echo "Tmux session stopped."
else
  echo "Tmux session '$SESSION_NAME' is not currently running."
fi

# 2. Cleanup any orphaned processes (Force kill)
echo "Cleaning up any orphaned backend or frontend processes..."
pkill -f "uvicorn backend.main:app"
pkill -f "vite --host 0.0.0.0"

echo "All services completely stopped."
