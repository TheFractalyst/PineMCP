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

    Runs 87 static-analysis rules (OPT-001 through OPT-090) covering all optimization
    techniques from TradingView's Pine Profiler documentation, the Limitations page,
    Repainting Prevention guide, and patterns from PineCoders' published v6 scripts.

    WHEN TO USE: After validate_syntax() confirms compilation. Before deploying to
    production. When a script is slow or exceeds runtime limits.

    NOT for: syntax errors (use validate_syntax), learning syntax (use get_function),
    or code generation (use generate_indicator/strategy).

    SEVERITY LEVELS:
      CRITICAL — Script failure, runtime errors, silent data corruption. Fix immediately.
      HIGH     — Significant performance impact or correctness risk. Fix before deploying.
      MEDIUM   — Moderate impact. Worth fixing for production code.
      LOW      — Minor optimization. Fix when convenient.

    AFTER RECEIVING RESULTS:
      1. Fix CRITICAL first, then HIGH, then MEDIUM, then LOW.
      2. Each finding has a doc_query — pass it to search_docs() for deeper guidance.
      3. For function-specific fixes, use get_function("function_name").
      4. After fixing: run validate_syntax() then optimize_code() again to verify.
      5. For real-world measurement, use TradingView's Pine Profiler (static analysis only).
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

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[optimize_code] {e}")
        raise ToolError(safe_error(e, "optimize_code"))
