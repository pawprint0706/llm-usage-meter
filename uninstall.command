#!/bin/bash
# Double-click to uninstall LLM Usage Meter on macOS.
cd "$(dirname "$0")"

if ./uninstall.sh; then
    :
else
    echo ""
    echo "Uninstall failed - see the messages above."
    read -p "Press Enter to close this window..."
fi
