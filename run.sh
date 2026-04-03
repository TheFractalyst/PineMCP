#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# run.sh — PineScript v6 MCP Server full pipeline
#
# 3-stage pipeline: discover → scrape → merge+index
#
# Usage:
#   chmod +x run.sh
#   ./run.sh [--rescrape] [--reset-db] [--skip-scrape] [--entry=X]
#
# Options:
#   --rescrape     Force re-scrape from TradingView
#   --reset-db     Wipe and rebuild ChromaDB from scratch
#   --skip-scrape  Skip scraping step (use existing data)
#   --entry=X      Scrape a single entry by fragment id
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Colour helpers ───────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()    { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }
header()  { echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}\n"; }

# ─── Parse flags ─────────────────────────────────────────────────────────────
RESCRAPE=false
RESET_DB=false
SKIP_SCRAPE=false
SINGLE_ENTRY=""

for arg in "$@"; do
    case "$arg" in
        --rescrape)    RESCRAPE=true ;;
        --reset-db)    RESET_DB=true ;;
        --skip-scrape) SKIP_SCRAPE=true ;;
        --entry=*)     SINGLE_ENTRY="${arg#*=}" ;;
        --help|-h)
            echo "Usage: ./run.sh [--rescrape] [--reset-db] [--skip-scrape] [--entry=X]"
            echo ""
            echo "  --rescrape     Force re-scrape from TradingView"
            echo "  --reset-db     Wipe and rebuild ChromaDB from scratch"
            echo "  --skip-scrape  Skip scraping step (use existing data)"
            echo "  --entry=X      Scrape a single entry by fragment id"
            exit 0
            ;;
    esac
done

# ─── Locate script directory ──────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

header "PineScript v6 MCP Server — Pipeline"
info "Rescrape:    $RESCRAPE"
info "Reset DB:    $RESET_DB"
info "Skip scrape: $SKIP_SCRAPE"
info "Entry:       ${SINGLE_ENTRY:-none}"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Virtual environment
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 1: Python virtual environment"

VENV_DIR="$SCRIPT_DIR/.venv"

if [ -d "$VENV_DIR" ]; then
    success "Virtual environment exists: $VENV_DIR"
else
    info "Creating virtual environment at $VENV_DIR ..."
    python3 -m venv "$VENV_DIR" || fail "Failed to create venv."
    success "Virtual environment created."
fi

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
info "Python: $($PYTHON --version)"

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Install dependencies
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 2: Installing dependencies"

info "Upgrading pip ..."
"$PIP" install --quiet --upgrade pip

info "Installing from requirements.txt ..."
"$PIP" install --quiet -r "$SCRIPT_DIR/requirements.txt"
success "Dependencies installed."

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Install Playwright Chromium
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 3: Playwright Chromium"

if [ "$SKIP_SCRAPE" = true ]; then
    info "Skipping Playwright check (--skip-scrape)."
else
    info "Checking Playwright Chromium..."
    "$PYTHON" -m playwright install chromium 2>/dev/null && \
        success "Playwright Chromium ready." || \
        warn "Playwright install failed. Scraping may not work. Try: python -m playwright install chromium"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Parse local documentation
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 4: Local documentation"

CHUNKS_FILE="$SCRIPT_DIR/pinescript_chunks.json"

if [ -f "$CHUNKS_FILE" ]; then
    LOCAL_COUNT=$("$PYTHON" -c "import json; print(len(json.load(open('$CHUNKS_FILE'))))" 2>/dev/null || echo "?")
    success "pinescript_chunks.json exists ($LOCAL_COUNT entries)."
else
    if [ -f "$SCRIPT_DIR/parse_docs.py" ]; then
        info "Running parse_docs.py ..."
        "$PYTHON" "$SCRIPT_DIR/parse_docs.py"
        LOCAL_COUNT=$("$PYTHON" -c "import json; print(len(json.load(open('$CHUNKS_FILE'))))" 2>/dev/null || echo "0")
        success "Parsed $LOCAL_COUNT entries."
    else
        warn "No pinescript_chunks.json and no parse_docs.py. Continuing with live data only."
        LOCAL_COUNT="0"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Discover TradingView entries
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 5: Discover TradingView entries"

INDEX_FILE="$SCRIPT_DIR/tv_entry_index.json"

if [ -f "$INDEX_FILE" ] && [ "$RESCRAPE" = false ]; then
    INDEX_COUNT=$("$PYTHON" -c "import json; print(len(json.load(open('$INDEX_FILE'))))" 2>/dev/null || echo "0")
    success "Using existing index: $INDEX_COUNT entries."
else
    info "Running discover_entries.py ..."
    "$PYTHON" "$SCRIPT_DIR/discover_entries.py"
    INDEX_COUNT=$("$PYTHON" -c "import json; print(len(json.load(open('$INDEX_FILE'))))" 2>/dev/null || echo "0")
    success "Discovered $INDEX_COUNT entries."
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Scrape TradingView entries
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 6: Scrape TradingView entries"

SCRAPE_FILE="$SCRIPT_DIR/tv_scraped_entries.json"

if [ "$SKIP_SCRAPE" = true ]; then
    info "Skipping scrape (--skip-scrape)."
elif [ -n "$SINGLE_ENTRY" ]; then
    info "Scraping single entry: $SINGLE_ENTRY"
    "$PYTHON" "$SCRIPT_DIR/scrape_entries.py" --entry "$SINGLE_ENTRY"
elif [ -f "$SCRAPE_FILE" ] && [ "$RESCRAPE" = false ]; then
    SCRAPE_COUNT=$("$PYTHON" -c "import json; print(len(json.load(open('$SCRAPE_FILE'))))" 2>/dev/null || echo "0")
    success "Using existing scrape: $SCRAPE_COUNT entries. Use --rescrape to refresh."
else
    info "Running scrape_entries.py ..."
    "$PYTHON" "$SCRIPT_DIR/scrape_entries.py"
    SCRAPE_COUNT=$("$PYTHON" -c "import json; print(len(json.load(open('$SCRAPE_FILE'))))" 2>/dev/null || echo "0")
    success "Scraped $SCRAPE_COUNT entries."
fi

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Merge and index into ChromaDB
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 7: Merge and index"

MERGE_ARGS=""
if [ "$RESET_DB" = true ]; then
    MERGE_ARGS="--reset"
    warn "--reset-db: wiping existing ChromaDB index."
fi

"$PYTHON" "$SCRIPT_DIR/merge_and_index.py" $MERGE_ARGS
success "Merge and index complete."

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 7.5 — Test pine-facade compiler connectivity
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 7.5: Pine-facade compiler"

info "Testing pine-facade compiler connectivity..."
"$PYTHON" - <<'PYEOF' 2>/dev/null || true
import httpx, urllib.parse, sys
try:
    r = httpx.post(
        "https://pine-facade.tradingview.com/pine-facade/compile",
        content=urllib.parse.urlencode({
            "source": '//@version=6\nindicator("test")\nplot(close)',
            "version": '{"major":6,"minor":0}',
        }),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
        },
        timeout=10,
    )
    data = r.json()
    errors = data.get("result", {}).get("errors", [])
    if not errors:
        print("  pine-facade: ONLINE (validation tools ready)")
    else:
        print(f"  pine-facade: ONLINE (test compile had {len(errors)} errors — expected for minimal code)")
except Exception as e:
    print(f"  pine-facade: UNREACHABLE ({e})")
    print("  Validation tools will degrade gracefully.")
PYEOF

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Final summary
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 8: Final summary"

DB_DIR="$SCRIPT_DIR/pinescript_db"
DB_COUNT=$("$PYTHON" - <<PYEOF
import sys
try:
    import chromadb
    client = chromadb.PersistentClient(path="$DB_DIR")
    col = client.get_collection("pinescript_v6")
    print(col.count())
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    print(0)
PYEOF
)

echo -e "${BOLD}Database entries:    ${GREEN}$DB_COUNT${NC}"
echo -e "${BOLD}Local entries:       ${LOCAL_COUNT:-0}${NC}"
echo -e "${BOLD}TV index entries:    ${INDEX_COUNT:-N/A}${NC}"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# STEP 9 — IDE configuration
# ═══════════════════════════════════════════════════════════════════════════════
header "STEP 9: IDE Configuration"

ABS_PYTHON="$(realpath "$PYTHON" 2>/dev/null || echo "$PYTHON")"
ABS_SERVER="$(realpath "$SCRIPT_DIR/pinescript_mcp.py" 2>/dev/null || echo "$SCRIPT_DIR/pinescript_mcp.py")"
ABS_DB="$(realpath "$DB_DIR" 2>/dev/null || echo "$DB_DIR")"

echo -e "${BOLD}Absolute paths:${NC}"
echo -e "  Python : ${GREEN}$ABS_PYTHON${NC}"
echo -e "  Server : ${GREEN}$ABS_SERVER${NC}"
echo -e "  DB     : ${GREEN}$ABS_DB${NC}"
echo ""
echo -e "${BOLD}IDE config locations:${NC}"
echo -e "  Claude Desktop : ~/Library/Application Support/Claude/claude_desktop_config.json"
echo -e "  Claude Code    : .mcp.json (project root)"
echo -e "  Cursor         : .cursor/mcp.json"
echo -e "  Windsurf       : .windsurf/mcp.json"
echo -e "  OpenCode       : .opencode/mcp.json"
echo ""
echo -e "${BOLD}Quick test:${NC}"
echo -e "  $ABS_PYTHON $ABS_SERVER"
echo ""

echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  PineScript v6 MCP Server is READY.                 ${NC}"
echo -e "${GREEN}${BOLD}  $DB_COUNT entries indexed and searchable.             ${NC}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════${NC}"
