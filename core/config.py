"""
core/config.py
──────────────────────────────────────────────────────────────────────────────
All configuration constants, environment variables, and server instructions.
"""

from __future__ import annotations

import os

# ─────────────────────────────────────────────────────────────────────────────
# Runtime configuration (env-var overridable)
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = os.getenv(
    "PINESCRIPT_DB_PATH",
    str(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pinescript_db")),
)
COLLECTION = os.getenv("PINESCRIPT_COLLECTION", "pinescript_v6")
EMBED_MODEL = os.getenv("PINESCRIPT_EMBED_MODEL", "all-MiniLM-L6-v2")
MAX_RESULTS = int(os.getenv("PINESCRIPT_MAX_RESULTS", "30"))
PINE_FACADE_URL = os.getenv(
    "PINE_FACADE_URL",
    "https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=admin&v=3",
)
PINE_FACADE_TIMEOUT = int(os.getenv("PINE_FACADE_TIMEOUT", "20"))
VALIDATION_CACHE_TTL = int(os.getenv("VALIDATION_CACHE_TTL", "300"))
VALIDATION_CACHE_MAX_SIZE = int(os.getenv("VALIDATION_CACHE_SIZE", "500"))
MAX_TOOL_RESPONSE_CHARS = 8000
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
via a ChromaDB vector store with 3,400+ entries covering all functions,
variables, types, constants, keywords, operators, and user guides.

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

IMPORTANT NOTES
───────────────
- All code examples returned are real, working PineScript from the official
  TradingView documentation.
- PineScript is executed on every bar, so variable semantics differ from
  general-purpose languages.
- Use the `var` keyword for variables that should preserve state across bars.
- Strategy scripts require //@version=6 and strategy() declaration.
- Indicator scripts require //@version=6 and indicator() declaration.
"""
