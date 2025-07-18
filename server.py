"""
server.py
------------------------------------------------------------------------------
PineScript v6 Complete Reference MCP Server - modular entry point.

Architecture:
  - FastMCP 3.0 with FileSystemProvider for auto-discovery of @tool/@resource
  - Composable lifespans: db | model | cache
  - Remote TradingView v6 compiler for all validation paths
  - 6 tools + 1 resource, 100% local ChromaDB vector store

Usage:
  python server.py                 # Start the MCP server (stdio transport)
  python pinescript_mcp.py         # Legacy entry point (redirects here)
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib as _pl
import sys
from pathlib import Path

from dotenv import load_dotenv

_server_dir = _pl.Path(__file__).resolve().parent
_env_file = _server_dir / ".env"
if _env_file.is_file():
    load_dotenv(str(_env_file), override=False)

from loguru import logger  # noqa: E402

logger.remove()
logger.add(
    sys.stderr,
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    level=os.getenv("LOG_LEVEL", "INFO"),
)

# -----------------------------------------------------------------------------
# HTTP transport runtime setup (stderr + daily rotating file)
# -----------------------------------------------------------------------------
from core.config import _TRANSPORT  # noqa: E402

_LOG_DIR = os.getenv("LOG_DIR", os.path.join(os.path.expanduser("~"), ".pinescript_mcp", "logs"))

if _TRANSPORT in ("http", "sse"):
    try:
        from pathlib import Path as _P
        _log_path = _P(_LOG_DIR)
        _log_path.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(_log_path / "server_{time:YYYY-MM-DD}.log"),
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} | {message}",
            level=os.getenv("LOG_LEVEL", "INFO"),
            rotation="00:00",          # daily rotation at midnight
            retention="30 days",       # keep 30 days of logs
            compression="gz",          # compress old logs
            encoding="utf-8",
            enqueue=True,              # thread-safe, non-blocking
            backtrace=True,
            diagnose=False,
        )
        logger.debug(f"Diag dir: {_log_path}")
    except Exception as _e:
        # Never crash the server for a diagnostics issue
        logger.warning(f"Runtime state dir unavailable: {_e}")

from fastmcp import FastMCP  # noqa: E402, I001
from fastmcp.server.lifespan import lifespan  # noqa: E402
from fastmcp.server.middleware.caching import ResponseCachingMiddleware  # noqa: E402
from fastmcp.server.middleware.timing import DetailedTimingMiddleware  # noqa: E402
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware  # noqa: E402
from fastmcp.server.providers.filesystem import FileSystemProvider  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

from core.config import INSTRUCTIONS, MAX_TOOL_RESPONSE_CHARS, _safe_int  # noqa: E402
from formatters.errors import safe_error  # noqa: E402
from core.build_db import build_db_if_needed  # noqa: E402
from core.db import get_collection, build_name_index  # noqa: E402
from core.embeddings import get_model, _model_executor, _embedding_model_ready  # noqa: E402
from core.hot_cache import build_hot_cache  # noqa: E402
from core.pine_facade import shutdown_http_client_async  # noqa: E402
from core import _rtstate  # noqa: E402

# _TRANSPORT is already set above (line 39) for file-logging setup.

# -----------------------------------------------------------------------------
# Composable lifespans
# -----------------------------------------------------------------------------


@lifespan
async def db_lifespan(server):
    """Initialize ChromaDB collection and name index. Auto-builds DB on first run."""
    try:
        await asyncio.get_running_loop().run_in_executor(None, build_db_if_needed)
        logger.info("Preloading ChromaDB collection...")
        get_collection()
        logger.info("ChromaDB collection ready")

        logger.info("Building name index...")
        build_name_index()
        logger.info("Name index ready")
    except Exception as e:
        logger.error(f"ChromaDB init failed: {e}. Search tools will be unavailable.")
    yield


@lifespan
async def model_lifespan(server):
    """Initialize embedding model in thread pool."""
    if os.getenv("LAZY_MODEL", "").lower() in ("1", "true"):
        logger.info("LAZY_MODEL=1 - deferring embedding model load to first query")
        yield
        return
    try:
        logger.info("Preloading embedding model...")
        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(_model_executor, get_model)
        _embedding_model_ready.set()
        # Warm-up inference: eliminates 50-200ms cold-start on first real query
        await loop.run_in_executor(_model_executor, lambda: model.encode(["warmup"]))
        logger.info("Embedding model ready (warmed up)")
    except Exception as e:
        logger.error(f"Embedding model load failed: {e}. Semantic search will be unavailable.")
    yield


@lifespan
async def cache_lifespan(server):
    """Build hot cache and initialize runtime state."""
    logger.info("Building hot cache...")
    success = await build_hot_cache()
    if success:
        logger.info("Hot cache ready")
    else:
        logger.warning("Hot cache build failed - direct DB lookups will be used")

    yield
    # Shutdown: close HTTP client and thread pool
    await shutdown_http_client_async()
    _model_executor.shutdown(wait=False)


# -----------------------------------------------------------------------------
# Response caching middleware (complementary to internal LRU caches)
#
# Two-tier MCP-level caching:
#   1. Lookup middleware (1h TTL): deterministic, stable doc entries
#   2. Search middleware (5m TTL): same query = same result within a session
#
# Validation/codegen tools are EXCLUDED - unique code on nearly every call.
# -----------------------------------------------------------------------------

_lookup_cache_mw = ResponseCachingMiddleware(
    call_tool_settings={
        "ttl": 3600,
        "enabled": True,
        "included_tools": [
            "pine_lookup",
            "pine_browse",
        ],
    },
)

_search_cache_mw = ResponseCachingMiddleware(
    call_tool_settings={
        "ttl": 300,
        "enabled": True,
        "included_tools": [
            "pine_search",
        ],
    },
)

# -----------------------------------------------------------------------------
# FastMCP server instance with FileSystemProvider auto-discovery
# -----------------------------------------------------------------------------

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
        ResponseLimitingMiddleware(max_size=MAX_TOOL_RESPONSE_CHARS + 10_000),
    ] + ([DetailedTimingMiddleware()] if os.getenv("LOG_LEVEL", "INFO") == "DEBUG" else []),
)

# -----------------------------------------------------------------------------
# API key loading (supports hot-reload from api_keys.json)
# -----------------------------------------------------------------------------

_key_container: list[dict[str, dict]] = [{}]


def _load_key_map() -> dict[str, dict]:
    """Load API keys from api_keys.json, falling back to env vars."""
    key_file = Path(__file__).parent / "api_keys.json"
    if key_file.exists():
        try:
            data = json.loads(key_file.read_text(encoding="utf-8"))
            if data and isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    raw = os.getenv("MCP_API_KEYS", "")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    single = os.getenv("MCP_API_KEY", "")
    if single:
        return {single: {"name": "admin", "role": "admin"}}
    return {}


def _reload_keys() -> int:
    """Hot-reload API keys from api_keys.json. Returns new key count."""
    _key_container[0] = _load_key_map()
    return len(_key_container[0])


# -----------------------------------------------------------------------------
# Health check endpoint (no auth required)
# -----------------------------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Health endpoint for local development and testing."""
    try:
        col = get_collection()
        return JSONResponse({"status": "ok", "entries": col.count()})
    except Exception as e:
        return JSONResponse({"status": "error", "error": safe_error(e, "health_check")}, status_code=503)


@mcp.custom_route("/admin/reload", methods=["POST"])
async def admin_reload_keys(request: Request) -> JSONResponse:
    """Hot-reload API keys from api_keys.json. Requires admin role."""
    raw_key = request.headers.get("x-api-key", "")
    entry = _key_container[0].get(raw_key)
    if not entry or entry.get("role") != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    count = _reload_keys()
    logger.info(f"API keys reloaded via /admin/reload ({count} keys)")
    return JSONResponse({"status": "ok", "keys": count})


@mcp.custom_route("/admin/stats", methods=["GET"])
async def admin_stats(request: Request) -> JSONResponse:
    """Return server stats. Requires admin role."""
    raw_key = request.headers.get("x-api-key", "")
    entry = _key_container[0].get(raw_key)
    if not entry or entry.get("role") != "admin":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        col = get_collection()
        entries = col.count()
    except Exception:
        entries = -1
    import core.hot_cache as _hc
    return JSONResponse({
        "status": "ok",
        "entries": entries,
        "api_keys": len(_key_container[0]),
        "key_names": [v.get("name", "?") for v in _key_container[0].values()],
        "cache": {
            "hot_cache_size": len(_hc.HOT_CACHE),
            "hot_cache_hits": _hc._cache_hits,
            "hot_cache_misses": _hc._cache_misses,
        },
    })

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def main():
    """CLI entry point for pinemcp console script.

    Usage:
        pinemcp              Start MCP server (stdio, default)
        pinemcp build        Build ChromaDB from shipped data and exit
        pinemcp --transport sse --port 8080   Start SSE server
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="pinemcp",
        description="PineScript v6 MCP Server - 6 tools for docs lookup, code validation, and code generation",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "build"],
        help="Command: 'serve' (default) starts the MCP server, 'build' builds ChromaDB and exits",
    )
    parser.add_argument("--transport", default=None, help="Transport: stdio (default) or sse")
    parser.add_argument("--port", type=int, default=None, help="Port for SSE transport")
    args = parser.parse_args()

    if args.command == "build":
        from core.build_db import build_db

        count = build_db(force=True)
        print(f"ChromaDB built: {count} entries")
        return

    logger.info("Starting PineScript v6 Complete Reference MCP server (6 tools, 100% local)")

    transport = args.transport or _TRANSPORT

    if transport in ("http", "sse"):
        _port = args.port or _safe_int("PORT", 8080)
        logger.info(f"Transport: SSE (HTTP) on 0.0.0.0:{_port}")

        _loaded = _load_key_map()
        if _loaded:
            _key_container[0] = _loaded
        else:
            _api_key = os.getenv("MCP_API_KEY", "")
            _api_keys_raw = os.getenv("MCP_API_KEYS", "")
            if _api_keys_raw:
                try:
                    _key_container[0] = json.loads(_api_keys_raw)
                except json.JSONDecodeError:
                    logger.error("MCP_API_KEYS is not valid JSON - falling back to MCP_API_KEY")
            if not _key_container[0] and _api_key:
                _key_container[0] = {_api_key: {"name": "admin", "role": "admin"}}

        if _key_container[0]:
            from starlette.middleware import Middleware as StarletteMiddleware

            class _ApiKeyASGIMiddleware:
                """Pure-ASGI auth middleware that doesn't wrap responses."""

                def __init__(self, app):
                    self.app = app

                async def __call__(self, scope, receive, send):
                    if scope["type"] != "http":
                        await self.app(scope, receive, send)
                        return
                    path = scope.get("path", "")
                    if path == "/health":
                        await self.app(scope, receive, send)
                        return
                    headers = dict(scope.get("headers", []))
                    raw_key = headers.get(b"x-api-key", b"").decode("utf-8", errors="replace")
                    entry = _key_container[0].get(raw_key)
                    if entry is None:
                        body = b'{"error":"unauthorized"}'
                        await send({"type": "http.response.start", "status": 401,
                                     "headers": [[b"content-type", b"application/json"],
                                                  [b"content-length", str(len(body)).encode()]]})
                        await send({"type": "http.response.body", "body": body})
                        return
                    _rtstate.bind_rid(entry.get("name", "unknown"))
                    await self.app(scope, receive, send)

            _names = [v.get("name", "?") for v in _key_container[0].values()]
            logger.info(f"API key authentication enabled ({len(_key_container[0])} key(s): {', '.join(_names)})")
            mcp.run(
                transport="sse",
                host="0.0.0.0",
                port=_port,
                middleware=[StarletteMiddleware(_ApiKeyASGIMiddleware)],
            )
        else:
            logger.warning("No MCP_API_KEY, MCP_API_KEYS, or api_keys.json - server is open to all connections")
            mcp.run(transport="sse", host="0.0.0.0", port=_port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
