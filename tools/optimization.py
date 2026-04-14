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

    Runs 71 static-analysis rules (OPT-001 through OPT-074) covering TradingView's
    Pine Profiler documentation, Limitations page, Repainting Prevention guide,
    Style Guide, and Other Timeframes page.

    WHEN TO USE:
      - After validate_syntax() confirms your code compiles cleanly
      - Before deploying scripts to production or publishing to TradingView
      - When a script is slow on historical bars or exceeds runtime limits
      - To audit code quality and catch non-obvious performance traps

    WHEN NOT TO USE:
      - For syntax errors or compilation failures — use validate_syntax() or
        validate_and_explain() instead
      - To learn PineScript syntax — use get_function(), get_keyword(), or search_docs()
      - For code generation — use generate_indicator() or generate_strategy()

    CATEGORIES OF ISSUES DETECTED (71 rules across 9 categories):

    1. LOOP WASTE (10 rules: OPT-001/002/006/007/008/009/040/064/066/069)
       Reimplemented built-ins with loops, repeated identical calls,
       loop-invariant calculations, manual array.get() loops, array.prepend,
       color.new() recomputed per bar, matrix ops in loops.

    2. DRAWING WASTE (6 rules: OPT-004/005/013/038/055/059)
       Delete+recreate vs setters, unprotected historical-bar updates,
       na coordinates wasting slots, table creation every bar,
       missing max_*_count, x-coordinate beyond limits.

    3. REQUEST/TA WASTE (6 rules: OPT-003/035/039/041/057/074)
       Duplicate request.security() calls, collections returned from requests,
       unused request results, missing calc_bars_count, request.*() in loops,
       lower-timeframe request.security misuse.

    4. MEMORY/BUFFER (7 rules: OPT-010/011/012/020/021/056/067/068)
       Missing max_bars_back, oversized buffers, unbounded array growth,
       deep history references, map populated in loops, fixed-size push
       without pre-allocation, unnecessary var overhead.

    5. RESOURCE LIMITS (10 rules: OPT-014/015/016/017/018/036/042/043/045/047/048/065)
       Plot count (64), request.*() calls (40), tuple elements (127),
       token limit, variable count (1000), table count (9), drawing IDs (500),
       polyline IDs (100), script size (5MB), dead plots, unused imports,
       code duplication.

    6. CORRECTNESS (11 rules: OPT-026/027/028/029/030/031/033/034/049/050/052/070/071/072)
       History refs on local-scope vars, ta.*() in local scopes, varip repainting,
       realtime tick repaint, missing var, buffer mismatches, var in loop headers,
       variable shadowing, lookahead future leak, timenow inconsistency,
       missing isconfirmed, input.*() in local scope, unbounded input.int,
       syminfo.ticker vs tickerid.

    7. REPAINTING (4 rules: OPT-028/029/051/054)
       varip plotted output, realtime tick+plot, barstate.isnew signal repaint,
       request.security_lower_tf() inconsistency.

    8. STRATEGY PERF (6 rules: OPT-032/037/044/046/053/073)
       calc_on_order_fills overhead, missing date filters, order count limits,
       calc_on_every_tick overhead, non-standard chart data, redundant
       cancel_all/close_all calls.

    9. CODE QUALITY (4 rules: OPT-060/061/062/063)
       Long if/else chains (use switch), dead functions, string concat in loops,
       str.tostring/str.format in loops.

    SEVERITY LEVELS:
      CRITICAL — Will cause script failure, runtime errors, or silent data corruption.
                  Fix immediately. Examples: exceeding plot/request limits, future leak.
      HIGH     — Significant performance impact or correctness risk on real-time bars.
                  Fix before deploying. Examples: ta.*() in local scope, missing var.
      MEDIUM   — Moderate impact. Worth fixing for production code.
                  Examples: missing input bounds, code duplication.
      LOW      — Minor optimization. Fix when convenient.
                  Examples: unnecessary var, color.new recomputation.

    HOW TO INTERPRET RESULTS:
      Each finding includes: rule ID, severity, line number, code snippet,
      and a specific fix suggestion. The report also provides search_docs()
      queries you can use to get detailed documentation for each issue.

    NEXT STEPS AFTER ANALYSIS:
      - For function-specific fixes: use get_function("function_name")
      - For pattern examples: use get_examples("concept description")
      - After applying fixes: re-run validate_syntax() to confirm compilation
      - Then re-run optimize_code() to verify issues are resolved
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
