# PineScript-v6 MCP | © 2025-2026 @Fractalyst
# ruff: noqa: E501
"""
tools/optimization.py
──────────────────────────────────────────────────────────────────────────────
OPTIMIZE tool (1): optimize_code
"""

from __future__ import annotations

from typing import Annotated

from fastmcp.tools import tool
from mcp.types import ToolAnnotations
from pydantic import Field

from core.optimizer import analyze_code, format_results


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

    Detects 55 issues from TradingView's official profiling & optimization docs:
    - Request/TA call waste: duplicate request.security(), exceeding call limits
    - Drawing inefficiency: delete+recreate vs setters, unprotected updates, display limits
    - Loop waste: reimplemented built-ins, loop-invariant code, indexof in loops
    - Memory/buffer: missing max_bars_back, unbounded arrays, oversized buffers, map limits
    - Correctness traps: ta.*() in local scopes, missing var, varip repainting, future leak
    - Repainting: lookahead without offset, timenow, barstate.isnew, missing isconfirmed
    - Resource limits: plot count, token count, scope variable count, timeouts
    - Strategy perf: calc_on_order_fills overhead, date filters, order limits, non-standard charts
    - Code quality: unused imports, code duplication, oversized scripts, lower TF requests

    Returns a line-by-line report with severity ratings and fix suggestions.
    This tool does NOT modify your code — it only analyzes and reports.

    Use this AFTER validate_syntax confirms your code compiles, to check for
    performance issues that don't cause compilation errors.
    """
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

    return report
