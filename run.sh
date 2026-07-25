#!/bin/bash
# (Re)start LLM Usage Meter detached from this terminal: a running instance is
# stopped first (--replace), and closing the terminal does not kill the app.
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "No .venv found - run ./setup.sh first."
    exit 1
fi

LOG_DIR="$HOME/.llm-usage-meter"
mkdir -p "$LOG_DIR"

nohup .venv/bin/python launch.py --replace </dev/null >>"$LOG_DIR/launcher.log" 2>&1 &
disown 2>/dev/null || true

echo "The meter is (re)starting in the menu bar. You can close this terminal."
echo "(Logs: $LOG_DIR/app.log)"
