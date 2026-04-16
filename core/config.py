# PineScript-v6 MCP | © 2025-2026 @Fractalyst
"""
core/config.py
──────────────────────────────────────────────────────────────────────────────
All configuration constants, environment variables, and server instructions.
"""

from __future__ import annotations

import os

SERVER_VERSION = "4.0"


def _safe_int(env_var: str, default: int, min_val: int = 0) -> int:
    """Parse env var as int, returning default on invalid or negative values."""
    try:
        val = int(os.getenv(env_var, str(default)))
        return val if val >= min_val else default
    except (ValueError, TypeError):
        return default


# ─────────────────────────────────────────────────────────────────────────────
# Runtime configuration (env-var overridable)
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = os.getenv(
    "PINESCRIPT_DB_PATH",
    str(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pinescript_db")),
)
COLLECTION = os.getenv("PINESCRIPT_COLLECTION", "pinescript_v6")
EMBED_MODEL = os.getenv("PINESCRIPT_EMBED_MODEL", "all-MiniLM-L6-v2")
EMBED_DIM = 384  # all-MiniLM-L6-v2 output dimension
MAX_RESULTS = _safe_int("PINESCRIPT_MAX_RESULTS", 100)
PINE_FACADE_URL = os.getenv(
    "PINE_FACADE_URL",
    "https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=admin&v=3",
)
PINE_FACADE_TIMEOUT = _safe_int("PINE_FACADE_TIMEOUT", 20)
VALIDATION_CACHE_TTL = _safe_int("VALIDATION_CACHE_TTL", 300)
VALIDATION_CACHE_MAX_SIZE = _safe_int("VALIDATION_CACHE_SIZE", 500)
MAX_TOOL_RESPONSE_CHARS = 80000
MAX_FUZZY_SCAN_ENTRIES = 5000

# Pre-computed at import time (not inside per-call hot path)
_ALLOWED_BASE_DIRS = [
    os.path.realpath(os.path.expanduser("~/Documents")),
    os.path.realpath(os.path.expanduser("~/Desktop")),
    os.path.realpath(os.path.expanduser("~/Projects")),
    os.path.realpath(os.path.expanduser("~/repos")),
    os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
]

# ─────────────────────────────────────────────────────────────────────────────
# Server instructions (shown to AI agents connecting to this MCP server)
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCTIONS = """\
You are connected to the complete PineScript v6 reference documentation server.

ABOUT PINESCRIPT v6
───────────────────
PineScript is TradingView's domain-specific language for creating custom
technical indicators, strategies, and libraries that run on the TradingView
platform. Version 6 (v6) is the current production release and introduces
UDTs (user-defined types), methods, enums, polylines, and improved performance.

This server provides complete local PineScript v6 reference documentation
via a ChromaDB vector store with 7,400+ entries covering all functions,
variables, types, constants, keywords, operators, and user guides.

MANDATORY USAGE PATTERN (NON-NEGOTIABLE)
─────────────────────────────────────────
When working on ANY PineScript file (.ps, .pine), you MUST consult this
server before writing code. Do NOT rely on training data or assumptions.

BEFORE writing a function call or using a built-in:
  1. get_function(name) — get exact parameter types, order, return type, and examples
  2. If unsure of the name: suggest_functions(description) or search_docs(query)

BEFORE using a variable, constant, or type:
  1. get_variable(name) or get_constant(name) — verify it exists and check behavior
  2. get_type(name) — for UDTs, arrays, matrices, maps — get all fields/methods

BEFORE using a namespace you haven't memorized:
  1. get_namespace_cheatsheet(namespace) — quick scan of all members
  2. list_namespace(namespace) — full list with descriptions

AFTER every edit to a .ps/.pine file:
  1. validate_syntax(code) or validate_file(file_path) — confirm it compiles
  2. If errors: validate_and_explain(code) — get diagnostics + doc-referenced fixes

WHEN TO USE EACH TOOL
──────────────────────
LOOKUP TOOLS (use for specific names you know):
  get_function(name)       Full docs for a function: syntax, params, examples
  get_variable(name)       Built-in variable description and behavior
  get_type(name)           Type definition, fields, methods
  get_constant(name)       Constant value and usage
  get_keyword(name)        Keyword syntax and examples
  get_operator(name)       Operator description and examples

SEARCH TOOLS (use when you don't know exact name):
  search_docs(query)               Semantic search across everything
  get_examples(concept)            Find real working code by concept
  search_by_return_type(type)      Find functions returning a type
  list_namespace(namespace)        All members of a namespace
  suggest_functions(context)       Find the right function for a task
  get_namespace_cheatsheet(ns)     Compact reference for a namespace

VALIDATION TOOLS (use after every edit):
  validate_syntax(code)            Compile check via TradingView's pine-facade
  validate_and_explain(code)       Compile + cross-reference errors against docs
  validate_file(file_path)         Validate by file path (for large files)
  fix_and_validate(code, error)    Auto-fix when you have a specific compiler error.
                                   Applies targeted v6 namespace + syntax fixes.
  debug_pine_facade(code)          Raw compiler response for debugging

CODEGEN TOOLS (use for scaffolding):
  generate_indicator(name, ...)    Scaffold a validated indicator
  generate_strategy(name, ...)     Scaffold a validated strategy
  lookup_and_correct(code, desc)   Validate + v5→v6 migration when you know what
                                   the code should do but don't have an error msg.

OPTIMIZATION TOOLS (use for performance analysis):
  optimize_code(code)             Detect 87 static-analysis rules (OPT-001 to OPT-090)
                                   covering all Pine Profiler optimization techniques:
                                   built-in usage, repetition reduction, request consolidation,
                                   drawing lifecycle, value storage, loop elimination,
                                   buffer management, platform limits, repainting prevention,
                                   visible chart optimization, varip lifecycle, dynamic-length
                                   buffers, string optimization, xloc correctness, and code quality.
                                   Returns severity-rated findings with
                                   fix suggestions and doc lookup queries.

This tool is OPT-IN ONLY. It does NOT run automatically on validation or codegen calls.
Call it explicitly when you want to check PineScript code for performance issues.

IMPORTANT NOTES
───────────────
- All code examples returned are real, working PineScript from the official
  TradingView documentation.
- PineScript is executed on every bar, so variable semantics differ from
  general-purpose languages.
- Use the `var` keyword for variables that should preserve state across bars.
- Strategy scripts require //@version=6 and strategy() declaration.
- Indicator scripts require //@version=6 and indicator() declaration.
- NEVER guess parameter types or function signatures — always verify with get_function().
- TradingView ships 60+ ta.* functions, 40+ math.* functions, and extensive
  request.*/str.*/array.*/matrix.*/map.* utilities. Check before writing custom logic.
"""
