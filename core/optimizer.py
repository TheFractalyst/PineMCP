# ruff: noqa: E501
"""
core/optimizer.py
──────────────────────────────────────────────────────────────────────────────
Static analysis engine for PineScript v6 performance optimization.

28 detection rules (OPT-001 through OPT-032; 4 runtime-only issues not
statically detectable).

All rules use regex-based detection (fast, deterministic, <50ms per analysis).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

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
    """Remove // comments from a line (naive — doesn't handle strings)."""
    idx = line.find("//")
    if idx >= 0:
        return line[:idx]
    return line


def _count_in_scope(code: str, pattern: re.Pattern[str]) -> int:
    """Count non-comment matches across entire code."""
    count = 0
    for line in code.splitlines():
        if _strip_comments(line).strip():
            count += len(pattern.findall(_strip_comments(line)))
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
# Detection rules — 32 anti-patterns
# ─────────────────────────────────────────────────────────────────────────────

def _detect_reimplemented_builtins(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-001: Reimplementing built-in functions (ta.highest/lowest/sma/etc) with loops."""
    results: list[OptimizationResult] = []
    # Look for functions that iterate source[i] in a for loop and accumulate
    # This catches patterns like: for i = 1 to length - 1 / result := math.max(result, source[i])
    func_pattern = re.compile(r"for\s+\w+\s*=\s*\d+\s+to\s+length\b")
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
    has_islast_guard = "barstate.islast" in code

    # Check for var-declared table/box/line/label with setters outside islast guard
    var_draw_pattern = re.compile(r"var\s+(table|box|line|label)\s+\w+")
    has_var_drawing = var_draw_pattern.search(code) is not None

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
    loop_pattern = re.compile(r"^\s*for\s+\w+\s*=")
    invariant_funcs = re.compile(r"(math\.(cos|sin|sqrt|log|exp|pow)|array\.(min|max|range|size))\s*\(")
    in_loop = False
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if loop_pattern.match(lines[i]):
            in_loop = True
        elif in_loop and stripped and not stripped.startswith(("for ", "if ", "else", "//")):
            # Check if line has invariant function calls
            if invariant_funcs.search(stripped) and "i" not in stripped.split("(")[0]:
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
    has_max_bars_back = "max_bars_back(" in code
    has_islast = "barstate.islast" in code

    if has_islast and not has_max_bars_back:
        # Look for history references inside islast blocks
        for i, line in enumerate(lines):
            stripped = _strip_comments(line).strip()
            if re.search(r"\w+\[\d+\]", stripped) and re.search(r"\[4\d{2,}\]", stripped):
                results.append(_result(
                    _RULES_BY_ID["OPT-010"], i + 1,
                    stripped[:100],
                    "Add `max_bars_back(varName, N)` before the history reference to avoid "
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
    has_islast_heavy = code.count("barstate.islast") >= 1
    has_calc_bars_count = "calc_bars_count" in code

    if has_islast_heavy and not has_calc_bars_count:
        # Only suggest if there's drawing-heavy islast logic
        if re.search(r"(table|box|line|label)\.(new|cell_set|set_)", code):
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
    na_coord_pattern = re.compile(r"(label|box|line)\.new\s*\(.*\?.*:.*\bna\b")
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
    plot_funcs = re.findall(r"\b(plot|plotarrow|plotbar|plotcandle|plotchar|plotshape|bgcolor|barcolor)\s*\(", code)
    count = len(plot_funcs)
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
    req_calls = re.findall(r"request\.\w+\s*\(", code)
    unique_calls = set(req_calls)
    if len(req_calls) > 35:
        results.append(_result(
            _RULES_BY_ID["OPT-015"], 0,
            f"Found {len(req_calls)} request.*() calls ({len(unique_calls)} unique)",
            f"Approaching the 40 unique request.*() call limit ({len(req_calls)} total, "
            f"{len(unique_calls)} unique). Consolidate using tuple requests or reduce calls."
        ))
    return results


def _detect_tuple_limit(code: str, lines: list[str]) -> list[OptimizationResult]:
    """OPT-016: Exceeding 127 tuple elements in request.*()."""
    results: list[OptimizationResult] = []
    tuple_pattern = re.compile(r"\[([^\]]{50,})\]\s*=\s*request\.\w+\s*\(")
    for m in tuple_pattern.finditer(code):
        elements = m.group(1).split(",")
        if len(elements) > 100:
            results.append(_result(
                _RULES_BY_ID["OPT-016"], code[:m.start()].count("\n") + 1,
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
    has_shift = "array.shift(" in code or "array.pop(" in code
    has_fixed_size = "array.new<" in code and re.search(r"array\.new<\w+>\s*\(\s*\d+", code)

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
            if re.search(r"(line|box)\.(new|set_)", stripped):
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

        if re.match(r"^(if|for|while|switch)\b", stripped):
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
                    if var in stripped:
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
    # Collect varip variable names assigned inside barstate.isrealtime/isnew blocks
    varip_vars: set[str] = set()
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
            # varip assignment: varip float x or x := close (where x was declared varip)
            m_assign = re.match(r"(\w+)\s*:=\s*", stripped)
            if m_assign:
                varip_vars.add(m_assign.group(1))

    if not varip_vars:
        return results

    # Check if any varip variable feeds into a plot call
    plot_pattern = re.compile(r"\b(plot|plotcandle|plotchar|plotshape|plotarrow|plotbar)\s*\(")
    for i, line in enumerate(lines):
        stripped = _strip_comments(line).strip()
        if plot_pattern.search(stripped):
            for var in varip_vars:
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
        except Exception:
            # Never let a single rule crash the analysis
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
