"""
mcp/tools/lookup.py
------------------------------------------------------------------------------
LOOKUP tool (1): pine_lookup - unified doc retrieval by name.

Handles every named entity kind in one call:
  - function  (ta.ema, strategy.entry, array.new, request.security, ...)
  - variable  (close, volume, bar_index, barstate.*, syminfo.*, ...)
  - type      (array, matrix, map, line, label, box, table, color, ...)
  - constant  (color.red, strategy.long, shape.circle, ...)
  - keyword   (if, for, var, varip, type, method, import, ...)
  - operator  (:=, +=, ==, !=, =>, [], and, or, not, ...)
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

import core.db as _db
from core.db import (
    get_all_where_async,
    get_by_names_async,
    get_type_by_name_async,
    query_async,
    search_by_name_async,
)
from core.hot_cache import cache_lookup, ensure_hot_cache
from formatters.entry import format_entry_detail, is_function_like
from formatters.errors import (
    cap_response,
    check_query_error,
    circuit_breaker_msg,
    norm_name,
    safe_error,
)

_Kind = Literal["function", "variable", "type", "constant", "keyword", "operator"]

_BUILTIN_TYPES = (
    "array, matrix, map, line, label, box, table, polyline, "
    "color, string, int, float, bool"
)


# -----------------------------------------------------------------------------
# Internal helpers (preserved from the old per-kind tools)
# -----------------------------------------------------------------------------


def _pick_best_version(result: dict) -> tuple:
    """From multiple DB entries with the same name, pick the best version.

    Priority:
    1. Function-like entries (have syntax with parens or params) - override to FUNCTION
    2. Entries with syntax (over hollow entries)
    3. Entries with longer docs
    """
    candidates = list(zip(result["metadatas"], result["documents"]))

    for meta, doc in candidates:
        if is_function_like(meta):
            meta = {**meta, "category": "function"}
            return meta, doc

    for meta, doc in candidates:
        syntax = meta.get("syntax") or ""
        if syntax:
            return meta, doc

    if candidates:
        best = max(candidates, key=lambda x: len(x[1]) if x[1] else 0)
        return best[0], best[1]

    return None, None


async def lookup_entry(name: str, category: str | None) -> str:
    """Resolve a name within an optional category, returning formatted docs.

    Search pipeline (fast paths first):
      0. Hot cache (sub-ms for priority entries)
      0.5. Exact name match across ALL categories -> _pick_best_version
      1. Fuzzy name-index match within category
      2. Semantic query within category
      3. Broad semantic fallback (category-matching only)
      4. Fuzzy suggestions ("did you mean")
    """
    try:
        await ensure_hot_cache()
        name_preserved = name.strip()
        name_lower = name.lower().strip()

        cached = cache_lookup(name)
        if cached:
            cached_cat = cached["metadata"].get("category")
            cached_syntax = cached["metadata"].get("syntax") or ""
            if (category and cached_cat != category) or not cached_syntax:
                pass
            else:
                result = format_entry_detail(
                    cached["metadata"].get("name", name),
                    cached["metadata"],
                    cached["document"],
                )
                return cap_response(result)

        try:
            name_variants = list({name_preserved, name_lower})
            all_versions = await get_by_names_async(name_variants)
            if all_versions["ids"]:
                for meta, doc in zip(
                    all_versions["metadatas"], all_versions["documents"]
                ):
                    if not category or meta.get("category") == category:
                        return cap_response(format_entry_detail(meta.get("name", name), meta, doc))
                best_meta, best_doc = _pick_best_version(all_versions)
                if best_meta:
                    return cap_response(format_entry_detail(
                        best_meta.get("name", name), best_meta, best_doc
                    ))
        except Exception as e:
            logger.debug(f"Cross-category lookup failed: {e}")

        candidates = await search_by_name_async(
            name, where={"category": category} if category else None
        )

        if candidates and candidates[0][0] >= 85:
            best_sim, best_entry = candidates[0]
            return cap_response(format_entry_detail(
                best_entry["metadata"].get("name", name),
                best_entry["metadata"],
                best_entry["document"],
            ))

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
            name_match = search_name == top_name or (
                len(search_name) >= 3 and search_name in top_name
            )
            if name_match or top_dist < 0.35:
                return cap_response(format_entry_detail(
                    top_meta.get("name", name),
                    top_meta,
                    results["documents"][0][0],
                    top_dist,
                ))

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
                return cap_response(format_entry_detail(
                    top_meta.get("name", name),
                    top_meta,
                    results["documents"][0][0],
                    top_dist,
                ))

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

        cat_label = (category or "ENTRY").upper()
        if suggestions:
            return (
                f"{cat_label} '{name}' not found in the database.\n\n"
                f"Did you mean:\n" + "\n".join(suggestions)
            )
        return (
            f"{cat_label} '{name}' not found. Try pine_search() for a broader search."
        )

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[lookup_entry] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, category or "lookup"))


async def _lookup_function(name: str) -> str:
    """Function-optimized lookup: tries hot cache + exact-name best-version pick,
    then falls back to the generic category-scoped pipeline."""
    await ensure_hot_cache()
    cached = cache_lookup(name)
    if cached and cached["metadata"].get("category") == "function":
        cached_syntax = cached["metadata"].get("syntax") or ""
        if cached_syntax:
            result = format_entry_detail(
                cached["metadata"].get("name", name),
                cached["metadata"],
                cached["document"],
            )
            return cap_response(result)

    name_preserved = name.strip()
    name_lower = name.lower().strip()

    try:
        name_variants = list({name_preserved, name_lower})
        all_versions = await get_by_names_async(name_variants)
        if all_versions["ids"]:
            best_meta, best_doc = _pick_best_version(all_versions)
            if best_meta:
                return cap_response(format_entry_detail(
                    best_meta.get("name", name), best_meta, best_doc
                ))
    except Exception as e:
        logger.debug(f"Exact name match failed: {e}")

    return await lookup_entry(name, "function")


async def _enrich_type_methods(ns: str, doc: str, formatted: str) -> str:
    """For thin type entries (<500 chars), append an AVAILABLE METHODS list
    scanned from entries sharing the same namespace."""
    if len(doc) >= 500 or not ns:
        return formatted
    try:
        ns_entries = await get_all_where_async({"namespace": ns})
        methods: list[str] = []
        for e in ns_entries:
            m = e["metadata"]
            if m.get("category") == "function" and m.get("name", "").startswith(f"{ns}."):
                methods.append(m.get("name", ""))
        if methods:
            methods.sort()
            methods_str = ", ".join(methods[:30])
            formatted += f"\n\nAVAILABLE METHODS ({len(methods)}): {methods_str}"
    except Exception as e:
        logger.debug(f"Type method enrichment failed: {e}")
    return formatted


async def _lookup_type(name: str) -> str:
    """Type-specialized lookup with method enrichment for thin entries."""
    await ensure_hot_cache()
    cached = cache_lookup(name)
    if cached and cached["metadata"].get("category") == "type":
        result = format_entry_detail(
            cached["metadata"].get("name", name),
            cached["metadata"],
            cached["document"],
        )
        return cap_response(result)

    name_lower = name.lower().strip()
    try:
        result = await get_type_by_name_async(name)
        if result["ids"]:
            best_meta = result["metadatas"][0]
            best_doc = result["documents"][0]
            formatted = format_entry_detail(
                best_meta.get("name", name), best_meta, best_doc
            )
            ns = best_meta.get("namespace", "") or name_lower
            formatted = await _enrich_type_methods(ns, best_doc, formatted)
            return cap_response(formatted)
    except Exception as e:
        logger.debug(f"Exact type match failed: {e}")

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
        if top_dist > 0.65:
            return (
                f"Type '{name}' not found in docs.\n"
                f"Available types: {_BUILTIN_TYPES}"
            )
        top_meta = results["metadatas"][0][0]
        top_doc = results["documents"][0][0]
        formatted = format_entry_detail(
            top_meta.get("name", name), top_meta, top_doc, top_dist
        )
        ns = top_meta.get("name", "").lower().split(".")[0]
        formatted = await _enrich_type_methods(ns, top_doc, formatted)
        return cap_response(formatted)

    return (
        f"Type '{name}' not found in docs.\n"
        f"Available types: {_BUILTIN_TYPES}"
    )


# -----------------------------------------------------------------------------
# TOOL: pine_lookup - unified lookup by name
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Lookup PineScript Entry",
        readOnlyHint=True,
        openWorldHint=False,
        idempotentHint=True,
    )
)
async def pine_lookup(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=200,
            description=(
                "Exact PineScript v6 symbol name. Examples: 'ta.ema', "
                "'strategy.entry', 'close', 'barstate.isconfirmed', "
                "'array', 'color.red', 'if', ':=', '=>'."
            ),
        ),
    ],
    kind: Annotated[
        _Kind | None,
        Field(
            default=None,
            description=(
                "Optional filter by entity kind. Leave unset (recommended) to "
                "auto-pick the richest doc across all kinds - best for ambiguous "
                "names like 'color' that exist as both a type and a namespace. "
                "Set explicitly to disambiguate when you want a specific kind: "
                "'function' | 'variable' | 'type' | 'constant' | 'keyword' | 'operator'."
            ),
        ),
    ] = None,
) -> str:
    """
    Get complete documentation for a PineScript v6 symbol by exact name.

    Returns full entry details: syntax, every parameter with types and
    descriptions, return type, remarks, and all code examples.

    WHEN TO USE:
      - You know the exact symbol name (e.g. 'ta.ema', 'close', 'array').
      - You need the authoritative reference before writing code.

    WHEN NOT TO USE:
      - Fuzzy or conceptual searches like "how do I compute RSI?" -> use pine_search().
      - Browsing every member of a namespace -> use pine_browse().
    """
    try:
        canonical = norm_name(name)

        if kind == "function":
            return await _lookup_function(canonical)
        if kind == "type":
            return await _lookup_type(canonical)
        if kind in ("variable", "constant", "keyword", "operator"):
            return await lookup_entry(canonical, kind)

        return await _lookup_function(canonical)

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_lookup] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "pine_lookup"))
