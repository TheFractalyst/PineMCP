# PineScript-v6 MCP | © 2025-2026 @Fractalyst
# ruff: noqa: E501
"""
tools/optimization.py
──────────────────────────────────────────────────────────────────────────────
OPTIMIZE tool (1): optimize_code
"""

from __future__ import annotations

from typing import Annotated

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

from core.optimizer import analyze_code, format_results
from formatters.errors import cap_response, safe_error


@tool(
    annotations=ToolAnnotations(
        title="Optimize PineScript Code",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    ),
)
async def optimize_code(
    code: Annotated[str, Field(
        description="Complete PineScript v6 source code to analyze for performance anti-patterns. "
                    "Can be a full script or a code snippet.",
        min_length=10,
        max_length=50000,
    )],
) -> str:
    """Analyze PineScript v6 code for performance anti-patterns and optimization opportunities.

    Runs 73 static-analysis rules (OPT-001 through OPT-076) covering ALL
    optimization techniques from TradingView's Pine Profiler documentation,
    plus the Limitations page, Repainting Prevention guide, Style Guide,
    and Other Timeframes page.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    WHEN TO USE
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    - After validate_syntax() confirms your code compiles cleanly
    - Before deploying scripts to production or publishing to TradingView
    - When a script is slow on historical bars or exceeds runtime limits
    - To audit code quality and catch non-obvious performance traps

    NOT for: syntax errors (use validate_syntax), learning syntax (use get_function),
    or code generation (use generate_indicator/strategy).

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    PINE PROFILER TECHNIQUE → RULE MAP
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    These are the 9 optimization techniques from TradingView's Pine Profiler
    documentation, mapped to the specific rules that detect violations:

    1. USE BUILT-INS          → OPT-001 (manual loops vs ta.highest/sma),
                                OPT-007 (loop when loop-free builtin exists),
                                OPT-040 (manual array.get loop vs for...in)
    2. REDUCE REPETITION      → OPT-002 (3+ identical calls → cache in variable),
                                OPT-043 (repeated code → extract to function),
                                OPT-061 (dead functions consuming tokens)
    3. MINIMIZE REQUEST CALLS → OPT-003 (duplicate request.security → tuple),
                                OPT-015 (approaching 40-call limit),
                                OPT-035 (returning arrays from requests),
                                OPT-039 (unused request result),
                                OPT-041 (missing calc_bars_count),
                                OPT-057 (request in loop with variable args),
                                OPT-074 (lower-TF request.security misuse)
    4. AVOID REDRAWING        → OPT-004 (delete+recreate → use setters),
                                OPT-013 (na coordinates waste drawing slots)
    5. REDUCE DRAWING UPDATES → OPT-005 (wrap in barstate.islast),
                                OPT-038 (table creation → barstate.isfirst),
                                OPT-055 (missing max_*_count → only 50 shown)
    6. STORE CALCULATED VALUES→ OPT-030 (missing var for cross-bar accumulation),
                                OPT-066 (color.new every bar → var),
                                OPT-068 (unnecessary var for always-overwritten),
                                OPT-075 (literal values → const keyword)
    7. ELIMINATE LOOPS        → OPT-001 (loop vs ta.highest/lowest/sma),
                                OPT-007 (algebraic simplification)
    8. OPTIMIZE LOOPS         → OPT-006 (invariant calc inside loop),
                                OPT-008 (array.indexof in for...in),
                                OPT-009 (array.min/max inside loop),
                                OPT-064 (array.insert prepend O(n)),
                                OPT-067 (push in fixed loop → pre-allocate)
    9. MINIMIZE BUFFERS       → OPT-010 (missing max_bars_back),
                                OPT-011 (oversized buffer),
                                OPT-012 (missing calc_bars_count),
                                OPT-020 (unbounded array growth),
                                OPT-021 (deep history >5000 bars)

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    PLATFORM LIMITS (all enforced by specific rules)
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    plots=64 | drawings=500/type | polylines=100 | tables=9
    request.*()=40 unique | tuples=127 elements | collections=100K
    map=50K pairs | history=5000 bars | orders=9K backtest
    loop=500ms/bar | execution=20s basic/40s pro | compile=2min
    tokens=100K | vars=1000/scope | request_size=5MB

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    COMMON OPTIMIZATION TRANSFORMATIONS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    Apply these patterns when fixing findings:

    1. MANUAL LOOP → BUILT-IN:
       BAD:  for i = 1 to length - 1 / sum += source[i]
       GOOD: math.sum(source, length)

    2. REPEATED CALL → VARIABLE:
       BAD:  ta.ema(close, 20) used on 3+ lines
       GOOD: ema20 = ta.ema(close, 20) // declare once, reference everywhere

    3. DELETE+RECREATE → SETTER:
       BAD:  label.delete(lbl) / lbl := label.new(x, y, text)
       GOOD: label.set_xy(lbl, x, y) / label.set_text(lbl, text)

    4. GLOBAL DRAWING → ISLAST GUARD:
       BAD:  table.cell(tbl, 0, 0, str.tostring(close)) // runs every bar
       GOOD: if barstate.islast / table.cell(tbl, 0, 0, str.tostring(close))

    5. LITERAL VALUE → CONST:
       BAD:  color BULL_COLOR = color.green
       GOOD: const color BULL_COLOR = color.green // computed once at compile

    6. DUPLICATE REQUESTS → TUPLE:
       BAD:  h = request.security(syminfo.tickerid, "D", high[1], lookahead=barmerge.lookahead_on)
             l = request.security(syminfo.tickerid, "D", low[1], lookahead=barmerge.lookahead_on)
       GOOD: [h, l] = request.security(syminfo.tickerid, "D", [high[1], low[1]], lookahead=barmerge.lookahead_on)

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    SEVERITY LEVELS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    CRITICAL — Script failure, runtime errors, or silent data corruption.
                Fix immediately. Examples: exceeding limits, future data leak.
    HIGH     — Significant performance impact or correctness risk on real-time.
                Fix before deploying. Examples: ta.*() in local scope, missing var.
    MEDIUM   — Moderate impact. Worth fixing for production code.
                Examples: missing input bounds, code duplication.
    LOW      — Minor optimization. Fix when convenient.
                Examples: unnecessary var, missing const.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    AFTER RECEIVING RESULTS
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    1. Fix CRITICAL findings first, then HIGH, then MEDIUM, then LOW.
    2. Each finding has a `doc_query` — pass it to search_docs() for deeper guidance.
    3. For function-specific fixes, use get_function("function_name").
    4. After applying fixes:
       a. Run validate_syntax() to confirm compilation.
       b. Run optimize_code() again to verify issues are resolved.
    5. For real-world performance measurement, use TradingView's Pine Profiler
       (this tool is static analysis only — it cannot measure actual execution time).
    """
    try:
        # Run static analysis
        results = analyze_code(code)

        # Format the report
        report = format_results(results)

        # Add doc lookup suggestions for each finding (lightweight — just query hints)
        if results:
            report += "\n\n"
            report += "DOCUMENTATION LOOKUP QUERIES:\n"
            report += "Use search_docs() or get_examples() with these queries for detailed fix guidance:\n"
            seen_queries: set[str] = set()
            for r in results:
                if r.doc_query not in seen_queries:
                    report += f"  - \"{r.doc_query}\"\n"
                    seen_queries.add(r.doc_query)

        return cap_response(report)

    except Exception as e:
        logger.error(f"[optimize_code] {e}")
        raise ToolError(safe_error(e, "optimize_code"))
