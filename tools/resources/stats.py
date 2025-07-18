"""
mcp/resources/stats.py
------------------------------------------------------------------------------
Resource: pinescript://stats
Returns database and server statistics as JSON.
"""

from __future__ import annotations

import json

from fastmcp.resources import resource
from loguru import logger

import core.caches as _caches_module
from core.config import SERVER_VERSION
from core.db import _chroma_breaker, get_collection
from core.embeddings import _embedding_model_ready
from core.hot_cache import HOT_CACHE
from core.pine_facade import pine_cb


@resource("pinescript://stats")
async def get_stats() -> str:
    """Return database statistics as JSON string. No paths or internal details leaked."""
    try:
        col = get_collection()
        total = col.count()

        return json.dumps(
            {
                "total_entries": total,
                "hot_cache_entries": len(HOT_CACHE),
                "compiler_circuit_open": pine_cb.is_open(),
                "chroma_circuit_open": _chroma_breaker.is_open(),
                "validation_cache_entries": len(_caches_module._VALIDATION_CACHE),
                "file_validation_cache_entries": len(_caches_module._FILE_VALIDATION_CACHE),
                "embedding_model_ready": _embedding_model_ready.is_set(),
                "version": SERVER_VERSION,
            },
            indent=2,
        )
    except Exception as e:
        logger.error(f"[get_stats] {e}")
        from formatters.errors import safe_error
        return json.dumps(
            {
                "error": safe_error(e, "get_stats"),
                "total_entries": None,
                "hot_cache_entries": None,
                "compiler_circuit_open": None,
                "chroma_circuit_open": None,
                "validation_cache_entries": None,
                "file_validation_cache_entries": None,
                "embedding_model_ready": _embedding_model_ready.is_set(),
                "version": SERVER_VERSION,
            },
            indent=2,
        )
