#!/bin/bash
# Create the virtualenv, install dependencies and start the app (macOS / Linux).
set -e
cd "$(dirname "$0")"

# Stop a running instance first, so package files aren't replaced under it.
if [ -x .venv/bin/python ]; then
    echo "Stopping any running instance..."
    .venv/bin/python launch.py --stop >/dev/null 2>&1 || true
fi

python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo ""
echo "Done. Starting LLM Usage Meter..."
LOG_DIR="$HOME/.llm-usage-meter"
mkdir -p "$LOG_DIR"
nohup .venv/bin/python launch.py --replace </dev/null >>"$LOG_DIR/launcher.log" 2>&1 &
disown 2>/dev/null || true
echo "The meter is (re)starting in the menu bar. You can close this terminal."
echo "(Logs: $LOG_DIR/app.log)"
echo "Tip: turn on 'Start at login' in the popup's settings menu."
read -p "Press Enter to close this window..."
