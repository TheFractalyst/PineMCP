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
    # NOTE: Order matters! lookup_fix_hint() uses first-match-wins substring search.
    # Longer/more-specific patterns MUST appear before shorter/generic ones that
    # could match the same error text. For example, "Cannot call 'na()' with"
    # must appear before "Cannot call" or the generic hint wins incorrectly.
    #
    # ── Most-specific patterns first (namespace-qualified, multi-word) ──────
    "CE10101": "Condition of if/switch statement must evaluate to 'bool'. Use comparison operators (==, !=, >, <, >=, <=) or boolean expressions (and, or, not). v6 removed implicit bool casting.",
    "CW10003": "History-dependent function called inside conditional/loop. Move the call to global scope, store in a variable, then use that variable conditionally. The function relies on past bar data and produces incorrect results when called sporadically.",
    "RE10139": "Memory limits exceeded. Common causes: request.*() returning large collections, unbounded array growth, or excessive drawing objects. Reduce request.*() data volume, use array.shift() for fixed queues, or add max_bars_back() for large buffers.",
    "RE10143": "Historical offset exceeds the buffer limit. Add max_bars_back(varName, N) where N is the maximum history offset needed. The default buffer is determined from the first 244 bars — if first reference is later, add explicit max_bars_back().",
    "Cannot call 'na()' with": "v6 booleans cannot be na. na()/nz()/fixnan() no longer accept bool arguments. Use int (-1/0/1) or an enum for three-state logic.",
    "Cannot call 'request.security' from": "v6 with dynamic_requests=false blocks request.*() in local scopes. Remove dynamic_requests=false (defaults to true) or move request.*() to global scope.",
    "Cannot call method": "Method call on wrong type. Check the variable type with get_type(). Example: array methods require an array<type> variable.",
    "Cannot use operator '[]'": "v6 restricts history operator []. For UDT fields use (obj[n]).field syntax. Literals/constants (6[1], true[10]) are invalid. Cache value in a variable before using [].",
    "Cannot use 'var' in this context": "'var' only works for persistent variables at the bar level. Move the declaration outside of if/for/while blocks.",
    "Cannot use 'na' as": "v6 requires typed na. Use float(na), int(na), or 'var float x = na'. Bare na not allowed where a specific type is expected.",
    "Cannot use 'strategy'": "Strategy functions require //@version=6 and strategy() declaration at the top.",
    "Cannot use request.security inside": "request.security() cannot be nested inside loops or other request.security() calls. Cache the result in a variable first.",
    "Cannot convert 'series float' to 'bool'": "v6 removed implicit bool casting. Use explicit comparison: e.g., if volume > 0 instead of if volume, if close instead use if close > 0.",
    "Cannot mix 'series' and 'simple'": "Mixing series and simple/const contexts. Wrap the call in a request.security() or ensure both sides are the same qualifier.",
    "Cannot assign 'na' to": "v6 requires unique types for 'na'. Declare explicitly: 'var float x = na'. Unique type constants (plot.style_*, xloc.*) need a default branch: => plot.style_line.",
    "Cannot determine the type": "v6 cannot infer variable type. Add explicit type annotation: 'float x = close * 2' instead of 'x = close * 2'.",
    "series int' type was used but a 'simple": "v6 correctly qualifies mutable variables (modified with :=/+=) as 'series'. Pass a const/input value to parameters expecting 'simple' or 'const' types.",
    "requested historical offset": "Script references more history than the buffer allows. Add max_bars_back=5000 to indicator()/strategy(), or use max_bars_back(varName, N) for specific variables.",
    "should be called on each calculation": "History-dependent function (ta.rsi, ta.ema, etc.) called inside conditional/loop. Move the call to global scope, store in a variable, then use that variable conditionally.",
    "An argument 'when' of": "v6 removed the 'when' parameter from strategy.entry/exit. Use an if block: if condition \\n strategy.entry(...)",
    "Argument 'source' must be a 'series float'": "The source input requires a price series (close, open, etc.) or a series float variable. Check what you passed as the source argument.",
    "The 'strategy' namespace": "strategy.* functions require strategy() declaration, not indicator().",
    "The 'series' type is not supported here": "This parameter requires 'simple' or 'const' — not a dynamic series. Assign the value to a variable with 'var' outside the function call.",
    "timeframe.period": "v6 timeframe.period always includes a multiplier: '1D' not 'D', '1W' not 'W'. Use timeframe.isdaily/isweekly/ismonthly for cleaner comparisons.",
    "strategy.exit": "v6 strategy.exit() evaluates BOTH relative (profit/loss/trail_points) AND absolute (limit/stop) parameters. Remove zero-valued relative params that v5 silently ignored.",
    "Compilation request size": "Script too large for compiler. Remove unused imports (entire library compiles even if you use one function), inline logic, or split into smaller scripts.",
    "is not found in the namespace": "Wrong import alias or missing import. Check library import uses 'author/libraryName/version' format and alias matches usage.",
    "is not a named argument": "v6 requires named arguments in some functions. Use 'strategy.entry(\"Long\", direction=strategy.long)' — check the function signature.",
    "Script could not be translated": "Major syntax error. Check //@version=6 header and function declarations.",
    "Script has too many": "Script exceeds a TradingView resource limit (tokens, variables, plots, lines, etc.). Simplify: reduce plot count, remove unused variables, inline helper functions.",
    "Supported versions are >=": "Missing or wrong version declaration. First line must be exactly: '//@version=6'",
    "Please use 'var' or 'varip' to declare": "Variable reassignment without declaration. Change '=' to ':=' for reassignment, or add 'var float x = na' to declare first.",
    "Nested functions are not": "PineScript does not support nested function definitions. Move helper logic to the global scope or use a method on a user-defined type.",
    "The signature of": "Wrong number or type of arguments passed to a function. Use get_function(name) to check the exact parameter list and types.",
    "Syntax error at input": "Check function syntax — v6 uses '=>' for inline functions. Verify commas between parameters and correct indentation.",
    "No overload of function": "Wrong number or types of arguments. Call get_function(name) for exact parameter list and types.",
    "Function must return a result": "All branches of if/switch must return a value. Add an else clause.",
    "Function must return a value": "All code paths in a function must return a value. Add a final 'else =>' or default return at the end.",
    "Invalid test for": "Cannot test na in bool context (e.g., 'if pivot' where pivot can be na). Use 'if not na(pivot)' instead. Booleans strictly true/false in v6.",
    "Condition must be 'bool'": "If/while condition must be boolean. Use comparison operators: ==, !=, >, <, >=, <=, 'and', 'or', 'not'.",
    #
    # ── v6 breaking changes (qualified patterns) ───────────────────────────
    "no longer accepts 'bool'": "v6 tightened type requirements — this parameter no longer accepts 'bool' where it once did. Pass the expected type explicitly.",
    "Duplicate argument": "v6 disallows duplicate named arguments in function calls. Remove the duplicate parameter — only one of each name is allowed.",
    "closedtrades": "v6 trims oldest trades past the 9000 limit. Use strategy.closedtrades.first_index as the starting index when looping — trimmed trades return na.",
    "division operator": "v6 changed integer division: 3/2 now returns 1.5 (float), not 1. Use math.floor(a/b) or int(a/b) for integer division.",
    #
    # ── Shorter generic patterns LAST (after all specific patterns above) ───
    "Undeclared identifier": "Variable not declared. Add 'var float {name} = na' before use, or check spelling. In v6, all identifiers must be declared.",
    "Cannot call": "Wrong argument type or count. Check parameter types with get_function().",
    "Cannot cast": "Type mismatch. PineScript is strongly typed — use explicit type conversions.",
    "Casting is not possible": "Incompatible types. Use explicit conversion: int(x), float(x), str.tostring(x), or str.tonumber(x).",
    "Cannot use": "Check PineScript v6 syntax for this construct. Use get_function() or search_docs() for guidance.",
    "An argument of type": "Wrong type passed to function. Check the function signature with get_function().",
    "Mismatched types": "Type mismatch between expected and actual. Check function parameter types — v6 is stricter than v5. Use explicit type conversions: float(), int(), str.tostring().",
    "Mismatched input": "Syntax error — check for missing commas, parentheses, or brackets.",
    "Variable is undefined": "Declare the variable before use with := for reassignment or = for initial assignment.",
    "Variable is not found": "Typo or undeclared variable. PineScript is case-sensitive. Check spelling and ensure the variable is declared before use in the script.",
    "Reserved keyword": "PineScript reserves words like 'strategy', 'plot', 'if'. Rename variable: 'strategy = 1' → 'myStrategy = 1'.",
    "Recursive call": "PineScript does not support direct recursion. Use a var variable or request.security().",
    "Add to chart is not allowed": "Use plot(), plotshape(), or another visual output function.",
    "Loop is too long": "Pine limits loop body size. Extract logic into a function: 'f(x) => ...body...' and call f() inside the loop.",
    "Loop body is too long": "Pine limits loop body size. Extract logic into a function: 'f(x) => ...body...' and call f() inside the loop.",
    "Loop took too long": "Loop exceeded the 500ms per-bar timeout. Reduce iteration count, optimize loop body, or precompute outside the loop.",
    "Series is not allowed": "This context requires simple/const type, not series. Use ta.valuewhen() or barstate lookups.",
    "memory limit": "Exceeded Pine's memory limits. Reduce drawing count, use smaller arrays (max 100,000 elements), or reduce request.*() data volume.",
    "cannot add string and": "PineScript does not auto-convert numbers to strings. Use str.tostring(value): 'Price: ' + str.tostring(close).",
    "lookahead": "request.security() with lookahead=barmerge.lookahead_on without [1] offset peeks into future (repainting). With [1] offset it IS the correct non-repainting HTF pattern (PineCoders). Use lookahead_off only when no expression offset is needed.",
    "repainting": "Signal uses future data or unconfirmed bar values. Guard with barstate.isconfirmed. Avoid lookahead=barmerge.lookahead_on.",
    # ── v5→v6 parameter removals (short but specific context) ───────────────
    "transp": "v6 removed the 'transp' parameter from plot(), fill(), bgcolor(), etc. Use color.new(color, transparency) instead, where transparency is 0 (opaque) to 100 (invisible).",
    "offset": "v6 changed 'offset' parameter: it no longer accepts 'series int', only 'simple int'. Calculate the offset outside the call and pass the result.",
    "linewidth": "v6 enforces minimum linewidth of 1. Use linewidth=1 or higher. Zero or negative values are no longer accepted.",
    "margin": "v6 changed default margin from 0 to 100% (no margin trading). Set margin_long=0 and margin_short=0 in strategy() to restore margin behavior.",
    # ── Runtime errors ──────────────────────────────────────────────────────
    "Too many drawings": "Drawing objects exceed the limit. Set max_lines_count=500, max_labels_count=500, or max_boxes_count=500 in your declaration.",
    "too many local variables": "Each scope has a 1000-variable limit. Inline expressions to reduce count, or extract into helper functions.",
    "too many securities": "Pine limits to 40 request.security() calls. Combine calls using tuples, or wrap in a UDF and reuse the result.",
    # ── v6 API patterns (from PineCoders published scripts) ──────────────────
    "Cannot use 'import'": "v6 import syntax: use 'import author/libraryName/version as alias'. The alias is then used as prefix: alias.function(). Check the library exists and version number is correct.",
    "method already defined": "v6 disallows duplicate method definitions for the same type. Each type can only have one method with a given name. Rename or remove the duplicate.",
    "Library script must have": "v6 library scripts require //@version=6 and library() declaration at the top. The library() call replaces indicator()/strategy(). Use export to expose functions.",
    "order of elements in tuple": "v6 enforces strict tuple element ordering. Elements in the assignment must match the expression order exactly. Check that [a, b, c] = matches the order of [expr1, expr2, expr3].",
    "runtime.error": "v6 runtime.error() raises a visible error on the chart. Use it to validate inputs: `if val == 0\n    runtime.error(\"msg\")`. This prevents silent failures.",
    "chart.left_visible_bar_time": "v6 chart.left/right_visible_bar_time triggers full recalculation on every scroll/zoom. Keep calculations lightweight. Create drawings with `var` and update with setters on `barstate.islast`. Use visibility guards to restrict heavy calculations. PineCoders VisibleChart pattern.",
    "timeframe.from_seconds": "v6 timeframe.from_seconds() converts a seconds count to a timeframe string. Use with timeframe.in_seconds() for dynamic TF construction: `timeframe.from_seconds(timeframe.in_seconds() * mult)`.",
    "force_overlay": "v6 force_overlay=true parameter on label.new()/bgcolor() allows pane indicators to draw on the main chart. Without it, drawings stay in the indicator pane when overlay=false.",
    "display.data_window": "v6 display parameter controls plot visibility. Use `display = display.data_window` to keep values accessible without chart rendering. Combine with `display.status_line` for status line + data window.",
    "math.avg": "v6 math.avg() calculates the average of multiple arguments. Use `math.avg(a, b)` instead of `(a + b) / 2` for cleaner midpoint calculations.",
    "array.from": "v6 array.from() creates and populates an array in one statement. Use `array.from(val1, val2, val3)` instead of multiple `array.push()` calls.",
    "color.from_gradient": "v6 color.from_gradient() interpolates between two colors based on a value. Use it instead of manual transparency calculations with color.new().",
    "str.format_time": "v6 str.format_time() formats UNIX timestamps. Use `str.format_time(time, \"dd/MM/yy '@' HH:mm:ss\")` instead of manual time formatting.",
    "barstate.isnew": "v6 barstate.isnew is true at bar close on historical data but at bar open in realtime. Use barstate.isconfirmed for consistent signal generation. Use barstate.isnew ONLY to reset varip variables at bar boundaries — NOT for signals (repaints).",
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


def _has_unclosed_fence(text: str) -> bool:
    """Track code fence open/close state line-by-line.

    Correctly handles ``` appearing inside code blocks (e.g. markdown
    examples) by tracking whether we're inside a fence or not.
    """
    in_fence = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                # Closing fence: bare ``` or ``` followed by whitespace
                if stripped == "```" or (len(stripped) > 3 and stripped[3:4] == " "):
                    in_fence = False
            else:
                # Opening fence: ```lang or bare ```
                in_fence = True
    return in_fence


def cap_response(text: str, limit: int = MAX_TOOL_RESPONSE_CHARS) -> str:
    """Cap tool response size to avoid overwhelming AI context windows."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    # Close any unclosed markdown code fences using line-by-line tracking.
    if _has_unclosed_fence(truncated):
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

def strip_string_literals(code: str) -> str:
    """Replace string literal contents with spaces (preserving length for offsets).

    Handles both "..." and '...' strings with backslash escapes.
    Used to gate regex transformations so they don't match inside strings.
    """
    def _replacer(m: re.Match) -> str:
        s = m.group(0)
        # Preserve quotes, replace inner chars with spaces
        return s[0] + " " * (len(s) - 2) + s[-1] if len(s) >= 2 else s

    # Match double-quoted strings (with escaped chars)
    result = re.sub(r'"(?:[^"\\]|\\.)*"', _replacer, code)
    # Match single-quoted strings (with escaped chars)
    result = re.sub(r"'(?:[^'\\]|\\.)*'", _replacer, result)
    return result


def norm_name(name: str) -> str:
    """Normalize entry name: strip whitespace and trailing parens."""
    return name.strip().rstrip("()")


def norm_ns(ns: str) -> str:
    """Normalize namespace: strip, lowercase, remove trailing dot."""
    return ns.strip().lower().rstrip(".")
