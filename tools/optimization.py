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
from pydantic import Field

from core.optimizer import analyze_code, format_results


@tool(
    annotations={
        "readOnlyHint": True,
        "openWorldHint": False,
    },
)
async def optimize_code(
    code: Annotated[str, Field(
        description="Complete PineScript v6 source code to analyze for performance anti-patterns. "
                    "Can be a full script or a code snippet.",
        min_length=10,
    )],
) -> str:
    """Analyze PineScript v6 code for performance anti-patterns and optimization opportunities.

    Detects 32 issues from TradingView's official profiling & optimization docs:
    - Request/TA call waste: duplicate request.security(), exceeding call limits
    - Drawing inefficiency: delete+recreate vs setters, unprotected updates
    - Loop waste: reimplemented built-ins, loop-invariant code, indexof in loops
    - Memory/buffer: missing max_bars_back, unbounded arrays, oversized buffers
    - Correctness traps: ta.*() in local scopes, missing var, varip repainting
    - Resource limits: plot count, token count, scope variable count, timeouts
    - Strategy perf: calc_on_order_fills overhead

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
