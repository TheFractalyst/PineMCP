"""
mcp/tools/context.py
──────────────────────────────────────────────────────────────────────────────
CONTEXT tools (2): suggest_functions, get_namespace_cheatsheet
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

import core.db as _db
from core.db import query_async, get_all_where_async
from formatters.entry import (
    _BOX_TL, _BOX_TR, _BOX_BL, _BOX_BR, _BOX_H, _BOX_V, _BOX_MID,
)
from formatters.errors import (
    cap_response,
    check_query_error,
    circuit_breaker_msg,
    norm_ns,
    safe_error,
)

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 18: suggest_functions
# ─────────────────────────────────────────────────────────────────────────────


@tool(annotations=ToolAnnotations(title="Suggest Functions", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def suggest_functions(
    context: Annotated[str, Field(
        min_length=1,
        max_length=500,
        description="What you're trying to accomplish",
    )],
    current_line: Annotated[str | None, Field(
        default=None,
        max_length=200,
        description="The current line being written (optional)",
    )] = None,
    n_results: Annotated[int, Field(
        default=8,
        ge=1,
        le=20,
        description="How many suggestions (default 8)",
    )] = 8,
) -> str:
    """
    Given what you're trying to accomplish in PineScript, suggest the
    most relevant functions with their signatures.

    Use when user asks 'how do I...' or 'what function does...'.

    Args:
        context: What you're trying to accomplish
        current_line: The current line being written (optional)
        n_results: How many suggestions (default 8)
    """
    try:
        query_text = context
        if current_line:
            query_text += f" | current line: {current_line}"

        results = await query_async(query_text, n_results, where={"category": "function"})

        db_err = check_query_error(results)
        if db_err:
            return db_err

        if not results.get("ids") or not results["ids"][0]:
            return f"No functions found for '{context}'. Try a different search term."

        # Filter out irrelevant results (relevance < 30%)
        filtered = [
            (meta, doc, dist)
            for meta, doc, dist in zip(
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            )
            if dist < 0.7  # 30%+ relevance
        ]
        if not filtered:
            return f"No relevant functions found for '{context}'. Try a different search term."

        lines = []
        lines.append(f"SUGGESTED FUNCTIONS for '{context}':")
        lines.append("")

        for i, (meta, doc, dist) in enumerate(filtered, 1):
            name = meta.get("name", "?")
            namespace = meta.get("namespace") or ""
            syntax = meta.get("syntax") or ""
            returns = meta.get("returns") or ""
            desc = meta.get("raw_description", "")
            url = meta.get("url", "")
            ns = (
                f"{namespace}."
                if namespace and not name.startswith(f"{namespace}.")
                else ""
            )
            first_sentence = desc.split(".")[0][:100] if desc else ""

            lines.append(f"  {i}. {ns}{name}")
            if syntax:
                lines.append(f"     Syntax: {syntax[:100]}")
            if returns:
                lines.append(f"     Returns: {returns[:60]}")
            if first_sentence:
                lines.append(f"     {first_sentence}")
            if url:
                lines.append(f"     URL: {url}")
            lines.append("")

        return cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[suggest_functions] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "suggest_functions"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 19: get_namespace_cheatsheet
# ─────────────────────────────────────────────────────────────────────────────


@tool(annotations=ToolAnnotations(title="Namespace Cheatsheet", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_namespace_cheatsheet(
    namespace: Annotated[str, Field(
        min_length=1,
        max_length=50,
        description="Namespace e.g. 'ta', 'strategy'",
    )],
) -> str:
    """
    Get a compact cheatsheet for an entire namespace — all functions
    with signatures and one-line descriptions in a scannable format.
    Ideal for quick reference while coding.

    Namespaces: ta, strategy, math, array, matrix, map, str, color,
    chart, line, label, box, table, request, ticker, timeframe, syminfo
    """
    try:
        ns = norm_ns(namespace)
        if ns == "global":
            where: Optional[dict] = {"namespace": ""}
        else:
            where = {"namespace": ns}

        entries = await get_all_where_async(where)
        if not entries:
            return f"No entries found for namespace '{ns}'. Check the namespace name and try again."

        # Group by category
        groups: dict[str, list[dict]] = {}
        for entry in entries:
            cat = entry["metadata"].get("category", "unknown")
            groups.setdefault(cat, []).append(entry)

        # Sort each group alphabetically
        for cat in groups:
            groups[cat].sort(key=lambda e: e["metadata"].get("name", ""))

        total = len(entries)
        lines = []
        lines.append(f"{_BOX_TL}{_BOX_H * 60}{_BOX_TR}")
        lines.append(f"{_BOX_V} {ns.upper()} CHEATSHEET")
        lines.append(f"{_BOX_V} {total} entries | TradingView v6 docs")
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")

        category_order = [
            "function",
            "variable",
            "constant",
            "type",
            "keyword",
            "operator",
        ]
        for cat in category_order:
            if cat not in groups:
                continue
            cat_entries = groups[cat]
            lines.append(f"{_BOX_V} {cat.upper()}S ({len(cat_entries)})")
            lines.append(f"{_BOX_V}")
            for entry in cat_entries:
                meta = entry["metadata"]
                name = meta.get("name", "?")
                syntax = meta.get("syntax") or ""
                returns = meta.get("returns") or ""
                desc = meta.get("raw_description", "")
                first_sentence = desc.split(".")[0][:80] if desc else ""

                if cat == "function":
                    # Show compact signature
                    sig = syntax[:70] if syntax else name
                    ret = f" -> {returns[:25]}" if returns else ""
                    lines.append(f"{_BOX_V}   {sig}{ret}")
                else:
                    lines.append(f"{_BOX_V}   {name}")
                if first_sentence:
                    lines.append(f"{_BOX_V}     {first_sentence}")
            lines.append(f"{_BOX_V}")

        lines.append(f"{_BOX_BL}{_BOX_H * 60}{_BOX_BR}")
        lines.append(f"Total: {total} entries in namespace '{ns}'")
        return cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[get_namespace_cheatsheet] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_namespace_cheatsheet"))
