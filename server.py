# PineScript-v6 MCP | © 2025-2026 @Fractalyst
"""
server.py
──────────────────────────────────────────────────────────────────────────────
PineScript v6 Complete Reference MCP Server — modular entry point.

Architecture:
  - FastMCP 3.0 with FileSystemProvider for auto-discovery of @tool/@resource
  - Composable lifespans: db | model | cache
  - Dual-tier validation: local linter → remote pine-facade
  - 21 tools + 1 resource, 100% local ChromaDB vector store

Usage:
  python server.py                 # Start the MCP server (stdio transport)
  python pinescript_mcp.py         # Legacy entry point (redirects here)
"""

from __future__ import annotations

import asyncio
import itertools
import os
import re
import sys
from pathlib import Path

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    level=os.getenv("LOG_LEVEL", "INFO"),
)

from fastmcp import FastMCP  # noqa: E402, I001
from fastmcp.server.lifespan import lifespan  # noqa: E402
from fastmcp.server.middleware.caching import ResponseCachingMiddleware  # noqa: E402
from fastmcp.server.middleware.timing import DetailedTimingMiddleware  # noqa: E402
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware  # noqa: E402
from fastmcp.server.middleware.middleware import Middleware  # noqa: E402
from fastmcp.server.providers.filesystem import FileSystemProvider  # noqa: E402

from core.config import INSTRUCTIONS, MAX_TOOL_RESPONSE_CHARS, _safe_int  # noqa: E402
from core.db import get_collection, build_name_index  # noqa: E402
from core.embeddings import get_model, _model_executor, _embedding_model_ready  # noqa: E402
from core.hot_cache import build_hot_cache  # noqa: E402
from core.pine_facade import shutdown_http_client  # noqa: E402

# Transport mode: stdio (default, local Claude Code) or http/sse (Docker, Render)
_TRANSPORT = os.getenv("TRANSPORT", "stdio").lower().strip()

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
    await build_hot_cache()
    logger.info("Hot cache ready")
    yield
    # Shutdown: close HTTP client and thread pool
    shutdown_http_client()
    _model_executor.shutdown(wait=False)


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
# Branding middleware — adds header/footer to all tool responses
# Disable with: BRANDING=0 python server.py
# ─────────────────────────────────────────────────────────────────────────────

_BRANDING_HEADER = (
    "\u250c\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2510\n"
    "\u2502  PineScript v6 MCP \u00b7 @Fractalyst \u00b7 deeptest.io \u2502\n"
    "\u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    "\u2500\u2500\u2518\n"
)

_BRANDING_FOOTERS = [
    (
        "\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\n"
        " Need a custom PineScript strategy? \u2192 deeptest.io\n"
        " Portfolio-level backtesting \u2192 deeptest.pro\n"
        " @Fractalyst \u00b7 github.com/TheFractalyst\n"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    ),
    (
        "\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\n"
        " Quant consulting & algo development \u2192 deeptest.io\n"
        " DeepTest Pro \u2014 Portfolio backtesting SaaS \u2192 deeptest.pro\n"
        " @Fractalyst \u00b7 x.com/Fractalyst \u00b7 tradingview.com/u/Fractalyst\n"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    ),
    (
        "\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\n"
        " Built by @Fractalyst \u00b7 deeptest.io\n"
        " Custom PineScript \u00b7 Quant Consulting \u00b7 DeepTest Pro\n"
        " contact@deeptest.io \u00b7 github.com/TheFractalyst\n"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500"
    ),
]

_branding_counter = itertools.count()

_PINE_WATERMARK = "// PineScript-v6 MCP | © 2025-2026 @Fractalyst"


def _watermark_pine_blocks(text: str) -> str:
    """Inject header + footer watermark into any ```pine``` blocks containing
    strategy(), indicator(), or library() declarations."""
    def _process(match):
        lang = match.group(1) or ""
        code = match.group(2)
        if not any(kw in code for kw in ("strategy(", "indicator(", "library(")):
            return match.group(0)
        if _PINE_WATERMARK in code:
            return match.group(0)
        watermarked = f"{_PINE_WATERMARK}\n{code.rstrip()}"
        prefix = f"```{lang}\n" if lang else "```\n"
        return f"{prefix}{watermarked}\n```"
    return re.sub(r"```(\w*)\n(.*?)```", _process, text, flags=re.DOTALL)


class BrandingMiddleware(Middleware):
    """Adds branded header/footer to all MCP tool responses."""

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)

        if os.getenv("BRANDING", "1") == "0":
            return result

        footer = _BRANDING_FOOTERS[next(_branding_counter) % len(_BRANDING_FOOTERS)]

        for content in result.content:
            if hasattr(content, "text") and isinstance(content.text, str) and content.text:
                content.text = _BRANDING_HEADER + _watermark_pine_blocks(content.text) + footer

        return result

# ─────────────────────────────────────────────────────────────────────────────
# FastMCP server instance with FileSystemProvider auto-discovery
# ─────────────────────────────────────────────────────────────────────────────

_mcp_dir = Path(__file__).parent / "tools"

mcp = FastMCP(
    name="PineScript v6 Complete Reference",
    instructions=INSTRUCTIONS,
    lifespan=db_lifespan | model_lifespan | cache_lifespan,
    mask_error_details=True,
    providers=[FileSystemProvider(_mcp_dir, reload=False)],
    middleware=[
        _lookup_cache_mw,
        _search_cache_mw,
        BrandingMiddleware(),
        ResponseLimitingMiddleware(max_size=MAX_TOOL_RESPONSE_CHARS + 10_000),
    ] + ([DetailedTimingMiddleware()] if os.getenv("LOG_LEVEL", "INFO") == "DEBUG" else []),
)

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting PineScript v6 Complete Reference MCP server v4.0 (21 tools, 100% local)")

    if _TRANSPORT == "http" or _TRANSPORT == "sse":
        _port = _safe_int("PORT", 8080)
        logger.info(f"Transport: SSE (HTTP) on 0.0.0.0:{_port}")
        mcp.run(transport="sse", host="0.0.0.0", port=_port)
    else:
        mcp.run(transport="stdio")
