# PineScript-v6 MCP | © 2025-2026 @Fractalyst
"""
mcp/tools/search.py
──────────────────────────────────────────────────────────────────────────────
SEARCH tools (4): search_docs, get_examples, search_by_return_type,
                  list_namespace
"""

from __future__ import annotations

from typing import Annotated, Optional

import xxhash
from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

import core.db as _db
from core.db import get_all_where_async, query_async
from core.hot_cache import ensure_hot_cache
from formatters.entry import (
    _DIVIDER,
    format_examples_text,
    format_params_text,
    is_function_like,
    relevance_pct,
    source_tag,
)
from formatters.errors import (
    cap_response,
    check_query_error,
    circuit_breaker_msg,
    norm_ns,
    safe_error,
)


def _is_function_like(meta: dict) -> bool:
    """Check if an entry has function characteristics regardless of stored category."""
    return is_function_like(meta)


def _return_type_matches(query_type: str, field_type: str) -> bool:
    """Check if a query return type matches a field return type.

    Uses word-boundary-aware matching to avoid 'int' matching 'point'.
    """
    import re

    ft = field_type.lower()
    # Exact match
    if query_type == ft:
        return True
    # Query is a substring of field — check word boundary
    # e.g., "float" in "series float" ✓, "int" in "point" ✗
    if query_type in ft:
        # Check that query_type appears as a complete word in field_type
        pattern = rf"(?:^|[\s<>,\[\]]){re.escape(query_type)}(?:$|[\s<>,\[\]])"
        return bool(re.search(pattern, ft))
    # Field is a substring of query — e.g., "series float" matches query "float"
    if ft in query_type:
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1: search_docs
# ─────────────────────────────────────────────────────────────────────────────


async def _short_query_search(
    query: str,
    n_results: int,
    category_filter: str | None,
    namespace_filter: str | None,
) -> str:
    """Handle very short queries (≤3 chars) using exact name matching.

    Semantic embeddings produce poor vectors for short strings like 'ta', 'if'.
    """
    from core.db import search_by_name_async

    candidates = await search_by_name_async(query)
    if not candidates:
        return f"No results found for '{query}'. Try a longer search term."

    # Filter by category/namespace if specified
    filtered = []
    for sim, entry in candidates:
        meta = entry["metadata"]
        if category_filter and meta.get("category") != category_filter:
            continue
        if namespace_filter and meta.get("namespace") != namespace_filter:
            continue
        filtered.append((sim, entry))

    if not filtered:
        return f"No results found for '{query}' with the given filters."

    output_lines: list[str] = []
    for i, (sim, entry) in enumerate(filtered[:n_results]):
        meta = entry["metadata"]
        name = meta.get("name", "?")
        category = meta.get("category", "?").upper()
        namespace = meta.get("namespace") or ""
        ns = (
            f"{namespace}."
            if namespace and not name.startswith(f"{namespace}.")
            else ""
        )
        desc = meta.get("raw_description", "")
        first_para = desc.split("\n\n")[0][:200] if desc else ""

        output_lines.append(_DIVIDER)
        output_lines.append(f"[{i + 1}] {ns}{name} | {category} | Match: {sim:.0f}%")
        if first_para:
            output_lines.append(f"  {first_para}")

    output_lines.append(_DIVIDER)
    return cap_response("\n".join(output_lines))


@tool(
    annotations=ToolAnnotations(
        title="Search PineScript Docs",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def search_docs(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="Natural language or code query about PineScript v6",
        ),
    ],
    n_results: Annotated[
        int,
        Field(
            default=5,
            ge=1,
            le=30,
            description="Number of results (1-30, default 5)",
        ),
    ] = 5,
    category_filter: Annotated[
        str | None,
        Field(
            default=None,
            max_length=50,
            description="'function','variable','type',etc.",
        ),
    ] = None,
    namespace_filter: Annotated[
        str | None,
        Field(
            default=None,
            max_length=50,
            description="Namespace e.g. 'ta', 'strategy'",
        ),
    ] = None,
) -> str:
    """
    Semantic search across the complete PineScript v6 knowledge base.
    Searches functions, variables, types, constants, keywords, and operators.

    Args:
        query: Natural language or code query about PineScript v6
        n_results: Number of results (1-30, default 5)
        category_filter: Filter by entry type ('function','variable',etc.)
        namespace_filter: Filter by namespace (e.g. 'ta', 'strategy')
    """
    try:
        query = query.strip()
        await ensure_hot_cache()

        # Short query fallback: semantic embeddings perform poorly on 1-3 char
        # queries. Use exact name matching instead.
        if len(query) <= 3:
            return await _short_query_search(
                query, n_results, category_filter, namespace_filter
            )

        where_clauses: list[dict] = []
        if category_filter:
            where_clauses.append({"category": category_filter})
        if namespace_filter:
            where_clauses.append({"namespace": namespace_filter})

        where: Optional[dict] = None
        if len(where_clauses) == 1:
            where = where_clauses[0]
        elif len(where_clauses) > 1:
            where = {"$and": where_clauses}

        results = await query_async(query, n_results, where=where)

        # Supplementary search: if filtering by 'function', also find function-like
        # entries stored under other categories (e.g. strategy.closedtrades.profit
        # is stored as 'variable' but has function syntax with parameters)
        if category_filter == "function" and results["ids"] and results["ids"][0]:
            supp_where = {"namespace": namespace_filter} if namespace_filter else None
            supp = await query_async(query, n_results, where=supp_where)
            if supp["ids"] and supp["ids"][0]:
                existing_ids = set(results["ids"][0])
                for rid, meta, doc, dist in zip(
                    supp["ids"][0],
                    supp["metadatas"][0],
                    supp["documents"][0],
                    supp["distances"][0],
                ):
                    if (
                        rid not in existing_ids
                        and meta.get("category") != "function"
                        and _is_function_like(meta)
                    ):
                        # Override category so display shows FUNCTION, not VARIABLE
                        meta = {**meta, "category": "function"}
                        results["ids"][0].append(rid)
                        results["metadatas"][0].append(meta)
                        results["documents"][0].append(doc)
                        results["distances"][0].append(dist)
                        existing_ids.add(rid)

        # Cap total results to n_results after supplementary additions
        if results["ids"] and results["ids"][0] and len(results["ids"][0]) > n_results:
            for key in ("ids", "metadatas", "documents", "distances"):
                results[key][0] = results[key][0][:n_results]

        db_err = check_query_error(results)
        if db_err:
            return db_err

        if not results["ids"] or not results["ids"][0]:
            return f"No results found for '{query}'. Try broadening your search terms."

        # FIX 4: Add content-based deduplication
        seen_hashes = set()
        deduped_results = []

        for i, (rid, meta, doc, dist) in enumerate(
            zip(
                results["ids"][0],
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            )
        ):
            # Dedup: skip if content is >85% similar to already shown result
            # Use xxhash for deterministic dedup (Python hash() is randomized per process)
            content_key = doc[:120].strip().lower()
            content_hash = xxhash.xxh64(content_key.encode()).intdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            # Relevance gate: skip results below 30% relevance
            if dist < 0.7:
                deduped_results.append((rid, meta, doc, dist))

        if not deduped_results:
            return f"No results found for '{query}'. Try broadening your search terms."

        output_lines: list[str] = []
        for i, (rid, meta, doc, dist) in enumerate(deduped_results):
            name = meta.get("name", "?")
            category = meta.get("category", "?").upper()
            namespace = meta.get("namespace") or ""
            ns = (
                f"{namespace}."
                if namespace and not name.startswith(f"{namespace}.")
                else ""
            )
            rel = relevance_pct(dist)
            tag = source_tag(meta)

            output_lines.append(_DIVIDER)
            output_lines.append(f"[{i + 1}] {ns}{name} | {category} | Relevance: {rel}")
            output_lines.append(f"  {tag}")

            desc = meta.get("raw_description", "")
            if desc:
                first_para = desc.split("\n\n")[0]
                snippet = (
                    first_para[:300] + "..." if len(first_para) > 300 else first_para
                )
                output_lines.append(f"  {snippet}")

            param_text = format_params_text(meta)
            if param_text:
                output_lines.append("  " + param_text.split("\n")[0])

            returns = meta.get("returns") or ""
            if returns:
                output_lines.append(f"  RETURNS: {returns[:120]}")

            ex_count = meta.get("example_count", 0)
            if ex_count:
                output_lines.append(f"  Examples: {ex_count}")

        output_lines.append(_DIVIDER)
        return cap_response("\n".join(output_lines))

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[search_docs] {e}")
        if "ChromaDB" in str(e) or _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "search_docs"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 8: get_examples
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Get Code Examples",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def get_examples(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="Concept to find code examples for",
        ),
    ],
    n_results: Annotated[
        int,
        Field(
            default=4,
            ge=1,
            le=20,
            description="Number of examples to return",
        ),
    ] = 4,
) -> str:
    """
    Find real PineScript v6 code examples by concept.
    Returns complete, runnable code blocks from the official docs.

    Use for: "how to use strategy.entry with stop loss",
             "array iteration example", "drawing lines example"
    """
    try:
        query = query.strip()
        results = await query_async(query, n_results, where={"has_examples": 1})
        db_err = check_query_error(results)
        if db_err:
            return db_err
        if not results["ids"] or not results["ids"][0]:
            return f"No examples found for '{query}'. Try a different search term."

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
            return f"No relevant examples found for '{query}'. Try a different search term."

        output_lines: list[str] = []
        for i, (meta, doc, dist) in enumerate(filtered):
            name = meta.get("name", "?")
            category = meta.get("category", "?").upper()
            namespace = meta.get("namespace") or ""
            ns = (
                f"{namespace}."
                if namespace and not name.startswith(f"{namespace}.")
                else ""
            )
            rel = relevance_pct(dist)

            output_lines.append(_DIVIDER)
            output_lines.append(
                f"EXAMPLES from: {ns}{name} ({category}) — Relevance: {rel}"
            )

            ex_text = format_examples_text(meta)
            if ex_text:
                output_lines.append(ex_text)
            else:
                output_lines.append(
                    "  (Examples referenced but not stored in metadata)"
                )

        output_lines.append(_DIVIDER)
        return cap_response("\n".join(output_lines))

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[get_examples] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_examples"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 9: list_namespace
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="List Namespace Members",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def list_namespace(
    namespace: Annotated[
        str,
        Field(
            min_length=1,
            max_length=50,
            description="Namespace e.g. 'ta', 'strategy'",
        ),
    ],
    category_filter: Annotated[
        str | None,
        Field(
            default=None,
            max_length=50,
            description="Optional category filter",
        ),
    ] = None,
) -> str:
    """
    List ALL members of a PineScript v6 namespace.
    Returns every function, variable, and constant in the namespace
    with one-line descriptions.

    Namespaces: ta, strategy, math, array, matrix, map, str, color,
    chart, line, label, box, table, request, ticker, timeframe,
    syminfo, input, runtime, polyline (and 'global' for un-namespaced)
    """
    try:
        ns = norm_ns(namespace)
        if ns.lower() == "global":
            where: Optional[dict] = {"namespace": ""}
        else:
            where = {"namespace": ns}

        if category_filter:
            # ChromaDB requires $and for multiple conditions
            where = {
                "$and": [
                    {"namespace": ns if ns.lower() != "global" else ""},
                    {"category": category_filter},
                ]
            }

        entries = await get_all_where_async(where)
        if not entries:
            return f"No entries found for namespace '{ns}'. Check the namespace name and try again."

        # Group by category
        groups: dict[str, list[dict]] = {}
        for entry in entries:
            cat = entry["metadata"].get("category", "unknown")
            groups.setdefault(cat, []).append(entry)

        output_lines: list[str] = []
        output_lines.append(f"NAMESPACE: {ns} ({len(entries)} entries)")
        output_lines.append("")

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
            cat_entries = sorted(
                groups[cat], key=lambda e: e["metadata"].get("name", "")
            )
            output_lines.append(f"{cat.upper()}S ({len(cat_entries)}):")

            for entry in cat_entries:
                meta = entry["metadata"]
                name = meta.get("name", "?")
                syntax = meta.get("syntax") or ""
                returns = meta.get("returns") or ""
                desc = meta.get("raw_description", "")
                first_sentence = desc.split(".")[0][:100] if desc else ""

                if cat == "function":
                    # Show signature summary
                    sig = syntax[:80] if syntax else name
                    ret = f" -> {returns[:30]}" if returns else ""
                    output_lines.append(f"  {sig}{ret}")
                else:
                    desc_short = f" — {first_sentence}" if first_sentence else ""
                    output_lines.append(f"  {name}{desc_short}")

            output_lines.append("")

        # Show any categories not in the standard order (e.g., 'annotation', 'example')
        shown_cats = set(category_order)
        remaining = {k: v for k, v in groups.items() if k not in shown_cats}
        if remaining:
            for cat in sorted(remaining):
                cat_entries = sorted(
                    remaining[cat], key=lambda e: e["metadata"].get("name", "")
                )
                output_lines.append(f"{cat.upper()}S ({len(cat_entries)}):")
                for entry in cat_entries:
                    name = entry["metadata"].get("name", "?")
                    output_lines.append(f"  {name}")
                output_lines.append("")

        output_lines.append(f"Total: {len(entries)} entries in namespace '{ns}'")
        return cap_response("\n".join(output_lines))

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[list_namespace] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "list_namespace"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 10: search_by_return_type
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Search by Return Type",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def search_by_return_type(
    return_type: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Return type e.g. 'series float', 'line', 'array<int>'",
        ),
    ],
    n_results: Annotated[
        int,
        Field(
            default=10,
            ge=1,
            le=50,
            description="Number of results (1-50, default 10)",
        ),
    ] = 10,
) -> str:
    """
    Find all PineScript v6 functions that return a specific type.
    Useful when you know what type you need but not which function to use.

    Examples: search_by_return_type("series float"),
              search_by_return_type("line"),
              search_by_return_type("array<int>")
    """
    try:
        return_type = return_type.strip()
        # Search docs for functions that return this type
        # Fetch more candidates than requested — the returns-field filter will narrow down
        query = f"function returns {return_type}"
        fetch_count = max(n_results * 5, 50)
        results = await query_async(query, fetch_count, where={"category": "function"})
        db_err = check_query_error(results)
        if db_err:
            return db_err

        if not results.get("ids") or not results["ids"][0]:
            return f"No functions found that return '{return_type}'."

        # Filter: only show results where 'returns' metadata matches
        matched = []
        all_results = list(
            zip(
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            )
        )

        rt_lower = return_type.lower()
        direct_matches = []
        semantic_matches = []
        for meta, doc, dist in all_results:
            returns_field = (meta.get("returns") or "").lower()
            # Guard: skip empty returns fields — they would falsely match anything
            # Use word-boundary check to avoid "int" matching "point"
            if returns_field and _return_type_matches(rt_lower, returns_field):
                direct_matches.append((meta, doc, dist, True))
            elif dist < 0.5:
                semantic_matches.append((meta, doc, dist, False))

        has_direct = len(direct_matches) > 0

        # Build matched list: prefer direct, supplement with close semantic hits
        if has_direct:
            matched = direct_matches + [s for s in semantic_matches if s[2] < 0.4]
        else:
            # No direct returns-field match — show semantic results with a warning
            matched = [
                (m, d, dist, False) for m, d, dist in all_results[:5] if dist < 0.6
            ]

        if not matched:
            return (
                f"No functions found that return '{return_type}'. "
                f"Try a known type: 'series float', 'bool', 'line', 'label', 'array<int>', 'color'."
            )

        if has_direct:
            header = f"FUNCTIONS RETURNING '{return_type}':\nFound {len(matched)} candidate(s)"
        else:
            header = (
                f"No exact return-type match found for '{return_type}'.\n"
                f"Showing {len(matched)} semantically related function(s):"
            )
        output_lines = [header, ""]

        for meta, doc, dist, is_direct in matched:
            name = meta.get("name", "?")
            syntax = meta.get("syntax") or name
            returns = meta.get("returns") or ""
            namespace = meta.get("namespace") or ""
            ns = (
                f"{namespace}."
                if namespace and not name.startswith(f"{namespace}.")
                else ""
            )
            match_type = (
                "direct match" if is_direct else f"semantic ({relevance_pct(dist)})"
            )

            output_lines.append(f"  {ns}{name}")
            if returns:
                output_lines.append(f"    Returns: {returns[:100]}")
            if syntax:
                output_lines.append(f"    Syntax: {syntax[:100]}")
            output_lines.append(f"    ({match_type})")
            output_lines.append("")

        return cap_response("\n".join(output_lines))

    except Exception as e:
        logger.error(f"[search_by_return_type] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "search_by_return_type"))
