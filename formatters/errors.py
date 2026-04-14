# PineScript-v6 MCP | © 2025-2026 @Fractalyst
# ruff: noqa: E501
"""
formatters/errors.py
──────────────────────────────────────────────────────────────────────────────
Error formatting, fix hints, sanitization utilities, and response capping.
Pure functions — no shared state, safe to test in isolation.
"""

from __future__ import annotations

import re
from typing import Optional

from loguru import logger

from core.config import MAX_TOOL_RESPONSE_CHARS

# ─────────────────────────────────────────────────────────────────────────────
# Fix hints — pattern → user-friendly fix description
# ─────────────────────────────────────────────────────────────────────────────

_FIX_HINTS: dict[str, str] = {
    "Undeclared identifier": "Variable not declared. Add 'var float {name} = na' before use, or check spelling. In v6, all identifiers must be declared.",
    "Cannot call": "Wrong argument type or count. Check parameter types with get_function().",
    "Cannot cast": "Type mismatch. PineScript is strongly typed — use explicit type conversions.",
    "Add to chart is not allowed": "Use plot(), plotshape(), or another visual output function.",
    "Loop is too long": "Pine limits loop body size. Extract logic into a function: 'f(x) => ...body...' and call f() inside the loop.",
    "Function must return a result": "All branches of if/switch must return a value. Add an else clause.",
    "Series is not allowed": "This context requires simple/const type, not series. Use ta.valuewhen() or barstate lookups.",
    "Variable is undefined": "Declare the variable before use with := for reassignment or = for initial assignment.",
    "Mismatched input": "Syntax error — check for missing commas, parentheses, or brackets.",
    "An argument of type": "Wrong type passed to function. Check the function signature with get_function().",
    "The 'strategy' namespace": "strategy.* functions require strategy() declaration, not indicator().",
    "Script could not be translated": "Major syntax error. Check //@version=6 header and function declarations.",
    "Cannot use 'strategy'": "Strategy functions require //@version=6 and strategy() declaration at the top.",
    "Recursive call": "PineScript does not support direct recursion. Use a var variable or request.security().",
    "Cannot call method": "Method call on wrong type. Check the variable type with get_type(). Example: array methods require an array<type> variable.",
    "Loop body is too long": "Pine limits loop body size. Extract logic into a function: 'f(x) => ...body...' and call f() inside the loop.",
    "The 'series' type is not supported here": "This parameter requires 'simple' or 'const' — not a dynamic series. Assign the value to a variable with 'var' outside the function call.",
    "Casting is not possible": "Incompatible types. Use explicit conversion: int(x), float(x), str.tostring(x), or str.tonumber(x).",
    "Cannot use 'var' in this context": "'var' only works for persistent variables at the bar level. Move the declaration outside of if/for/while blocks.",
    "Function must return a value": "All code paths in a function must return a value. Add a final 'else =>' or default return at the end.",
    "Argument 'source' must be a 'series float'": "The source input requires a price series (close, open, etc.) or a series float variable. Check what you passed as the source argument.",
    "Cannot use request.security inside": "request.security() cannot be nested inside loops or other request.security() calls. Cache the result in a variable first.",
    "Supported versions are >=": "Missing or wrong version declaration. First line must be exactly: '//@version=6'",
    "Please use 'var' or 'varip' to declare": "Variable reassignment without declaration. Change '=' to ':=' for reassignment, or add 'var float x = na' to declare first.",
    "Condition must be 'bool'": "If/while condition must be boolean. Use comparison operators: ==, !=, >, <, >=, <=, 'and', 'or', 'not'.",
    "Cannot mix 'series' and 'simple'": "Mixing series and simple/const contexts. Wrap the call in a request.security() or ensure both sides are the same qualifier.",
    "No overload of function": "Wrong number or types of arguments. Call get_function(name) for exact parameter list and types.",
    "Cannot convert 'series float' to 'bool'": "v6 removed implicit bool casting. Use explicit comparison: e.g., if volume > 0 instead of if volume, if close instead use if close > 0.",
    "An argument 'when' of": "v6 removed the 'when' parameter from strategy.entry/exit. Use an if block: if condition \\n strategy.entry(...)",
    "division operator": "v6 changed integer division: 3/2 now returns 1.5 (float), not 1. Use math.floor(a/b) or int(a/b) for integer division.",
    # ── v6 breaking changes ──
    "transp": "v6 removed the 'transp' parameter from plot(), fill(), bgcolor(), etc. Use color.new(color, transparency) instead, where transparency is 0 (opaque) to 100 (invisible).",
    "Duplicate argument": "v6 disallows duplicate named arguments in function calls. Remove the duplicate parameter — only one of each name is allowed.",
    "Cannot use operator '[]'": "v6 restricts history operator []. For UDT fields use (obj[n]).field syntax. Literals/constants (6[1], true[10]) are invalid. Cache value in a variable before using [].",
    "no longer accepts 'bool'": "v6 tightened type requirements — this parameter no longer accepts 'bool' where it once did. Pass the expected type explicitly.",
    "Cannot assign 'na' to": "v6 requires unique types for 'na'. Declare explicitly: 'var float x = na'. Unique type constants (plot.style_*, xloc.*) need a default branch: => plot.style_line.",
    "offset": "v6 changed 'offset' parameter: it no longer accepts 'series int', only 'simple int'. Calculate the offset outside the call and pass the result.",
    "linewidth": "v6 enforces minimum linewidth of 1. Use linewidth=1 or higher. Zero or negative values are no longer accepted.",
    "margin": "v6 changed default margin from 0 to 100% (no margin trading). Set margin_long=0 and margin_short=0 in strategy() to restore margin behavior.",
    # ── v6 edge cases ──
    "Cannot call 'na()' with": "v6 booleans cannot be na. na()/nz()/fixnan() no longer accept bool arguments. Use int (-1/0/1) or an enum for three-state logic.",
    "Cannot call 'request.security' from": "v6 with dynamic_requests=false blocks request.*() in local scopes. Remove dynamic_requests=false (defaults to true) or move request.*() to global scope.",
    "series int' type was used but a 'simple": "v6 correctly qualifies mutable variables (modified with :=/+=) as 'series'. Pass a const/input value to parameters expecting 'simple' or 'const' types.",
    "closedtrades": "v6 trims oldest trades past the 9000 limit. Use strategy.closedtrades.first_index as the starting index when looping — trimmed trades return na.",
    "strategy.exit": "v6 strategy.exit() evaluates BOTH relative (profit/loss/trail_points) AND absolute (limit/stop) parameters. Remove zero-valued relative params that v5 silently ignored.",
    "timeframe.period": "v6 timeframe.period always includes a multiplier: '1D' not 'D', '1W' not 'W'. Use timeframe.isdaily/isweekly/ismonthly for cleaner comparisons.",
    # ── Runtime errors ──
    "requested historical offset": "Script references more history than the buffer allows. Add max_bars_back=5000 to indicator()/strategy(), or use max_bars_back(varName, N) for specific variables.",
    "Too many drawings": "Drawing objects exceed the limit. Set max_lines_count=500, max_labels_count=500, or max_boxes_count=500 in your declaration.",
    "too many local variables": "Each scope has a 1000-variable limit. Inline expressions to reduce count, or extract into helper functions.",
    "too many securities": "Pine limits to 40 request.security() calls. Combine calls using tuples, or wrap in a UDF and reuse the result.",
    "Loop took too long": "Loop exceeded the 500ms per-bar timeout. Reduce iteration count, optimize loop body, or precompute outside the loop.",
    "memory limit": "Exceeded Pine's memory limits. Reduce drawing count, use smaller arrays (max 100,000 elements), or reduce request.*() data volume.",
    # ── v6 compilation errors ──
    "Syntax error at input": "Check function syntax — v6 uses '=>' for inline functions. Verify commas between parameters and correct indentation.",
    "should be called on each calculation": "History-dependent function (ta.rsi, ta.ema, etc.) called inside conditional/loop. Move the call to global scope, store in a variable, then use that variable conditionally.",
    "Cannot use 'na' as": "v6 requires typed na. Use float(na), int(na), or 'var float x = na'. Bare na not allowed where a specific type is expected.",
    "cannot add string and": "PineScript does not auto-convert numbers to strings. Use str.tostring(value): 'Price: ' + str.tostring(close).",
    "Compilation request size": "Script too large for compiler. Remove unused imports (entire library compiles even if you use one function), inline logic, or split into smaller scripts.",
    "is not found in the namespace": "Wrong import alias or missing import. Check library import uses 'author/libraryName/version' format and alias matches usage.",
    "Invalid test for": "Cannot test na in bool context (e.g., 'if pivot' where pivot can be na). Use 'if not na(pivot)' instead. Booleans strictly true/false in v6.",
    # ── General common errors ──
    "Reserved keyword": "PineScript reserves words like 'strategy', 'plot', 'if'. Rename variable: 'strategy = 1' → 'myStrategy = 1'.",
    "lookahead": "request.security() with lookahead=barmerge.lookahead_on peeks into future (repainting). Use barmerge.lookahead_off (v6 default) for honest backtests.",
    "repainting": "Signal uses future data or unconfirmed bar values. Guard with barstate.isconfirmed. Avoid lookahead=barmerge.lookahead_on.",
}


def lookup_fix_hint(error_text: str) -> str:
    """Match an error message against known patterns and return a fix hint."""
    for pattern, hint in _FIX_HINTS.items():
        if pattern.lower() in error_text.lower():
            # Resolve {name} placeholder in hint using identifier from error text
            name_match = re.search(r"'([a-zA-Z_][a-zA-Z0-9_.]*)'", error_text)
            if name_match:
                hint = hint.replace("{name}", name_match.group(1))
            else:
                hint = hint.replace("{name}", "value")
            return hint
    return "Check the PineScript v6 reference for the correct syntax."


def extract_name_from_error(error_text: str) -> Optional[str]:
    """Extract a likely PineScript name from a compiler error message."""
    # Pattern: "Undeclared identifier 'ta.supertrend'" → ta.supertrend
    m = re.search(r"'([a-zA-Z_][a-zA-Z0-9_.]*)'", error_text)
    if m:
        return m.group(1)
    # Pattern: "Cannot call 'ta.ema'" → ta.ema
    m = re.search(r"call\s+['\"]?([a-zA-Z_][a-zA-Z0-9_.]*)", error_text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Pattern: "An argument of type 'series float'..." → look for function name
    m = re.search(
        r"function\s+['\"]?([a-zA-Z_][a-zA-Z0-9_.]*)", error_text, re.IGNORECASE
    )
    if m:
        return m.group(1)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Response utilities
# ─────────────────────────────────────────────────────────────────────────────

_PATH_PATTERN = re.compile(r"(/[\w./\-]+|[A-Z]:\\[\\\w.\\-]+)")


def safe_error(exc: Exception, context: str = "") -> str:
    """Return a user-safe error string — removes paths, caps length."""
    msg = str(exc)
    msg = _PATH_PATTERN.sub("[path]", msg)
    if len(msg) > 200:
        msg = msg[:200] + "..."
    prefix = f"[{context}] " if context else ""
    return f"{prefix}{type(exc).__name__}: {msg}"


def cap_response(text: str, limit: int = MAX_TOOL_RESPONSE_CHARS) -> str:
    """Cap tool response size to avoid overwhelming AI context windows."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    # Close any unclosed markdown code fences.
    # Count opening fences (lines starting with ``` followed by optional lang tag)
    # vs closing fences (bare ``` on their own line or at end).
    # Simple heuristic: count all ``` occurrences — odd count means unclosed.
    fence_count = truncated.count("```")
    if fence_count % 2 != 0:
        # Find the last opening fence (``` followed by a lang tag or at line start)
        # and truncate there to remove the incomplete block entirely
        last_fence_pos = truncated.rfind("```")
        # Check if this is an opening fence (not a closing one)
        after = truncated[last_fence_pos + 3:].lstrip()
        if after and not after.startswith("\n") and not after == "":
            # Opening fence with content — truncate before it
            truncated = truncated[:last_fence_pos].rstrip()
        else:
            # Likely a closing fence — just add the missing one
            truncated += "\n```"
    omitted = len(text) - len(truncated)
    return truncated + f"\n\n[...truncated — {omitted:,} chars omitted]"


def sanitize_text(text: str) -> str:
    """Remove null bytes and non-printable control characters."""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text.strip()


def sanitize_pine_string(s: str) -> str:
    """Make a string safe for embedding in PineScript string literals."""
    s = s.replace('"', "'")
    s = s.replace("\\", "/")
    s = re.sub(r"[\x00-\x1f]", "", s)
    s = s.strip()
    if not s:
        return "Script"
    return s[:100]


def circuit_breaker_msg() -> str:
    return (
        "DATABASE UNAVAILABLE\n"
        "The ChromaDB vector store has encountered repeated failures.\n"
        "To resolve:\n"
        "  1. Ensure pinescript_db/ exists next to pinescript_mcp.py\n"
        "  2. Run: python merge_and_index.py\n"
        "  3. Restart the MCP server"
    )


def check_query_error(results: dict) -> str | None:
    """Check if a query result indicates a database failure."""
    if "_error" in results:
        raw = str(results["_error"])
        # Sanitize: strip paths and cap length to avoid leaking hostnames/ports
        sanitized = _PATH_PATTERN.sub("[path]", raw)
        if len(sanitized) > 150:
            sanitized = sanitized[:150] + "..."
        return (
            "DATABASE UNAVAILABLE\n"
            "The ChromaDB vector store could not process this query.\n"
            "This is a transient error — please retry in a few seconds.\n"
            f"Detail: {sanitized}"
        )
    return None


def error(tool: str, msg: str) -> str:
    logger.error(f"[{tool}] {msg}")
    return f"ERROR [{tool}]: {msg}"


# ─────────────────────────────────────────────────────────────────────────────
# Name normalization helpers
# ─────────────────────────────────────────────────────────────────────────────

def norm_name(name: str) -> str:
    """Normalize entry name: strip whitespace and trailing parens."""
    return name.strip().rstrip("()")


def norm_ns(ns: str) -> str:
    """Normalize namespace: strip, lowercase, remove trailing dot."""
    return ns.strip().lower().rstrip(".")
