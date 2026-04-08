"""
mcp/tools/lookup.py
──────────────────────────────────────────────────────────────────────────────
LOOKUP tools (6): get_function, get_variable, get_type, get_constant,
                  get_keyword, get_operator
"""

from __future__ import annotations

from typing import Annotated

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

import core.db as _db
from core.db import (
    query_async,
    search_by_name_async,
    get_all_where_async,
    get_by_names_async,
    get_type_by_name_async,
)
from core.hot_cache import cache_lookup, ensure_hot_cache
from formatters.entry import format_entry_detail
from formatters.errors import (
    safe_error,
    circuit_breaker_msg,
    check_query_error,
    norm_name,
)

# ─────────────────────────────────────────────────────────────────────────────
# Generic lookup helper
# ─────────────────────────────────────────────────────────────────────────────


def _is_function_like(meta: dict) -> bool:
    """Check if an entry has function characteristics regardless of stored category.

    Many TradingView entries are scraped as 'variable' but take parameters
    (e.g. strategy.closedtrades.profit(trade_num), request.security(...)).
    """
    syntax = meta.get("syntax") or ""
    has_parens = "(" in syntax and ")" in syntax
    has_params = bool(meta.get("raw_parameters"))
    return has_parens or has_params


def _pick_best_version(result: dict) -> tuple:
    """From multiple DB entries with the same name, pick the best version.

    Priority:
    1. Function-like entries (have syntax with parens or params) — override to FUNCTION
    2. Entries with syntax (over hollow entries)
    3. Entries with longer docs
    """
    candidates = list(zip(result["metadatas"], result["documents"]))

    # First pass: look for function-like entries (syntax with parens or params)
    for meta, doc in candidates:
        if _is_function_like(meta):
            meta = {**meta, "category": "function"}
            return meta, doc

    # Second pass: prefer entries with syntax (non-hollow)
    for meta, doc in candidates:
        syntax = meta.get("syntax") or ""
        if syntax:
            # Keep original category — it might be constant, type, etc.
            return meta, doc

    # Third pass: pick the one with longest doc
    if candidates:
        best = max(candidates, key=lambda x: len(x[1]) if x[1] else 0)
        return best[0], best[1]

    return None, None


async def _lookup_entry(name: str, category: str) -> str:
    """Lookup an entry by name and category. Returns formatted string or error."""
    try:
        await ensure_hot_cache()
        name_preserved = name.strip()
        name_lower = name.lower().strip()

        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(name)
        if cached:
            cached_cat = cached["metadata"].get("category")
            cached_syntax = cached["metadata"].get("syntax") or ""
            # Skip cache if wrong category OR hollow entry (no syntax)
            if (category and cached_cat != category) or not cached_syntax:
                pass  # fall through to name search
            else:
                result = format_entry_detail(
                    cached["metadata"].get("name", name),
                    cached["metadata"],
                    cached["document"],
                )
                return result

        # Step 0.5: Exact name match across all categories — pick best version
        # (handles entries stored with wrong category, e.g. constant stored as function)
        try:
            name_variants = list({name_preserved, name_lower})
            all_versions = await get_by_names_async(name_variants)
            if all_versions["ids"]:
                # Find the version matching the requested category
                for meta, doc in zip(
                    all_versions["metadatas"], all_versions["documents"]
                ):
                    if not category or meta.get("category") == category:
                        return format_entry_detail(meta.get("name", name), meta, doc)
                # If no exact category match, pick the best version
                best_meta, best_doc = _pick_best_version(all_versions)
                if best_meta:
                    return format_entry_detail(
                        best_meta.get("name", name), best_meta, best_doc
                    )
        except Exception as e:
            logger.debug(f"Cross-category lookup failed: {e}")

        # Step 1: Try exact fuzzy match within category
        candidates = await search_by_name_async(
            name, where={"category": category} if category else None
        )

        if candidates and candidates[0][0] >= 85:
            best_sim, best_entry = candidates[0]
            return format_entry_detail(
                best_entry["metadata"].get("name", name),
                best_entry["metadata"],
                best_entry["document"],
            )

        # Step 2: Semantic search within category
        results = await query_async(
            name, 5, where={"category": category} if category else None
        )
        db_err = check_query_error(results)
        if db_err:
            return db_err
        if results["ids"] and results["ids"][0]:
            top_meta = results["metadatas"][0][0]
            top_dist = results["distances"][0][0]
            top_name = top_meta.get("name", "").lower().replace("()", "").strip()
            search_name = name.lower().replace("()", "").strip()
            # Only return if name matches or relevance is very strong (distance < 0.35 = 65%+)
            name_match = search_name == top_name or (
                len(search_name) >= 3 and search_name in top_name
            )
            if name_match or top_dist < 0.35:
                return format_entry_detail(
                    top_meta.get("name", name),
                    top_meta,
                    results["documents"][0][0],
                    top_dist,
                )

        # Step 3: Broaden to all categories (only if highly relevant)
        results = await query_async(name, 5)
        if results["ids"] and results["ids"][0]:
            top_meta = results["metadatas"][0][0]
            top_dist = results["distances"][0][0]
            top_name = top_meta.get("name", "").lower().replace("()", "").strip()
            search_name = name.lower().replace("()", "").strip()
            name_match_broad = search_name == top_name or (
                len(search_name) >= 3 and search_name in top_name
            )
            if name_match_broad or (
                top_dist < 0.35 and top_meta.get("category") == category
            ):
                return format_entry_detail(
                    top_meta.get("name", name),
                    top_meta,
                    results["documents"][0][0],
                    top_dist,
                )

        # Step 4: Fuzzy suggestions
        suggestions: list[str] = []
        if candidates:
            for sim, entry in candidates[:5]:
                suggestions.append(
                    f"  - {entry['metadata'].get('name', '?')} (similarity: {sim:.0f}%)"
                )
        else:
            all_candidates = await search_by_name_async(name)
            for sim, entry in all_candidates[:5]:
                suggestions.append(
                    f"  - {entry['metadata'].get('name', '?')} (similarity: {sim:.0f}%)"
                )

        cat_label = category.upper()
        if suggestions:
            return (
                f"{cat_label} '{name}' not found in the database.\n\n"
                f"Did you mean:\n" + "\n".join(suggestions)
            )
        return (
            f"{cat_label} '{name}' not found. Try search_docs() for a broader search."
        )

    except Exception as e:
        logger.error(f"[_lookup_entry] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        from formatters.errors import error

        return error(category, safe_error(e, category))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2: get_function
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Get Function Docs",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def get_function(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Entry name e.g. 'ta.ema', 'close', 'array'",
        ),
    ],
) -> str:
    """
    Get complete documentation for a PineScript v6 function.
    Returns all overloads, every parameter with type and description,
    return type, remarks, and ALL code examples in full.

    Use for: ta.*, strategy.*, array.*, math.*, str.*, request.*, etc.
    Example: get_function("ta.ema"), get_function("strategy.entry")
    """
    try:
        name = norm_name(name)
        await ensure_hot_cache()
        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(name)
        if cached and cached["metadata"].get("category") == "function":
            # Skip cache if function entry is hollow (no syntax) — a richer
            # version likely exists in another category
            cached_syntax = cached["metadata"].get("syntax") or ""
            if cached_syntax:
                result = format_entry_detail(
                    cached["metadata"].get("name", name),
                    cached["metadata"],
                    cached["document"],
                )
                return result

        name_preserved = name.strip()  # preserve case (e.g. "currency.USD")
        name_lower = name.lower().strip()

        # Step 1: Exact name match — find best version across ALL categories
        # Many entries have hollow 'function' versions (no syntax) alongside rich
        # 'variable'/'constant' versions. Pick the version with the most info.
        # Use $in to match both original case and lowercase (DB stores mixed case).
        try:
            name_variants = list({name_preserved, name_lower})
            all_versions = await get_by_names_async(name_variants)
            if all_versions["ids"]:
                best_meta, best_doc = _pick_best_version(all_versions)
                if best_meta:
                    return format_entry_detail(
                        best_meta.get("name", name), best_meta, best_doc
                    )
        except Exception as e:
            logger.debug(f"Exact name match failed: {e}")

        # Step 3: Fall back to the general lookup
        return await _lookup_entry(name, "function")

    except Exception as e:
        logger.error(f"[get_function] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_function"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3: get_variable
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Get Variable Docs",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def get_variable(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Entry name e.g. 'ta.ema', 'close', 'array'",
        ),
    ],
) -> str:
    """
    Get documentation for a PineScript v6 built-in variable.
    Built-in variables: close, open, high, low, volume, time,
    bar_index, barstate.*, syminfo.*, strategy.*, etc.
    """
    try:
        return await _lookup_entry(norm_name(name), "variable")
    except Exception as e:
        logger.error(f"[get_variable] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_variable"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4: get_type
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Get Type Docs",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def get_type(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Entry name e.g. 'ta.ema', 'close', 'array'",
        ),
    ],
) -> str:
    """
    Get documentation for a PineScript v6 type.
    Types: array, matrix, map, line, label, box, table, polyline,
    color, string, int, float, bool, and user-defined types.
    """
    try:
        name = norm_name(name)
        await ensure_hot_cache()
        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(name)
        if cached and cached["metadata"].get("category") == "type":
            result = format_entry_detail(
                cached["metadata"].get("name", name),
                cached["metadata"],
                cached["document"],
            )
            return result

        # Filter by category="type" — never return function entries
        name_lower = name.lower().strip()
        try:
            result = await get_type_by_name_async(name)
            if result["ids"]:
                best_meta = result["metadatas"][0]
                best_doc = result["documents"][0]
                formatted = format_entry_detail(
                    best_meta.get("name", name), best_meta, best_doc
                )

                # Enrich thin type entries with available methods
                ns = best_meta.get("namespace", "") or name_lower
                if len(best_doc) < 500:
                    try:
                        ns_entries = await get_all_where_async({"namespace": ns})
                        methods = []
                        for e in ns_entries:
                            m = e["metadata"]
                            if m.get("category") == "function" and m.get(
                                "name", ""
                            ).startswith(f"{ns}."):
                                methods.append(m.get("name", ""))
                        if methods:
                            methods.sort()
                            methods_str = ", ".join(methods[:30])
                            formatted += (
                                f"\n\nAVAILABLE METHODS ({len(methods)}): {methods_str}"
                            )
                    except Exception:
                        pass

                return formatted
        except Exception as e:
            logger.debug(f"Exact type match failed: {e}")

        # Semantic fallback — still enforce category filter
        results = await query_async(
            f"type {name_lower} definition fields methods",
            5,
            where={"category": "type"},
        )
        db_err = check_query_error(results)
        if db_err:
            return db_err
        if results["ids"] and results["ids"][0] and results["documents"][0]:
            top_dist = results["distances"][0][0]
            # Relevance gate: reject weak semantic matches (< 35% relevance)
            if top_dist > 0.65:
                return (
                    f"Type '{name}' not found in docs.\n"
                    f"Available types: array, matrix, map, line, label, "
                    f"box, table, polyline, color, string, int, float, bool"
                )
            top_meta = results["metadatas"][0][0]
            top_doc = results["documents"][0][0]
            formatted = format_entry_detail(
                top_meta.get("name", name), top_meta, top_doc, top_dist
            )

            # Enrich thin type entries with available methods from the same namespace
            top_name_clean = top_meta.get("name", "").lower().split(".")[0]
            if top_name_clean and len(top_doc) < 500:
                try:
                    ns_entries = await get_all_where_async(
                        {"namespace": top_name_clean}
                    )
                    methods = []
                    for e in ns_entries:
                        m = e["metadata"]
                        if m.get("category") == "function" and m.get(
                            "name", ""
                        ).startswith(f"{top_name_clean}."):
                            methods.append(m.get("name", ""))
                    if methods:
                        methods.sort()
                        methods_str = ", ".join(methods[:30])
                        formatted += (
                            f"\n\nAVAILABLE METHODS ({len(methods)}): {methods_str}"
                        )
                except Exception:
                    pass

            return formatted

        return (
            f"Type '{name}' not found in docs.\n"
            f"Available types: array, matrix, map, line, label, "
            f"box, table, polyline, color, string, int, float, bool"
        )

    except Exception as e:
        logger.error(f"[get_type] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_type"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 5: get_constant
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Get Constant Docs",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def get_constant(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Entry name e.g. 'ta.ema', 'close', 'array'",
        ),
    ],
) -> str:
    """
    Get documentation for a PineScript v6 built-in constant.
    Examples: color.red, strategy.long, order.ascending,
    shape.circle, location.top, etc.
    """
    try:
        return await _lookup_entry(norm_name(name), "constant")
    except Exception as e:
        logger.error(f"[get_constant] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_constant"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 6: get_keyword
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Get Keyword Docs",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def get_keyword(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Entry name e.g. 'ta.ema', 'close', 'array'",
        ),
    ],
) -> str:
    """
    Get documentation for a PineScript v6 keyword.
    Keywords: if, for, while, switch, var, varip, type, method,
    import, export, and, or, not, true, false, etc.
    """
    try:
        return await _lookup_entry(norm_name(name), "keyword")
    except Exception as e:
        logger.error(f"[get_keyword] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_keyword"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 7: get_operator
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Get Operator Docs",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def get_operator(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description="Entry name e.g. 'ta.ema', 'close', 'array'",
        ),
    ],
) -> str:
    """
    Get documentation for a PineScript v6 operator.
    Operators: :=, +=, -=, *=, /=, %=, ==, !=, >, <, >=, <=,
    ?, =>, +, -, *, /, %, not, and, or, [], etc.
    """
    try:
        return await _lookup_entry(norm_name(name), "operator")
    except Exception as e:
        logger.error(f"[get_operator] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "get_operator"))
