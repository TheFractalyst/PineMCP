# PineScript-v6 MCP | © 2025-2026 @Fractalyst
# ruff: noqa: E501
"""
core/optimizer.py
──────────────────────────────────────────────────────────────────────────────
Static analysis engine for PineScript v6 performance optimization.

80 detection rules (OPT-001 through OPT-083; OPT-019/024/025 are runtime-only).
All rules use regex-based detection (fast, deterministic, <50ms per analysis).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from loguru import logger

# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OptimizationResult:
    """Single optimization finding from static analysis."""
    rule_id: str
    name: str
    severity: str          # critical / high / medium / low
    line: int              # 1-based line number (0 = whole-file)
    snippet: str           # The problematic code line(s)
    suggestion: str        # What to do instead
    doc_query: str         # ChromaDB search query for detailed fix
    category: str          # For grouping in output


@dataclass
class _Rule:
    """Internal representation of a detection rule."""
    rule_id: str
    name: str
    severity: str
    category: str
    detect: Callable[[str, list[str]], list[OptimizationResult]]
    doc_query: str


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _find_lines(lines: list[str], pattern: re.Pattern[str]) -> list[int]:
    """Return 1-based line numbers where pattern matches."""
    return [i + 1 for i, line in enumerate(lines) if pattern.search(line)]


def _strip_comments(line: str) -> str:
    """Remove // comments from a line, preserving // inside quoted strings."""
    in_dquote = False
    in_squote = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '\\' and (in_dquote or in_squote) and i + 1 < len(line):
            i += 2  # skip escaped char inside string
            continue
        if ch == '"' and not in_squote:
            in_dquote = not in_dquote
        elif ch == "'" and not in_dquote:
            in_squote = not in_squote
        elif ch == '/' and not in_dquote and not in_squote and i + 1 < len(line) and line[i + 1] == '/':
            return line[:i]
        i += 1
    return line


def _code_has_keyword(code: str, keyword: str) -> bool:
    """Check if keyword appears in code as a whole word, ignoring comments.

    Uses word-boundary matching to avoid false positives:
    ``var`` won't match ``varip``, ``plot`` won't match ``my_plot``.
    Only applies ``\\b`` where the keyword starts/ends with word chars,
    so keywords like ``strategy(`` still match correctly.
    """
    prefix = r"\b" if keyword[0].isalnum() or keyword[0] == "_" else ""
    suffix = r"\b" if keyword[-1].isalnum() or keyword[-1] == "_" else ""
    pattern = re.compile(rf"{prefix}{re.escape(keyword)}{suffix}")
    for line in code.splitlines():
        if pattern.search(_strip_comments(line)):
            return True
    return False


def _count_in_scope(code: str, pattern: re.Pattern[str]) -> int:
    """Count non-comment matches across entire code."""
    count = 0
    for line in code.splitlines():
        stripped = _strip_comments(line)
        if stripped.strip():
            count += len(pattern.findall(stripped))
    return count


def _result(rule: _Rule, line: int, snippet: str, suggestion: str) -> OptimizationResult:
    return OptimizationResult(
        rule_id=rule.rule_id,
        name=rule.name,
        severity=rule.severity,
        line=line,
        snippet=snippet.strip(),
        suggestion=suggestion,
        doc_query=rule.doc_query,
        category=rule.category,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Detection rules — 44 anti-patterns
# ─────────────────────────────────────────────────────────────────────────────

def _detect_reimplemented_builtins(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-001: Reimplementing built-in functions (ta.highest/lowest/sma/etc) with loops."""
    results: list[OptimizationResult] = []
    # Look for functions that iterate source[i] in a for loop and accumulate
    # This catches patterns like: for i = 1 to length - 1 / result := math.max(result, source[i])
    func_pattern = re.compile(r"for\s+\w+\s*=\s*\d+\s+to\s+\w+")
    accum_pattern = re.compile(r"(math\.(max|min)|\+=).*(?:source|close|open|high|low)\[")
    in_func = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.match(r"\w+\(", stripped) and "=>" in stripped:
            in_func = True
        if in_func and func_pattern.search(stripped):
            # Check if loop body accumulates with history references
            body_lines = lines[i:min(i + 10, len(lines))]
            body_text = " ".join(_strip_comments(ln) for ln in body_lines)
            if accum_pattern.search(body_text):
                results.append(_result(
                    _RULES_BY_ID["OPT-001"], i + 1,
                    stripped,
                    "Use built-in ta.highest(), ta.lowest(), ta.sma(), etc. instead of manual loops. "
                    "Built-ins have internal optimizations (O(1) vs O(n))."
                ))
                break
            in_func = False
    return results


def _detect_repeated_calls(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-002: Repeating identical function calls across multiple lines."""
    results: list[OptimizationResult] = []
    call_counts: dict[str, list[int]] = {}
    call_pattern = re.compile(r"([\w.]+)\s*\(([^)]*)\)")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if not stripped:
            continue
        for m in call_pattern.finditer(stripped):
            key = f"{m.group(1)}({m.group(2).strip()})"
            if len(key) > 200:
                continue  # Skip very long expressions
            call_counts.setdefault(key, []).append(i + 1)

    for key, line_nums in call_counts.items():
        if len(line_nums) >= 3:
            # Heuristic: 3+ identical calls is a pattern
            results.append(_result(
                _RULES_BY_ID["OPT-002"], line_nums[0],
                key[:100],
                f"Same call repeated {len(line_nums)} times. Store in a variable and reuse: "
                f"`val = {key.split('(')[0]}(...)`, then use `val` on each line."
            ))
            break  # Report once per file
    return results


def _detect_multiple_request_security(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-003: Multiple request.security() calls to the same context."""
    results: list[OptimizationResult] = []
    req_pattern = re.compile(r"request\.security\s*\(\s*([^,]+)\s*,\s*([^,]+)\s*,")
    context_calls: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        for m in req_pattern.finditer(stripped):
            ctx = f"{m.group(1).strip()}, {m.group(2).strip()}"
            context_calls.setdefault(ctx, []).append(i + 1)

    for ctx, line_nums in context_calls.items():
        if len(line_nums) >= 2:
            snippet = lines[line_nums[0] - 1].strip()[:100]
            results.append(_result(
                _RULES_BY_ID["OPT-003"], line_nums[0],
                snippet,
                f"Consolidate {len(line_nums)} request.security() calls to the same context "
                f"into a single tuple request: `[v1, v2, ...] = request.security({ctx}, [expr1, expr2, ...])`"
            ))
    return results


def _detect_delete_recreate(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-004: Deleting and recreating drawings instead of using setters."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"\b(box|line|label)\.delete\s*\(", stripped):
            # Check next few lines for .new() of same type
            for j in range(i + 1, min(i + 5, len(lines))):
                next_stripped = _strip_comments(lines[j]).strip()
                if re.search(r"\b(box|line|label)\.new\s*\(", next_stripped):
                    results.append(_result(
                        _RULES_BY_ID["OPT-004"], i + 1,
                        stripped[:100],
                        "Use setter methods (box.set_lefttop(), line.set_xy1(), etc.) "
                        "instead of delete + recreate. In-place updates are ~2x faster."
                    ))
                    break
    return results


def _detect_unprotected_drawings(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-005: Updating drawings on every historical bar when only last bar matters."""
    results: list[OptimizationResult] = []
    setter_pattern = re.compile(r"(table|box|line|label)\.(cell_set_|set_|cell_set_bgcolor)")
    has_islast_guard = _code_has_keyword(code, "barstate.islast")

    # Check for var-declared table/box/line/label with setters outside islast guard
    var_draw_pattern = re.compile(r"var\s+(table|box|line|label)\s+\w+")
    has_var_drawing = any(var_draw_pattern.search(_strip_comments(ln)) for ln in lines)

    if has_var_drawing and not has_islast_guard:
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if setter_pattern.search(stripped):
                # Verify it's not inside an if block
                indent = len(line) - len(line.lstrip())
                if indent == 0:
                    results.append(_result(
                        _RULES_BY_ID["OPT-005"], i + 1,
                        stripped[:100],
                        "Wrap drawing/table updates in `if barstate.islast` to avoid "
                        "executing on every historical bar. Only the final state is visible."
                    ))
                    break  # Report once
    return results


def _detect_invariant_in_loop(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-006: Recalculating invariant values inside a per-bar loop."""
    results: list[OptimizationResult] = []
    loop_pattern = re.compile(r"^\s*for\s+(\w+)\s*=")
    invariant_funcs = re.compile(r"(math\.(cos|sin|sqrt|log|exp|pow)|array\.(min|max|range|size))\s*\(")
    loop_var = ""
    in_loop = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        loop_m = loop_pattern.match(lines[i])
        if loop_m:
            loop_var = loop_m.group(1)
            in_loop = True
        elif in_loop and stripped and not stripped.startswith(("for ", "if ", "else", "//")):
            # Check if line has invariant function calls (not using loop variable in their args)
            if invariant_funcs.search(stripped):
                # Extract the argument portion of the invariant call
                inv_m = invariant_funcs.search(stripped)
                # Get everything after the function name up to closing paren
                after_func = stripped[inv_m.end():]
                paren_depth = 0
                args = ""
                for ch in after_func:
                    if ch == "(":
                        paren_depth += 1
                    elif ch == ")":
                        paren_depth -= 1
                        if paren_depth < 0:
                            break
                    args += ch
                # Only flag if loop variable is NOT in the function arguments
                if loop_var not in args:
                    results.append(_result(
                        _RULES_BY_ID["OPT-006"], i + 1,
                        stripped[:100],
                        "Move loop-invariant calculations (math.cos, array.min, etc.) "
                        "outside the loop. Compute once before the loop, then reference."
                    ))
                    break
        elif in_loop and not stripped:
            in_loop = False
    return results


def _detect_loopable_to_builtin(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-007: Using loops when a loop-free built-in expression exists."""
    results: list[OptimizationResult] = []
    # Detect sum accumulation pattern: for i = 1 to length / sum += source[i] or source - source[i]
    loop_sum_pattern = re.compile(r"for\s+\w+\s*=\s*1\s+to\s+length")
    accum_pattern = re.compile(r"\w+\s*\+=\s*(?:\w+\s*-\s*)?\w+\[\w+\]")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if loop_sum_pattern.search(stripped):
            body = "\n".join(_strip_comments(ln) for ln in lines[i:min(i + 8, len(lines))])
            if accum_pattern.search(body):
                results.append(_result(
                    _RULES_BY_ID["OPT-007"], i + 1,
                    stripped[:100],
                    "Replace manual summation loop with math.sum(), ta.sma(), or algebraic "
                    "simplification. Example: (source * length - math.sum(source, length)[1]) / length"
                ))
                break
    return results


def _detect_indexof_in_loop(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-008: array.indexof() inside for...in loop."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"for\s+\w+\s+in\s+\w+", stripped):
            # Check body for array.indexof
            for j in range(i + 1, min(i + 10, len(lines))):
                body = _strip_comments(lines[j]).strip()
                if re.search(r"array\.indexof\s*\(", body):
                    results.append(_result(
                        _RULES_BY_ID["OPT-008"], j + 1,
                        body[:100],
                        "Use `for [index, item] in array` instead of `for item in array` + "
                        "`array.indexof()`. Avoids O(n) search per iteration."
                    ))
                    break
            if results:
                break
    return results


def _detect_loop_invariant_motion(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-009: Loop-invariant code inside loop (recomputing min/max/range)."""
    results: list[OptimizationResult] = []
    # Detect array.min/max/range/size called with the same array being iterated
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"for\s+\w+\s+in\s+(\w+)", stripped):
            arr_match = re.search(r"in\s+(\w+)", stripped)
            if not arr_match:
                continue
            arr_name = arr_match.group(1)
            for j in range(i + 1, min(i + 15, len(lines))):
                body = _strip_comments(lines[j]).strip()
                invariant = re.search(
                    rf"array\.(min|max|range|size)\s*\(\s*{arr_name}\s*\)", body
                )
                if invariant:
                    results.append(_result(
                        _RULES_BY_ID["OPT-009"], j + 1,
                        body[:100],
                        f"Hoist `array.{invariant.group(1)}({arr_name})` out of the loop. "
                        f"Compute once before the loop — the array doesn't change inside."
                    ))
                    break
            if results:
                break
    return results


def _detect_missing_max_bars_back(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-010: Late history reference without max_bars_back."""
    results: list[OptimizationResult] = []
    has_max_bars_back = _code_has_keyword(code, "max_bars_back(")
    has_islast = _code_has_keyword(code, "barstate.islast")

    if has_islast and not has_max_bars_back:
        # Look for history references inside islast blocks
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            m_offset = re.search(r"\[(\d{3,})\]", stripped)
            if m_offset and int(m_offset.group(1)) >= 400:
                results.append(_result(
                    _RULES_BY_ID["OPT-010"], i + 1,
                    stripped[:100],
                    f"Add `max_bars_back(varName, {m_offset.group(1)})` before the history reference to avoid "
                    "runtime re-execution across the entire dataset."
                ))
                break
    return results


def _detect_oversized_buffer(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-011: Oversized max_bars_back buffers."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = re.search(r"max_bars_back\s*\(\s*\w+\s*,\s*(\d+)\s*\)", stripped)
        if m and int(m.group(1)) > 4900:
            results.append(_result(
                _RULES_BY_ID["OPT-011"], i + 1,
                stripped[:100],
                f"max_bars_back value ({m.group(1)}) is very large. "
                "Use the smallest buffer needed — oversized buffers waste memory."
            ))
    return results


def _detect_missing_calc_bars_count(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-012: Missing calc_bars_count for last-bar-only logic."""
    results: list[OptimizationResult] = []
    has_islast_heavy = _code_has_keyword(code, "barstate.islast")
    has_calc_bars_count = _code_has_keyword(code, "calc_bars_count")

    if has_islast_heavy and not has_calc_bars_count:
        # Only suggest if there's drawing-heavy islast logic
        if _count_in_scope(code, re.compile(r"(table|box|line|label)\.(new|cell_set|set_)")) > 0:
            results.append(_result(
                _RULES_BY_ID["OPT-012"], 0,
                "Script uses barstate.islast with drawings",
                "Add `calc_bars_count = N` to your indicator()/strategy() declaration "
                "to limit unnecessary historical execution when only the last bar matters."
            ))
    return results


def _detect_na_drawing_coords(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-013: Drawing objects with na coordinates (wastes drawing slots)."""
    results: list[OptimizationResult] = []
    na_coord_pattern = re.compile(r"(label|box|line)\.new\s*\(.*\?.*:.*(?<![a-zA-Z])na(?![a-zA-Z])")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if na_coord_pattern.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-013"], i + 1,
                stripped[:100],
                "Use conditional `if` to create drawings instead of ternary with `na`. "
                "na IDs still count toward the 500 drawing limit."
            ))
    return results


def _detect_plot_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-014: Exceeding 64 plot counts."""
    results: list[OptimizationResult] = []
    plot_pattern = re.compile(r"\b(plot|plotarrow|plotbar|plotcandle|plotchar|plotshape|bgcolor|barcolor)\s*\(")
    count = _count_in_scope(code, plot_pattern)
    if count > 48:
        results.append(_result(
            _RULES_BY_ID["OPT-014"], 0,
            f"Found {count} plot-generating calls",
            f"Approaching or exceeding the 64 plot count limit ({count} found). "
            "Reduce plot calls or use conditional display. Series color params add extra counts."
        ))
    return results


def _detect_request_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-015: Exceeding 40 unique request.*() calls."""
    results: list[OptimizationResult] = []
    req_pattern = re.compile(r"request\.\w+\s*\(")
    req_count = _count_in_scope(code, req_pattern)
    # Track unique call signatures (function name + first two args)
    unique_calls: set[str] = set()
    req_sig = re.compile(r"request\.\w+\s*\([^,]{0,80},\s*[^,]{0,30}")
    for ln in code.splitlines():
        clean = _strip_comments(ln)
        unique_calls.update(req_sig.findall(clean))
    # Flag if many diverse calls (unique > 15) or many total calls with diversity
    if req_count > 35 and len(unique_calls) > 1:
        results.append(_result(
            _RULES_BY_ID["OPT-015"], 0,
            f"Found {req_count} request.*() calls ({len(unique_calls)} unique)",
            f"Approaching the 40 unique request.*() call limit ({req_count} total, "
            f"{len(unique_calls)} unique). Consolidate using tuple requests or reduce calls."
        ))
    elif len(unique_calls) > 35:
        results.append(_result(
            _RULES_BY_ID["OPT-015"], 0,
            f"Found {req_count} request.*() calls ({len(unique_calls)} unique)",
            f"Approaching the 40 unique request.*() call limit ({req_count} total, "
            f"{len(unique_calls)} unique). Consolidate using tuple requests or reduce calls."
        ))
    return results


def _detect_tuple_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-016: Exceeding 127 tuple elements in request.*()."""
    results: list[OptimizationResult] = []
    tuple_pattern = re.compile(r"\[([^\]]{50,})\]\s*=\s*request\.\w+\s*\(")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = tuple_pattern.search(stripped)
        if m:
            elements = m.group(1).split(",")
            if len(elements) > 100:
                results.append(_result(
                    _RULES_BY_ID["OPT-016"], i + 1,
                    f"[{len(elements)} elements] = request.*()",
                    f"Tuple has {len(elements)} elements — limit is 127. "
                    "Use a UDT (user-defined type) instead of tuples for large data structures."
                ))
    return results


def _detect_large_script(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-017: Very large scripts approaching token limits."""
    results: list[OptimizationResult] = []
    line_count = len(lines)
    if line_count > 4000:
        results.append(_result(
            _RULES_BY_ID["OPT-017"], 0,
            f"Script has {line_count} lines",
            "Very large scripts risk exceeding the 100K compiled token limit. "
            "Extract repeated code into functions, use libraries, or reduce duplication."
        ))
    return results


def _detect_scope_var_count(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-018: Many variable declarations per scope (approaching 1000 limit)."""
    results: list[OptimizationResult] = []
    # Count global-scope declarations (rough heuristic)
    global_vars = 0
    for line in lines:
        stripped = _strip_comments(line).strip()
        if re.match(r"^(int|float|bool|string|color|table|box|line|label|array|matrix|map)\s+", stripped):
            global_vars += 1
        elif re.match(r"^\w+\s*=", stripped) and not re.match(r"^(if|for|while|switch|else|var\b)", stripped):
            global_vars += 1
    if global_vars > 750:
        results.append(_result(
            _RULES_BY_ID["OPT-018"], 0,
            f"~{global_vars} variable declarations in global scope",
            f"Approaching the 1000 variables-per-scope limit (~{global_vars} found). "
            "Use arrays/maps/UDTs to group related values."
        ))
    return results


def _detect_unbounded_collection(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-020: Unbounded array.push() on every bar."""
    results: list[OptimizationResult] = []
    push_pattern = re.compile(r"array\.push\s*\(")
    has_shift = _code_has_keyword(code, "array.shift(") or _code_has_keyword(code, "array.pop(")
    has_fixed_size = _code_has_keyword(code, "array.new<") and _count_in_scope(code, re.compile(r"array\.new<\w+>\s*\(\s*\d+")) > 0

    if not has_shift and not has_fixed_size:
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if push_pattern.search(stripped):
                # Check if it's inside global scope (runs every bar)
                indent = len(line) - len(line.lstrip())
                if indent == 0:
                    results.append(_result(
                        _RULES_BY_ID["OPT-020"], i + 1,
                        stripped[:100],
                        "Array grows on every bar without bounds. Use array.shift() "
                        "to maintain a fixed-size queue, or use array.new(N) with pre-allocation. "
                        "Limit: 100,000 elements."
                    ))
                    break
    return results


def _detect_deep_history(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-021: History references beyond 5000 bars."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = re.search(r"\w+\[\s*(\d+)\s*\]", stripped)
        if m and int(m.group(1)) > 4900:
            var_name = stripped.split("[")[0].strip().split()[-1] if "[" in stripped else "series"
            results.append(_result(
                _RULES_BY_ID["OPT-021"], i + 1,
                stripped[:100],
                f"History reference [{m.group(1)}] may exceed the 5000-bar buffer limit "
                f"for user-defined series. Add max_bars_back({var_name}, {m.group(1)}) if needed."
            ))
    return results


def _detect_forward_bars(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-022: Exceeding 500 bars forward for drawing x-coordinates."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = re.search(r"bar_index\s*\+\s*(\d+)", stripped)
        if m and int(m.group(1)) > 400:
            if re.search(r"(line|box|label)\.(new|set_)", stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-022"], i + 1,
                    stripped[:100],
                    f"Forward bars ({m.group(1)}) approaching the 500-bar limit for drawing x-coordinates. "
                    "Add maxval=500 to the relevant input.int()."
                ))
    return results


def _detect_large_loop(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-023: Very large loops (risk of 500ms timeout)."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = re.search(r"for\s+\w+\s*=\s*\d+\s+to\s+(\d+)", stripped)
        if m and int(m.group(1)) > 10000:
            results.append(_result(
                _RULES_BY_ID["OPT-023"], i + 1,
                stripped[:100],
                f"Loop upper bound ({m.group(1)}) risks the 500ms per-bar timeout. "
                "Reduce iterations, use built-ins, or distribute across bars."
            ))
    return results


def _detect_ta_in_local_scope(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-027: ta.*() calls inside local scopes (if/for/function)."""
    results: list[OptimizationResult] = []
    ta_pattern = re.compile(r"\bta\.\w+\s*\(")

    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())

        if re.match(r"^(if|for|while|switch|else\s+if)\b", stripped):
            # Check body lines (indented lines following this one)
            for j in range(i + 1, min(i + 15, len(lines))):
                body_line = lines[j]
                body_stripped = _strip_comments(body_line).strip()
                if not body_stripped:
                    continue
                body_indent = len(body_line) - len(body_line.lstrip())
                if body_indent <= indent:
                    break  # Exited the block
                if ta_pattern.search(body_stripped):
                    results.append(_result(
                        _RULES_BY_ID["OPT-027"], j + 1,
                        body_stripped[:100],
                        "ta.*() functions called in local scope may produce incorrect results "
                        "due to inconsistent historical buffers. Call in global scope, then "
                        "conditionally use the result."
                    ))
                    break
            if results:
                break
    return results


def _detect_varip_repaint(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-028: varip used in plotted outputs (repainting risk)."""
    results: list[OptimizationResult] = []
    varip_vars: set[str] = set()
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = re.match(r"varip\s+\w+\s+(\w+)", stripped)
        if m:
            varip_vars.add(m.group(1))

    if varip_vars:
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if re.match(r"plot\s*\(", stripped):
                for var in varip_vars:
                    if re.search(rf"\b{var}\b", stripped):
                        results.append(_result(
                            _RULES_BY_ID["OPT-028"], i + 1,
                            stripped[:100],
                            f"varip variable '{var}' feeds into plot() — values will repaint "
                            "on realtime bars and differ after reload. Use var for non-repainting output."
                        ))
                        break
    return results


def _detect_missing_var(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-030: Missing var for cross-bar state persistence."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        # Detect: int x = 0 / x += 10  (missing var — x resets every bar)
        m = re.match(r"^(int|float|bool|string)\s+(\w+)\s*=\s*0", stripped)
        if m:
            var_name = m.group(2)
            # Check if var_name is modified with +=/-= later in global scope
            for j in range(len(lines)):
                other = _strip_comments(lines[j]).strip()
                if re.match(rf"^{var_name}\s*(\+|-|\*)=", other):
                    results.append(_result(
                        _RULES_BY_ID["OPT-030"], i + 1,
                        stripped[:100],
                        f"Variable '{var_name}' is initialized to 0 and modified with +=/-= but "
                        "lacks `var` keyword — it resets to 0 on every bar. Use `var int x = 0` "
                        "if you want cross-bar accumulation."
                    ))
                    break
            if results:
                break
    return results


def _detect_realtime_buffer_mismatch(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-031: Different history offsets for historical vs realtime."""
    results: list[OptimizationResult] = []
    ternary_history = re.compile(r"barstate\.ishistory\s*\?.*:\s*\w+\[\d+\]")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if ternary_history.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-031"], i + 1,
                stripped[:100],
                "Different history offsets for historical vs realtime bars can cause "
                "runtime errors. Add max_bars_back() with the maximum offset used."
            ))
    return results


def _detect_calc_on_order_fills(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-032: calc_on_order_fills causing 4x execution overhead."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"calc_on_order_fills\s*=\s*true", stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-032"], i + 1,
                stripped[:100],
                "calc_on_order_fills=true causes 4x executions per historical bar (one per OHLC tick). "
                "Remove unless you specifically need intra-bar order fill calculations."
            ))
    return results


def _detect_history_in_local_scope(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-026: History reference [] on local-scope variables."""
    results: list[OptimizationResult] = []
    # Detect: if condition / val := something / val[1] inside the if block
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        # Check if inside a local scope (indented) and using history on a var declared in same scope
        if indent > 0 and re.search(r"\w+\[\d+\]", stripped):
            var_match = re.search(r"(\w+)\[\d+\]", stripped)
            if var_match:
                var_name = var_match.group(1)
                # Check if var was declared in a local scope (not a built-in)
                builtins = {"close", "open", "high", "low", "volume", "time", "bar_index",
                            "hl2", "hlc3", "ohlc4", "hlcc4", "close", "open"}
                if var_name not in builtins:
                    # Find declaration
                    for j in range(len(lines)):
                        decl = _strip_comments(lines[j]).strip()
                        if re.match(rf"^(int|float|bool|string|color)\s+{var_name}\b", decl):
                            decl_indent = len(lines[j]) - len(lines[j].lstrip())
                            if decl_indent > 0:
                                results.append(_result(
                                    _RULES_BY_ID["OPT-026"], i + 1,
                                    stripped[:100],
                                    f"History reference {var_name}[n] on a local-scope variable "
                                    "may produce incorrect results. Declare the variable in "
                                    "global scope for consistent history tracking."
                                ))
                                break
            if results:
                break
    return results


def _detect_realtime_tick_repaint(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-029: barstate.isrealtime/isnew + varip feeding into plot (repainting)."""
    results: list[OptimizationResult] = []
    # Pre-collect varip-declared variable names
    declared_varip: set[str] = set()
    for line in lines:
        stripped = _strip_comments(line).strip()
        m_varip = re.match(r"varip\s+(?:int|float|bool|string|color)\s+(\w+)", stripped)
        if m_varip:
            declared_varip.add(m_varip.group(1))
        elif re.match(r"varip\s+(\w+)", stripped):
            # varip without type prefix (e.g., varip x = na)
            m2 = re.match(r"varip\s+(\w+)", stripped)
            if m2 and m2.group(1) not in ("int", "float", "bool", "string", "color",
                                           "table", "box", "line", "label", "array",
                                           "matrix", "map"):
                declared_varip.add(m2.group(1))

    # Collect variable names assigned inside barstate.isrealtime/isnew blocks
    # that were declared as varip, OR any variable if no varip declarations exist
    suspect_vars: set[str] = set()
    in_realtime_block = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"barstate\.(isrealtime|isnew)\b", stripped):
            in_realtime_block = True
            continue
        if in_realtime_block:
            indent = len(line) - len(line.lstrip())
            if stripped and indent == 0 and not stripped.startswith(("else", "//")):
                in_realtime_block = False
                continue
            m_assign = re.match(r"(\w+)\s*:=\s*", stripped)
            if m_assign:
                var_name = m_assign.group(1)
                # Only track if it's a known varip var, or if no varip declarations exist
                if declared_varip and var_name not in declared_varip:
                    continue
                suspect_vars.add(var_name)

    if not suspect_vars:
        return results

    # Check if any suspect variable feeds into a plot call
    plot_pattern = re.compile(r"\b(plot|plotcandle|plotchar|plotshape|plotarrow|plotbar)\s*\(")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if plot_pattern.search(stripped):
            for var in suspect_vars:
                if re.search(rf"\b{var}\b", stripped):
                    results.append(_result(
                        _RULES_BY_ID["OPT-029"], i + 1,
                        stripped[:100],
                        f"Variable '{var}' is updated inside a barstate.isrealtime/isnew block "
                        "and feeds into a plot. This causes values to repaint — different behavior "
                        "on historical vs realtime bars, and different results after chart reload. "
                        "Use `var` instead of real-time updates for plotted output."
                    ))
                    break
            if results:
                break
    return results


def _detect_collection_in_request(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-035: Returning arrays/collections from request.*() calls."""
    results: list[OptimizationResult] = []
    pattern = re.compile(r"request\.\w+\s*\([^)]*array\.(new|from)\s*<")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if pattern.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-035"], i + 1, stripped[:100],
                "Do not return arrays/collections from request.*() calls on every bar. "
                "Each bar creates a new copy in memory. Use request.security() for scalar "
                "values only, or compute the array once inside the requested context."
            ))
            break
    # Also check multi-line patterns
    if not results:
        req_pattern = re.compile(r"request\.\w+\s*\(")
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if req_pattern.search(stripped):
                block = " ".join(_strip_comments(lines[j]).strip() for j in range(i, min(i + 5, len(lines))))
                if re.search(r"array\.(new|from)\s*<", block) and not results:
                    results.append(_result(
                        _RULES_BY_ID["OPT-035"], i + 1, stripped[:100],
                        "Do not return arrays/collections from request.*() calls on every bar. "
                        "Each bar creates a new copy in memory."
                    ))
                    break
    return results


def _detect_unused_request(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-039: Unused request.*() result."""
    results: list[OptimizationResult] = []
    req_pattern = re.compile(r"^(?:(?:int|float|bool|string|color|array|matrix|map)\s+)?(\w+)\s*=\s*request\.\w+\s*\(")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = req_pattern.match(stripped)
        if m:
            var_name = m.group(1)
            # Count references to var_name in other lines
            ref_count = 0
            for j, other_line in enumerate(lines):
                if j == i:
                    continue
                other = _strip_comments(other_line).strip()
                if re.search(rf"\b{var_name}\b", other):
                    ref_count += 1
            if ref_count == 0:
                results.append(_result(
                    _RULES_BY_ID["OPT-039"], i + 1, stripped[:100],
                    f"The result of this request.*() call is assigned to '{var_name}' but never used. "
                    "Remove it to reduce execution overhead."
                ))
                break
    return results


def _detect_var_in_loop_header(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-033: var in loop header causes loop to exit after first iteration."""
    pattern = re.compile(r"\bfor\s+var(?:_(?:int|float|bool|string|color))?\s+\w+\s*=")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if pattern.search(stripped):
            return [_result(
                _RULES_BY_ID["OPT-033"], i + 1, stripped,
                "Remove 'var' from the loop header variable declaration. Using 'var' in a loop "
                "header causes the loop to exit after the first iteration because the variable "
                "persists across bars instead of being scoped to the loop."
            )]
    return []


def _detect_variable_shadowing(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-034: Variable shadowing (= instead of :=) in local scope."""
    results: list[OptimizationResult] = []
    # Collect global-scope variable assignments
    global_vars: set[str] = set()
    for line in lines:
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            m = re.match(r"^(\w+)\s*=\s*", stripped)
            if m and m.group(1) not in (
                "if", "for", "while", "switch", "else", "var", "varip",
                "indicator", "strategy", "library", "import", "export",
                "true", "false",
            ):
                global_vars.add(m.group(1))

    # Check indented lines for shadow assignments
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            m = re.match(r"^(\w+)\s*=\s*", stripped)
            if m and m.group(1) in global_vars:
                # Exclude if it has a type prefix (new local declaration)
                if not re.match(
                    r"^(int|float|bool|string|color|table|box|line|label|array|matrix|map)\s+",
                    stripped,
                ):
                    results.append(_result(
                        _RULES_BY_ID["OPT-034"], i + 1, stripped[:100],
                        f"Use ':=' to reassign outer-scope variable '{m.group(1)}' instead of "
                        f"'=' which creates a new local variable that shadows it."
                    ))
                    break  # Report once
    return results


def _detect_strategy_no_date_filter(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-037: strategy() with strategy.entry() but no time/date filter."""
    results: list[OptimizationResult] = []
    has_strategy = _code_has_keyword(code, "strategy(")
    has_entry = _code_has_keyword(code, "strategy.entry(")
    if not (has_strategy and has_entry):
        return results
    time_filters = (
        "time(", "year(", "month(", "dayofmonth(", "hour(", "minute(",
        "timenow", "timestamp(", "input.time", "timeframe.",
    )
    has_filter = any(_code_has_keyword(code, tf) for tf in time_filters)
    if not has_filter:
        results.append(_result(
            _RULES_BY_ID["OPT-037"], 0, "strategy() with strategy.entry()",
            "Strategy has entries but no time/date filter (time(), year(), timestamp(), etc.). "
            "Without a date range, the strategy runs on the entire chart history which is slow "
            "and produces irrelevant backtest results. Add a date filter like "
            "'useDateFilter = input.bool(true)' with 'if useDateFilter and time > timestamp(...)'."
        ))
    return results


def _detect_manual_array_get_loop(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-040: Manual for i=0 to size-1 with array.get() — use for...in instead."""
    results: list[OptimizationResult] = []
    for_loop_pattern = re.compile(r"\bfor\s+\w+\s*=\s*0\s+to\s+array\.size\s*\(\s*(\w+)\s*\)\s*-\s*1")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = for_loop_pattern.search(stripped)
        if m:
            arr_name = m.group(1)
            block = "\n".join(
                _strip_comments(lines[j]).strip()
                for j in range(i, min(i + 10, len(lines)))
            )
            if re.search(rf"array\.get\s*\(\s*{arr_name}\s*,", block):
                results.append(_result(
                    _RULES_BY_ID["OPT-040"], i + 1, stripped[:100],
                    f"Manual index loop with array.get() is slower than 'for...in'. "
                    f"Replace with: 'for item in {arr_name}' to iterate directly."
                ))
                break
    return results


def _detect_table_count_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-036: Approaching the 9-table-per-chart limit."""
    results: list[OptimizationResult] = []
    count = _count_in_scope(code, re.compile(r"table\.new\s*\("))
    if count > 7:
        results.append(_result(
            _RULES_BY_ID["OPT-036"], 0,
            f"Found {count} table.new() calls",
            f"Approaching the 9-table-per-chart limit ({count} found, limit is 9). "
            "Reduce table count by reusing a single table with conditional content."
        ))
    return results


def _detect_table_creation_every_bar(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-038: Table creation every bar without barstate.isfirst guard."""
    results: list[OptimizationResult] = []
    has_table_new = _code_has_keyword(code, "table.new(")
    has_isfirst = _code_has_keyword(code, "barstate.isfirst")
    if has_table_new and not has_isfirst:
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if re.search(r"table\.new\s*\(", stripped):
                indent = len(line) - len(line.lstrip())
                if indent == 0:
                    results.append(_result(
                        _RULES_BY_ID["OPT-038"], i + 1, stripped[:100],
                        "Create tables once on the first bar using `if barstate.isfirst` "
                        "with `var`, then update cells on `barstate.islast`. Creating tables "
                        "on every bar wastes resources."
                    ))
                    break
    return results


def _detect_request_missing_calc_bars(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-041: request.*() calls missing calc_bars_count optimization."""
    results: list[OptimizationResult] = []
    req_pattern = re.compile(r"request\.(security|security_lower_tf|seed)\s*\(")
    has_calc_bars = _code_has_keyword(code, "calc_bars_count")
    req_count = sum(1 for line in lines if req_pattern.search(_strip_comments(line)))
    if req_count >= 5 and not has_calc_bars:
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if req_pattern.search(stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-041"], i + 1, stripped[:100],
                    f"Script has {req_count} request.*() calls but no calc_bars_count parameter. "
                    "Add calc_bars_count to request.*() calls to restrict historical data retrieval "
                    "and reduce runtime."
                ))
                break
    return results


def _detect_drawing_id_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-042: Drawing object count approaching 500-ID limit."""
    results: list[OptimizationResult] = []
    drawing_pattern = re.compile(r"\b(line|box|label)\.new\s*\(")
    count = _count_in_scope(code, drawing_pattern)
    if count > 400:
        results.append(_result(
            _RULES_BY_ID["OPT-042"], 0,
            f"Found {count} drawing creation calls",
            f"Approaching the 500-drawing-ID limit ({count} found, limit is 500 per type). "
            "Use `var` to reuse drawing objects and update with setters. "
            "Wrap creation in conditional blocks to avoid wasting IDs."
        ))
    return results


def _detect_code_duplication(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-043: Repeated code blocks that should be extracted into functions."""
    results: list[OptimizationResult] = []
    # Count identical non-trivial lines (ignoring comments, blanks, declarations)
    stripped_lines = []
    for line in lines:
        s = _strip_comments(line).strip()
        if len(s) > 20 and not s.startswith("//@version") and not s.startswith("indicator(") and not s.startswith("strategy("):
            stripped_lines.append(s)
    # Group by identical content
    from collections import Counter
    line_counts = Counter(stripped_lines)
    for line_text, count in line_counts.most_common(5):
        if count >= 5:
            results.append(_result(
                _RULES_BY_ID["OPT-043"], 0, line_text[:100],
                f"Identical code line repeated {count} times. Extract into a function "
                "to reduce compiled tokens and improve maintainability."
            ))
            break
    return results


def _detect_strategy_order_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-044: Strategy likely to exceed 9000 order limit."""
    results: list[OptimizationResult] = []
    has_strategy = _code_has_keyword(code, "strategy(")
    has_entry = _code_has_keyword(code, "strategy.entry(")
    if not (has_strategy and has_entry):
        return results
    # Detect strategies with unconditional entry on every bar
    entry_in_global = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if "strategy.entry" in stripped:
            indent = len(line) - len(line.lstrip())
            # Entry at global scope (no if guard) = order on every bar
            if indent == 0:
                entry_in_global = True
                break
    if entry_in_global:
        results.append(_result(
            _RULES_BY_ID["OPT-044"], 0, "Unconditional strategy.entry() at global scope",
            "strategy.entry() at global scope creates an order on every bar. "
            "The backtesting limit is 9,000 orders (1M in Deep Backtesting). "
            "Add entry conditions (e.g., if block with signal check) to reduce order count."
        ))
    return results


def _detect_unused_imports(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-045: Import statements where the library prefix is never used."""
    results: list[OptimizationResult] = []
    import_pattern = re.compile(r"^import\s+\S+\s+(?:as\s+)?(\w+)\s*")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = import_pattern.match(stripped)
        if m:
            prefix = m.group(1)
            # Count references to the prefix (excluding the import line itself)
            ref_count = sum(
                1 for j, other in enumerate(lines)
                if j != i and re.search(rf"\b{prefix}\.", _strip_comments(other))
            )
            if ref_count == 0:
                results.append(_result(
                    _RULES_BY_ID["OPT-045"], i + 1, stripped[:100],
                    f"Imported library '{prefix}' is never used in the script. "
                    "Remove unused imports to reduce compilation request size."
                ))
                break
    return results


def _detect_calc_on_every_tick(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-046: calc_on_every_tick=true causes execution on every realtime tick."""
    results: list[OptimizationResult] = []
    if any(re.search(r"calc_on_every_tick\s*=\s*true", _strip_comments(ln)) for ln in lines):
        results.append(_result(
            _RULES_BY_ID["OPT-046"], 0, "calc_on_every_tick = true",
            "calc_on_every_tick=true causes the strategy to execute on every realtime tick. "
            "This significantly increases execution load. Only enable if you need "
            "intrabar signal generation."
        ))
    return results


def _detect_oversized_script_file(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-047: Script approaching 5MB compilation request size limit."""
    results: list[OptimizationResult] = []
    size_bytes = len(code.encode("utf-8"))
    if size_bytes > 3_000_000:  # 3MB = 60% of 5MB limit
        results.append(_result(
            _RULES_BY_ID["OPT-047"], 0,
            f"Script size: {size_bytes:,} bytes",
            f"Script is {size_bytes / 1_000_000:.1f}MB, approaching the 5MB compilation request "
            "size limit. Reduce script size by extracting code into functions or libraries."
        ))
    return results


def _detect_polyline_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-048: Polyline count approaching 100-ID limit."""
    results: list[OptimizationResult] = []
    count = _count_in_scope(code, re.compile(r"\bpolyline\.new\s*\("))
    if count > 80:
        results.append(_result(
            _RULES_BY_ID["OPT-048"], 0,
            f"Found {count} polyline.new() calls",
            f"Approaching the 100-polyline-ID limit ({count} found, limit is 100). "
            "Reuse polyline objects with setters instead of creating new ones."
        ))
    return results


def _detect_lookahead_future_leak(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-049: lookahead_on without [1] offset causes future data leak."""
    results: list[OptimizationResult] = []
    lookahead_pattern = re.compile(r"barmerge\.lookahead_on")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if lookahead_pattern.search(stripped) and "request." in stripped:
            # Check if the expression uses [1] offset for non-repainting
            # Look in the same line and nearby lines for the offset
            block = "\n".join(_strip_comments(lines[j]).strip() for j in range(i, min(i + 3, len(lines))))
            if not re.search(r"\[\s*1\s*\]", block):
                results.append(_result(
                    _RULES_BY_ID["OPT-049"], i + 1, stripped[:100],
                    "lookahead = barmerge.lookahead_on without [1] offset causes future data leak. "
                    "Use `request.security(..., expression[1], lookahead = barmerge.lookahead_on)` "
                    "for non-repainting HTF data. TradingView moderates scripts using this incorrectly."
                ))
                break
    return results


def _detect_timenow_repaint(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-050: timenow usage causes inconsistent historical/realtime behavior."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"\btimenow\b", stripped) and not stripped.startswith("//"):
            results.append(_result(
                _RULES_BY_ID["OPT-050"], i + 1, stripped[:100],
                "'timenow' returns the current real-world time, producing different values "
                "on historical vs realtime bars and after chart reload. Use 'time' or "
                "'input.time()' for consistent historical behavior."
            ))
            break
    return results


def _detect_isnew_signal_repaint(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-051: barstate.isnew for signal logic repaints."""
    results: list[OptimizationResult] = []
    has_isnew = False
    has_signal_output = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"\bbarstate\.isnew\b", stripped):
            has_isnew = True
        if re.search(r"\b(strategy\.entry|alert|plot\(|plotshape\()", stripped):
            has_signal_output = True

    if has_isnew and has_signal_output:
        # Check if isnew is used in a conditional block that also contains signal logic
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if re.search(r"\bbarstate\.isnew\b", stripped):
                # Check body for signal-producing calls
                for j in range(i + 1, min(i + 10, len(lines))):
                    body = _strip_comments(lines[j]).strip()
                    body_indent = len(lines[j]) - len(lines[j].lstrip())
                    if body_indent <= len(line) - len(line.lstrip()) and body and not body.startswith(("else", "//")):
                        break
                    if re.search(r"\b(strategy\.entry|alert|plotshape|plot\()", body):
                        results.append(_result(
                            _RULES_BY_ID["OPT-051"], i + 1, stripped[:100],
                            "barstate.isnew is true at bar close on historical data but at bar open "
                            "in realtime — signals produced here will repaint. Use "
                            "barstate.isconfirmed instead for consistent signal generation."
                        ))
                        break
                if results:
                    break
    return results


def _detect_missing_isconfirmed(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-052: Signal-producing logic without barstate.isconfirmed guard."""
    results: list[OptimizationResult] = []
    has_strategy = _code_has_keyword(code, "strategy(")
    has_isconfirmed = _code_has_keyword(code, "barstate.isconfirmed")

    if not has_strategy or has_isconfirmed:
        return results

    # Look for strategy.entry/exit/alertcondition/alert at global scope (no guard)
    signal_pattern = re.compile(r"\b(strategy\.entry|strategy\.exit|alertcondition|alert\s*\()\b")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if signal_pattern.search(stripped):
            indent = len(line) - len(line.lstrip())
            if indent == 0:
                results.append(_result(
                    _RULES_BY_ID["OPT-052"], i + 1, stripped[:100],
                    "Signal-producing logic at global scope without barstate.isconfirmed guard "
                    "will trigger on unconfirmed realtime ticks, producing false signals. "
                    "Wrap in `if barstate.isconfirmed` to only execute on confirmed bar closes."
                ))
                break
    return results


def _detect_non_standard_chart_strategy(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-053: Strategy using non-standard chart types produces misleading backtests."""
    results: list[OptimizationResult] = []
    has_strategy = _code_has_keyword(code, "strategy(")
    if not has_strategy:
        return results
    non_standard = re.compile(r"ticker\.(heikinashi|renko|kagi|linebreak|pointfigure|range)\s*\(")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if non_standard.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-053"], i + 1, stripped[:100],
                "Strategy using non-standard chart data (Heikin-Ashi, Renko, etc.) produces "
                "misleading backtest results because the chart data is reconstructed and does "
                "not represent actual market prices. Only use standard chart types for backtesting."
            ))
            break
    return results


def _detect_lower_tf_request(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-054: request.security_lower_tf() results differ on historical vs realtime."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.search(r"\brequest\.security_lower_tf\s*\(", stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-054"], i + 1, stripped[:100],
                "request.security_lower_tf() returns different results on historical vs realtime "
                "bars — realtime intrabars are not sorted and may differ from historical ones. "
                "Test thoroughly and avoid using for signal generation."
            ))
            break
    return results


def _detect_drawing_display_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-055: Many drawings without max_*_count parameter."""
    results: list[OptimizationResult] = []
    drawing_count = _count_in_scope(code, re.compile(r"\b(line|box|label)\.new\s*\("))
    has_max_count = any(
        re.search(r"max_(lines|boxes|labels)_count\s*=", _strip_comments(ln))
        for ln in lines
    )
    if drawing_count > 50 and not has_max_count:
        results.append(_result(
            _RULES_BY_ID["OPT-055"], 0,
            f"Found {drawing_count} drawing creation calls without max_*_count",
            f"Script creates {drawing_count} drawings but only the last 50 are displayed by default. "
            "Add max_lines_count, max_boxes_count, or max_labels_count to your indicator()/strategy() "
            "declaration to display more."
        ))
    return results


def _detect_map_size_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-056: Map approaching 50,000 key-value pair limit."""
    results: list[OptimizationResult] = []
    # Check for maps populated in loops (pattern: map.put inside for loop)
    has_map_put_in_loop = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        # Match for...in or bounded for with high upper bound
        for_in_m = re.search(r"\bfor\s+\w+\s+in\s+\w+", stripped)
        for_range_m = re.search(r"\bfor\s+\w+\s*=\s*\d+\s+to\s+(\d+)", stripped)
        is_large_loop = bool(for_in_m) or (for_range_m and int(for_range_m.group(1)) >= 1000)
        if is_large_loop:
            for j in range(i + 1, min(i + 10, len(lines))):
                body = _strip_comments(lines[j]).strip()
                if re.search(r"\bmap\.put\s*\(", body):
                    has_map_put_in_loop = True
                    break
        if has_map_put_in_loop:
            break

    if has_map_put_in_loop:
        results.append(_result(
            _RULES_BY_ID["OPT-056"], 0,
            "Map populated inside loop",
            "Maps are limited to 50,000 key-value pairs (100,000 elements total). "
            "Populating a map inside a loop risks exceeding this limit. Use array.size() "
            "checks or pre-allocate with map.new() if possible."
        ))
    return results


def _detect_request_in_loop_variable(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-057: request.*() inside loop with variable arguments (request count explosion)."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        # Match for loop headers
        loop_match = re.search(r"\bfor\s+(\w+)\s+in\s+(\w+)", stripped)
        if not loop_match:
            loop_match = re.search(r"\bfor\s+(\w+)\s*=\s*\d+\s+to\s+", stripped)
        if not loop_match:
            continue
        loop_var = loop_match.group(1) if loop_match else ""
        # Check loop body for request.*() calls that use the loop variable
        for j in range(i + 1, min(i + 15, len(lines))):
            body = _strip_comments(lines[j]).strip()
            body_indent = len(lines[j]) - len(lines[j].lstrip())
            if body_indent <= len(line) - len(line.lstrip()) and body and not body.startswith(("else", "//")):
                break
            if re.search(r"\brequest\.\w+\s*\(", body) and loop_var and loop_var in body:
                results.append(_result(
                    _RULES_BY_ID["OPT-057"], j + 1, body[:100],
                    "request.*() called inside a loop with the loop variable as an argument. "
                    "Each iteration may create a unique request counting toward the 40-call limit. "
                    "Pre-compute the values outside the loop or use a different approach."
                ))
                break
        if results:
            break
    return results


def _detect_footprint_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-058: request.footprint() called more than once (limit is 1 per script)."""
    results: list[OptimizationResult] = []
    count = _count_in_scope(code, re.compile(r"\brequest\.footprint\s*\("))
    if count > 1:
        results.append(_result(
            _RULES_BY_ID["OPT-058"], 0,
            f"Found {count} request.footprint() calls",
            f"PineScript limits scripts to a single request.footprint() call ({count} found). "
            "Remove duplicate calls."
        ))
    return results


def _detect_drawing_past_max_bars(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-059: Drawing x-coordinate references beyond 10,000 bars."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        # Check bar_index - N (backward) or bar_index + N (forward) beyond 10,000
        m = re.search(r"bar_index\s*-\s*(\d+)", stripped)
        if m and int(m.group(1)) > 9000:
            if re.search(r"(line|box|label)\.(new|set_)", stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-059"], i + 1, stripped[:100],
                    f"Drawing x-coordinate references {m.group(1)} bars back, exceeding the "
                    "10,000-bar limit for xloc.bar_index drawings. Reduce the offset."
                ))
                break
    return results


def _detect_long_if_else_chain(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-060: Long if/else if chain replaceable with switch."""
    results: list[OptimizationResult] = []
    # Count consecutive else-if blocks comparing the same variable against literals
    chain_var = ""
    chain_count = 0
    chain_start = 0
    init_pattern = re.compile(r"^if\s+(\w+)\s*==\s*(?:\d+|\"[^\"]*\"|'[^']*'|true|false)")
    comparison_pattern = re.compile(r"else\s+if\s+(\w+)\s*==\s*(?:\d+|\"[^\"]*\"|'[^']*'|true|false)")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        # Check for initial `if` that starts a chain
        init_m = init_pattern.match(stripped)
        m = comparison_pattern.match(stripped)
        if init_m and chain_count == 0:
            # Initial `if` starts a potential chain
            chain_var = init_m.group(1)
            chain_start = i
            chain_count = 1
        elif m:
            var = m.group(1)
            if chain_count == 0:
                chain_var = var
                chain_start = i
                chain_count = 1
            elif var == chain_var:
                chain_count += 1
            else:
                # Different variable — reset
                chain_var = var
                chain_start = i
                chain_count = 1
        elif stripped.startswith("else if") or stripped.startswith("elif"):
            # Non-comparison else-if, count toward chain
            chain_count += 1
        elif chain_count >= 5:
            break  # Already enough to report
        else:
            chain_count = 0
            chain_var = ""

    if chain_count >= 5:
        snippet = _strip_comments(lines[chain_start]).strip()[:100]
        results.append(_result(
            _RULES_BY_ID["OPT-060"], chain_start + 1, snippet,
            f"Long if/else chain ({chain_count}+ branches) comparing '{chain_var}' against values. "
            f"Use `switch {chain_var}` for cleaner code and potentially better compiled performance."
        ))
    return results


def _detect_dead_function(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-061: User-defined function declared but never called."""
    results: list[OptimizationResult] = []
    # Match single-line: name(params) => expr  or  name(params) =>
    func_pattern = re.compile(r"^(\w+)\s*\([^)]*\)\s*=>")
    # Also match multi-line: name(params) => on its own line (arrow function header)
    multiline_func = re.compile(r"^(\w+)\s*\([^)]*\)\s*=>\s*$")

    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = func_pattern.match(stripped) or multiline_func.match(stripped)
        if not m:
            continue
        fname = m.group(1)
        # Skip PineScript keywords that look like function defs
        if fname in ("if", "for", "while", "switch", "else", "var", "varip",
                     "import", "export", "indicator", "strategy", "library", "method"):
            continue
        # Count references in non-comment code (excluding the declaration line itself)
        ref_count = 0
        for j, other in enumerate(lines):
            if j == i:
                continue
            other_stripped = _strip_comments(other).strip()
            # Look for function call: fname( or method call: prefix.fname(
            if re.search(rf"(?<!\w){re.escape(fname)}\s*\(", other_stripped):
                ref_count += 1
                if ref_count >= 1:
                    break
        if ref_count == 0:
            results.append(_result(
                _RULES_BY_ID["OPT-061"], i + 1, stripped[:100],
                f"Function '{fname}' is declared but never called. "
                "The PineScript compiler may remove unused code, but it still consumes "
                "compilation tokens and adds clutter. Remove dead functions."
            ))
            break  # Report once
    return results


def _detect_string_concat_in_loop(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-062: String concatenation (+=) inside loop."""
    results: list[OptimizationResult] = []
    in_loop = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.match(r"^\s*for\s+\w+", stripped) or re.match(r"^\s*while\s+", stripped):
            in_loop = True
            continue
        if in_loop and stripped:
            indent = len(line) - len(line.lstrip())
            # Check for string concatenation: varName += "..." or varName += '...'
            if re.search(r"\w+\s*\+=\s*(?:\"[^\"]*\"|'[^']*')", stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-062"], i + 1, stripped[:100],
                    "String concatenation with += inside a loop creates new string objects each iteration. "
                    "Collect parts in an array<string>, then use str.join() after the loop for O(n) instead of O(n²)."
                ))
                break
            # Exit loop body
            if indent == 0 and not stripped.startswith("else"):
                in_loop = False
    return results


def _detect_formatting_in_loop(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-063: str.tostring()/str.format() inside loop body."""
    results: list[OptimizationResult] = []
    in_loop = False
    loop_indent = 0
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.match(r"^\s*for\s+\w+", stripped) or re.match(r"^\s*while\s+", stripped):
            in_loop = True
            loop_indent = len(line) - len(line.lstrip())
            continue
        if in_loop:
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= loop_indent and not stripped.startswith("else"):
                in_loop = False
                continue
            if re.search(r"\bstr\.(tostring|format)\s*\(", stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-063"], i + 1, stripped[:100],
                    "str.tostring()/str.format() inside a loop is expensive. "
                    "If the format result doesn't change per iteration, compute it before the loop. "
                    "If it does, minimize format string complexity."
                ))
                break
    return results


def _detect_array_prepend(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-064: array.insert(arr, 0, val) — O(n) prepend operation."""
    results: list[OptimizationResult] = []
    prepend_pattern = re.compile(r"array\.insert\s*\(\s*(\w+)\s*,\s*0\s*,")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if prepend_pattern.search(stripped):
            arr_match = prepend_pattern.search(stripped)
            arr_name = arr_match.group(1) if arr_match else "array"
            results.append(_result(
                _RULES_BY_ID["OPT-064"], i + 1, stripped[:100],
                f"array.insert({arr_name}, 0, val) shifts all elements — O(n) per call. "
                "Use array.push() to append, then iterate in reverse, or use array.unshift() if available."
            ))
            break
    return results


def _detect_dead_plot(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-065: plot() with display=display.none wastes a plot count slot."""
    results: list[OptimizationResult] = []
    dead_plot = re.compile(r"\bplot\s*\([^)]*display\s*=\s*display\.none")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if dead_plot.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-065"], i + 1, stripped[:100],
                "plot() with display=display.none still consumes one of the 64 plot count slots "
                "and executes its computation every bar. Remove the plot entirely or comment it out."
            ))
            break
    return results


def _detect_color_new_every_bar(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-066: color.new() recomputed every bar instead of pre-computed."""
    results: list[OptimizationResult] = []
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        # color.new() at global scope (indent 0) without var — recomputed every bar
        if indent == 0 and re.search(r"\bcolor\.new\s*\(", stripped):
            # Check it's NOT a var declaration
            if not stripped.startswith(("var ", "varip ")):
                results.append(_result(
                    _RULES_BY_ID["OPT-066"], i + 1, stripped[:100],
                    "color.new() at global scope is recomputed on every bar. "
                    "If the color doesn't change, declare it with `var` to compute once: "
                    "`var myColor = color.new(color.red, 80)`."
                ))
                break
    return results


def _detect_fixed_size_push(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-067: array.push() inside loop with fixed/known bounds."""
    results: list[OptimizationResult] = []
    fixed_loop = re.compile(r"\bfor\s+\w+\s*=\s*(\d+)\s+to\s+(\d+)\b")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = fixed_loop.search(stripped)
        if m:
            lower = int(m.group(1))
            upper = int(m.group(2))
            iterations = upper - lower + 1
            if iterations > 3 and iterations <= 10000:
                # Check body for array.push
                for j in range(i + 1, min(i + 10, len(lines))):
                    body = _strip_comments(lines[j]).strip()
                    if re.search(r"\barray\.push\s*\(", body):
                        results.append(_result(
                            _RULES_BY_ID["OPT-067"], i + 1, stripped[:100],
                            f"Loop runs {iterations} times with array.push() inside. "
                            f"Pre-allocate the array with array.new<type>({iterations}) and use "
                            "array.set() for index-based assignment — avoids repeated memory reallocations."
                        ))
                        break
                if results:
                    break
    return results


def _detect_unnecessary_var(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-068: var declared but unconditionally overwritten every bar."""
    results: list[OptimizationResult] = []
    var_pattern = re.compile(r"^var\s+(?:int|float|bool|string|color)\s+(\w+)\s*=")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            continue
        m = var_pattern.match(stripped)
        if not m:
            continue
        var_name = m.group(1)
        # Check if var_name is unconditionally reassigned (:=) at global scope
        unconditional_reassign = False
        for j, other in enumerate(lines):
            if j == i:
                continue
            other_stripped = _strip_comments(other).strip()
            other_indent = len(lines[j]) - len(lines[j].lstrip())
            if other_indent == 0 and re.match(rf"^{re.escape(var_name)}\s*:=", other_stripped):
                unconditional_reassign = True
                break
        if unconditional_reassign:
            results.append(_result(
                _RULES_BY_ID["OPT-068"], i + 1, stripped[:100],
                f"Variable '{var_name}' is declared with `var` but unconditionally reassigned every bar. "
                "`var` adds persistence overhead. Use a normal declaration without `var` "
                "since the value is always overwritten."
            ))
            break
    return results


def _detect_matrix_in_loops(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-069: Matrix operations inside per-bar loops."""
    results: list[OptimizationResult] = []
    matrix_pattern = re.compile(r"\bmatrix\.(add|sub|mul|set|get|row|col)\s*\(")
    in_loop = False
    loop_indent = 0
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if re.match(r"^\s*for\s+\w+", stripped) or re.match(r"^\s*while\s+", stripped):
            in_loop = True
            loop_indent = len(line) - len(line.lstrip())
            continue
        if in_loop:
            indent = len(line) - len(line.lstrip())
            if stripped and indent <= loop_indent and not stripped.startswith("else"):
                in_loop = False
                continue
            if matrix_pattern.search(stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-069"], i + 1, stripped[:100],
                    "Matrix operation inside a loop body. Matrix operations can be expensive — "
                    "consider using matrix.mult() for batch operations or hoisting invariant "
                    "matrix calculations outside the loop."
                ))
                break
    return results


def _detect_input_in_local_scope(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-070: input.*() calls inside local scopes (if/for/function)."""
    results: list[OptimizationResult] = []
    input_pattern = re.compile(r"\binput\.(int|float|bool|string|source|color)\s*\(")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if indent > 0 and input_pattern.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-070"], i + 1, stripped[:100],
                "input.*() called inside a local scope. PineScript evaluates inputs at global scope. "
                "Move input declarations to the top of the script (global scope) for clarity "
                "and to avoid potential issues with conditional input evaluation."
            ))
            break
    return results


def _detect_missing_input_bounds(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-071: input.int() without minval/maxval bounds."""
    results: list[OptimizationResult] = []
    input_int_pattern = re.compile(r"\binput\.int\s*\(")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if input_int_pattern.search(stripped):
            has_minval = "minval" in stripped
            has_maxval = "maxval" in stripped
            if not has_minval and not has_maxval:
                results.append(_result(
                    _RULES_BY_ID["OPT-071"], i + 1, stripped[:100],
                    "input.int() without minval or maxval bounds. Unbounded integer inputs can "
                    "cause loop overflows, buffer overruns, or excessive memory allocation. "
                    "Add minval= and maxval= to constrain valid values."
                ))
                break  # Report once
    return results


def _detect_ticker_vs_tickerid(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-072: syminfo.ticker in request.security() instead of syminfo.tickerid."""
    results: list[OptimizationResult] = []
    pattern = re.compile(r"request\.security\s*\(\s*syminfo\.ticker\b")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if pattern.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-072"], i + 1, stripped[:100],
                "request.security() uses syminfo.ticker which lacks the exchange prefix. "
                "This can cause incorrect data on multi-exchange symbols. "
                "Use syminfo.tickerid instead for exchange-aware symbol resolution."
            ))
            break
    return results


def _detect_redundant_cancel(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-073: Multiple strategy.cancel_all() or strategy.close_all() calls."""
    results: list[OptimizationResult] = []
    cancel_pattern = re.compile(r"\bstrategy\.(cancel_all|close_all)\s*\(")
    call_counts: dict[str, list[int]] = {}
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        m = cancel_pattern.search(stripped)
        if m:
            func = m.group(1)
            call_counts.setdefault(func, []).append(i + 1)

    for func, line_nums in call_counts.items():
        if len(line_nums) >= 2:
            results.append(_result(
                _RULES_BY_ID["OPT-073"], line_nums[0],
                f"strategy.{func}() called {len(line_nums)} times",
                f"strategy.{func}() called {len(line_nums)} times. The first call already "
                f"{'cancels all orders' if 'cancel' in func else 'closes all positions'}. "
                "Remove redundant calls to reduce execution overhead."
            ))
            break
    return results


def _detect_lower_tf_request_security(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-074: request.security() for lower timeframe data (should use request.security_lower_tf)."""
    results: list[OptimizationResult] = []
    # Detect request.security with very low timeframe strings like "1", "5", "15", "1S", etc.
    low_tf_pattern = re.compile(
        r"request\.security\s*\([^,]+,\s*\"(?:1|5|15|30|1[SMH]|5[SMH]|15[SMH])\""
    )
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if low_tf_pattern.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-074"], i + 1, stripped[:100],
                "request.security() with a lower timeframe may return only the last intrabar "
                "and produce different results on historical vs realtime bars. "
                "Use request.security_lower_tf() for lower timeframe data access."
            ))
            break
    return results


def _detect_missing_const(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-075: Variables assigned literal values without const keyword."""
    results: list[OptimizationResult] = []
    const_candidates = re.compile(
        r"^(color|string|int|float|bool)\s+(\w+)\s*=\s*"
        r'(color\.\w+|"[^"]*"|\'[^\']*\'|\d+\.?\d*|true|false)\s*$'
    )
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            continue
        m = const_candidates.match(stripped)
        if m and not stripped.startswith(("var ", "const ", "varip ")):
            type_prefix = m.group(1)
            var_name = m.group(2)
            value = m.group(3)
            results.append(_result(
                _RULES_BY_ID["OPT-075"], i + 1, stripped[:100],
                f"Variable '{var_name}' holds a constant value but lacks the `const` keyword. "
                f"Use `const {type_prefix} {var_name} = {value}` to compute once at "
                "compilation time instead of on every bar."
            ))
            break
    return results


def _detect_unused_variables(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-076: Declared but unreferenced variables (compilation token waste)."""
    results: list[OptimizationResult] = []
    decl_pattern = re.compile(
        r"^(?:(?:int|float|bool|string|color|table|box|line|label|array|matrix|map)<[^>]+>\s+|"
        r"(?:int|float|bool|string|color|table|box|line|label|array|matrix|map)\s+|"
        r"var\s+(?:int|float|bool|string|color)\s+|var\s+)"
        r"(\w+)\s*="
    )
    skip_names = {
        "close", "open", "high", "low", "volume", "time", "bar_index",
        "hl2", "hlc3", "ohlc4", "hlcc4", "true", "false", "na",
        "strategy", "indicator", "library", "import", "export",
    }
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            continue
        m = decl_pattern.match(stripped)
        if not m:
            continue
        var_name = m.group(1)
        if var_name in skip_names:
            continue
        # Skip if the variable is used in plot/strategy/label/etc. output expressions
        # — these are "used" even if not referenced elsewhere
        if re.match(r"^\w+\s*=\s*(plot|plotshape|plotchar|plotarrow|plotbar|plotcandle|bgcolor|barcolor)\s*\(", stripped):
            continue
        # Count references (excluding declaration line)
        ref_count = sum(
            1 for j, other in enumerate(lines)
            if j != i and re.search(rf"\b{var_name}\b", _strip_comments(other))
        )
        if ref_count == 0:
            results.append(_result(
                _RULES_BY_ID["OPT-076"], i + 1, stripped[:100],
                f"Variable '{var_name}' is declared but never used. Remove it to save "
                "compilation tokens (limit: 100K per script)."
            ))
            break
    return results


def _detect_manual_cum(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-077: Manual cumulative sum instead of ta.cum()."""
    results: list[OptimizationResult] = []
    # Pattern: var float cumX = 0 ... cumX := cumX + val  OR  cumX += val
    # at global scope, which is exactly what ta.cum() does.
    cum_var_pattern = re.compile(r"\bvar\s+(float|int)\s+(\w+)\s*=\s*0")
    cum_vars = {}
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            continue
        m = cum_var_pattern.match(stripped)
        if m:
            cum_vars[m.group(2)] = i

    for var_name, decl_line in cum_vars.items():
        # Check if the var is updated with += or := var + something
        has_cumulative_update = False
        for j, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if j == decl_line:
                continue
            # cumX += expr  or  cumX := cumX + expr
            if re.search(rf"\b{var_name}\b\s*\+=", stripped):
                has_cumulative_update = True
                break
            if re.search(rf"\b{var_name}\b\s*:=\s*{var_name}\b\s*\+", stripped):
                has_cumulative_update = True
                break
        if has_cumulative_update:
            results.append(_result(
                _RULES_BY_ID["OPT-077"], decl_line + 1,
                _strip_comments(lines[decl_line]).strip()[:100],
                f"Variable '{var_name}' accumulates values with `+=` or `:= var + expr`. "
                "Use `ta.cum(source)` instead — it's a built-in that handles this efficiently "
                "without a `var` declaration. Example: `ta.cum(close > open ? 1 : 0)`."
            ))
            break
    return results


def _detect_push_loop_to_array_from(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-078: Multiple array.push() calls that could use array.from()."""
    results: list[OptimizationResult] = []
    # Look for 3+ consecutive array.push() calls to the same array at same scope
    push_pattern = re.compile(r"array\.push\s*\(\s*(\w+)\s*,")
    consecutive: list[tuple[str, int, int]] = []  # (var_name, line_idx, indent)
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        m = push_pattern.search(stripped)
        if m:
            var_name = m.group(1)
            if consecutive and consecutive[-1][0] == var_name and consecutive[-1][2] == indent:
                consecutive.append((var_name, i, indent))
            else:
                consecutive = [(var_name, i, indent)]
        else:
            if len(consecutive) >= 3:
                arr_name = consecutive[0][0]
                first_line = consecutive[0][1]
                results.append(_result(
                    _RULES_BY_ID["OPT-078"], first_line + 1,
                    _strip_comments(lines[first_line]).strip()[:100],
                    f"Array '{arr_name}' has {len(consecutive)} consecutive push() calls. "
                    "Use `array.from(val1, val2, val3, ...)` to create and populate the array "
                    "in a single statement. This reduces code size and compilation tokens."
                ))
                break
            consecutive = []
    # Check final batch
    if not results and len(consecutive) >= 3:
        arr_name = consecutive[0][0]
        first_line = consecutive[0][1]
        results.append(_result(
            _RULES_BY_ID["OPT-078"], first_line + 1,
            _strip_comments(lines[first_line]).strip()[:100],
            f"Array '{arr_name}' has {len(consecutive)} consecutive push() calls. "
            "Use `array.from(val1, val2, val3, ...)` to create and populate the array "
            "in a single statement. This reduces code size and compilation tokens."
        ))
    return results


def _detect_manual_midpoint(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-079: Manual midpoint (a + b) / 2 instead of math.avg(a, b)."""
    results: list[OptimizationResult] = []
    # Pattern: (expr + expr) / 2  or  expr + (expr - expr) / 2 (weighted midpoint)
    midpoint_pattern = re.compile(r"\([\s\w.\[\]]+\s*\+\s*[\s\w.\[\]]+\s*\)\s*/\s*2\b")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if midpoint_pattern.search(stripped):
            # More specific: (a + b) / 2
            if re.search(r"\(\s*\w[\w.]*\s*\+\s*\w[\w.]*\s*\)\s*/\s*2\b", stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-079"], i + 1, stripped[:100],
                    "Midpoint calculated as `(a + b) / 2`. Use `math.avg(a, b)` instead — "
                    "it's a built-in that's more readable and handles edge cases."
                ))
                break
    return results


def _detect_missing_runtime_validation(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-080: Division or operation without runtime.error() guard for zero/invalid inputs."""
    results: list[OptimizationResult] = []
    # Check for input.int/float used in division without runtime.error() guard
    has_runtime_error = _code_has_keyword(code, "runtime.error(")
    if has_runtime_error:
        return results

    # Find input variables used as divisors
    input_var_pattern = re.compile(r"(?:int|float)\s+(\w+)\s*=\s*input\.(int|float)\s*\(")
    input_vars = set()
    for line in lines:
        stripped = _strip_comments(line).strip()
        m = input_var_pattern.search(stripped)
        if m:
            input_vars.add(m.group(1))

    if not input_vars:
        return results

    # Check if any input var is used as a divisor (denominator)
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        for var in input_vars:
            # / var or / (var * ...) or / (var + ...)
            if re.search(rf"/\s*{re.escape(var)}\b", stripped) or \
               re.search(rf"/\s*\(\s*{re.escape(var)}\b", stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-080"], i + 1, stripped[:100],
                    f"Input '{var}' is used as a divisor without validation. "
                    "Add `if var == 0` check with `runtime.error(\"message\")` to prevent "
                    "division-by-zero at runtime. Example: "
                    "`if length == 0\n    runtime.error(\"Length must be > 0\")`."
                ))
                break
        if results:
            break
    return results


def _detect_plot_display_optimization(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-081: plot() used only for data but rendered visually (missing display.data_window)."""
    results: list[OptimizationResult] = []
    # Look for plot() with conditional values (na on condition) that suggests
    # the plot is used as a data source, not visual display
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        # plot(cond ? val : na, ...) — conditional plot used for data passing
        if re.search(r"\bplot\s*\(.+\?.+:.*na\b", stripped):
            # Check if display= parameter is already present
            if "display" not in stripped:
                results.append(_result(
                    _RULES_BY_ID["OPT-081"], i + 1, stripped[:100],
                    "Conditional plot() without `display` parameter. If this plot is only "
                    "used as a data source for other scripts (indicator-on-indicator), add "
                    "`display = display.data_window` to avoid visual rendering overhead. "
                    "Use `display = display.status_line + display.data_window` if you want "
                    "values visible without chart clutter."
                ))
                break
    return results


def _detect_request_security_repainting(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-082: request.security() without anti-repainting safeguards (no lookahead + no offset)."""
    results: list[OptimizationResult] = []
    req_pattern = re.compile(r"\brequest\.security\s*\(")

    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if not req_pattern.search(stripped):
            continue

        # Skip calls inside user-defined functions (wrappers may handle repainting)
        indent = len(line) - len(line.lstrip())
        if indent > 0:
            continue

        # Collect full call text (may span multiple lines)
        call_text = stripped
        for j in range(i + 1, min(i + 8, len(lines))):
            next_line = _strip_comments(lines[j]).strip()
            call_text += " " + next_line
            if ")" in next_line:
                break

        # Check for lookahead parameter (explicit awareness = user made a choice)
        has_lookahead = "lookahead" in call_text

        # Check for any numeric history offset [N] on the expression (e.g. close[1])
        has_offset = bool(re.search(r"\[\s*\d+\s*\]", call_text))

        # No lookahead AND no offset = naive repainting call
        if not has_lookahead and not has_offset:
            results.append(_result(
                _RULES_BY_ID["OPT-082"], i + 1, stripped[:100],
                "request.security() without lookahead control or expression offset may repaint "
                "on realtime bars — values differ from historical data after reload. Use "
                "`request.security(sym, tf, expr[1], lookahead = barmerge.lookahead_on)` "
                "for non-repainting HTF data. This pattern returns the previous HTF bar's "
                "confirmed value immediately when a new HTF bar starts. "
                "PineCoders pattern: wrap in a validated helper function."
            ))
            break  # Report once

    return results


def _detect_request_no_tf_validation(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-083: request.security() without timeframe validation (TF may not be higher)."""
    results: list[OptimizationResult] = []
    req_pattern = re.compile(r"\brequest\.security\s*\(")
    has_tf_check = _code_has_keyword(code, "timeframe.in_seconds(")

    if has_tf_check:
        return results

    # Only flag if there are request.security calls at global scope
    has_global_req = False
    for line in lines:
        stripped = _strip_comments(line).strip()
        indent = len(line) - len(line.lstrip())
        if indent == 0 and req_pattern.search(stripped):
            has_global_req = True
            break

    if not has_global_req:
        return results

    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if req_pattern.search(stripped):
            results.append(_result(
                _RULES_BY_ID["OPT-083"], i + 1, stripped[:100],
                "request.security() without timeframe validation. Requesting a TF "
                "that's not higher than the chart's TF gives incorrect or redundant data. "
                "Add: `if timeframe.in_seconds(requestedTf) <= timeframe.in_seconds()` "
                "then `runtime.error(\"Requested TF must be higher than chart TF\")`. "
                "PineCoders pattern: validate TF in a wrapper function before calling "
                "request.security()."
            ))
            break

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Rule registry
# ─────────────────────────────────────────────────────────────────────────────

_RULES: list[_Rule] = [
    _Rule("OPT-001", "Reimplementing built-ins with loops", "critical", "Loop waste",
          _detect_reimplemented_builtins, "PineScript built-in functions ta.highest ta.lowest optimization"),

    _Rule("OPT-002", "Repeated identical function calls", "high", "Loop waste",
          _detect_repeated_calls, "PineScript reducing repetition caching function results"),

    _Rule("OPT-003", "Multiple request.security() to same context", "critical", "Request/TA waste",
          _detect_multiple_request_security, "request.security tuple consolidation optimization"),

    _Rule("OPT-004", "Delete + recreate drawings vs setters", "high", "Drawing waste",
          _detect_delete_recreate, "PineScript drawing update setter vs delete recreate"),

    _Rule("OPT-005", "Drawing updates on all historical bars", "high", "Drawing waste",
          _detect_unprotected_drawings, "barstate.islast drawing table update optimization"),

    _Rule("OPT-006", "Loop-invariant calculations inside loop", "high", "Loop waste",
          _detect_invariant_in_loop, "loop invariant code motion optimization PineScript"),

    _Rule("OPT-007", "Loop when loop-free built-in exists", "critical", "Loop waste",
          _detect_loopable_to_builtin, "PineScript eliminate loops math.sum built-in replacement"),

    _Rule("OPT-008", "array.indexof() inside for...in loop", "high", "Loop waste",
          _detect_indexof_in_loop, "for index item in array optimization PineScript"),

    _Rule("OPT-009", "Loop-invariant array.min/max inside loop", "high", "Loop waste",
          _detect_loop_invariant_motion, "loop invariant code motion array operations"),

    _Rule("OPT-010", "Missing max_bars_back for late references", "high", "Memory/buffer",
          _detect_missing_max_bars_back, "max_bars_back historical buffer calculation"),

    _Rule("OPT-011", "Oversized max_bars_back buffer", "medium", "Memory/buffer",
          _detect_oversized_buffer, "max_bars_back buffer size optimization"),

    _Rule("OPT-012", "Missing calc_bars_count for last-bar logic", "medium", "Memory/buffer",
          _detect_missing_calc_bars_count, "calc_bars_count restrict execution optimization"),

    _Rule("OPT-013", "Drawings with na coordinates", "medium", "Drawing waste",
          _detect_na_drawing_coords, "PineScript drawing limit na coordinates conditional creation"),

    _Rule("OPT-014", "Approaching plot count limit (64)", "critical", "Resource limits",
          _detect_plot_limit, "PineScript plot count limit 64"),

    _Rule("OPT-015", "Approaching request.*() call limit (40)", "critical", "Resource limits",
          _detect_request_limit, "request security call limit 40 optimization"),

    _Rule("OPT-016", "Large tuple in request.*() (limit 127)", "critical", "Resource limits",
          _detect_tuple_limit, "request security tuple element limit UDT alternative"),

    _Rule("OPT-017", "Very large script (token limit risk)", "critical", "Resource limits",
          _detect_large_script, "PineScript compiled token limit functions libraries"),

    _Rule("OPT-018", "Many variables per scope (limit 1000)", "critical", "Resource limits",
          _detect_scope_var_count, "PineScript variables per scope limit arrays UDT"),

    _Rule("OPT-020", "Unbounded array growth", "critical", "Memory/buffer",
          _detect_unbounded_collection, "array size limit 100000 shift fixed queue"),

    _Rule("OPT-021", "Deep history reference (5000-bar limit)", "critical", "Memory/buffer",
          _detect_deep_history, "max_bars_back 5000 historical buffer limit"),

    _Rule("OPT-022", "Forward bars >500 for drawings", "medium", "Resource limits",
          _detect_forward_bars, "drawing bars forward limit 500 maxval"),

    _Rule("OPT-023", "Very large loop (timeout risk)", "critical", "Resource limits",
          _detect_large_loop, "loop execution timeout 500ms optimization"),

    _Rule("OPT-026", "History reference on local-scope variable", "high", "Correctness",
          _detect_history_in_local_scope, "time series local scope history reference PineScript"),

    _Rule("OPT-027", "ta.*() in local scope", "high", "Correctness",
          _detect_ta_in_local_scope, "ta functions local scope historical buffer incorrect"),

    _Rule("OPT-028", "varip repainting on plotted output", "medium", "Correctness",
          _detect_varip_repaint, "varip repainting plot realtime behavior"),

    _Rule("OPT-029", "Realtime tick data + plot repainting", "medium", "Correctness",
          _detect_realtime_tick_repaint, "varip repainting realtime tick data plot behavior"),

    _Rule("OPT-030", "Missing var for cross-bar persistence", "high", "Correctness",
          _detect_missing_var, "var keyword cross bar state persistence accumulation"),

    _Rule("OPT-031", "Different history offsets historical vs realtime", "critical", "Correctness",
          _detect_realtime_buffer_mismatch, "realtime historical buffer mismatch max_bars_back"),

    _Rule("OPT-032", "calc_on_order_fills 4x overhead", "medium", "Strategy perf",
          _detect_calc_on_order_fills, "calc_on_order_fills strategy performance overhead"),

    _Rule("OPT-033", "'var' in loop header (exits after 1st iteration)", "high", "Correctness",
          _detect_var_in_loop_header, "PineScript var keyword loop header iteration bug"),

    _Rule("OPT-034", "Variable shadowing (= instead of :=)", "high", "Correctness",
          _detect_variable_shadowing, "PineScript variable shadowing assignment operator := vs = scope"),

    _Rule("OPT-035", "Returning collections from request.*()", "critical", "Request/TA waste",
          _detect_collection_in_request, "PineScript request.security array collection memory per bar"),

    _Rule("OPT-036", "Table count approaching 9-table limit", "medium", "Resource limits",
          _detect_table_count_limit, "PineScript table.new maximum 9 tables per chart limit"),

    _Rule("OPT-037", "Strategy without date filter", "medium", "Strategy perf",
          _detect_strategy_no_date_filter, "PineScript strategy backtest date range filter time year"),

    _Rule("OPT-038", "Table creation without barstate.isfirst guard", "high", "Drawing waste",
          _detect_table_creation_every_bar, "PineScript table.new barstate.isfirst optimization create once"),

    _Rule("OPT-039", "Unused request.*() result", "medium", "Request/TA waste",
          _detect_unused_request, "PineScript request.security unused result removal optimization"),

    _Rule("OPT-040", "Manual array.get() loop vs for...in", "medium", "Loop waste",
          _detect_manual_array_get_loop, "PineScript for...in array iteration optimization"),

    # --- Additional Resource/Strategy Rules ---
    _Rule("OPT-041", "request.*() missing calc_bars_count", "medium", "Request/TA waste",
          _detect_request_missing_calc_bars, "PineScript request.security calc_bars_count optimization"),

    _Rule("OPT-042", "Drawing ID count approaching 500 limit", "critical", "Resource limits",
          _detect_drawing_id_limit, "PineScript line box label 500 ID limit optimization"),

    _Rule("OPT-043", "Repeated code (extract to function)", "medium", "Resource limits",
          _detect_code_duplication, "PineScript reduce compiled tokens function extraction"),

    _Rule("OPT-044", "Strategy may exceed 9000 order limit", "high", "Strategy perf",
          _detect_strategy_order_limit, "PineScript strategy backtesting 9000 order limit"),

    _Rule("OPT-045", "Unused import", "medium", "Resource limits",
          _detect_unused_imports, "PineScript unused library import compilation size"),

    _Rule("OPT-046", "calc_on_every_tick overhead", "medium", "Strategy perf",
          _detect_calc_on_every_tick, "PineScript calc_on_every_tick realtime execution overhead"),

    _Rule("OPT-047", "Script approaching 5MB compilation limit", "high", "Resource limits",
          _detect_oversized_script_file, "PineScript 5MB compilation request size limit"),

    _Rule("OPT-048", "Polyline count approaching 100 limit", "high", "Resource limits",
          _detect_polyline_limit, "PineScript polyline.new 100 ID limit optimization"),

    # --- Correctness / Repainting Rules ---
    _Rule("OPT-049", "lookahead_on without [1] offset (future leak)", "critical", "Correctness",
          _detect_lookahead_future_leak, "PineScript lookahead barmerge future data leak repainting"),

    _Rule("OPT-050", "timenow causing repaint", "high", "Correctness",
          _detect_timenow_repaint, "PineScript timenow historical realtime inconsistent behavior"),

    _Rule("OPT-051", "barstate.isnew signal repaint", "high", "Correctness",
          _detect_isnew_signal_repaint, "PineScript barstate.isnew repainting signal isconfirmed"),

    _Rule("OPT-052", "Signal without isconfirmed guard", "high", "Correctness",
          _detect_missing_isconfirmed, "PineScript barstate.isconfirmed signal false trigger realtime"),

    _Rule("OPT-053", "Strategy on non-standard chart data", "critical", "Correctness",
          _detect_non_standard_chart_strategy, "PineScript strategy Heikin-Ashi Renko misleading backtest"),

    _Rule("OPT-054", "request.security_lower_tf() repainting", "high", "Correctness",
          _detect_lower_tf_request, "PineScript request.security_lower_tf intrabar historical realtime difference"),

    _Rule("OPT-055", "Drawings without max_*_count (default 50)", "medium", "Drawing waste",
          _detect_drawing_display_limit, "PineScript max_lines_count max_boxes_count display limit 50"),

    _Rule("OPT-056", "Map populated in loop (50K limit)", "medium", "Memory/buffer",
          _detect_map_size_limit, "PineScript map size limit 50000 key-value pairs"),

    _Rule("OPT-057", "request.*() in loop with variable args", "critical", "Request/TA waste",
          _detect_request_in_loop_variable, "PineScript request.security inside loop variable arguments count limit"),

    _Rule("OPT-058", "request.footprint() called more than once (limit 1)", "critical", "Resource limits",
          _detect_footprint_limit, "PineScript request.footprint limit 1 per script"),

    _Rule("OPT-059", "Drawing x-coordinate >10,000 bars", "high", "Drawing waste",
          _detect_drawing_past_max_bars, "PineScript drawing bar_index coordinate limit 10000"),

    # --- Code Quality ---
    _Rule("OPT-060", "Long if/else chain replaceable with switch", "medium", "Code quality",
          _detect_long_if_else_chain, "PineScript switch statement optimization if else chain"),

    _Rule("OPT-061", "Dead user-defined function", "medium", "Code quality",
          _detect_dead_function, "PineScript unused function dead code removal"),

    _Rule("OPT-062", "String concatenation in loop", "medium", "Code quality",
          _detect_string_concat_in_loop, "PineScript string concatenation loop str.join array optimization"),

    _Rule("OPT-063", "str.tostring()/str.format() in loop body", "medium", "Code quality",
          _detect_formatting_in_loop, "PineScript str.tostring str.format loop performance optimization"),

    _Rule("OPT-064", "array.insert(arr, 0, val) O(n) prepend", "medium", "Loop waste",
          _detect_array_prepend, "PineScript array.insert prepend O(n) push reverse optimization"),

    # --- Resource/Memory ---
    _Rule("OPT-065", "plot() with display=display.none (dead plot)", "medium", "Resource limits",
          _detect_dead_plot, "PineScript plot display.none dead plot count slot waste"),

    _Rule("OPT-066", "color.new() recomputed every bar", "low", "Loop waste",
          _detect_color_new_every_bar, "PineScript color.new var pre-compute optimization"),

    _Rule("OPT-067", "Array push in fixed-bounds loop (pre-allocate)", "medium", "Memory/buffer",
          _detect_fixed_size_push, "PineScript array pre-allocation fixed loop push optimization"),

    _Rule("OPT-068", "Unnecessary var for always-overwritten variable", "low", "Memory/buffer",
          _detect_unnecessary_var, "PineScript var keyword unnecessary persistence overhead removal"),

    _Rule("OPT-069", "Matrix operations in per-bar loop", "medium", "Loop waste",
          _detect_matrix_in_loops, "PineScript matrix operations loop batch optimization"),

    # --- Correctness/Strategy ---
    _Rule("OPT-070", "input.*() in local scope", "medium", "Correctness",
          _detect_input_in_local_scope, "PineScript input local scope global scope requirement"),

    _Rule("OPT-071", "input.int() missing minval/maxval bounds", "medium", "Correctness",
          _detect_missing_input_bounds, "PineScript input.int minval maxval bounds validation"),

    _Rule("OPT-072", "syminfo.ticker in request.security (use tickerid)", "high", "Correctness",
          _detect_ticker_vs_tickerid, "PineScript syminfo.ticker vs tickerid request.security exchange prefix"),

    _Rule("OPT-073", "Redundant strategy.cancel_all/close_all calls", "medium", "Strategy perf",
          _detect_redundant_cancel, "PineScript strategy.cancel_all strategy.close_all redundant calls"),

    _Rule("OPT-074", "request.security() for lower timeframe data", "high", "Correctness",
          _detect_lower_tf_request_security, "PineScript request.security_lower_tf lower timeframe data"),

    # --- Pine Profiler: Store calculated values / Code quality ---
    _Rule("OPT-075", "Missing const for literal values", "low", "Code quality",
          _detect_missing_const, "PineScript const keyword compilation time optimization literal values"),

    _Rule("OPT-076", "Unused variable (compilation token waste)", "medium", "Code quality",
          _detect_unused_variables, "PineScript unused variable compilation token limit removal"),

    # --- PineCoders v6 patterns (OPT-077 to OPT-081) ---
    _Rule("OPT-077", "Manual cumulative sum instead of ta.cum()", "low", "Code quality",
          _detect_manual_cum, "PineScript ta.cum built-in cumulative sum replacement manual loop"),

    _Rule("OPT-078", "Multiple array.push() instead of array.from()", "low", "Code quality",
          _detect_push_loop_to_array_from, "PineScript array.from bulk initialization push optimization"),

    _Rule("OPT-079", "Manual midpoint (a+b)/2 instead of math.avg()", "low", "Code quality",
          _detect_manual_midpoint, "PineScript math.avg built-in midpoint calculation optimization"),

    _Rule("OPT-080", "Division by input without runtime.error() guard", "medium", "Correctness",
          _detect_missing_runtime_validation, "PineScript runtime.error input validation division by zero"),

    _Rule("OPT-081", "Conditional plot() without display parameter", "low", "Code quality",
          _detect_plot_display_optimization, "PineScript display.data_window hidden plot performance optimization"),

    # --- request.security() Anti-Repainting (from PineCoders HTF patterns) ---
    _Rule("OPT-082", "request.security() may repaint (no lookahead + no offset)", "high", "Correctness",
          _detect_request_security_repainting, "PineScript request.security repainting lookahead offset anti-repainting"),

    _Rule("OPT-083", "request.security() without timeframe validation", "medium", "Correctness",
          _detect_request_no_tf_validation, "PineScript request.security timeframe.in_seconds validation higher timeframe"),
]

_RULES_BY_ID: dict[str, _Rule] = {r.rule_id: r for r in _RULES}

# Severity ordering for output
_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
_SEVERITY_ICON = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def analyze_code(code: str) -> list[OptimizationResult]:
    """Run all optimization rules against PineScript code.

    Args:
        code: Complete PineScript v6 source code.

    Returns:
        List of OptimizationResult sorted by severity (critical first).
    """
    if not code or not code.strip():
        return []

    lines = code.splitlines()
    all_results: list[OptimizationResult] = []

    for rule in _RULES:
        try:
            results = rule.detect(code, lines)
            all_results.extend(results)
        except Exception as e:
            # Never let a single rule crash the analysis
            logger.debug(f"Rule {rule.rule_id} failed: {e}")
            continue

    # Sort by severity, then by line number
    all_results.sort(key=lambda r: (_SEVERITY_ORDER.get(r.severity, 99), r.line))
    return all_results


def format_results(results: list[OptimizationResult]) -> str:
    """Format optimization results into a readable report."""
    if not results:
        return (
            "OPTIMIZATION ANALYSIS — No issues found\n"
            "\u2550" * 50 + "\n"
            "Code appears to follow PineScript v6 performance best practices.\n"
            "Run Pine Profiler on TradingView to measure actual execution times."
        )

    # Count by severity
    counts: dict[str, int] = {}
    for r in results:
        counts[r.severity] = counts.get(r.severity, 0) + 1

    parts: list[str] = []
    parts.append(f"OPTIMIZATION ANALYSIS ({len(results)} issue{'s' if len(results) != 1 else ''} found)")
    parts.append("\u2550" * 50)

    for r in results:
        icon = _SEVERITY_ICON.get(r.severity, r.severity.upper())
        parts.append("")
        parts.append(f"[{icon}] {r.rule_id}: {r.name}")
        if r.line > 0:
            parts.append(f"   Line {r.line}: {r.snippet}")
        else:
            parts.append(f"   {r.snippet}")
        parts.append(f"   Fix: {r.suggestion}")

    # Summary
    parts.append("")
    summary_parts = []
    for sev in ("critical", "high", "medium", "low"):
        if counts.get(sev, 0) > 0:
            summary_parts.append(f"{counts[sev]} {sev}")
    parts.append(f"Summary: {', '.join(summary_parts)}")
    parts.append("Run Pine Profiler on TradingView to measure actual impact.")

    return "\n".join(parts)
