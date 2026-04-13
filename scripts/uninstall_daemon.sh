#!/usr/bin/env bash
# uninstall_daemon.sh
# ─────────────────────────────────────────────────────────────────────────────
# Stop and remove the PineScript MCP Launch Agent.
# The server process is terminated and the plist is removed.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

LABEL="com.pinescript.mcp"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "==> PineScript MCP Daemon Uninstaller"

if [[ ! -f "$PLIST_DST" ]]; then
    echo "Daemon plist not found at $PLIST_DST — already uninstalled?"
    exit 0
fi

# Unload (stops process + removes from launchd)
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
    echo "--> Unloading daemon..."
    launchctl unload -w "$PLIST_DST"
    echo "--> Daemon stopped"
else
    echo "--> Daemon was not running"
fi

# Remove plist
rm -f "$PLIST_DST"
echo "--> Plist removed: $PLIST_DST"

echo ""
echo "Daemon uninstalled. The server is no longer running."
echo "To revert your IDE config, restore the command-based entry in mcp_config.json."
