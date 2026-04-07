"""
server.py
──────────────────────────────────────────────────────────────────────────────
PineScript v6 Complete Reference MCP Server — modular entry point.

Architecture:
  - FastMCP 3.0 with FileSystemProvider for auto-discovery of @tool/@resource
  - Composable lifespans: db | model | cache
  - Dual-tier validation: local linter → remote pine-facade
  - 20 tools + 1 resource, 100% local ChromaDB vector store

Usage:
  python server.py                 # Start the MCP server (stdio transport)
  python pinescript_mcp.py         # Legacy entry point (redirects here)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import sys
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    level=os.getenv("LOG_LEVEL", "INFO"),
)

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from fastmcp.server.providers.filesystem import FileSystemProvider

from core.config import INSTRUCTIONS
from core.db import get_collection, build_name_index
from core.embeddings import get_model, _model_executor, _embedding_model_ready
from core.hot_cache import build_hot_cache, _hot_cache_built
from core.pine_facade import shutdown_http_client

# ─────────────────────────────────────────────────────────────────────────────
# Composable lifespans
# ─────────────────────────────────────────────────────────────────────────────


@lifespan
async def db_lifespan(server):
    """Initialize ChromaDB collection and name index."""
    logger.info("Preloading ChromaDB collection...")
    get_collection()
    logger.info("ChromaDB collection ready")

    logger.info("Building name index...")
    build_name_index()
    logger.info("Name index ready")
    yield


@lifespan
async def model_lifespan(server):
    """Initialize embedding model in thread pool."""
    logger.info("Preloading embedding model...")
    loop = asyncio.get_running_loop()
    model = await loop.run_in_executor(_model_executor, get_model)
    _embedding_model_ready.set()
    # Warm-up inference: eliminates 50-200ms cold-start on first real query
    await loop.run_in_executor(_model_executor, lambda: model.encode(["warmup"]))
    logger.info("Embedding model ready (warmed up)")
    yield


@lifespan
async def cache_lifespan(server):
    """Build hot cache."""
    logger.info("Building hot cache...")
    success = await build_hot_cache()
    if success:
        import core.hot_cache as _hc
        _hc._hot_cache_built = True
    logger.info("Hot cache ready")
    yield
    # Shutdown: close HTTP client
    shutdown_http_client()


# ─────────────────────────────────────────────────────────────────────────────
# Response caching middleware (complementary to internal LRU caches)
#
# Two-tier MCP-level caching:
#   1. Lookup middleware (1h TTL): deterministic, stable doc entries
#   2. Search middleware (5m TTL): same query = same result within a session
#
# Validation/codegen tools are EXCLUDED — unique code on nearly every call.
# ─────────────────────────────────────────────────────────────────────────────

_lookup_cache_mw = ResponseCachingMiddleware(
    call_tool_settings={
        "ttl": 3600,
        "enabled": True,
        "included_tools": [
            "get_function",
            "get_variable",
            "get_type",
            "get_constant",
            "get_keyword",
            "get_operator",
            "list_namespace",
            "get_namespace_cheatsheet",
        ],
    },
)

_search_cache_mw = ResponseCachingMiddleware(
    call_tool_settings={
        "ttl": 300,
        "enabled": True,
        "included_tools": [
            "search_docs",
            "get_examples",
            "search_by_return_type",
            "suggest_functions",
        ],
    },
)

# ─────────────────────────────────────────────────────────────────────────────
# FastMCP server instance with FileSystemProvider auto-discovery
# ─────────────────────────────────────────────────────────────────────────────

_mcp_dir = Path(__file__).parent / "pine_tools"

mcp = FastMCP(
    name="PineScript v6 Complete Reference",
    instructions=INSTRUCTIONS,
    lifespan=db_lifespan | model_lifespan | cache_lifespan,
    mask_error_details=True,
    providers=[FileSystemProvider(_mcp_dir, reload=False)],
    middleware=[
        _lookup_cache_mw,
        _search_cache_mw,
        DetailedTimingMiddleware(),
        ResponseLimitingMiddleware(max_size=500_000),
    ],
)

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting PineScript v6 Complete Reference MCP server v4.0 (20 tools, 100% local)")
    mcp.run(transport="stdio")
