"""
pinescript_mcp.py
─────────────────────────────────────────────────────────────────────────────
Backward-compatible shim — re-exports all public symbols from the new
modular packages so existing tests and external scripts keep working.

New modular entry point: server.py
Architecture:
  core/           ChromaDB, embeddings, caches, pine-facade, hot cache
  formatters/     Entry formatting, error formatting, response utilities
  templates/      Indicator templates, v5→v6 migration map
  mcp/tools/      20 @tool decorated functions (auto-discovered by FileSystemProvider)
  mcp/resources/  1 @resource (pinescript://stats)
"""

from __future__ import annotations

# ─────────────────────────────────────────────────────────────────────────────
# Core infrastructure re-exports
# ─────────────────────────────────────────────────────────────────────────────

# Config constants
from core.config import (
    DB_PATH,
    COLLECTION,
    EMBED_MODEL,
    MAX_RESULTS,
    PINE_FACADE_URL,
    PINE_FACADE_TIMEOUT,
    VALIDATION_CACHE_TTL,
    VALIDATION_CACHE_MAX_SIZE,
    MAX_TOOL_RESPONSE_CHARS,
    MAX_FUZZY_SCAN_ENTRIES,
    _ALLOWED_BASE_DIRS,
    INSTRUCTIONS,
)

# ChromaDB
import core.db as _core_db
from core.db import (
    ChromaDBCircuitBreaker,
    get_collection as _get_collection,
    build_name_index as _build_name_index,
    query_async as _query_async,
    search_by_name_async as _search_by_name_async,
    get_all_where_async as _get_all_where_async,
    _chroma_breaker,
    _COMMON_PARAM_NAMES,
)

# Embeddings
from core.embeddings import (
    get_model as _get_model,
    _embedding_model_ready,
    _model_executor,
)

# Hot cache
from core.hot_cache import (
    HOT_CACHE,
    cache_lookup,
    build_hot_cache,
    ensure_hot_cache as _ensure_hot_cache,
    PRIORITY_NAMESPACES,
    PRIORITY_GLOBALS,
)

# Pine facade
from core.pine_facade import (
    PineFacadeCircuitBreaker,
    pine_cb as _pine_cb,
    call_pine_facade as _call_pine_facade,
    normalize_facade_response as _normalize_facade_response,
    enrich_error_with_code as _enrich_error_with_code,
    shutdown_http_client as _shutdown_http_client,
)

# Caches
import core.caches as _caches_module
from core.caches import (
    _VALIDATION_CACHE,
    _FILE_VALIDATION_CACHE,
    _QUERY_RESULT_CACHE,
    _CODEGEN_CACHE,
    get_cached_validation as _get_cached_validation,
    set_cached_validation as _cache_validation,
    get_cached_file_validation as _get_cached_file_validation,
    set_cached_file_validation as _cache_file_validation,
    codegen_cache_key as _codegen_cache_key,
    get_codegen_cache as _get_codegen_cache,
    set_codegen_cache as _set_codegen_cache,
)

# ─────────────────────────────────────────────────────────────────────────────
# Formatter re-exports (with underscore-prefixed aliases for test compat)
# ─────────────────────────────────────────────────────────────────────────────

from formatters.entry import (
    _BOX_TL, _BOX_TR, _BOX_BL, _BOX_BR, _BOX_H, _BOX_V, _BOX_MID, _DIVIDER,
    relevance_pct as _relevance_pct,
    format_params_text as _format_params_text,
    format_examples_text as _format_examples_text,
    format_type_info as _format_type_info,
    dedup_examples as _dedup_examples,
    format_entry_detail as _format_entry_detail,
    source_tag as _source_tag,
    source_line as _source_line,
    section_line as _section_line,
)

from formatters.errors import (
    _FIX_HINTS,
    lookup_fix_hint as _lookup_fix_hint,
    extract_name_from_error as _extract_name_from_error,
    safe_error as _safe_error,
    cap_response as _cap_response,
    sanitize_text as _sanitize_text,
    sanitize_pine_string as _sanitize_pine_string,
    circuit_breaker_msg as _circuit_breaker_msg,
    check_query_error as _check_query_error,
    error as _error,
    norm_name as _norm_name,
    norm_ns as _norm_ns,
)

# ─────────────────────────────────────────────────────────────────────────────
# Template re-exports
# ─────────────────────────────────────────────────────────────────────────────

from templates.indicators import (
    _INDICATOR_TEMPLATES,
    extract_indicator_keywords as _extract_indicator_keywords,
    map_input_to_param as _map_input_to_param,
)
from templates.v5_migration import V5_TO_V6

# ─────────────────────────────────────────────────────────────────────────────
# Tool function re-exports (all 20 tools)
# ─────────────────────────────────────────────────────────────────────────────

# LOOKUP tools
from pine_tools.tools.lookup import (
    get_function,
    get_variable,
    get_type,
    get_constant,
    get_keyword,
    get_operator,
    _lookup_entry,
)

# SEARCH tools
from pine_tools.tools.search import (
    search_docs,
    get_examples,
    list_namespace,
    search_by_return_type,
)

# VALIDATE tools
from pine_tools.tools.validation import (
    validate_syntax,
    validate_and_explain,
    fix_and_validate,
    debug_pine_facade,
    validate_file,
)

# CODEGEN tools
from pine_tools.tools.codegen import (
    generate_indicator,
    generate_strategy,
    lookup_and_correct,
)

# CONTEXT tools
from pine_tools.tools.context import (
    suggest_functions,
    get_namespace_cheatsheet,
)

# ─────────────────────────────────────────────────────────────────────────────
# Module-level __getattr__ for dynamic globals (bool flags that change at runtime)
# ─────────────────────────────────────────────────────────────────────────────

# Direct reference to the same dict object — mutations are shared
_name_index = _core_db._name_index


def __getattr__(name: str):
    """Dynamic attribute lookup for mutable globals that change after import."""
    if name == "_name_index_built":
        return _core_db._name_index_built
    if name == "_hot_cache_built":
        import core.hot_cache as _hc
        return _hc._hot_cache_built
    if name == "_collection":
        return _core_db._collection
    if name == "_embed_model":
        from core.embeddings import _embed_model
        return _embed_model
    raise AttributeError(f"module 'pinescript_mcp' has no attribute {name!r}")


# ─────────────────────────────────────────────────────────────────────────────
# The MCP server instance (from server.py) + entry point
# ─────────────────────────────────────────────────────────────────────────────

from server import mcp

if __name__ == "__main__":
    import sys
    import os
    from loguru import logger

    logger.remove()
    logger.add(
        sys.stderr,
        format="{time:HH:mm:ss} | {level:<8} | {message}",
        level=os.getenv("LOG_LEVEL", "INFO"),
    )
    logger.info("Starting PineScript v6 Complete Reference MCP server v4.0 (20 tools, 100% local)")
    mcp.run(transport="stdio")
