#!/usr/bin/env bash
# install_daemon.sh
# ─────────────────────────────────────────────────────────────────────────────
# Install PineScript MCP as a persistent macOS Launch Agent.
#
# What it does:
#   1. Resolves project paths automatically (no manual editing needed)
#   2. Fills in the launchd plist template with real paths
#   3. Copies the plist to ~/Library/LaunchAgents/
#   4. Loads it with launchctl (starts immediately + on every login)
#   5. Prints the URL to add to your Windsurf/IDE MCP config
#
# Usage:
#   bash scripts/install_daemon.sh              # port 8765 (default)
#   bash scripts/install_daemon.sh --port 9000  # custom port
#
# After running:
#   Update your Windsurf MCP config:
#   Change the pinescript-v6 entry from command-based to:
#     "serverUrl": "http://127.0.0.1:PORT/mcp"
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
SERVER_PY="$PROJECT_DIR/server.py"
PLIST_TEMPLATE="$PROJECT_DIR/launchd/com.pinescript.mcp.plist"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DST="$LAUNCH_AGENTS_DIR/com.pinescript.mcp.plist"
LOG_DIR="$HOME/Library/Logs"
DB_PATH="$PROJECT_DIR/pinescript_db"
PORT="8765"
LABEL="com.pinescript.mcp"

# ── Parse args ───────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: $0 [--port PORT]"
            echo "  --port PORT   HTTP port (default: 8765)"
            exit 0
            ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

# ── Preflight checks ─────────────────────────────────────────────────────────
echo "==> PineScript MCP Daemon Installer"
echo ""

if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    echo "Run: make install"
    exit 1
fi

if [[ ! -f "$SERVER_PY" ]]; then
    echo "ERROR: server.py not found at $SERVER_PY"
    exit 1
fi

if [[ ! -f "$PLIST_TEMPLATE" ]]; then
    echo "ERROR: plist template not found at $PLIST_TEMPLATE"
    exit 1
fi

# Check port availability
if lsof -ti ":$PORT" &>/dev/null; then
    EXISTING_PID=$(lsof -ti ":$PORT" | head -1)
    EXISTING_CMD=$(ps -p "$EXISTING_PID" -o comm= 2>/dev/null || echo "unknown")
    echo "WARNING: Port $PORT is already in use by PID $EXISTING_PID ($EXISTING_CMD)"
    echo "The daemon may fail to start. Choose a different port with --port."
    read -r -p "Continue anyway? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
fi

# ── Unload existing daemon if running ────────────────────────────────────────
if launchctl list | grep -q "$LABEL" 2>/dev/null; then
    echo "--> Unloading existing daemon..."
    launchctl unload "$PLIST_DST" 2>/dev/null || true
fi

# ── Fill plist template ──────────────────────────────────────────────────────
mkdir -p "$LAUNCH_AGENTS_DIR"

sed \
    -e "s|__VENV_PYTHON__|$VENV_PYTHON|g" \
    -e "s|__SERVER_PY__|$SERVER_PY|g" \
    -e "s|__PORT__|$PORT|g" \
    -e "s|__DB_PATH__|$DB_PATH|g" \
    -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__LOG_DIR__|$LOG_DIR|g" \
    "$PLIST_TEMPLATE" > "$PLIST_DST"

echo "--> Plist written to: $PLIST_DST"

# ── Load the daemon ──────────────────────────────────────────────────────────
launchctl load -w "$PLIST_DST"
echo "--> Daemon loaded and started"

# ── Wait for health check ────────────────────────────────────────────────────
echo "--> Waiting for server to come up (port $PORT)..."
MAX_WAIT=30
ELAPSED=0
while ! curl -sf "http://127.0.0.1:$PORT/health" &>/dev/null; do
    sleep 1
    ELAPSED=$((ELAPSED + 1))
    if [[ $ELAPSED -ge $MAX_WAIT ]]; then
        echo ""
        echo "WARNING: Server did not respond within ${MAX_WAIT}s."
        echo "Check logs: tail -f ~/Library/Logs/pinescript_mcp_err.log"
        break
    fi
    printf "."
done
echo ""

# ── Health check ─────────────────────────────────────────────────────────────
HEALTH=$(curl -sf "http://127.0.0.1:$PORT/health" 2>/dev/null || echo '{"status":"unreachable"}')
echo "--> Health: $HEALTH"

# ── Print next steps ─────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  PineScript MCP daemon installed!                           ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Server URL:  http://127.0.0.1:$PORT/mcp"
echo "  Health URL:  http://127.0.0.1:$PORT/health"
echo "  Logs:        ~/Library/Logs/pinescript_mcp.log"
echo "  Error log:   ~/Library/Logs/pinescript_mcp_err.log"
echo ""
echo "  Update your Windsurf MCP config (~/.codeium/windsurf/mcp_config.json):"
echo "  Replace the pinescript-v6 'command' entry with:"
echo ""
echo '    "pinescript-v6": {'
echo "      \"serverUrl\": \"http://127.0.0.1:$PORT/mcp\","
echo '      "disabled": false'
echo '    }'
echo ""
echo "  Management commands:"
echo "    make daemon-status   # check daemon status"
echo "    make daemon-logs     # tail server logs"
echo "    make undaemon        # stop + uninstall daemon"
