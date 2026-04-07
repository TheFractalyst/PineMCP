"""
pinescript_mcp.py
─────────────────────────────────────────────────────────────────────────────
PineScript v6 Complete Knowledge MCP Server
FastMCP 3.0 · Transport: stdio · 20 tools + 1 resource

Serves PineScript v6 reference documentation via semantic-search tools
backed by a local ChromaDB vector store (3,400+ entries, 100% local).

Tools (20 total):
  LOOKUP (6):   get_function, get_variable, get_type, get_constant,
                get_keyword, get_operator
  SEARCH (4):   search_docs, get_examples, search_by_return_type,
                list_namespace
  VALIDATE (5): validate_syntax, validate_and_explain, fix_and_validate,
                debug_pine_facade, validate_file
  CODEGEN (3):  generate_indicator, generate_strategy, lookup_and_correct
  CONTEXT (2):  suggest_functions, get_namespace_cheatsheet

Resource: pinescript://stats
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import random
import re
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Annotated, Optional

import httpx
import xxhash
from loguru import logger

from pine_linter import lint as _pine_lint

logger.remove()
logger.add(
    sys.stderr,
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    level=os.getenv("LOG_LEVEL", "INFO"),
)

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from fastmcp.server.middleware.timing import DetailedTimingMiddleware
from fastmcp.server.middleware.response_limiting import ResponseLimitingMiddleware
from mcp.types import ToolAnnotations
from pydantic import Field

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DB_PATH = os.getenv(
    "PINESCRIPT_DB_PATH",
    str(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pinescript_db")),
)
COLLECTION = os.getenv("PINESCRIPT_COLLECTION", "pinescript_v6")
EMBED_MODEL = os.getenv("PINESCRIPT_EMBED_MODEL", "all-MiniLM-L6-v2")
MAX_RESULTS = int(os.getenv("PINESCRIPT_MAX_RESULTS", "30"))
PINE_FACADE_URL = os.getenv(
    "PINE_FACADE_URL",
    "https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=admin&v=3",
)
PINE_FACADE_TIMEOUT = int(os.getenv("PINE_FACADE_TIMEOUT", "20"))
VALIDATION_CACHE_TTL = int(os.getenv("VALIDATION_CACHE_TTL", "300"))
VALIDATION_CACHE_MAX_SIZE = int(os.getenv("VALIDATION_CACHE_SIZE", "500"))
MAX_TOOL_RESPONSE_CHARS = 8000
MAX_FUZZY_SCAN_ENTRIES = 5000

# Pre-computed at import time (not inside per-call hot path)
_ALLOWED_BASE_DIRS = [
    os.path.realpath(os.path.expanduser("~/Documents")),
    os.path.realpath(os.path.expanduser("~/Desktop")),
    os.path.realpath(os.path.expanduser("~/Projects")),
    os.path.realpath(os.path.expanduser("~/repos")),
    os.path.realpath(os.path.dirname(os.path.abspath(__file__))),
]

# ─────────────────────────────────────────────────────────────────────────────
# Server definition
# ─────────────────────────────────────────────────────────────────────────────

INSTRUCTIONS = """\
You are connected to the complete PineScript v6 reference documentation server.

ABOUT PINESCRIPT v6
───────────────────
PineScript is TradingView's domain-specific language for creating custom
technical indicators, strategies, and libraries that run on the TradingView
platform. Version 6 (v6) is the current production release and introduces
UDTs (user-defined types), methods, enums, polylines, and improved performance.

This server provides complete local PineScript v6 reference documentation
via a ChromaDB vector store with 3,400+ entries covering all functions,
variables, types, constants, keywords, operators, and user guides.

WHEN TO USE EACH TOOL
──────────────────────
LOOKUP TOOLS (use for specific names you know):
  get_function(name)       Full docs for a function: syntax, params, examples
  get_variable(name)       Built-in variable description and behavior
  get_type(name)           Type definition, fields, methods
  get_constant(name)       Constant value and usage
  get_keyword(name)        Keyword syntax and examples
  get_operator(name)       Operator description and examples

SEARCH TOOLS (use when you don't know exact name):
  search_docs(query)               Semantic search across everything
  get_examples(concept)            Find real working code by concept
  search_by_return_type(type)      Find functions returning a type
  list_namespace(namespace)        All members of a namespace

IMPORTANT NOTES
───────────────
- All code examples returned are real, working PineScript from the official
  TradingView documentation.
- PineScript is executed on every bar, so variable semantics differ from
  general-purpose languages.
- Use the `var` keyword for variables that should preserve state across bars.
- Strategy scripts require //@version=6 and strategy() declaration.
- Indicator scripts require //@version=6 and indicator() declaration.
"""

from contextlib import asynccontextmanager

@asynccontextmanager
async def _startup_lifespan(server):
    """Preload embedding model, ChromaDB collection, name index, and hot cache at startup."""
    logger.info("Preloading embedding model...")
    loop = asyncio.get_running_loop()
    model = await loop.run_in_executor(_model_executor, _get_model)
    _embedding_model_ready.set()
    # Warm-up inference: eliminates 50-200ms cold-start on first real query
    await loop.run_in_executor(_model_executor, lambda: model.encode(["warmup"]))
    logger.info("Embedding model ready (warmed up)")

    logger.info("Preloading ChromaDB collection...")
    _get_collection()
    logger.info("ChromaDB collection ready")

    logger.info("Building name index...")
    _build_name_index()
    logger.info("Name index ready")

    logger.info("Building hot cache...")
    success = await build_hot_cache()
    global _hot_cache_built
    if success:
        _hot_cache_built = True
    logger.info("Hot cache ready")

    yield  # server runs here

    # Shutdown: close HTTP client
    _shutdown_http_client()


# ── Response caching middleware ──────────────────────────────────────────────
#
# Two-tier caching at the MCP response level (caches serialized ToolResult):
#
#   1. Lookup middleware (1h TTL): get_function, get_variable, get_type,
#      get_constant, get_keyword, get_operator, list_namespace,
#      get_namespace_cheatsheet. Results are deterministic and stable —
#      ChromaDB docs don't change between re-indexing.
#
#   2. Search middleware (5m TTL): search_docs, get_examples,
#      search_by_return_type, suggest_functions. Same query + same args =
#      same result (deterministic embedding + ChromaDB), but queries vary
#      widely so cache hit rate is lower. 5m TTL caches repeat lookups
#      during a debugging session without going stale.
#
# Validation tools (validate_syntax, validate_and_explain, fix_and_validate,
# debug_pine_facade, validate_file, lookup_and_correct) and codegen tools
# (generate_indicator, generate_strategy) are EXCLUDED — they receive unique
# code on nearly every call, so caching wastes memory and risks stale results.
#
# Relationship to existing internal caches (complementary, not redundant):
#   HOT_CACHE           -> permanent dict of priority entry raw data
#   _QUERY_RESULT_CACHE -> LRU of raw ChromaDB query results (120s, 200 max)
#   _VALIDATION_CACHE   -> LRU of pine-facade compilation results (300s, 500 max)
#   This middleware      -> LRU of final serialized ToolResult objects
#
# Cache keys are SHA-256(tool_name + ":" + JSON(args)) — deterministic,
# so identical tool calls always hit cache within the TTL window.
# Memory overhead: ~50-100 entries at steady state, ~200-500KB total.

_lookup_cache_mw = ResponseCachingMiddleware(
    call_tool_settings={
        "ttl": 3600,  # 1 hour
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
        "ttl": 300,  # 5 minutes
        "enabled": True,
        "included_tools": [
            "search_docs",
            "get_examples",
            "search_by_return_type",
            "suggest_functions",
        ],
    },
)

mcp = FastMCP(
    name="PineScript v6 Complete Reference",
    instructions=INSTRUCTIONS,
    lifespan=_startup_lifespan,
    mask_error_details=True,
    middleware=[
        _lookup_cache_mw,
        _search_cache_mw,
        DetailedTimingMiddleware(),
        ResponseLimitingMiddleware(max_size=500_000),
    ],
)

# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB + Embedding singleton — with circuit-breaker
# ─────────────────────────────────────────────────────────────────────────────

# Removed: _db_failure_count / _DB_FAILURE_LIMIT — replaced by ChromaDBCircuitBreaker
_collection = None
_embed_model = None

# Name index for O(1) exact lookups — built at startup
_name_index: dict[str, list[dict]] = {}  # lowercase name -> [{id, metadata, document}]
_name_index_built: bool = False

# Common PineScript parameter names that should NOT trigger doc lookups
_COMMON_PARAM_NAMES = frozenset({
    "length", "len", "period", "source", "src", "mult", "multiplier", "factor",
    "offset", "basis", "dev", "deviation", "signal", "fast", "slow", "size",
    "threshold", "limit", "color", "title", "minval", "maxval", "step",
    "defval", "group", "inline", "confirm", "options", "tooltip",
    "bar_index", "gap", "style", "width", "transparency",
})

# ── C1: ChromaDB circuit breaker with cooldown + auto-reset ────────────


class ChromaDBCircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown: int = 30):
        self.failures: int = 0
        self.threshold: int = threshold
        self.cooldown: int = cooldown
        self.open_until: float = 0.0

    def is_open(self) -> bool:
        if self.open_until and time.time() > self.open_until:
            self.failures = 0
            self.open_until = 0.0
            logger.info("ChromaDB circuit RESET (cooldown expired)")
        return time.time() < self.open_until

    def record_failure(self, exc: Exception) -> None:
        # NOTE: Must remain synchronous — called from _get_collection() which is sync
        self.failures += 1
        logger.warning(
            f"ChromaDB failure {self.failures}/{self.threshold}: "
            f"{type(exc).__name__}"
        )
        if self.failures >= self.threshold:
            self.open_until = time.time() + self.cooldown
            logger.error(f"ChromaDB circuit OPEN — cooldown {self.cooldown}s")

    def record_success(self) -> None:
        if self.failures:
            self.failures = 0
            self.open_until = 0.0


_chroma_breaker = ChromaDBCircuitBreaker(threshold=3, cooldown=30)


def _build_name_index() -> None:
    """Build in-memory name->entry index for O(1) exact lookups."""
    global _name_index, _name_index_built
    if _name_index_built:
        return
    try:
        col = _get_collection()
        total = col.count()
        result = col.get(include=["metadatas", "documents"], limit=total)
        for rid, meta, doc in zip(result["ids"], result["metadatas"], result["documents"]):
            key = (meta.get("name") or "").lower().strip()
            if key:
                entry = {"id": rid, "metadata": meta, "document": doc}
                if key not in _name_index:
                    _name_index[key] = []
                _name_index[key].append(entry)
        _name_index_built = True
        logger.info(f"Name index built: {len(_name_index)} unique names from {total} entries")
    except Exception as e:
        logger.error(f"Failed to build name index: {e}")

# ── H5: Non-blocking embedding model loader ────────────────────────────
_model_executor = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="embedding"
)
_embedding_model_ready = asyncio.Event()


def _get_collection():
    """Return the ChromaDB collection, initializing lazily. Circuit-breaker aware."""
    global _collection
    if _chroma_breaker.is_open():
        raise RuntimeError(
            "ChromaDB circuit breaker is open (cooldown). "
            "Please wait and try again."
        )
    if _collection is not None:
        # Detect stale cache: if collection count changed, reload
        try:
            current_count = _collection.count()
            if current_count == 0:
                logger.info("ChromaDB collection empty — forcing reload")
                _collection = None
        except Exception:
            _collection = None
        if _collection is not None:
            _chroma_breaker.record_success()
            return _collection
    try:
        import chromadb

        client = chromadb.PersistentClient(path=DB_PATH)
        _collection = client.get_collection(name=COLLECTION)
        count = _collection.count()
        logger.info(f"Connected to ChromaDB - {count} entries")

        # HNSW warmup: force index load with a lightweight query.
        # Eliminates ~14ms cold-start penalty on first real query.
        if count > 0:
            try:
                _collection.query(
                    query_embeddings=[[0.0] * 384],
                    n_results=1,
                    include=["distances"],
                )
                logger.debug("HNSW index warmed up")
            except Exception:
                pass  # Non-critical — first real query will warm it

        _chroma_breaker.record_success()
        return _collection
    except Exception as e:
        _chroma_breaker.record_failure(e)
        logger.error(f"ChromaDB init failed: {e}")
        raise


def _get_model():
    """Return the SentenceTransformer, initializing lazily.

    Uses PyTorch with MPS acceleration on Apple Silicon (faster than ONNX
    due to Metal GPU). Falls back to ONNX on CPU-only systems where
    ONNX Runtime is significantly faster than PyTorch-CPU.
    """
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    try:
        from sentence_transformers import SentenceTransformer
        import torch

        # Apple Silicon: MPS is faster than ONNX for this model size
        # CPU-only systems: ONNX can be 1.4-3x faster than PyTorch-CPU
        if torch.backends.mps.is_available():
            _embed_model = SentenceTransformer(EMBED_MODEL, device="mps")
            logger.info(f"Embedding model loaded: {EMBED_MODEL} (PyTorch/MPS)")
        elif not torch.cuda.is_available():
            # CPU-only: try ONNX for speedup
            try:
                _embed_model = SentenceTransformer(EMBED_MODEL, backend="onnx")
                logger.info(f"Embedding model loaded: {EMBED_MODEL} (ONNX/CPU)")
            except Exception:
                _embed_model = SentenceTransformer(EMBED_MODEL)
                logger.info(f"Embedding model loaded: {EMBED_MODEL} (PyTorch/CPU)")
        else:
            _embed_model = SentenceTransformer(EMBED_MODEL)
            logger.info(f"Embedding model loaded: {EMBED_MODEL} (PyTorch)")

        return _embed_model
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}")
        raise


async def _ensure_embedding_model():
    """Load SentenceTransformer in thread pool — never blocks event loop."""
    if _embedding_model_ready.is_set():
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_model_executor, _get_model)
    _embedding_model_ready.set()


def _query(query_text: str, n: int, where: Optional[dict] = None) -> dict:
    """Run a ChromaDB query with the local embedding model.

    L1 LRU cache on query results for sub-ms repeat lookups.
    Falls through to ChromaDB on cache miss.

    H3: Wraps collection.query() in try/except — never lets ChromaDB
    or embedding exceptions propagate naked to tool handlers.

    NOTE: This is a synchronous function. For use from async tool handlers,
    use `await _query_async(...)` instead to avoid blocking the event loop.
    """
    # L1 cache: deterministic key from query text + n + where
    _cache_key = xxhash.xxh64(f"{query_text}|{n}|{where}".encode()).hexdigest()
    with _QUERY_CACHE_LOCK:
        if _cache_key in _QUERY_RESULT_CACHE:
            cached_result, cached_ts = _QUERY_RESULT_CACHE[_cache_key]
            if time.time() - cached_ts < _QUERY_CACHE_TTL:
                logger.debug(f"L1 cache hit: {query_text[:40]}")
                return cached_result
            else:
                del _QUERY_RESULT_CACHE[_cache_key]
    try:
        model = _get_model()
        col = _get_collection()
        embedding = model.encode([query_text])[0].tolist()

        kwargs: dict = dict(
            query_embeddings=[embedding],
            n_results=min(n, MAX_RESULTS),
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where

        result = col.query(**kwargs)

        # Write back to L1 cache (move to end = most recent)
        with _QUERY_CACHE_LOCK:
            _QUERY_RESULT_CACHE[_cache_key] = (result, time.time())
            _QUERY_RESULT_CACHE.move_to_end(_cache_key)
            # Evict oldest (O(1) via OrderedDict.popitem)
            while len(_QUERY_RESULT_CACHE) > _QUERY_CACHE_MAX:
                _QUERY_RESULT_CACHE.popitem(last=False)

        return result
    except Exception as e:
        error_type = type(e).__name__
        logger.error(
            f"_query() failed | type={error_type} | where={where} | "
            f"query={query_text[:80]}"
        )
        # Propagate error signal so callers distinguish "no results" from "DB down"
        return {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
            "_error": f"{error_type}: {str(e)[:200]}",
        }


async def _query_async(query_text: str, n: int, where: Optional[dict] = None) -> dict:
    """Async wrapper for _query() — runs in thread pool to avoid blocking event loop.

    Embedding model inference + ChromaDB query can take 10-200ms.
    Without this wrapper, every _query() call from async tool handlers
    blocks the entire event loop, preventing concurrent tool execution.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _query, query_text, n, where)


def _get_by_id(entry_id: str) -> Optional[dict]:
    """Fetch a single entry by exact ID."""
    try:
        col = _get_collection()
        result = col.get(ids=[entry_id], include=["documents", "metadatas"])
        if result["ids"]:
            return {
                "id": entry_id,
                "metadata": result["metadatas"][0],
                "document": result["documents"][0],
            }
        return None
    except Exception as e:
        logger.error(f"_get_by_id({entry_id}) failed: {e}")
        return None


def _search_by_name(
    name: str, where: Optional[dict] = None
) -> list[tuple[float, dict]]:
    """Exact then fuzzy name lookup. Scans up to MAX_FUZZY_SCAN_ENTRIES for fuzzy match."""
    try:
        from rapidfuzz import fuzz

        col = _get_collection()
        name_lower = name.lower().strip()

        # BUG FIX: If name contains ".", it's fully qualified — exact match only, no namespace fallback
        if "." in name_lower:
            # Fully qualified — exact match only, no namespace fuzzy fallback
            try:
                exact = col.get(
                    where={"name": name_lower},
                    include=["metadatas", "documents"]
                )
                if exact["ids"]:
                    return [
                        (
                            100.0,
                            {
                                "id": rid,
                                "metadata": meta,
                                "document": doc,
                            },
                        )
                        for rid, meta, doc in zip(
                            exact["ids"], exact["metadatas"], exact["documents"]
                        )
                    ]
            except Exception as e:
                logger.debug(f"Qualified lookup failed: {e}")
            # Try with type=function specifically
            try:
                typed = col.get(
                    where={"$and": [{"name": name_lower}, {"category": "function"}]},
                    include=["metadatas", "documents"]
                )
                if typed["ids"]:
                    return [
                        (
                            100.0,
                            {
                                "id": rid,
                                "metadata": meta,
                                "document": doc,
                            },
                        )
                        for rid, meta, doc in zip(
                            typed["ids"], typed["metadatas"], typed["documents"]
                        )
                    ]
            except Exception as e:
                logger.debug(f"Typed lookup failed: {e}")
            # No result — return empty (do NOT fall through to namespace match)
            return []

        # Fast path: O(1) lookup from pre-built name index
        if _name_index_built:
            hits = _name_index.get(name_lower)
            if hits:
                if where:
                    cat = where.get("category")
                    if cat:
                        hits = [h for h in hits if h["metadata"].get("category") == cat]
                    for clause in where.get("$and", []):
                        if "category" in clause:
                            hits = [h for h in hits if h["metadata"].get("category") == clause["category"]]
                if hits:
                    return [(100.0, h) for h in hits]

        # Strategy 1: exact metadata match (fast, uses ChromaDB index)
        try:
            exact_where: dict = {"name": name_lower}
            if where:
                cat = where.get("category")
                if cat:
                    exact_where = {"$and": [{"name": name_lower}, {"category": cat}]}
            exact = col.get(where=exact_where, include=["metadatas", "documents"])
            if exact["ids"]:
                return [
                    (
                        100.0,
                        {
                            "id": rid,
                            "metadata": meta,
                            "document": doc,
                        },
                    )
                    for rid, meta, doc in zip(
                        exact["ids"], exact["metadatas"], exact["documents"]
                    )
                ]
        except Exception as e:
            logger.debug(f"Exact unqualified lookup failed: {e}")

        # Strategy 2: fuzzy — fetch ALL, filter in Python (capped for safety)
        total = min(col.count(), MAX_FUZZY_SCAN_ENTRIES)
        get_kwargs: dict = dict(include=["metadatas", "documents"], limit=total)
        if where:
            get_kwargs["where"] = where
        result = col.get(**get_kwargs)
        if not result["ids"]:
            return []
        candidates: list[tuple[float, dict]] = []
        for meta, doc, rid in zip(
            result["metadatas"], result["documents"], result["ids"]
        ):
            entry_name = (meta.get("name") or "").lower().replace("()", "").strip()
            ratio = fuzz.ratio(name_lower, entry_name)
            candidates.append((ratio, {"id": rid, "metadata": meta, "document": doc}))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates
    except Exception as e:
        logger.error(f"_search_by_name({name}) failed: {e}")
        return []


def _get_all_where(where: dict | None, limit: int | None = None) -> list[dict]:
    """Fetch all entries matching a where filter. Defaults to full collection."""
    try:
        col = _get_collection()
        if limit is None:
            limit = col.count()
        # Handle empty where clause - ChromaDB doesn't accept {} as where
        if where:
            result = col.get(where=where, include=["metadatas", "documents"], limit=limit)
        else:
            result = col.get(include=["metadatas", "documents"], limit=limit)
        entries = []
        for rid, meta, doc in zip(
            result["ids"], result["metadatas"], result["documents"]
        ):
            entries.append({"id": rid, "metadata": meta, "document": doc})
        return entries
    except Exception as e:
        logger.error(f"_get_all_where failed: {e}")
        return []


async def _search_by_name_async(name: str, where: Optional[dict] = None) -> list[tuple[float, dict]]:
    """Async wrapper for _search_by_name — avoids blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _search_by_name, name, where)


async def _get_all_where_async(where: dict | None, limit: int | None = None) -> list[dict]:
    """Async wrapper for _get_all_where — avoids blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _get_all_where, where, limit)


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

_BOX_TL = "\u2554"
_BOX_TR = "\u2557"
_BOX_BL = "\u255a"
_BOX_BR = "\u255d"
_BOX_H = "\u2550"
_BOX_V = "\u2562"
_BOX_MID = "\u2564"
_DIVIDER = "\u2500" * 70


def _relevance_pct(distance: float) -> str:
    """Convert cosine distance to human-readable relevance %."""
    relevance = max(0.0, 1.0 - distance) * 100
    return f"{relevance:.0f}%"


def _section_line(text: str = "") -> str:
    return f"{_BOX_V} {text}"


def _source_tag(meta: dict) -> str:
    return "[Local]"


def _source_line(meta: dict) -> str:
    return _section_line("SOURCE: [Local]")


def _format_params_text(meta: dict) -> str:
    """Format parameters from raw_parameters metadata."""
    raw_params = meta.get("raw_parameters", "")
    if not raw_params:
        param_count = meta.get("param_count", 0)
        if param_count:
            return _section_line(f"({param_count} parameters — see raw_parameters)")
        return ""

    try:
        params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
    except (json.JSONDecodeError, TypeError):
        return ""

    if not params:
        return ""

    lines = [_section_line(f"PARAMETERS ({len(params)})")]
    for p in params:
        pname = p.get("name", "?")
        ptype = p.get("type", "")
        pdesc = p.get("description", "")
        opt = " [optional]" if p.get("optional") else ""
        default = f" = {p['default']}" if p.get("default") else ""
        ptype_str = f" ({ptype})" if ptype else ""
        lines.append(f"  {pname}{ptype_str}{opt}{default}")
        if pdesc:
            lines.append(f"    {pdesc}")
    return "\n".join(lines)


def _format_examples_text(meta: dict) -> str:
    """Format examples from raw_examples metadata."""
    raw_ex = meta.get("raw_examples", "")
    if not raw_ex:
        ex_count = meta.get("example_count", 0)
        if ex_count:
            return _section_line(f"({ex_count} examples — see raw_examples)")
        return ""

    blocks = [b.strip() for b in raw_ex.split(" ||| ") if b.strip()]
    if not blocks:
        return ""

    # FIX 4: Deduplicate examples
    blocks = _dedup_examples(blocks)

    lines = [_section_line(f"EXAMPLES ({len(blocks)})")]
    for i, ex in enumerate(blocks, 1):
        lines.append(f"  {'─' * 50}")
        lines.append(f"  Example {i}")
        for code_line in ex.splitlines():
            lines.append(f"  {code_line}")
        lines.append("")
    return "\n".join(lines)


def _format_type_info(meta: dict) -> str:
    """Format type fields and methods."""
    raw_fields = meta.get("raw_type_fields", "")
    if not raw_fields:
        return ""

    lines = []
    try:
        fields = json.loads(raw_fields) if isinstance(raw_fields, str) else raw_fields
    except (json.JSONDecodeError, TypeError):
        return ""

    if fields:
        lines.append(_section_line("FIELDS"))
        for f in fields:
            fname = f.get("name", "?")
            ftype = f.get("type", "")
            fdesc = f.get("description", "")
            ftype_str = f" ({ftype})" if ftype else ""
            lines.append(f"  {fname}{ftype_str}")
            if fdesc:
                lines.append(f"    {fdesc}")
    return "\n".join(lines)


def _dedup_examples(examples: list[str]) -> list[str]:
    """Remove duplicate examples by comparing whitespace-normalized content.
    Prefers formatted versions (more newlines) over collapsed ones."""
    seen: dict[str, str] = {}  # normalized_key -> best example
    for ex in examples:
        key = re.sub(r'\s+', '', ex).lower()[:120]
        existing = seen.get(key)
        if existing is None:
            seen[key] = ex
        else:
            # Prefer the version with more newlines (formatted over collapsed)
            if ex.count("\n") > existing.count("\n"):
                seen[key] = ex
    return list(seen.values())


def _format_entry_detail(
    name: str, meta: dict, doc: str, distance: Optional[float] = None
) -> str:
    """Format a complete detailed entry for get_* tools."""
    
    # FIX 2B: Check for hollow results
    if not doc or len(doc) < 50:
        entry_type = meta.get("type", meta.get("category", "unknown"))
        return (
            f"'{name}' was found but has no local documentation.\n"
            f"This is likely a newer v6 feature not yet indexed."
        )
    
    lines: list[str] = []

    category = meta.get("category", "?").upper()
    namespace = meta.get("namespace") or ""
    syntax = meta.get("syntax") or ""
    description = meta.get("raw_description", "")
    returns = meta.get("returns") or ""
    remarks = meta.get("remarks") or ""
    see_also_raw = meta.get("raw_see_also", "")
    rel = f"  (Relevance: {_relevance_pct(distance)})" if distance is not None else ""
    ns = f"{namespace}." if namespace and not name.startswith(f"{namespace}.") else ""

    lines.append(f"{_BOX_TL}{_BOX_H * 60}{_BOX_TR}")
    lines.append(f"{_BOX_V} {category}: {ns}{name}{rel}")
    lines.append(_source_line(meta))
    lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")

    if syntax:
        lines.append(f"{_BOX_V} SYNTAX")
        lines.append(f"{_BOX_V} {syntax}")

    if description:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(f"{_BOX_V} DESCRIPTION")
        for dline in description.splitlines():
            lines.append(f"{_BOX_V} {dline}" if dline.strip() else _BOX_V)

    param_text = _format_params_text(meta)
    if param_text:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(param_text)

    if returns:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(_section_line(f"RETURNS: {returns}"))

    if remarks:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(_section_line("REMARKS"))
        for rline in remarks.splitlines():
            lines.append(f"{_BOX_V} {rline}" if rline.strip() else _BOX_V)

    # Type fields
    type_text = _format_type_info(meta)
    if type_text:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(type_text)

    # Examples
    ex_text = _format_examples_text(meta)
    if ex_text:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(ex_text)

    if see_also_raw:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(_section_line(f"SEE ALSO: {see_also_raw}"))

    lines.append(f"{_BOX_BL}{_BOX_H * 60}{_BOX_BR}")
    return _cap_response("\n".join(lines))


def _check_query_error(results: dict) -> str | None:
    """Check if a _query result indicates a database failure.

    Returns an error message if the database was unreachable, or None if
    the result is valid (including empty-but-valid results).
    """
    if "_error" in results:
        return (
            "⚠️ DATABASE UNAVAILABLE\n"
            "The ChromaDB vector store could not process this query.\n"
            "This is a transient error — please retry in a few seconds.\n"
            f"Detail: {results['_error']}"
        )
    return None

def _error(tool: str, msg: str) -> str:
    logger.error(f"[{tool}] {msg}")
    return f"ERROR [{tool}]: {msg}"


# M11: Sanitize error messages for user-facing output
_PATH_PATTERN = re.compile(r"(/[\w./\-]+|[A-Z]:\\[\\\w.\\-]+)")


def _safe_error(exc: Exception, context: str = "") -> str:
    """Return a user-safe error string — removes paths, caps length."""
    msg = str(exc)
    msg = _PATH_PATTERN.sub("[path]", msg)
    if len(msg) > 200:
        msg = msg[:200] + "..."
    prefix = f"[{context}] " if context else ""
    return f"{prefix}{type(exc).__name__}: {msg}"


# M9: Cap tool response size
def _cap_response(text: str, limit: int = MAX_TOOL_RESPONSE_CHARS) -> str:
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    last_fence = truncated.rfind("```")
    if last_fence > limit * 0.8:
        truncated = truncated[:last_fence]
    omitted = len(text) - len(truncated)
    return truncated + f"\n\n[...truncated — {omitted:,} chars omitted]"


# M15: Sanitize null bytes and control characters
def _sanitize_text(text: str) -> str:
    """Remove null bytes and non-printable control characters."""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\x00", "")
    text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text.strip()


# M16: Sanitize name for PineScript string literals
def _sanitize_pine_string(s: str) -> str:
    """Make a string safe for embedding in PineScript string literals."""
    s = s.replace('"', "'")
    s = s.replace("\\", "/")
    s = re.sub(r"[\x00-\x1f]", "", s)
    s = s.strip()
    return s[:100]


def _circuit_breaker_msg() -> str:
    return (
        "DATABASE UNAVAILABLE\n"
        "The ChromaDB vector store has encountered repeated failures.\n"
        "To resolve:\n"
        "  1. Ensure pinescript_db/ exists next to pinescript_mcp.py\n"
        "  2. Run: python merge_and_index.py\n"
        "  3. Restart the MCP server"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pine-facade compiler integration
# ─────────────────────────────────────────────────────────────────────────────


class PineFacadeCircuitBreaker:
    """Circuit breaker for pine-facade API calls.

    Separates network failures (connection refused, timeout, DNS) from
    compiler responses (4xx/5xx HTTP with a valid body). Only network
    failures trip the breaker — compiler errors are expected and don't
    indicate the service is down.
    """

    def __init__(self, threshold: int = 10, cooldown: int = 60):
        self.network_failures: int = 0
        self.threshold = threshold
        self.cooldown = cooldown
        self.open_until: float = 0.0
        self.total_calls: int = 0
        self.total_network_errors: int = 0
        self.total_compiler_errors: int = 0
        self.total_successes: int = 0

    def is_open(self) -> bool:
        return time.time() < self.open_until

    def record_network_failure(self) -> None:
        """Record a network-level failure (timeout, connection refused, DNS).
        These indicate the service is unreachable and SHOULD trip the breaker.
        Uses exponential backoff with jitter: 60s, 120s, 240s... ±15% jitter.
        """
        self.network_failures += 1
        self.total_network_errors += 1
        self.total_calls += 1
        if self.network_failures >= self.threshold:
            # Exponential backoff: base * 2^(failure_count - threshold)
            backoff_power = min(self.network_failures - self.threshold, 5)
            base_cooldown = self.cooldown * (2 ** backoff_power)
            # Cap at 10 minutes
            base_cooldown = min(base_cooldown, 600)
            # ±15% jitter to avoid thundering herd
            jitter = base_cooldown * 0.15 * (random.random() * 2 - 1)
            actual_cooldown = max(30, base_cooldown + jitter)
            self.open_until = time.time() + actual_cooldown
            logger.warning(
                f"Pine-facade circuit OPEN for {actual_cooldown:.0f}s "
                f"({self.network_failures} consecutive network failures, "
                f"backoff power={backoff_power})"
            )

    def record_compiler_error(self) -> None:
        """Record a compiler response (HTTP 200 with errors in JSON body).
        These are EXPECTED and should NOT trip the breaker.
        """
        self.total_compiler_errors += 1
        self.total_calls += 1
        # Compiler errors don't accumulate — reset network counter
        self.network_failures = 0

    def record_success(self) -> None:
        """Record a successful compilation (HTTP 200, no errors)."""
        self.total_successes += 1
        self.total_calls += 1
        self.network_failures = 0
        self.open_until = 0.0

    def stats(self) -> dict:
        return {
            "circuit_open": self.is_open(),
            "network_failures": self.network_failures,
            "total_calls": self.total_calls,
            "total_network_errors": self.total_network_errors,
            "total_compiler_errors": self.total_compiler_errors,
            "total_successes": self.total_successes,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
        }


_pine_cb = PineFacadeCircuitBreaker()
_facade_http_client: Optional[httpx.AsyncClient] = None

_VALIDATION_CACHE: OrderedDict[str, tuple[str, float]] = OrderedDict()
_VALIDATION_CACHE_LOCK = threading.Lock()

# File-level validation cache: key=(path, mtime_ns, size), value=(result_str, timestamp)
# Skips disk read + linter + network if file unchanged since last validation.
_FILE_VALIDATION_CACHE: OrderedDict[tuple[str, int, int], tuple[str, float]] = OrderedDict()
_FILE_VALIDATION_CACHE_LOCK = threading.Lock()
_FILE_VALIDATION_CACHE_TTL = float(os.getenv("FILE_VALIDATION_CACHE_TTL", "1800"))  # 30 min default
_FILE_VALIDATION_CACHE_MAX = int(os.getenv("FILE_VALIDATION_CACHE_SIZE", "200"))

# L1 query result cache — avoids re-embedding identical queries
_QUERY_RESULT_CACHE: OrderedDict[str, tuple[dict, float]] = OrderedDict()
_QUERY_CACHE_LOCK = threading.Lock()
_QUERY_CACHE_TTL = 120.0  # seconds — L1 ChromaDB query result cache
_QUERY_CACHE_MAX = 200  # max entries before eviction

_FIX_HINTS: dict[str, str] = {
    "Undeclared identifier": "Variable not declared. Add 'var float {name} = na' before use, or check spelling. In v6, all identifiers must be declared.",
    "Cannot call": "Wrong argument type or count. Check parameter types with get_function().",
    "Cannot cast": "Type mismatch. PineScript is strongly typed — use explicit type conversions.",
    "Add to chart is not allowed": "Use plot(), plotshape(), or another visual output function.",
    "Loop is too long": "Pine limits loop body size. Extract logic into a function: 'f(x) => ...body...' and call f() inside the loop.",
    "Function must return a result": "All branches of if/switch must return a value. Add an else clause.",
    "Series is not allowed": "This context requires simple/const type, not series. Use ta.valuewhen() or barstate lookups.",
    "Variable is undefined": "Declare the variable before use with := for reassignment or = for initial assignment.",
    "Mismatched input": "Syntax error — check for missing commas, parentheses, or brackets.",
    "An argument of type": "Wrong type passed to function. Check the function signature with get_function().",
    "The 'strategy' namespace": "strategy.* functions require strategy() declaration, not indicator().",
    "Script could not be translated": "Major syntax error. Check //@version=6 header and function declarations.",
    "Cannot use 'strategy'": "Strategy functions require //@version=6 and strategy() declaration at the top.",
    "Recursive call": "PineScript does not support direct recursion. Use a var variable or request.security().",
    "Cannot call method": "Method call on wrong type. Check the variable type with get_type(). Example: array methods require an array<type> variable.",
    "Loop body is too long": "Pine limits loop body size. Extract logic into a function: 'f(x) => ...body...' and call f() inside the loop.",
    "The 'series' type is not supported here": "This parameter requires 'simple' or 'const' — not a dynamic series. Assign the value to a variable with 'var' outside the function call.",
    "Casting is not possible": "Incompatible types. Use explicit conversion: int(x), float(x), str.tostring(x), or str.tonumber(x).",
    "Cannot use 'var' in this context": "'var' only works for persistent variables at the bar level. Move the declaration outside of if/for/while blocks.",
    "Function must return a value": "All code paths in a function must return a value. Add a final 'else =>' or default return at the end.",
    "Argument 'source' must be a 'series float'": "The source input requires a price series (close, open, etc.) or a series float variable. Check what you passed as the source argument.",
    "Cannot use request.security inside": "request.security() cannot be nested inside loops or other request.security() calls. Cache the result in a variable first.",
    "Supported versions are >=": "Missing or wrong version declaration. First line must be exactly: '//@version=6'",
    "Please use 'var' or 'varip' to declare": "Variable reassignment without declaration. Change '=' to ':=' for reassignment, or add 'var float x = na' to declare first.",
    "Condition must be 'bool'": "If/while condition must be boolean. Use comparison operators: ==, !=, >, <, >=, <=, 'and', 'or', 'not'.",
    "Cannot mix 'series' and 'simple'": "Mixing series and simple/const contexts. Wrap the call in a request.security() or ensure both sides are the same qualifier.",
    "No overload of function": "Wrong number or types of arguments. Call get_function(name) for exact parameter list and types.",
    "Cannot convert 'series float' to 'bool'": "v6 removed implicit bool casting. Use explicit comparison: e.g., if volume > 0 instead of if volume, if close instead use if close > 0.",
    "An argument 'when' of": "v6 removed the 'when' parameter from strategy.entry/exit. Use an if block: if condition \\n strategy.entry(...)",
    "division operator": "v6 changed integer division: 3/2 now returns 1.5 (float), not 1. Use math.floor(a/b) or int(a/b) for integer division.",
    # ── v6 breaking changes (8 new entries from research) ──
    "transp": "v6 removed the 'transp' parameter from plot(), fill(), bgcolor(), etc. Use color.new(color, transparency) instead, where transparency is 0 (opaque) to 100 (invisible).",
    "Duplicate argument": "v6 disallows duplicate named arguments in function calls. Remove the duplicate parameter — only one of each name is allowed.",
    "Cannot use operator '[]'": "v6 restricts history operator []. For UDT fields use (obj[n]).field syntax. Literals/constants (6[1], true[10]) are invalid. Cache value in a variable before using [].",
    "no longer accepts 'bool'": "v6 tightened type requirements — this parameter no longer accepts 'bool' where it once did. Pass the expected type explicitly.",
    "Cannot assign 'na' to": "v6 requires unique types for 'na'. Declare explicitly: 'var float x = na'. Unique type constants (plot.style_*, xloc.*) need a default branch: => plot.style_line.",
    "offset": "v6 changed 'offset' parameter: it no longer accepts 'series int', only 'simple int'. Calculate the offset outside the call and pass the result.",
    "linewidth": "v6 enforces minimum linewidth of 1. Use linewidth=1 or higher. Zero or negative values are no longer accepted.",
    "margin": "v6 changed default margin from 0 to 100% (no margin trading). Set margin_long=0 and margin_short=0 in strategy() to restore margin behavior.",
    # ── v6 edge cases from deep research (8 more entries) ──
    "Cannot call 'na()' with": "v6 booleans cannot be na. na()/nz()/fixnan() no longer accept bool arguments. Use int (-1/0/1) or an enum for three-state logic.",
    "Cannot call 'request.security' from": "v6 with dynamic_requests=false blocks request.*() in local scopes. Remove dynamic_requests=false (defaults to true) or move request.*() to global scope.",
    "series int' type was used but a 'simple": "v6 correctly qualifies mutable variables (modified with :=/+=) as 'series'. Pass a const/input value to parameters expecting 'simple' or 'const' types.",
    "closedtrades": "v6 trims oldest trades past the 9000 limit. Use strategy.closedtrades.first_index as the starting index when looping — trimmed trades return na.",
    "strategy.exit": "v6 strategy.exit() evaluates BOTH relative (profit/loss/trail_points) AND absolute (limit/stop) parameters. Remove zero-valued relative params that v5 silently ignored.",
    "timeframe.period": "v6 timeframe.period always includes a multiplier: '1D' not 'D', '1W' not 'W'. Use timeframe.isdaily/isweekly/ismonthly for cleaner comparisons.",
    # ── Runtime errors ──
    "requested historical offset": "Script references more history than the buffer allows. Add max_bars_back=5000 to indicator()/strategy(), or use max_bars_back(varName, N) for specific variables.",
    "Too many drawings": "Drawing objects exceed the limit. Set max_lines_count=500, max_labels_count=500, or max_boxes_count=500 in your declaration.",
    "too many local variables": "Each scope has a 1000-variable limit. Inline expressions to reduce count, or extract into helper functions.",
    "too many securities": "Pine limits to 40 request.security() calls. Combine calls using tuples, or wrap in a UDF and reuse the result.",
    "Loop took too long": "Loop exceeded the 500ms per-bar timeout. Reduce iteration count, optimize loop body, or precompute outside the loop.",
    "memory limit": "Exceeded Pine's memory limits. Reduce drawing count, use smaller arrays (max 100,000 elements), or reduce request.*() data volume.",
    # ── v6 compilation errors (from migration research) ──
    "Syntax error at input": "Check function syntax — v6 uses '=>' for inline functions. Verify commas between parameters and correct indentation.",
    "should be called on each calculation": "History-dependent function (ta.rsi, ta.ema, etc.) called inside conditional/loop. Move the call to global scope, store in a variable, then use that variable conditionally.",
    "Cannot use 'na' as": "v6 requires typed na. Use float(na), int(na), or 'var float x = na'. Bare na not allowed where a specific type is expected.",
    "cannot add string and": "PineScript does not auto-convert numbers to strings. Use str.tostring(value): 'Price: ' + str.tostring(close).",
    "Compilation request size": "Script too large for compiler. Remove unused imports (entire library compiles even if you use one function), inline logic, or split into smaller scripts.",
    "is not found in the namespace": "Wrong import alias or missing import. Check library import uses 'author/libraryName/version' format and alias matches usage.",
    "Invalid test for": "Cannot test na in bool context (e.g., 'if pivot' where pivot can be na). Use 'if not na(pivot)' instead. Booleans strictly true/false in v6.",
    # ── General common errors ──
    "Reserved keyword": "PineScript reserves words like 'strategy', 'plot', 'if'. Rename variable: 'strategy = 1' → 'myStrategy = 1'.",
    "lookahead": "request.security() with lookahead=barmerge.lookahead_on peeks into future (repainting). Use barmerge.lookahead_off (v6 default) for honest backtests.",
    "repainting": "Signal uses future data or unconfirmed bar values. Guard with barstate.isconfirmed. Avoid lookahead=barmerge.lookahead_on.",
}


def _get_facade_client() -> httpx.AsyncClient:
    """Lazy-init a shared httpx.AsyncClient for pine-facade calls."""
    global _facade_http_client
    if _facade_http_client is None or _facade_http_client.is_closed:
        _facade_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(PINE_FACADE_TIMEOUT), connect=5.0),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            headers={
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "DNT": "1",
            },
        )
    return _facade_http_client


def _shutdown_http_client():
    global _facade_http_client
    if _facade_http_client and not _facade_http_client.is_closed:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — create a fresh one to close the client
            try:
                asyncio.run(_facade_http_client.aclose())
            except Exception as e:
                logger.debug(f"HTTP shutdown (new loop) error: {e}")
            finally:
                _facade_http_client = None
            return
        try:
            if loop.is_running():
                loop.create_task(_facade_http_client.aclose())
            else:
                loop.run_until_complete(_facade_http_client.aclose())
        except Exception as e:
            logger.debug(f"HTTP shutdown error: {e}")
        finally:
            _facade_http_client = None


atexit.register(_shutdown_http_client)


def _lookup_fix_hint(error_text: str) -> str:
    """Match an error message against known patterns and return a fix hint."""
    for pattern, hint in _FIX_HINTS.items():
        if pattern.lower() in error_text.lower():
            # Resolve {name} placeholder in hint using identifier from error text
            name_match = re.search(r"'([a-zA-Z_][a-zA-Z0-9_.]*)'", error_text)
            if name_match:
                hint = hint.replace("{name}", name_match.group(1))
            else:
                hint = hint.replace("{name}", "value")
            return hint
    return "Check the PineScript v6 reference for the correct syntax."


def _extract_name_from_error(error_text: str) -> Optional[str]:
    """Extract a likely PineScript name from a compiler error message."""

    # Pattern: "Undeclared identifier 'ta.supertrend'" → ta.supertrend
    m = re.search(r"'([a-zA-Z_][a-zA-Z0-9_.]*)'", error_text)
    if m:
        return m.group(1)
    # Pattern: "Cannot call 'ta.ema'" → ta.ema
    m = re.search(r"call\s+['\"]?([a-zA-Z_][a-zA-Z0-9_.]*)", error_text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Pattern: "An argument of type 'series float'..." → look for function name
    m = re.search(
        r"function\s+['\"]?([a-zA-Z_][a-zA-Z0-9_.]*)", error_text, re.IGNORECASE
    )
    if m:
        return m.group(1)
    return None


def _get_cached_validation(code: str) -> Optional[dict]:
    """Return cached validation result if still fresh. Returns parsed dict."""
    h = xxhash.xxh64(code.encode()).hexdigest()
    with _VALIDATION_CACHE_LOCK:
        if h in _VALIDATION_CACHE:
            result_str, ts = _VALIDATION_CACHE[h]
            if time.time() - ts < VALIDATION_CACHE_TTL:
                try:
                    return json.loads(result_str)
                except json.JSONDecodeError:
                    logger.warning("Corrupt validation cache entry — evicting")
                    del _VALIDATION_CACHE[h]
    return None


def _cache_validation(code: str, result: str) -> None:
    """Store a validation result in cache."""
    h = xxhash.xxh64(code.encode()).hexdigest()
    with _VALIDATION_CACHE_LOCK:
        _VALIDATION_CACHE[h] = (result, time.time())
        _VALIDATION_CACHE.move_to_end(h)
        # O(1) eviction via OrderedDict
        while len(_VALIDATION_CACHE) > VALIDATION_CACHE_MAX_SIZE:
            _VALIDATION_CACHE.popitem(last=False)


def _get_cached_file_validation(file_path: str, mtime_ns: int, file_size: int) -> Optional[str]:
    """Return cached file validation result if file fingerprint matches and entry is fresh.

    Keyed on (resolved_path, mtime_ns, size) — if the file hasn't changed,
    the result is still valid regardless of TTL. TTL only expires entries
    for files that may have been deleted or replaced.
    """
    key = (file_path, mtime_ns, file_size)
    with _FILE_VALIDATION_CACHE_LOCK:
        if key in _FILE_VALIDATION_CACHE:
            result_str, ts = _FILE_VALIDATION_CACHE[key]
            # If mtime+size match, file content is identical — extend TTL effectively forever
            # Still expire very old entries to bound memory
            if time.time() - ts < _FILE_VALIDATION_CACHE_TTL:
                logger.debug(f"File validation cache hit: {file_path}")
                return result_str
            else:
                del _FILE_VALIDATION_CACHE[key]
    return None


def _cache_file_validation(file_path: str, mtime_ns: int, file_size: int, result: str) -> None:
    """Store a file validation result keyed by file fingerprint."""
    key = (file_path, mtime_ns, file_size)
    with _FILE_VALIDATION_CACHE_LOCK:
        _FILE_VALIDATION_CACHE[key] = (result, time.time())
        _FILE_VALIDATION_CACHE.move_to_end(key)
        # O(1) eviction via OrderedDict
        while len(_FILE_VALIDATION_CACHE) > _FILE_VALIDATION_CACHE_MAX:
            _FILE_VALIDATION_CACHE.popitem(last=False)


def _normalize_facade_response(raw: dict) -> dict:
    """Normalize /compile API response.

    Success shape (/compile):
        { "success": true, "result": { ... } }

    Error shape (/compile with compile errors):
        { "success": false, "result": { "errors": [...] } }
        Each error: { "line": int, "column": int, "message": str, "code": str }

    Rejection shape (version too old, etc.):
        { "success": false, "reason": "...", "result": null }

    The /compile endpoint returns success=false on compile errors with
    an "errors" array inside the "result" object.
    """
    success = raw.get("success", False)

    result_obj = raw.get("result") or {}
    raw_errors = result_obj.get("errors", []) if isinstance(result_obj, dict) else []

    # /compile may also put errors at top level
    if not raw_errors and "errors" in raw:
        raw_errors = raw.get("errors", [])

    # Handle rejection shape (success=false with reason, result=null)
    if not success and not raw_errors:
        reason = raw.get("reason", "Unknown compilation failure")
        return {
            "success": False,
            "errors": [{"line": 0, "column": 0, "text": reason, "type": "error"}],
            "warnings": [],
            "meta": {},
            "raw_response": raw,
        }

    def normalize_error(e: dict) -> dict:
        text = e.get("text") or e.get("message") or e.get("msg") or str(e)
        # TradingView pine-facade returns template variables like {kind}, {fullName},
        # {funId}, {argDisplayName}, {argumentType}, {identifier}, etc.
        # Step 1: Resolve from error object fields
        for key, val in e.items():
            if isinstance(val, (str, int, float)) and key not in ("line", "column", "col",
                "lineNumber", "type", "severity", "start", "end"):
                placeholder = f"{{{key}}}"
                if placeholder in text:
                    text = text.replace(placeholder, str(val))
        return {
            "line": e.get("line")
            or e.get("lineNumber")
            or e.get("start", {}).get("line", 0),
            "column": e.get("column")
            or e.get("col")
            or e.get("start", {}).get("column", 0),
            "text": text,
            "type": e.get("type") or "error",
        }

    all_normalized = [normalize_error(e) for e in raw_errors if isinstance(e, dict)]
    # Separate errors from warnings — warnings must NOT appear in errors list
    errors = [e for e in all_normalized if e.get("type") != "warning"]
    warnings = [e for e in all_normalized if e.get("type") == "warning"]

    # Meta from result object - extract useful fields
    meta = {}
    if isinstance(result_obj, dict):
        for key in ("variables", "functions", "types", "enums", "scopes"):
            if key in result_obj:
                meta[key] = result_obj[key]

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "meta": meta,
        "raw_response": raw,
    }


def _enrich_error_with_code(errors: list[dict], code: str) -> list[dict]:
    """Resolve remaining {placeholder} vars in error text using source code context."""
    if not code:
        return errors
    code_lines = code.splitlines()
    placeholder_re = re.compile(r"\{(\w+)\}")

    for err in errors:
        text = err.get("text", "")
        if not placeholder_re.search(text):
            continue

        # Extract identifier/expression at error position from source code
        line_num = err.get("line", 0)
        col_num = err.get("column", 0)
        ident = ""
        line_text = ""
        if isinstance(line_num, int) and 0 < line_num <= len(code_lines):
            line_text = code_lines[line_num - 1]
            if isinstance(col_num, int) and 0 < col_num <= len(line_text):
                i = col_num - 1
                while i < len(line_text) and (line_text[i].isalnum() or line_text[i] in "_."):
                    ident += line_text[i]
                    i += 1

        # Resolve known placeholders using extracted context
        replacements = {
            "identifier": ident or "value",
            "name": ident or "value",
            "kind": "identifier",
            "fullName": ident or "value",
            "funId": ident or "function",
            "funName": ident or "function",
            "argDisplayName": ident or "argument",
            "argUserFriendlyRepresentation": ident or "value",
            "argumentType": "type",
            "currentTypeDocStr": "expected type",
            "typePostfix": "",
            "scope": "scope",
        }
        for ph_key, ph_val in replacements.items():
            text = text.replace(f"{{{ph_key}}}", ph_val)

        # Catch any remaining unknown {placeholders}
        text = placeholder_re.sub(lambda m: m.group(1), text)
        err["text"] = text
    return errors


async def _call_pine_facade(code: str, *, skip_lint: bool = False) -> dict:
    """POST code to pine-facade compiler. Returns normalized response dict.

    Checks content-hash cache FIRST (avoids linter + network on repeat calls).
    Then runs local Tier 1 linter, then attempts remote compile.
    If remote fails, returns local linter results as fallback.

    Args:
        code: PineScript source code to validate.
        skip_lint: If True, skip local linter (caller already ran it).

    Returns:
        {
            "success": bool,
            "errors": [{"line", "column", "text", "type"}, ...],
            "warnings": [{"line", "column", "text"}, ...],
            "meta": dict,
            "raw_response": dict
        }
    """
    # Guard: reject empty/whitespace-only code before any work
    if not code or not code.strip():
        return {
            "success": False,
            "errors": [{"line": 0, "column": 0, "text": "No code provided — empty source", "type": "error"}],
            "warnings": [],
            "meta": {},
            "raw_response": {},
        }

    # Fast path: check content-hash cache BEFORE running linter or network call.
    # On cache hit, returns in ~0.5ms instead of ~15ms (linter) or ~2800ms (remote).
    cached = _get_cached_validation(code)
    if cached:
        return cached

    # Tier 1: Run local linter (instant, always available)
    local_result = _pine_lint(code) if not skip_lint else None

    # Lazy linter: ensures local_result is populated when needed for fallback paths.
    # Avoids running the linter twice when skip_lint=True (caller already ran it).
    def _ensure_lint():
        nonlocal local_result
        if local_result is None:
            local_result = _pine_lint(code)
        return local_result

    if _pine_cb.is_open():
        # Remote unavailable — return local linter results as fallback
        logger.info("Circuit breaker open, returning local linter results")
        lint_dict = _ensure_lint().to_dict()
        lint_dict["meta"]["fallback"] = "local_linter_tier1"
        lint_dict["meta"]["note"] = "Remote compiler unavailable. Local linter catches ~50% of errors."
        return lint_dict

    code = _sanitize_text(code)

    try:
        client = _get_facade_client()
        resp = await client.post(
            PINE_FACADE_URL,
            files={"source": (None, code)},
        )

        if resp.status_code == 403:
            # 403 is most likely anti-automation/auth rejection, not rate limiting.
            logger.warning(
                f"pine-facade 403 — headers: {dict(resp.headers)} | "
                f"body: {resp.text[:200]}"
            )
            _pine_cb.record_network_failure()
            # Return local linter results as fallback
            lint_dict = _ensure_lint().to_dict()
            lint_dict["meta"]["fallback"] = "local_linter_tier1"
            lint_dict["meta"]["note"] = (
                "Remote compiler returned HTTP 403 (access denied). "
                "Showing local linter results — catches ~50% of common errors. "
                "Validate in TradingView's Pine Editor for full compilation."
            )
            lint_dict["raw_response"] = {
                "http_status": resp.status_code,
                "body": resp.text[:200],
            }
            return lint_dict

        if resp.status_code in (502, 503, 504):
            _pine_cb.record_network_failure()
            lint_dict = _ensure_lint().to_dict()
            lint_dict["meta"]["fallback"] = "local_linter_tier1"
            lint_dict["meta"]["note"] = (
                f"Remote compiler returned HTTP {resp.status_code} (service unavailable). "
                "Showing local linter results."
            )
            lint_dict["raw_response"] = {
                "http_status": resp.status_code,
                "body": resp.text[:200],
            }
            return lint_dict

        if resp.status_code != 200:
            if resp.status_code in (400, 429):
                try:
                    data = resp.json()
                    normalized = _normalize_facade_response(data)
                    _cache_validation(code, json.dumps(normalized))
                    return normalized
                except Exception as e:
                    logger.debug(f"Cache write failed: {e}")
            else:
                _pine_cb.record_network_failure()

            return {
                "success": False,
                "errors": [
                    {
                        "line": 0,
                        "column": 0,
                        "text": f"HTTP {resp.status_code}: {resp.text[:200]}",
                        "type": "http",
                    }
                ],
                "warnings": [],
                "meta": {},
                "raw_response": {
                    "http_status": resp.status_code,
                    "body": resp.text[:500],
                },
            }

        # HTTP 200 — parse the response
        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.error(
                f"pine-facade returned non-JSON (HTTP {resp.status_code}): "
                f"{resp.text[:200]}"
            )
            return {
                "success": False,
                "errors": [
                    {
                        "line": 0,
                        "column": 0,
                        "text": "Compiler returned non-JSON response",
                        "type": "error",
                    }
                ],
                "warnings": [],
                "meta": {},
                "raw_response": {"raw_text": resp.text[:500]},
            }
        normalized = _normalize_facade_response(data)

        if normalized["success"]:
            _pine_cb.record_success()
        else:
            _pine_cb.record_compiler_error()

        _cache_validation(code, json.dumps(normalized))
        return normalized

    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.PoolTimeout,
        httpx.WriteTimeout,
        OSError,
    ) as e:
        _pine_cb.record_network_failure()
        logger.error(f"[_call_pine_facade] network error: {e}")
        lint_dict = _ensure_lint().to_dict()
        lint_dict["meta"]["fallback"] = "local_linter_tier1"
        lint_dict["meta"]["note"] = (
            f"Remote compiler unreachable ({type(e).__name__}). "
            "Showing local linter results — catches ~50% of common errors."
        )
        lint_dict["raw_response"] = {"exception": str(e)}
        return lint_dict
    except Exception as e:
        logger.error(f"[_call_pine_facade] unexpected: {e}")
        lint_dict = _ensure_lint().to_dict()
        lint_dict["meta"]["fallback"] = "local_linter_tier1"
        lint_dict["meta"]["note"] = (
            f"Remote compiler error ({type(e).__name__}). "
            "Showing local linter results."
        )
        lint_dict["raw_response"] = {"exception": str(e)}
        return lint_dict


# ─────────────────────────────────────────────────────────────────────────────
# Hot cache — memory-first lookup for top entries
# ─────────────────────────────────────────────────────────────────────────────

HOT_CACHE: dict[str, dict] = {}
_hot_cache_built: bool = False

_PRIORITY_NAMESPACES_DEFAULT = [
    "ta",
    "strategy",
    "math",
    "array",
    "str",
    "matrix",
    "map",
    "request",
    "ticker",
    "timeframe",
    "syminfo",
    "input",
    "color",
    "line",
    "label",
    "box",
    "table",
    "chart",
    "runtime",
]
_OVERRIDE_NS = os.getenv("HOT_CACHE_NAMESPACES", "")
PRIORITY_NAMESPACES = (
    [ns.strip() for ns in _OVERRIDE_NS.split(",") if ns.strip()]
    if _OVERRIDE_NS
    else _PRIORITY_NAMESPACES_DEFAULT
)

PRIORITY_GLOBALS = [
    "close",
    "open",
    "high",
    "low",
    "volume",
    "time",
    "bar_index",
    "barstate.isconfirmed",
    "barstate.islast",
    "barstate.isfirst",
    "na",
    "nz",
    "true",
    "false",
]

_cache_hits: int = 0
_cache_misses: int = 0


async def build_hot_cache() -> bool:
    """Load priority entries into memory for sub-millisecond lookups. Returns True on success."""
    global _cache_hits, _cache_misses
    logger.info("Building hot cache...")
    try:
        col = _get_collection()
        count = 0

        # Load all entries from priority namespaces
        for namespace in PRIORITY_NAMESPACES:
            try:
                result = col.get(
                    where={"namespace": namespace},
                    include=["documents", "metadatas"],
                )
                for rid, doc, meta in zip(
                    result["ids"], result["documents"], result["metadatas"]
                ):
                    key = meta.get("name", "").lower().strip()
                    if key:
                        HOT_CACHE[key] = {"id": rid, "document": doc, "metadata": meta}
                        count += 1
            except Exception as e:
                logger.warning(
                    f"Hot cache: failed to load namespace '{namespace}': {e}"
                )

        # Load priority global variables
        for name in PRIORITY_GLOBALS:
            try:
                result = col.get(
                    where={"name": name},
                    include=["documents", "metadatas"],
                )
                if result["ids"]:
                    HOT_CACHE[name.lower()] = {
                        "id": result["ids"][0],
                        "document": result["documents"][0],
                        "metadata": result["metadatas"][0],
                    }
                    count += 1
            except Exception as e:
                logger.debug(f"Hot cache load failed for '{name}': {e}")

        logger.info(f"Hot cache ready: {count} entries loaded into memory")
        return True
    except Exception as e:
        logger.error(f"Failed to build hot cache: {e}")
        return False


def cache_lookup(name: str) -> Optional[dict]:
    """Check hot cache first. Returns entry dict or None."""
    global _cache_hits, _cache_misses
    key = name.lower().strip()
    entry = HOT_CACHE.get(key)
    if entry:
        _cache_hits += 1
        return entry
    # Try just the last part after a dot
    if "." in key:
        short = key.split(".")[-1]
        entry = HOT_CACHE.get(short)
        if entry:
            _cache_hits += 1
            return entry
    _cache_misses += 1
    return None


async def _ensure_hot_cache():
    """Build hot cache on first call if not already built."""
    global _hot_cache_built
    if not _hot_cache_built:
        success = await build_hot_cache()
        if success:
            _hot_cache_built = True


# ─────────────────────────────────────────────────────────────────────────────
# Tool parameter helpers (strip/normalize applied inline in each tool)
# ─────────────────────────────────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    """Normalize entry name: strip whitespace and trailing parens."""
    return name.strip().rstrip("()")

def _norm_ns(ns: str) -> str:
    """Normalize namespace: strip, lowercase, remove trailing dot."""
    return ns.strip().lower().rstrip(".")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1: search_docs
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Search PineScript Docs", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def search_docs(
    query: Annotated[str, Field(
        min_length=1,
        max_length=500,
        description="Natural language or code query about PineScript v6",
    )],
    n_results: Annotated[int, Field(
        default=5,
        ge=1,
        le=30,
        description="Number of results (1-30, default 5)",
    )] = 5,
    category_filter: Annotated[str | None, Field(
        default=None,
        description="'function','variable','type',etc.",
    )] = None,
    namespace_filter: Annotated[str | None, Field(
        default=None,
        description="Namespace e.g. 'ta', 'strategy'",
    )] = None,
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
        await _ensure_hot_cache()
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

        results = await _query_async(query, n_results, where=where)

        db_err = _check_query_error(results)
        if db_err:
            return db_err

        if not results["ids"] or not results["ids"][0]:
            raise ToolError(f"No results for '{query}'")

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
            content_key = doc[:120].strip().lower()
            content_hash = hash(content_key)
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            deduped_results.append((rid, meta, doc, dist))

        output_lines: list[str] = []
        for i, (rid, meta, doc, dist) in enumerate(deduped_results):
            name = meta.get("name", "?")
            category = meta.get("category", "?").upper()
            namespace = meta.get("namespace") or ""
            syntax = meta.get("syntax") or ""
            ns = (
                f"{namespace}."
                if namespace and not name.startswith(f"{namespace}.")
                else ""
            )
            rel = _relevance_pct(dist)
            tag = _source_tag(meta)

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

            param_text = _format_params_text(meta)
            if param_text:
                output_lines.append("  " + param_text.split("\n")[0])

            returns = meta.get("returns") or ""
            if returns:
                output_lines.append(f"  RETURNS: {returns[:120]}")

            ex_count = meta.get("example_count", 0)
            if ex_count:
                output_lines.append(f"  Examples: {ex_count}")

        output_lines.append(_DIVIDER)
        return _cap_response("\n".join(output_lines))

    except ToolError:
        raise  # Don't double-wrap ToolError from inside the try block
    except Exception as e:
        logger.error(f"[search_docs] {e}")
        if "ChromaDB" in str(e) or _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "search_docs"))


# ─────────────────────────────────────────────────────────────────────────────
# Generic lookup helper used by tools 2-7
# ─────────────────────────────────────────────────────────────────────────────


async def _lookup_entry(name: str, category: str) -> str:
    """Lookup an entry by name and category. Returns formatted string or error."""
    try:
        await _ensure_hot_cache()
        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(name)
        if cached:
            # Verify category match — skip cache if wrong category
            if category and cached["metadata"].get("category") != category:
                pass  # fall through to name search
            else:
                result = _format_entry_detail(
                    cached["metadata"].get("name", name),
                    cached["metadata"],
                    cached["document"],
                )
                return result

        # Step 1: Try exact fuzzy match within category
        candidates = await _search_by_name_async(
            name, where={"category": category} if category else None
        )

        if candidates and candidates[0][0] >= 85:
            best_sim, best_entry = candidates[0]
            return _format_entry_detail(
                best_entry["metadata"].get("name", name),
                best_entry["metadata"],
                best_entry["document"],
            )

        # Step 2: Semantic search within category
        results = await _query_async(name, 5, where={"category": category} if category else None)
        db_err = _check_query_error(results)
        if db_err:
            return db_err
        if results["ids"] and results["ids"][0]:
            top_meta = results["metadatas"][0][0]
            top_dist = results["distances"][0][0]
            top_name = top_meta.get("name", "").lower().replace("()", "").strip()
            search_name = name.lower().replace("()", "").strip()
            # Only return if name matches or relevance is strong (distance < 0.6 = 40%+)
            name_match = (search_name == top_name or
                         (len(search_name) >= 3 and search_name in top_name))
            if name_match or top_dist < 0.6:
                return _format_entry_detail(
                    top_meta.get("name", name),
                    top_meta,
                    results["documents"][0][0],
                    top_dist,
                )

        # Step 3: Broaden to all categories (only if highly relevant)
        results = await _query_async(name, 5)
        if results["ids"] and results["ids"][0]:
            top_meta = results["metadatas"][0][0]
            top_dist = results["distances"][0][0]
            top_name = top_meta.get("name", "").lower().replace("()", "").strip()
            search_name = name.lower().replace("()", "").strip()
            name_match_broad = (search_name == top_name or
                               (len(search_name) >= 3 and search_name in top_name))
            if (
                name_match_broad
                or (top_dist < 0.5 and top_meta.get("category") == category)
            ):
                return _format_entry_detail(
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
            all_candidates = await _search_by_name_async(name)
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
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        return _error(category, _safe_error(e, category))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2: get_function
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Get Function Docs", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_function(
    name: Annotated[str, Field(
        min_length=1,
        max_length=200,
        description="Entry name e.g. 'ta.ema', 'close', 'array'",
    )],
) -> str:
    """
    Get complete documentation for a PineScript v6 function.
    Returns all overloads, every parameter with type and description,
    return type, remarks, and ALL code examples in full.

    Use for: ta.*, strategy.*, array.*, math.*, str.*, request.*, etc.
    Example: get_function("ta.ema"), get_function("strategy.entry")
    """
    try:
        name = _norm_name(name)
        await _ensure_hot_cache()
        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(name)
        if cached and cached["metadata"].get("category") == "function":
            result = _format_entry_detail(
                cached["metadata"].get("name", name),
                cached["metadata"],
                cached["document"],
            )
            return result

        # BUG FIX: For function lookups, always try exact match with category=function first
        name_lower = name.lower().strip()

        # Try exact function match first
        try:
            col = _get_collection()
            exact_func = col.get(
                where={"$and": [
                    {"name": name_lower},
                    {"category": "function"}
                ]},
                include=["metadatas", "documents"]
            )
            if exact_func["ids"]:
                best_meta = exact_func["metadatas"][0]
                best_doc = exact_func["documents"][0]
                return _format_entry_detail(
                    best_meta.get("name", name),
                    best_meta,
                    best_doc
                )
        except Exception as e:
            logger.debug(f"Exact function match failed: {e}")

        # Fall back to the general lookup
        return await _lookup_entry(name, "function")

    except Exception as e:
        logger.error(f"[get_function] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_function"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3: get_variable
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Get Variable Docs", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_variable(
    name: Annotated[str, Field(
        min_length=1,
        max_length=200,
        description="Entry name e.g. 'ta.ema', 'close', 'array'",
    )],
) -> str:
    """
    Get documentation for a PineScript v6 built-in variable.
    Built-in variables: close, open, high, low, volume, time,
    bar_index, barstate.*, syminfo.*, strategy.*, etc.
    """
    try:
        return await _lookup_entry(_norm_name(name), "variable")
    except Exception as e:
        logger.error(f"[get_variable] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_variable"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4: get_type
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Get Type Docs", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_type(
    name: Annotated[str, Field(
        min_length=1,
        max_length=200,
        description="Entry name e.g. 'ta.ema', 'close', 'array'",
    )],
) -> str:
    """
    Get documentation for a PineScript v6 type.
    Types: array, matrix, map, line, label, box, table, polyline,
    color, string, int, float, bool, and user-defined types.
    """
    try:
        name = _norm_name(name)
        await _ensure_hot_cache()
        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(name)
        if cached and cached["metadata"].get("category") == "type":
            result = _format_entry_detail(
                cached["metadata"].get("name", name),
                cached["metadata"],
                cached["document"],
            )
            return result

        # Filter by category="type" — never return function entries
        col = _get_collection()
        name_lower = name.lower().strip()

        # Always filter by category="type" — never return function entries
        try:
            result = col.get(
                where={"$and": [
                    {"name": {"$in": [name_lower, f"type.{name_lower}"]}},
                    {"category": "type"}
                ]},
                include=["documents", "metadatas"]
            )
            if result["ids"]:
                best_meta = result["metadatas"][0]
                best_doc = result["documents"][0]
                return _format_entry_detail(
                    best_meta.get("name", name),
                    best_meta,
                    best_doc
                )
        except Exception as e:
            logger.debug(f"Exact type match failed: {e}")

        # Semantic fallback — still enforce category filter
        results = await _query_async(
            f"type {name_lower} definition fields methods",
            5,
            where={"category": "type"}
        )
        db_err = _check_query_error(results)
        if db_err:
            return db_err
        if results["ids"] and results["ids"][0] and results["documents"][0]:
            top_meta = results["metadatas"][0][0]
            top_doc = results["documents"][0][0]
            top_dist = results["distances"][0][0]
            return _format_entry_detail(
                top_meta.get("name", name),
                top_meta,
                top_doc,
                top_dist
            )

        return (
            f"Type '{name}' not found in docs.\n"
            f"Available types: array, matrix, map, line, label, "
            f"box, table, polyline, color, string, int, float, bool"
        )

    except Exception as e:
        logger.error(f"[get_type] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_type"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 5: get_constant
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Get Constant Docs", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_constant(
    name: Annotated[str, Field(
        min_length=1,
        max_length=200,
        description="Entry name e.g. 'ta.ema', 'close', 'array'",
    )],
) -> str:
    """
    Get documentation for a PineScript v6 built-in constant.
    Examples: color.red, strategy.long, order.ascending,
    shape.circle, location.top, etc.
    """
    try:
        return await _lookup_entry(_norm_name(name), "constant")
    except Exception as e:
        logger.error(f"[get_constant] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_constant"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 6: get_keyword
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Get Keyword Docs", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_keyword(
    name: Annotated[str, Field(
        min_length=1,
        max_length=200,
        description="Entry name e.g. 'ta.ema', 'close', 'array'",
    )],
) -> str:
    """
    Get documentation for a PineScript v6 keyword.
    Keywords: if, for, while, switch, var, varip, type, method,
    import, export, and, or, not, true, false, etc.
    """
    try:
        return await _lookup_entry(_norm_name(name), "keyword")
    except Exception as e:
        logger.error(f"[get_keyword] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_keyword"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 7: get_operator
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Get Operator Docs", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_operator(
    name: Annotated[str, Field(
        min_length=1,
        max_length=200,
        description="Entry name e.g. 'ta.ema', 'close', 'array'",
    )],
) -> str:
    """
    Get documentation for a PineScript v6 operator.
    Operators: :=, +=, -=, *=, /=, %=, ==, !=, >, <, >=, <=,
    ?, =>, +, -, *, /, %, not, and, or, [], etc.
    """
    try:
        return await _lookup_entry(_norm_name(name), "operator")
    except Exception as e:
        logger.error(f"[get_operator] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_operator"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 8: get_examples
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Get Code Examples", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def get_examples(
    query: Annotated[str, Field(
        min_length=1,
        max_length=500,
        description="Concept to find code examples for",
    )],
    n_results: Annotated[int, Field(
        default=4,
        ge=1,
        le=20,
        description="Number of examples to return",
    )] = 4,
) -> str:
    """
    Find real PineScript v6 code examples by concept.
    Returns complete, runnable code blocks from the official docs.

    Use for: "how to use strategy.entry with stop loss",
             "array iteration example", "drawing lines example"
    """
    try:
        query = query.strip()
        results = await _query_async(query, n_results, where={"has_examples": 1})
        db_err = _check_query_error(results)
        if db_err:
            raise ToolError(db_err)
        if not results["ids"] or not results["ids"][0]:
            raise ToolError(f"No examples found for '{query}'")

        output_lines: list[str] = []
        for i, (meta, doc, dist) in enumerate(
            zip(
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            )
        ):
            name = meta.get("name", "?")
            category = meta.get("category", "?").upper()
            namespace = meta.get("namespace") or ""
            ns = (
                f"{namespace}."
                if namespace and not name.startswith(f"{namespace}.")
                else ""
            )
            rel = _relevance_pct(dist)

            output_lines.append(_DIVIDER)
            output_lines.append(
                f"EXAMPLES from: {ns}{name} ({category}) — Relevance: {rel}"
            )

            ex_text = _format_examples_text(meta)
            if ex_text:
                output_lines.append(ex_text)
            else:
                output_lines.append(
                    "  (Examples referenced but not stored in metadata)"
                )

        output_lines.append(_DIVIDER)
        return _cap_response("\n".join(output_lines))

    except ToolError:
        raise  # Don't double-wrap ToolError from inside the try block
    except Exception as e:
        logger.error(f"[get_examples] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_examples"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 9: list_namespace
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="List Namespace Members", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def list_namespace(
    namespace: Annotated[str, Field(
        min_length=1,
        max_length=50,
        description="Namespace e.g. 'ta', 'strategy'",
    )],
    category_filter: Annotated[str | None, Field(
        default=None,
        description="Optional category filter",
    )] = None,
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
        ns = _norm_ns(namespace)
        if ns.lower() == "global":
            where: Optional[dict] = {"namespace": ""}
        else:
            where = {"namespace": ns}

        if category_filter:
            where["category"] = category_filter

        entries = await _get_all_where_async(where)
        if not entries:
            raise ToolError(f"No entries found for namespace '{ns}'")

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

        output_lines.append(f"Total: {len(entries)} entries in namespace '{ns}'")
        return _cap_response("\n".join(output_lines))

    except ToolError:
        raise  # Don't double-wrap ToolError from inside the try block
    except Exception as e:
        logger.error(f"[list_namespace] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "list_namespace"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 10: search_by_return_type
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Search by Return Type", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
async def search_by_return_type(
    return_type: Annotated[str, Field(
        min_length=1,
        max_length=100,
        description="Return type e.g. 'series float', 'line', 'array<int>'",
    )],
    n_results: Annotated[int, Field(
        default=10,
        ge=1,
        le=50,
        description="Number of results (1-50, default 10)",
    )] = 10,
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
        # Filter by return type metadata, fall back to semantic if empty
        where = {"category": "function"}
        # Only add returns filter if it's likely to have matches
        ret_filter = {"returns": {"$contains": return_type}}
        try:
            # Test if the filter returns results
            probe = _get_collection().get(
                where={
                    "category": "function",
                    "returns": {"$contains": return_type},
                },
                include=["documents"],
                limit=1,
            )
            if probe["ids"]:
                where = {"$and": [{"category": "function"}, ret_filter]}
        except Exception as e:
            logger.debug(f"Return type probe failed: {e}")
        results = await _query_async(return_type, n_results, where=where)
        db_err = _check_query_error(results)
        if db_err:
            return db_err

        if not results["ids"] or not results["ids"][0]:
            # Fallback: semantic search with category filter only
            results = await _query_async(
                f"functions returning {return_type}",
                n_results,
                where={"category": "function"},
            )
            db_err = _check_query_error(results)
            if db_err:
                return db_err

        if not results["ids"] or not results["ids"][0]:
            raise ToolError(f"No functions found returning '{return_type}'")

        output_lines: list[str] = []
        output_lines.append(f"Functions returning '{return_type}':")
        output_lines.append("")

        for i, (meta, doc, dist) in enumerate(
            zip(
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            )
        ):
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
            rel = _relevance_pct(dist)

            first_sentence = desc.split(".")[0][:100] if desc else ""
            output_lines.append(f"[{i + 1}] {ns}{name} — Relevance: {rel}")
            if syntax:
                output_lines.append(f"    Syntax: {syntax[:100]}")
            if returns:
                output_lines.append(f"    Returns: {returns[:100]}")
            if first_sentence:
                output_lines.append(f"    {first_sentence}")
            if url:
                output_lines.append(f"    URL: {url}")
            output_lines.append("")

        return "\n".join(output_lines)

    except Exception as e:
        logger.error(f"[search_by_return_type] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "search_by_return_type"))


# ─────────────────────────────────────────────────────────────────────────────────────
# TOOL 11: validate_syntax
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Validate PineScript Code", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def validate_syntax(
    code: Annotated[str, Field(
        max_length=50000,
        description="Complete PineScript v6 source code to validate",
    )] = "",
) -> str:
    """
    Validate PineScript v6 code using TradingView's official pine-facade
    compiler — the exact same compiler used by TradingView's web editor.

    Returns real compilation errors with line numbers and column positions.
    Use BEFORE suggesting code to the user to catch errors proactively.

    Args:
        code: Complete PineScript v6 source code to validate
    """
    try:
        code = code.strip()
        if not code:
            return "ERROR: No code provided. Pass the complete PineScript v6 source code to validate."

        result = await _call_pine_facade(code)

        errors = _enrich_error_with_code(result.get("errors", []), code)
        warnings = result.get("warnings", [])
        success = result.get("success", False)
        meta = result.get("meta", {})
        is_fallback = meta.get("fallback") == "local_linter_tier1"
        compiler_label = "Local Linter (Tier 1)" if is_fallback else "TradingView pine-facade v6"

        if success and not errors and not warnings:
            name = meta.get("name", "")
            extra = f"\nMeta: {name}" if name else ""
            fallback_note = "\nNote: Validated by local linter (remote compiler unavailable)." if is_fallback else ""

            # Add quick code analysis for richer output
            code_lines = code.strip().splitlines()
            code_analysis = []
            is_strategy = any("strategy(" in l for l in code_lines)
            is_indicator = any("indicator(" in l for l in code_lines)
            is_library = any("library(" in l for l in code_lines)
            script_type = "strategy" if is_strategy else ("indicator" if is_indicator else ("library" if is_library else "unknown"))
            plots = sum(1 for l in code_lines if l.strip().startswith("plot(") or l.strip().startswith("plotshape(") or l.strip().startswith("plotchar("))
            inputs = sum(1 for l in code_lines if "input." in l)
            has_request = any("request." in l for l in code_lines)

            code_analysis.append(f"Script type: {script_type}")
            code_analysis.append(f"Lines: {len(code_lines)}")
            if plots:
                code_analysis.append(f"Plots: {plots}")
            if inputs:
                code_analysis.append(f"Inputs: {inputs}")
            if has_request:
                code_analysis.append("Uses request.*() (external data)")

            analysis_block = "\n".join(f"  {a}" for a in code_analysis)

            return (
                f"VALID — Code compiles successfully.{extra}{fallback_note}\n"
                f"Compiler: {compiler_label}\n"
                f"Errors: 0 | Warnings: 0\n\n"
                f"Code Analysis:\n{analysis_block}"
            )

        lines = []
        total_issues = len(errors) + len(warnings)
        lines.append(f"COMPILATION ISSUES ({total_issues}):")
        lines.append(f"Compiler: {compiler_label}")
        if is_fallback:
            note = meta.get("note", "Local linter catches ~50% of common errors.")
            lines.append(f"Note: {note}")
        lines.append(f"Errors: {len(errors)} | Warnings: {len(warnings)}")
        lines.append("")

        for i, err in enumerate(errors, 1):
            line_num = err.get("line", "?")
            col_num = err.get("column", "?")
            text = err.get("text", "Unknown error")
            err_type = err.get("type", "error").upper()
            hint = _lookup_fix_hint(text)
            lines.append(f"  ERROR {i} — Line {line_num}, Col {col_num} [{err_type}]")
            lines.append(f"    {text}")
            if hint:
                lines.append(f"    Fix hint: {hint}")
            lines.append("")

        for i, warn in enumerate(warnings, 1):
            line_num = warn.get("line", "?")
            col_num = warn.get("column", "?")
            text = warn.get("text", "Unknown warning")
            hint = _lookup_fix_hint(text)
            lines.append(f"  WARNING {i} — Line {line_num}, Col {col_num}")
            lines.append(f"    {text}")
            if hint:
                lines.append(f"    Fix hint: {hint}")
            lines.append("")

        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[validate_syntax] {e}")
        return _error("validate_syntax", _safe_error(e, "validate_syntax"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 12: validate_and_explain
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Validate and Explain Errors", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def validate_and_explain(
    code: Annotated[str, Field(
        max_length=50000,
        description="Complete PineScript v6 source code to validate",
    )] = "",
) -> str:
    """
    Validate PineScript v6 code AND cross-reference any errors against
    the documentation database to provide precise fix instructions.

    Combines pine-facade compilation + semantic doc lookup into one call.
    This is the most powerful debugging tool for PineScript AI assistance.

    Use when helping user debug failing PineScript code.
    """
    try:
        code = code.strip()
        if not code:
            return "ERROR: No code provided. Pass the complete PineScript v6 source code to validate and explain."

        result = await _call_pine_facade(code)

        errors = _enrich_error_with_code(result.get("errors", []), code)
        warnings = result.get("warnings", [])
        success = result.get("success", False)
        meta = result.get("meta", {})
        is_fallback = meta.get("fallback") == "local_linter_tier1"
        compiler_label = "Local Linter (Tier 1)" if is_fallback else "TradingView pine-facade v6"

        if success and not errors and not warnings:
            # Quick code analysis on success
            code_lines = code.strip().splitlines()
            plots = sum(
                1
                for l in code_lines
                if l.strip().startswith("plot(") or l.strip().startswith("plotshape(")
            )
            inputs = sum(1 for l in code_lines if "input." in l)
            is_strategy = any("strategy(" in l for l in code_lines)
            is_indicator = any("indicator(" in l for l in code_lines)
            script_type = (
                "strategy"
                if is_strategy
                else ("indicator" if is_indicator else "library")
            )
            fallback_note = "\nNote: Validated by local linter (remote compiler unavailable)." if is_fallback else ""

            return (
                f"VALIDATION + DEBUG REPORT\n"
                f"{'=' * 50}\n"
                f"Compiler: {compiler_label}\n"
                f"Status: PASSED\n"
                f"Errors: 0 | Warnings: 0\n\n"
                f"Code Analysis:\n"
                f"  Script type: {script_type}\n"
                f"  Lines: {len(code_lines)}\n"
                f"  Plots: {plots}\n"
                f"  Inputs: {inputs}\n"
                f"{fallback_note}"
            )

        # Process errors with doc cross-reference
        lines = []
        lines.append("VALIDATION + DEBUG REPORT")
        lines.append("=" * 50)
        lines.append(f"Compiler: {compiler_label}")
        if is_fallback:
            note = meta.get("note", "Local linter catches ~50% of common errors.")
            lines.append(f"Note: {note}")
        lines.append(f"Status: FAILED")
        lines.append(f"Errors: {len(errors)} | Warnings: {len(warnings)}")
        lines.append("")

        for i, err in enumerate(errors, 1):
            line_num = err.get("line", "?")
            col_num = err.get("column", "?")
            text = err.get("text", "Unknown error")

            lines.append(f"ERROR {i} — Line {line_num}, Col {col_num}:")
            lines.append(f"  Compiler says: {text}")

            # Try to extract a name from the error and look it up
            extracted_name = _extract_name_from_error(text)
            if extracted_name:
                lines.append(f"  Docs lookup for '{extracted_name}':")
                doc_result = await _lookup_entry(extracted_name, "")
                if "not found" not in doc_result[:80].lower():
                    # Show first 5 lines of the doc result
                    doc_lines = doc_result.splitlines()[:5]
                    for dl in doc_lines:
                        lines.append(f"    {dl}")
                else:
                    lines.append(
                        f"    Not found in docs — may be misspelled or v5-only syntax"
                    )

            hint = _lookup_fix_hint(text)
            if hint:
                lines.append(f"  Fix hint: {hint}")
            lines.append("")

        for i, warn in enumerate(warnings, 1):
            text = warn.get("text", "Unknown warning")
            lines.append(f"WARNING {i} — Line {warn.get('line', '?')}:")
            lines.append(f"  {text}")
            hint = _lookup_fix_hint(text)
            if hint:
                lines.append(f"  Fix hint: {hint}")
            lines.append("")

        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[validate_and_explain] {e}")
        return (
            f"VALIDATION FAILED: {_safe_error(e, 'validate_and_explain')}\n"
            f"Check your code for syntax errors."
        )


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 13: fix_and_validate
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Fix and Validate Code", readOnlyHint=True, openWorldHint=True, destructiveHint=True, idempotentHint=False))
async def fix_and_validate(
    code: Annotated[str, Field(
        max_length=50000,
        description="The failing PineScript v6 code",
    )] = "",
    error_description: Annotated[str, Field(
        max_length=500,
        description="The error message or what's wrong",
    )] = "",
) -> str:
    """
    Given PineScript code and a description of what's wrong (or the
    compiler error text), look up the correct syntax in the docs and
    return the precise fix with validation confirmation.

    Use when the user has a specific error they want fixed.

    Args:
        code: The failing PineScript v6 code
        error_description: The error message or description of the problem
    """
    try:
        code = code.strip()
        error_description = error_description.strip()
        if not code:
            return "ERROR: No code provided. Pass the failing PineScript v6 source code."
        if not error_description:
            return "ERROR: No error description provided. Describe the error or paste the compiler message."

        # Step 1: Find best matching hint using substring scan
        error_lower = error_description.lower()
        matched_hint = None
        best_score = 0

        for pattern, hint in _FIX_HINTS.items():
            pattern_lower = pattern.lower()
            # Score: longer pattern match = more specific = higher score
            if pattern_lower in error_lower:
                score = len(pattern_lower)
                if score > best_score:
                    best_score = score
                    matched_hint = hint

        # Step 2: Extract identifier from error if present
        # Common patterns: "Undeclared identifier 'foo'", "Cannot find 'bar'"
        identifier_match = re.search(
            r"['\"]([a-zA-Z_][\w.]*)['\"]", error_description
        )
        identifier = identifier_match.group(1) if identifier_match else None

        # Interpolate {name} placeholder in hints with the actual identifier
        if matched_hint and identifier:
            matched_hint = matched_hint.replace("{name}", identifier)

        # Step 3: Cross-reference identifier against MCP docs
        doc_context = ""
        if identifier and identifier.lower() not in _COMMON_PARAM_NAMES:
            try:
                results = await _search_by_name_async(identifier)
                if results:
                    best_sim, best_entry = results[0]
                    doc_context = (
                        f"\nDOC REFERENCE for '{identifier}':\n"
                        f"{best_entry.get('document', '')[:300]}"
                    )
                else:
                    # Try with common namespaces
                    for ns in ["ta", "strategy", "math", "array", "str"]:
                        ns_results = await _search_by_name_async(f"{ns}.{identifier}")
                        if ns_results:
                            best_sim, best_entry = ns_results[0]
                            doc_context = (
                                f"\nSUGGESTION: Did you mean '{ns}.{identifier}'?\n"
                                f"{best_entry.get('document', '')[:200]}"
                            )
                            break
            except Exception as e:
                logger.debug(f"Doc lookup for fix failed: {e}")

        # Step 4: Attempt auto-fix for common patterns
        fixed_code = code
        fix_applied = "No automatic fix available"
        fixes_list = []

        # Pattern 1: missing namespace (ema → ta.ema, sma → ta.sma, etc.)
        # (?<!\.) prevents double-prefixing; \b ensures whole-word match
        bare_fn_pattern = re.compile(
            r'(?<!\.)\b(ema|sma|rsi|macd|atr|bb|stoch|wma|hma|vwap|crossover|'
            r'crossunder|highest|lowest|barssince|valuewhen|linreg|mom|'
            r'cum|change|pivothigh|pivotlow|supertrend|correlation)\s*\('
        )
        if bare_fn_pattern.search(fixed_code):
            fixed_code = bare_fn_pattern.sub(r'ta.\1(', fixed_code)
            fixes_list.append("Added ta. namespace prefix to unqualified TA functions")

        # Pattern 2: v6 breaking change — transp= parameter removed
        transp_pattern = re.compile(r',\s*transp\s*=\s*\d+')
        if transp_pattern.search(fixed_code):
            fixed_code = transp_pattern.sub('', fixed_code)
            fixes_list.append("Removed transp= parameter (v6: use color.new() instead)")

        # Pattern 3: v6 breaking change — when= parameter removed from strategy.*
        when_pattern = re.compile(r'(strategy\.\w+\([^)]*),\s*when\s*=\s*([^)]+)\)')
        if when_pattern.search(fixed_code):
            # Can't fully auto-fix (need if block), but strip the when= and note it
            fixed_code = when_pattern.sub(r'\1)', fixed_code)
            fixes_list.append("Removed when= parameter (v6: wrap in if block instead)")

        # Pattern 4: strategy.* called in indicator context
        if "strategy.entry" in fixed_code and "strategy(" not in fixed_code:
            fixes_list.append("strategy.entry() requires strategy() declaration, not indicator()")

        # Pattern 5: Implicit bool — if volume, if close (v6 needs explicit comparison)
        implicit_bool_pattern = re.compile(r'\bif\s+(volume|close|open|high|low)\b(?!\s*[<>=!])')
        if implicit_bool_pattern.search(fixed_code):
            fixed_code = implicit_bool_pattern.sub(r'if \1 > 0', fixed_code)
            fixes_list.append("Added explicit > 0 comparison (v6: implicit bool casting removed)")

        # Pattern 6: bool x = na (v6: bools can't be na)
        bool_na_pattern = re.compile(r'\bbool\s+(\w+)\s*=\s*na\b')
        if bool_na_pattern.search(fixed_code):
            fixed_code = bool_na_pattern.sub(r'var bool \1 = false', fixed_code)
            fixes_list.append("Changed 'bool x = na' to 'var bool x = false' (v6: bool can't be na)")

        if fixes_list:
            fix_applied = " | ".join(fixes_list)

        # Step 5: Validate the fixed code
        validation_result = None
        if fixed_code != code:
            try:
                raw = await _call_pine_facade(fixed_code)
                if raw["success"]:
                    validation_result = "✅ Fixed code compiles successfully"
                else:
                    errs = raw["errors"]
                    validation_result = (
                        f"⚠️ Fixed code still has {len(errs)} error(s):\n" +
                        "\n".join(f"  Line {e['line']}: {e['text']}" for e in errs[:3])
                    )
            except Exception:
                validation_result = "⚠️ Could not validate fix (pine-facade unavailable)"

        # Build response
        lines = [
            f"FIX AND VALIDATE REPORT",
            f"{'='*50}",
            f"Error: {error_description}",
            f"",
            f"HINT: {matched_hint or 'No specific hint — check PineScript v6 syntax'}",
            f"",
            f"Fix Applied: {fix_applied}",
        ]
        if doc_context:
            lines.append(doc_context)
        if validation_result:
            lines.extend(["", validation_result])
        if fixed_code != code:
            lines.extend(["", "FIXED CODE:", "```pine", fixed_code, "```"])

        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[fix_and_validate] {e}")
        raise ToolError(_safe_error(e, "fix_and_validate"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 14: generate_indicator  (helpers + fallback templates)
# ─────────────────────────────────────────────────────────────────────────────

# Known-correct fallback templates for common indicator types.
# Keys are lowercased indicator-family names; values are (calc_stub, overlay_default).
_INDICATOR_TEMPLATES: dict[str, tuple[str, bool]] = {
    "rsi": (
        "rsiValue = ta.rsi(src, length)\nplot(rsiValue, \"RSI\", color.orange)\n"
        "hline(70, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(30, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "macd": (
        "[macdLine, signalLine, histLine] = ta.macd(src, fastLength, slowLength, signalLength)\n"
        "plot(macdLine, \"MACD\", color.blue)\nplot(signalLine, \"Signal\", color.orange)\n"
        "plot(histLine, \"Histogram\", color.red, style=plot.style_histogram)",
        False,
    ),
    "bollinger": (
        "[middle, upper, lower] = ta.bb(src, length, mult)\n"
        "plot(middle, \"Basis\", color.blue)\nplot(upper, \"Upper\", color.red)\n"
        "plot(lower, \"Lower\", color.green)\n"
        "p1 = plot(upper, display=display.none)\np2 = plot(lower, display=display.none)\n"
        "fill(p1, p2, color=color.new(color.blue, 90))",
        True,
    ),
    "ema": (
        "emaValue = ta.ema(src, length)\nplot(emaValue, \"EMA\", color.orange)",
        True,
    ),
    "sma": (
        "smaValue = ta.sma(src, length)\nplot(smaValue, \"SMA\", color.orange)",
        True,
    ),
    "atr": (
        "atrValue = ta.atr(length)\nplot(atrValue, \"ATR\", color.orange)",
        False,
    ),
    "stochastic": (
        "k = ta.sma(ta.stoch(src, high, low, length), kSmooth)\n"
        "d = ta.sma(k, dSmooth)\n"
        "plot(k, \"K\", color.blue)\nplot(d, \"D\", color.orange)\n"
        "hline(80, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(20, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "supertrend": (
        "[supertrendValue, direction] = ta.supertrend(factor, atrLength)\n"
        "upTrend = plot(direction < 0 ? supertrendValue : na, \"Up Trend\", color.green, linewidth=2)\n"
        "dnTrend = plot(direction < 0 ? na : supertrendValue, \"Down Trend\", color.red, linewidth=2)\n"
        "fill(upTrend, dnTrend, color=direction < 0 ? color.new(color.green, 90) : color.new(color.red, 90))",
        True,
    ),
    "vwap": (
        "vwapValue = ta.vwap(hlc3)\nplot(vwapValue, \"VWAP\", color.orange, linewidth=2)",
        True,
    ),
    "adl": (
        "adlValue = ta.accdist\nplot(adlValue, \"ADL\", color.orange)",
        False,
    ),
    "obv": (
        "obvValue = ta.obv\nplot(obvValue, \"OBV\", color.orange)",
        False,
    ),
    "cci": (
        "cciValue = ta.cci(src, length)\nplot(cciValue, \"CCI\", color.orange)\n"
        "hline(100, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(-100, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "mfi": (
        "mfiValue = ta.mfi(length)\nplot(mfiValue, \"MFI\", color.orange)\n"
        "hline(80, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(20, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "williams": (
        "wrValue = ta.wpr(length)\nplot(wrValue, \"Williams %R\", color.orange)\n"
        "hline(-20, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(-80, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "macd": (
        "[macdLine, signalLine, histLine] = ta.macd(src, fastLength, slowLength, signalLength)\n"
        "plot(macdLine, \"MACD\", color.blue)\nplot(signalLine, \"Signal\", color.orange)\n"
        "plot(histLine, \"Histogram\", color.red, style=plot.style_histogram)",
        False,
    ),
    "dmi": (
        "[diPlus, diMinus, adxValue] = ta.dmi(diLength, adxSmoothing)\n"
        "plot(diPlus, \"+DI\", color.green)\nplot(diMinus, \"-DI\", color.red)\n"
        "plot(adxValue, \"ADX\", color.orange, linewidth=2)",
        False,
    ),
    "ichimoku": (
        "tenkan = math.avg(ta.highest(high, 9), ta.lowest(low, 9))\n"
        "kijun = math.avg(ta.highest(high, 26), ta.lowest(low, 26))\n"
        "senkouA = math.avg(tenkan, kijun)\n"
        "senkouB = math.avg(ta.highest(high, 52), ta.lowest(low, 52))\n"
        "plot(tenkan, \"Tenkan\", color.blue)\nplot(kijun, \"Kijun\", color.red)\n"
        "p1 = plot(senkouA, \"Senkou A\", display=display.none)\n"
        "p2 = plot(senkouB, \"Senkou B\", display=display.none)\n"
        "fill(p1, p2, color=senkouA > senkouB ? color.new(color.green, 90) : color.new(color.red, 90))",
        True,
    ),
    "sar": (
        "sarValue = ta.sar(start, increment, maximum)\n"
        "plot(sarValue, \"Parabolic SAR\", color.orange, style=plot.style_cross, linewidth=2)",
        True,
    ),
    "keltner": (
        "emaValue = ta.ema(src, length)\n"
        "atrValue = ta.atr(atrLength)\n"
        "upper = emaValue + mult * atrValue\nlower = emaValue - mult * atrValue\n"
        "plot(emaValue, \"EMA\", color.blue)\nplot(upper, \"Upper\", color.red)\n"
        "plot(lower, \"Lower\", color.green)\n"
        "p1 = plot(upper, display=display.none)\np2 = plot(lower, display=display.none)\n"
        "fill(p1, p2, color=color.new(color.blue, 90))",
        True,
    ),
    "donchian": (
        "upper = ta.highest(high, length)\nlower = ta.lowest(low, length)\n"
        "mid = math.avg(upper, lower)\n"
        "plot(upper, \"Upper\", color.red)\nplot(lower, \"Lower\", color.green)\n"
        "plot(mid, \"Middle\", color.orange, style=plot.style_circles)",
        True,
    ),
    "aroon": (
        "up = ta.aroon(length).up\ndn = ta.aroon(length).down\n"
        "osc = up - dn\n"
        "plot(up, \"Aroon Up\", color.green)\nplot(dn, \"Aroon Down\", color.red)\n"
        "plot(osc, \"Oscillator\", color.orange, style=plot.style_histogram)\n"
        "hline(0, \"Zero\", color.gray, hline.style_dotted)",
        False,
    ),
    "cmf": (
        "cmfValue = ta.cmf(length)\nplot(cmfValue, \"CMF\", color.orange)\n"
        "hline(0, \"Zero\", color.gray, hline.style_dotted)",
        False,
    ),
    "tema": (
        "e1 = ta.ema(src, length)\ne2 = ta.ema(e1, length)\ne3 = ta.ema(e2, length)\n"
        "temaValue = 3 * (e1 - e2) + e3\nplot(temaValue, \"TEMA\", color.orange)",
        True,
    ),
    "dema": (
        "e1 = ta.ema(src, length)\ne2 = ta.ema(e1, length)\n"
        "demaValue = 2 * e1 - e2\nplot(demaValue, \"DEMA\", color.orange)",
        True,
    ),
}


def _extract_indicator_keywords(description: str) -> list[str]:
    """Extract indicator-family keywords from a natural language description.

    Returns a list of lowercase keyword tokens that map to keys in
    _INDICATOR_TEMPLATES.  Order matters: longer/more-specific patterns
    are tested first so "Bollinger Bands" is not swallowed by "bb".
    """
    desc_lower = description.lower()
    # Order matters: longer/more specific patterns first
    patterns = [
        ("bollinger", r"\bbollinger\b|\bbb\b(?!\s*=)"),
        ("supertrend", r"\bsupertrend\b|\bsuper.?trend\b"),
        ("stochastic", r"\bstochastic\b|\bstoch\b"),
        ("rsi", r"\brelative\s+strength\b|\brsi\b"),
        ("ema", r"\bexponential\s+moving\s+average\b|\bema\b"),
        ("sma", r"\bsimple\s+moving\s+average\b|\bsma\b"),
        ("atr", r"\baverage\s+true\s+range\b|\batr\b"),
        ("vwap", r"\bvolume\s+weighted\s+average\b|\bvwap\b"),
        ("adl", r"\baccumulation\s+distribution\b|\badl\b|\baccdist\b"),
        ("obv", r"\bon\s+balance\s+volume\b|\bobv\b"),
        ("cci", r"\bcommodity\s+channel\b|\bcci\b"),
        ("mfi", r"\bmoney\s+flow\s+index\b|\bmfi\b"),
        ("williams", r"\bwilliams\s*%?\s*r\b|\bwpr\b"),
        ("macd", r"\bmacd\b|\bmoving\s+average\s+convergence\s+divergence\b"),
        ("dmi", r"\bdirectional\s+movement\b|\bdmi\b|\badx\b"),
        ("ichimoku", r"\bichimoku\b|\bcloud\b"),
        ("sar", r"\bparabolic\s+sar\b|\bstop\s+and\s+reverse\b|\bsar\b"),
        ("keltner", r"\bkeltner\b|\bkc\b"),
        ("donchian", r"\bdonchian\b|\bchannel\b(?!.*cci)"),
        ("aroon", r"\baroon\b"),
        ("cmf", r"\bchaikin\s+money\s+flow\b|\bcmf\b"),
        ("tema", r"\btriple\s+exponential\b|\btema\b"),
        ("dema", r"\bdouble\s+exponential\b|\bdema\b"),
    ]
    matches = []
    for family, pattern in patterns:
        if re.search(pattern, desc_lower):
            matches.append(family)
    return matches


def _map_input_to_param(
    var_name: str, param_names: list[str]
) -> str | None:
    """Map a user input variable name to the best-matching function parameter.

    Matching strategy (in priority order):
    1. Exact match
    2. Param name is a suffix of the var name (e.g. rsiLength -> length)
    3. Param name is a prefix of the var name (e.g. src -> srcClose)
    4. Substring containment with minimum 3-char overlap
    """
    vl = var_name.lower()
    # 1. Exact
    for pn in param_names:
        if vl == pn.lower():
            return pn
    # 2. Param is suffix of var (rsiLength -> length)
    for pn in param_names:
        pnl = pn.lower()
        if len(pnl) >= 3 and vl.endswith(pnl):
            return pn
    # 3. Param is prefix of var (src -> srcClose)
    for pn in param_names:
        pnl = pn.lower()
        if len(pnl) >= 3 and vl.startswith(pnl):
            return pn
    # 4. Substring containment (min 3 chars)
    for pn in param_names:
        pnl = pn.lower()
        if len(pnl) >= 3 and (pnl in vl or vl in pnl):
            return pn
    return None


# Code generation LRU cache — avoids re-compiling identical templates
_CODEGEN_CACHE: OrderedDict[str, tuple[str, float]] = OrderedDict()
_CODEGEN_CACHE_LOCK = threading.Lock()
_CODEGEN_CACHE_TTL = 600.0  # 10 min
_CODEGEN_CACHE_MAX = 50

def _codegen_cache_key(name: str, description: str, inputs: str | None, overlay: bool) -> str:
    return xxhash.xxh64(f"{name}|{description}|{inputs}|{overlay}".encode()).hexdigest()

def _get_codegen_cache(key: str) -> str | None:
    with _CODEGEN_CACHE_LOCK:
        if key in _CODEGEN_CACHE:
            result, ts = _CODEGEN_CACHE[key]
            if time.time() - ts < _CODEGEN_CACHE_TTL:
                return result
            del _CODEGEN_CACHE[key]
    return None

def _set_codegen_cache(key: str, result: str) -> None:
    with _CODEGEN_CACHE_LOCK:
        _CODEGEN_CACHE[key] = (result, time.time())
        _CODEGEN_CACHE.move_to_end(key)
        while len(_CODEGEN_CACHE) > _CODEGEN_CACHE_MAX:
            _CODEGEN_CACHE.popitem(last=False)


@mcp.tool(annotations=ToolAnnotations(title="Generate Indicator Template", readOnlyHint=False, openWorldHint=True, destructiveHint=True, idempotentHint=False))
async def generate_indicator(
    name: Annotated[str, Field(
        min_length=1,
        max_length=100,
        description="Indicator display name",
    )],
    description: Annotated[str, Field(
        default="",
        max_length=500,
        description="What the indicator calculates",
    )] = "",
    inputs: Annotated[str | None, Field(
        default=None,
        description="Comma-separated input descriptions, e.g. 'length=14,src=close,mult=2.0'",
    )] = None,
    overlay: Annotated[bool, Field(
        default=False,
        description="True if indicator overlays the price chart",
    )] = False,
) -> str:
    """
    Generate a syntactically correct PineScript v6 indicator template.
    Validates the output with pine-facade before returning.

    Args:
        name: Indicator display name
        description: What the indicator calculates
        inputs: List of input parameter descriptions (optional)
        overlay: True if indicator overlays the price chart
    """
    try:
        name = name.strip()
        if not name:
            return "ERROR: No indicator name provided. Pass a display name for the indicator."
        safe_name = _sanitize_pine_string(name)

        # ── Cache check: avoid re-compiling identical templates ──
        cache_key = _codegen_cache_key(safe_name, description or "", inputs or "", overlay)
        cached_result = _get_codegen_cache(cache_key)
        if cached_result:
            return cached_result

        # ── Phase 0: Check for known indicator templates ──
        # Fast-path: if the description matches a well-known indicator family,
        # use a hand-verified template instead of relying on vector search.
        template_source = "none"
        matched_keywords = _extract_indicator_keywords(description or name)
        if matched_keywords:
            # Use the first matched keyword's template
            family = matched_keywords[0]
            if family in _INDICATOR_TEMPLATES:
                calc_stub, overlay_default = _INDICATOR_TEMPLATES[family]
                if not overlay:
                    overlay = overlay_default
                template_source = f"template:{family}"

        # ── Phase 1: Build input lines (unchanged logic) ──
        input_lines = []
        if inputs:
            for raw_inp in inputs.split(","):
                raw_inp = raw_inp.strip()
                if not raw_inp:
                    continue
                # Parse key=value pairs (e.g. "rsiLength=14", "src=close", "mult=2.0")
                pine_type = "float"
                default_val = "1.0"
                var_name = ""
                display_name = raw_inp

                if "=" in raw_inp:
                    key, val = raw_inp.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    var_name = key.replace(" ", "_").replace("-", "_")
                    display_name = key
                    inp_lower = key.lower()

                    # Infer Pine type and default from the value
                    if val.startswith('"') or val.startswith("'"):
                        pine_type = "string"
                        default_val = val
                    elif val in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
                        pine_type = "source"
                        default_val = val
                    elif val.lower() in ("true", "false"):
                        pine_type = "bool"
                        default_val = val.lower()
                    elif "." in val:
                        pine_type = "float"
                        default_val = val
                    else:
                        try:
                            int(val)
                            pine_type = "int"
                            default_val = val
                        except ValueError:
                            pine_type = "float"
                            default_val = val
                else:
                    # Bare name — infer type from semantic hints
                    inp_lower = raw_inp.lower()
                    var_name = raw_inp.replace(" ", "_").replace("-", "_")
                    if any(k in inp_lower for k in ("length", "period", "len")):
                        pine_type = "int"
                        default_val = "20"
                    elif any(k in inp_lower for k in ("source", "src")):
                        pine_type = "source"
                        default_val = "close"
                    elif any(k in inp_lower for k in ("mult", "factor", "multiplier")):
                        pine_type = "float"
                        default_val = "2.0"
                    elif any(k in inp_lower for k in ("color", "colour")):
                        pine_type = "color"
                        default_val = "color.blue"
                    elif any(k in inp_lower for k in ("enable", "use", "show")):
                        pine_type = "bool"
                        default_val = "true"

                # Generate the input.*() call
                if pine_type == "source":
                    input_lines.append(
                        f'{var_name} = input.source({default_val}, "{display_name}")'
                    )
                elif pine_type == "int":
                    input_lines.append(
                        f'int {var_name} = input.int({default_val}, "{display_name}")'
                    )
                elif pine_type == "float":
                    input_lines.append(
                        f'float {var_name} = input.float({default_val}, "{display_name}")'
                    )
                elif pine_type == "bool":
                    input_lines.append(
                        f'bool {var_name} = input.bool({default_val}, "{display_name}")'
                    )
                elif pine_type == "string":
                    input_lines.append(
                        f'string {var_name} = input.string({default_val}, "{display_name}")'
                    )
                elif pine_type == "color":
                    input_lines.append(
                        f'{var_name} = input.color({default_val}, "{display_name}")'
                    )

        # ── Phase 2: Search docs with namespace-aware queries ──
        # If no template matched, search the vector store.
        # Run TWO queries in parallel: one constrained to ta.* namespace
        # (high confidence for technical indicators), one unconstrained as fallback.
        relevant_funcs = []
        calc_stub_phase2 = "plot(close, \"Price\", color.blue)"  # safe default

        if template_source == "none":
            # Build enriched query from keyword extraction
            # This gives the embedding model better signal than raw description
            enrich_terms = {
                "rsi": "ta.rsi relative strength index oscillator",
                "macd": "ta.macd moving average convergence divergence",
                "bollinger": "ta.bb bollinger bands standard deviation",
                "ema": "ta.ema exponential moving average",
                "sma": "ta.sma simple moving average",
                "atr": "ta.atr average true range volatility",
                "stochastic": "ta.stoch stochastic oscillator k d",
                "supertrend": "ta.supertrend super trend",
                "vwap": "ta.vwap volume weighted average price",
                "adl": "ta.accdist accumulation distribution line",
                "obv": "ta.obv on balance volume",
                "cci": "ta.cci commodity channel index",
                "mfi": "ta.mfi money flow index",
                "williams": "ta.wpr williams percent range",
            }
            kw = _extract_indicator_keywords(description or name)
            if kw:
                enriched_query = f"{description} {enrich_terms.get(kw[0], '')}"
            else:
                enriched_query = description

            # Single broad query — then prioritize ta.* results in Python.
            # Saves one full embedding + ChromaDB round-trip (~30-50ms).
            combined_results = await _query_async(
                enriched_query, 10,
                where={"category": "function"}
            )
            db_err = _check_query_error(combined_results)
            if db_err:
                return db_err

            # Pick best result, preferring ta.* namespace
            best_meta = None
            best_dist = 1.0
            best_query_label = ""

            if combined_results.get("ids") and combined_results["ids"][0]:
                for i, (meta, dist) in enumerate(
                    zip(combined_results["metadatas"][0], combined_results["distances"][0])
                ):
                    fname = meta.get("name", "?")
                    fsyntax = meta.get("syntax", "")
                    # Deduplicate by name
                    if any(rf_name == fname for rf_name, _ in relevant_funcs):
                        continue
                    relevant_funcs.append((fname, f"//   {fname}: {fsyntax[:80]}"))

                    # Prefer ta.* results; among equal namespace, prefer lower distance
                    label = "ta" if fname.startswith("ta.") else "broad"
                    # Apply a 0.05 distance bonus for ta.* namespace
                    effective_dist = dist - (0.05 if label == "ta" else 0.0)
                    if best_meta is None or effective_dist < best_dist:
                        best_meta = meta
                        best_dist = effective_dist
                        best_query_label = label

            # ── Phase 3: Relevance gating ──
            # Tightened from 0.8 to 0.6.  The old name_match was too loose;
            # now we require BOTH distance < 0.6 AND the result is from ta.*
            # namespace OR a strong keyword match in the function name itself.
            if best_meta is not None:
                top_name = best_meta.get("name", "")
                top_desc = (best_meta.get("raw_description") or "").lower()
                desc_lower = (description or "").lower()

                # Strong keyword match: indicator-family name appears in function name
                strong_name_match = False
                for kw_str in _extract_indicator_keywords(description or name):
                    if kw_str in top_name.lower():
                        strong_name_match = True
                        break

                # Accept if: (distance < 0.6) OR (ta.* namespace AND distance < 0.75) OR (strong name match)
                is_ta_ns = top_name.startswith("ta.")
                accept = (
                    best_dist < 0.6
                    or (is_ta_ns and best_dist < 0.75)
                    or strong_name_match
                )

                if accept:
                    # Gather variable names from inputs
                    input_vars = []
                    for il in input_lines:
                        parts = il.split("=")
                        if len(parts) >= 1:
                            var_part = parts[0].strip()
                            tokens = var_part.split()
                            input_vars.append(tokens[-1] if tokens else var_part)

                    # Parse function parameters
                    raw_params = best_meta.get("raw_parameters", "")
                    param_names = []
                    if raw_params:
                        try:
                            params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
                            param_names = [p.get("name", "") for p in params if isinstance(p, dict)]
                        except (json.JSONDecodeError, TypeError):
                            pass

                    # Build args using improved param matching
                    args_list = []
                    if input_vars and param_names:
                        for pv in input_vars:
                            matched_param = _map_input_to_param(pv, param_names)
                            if matched_param:
                                args_list.append(f"{matched_param}={pv}")
                            else:
                                args_list.append(pv)
                    elif param_names:
                        # Auto-fill first param as close for ta.* functions
                        if top_name.startswith("ta.") and param_names:
                            if param_names[0] in ("source", "src"):
                                args_list.append("source=close")
                            else:
                                args_list.append(f"{param_names[0]}=close")
                        for pn in param_names[1:]:
                            if "length" in pn.lower() or "period" in pn.lower():
                                args_list.append(f"{pn}=14")
                            elif "mult" in pn.lower() or "factor" in pn.lower():
                                args_list.append(f"{pn}=2.0")
                    else:
                        args_list = input_vars if input_vars else ["close"]

                    args = ", ".join(args_list) if args_list else "close"
                    calc_stub_phase2 = (
                        f"result = {top_name}({args})\n"
                        f"plot(result, \"Result\", color.orange)"
                    )
                    template_source = f"search:{top_name} (dist={best_dist:.3f}, {best_query_label})"
                else:
                    # Top result too far away; keep the safe default plot
                    template_source = f"rejected:{top_name} (dist={best_dist:.3f}, too far)"
        else:
            # Template matched — still run a search to populate relevant_funcs
            # for the "RELEVANT FUNCTIONS" section of the output
            ta_results = await _query_async(
                description or name, 5,
                where={"$and": [{"category": "function"}, {"namespace": "ta"}]}
            )
            if ta_results.get("ids") and ta_results["ids"][0]:
                for meta in ta_results["metadatas"][0][:5]:
                    fname = meta.get("name", "?")
                    fsyntax = meta.get("syntax", "")
                    relevant_funcs.append((fname, f"//   {fname}: {fsyntax[:80]}"))

        # Use template stub if matched, otherwise use search-derived stub
        if template_source.startswith("template:"):
            pass  # calc_stub already set in Phase 0
        else:
            calc_stub = calc_stub_phase2

        # Extract just the formatted strings for output
        relevant_func_strings = [rf[1] for rf in relevant_funcs]

        # ── Phase 4: Generate template ──
        code = f"""//@version=6
indicator("{safe_name}", overlay={str(overlay).lower()}, shorttitle="{safe_name[:16]}")

// ── Inputs ──"""
        for il in input_lines:
            code += f"\n{il}"
        if not input_lines:
            code += "\n// (Add your inputs here with input.int, input.float, input.source, etc.)"

        code += f"""

// ── Calculations ──
// {description}
// Available functions from docs:"""
        for rf in relevant_func_strings:
            code += f"\n{rf}"
        if not relevant_func_strings:
            code += (
                "\n// (Use search_docs or suggest_functions to find relevant functions)"
            )

        code += f"""
{calc_stub}
"""

        # Validate
        validation = await _call_pine_facade(code)
        errors = validation.get("errors", [])

        lines = []
        lines.append("GENERATED INDICATOR TEMPLATE:")
        lines.append("=" * 50)
        lines.append(code)
        lines.append("")

        if errors:
            lines.append(
                f"VALIDATION: {len(errors)} compilation issues (template may need manual fixes)"
            )
            for err in errors:
                lines.append(
                    f"  Line {err.get('line', '?')}, Col {err.get('column', '?')}: {err.get('text', '?')} [{err.get('type', '?')}]"
                )
        else:
            lines.append("VALIDATION: Template compiles successfully.")

        if relevant_func_strings:
            lines.append("")
            lines.append("RELEVANT FUNCTIONS from docs:")
            for rf in relevant_func_strings:
                lines.append(f"  {rf}")

        lines.append(f"\nSOURCE: {template_source}")

        result = _cap_response("\n".join(lines))
        _set_codegen_cache(cache_key, result)
        return result

    except Exception as e:
        logger.error(f"[generate_indicator] {e}")
        if _chroma_breaker.is_open():
            raise ToolError(_circuit_breaker_msg())
        raise ToolError(_safe_error(e, "generate_indicator"))

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 15: generate_strategy
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Generate Strategy Template", readOnlyHint=False, openWorldHint=True, destructiveHint=True, idempotentHint=False))
async def generate_strategy(
    name: Annotated[str, Field(
        min_length=1,
        max_length=100,
        description="Strategy display name",
    )],
    description: Annotated[str, Field(
        default="",
        max_length=500,
        description="Trading strategy description",
    )] = "",
    initial_capital: Annotated[int, Field(
        default=10000,
        ge=1,
        le=1000000,
        description="Starting capital (default 10000)",
    )] = 10000,
    commission_pct: Annotated[float, Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Commission percentage (default 0.1)",
    )] = 0.1,
    pyramiding: Annotated[int, Field(
        default=1,
        ge=1,
        le=10,
        description="Max simultaneous positions (default 1)",
    )] = 1,
) -> str:
    """
    Generate a syntactically correct PineScript v6 strategy template.
    Validates the output with pine-facade before returning.
    Includes all required strategy() parameters and entry/exit scaffolding.

    Args:
        name: Strategy display name
        description: Trading strategy description
        initial_capital: Starting capital (default 10000)
        commission_pct: Commission percentage (default 0.1)
        pyramiding: Max simultaneous positions (default 1)
    """
    try:
        name = name.strip()
        if not name:
            return "ERROR: No strategy name provided. Pass a display name for the strategy."
        safe_name = _sanitize_pine_string(name)

        # Search docs for strategy-related functions
        relevant = await _query_async(description, 5, where={"namespace": "strategy"})
        db_err = _check_query_error(relevant)
        if db_err:
            return db_err
        # Build relevant function list
        relevant_funcs = []
        if relevant.get("ids") and relevant["ids"][0]:
            for meta in relevant["metadatas"][0][:5]:
                fname = meta.get("name", "?")
                fsyntax = meta.get("syntax", "")
                relevant_funcs.append(f"//   {fname}: {fsyntax[:80]}")

        # BUG FIX: Use correct v6 input.bool syntax (default value first, then title)
        template = f"""//@version=6
strategy("{safe_name}", overlay=true,
    initial_capital={initial_capital},
    commission_type=strategy.commission.percent,
    commission_value={commission_pct},
    default_qty_type=strategy.percent_of_equity,
    default_qty_value=100,
    pyramiding={pyramiding},
    margin_long=0, margin_short=0,
    calc_on_every_tick=false)

// ── Inputs ──────────────────────────────────────────────────
enableLong  = input.bool(true,  "Enable Long",  group="Filters")
enableShort = input.bool(false, "Enable Short", group="Filters")
src         = input.source(close, "Source",     group="Settings")
fastLen     = input.int(12, "Fast Length", minval=1, group="Settings")
slowLen     = input.int(26, "Slow Length", minval=2, group="Settings")

// ── Calculations ─────────────────────────────────────────────
fastMA = ta.ema(src, fastLen)
slowMA = ta.ema(src, slowLen)

// ── Conditions ───────────────────────────────────────────────
longCondition  = ta.crossover(fastMA, slowMA)
shortCondition = ta.crossunder(fastMA, slowMA)

// ── Entries ──────────────────────────────────────────────────
if enableLong and longCondition and barstate.isconfirmed
    strategy.entry("Long", strategy.long)

if enableShort and shortCondition and barstate.isconfirmed
    strategy.entry("Short", strategy.short)

// ── Exits ────────────────────────────────────────────────────
strategy.exit("Long Exit",  from_entry="Long",  
                profit=na, loss=na)
strategy.exit("Short Exit", from_entry="Short", 
                profit=na, loss=na)

// ── Cleanup ──────────────────────────────────────────────────
if barstate.islast
    strategy.close_all()
"""

        # BUG FIX: Compile-guard: validate before returning
        validation = await _call_pine_facade(template)
        if not validation["success"]:
            errors_str = "\n".join(
                f"  Line {e['line']}: {e['text']}"
                for e in validation["errors"][:5]
            )
            return (f"⚠️ Template generation failed validation:\n{errors_str}\n\n"
                    f"Raw template for manual fix:\n```pine\n{template}\n```")

        lines = []
        lines.append("GENERATED STRATEGY TEMPLATE")
        lines.append("=" * 50)
        lines.append("Validated: ✅ 0 compilation errors")
        lines.append("")
        lines.append("```pine")
        lines.append(template.strip())
        lines.append("```")

        if relevant_funcs:
            lines.append("")
            lines.append("RELEVANT STRATEGY FUNCTIONS from docs:")
            for rf in relevant_funcs:
                lines.append(f"  {rf}")

        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[generate_strategy] {e}")
        if _chroma_breaker.is_open():
            raise ToolError(_circuit_breaker_msg())
        raise ToolError(_safe_error(e, "generate_strategy"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 16: lookup_and_correct
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Lookup and Correct Code", readOnlyHint=True, openWorldHint=True, destructiveHint=True, idempotentHint=False))
async def lookup_and_correct(
    code: Annotated[str, Field(
        max_length=50000,
        description="The PineScript code (can be partial or full script)",
    )] = "",
    error_description: Annotated[str, Field(
        max_length=500,
        description="What the code is supposed to do",
    )] = "",
) -> str:
    """
    Given a PineScript code snippet and what it's supposed to do,
    validates it, looks up correct syntax for any issues, and returns
    a corrected version with explanations.

    Use when user shares code and asks 'what's wrong with this'.

    Args:
        code: The PineScript code (can be partial or full script)
        error_description: What the code is supposed to do
    """
    try:
        code = code.strip()
        error_description = error_description.strip()
        if not code:
            return "ERROR: No code provided. Pass the PineScript code snippet to look up and correct."
        if not error_description:
            return "ERROR: No description provided. Describe what the code is supposed to do."

        # Step 1: Validate
        validation = await _call_pine_facade(code)
        errors = validation.get("errors", [])

        # Step 2: Apply ALL v5→v6 namespace fixes
        fixed_code = code
        changes_made = []

        # BUG FIX: Complete v5 → v6 namespace migration map
        # NOTE: (?<!\.) prevents double-prefixing; \b ensures whole-word match
        V5_TO_V6 = {
            # ta.* functions (most common v5 issue)
            r'(?<!\.)\bema\s*\(':          'ta.ema(',
            r'(?<!\.)\bsma\s*\(':          'ta.sma(',
            r'(?<!\.)\brsi\s*\(':          'ta.rsi(',
            r'(?<!\.)\bmacd\s*\(':         'ta.macd(',
            r'(?<!\.)\batr\s*\(':          'ta.atr(',
            r'(?<!\.)\bbb\s*\(':           'ta.bb(',
            r'(?<!\.)\bstoch\s*\(':        'ta.stoch(',
            r'(?<!\.)\bwma\s*\(':          'ta.wma(',
            r'(?<!\.)\bhma\s*\(':          'ta.hma(',
            r'(?<!\.)\bvwap\b':            'ta.vwap',
            r'(?<!\.)\bcrossover\s*\(':    'ta.crossover(',
            r'(?<!\.)\bcrossunder\s*\(':   'ta.crossunder(',
            r'(?<!\.)\bhighest\s*\(':      'ta.highest(',
            r'(?<!\.)\blowest\s*\(':       'ta.lowest(',
            r'(?<!\.)\bbarssince\s*\(':    'ta.barssince(',
            r'(?<!\.)\bvaluewhen\s*\(':    'ta.valuewhen(',
            r'(?<!\.)\blinreg\s*\(':       'ta.linreg(',
            r'(?<!\.)\bmom\s*\(':          'ta.mom(',
            r'(?<!\.)\bcum\s*\(':          'ta.cum(',
            r'(?<!\.)\bchange\s*\(':       'ta.change(',
            r'(?<!\.)\bpivothigh\s*\(':    'ta.pivothigh(',
            r'(?<!\.)\bpivotlow\s*\(':     'ta.pivotlow(',
            r'(?<!\.)\bsupertrend\s*\(':   'ta.supertrend(',
            r'(?<!\.)\bcorrelation\s*\(':  'ta.correlation(',
            r'(?<!\.)\bpercentrank\s*\(':  'ta.percentrank(',
            r'(?<!\.)\bdmi\s*\(':          'ta.dmi(',
            r'(?<!\.)\bstdev\s*\(':        'ta.stdev(',
            r'(?<!\.)\bvariance\s*\(':     'ta.variance(',
            # request.* functions
            r'(?<!\.)\bsecurity\s*\(':     'request.security(',
            # math.* functions
            r'(?<!\.)\babs\s*\(':          'math.abs(',
            r'(?<!\.)\bround\s*\(':        'math.round(',
            r'(?<!\.)\bfloor\s*\(':        'math.floor(',
            r'(?<!\.)\bceil\s*\(':         'math.ceil(',
            r'(?<!\.)\bpow\s*\(':          'math.pow(',
            r'(?<!\.)\bsqrt\s*\(':         'math.sqrt(',
            r'(?<!\.)\blog\s*\(':          'math.log(',
            r'(?<!\.)\bexp\s*\(':          'math.exp(',
            r'(?<!\.)\bsign\s*\(':         'math.sign(',
            r'(?<!\.)\bsin\s*\(':          'math.sin(',
            r'(?<!\.)\bcos\s*\(':          'math.cos(',
            r'(?<!\.)\bmax\s*\(':          'math.max(',
            r'(?<!\.)\bmin\s*\(':          'math.min(',
            # str.* functions
            r'(?<!\.)\btostring\s*\(':     'str.tostring(',
            r'(?<!\.)\btonumber\s*\(':     'str.tonumber(',
        }

        # v6 breaking changes: transp, when, implicit bool, bool=na
        # Pattern: transp=N → remove (use color.new instead)
        transp_pattern = re.compile(r',\s*transp\s*=\s*\d+')
        if transp_pattern.search(fixed_code):
            fixed_code = transp_pattern.sub('', fixed_code)
            changes_made.append("Removed transp= parameter (v6: use color.new())")

        # Pattern: bool x = na → var bool x = false
        bool_na = re.compile(r'\bbool\s+(\w+)\s*=\s*na\b')
        if bool_na.search(fixed_code):
            fixed_code = bool_na.sub(r'var bool \1 = false', fixed_code)
            changes_made.append("Changed 'bool x = na' to 'var bool x = false' (v6)")

        # Pattern: if volume/close (implicit bool) → if volume > 0
        implicit_bool = re.compile(r'\bif\s+(volume|close|open|high|low)\b(?!\s*[<>=!])')
        if implicit_bool.search(fixed_code):
            fixed_code = implicit_bool.sub(r'if \1 > 0', fixed_code)
            changes_made.append("Added explicit > 0 (v6: implicit bool removed)")

        # Pattern: study() → indicator() (v5 → v6)
        study_pattern = re.compile(r'\bstudy\s*\(')
        if study_pattern.search(fixed_code):
            fixed_code = study_pattern.sub('indicator(', fixed_code)
            changes_made.append("Replaced study() → indicator() (v6)")

        # Apply ALL replacements sequentially
        for pattern, replacement in V5_TO_V6.items():
            if re.search(pattern, fixed_code):
                fixed_code = re.sub(pattern, replacement, fixed_code)
                changes_made.append(f"Replaced: {pattern} → {replacement}")

        # Step 3: Re-validate the fixed code
        validation_after = await _call_pine_facade(fixed_code)
        errors_after = validation_after.get("errors", [])

        # Step 4: Search docs for intent
        intent_results = await _query_async(error_description, 3)
        # Non-critical: if DB is down for intent lookup, just show no docs section

        intent_err = _check_query_error(intent_results)
        # Non-critical: intent lookup failure shouldn't block the correction report

        lines = []
        lines.append("LOOKUP AND CORRECT REPORT")
        lines.append("=" * 50)
        lines.append("")

        # Show validation before
        if errors:
            lines.append(f"BEFORE FIXES: {len(errors)} issue(s) found")
            for i, err in enumerate(errors[:3], 1):
                text = err.get("text", "?")
                line_num = err.get("line", "?")
                col_num = err.get("column", "?")
                err_type = err.get("type", "error")
                lines.append(f"  Issue {i} (Line {line_num}, Col {col_num} [{err_type}]): {text}")
            if len(errors) > 3:
                lines.append(f"  ... and {len(errors) - 3} more issues")
            lines.append("")
        else:
            lines.append("BEFORE FIXES: No compilation errors. Code appears correct.")
            lines.append("")

        # Show changes made
        if changes_made:
            lines.append(f"NAMESPACE FIXES APPLIED: {len(changes_made)}")
            for change in changes_made:
                lines.append(f"  • {change}")
            lines.append("")
        else:
            lines.append("NAMESPACE FIXES: No v5→v6 namespace issues detected.")
            lines.append("")

        # Show validation after
        if errors_after:
            lines.append(f"AFTER FIXES: {len(errors_after)} issue(s) remain")
            for i, err in enumerate(errors_after[:3], 1):
                text = err.get("text", "?")
                line_num = err.get("line", "?")
                col_num = err.get("column", "?")
                err_type = err.get("type", "error")
                lines.append(f"  Issue {i} (Line {line_num}, Col {col_num} [{err_type}]): {text}")
            if len(errors_after) > 3:
                lines.append(f"  ... and {len(errors_after) - 3} more issues")
            lines.append("")
        else:
            lines.append("AFTER FIXES: ✅ All issues resolved! Code compiles successfully.")
            lines.append("")

        # Show relevant docs for intent
        lines.append(f"RELEVANT DOCS FOR '{error_description}':")
        lines.append("-" * 40)
        if intent_results.get("ids") and intent_results["ids"][0]:
            for i, (meta, doc, dist) in enumerate(
                zip(
                    intent_results["metadatas"][0],
                    intent_results["documents"][0],
                    intent_results["distances"][0],
                ),
                1,
            ):
                name = meta.get("name", "?")
                syntax = meta.get("syntax", "")
                ns = meta.get("namespace") or ""
                ns_prefix = f"{ns}." if ns and not name.startswith(f"{ns}.") else ""
                url = meta.get("url", "")
                lines.append(f"  {i}. {ns_prefix}{name}")
                if syntax:
                    lines.append(f"     {syntax[:100]}")
                if url:
                    lines.append(f"     {url}")
                lines.append("")
        else:
            lines.append("  No results found.")

        # Show fixed code if changes were made
        if changes_made:
            lines.append("FIXED CODE:")
            lines.append("```pine")
            lines.append(fixed_code)
            lines.append("```")

        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[lookup_and_correct] {e}")
        if _chroma_breaker.is_open():
            raise ToolError(_circuit_breaker_msg())
        raise ToolError(_safe_error(e, "lookup_and_correct"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 17: debug_pine_facade
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Debug Pine Facade", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def debug_pine_facade(
    code: Annotated[str, Field(
        max_length=50000,
        description="Complete PineScript v6 source code to compile",
    )] = "",
) -> str:
    """
    Diagnostic tool: compile code via pine-facade and return the FULL raw
    response alongside the normalized interpretation. Use for debugging
    when validate_syntax or validate_and_explain produce unexpected results.

    Args:
        code: Complete PineScript v6 source code to compile
    """
    try:
        code = code.strip()
        if not code:
            return "ERROR: No code provided. Pass the PineScript v6 source code to debug."

        result = await _call_pine_facade(code)

        lines = []
        lines.append("DEBUG PINE-FACADE REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Circuit breaker stats
        cb_stats = _pine_cb.stats()
        lines.append("CIRCUIT BREAKER:")
        for k, v in cb_stats.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

        # Normalized result
        lines.append("NORMALIZED RESULT:")
        lines.append(f"  success: {result.get('success', '?')}")
        lines.append(f"  errors: {len(result.get('errors', []))}")
        lines.append(f"  warnings: {len(result.get('warnings', []))}")
        lines.append("")

        errors = result.get("errors", [])
        if errors:
            lines.append("ERRORS (normalized):")
            for i, err in enumerate(errors, 1):
                lines.append(
                    f"  [{i}] line={err.get('line')} col={err.get('column')} type={err.get('type')}"
                )
                lines.append(f"      text: {err.get('text', '?')}")
            lines.append("")

        warnings = result.get("warnings", [])
        if warnings:
            lines.append("WARNINGS (normalized):")
            for i, warn in enumerate(warnings, 1):
                lines.append(
                    f"  [{i}] line={warn.get('line')} col={warn.get('column')}"
                )
                lines.append(f"      text: {warn.get('text', '?')}")
            lines.append("")

        # Raw response
        raw = result.get("raw_response", {})
        lines.append("RAW RESPONSE:")
        lines.append(json.dumps(raw, indent=2, default=str)[:2000])
        lines.append("")

        # Validation cache
        lines.append(f"Validation cache entries: {len(_VALIDATION_CACHE)}")

        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[debug_pine_facade] {e}")
        # Return full diagnostic on error
        cb_stats = _pine_cb.stats()
        return (
            f"DEBUG PINE-FACADE ERROR\n"
            f"Exception: {_safe_error(e, 'debug_pine_facade')}\n"
            f"Circuit breaker: {json.dumps(cb_stats)}\n"
            f"Cache entries: {len(_VALIDATION_CACHE)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 18: suggest_functions
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Suggest Functions", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
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

        results = await _query_async(query_text, n_results, where={"category": "function"})

        db_err = _check_query_error(results)
        if db_err:
            return db_err

        if not results.get("ids") or not results["ids"][0]:
            return _error(
                "suggest_functions", f"No functions found for '{context}'"
            )

        lines = []
        lines.append(f"SUGGESTED FUNCTIONS for '{context}':")
        lines.append("")

        for i, (meta, doc, dist) in enumerate(
            zip(
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            ),
            1,
        ):
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

        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[suggest_functions] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "suggest_functions"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 19: get_namespace_cheatsheet
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=ToolAnnotations(title="Namespace Cheatsheet", readOnlyHint=True, openWorldHint=False, idempotentHint=True))
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
        ns = _norm_ns(namespace)
        if ns == "global":
            where: Optional[dict] = {"namespace": ""}
        else:
            where = {"namespace": ns}

        entries = await _get_all_where_async(where)
        if not entries:
            raise ToolError(f"No entries found for namespace '{ns}'")

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
        return _cap_response("\n".join(lines))

    except Exception as e:
        logger.error(f"[get_namespace_cheatsheet] {e}")
        if _chroma_breaker.is_open():
            return _circuit_breaker_msg()
        raise ToolError(_safe_error(e, "get_namespace_cheatsheet"))


# ─────────────────────────────────────────────────────────────────────────────
# Resource: pinescript://stats
# ─────────────────────────────────────────────────────────────────────────────


@mcp.resource("pinescript://stats")
async def get_stats() -> str:
    """Return database statistics as JSON string. No paths or internal details leaked."""
    try:
        col = _get_collection()
        total = col.count()

        return json.dumps(
            {
                "total_entries": total,
                "hot_cache_entries": len(HOT_CACHE),
                "pine_facade_circuit_open": _pine_cb.is_open(),
                "chroma_circuit_open": _chroma_breaker.is_open(),
                "validation_cache_entries": len(_VALIDATION_CACHE),
                "file_validation_cache_entries": len(_FILE_VALIDATION_CACHE),
                "embedding_model_ready": _embedding_model_ready.is_set(),
                "total_tools": 20,
                "version": "4.0",
            },
            indent=2,
        )
    except Exception as e:
        logger.error(f"[get_stats] {e}")
        return json.dumps(
            {
                "error": _safe_error(e, "get_stats"),
                "total_tools": 20,
                "version": "4.0",
            },
            indent=2,
        )


@mcp.tool(annotations=ToolAnnotations(title="Validate PineScript File", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def validate_file(
    file_path: Annotated[str, Field(
        description="Absolute path to PineScript v6 file to validate"
    )]
) -> str:
    """
    Validate a PineScript v6 file by path instead of content.

    This tool bypasses MCP parameter size limitations by reading the file
    directly on the server side. Use this for large files (>30KB) that
    cannot be passed as inline parameters through Claude Code.

    Optimization: caches results keyed on (path, mtime_ns, size). Re-validating
    an unchanged file returns the cached result in <1ms instead of ~2800ms.
    Runs local linter as fast-reject before remote compile.

    Args:
        file_path: Absolute path to the .ps file to validate

    Returns:
        Validation results in the same format as validate_syntax
    """
    if not file_path:
        return "ERROR: No file path provided. Provide an absolute path to a PineScript file."

    # Path safety: resolve symlinks, enforce .ps extension, allowlist directories
    try:
        resolved = os.path.realpath(file_path)
    except Exception:
        return "ERROR: Invalid path provided."

    if not resolved.endswith('.ps') and not resolved.endswith('.pine'):
        return "ERROR: Only .ps and .pine files are accepted."

    # Allowlist: only permit files under these base directories
    _allowed = any(
        resolved.startswith(d + os.sep) or resolved == d
        for d in _ALLOWED_BASE_DIRS
    )
    if not _allowed:
        return "ERROR: Access denied -- path outside allowed scope."

    if not os.path.exists(resolved) or not os.path.isfile(resolved):
        return "ERROR: File not found or inaccessible."

    # File size limit: prevent OOM on large files
    _MAX_FILE_SIZE = 1_000_000  # 1MB
    try:
        file_stat = os.stat(resolved)
        if file_stat.st_size > _MAX_FILE_SIZE:
            return f"ERROR: File too large ({file_stat.st_size:,} bytes). Maximum allowed: {_MAX_FILE_SIZE:,} bytes."
    except Exception as e:
        return f"ERROR: Cannot stat file -- {_safe_error(e, 'validate_file')}"

    # -- Optimization 1: mtime-based cache (skip everything if file unchanged) --
    # Key = (resolved_path, mtime_ns, size). If file hasn't been written to,
    # the content is identical to last validation -- return cached result in <1ms.
    mtime_ns = file_stat.st_mtime_ns
    fsize = file_stat.st_size
    cached_response = _get_cached_file_validation(resolved, mtime_ns, fsize)
    if cached_response is not None:
        return cached_response

    # Read file content (only reached on cache miss)
    try:
        with open(resolved, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception as e:
        return f"ERROR: Failed to read file -- {_safe_error(e, 'validate_file')}"

    # Get file stats for display
    file_size = len(code)
    line_count = code.count('\n') + 1

    # Validate using the same logic as validate_syntax
    try:
        code = code.strip()
        if not code:
            return f"ERROR: File is empty or contains only whitespace: {file_path}"

        # -- Optimization 2: fast-reject via local linter --
        # Run local linter first (~5-15ms). If it finds errors, return immediately
        # without waiting for the ~2800ms remote compile. The remote compiler will
        # be called on the next validation (after user fixes the linter-caught errors),
        # at which point the linter should pass clean and we proceed to remote.
        local_result = _pine_lint(code)
        local_errors = local_result.to_dict().get("errors", [])

        if local_errors:
            # Local linter found errors -- fast-reject, skip remote call entirely.
            errors = _enrich_error_with_code(local_errors, code)
            meta = local_result.to_dict().get("meta", {})
            warnings = local_result.to_dict().get("warnings", [])

            response = f"FILE: {file_path}\n"
            response += f"Size: {file_size:,} bytes | Lines: {line_count:,}\n"
            response += "=" * 80 + "\n\n"
            total_issues = len(errors) + len(warnings)
            response += f"COMPILATION ISSUES ({total_issues})\n"
            response += "Compiler: Local Linter (Tier 1) -- fast-reject\n"
            response += f"Errors: {len(errors)} | Warnings: {len(warnings)}\n\n"

            for idx, err in enumerate(errors, 1):
                line = err.get("line", "?")
                col = err.get("column", "?")
                text = err.get("text", "Unknown error")
                err_type = err.get("type", "error")
                response += f"  ERROR {idx} -- Line {line}, Col {col} [{err_type.upper()}]\n"
                response += f"    {text}\n"
                hint = _lookup_fix_hint(text)
                if hint:
                    response += f"    Fix hint: {hint}\n"
                response += "\n"

            for idx, warn in enumerate(warnings, 1):
                line = warn.get("line", "?")
                col = warn.get("column", "?")
                text = warn.get("text", "Unknown warning")
                response += f"  WARNING {idx} -- Line {line}, Col {col}\n"
                response += f"    {text}\n\n"

            # Cache the fast-reject result (file won't compile remotely either)
            _cache_file_validation(resolved, mtime_ns, fsize, response)
            return response

        # -- Linter passed clean -- proceed to remote compiler --
        # skip_lint=True avoids running the linter again inside _call_pine_facade
        result = await _call_pine_facade(code, skip_lint=True)

        errors = _enrich_error_with_code(result.get("errors", []), code)
        warnings = result.get("warnings", [])
        success = result.get("success", False)
        meta = result.get("meta", {})
        is_fallback = meta.get("fallback") == "local_linter_tier1"
        compiler_label = "Local Linter (Tier 1)" if is_fallback else "TradingView pine-facade v6"

        # Build response with file info
        response = f"FILE: {file_path}\n"
        response += f"Size: {file_size:,} bytes | Lines: {line_count:,}\n"
        response += "=" * 80 + "\n\n"

        if success and not errors and not warnings:
            response += "VALID -- PineScript v6 code compiles successfully.\n\n"
            response += f"Compiler: {compiler_label}\n"
            response += f"Errors: 0 | Warnings: 0\n"
            if is_fallback and meta.get("note"):
                response += f"\nNote: {meta['note']}\n"
            _cache_file_validation(resolved, mtime_ns, fsize, response)
            return response

        # Has errors or warnings
        total_issues = len(errors) + len(warnings)
        response += f"{'COMPILATION ISSUES' if errors else 'WARNINGS'} ({total_issues})\n"
        response += f"Compiler: {compiler_label}\n"

        if is_fallback and meta.get("note"):
            response += f"Note: {meta['note']}\n"

        response += f"Errors: {len(errors)} | Warnings: {len(warnings)}\n\n"

        # Display errors
        for idx, err in enumerate(errors, 1):
            line = err.get("line", "?")
            col = err.get("column", "?")
            text = err.get("text", "Unknown error")
            err_type = err.get("type", "error")
            response += f"  ERROR {idx} -- Line {line}, Col {col} [{err_type.upper()}]\n"
            response += f"    {text}\n"

            hint = _lookup_fix_hint(text)
            if hint:
                response += f"    Fix hint: {hint}\n"
            response += "\n"

        # Display warnings
        for idx, warn in enumerate(warnings, 1):
            line = warn.get("line", "?")
            col = warn.get("column", "?")
            text = warn.get("text", "Unknown warning")
            response += f"  WARNING {idx} -- Line {line}, Col {col}\n"
            response += f"    {text}\n\n"

        _cache_file_validation(resolved, mtime_ns, fsize, response)
        return response

    except Exception as e:
        logger.exception("Unexpected error in validate_file")
        return f"ERROR: Validation failed -- {_safe_error(e, 'validate_file')}"

# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting PineScript v6 Complete Reference MCP server v4.0 (20 tools, 100% local)")
    mcp.run(transport="stdio")
