#!/bin/bash
# Uninstall LLM Usage Meter (macOS / Linux): stop the app, remove the autostart
# entry, the stored logins, the app data and the virtualenv.
set -e
cd "$(dirname "$0")"

echo "This will stop LLM Usage Meter and remove:"
echo "  - the start-at-login entry (LaunchAgent / autostart)"
echo "  - the Codex login and the OpenCode session key from the keychain"
echo "  - app data in ~/.llm-usage-meter"
echo "  - the .venv folder in this project"
read -p "Continue? [y/N] " CONFIRM
case "$CONFIRM" in
    [yY]|[yY][eE][sS]) ;;
    *) echo "Cancelled."; exit 0 ;;
esac

echo ""
echo "Stopping the app and removing logins and autostart..."
if [ -x .venv/bin/python ]; then
    .venv/bin/python launch.py --uninstall >/dev/null 2>&1 || true
fi

# Fallback: remove the LaunchAgent directly if the virtualenv is already gone.
LABEL="local.llm-usage-meter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ -f "$PLIST" ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
    rm -f "$PLIST"
fi
rm -f "$HOME/.config/autostart/llm-usage-meter.desktop"

echo "Removing app data (~/.llm-usage-meter) and .venv..."
rm -rf "$HOME/.llm-usage-meter"
rm -rf .venv

echo ""
echo "Done. LLM Usage Meter has been removed."
echo "You can now delete this project folder if you want: $(pwd)"
read -p "Press Enter to close this window..."
