"""
core/config.py
------------------------------------------------------------------------------
All configuration constants, environment variables, and server instructions.
"""

from __future__ import annotations

import os

SERVER_VERSION = "1.0.0"


def _safe_int(env_var: str, default: int, min_val: int = 0) -> int:
    """Parse env var as int, returning default on invalid or negative values."""
    try:
        val = int(os.getenv(env_var, str(default)))
        return val if val >= min_val else default
    except (ValueError, TypeError):
        return default


# -----------------------------------------------------------------------------
# Runtime configuration (env-var overridable)
# -----------------------------------------------------------------------------

DB_PATH = os.getenv(
    "PINESCRIPT_DB_PATH",
    os.path.join(os.path.expanduser("~"), ".pinescript_mcp", "db"),
)
COLLECTION = os.getenv("PINESCRIPT_COLLECTION", "pine_reference")
EMBED_MODEL = os.getenv("PINE_EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM = 384  # all-MiniLM-L6-v2 output dimension
MAX_RESULTS = _safe_int("PINESCRIPT_MAX_RESULTS", 100)
PINE_FACADE_URL = os.getenv(
    "PINE_FACADE_URL",
    "https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=admin&v=3",
)
PINE_FACADE_TIMEOUT = _safe_int("PINE_FACADE_TIMEOUT", 20)
PINE_FACADE_FALLBACK_ENABLED = os.getenv("PINE_FACADE_FALLBACK_ENABLED", "true").lower() in ("true", "1", "yes")
VALIDATION_CACHE_TTL = _safe_int("VALIDATION_CACHE_TTL", 300)
VALIDATION_CACHE_MAX_SIZE = _safe_int("VALIDATION_CACHE_SIZE", 500)
MAX_TOOL_RESPONSE_CHARS = 80000
MAX_FUZZY_SCAN_ENTRIES = 5000

# -----------------------------------------------------------------------------
# Search / lookup thresholds (previously magic numbers in tool code)
# -----------------------------------------------------------------------------
FUZZY_MATCH_THRESHOLD = 85        # search.py: minimum similarity % for fuzzy hit
SEMANTIC_DISTANCE_THRESHOLD = 0.35  # search.py/lookup.py: distance for semantic hit
RELEVANCE_DISTANCE_CUTOFF = 0.7   # search.py: discard results beyond this distance
TYPE_REJECTION_DISTANCE = 0.65    # lookup.py: reject type lookup beyond this distance

# -----------------------------------------------------------------------------
# Internal persistence controls (server-side only, HTTP transport only)
# -----------------------------------------------------------------------------

_TRANSPORT = os.getenv("TRANSPORT", "stdio").lower().strip()

# Pre-computed at import time (not inside per-call hot path)
_ALLOWED_BASE_DIRS = [
    os.path.realpath(os.path.expanduser("~")),
    os.path.realpath(os.path.expanduser("~/Documents")),
    os.path.realpath(os.path.expanduser("~/Desktop")),
    os.path.realpath(os.path.expanduser("~/Projects")),
    os.path.realpath(os.path.expanduser("~/repos")),
    os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
]

# -----------------------------------------------------------------------------
# Server instructions (shown to AI agents connecting to this MCP server)
# -----------------------------------------------------------------------------

INSTRUCTIONS = """\
You are connected to the complete PineScript v6 reference documentation server.

ABOUT PINESCRIPT v6
-------------------
PineScript is TradingView's domain-specific language for creating custom
technical indicators, strategies, and libraries that run on the TradingView
platform. Version 6 (v6) is the current production release and introduces
UDTs (user-defined types), methods, enums, polylines, and improved performance.

This server provides complete local PineScript v6 reference documentation
via a ChromaDB vector store covering ALL v6 functions, variables, types,
constants, keywords, operators, and annotations.

This server ONLY supports PineScript v6. No v4 or v5 content is included.
When migrating older scripts, use pine_repair(mode="migrate") to upgrade
v5 code to v6.

MANDATORY USAGE PATTERN (NON-NEGOTIABLE)
----------------------------------------
When working on ANY PineScript file (.ps, .pine, or any file containing
indicator(), strategy(), or library() declarations), you MUST consult this
server before writing code. Do NOT rely on training data or assumptions.

BEFORE writing a function call or using a built-in:
  1. pine_lookup(name) - exact symbol docs (auto-detects function/variable/
     type/constant/keyword/operator).
  2. If unsure of the name: pine_search(query) for semantic hits, or
     pine_search(query, current_line=...) for context-aware suggestions.

BEFORE using a namespace you haven't memorized:
  1. pine_browse(namespace) - list every member.
  2. pine_browse(namespace, style="cheatsheet") - compact reference.

AFTER every edit to a PineScript file (.ps/.pine or content-detected):
  1. pine_compile(code=...) - confirm it compiles.
     For large files: pine_compile(file_path="/abs/path.ps").
     To debug: pine_compile(code=..., explain=True).
  2. If the compile fails: pine_repair(code, context) - targeted fix + validation.

WHEN TO USE EACH TOOL
----------------------
READ SURFACE (always safe, idempotent):
  pine_lookup(name, kind?)
      Full docs for one symbol by exact name. `kind` in {function, variable,
      type, constant, keyword, operator} disambiguates; leave unset to
      auto-pick the richest match.
  pine_search(query, category?, namespace?, return_type?, has_examples?,
              current_line?, n_results?)
      Semantic discovery across the whole knowledge base.
        * return_type="series float"   -> functions returning that type.
        * current_line="ta."           -> context-aware suggestions.
        * has_examples=True            -> runnable code-example blocks only.
        * else                         -> ranked semantic hits across kinds.
  pine_browse(namespace, category?, style?)
      Enumerate every member of a namespace. style="cheatsheet" gives a
      compact, box-drawn signature summary.

WRITE SURFACE (compile/repair/generate):
  pine_compile(code?, file_path?, explain?)
      Compile inline code OR a file. explain=True embeds doc lookups for each
      error. File results are cached by (path, mtime, size).
  pine_repair(code, context, mode?)
      mode="targeted" (default) - fix a specific compiler error described by
          `context`.
      mode="migrate" - apply every known v5->v6 replacement in one pass, then
          recompile. Use this to upgrade v5 scripts to v6.
  pine_scaffold(kind, name, description?, inputs?, overlay?, initial_capital?,
                commission_pct?, pyramiding?)
      Generate a validated template.
        * kind="indicator" -> uses `inputs`, `overlay`.
        * kind="strategy"  -> uses `initial_capital`, `commission_pct`, `pyramiding`.

FILE DETECTION (pine_compile file branch)
------------------------------------------
pine_compile accepts a file_path by extension OR content:
  - .ps and .pine: always accepted.
  - Any other extension: accepted when the first 20 lines contain one of
    //@version=6, indicator(, strategy(, library(.

IMPORTANT NOTES
---------------
- This server ONLY supports PineScript v6. No v4 or v5 content.
- All code examples returned are real, working PineScript v6.
- PineScript is executed on every bar, so variable semantics differ from
  general-purpose languages.
- Use the `var` keyword for variables that must preserve state across bars.
- Strategy scripts require //@version=6 and strategy() declaration.
- Indicator scripts require //@version=6 and indicator() declaration.
- NEVER guess parameter types or function signatures - always verify with
  pine_lookup().
- TradingView ships 60+ ta.* functions, 40+ math.* functions, and extensive
  request.*/str.*/array.*/matrix.*/map.* utilities. Check before writing
  custom logic.

AUTOMATIC ERROR RECOVERY (REQUIRED FOR AGENTS)
-----------------------------------------------
If a tool call returns:
  {"error":"MCP error -32602: Invalid request parameters"}
the agent MUST recover automatically and retry - do NOT ask the user.

Recovery sequence:
  1) Refresh schema from tools/list and read the target tool's current
     argument schema before retrying.
  2) Send only schema-valid fields with correct JSON types.
  3) Remove unknown keys, fix enum values, and provide required fields.
  4) Retry once with corrected arguments.

Remote transport rules (SSE/HTTP):
  - Do NOT rely on server-side file_path resolution for client-local paths.
  - For remote files, use:
      pine_compile(file_path="label.ps", file_content="<full source>")
    or:
      pine_compile(file_content="<full source>")
  - file_path-only is local/stdio behavior and may be rejected remotely.

HIGHER-TIMEFRAME (HTF) BEST PRACTICES (PineCoders)
----------------------------------------------------
The ONLY reliable non-repainting HTF pattern in v6:
  request.security(syminfo.tickerid, "D", close[1], lookahead = barmerge.lookahead_on)
The [1] offset and lookahead=barmerge.lookahead_on are INTERDEPENDENT.
  - Without [1]: lookahead_on leaks future data on historical bars.
  - Without lookahead_on: [1] causes inconsistent timing.
DEPRECATED: barstate-based f_security() wrappers - produce inaccurate timing.
ALWAYS guard: runtime.error() if timeframe.in_seconds() >= timeframe.in_seconds(htfInput).
Tuple requests: offset ALL elements [open[1], close[1]] with lookahead_on.

LOWER-TIMEFRAME (LTF) BEST PRACTICES
-------------------------------------
Use request.security_lower_tf() - NOT request.security() for LTF data.
Returns array<type> of intrabar values. Max 100K intrabars total.
Calculate LTF: timeframe.from_seconds(timeframe.in_seconds() / divisor).
Trade-off: lower LTF = more precision per bar, but fewer bars covered.
"""
