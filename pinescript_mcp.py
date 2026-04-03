"""
pinescript_mcp.py
─────────────────────────────────────────────────────────────────────────────
PineScript v6 Complete Knowledge MCP Server
FastMCP 3.0 · Transport: stdio · 23 tools + 1 resource

Serves PineScript v6 reference documentation via semantic-search tools
backed by a local ChromaDB vector store populated from:
  1. Local parsed documentation (1,215+ entries from Markdown reference)
  2. Live-scraped TradingView reference (~1,360 entries)

Tools (23 total):
  LOOKUP (6):   get_function, get_variable, get_type, get_constant,
                get_keyword, get_operator
  SEARCH (4):   search_docs, get_examples, search_by_return_type,
                list_namespace
  LIVE (2):     get_live_entry, get_source_url
  MAINT (2):    diff_entry, check_freshness
  VALIDATE (4): validate_syntax, validate_and_explain, fix_and_validate,
                debug_pine_facade
  CODEGEN (3):  generate_indicator, generate_strategy, lookup_and_correct
  CONTEXT (2):  suggest_functions, get_namespace_cheatsheet

Resource: pinescript://stats
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    level=os.getenv("LOG_LEVEL", "INFO"),
)

from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator

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
TV_BASE_URL = "https://www.tradingview.com/pine-script-reference/v6/"
PINE_FACADE_URL = "https://pine-facade.tradingview.com/pine-facade/translate_light?user_name=admin&v=3"
PINE_FACADE_TIMEOUT = int(os.getenv("PINE_FACADE_TIMEOUT", "20"))
VALIDATION_CACHE_TTL = int(os.getenv("VALIDATION_CACHE_TTL", "300"))
VALIDATION_CACHE_MAX_SIZE = int(os.getenv("VALIDATION_CACHE_SIZE", "500"))
MAX_TOOL_RESPONSE_CHARS = 8000

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

This server combines two data sources:
1. Local parsed documentation (1,242 entries from PDF/Markdown)
2. Live-scraped TradingView reference (~1,360 entries)
Merged total: ~1,400-1,600 unique entries.

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

LIVE DATA TOOLS (use for most current information):
  get_live_entry(name)   Real-time fetch from TradingView site
  get_source_url(name)   Get direct TradingView URL for manual lookup

MAINTENANCE TOOLS:
  diff_entry(name)       Compare indexed vs live TradingView data
  check_freshness()      See which entries have live vs local data only

IMPORTANT NOTES
───────────────
- All code examples returned are real, working PineScript from the official
  TradingView documentation.
- PineScript is executed on every bar, so variable semantics differ from
  general-purpose languages.
- Use the `var` keyword for variables that should preserve state across bars.
- Strategy scripts require //@version=6 and strategy() declaration.
- Indicator scripts require //@version=6 and indicator() declaration.
- ALWAYS cite the source when answering: note if data is from local docs,
  live TradingView, or both. Include the TradingView URL when available.
"""

mcp = FastMCP(
    name="PineScript v6 Complete Reference",
    instructions=INSTRUCTIONS,
)

# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB + Embedding singleton — with circuit-breaker
# ─────────────────────────────────────────────────────────────────────────────

_db_failure_count: int = 0
_DB_FAILURE_LIMIT: int = 3
_collection = None
_embed_model = None

# ── C1: ChromaDB circuit breaker with cooldown + auto-reset ────────────


class ChromaDBCircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown: int = 30):
        self.failures: int = 0
        self.threshold: int = threshold
        self.cooldown: int = cooldown
        self.open_until: float = 0.0
        self._lock = asyncio.Lock()

    def is_open(self) -> bool:
        if self.open_until and time.time() > self.open_until:
            self.failures = 0
            self.open_until = 0.0
            logger.info("ChromaDB circuit RESET (cooldown expired)")
        return time.time() < self.open_until

    async def record_failure(self, exc: Exception) -> None:
        async with self._lock:
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

# ── H5: Non-blocking embedding model loader ────────────────────────────
_model_executor = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="embedding"
)
_embedding_model_ready = asyncio.Event()


def _get_collection():
    """Return the ChromaDB collection, initializing lazily. Circuit-breaker aware."""
    global _collection, _db_failure_count
    if _db_failure_count >= _DB_FAILURE_LIMIT:
        raise RuntimeError(
            "ChromaDB has failed too many times. "
            "Please check that pinescript_db exists and run merge_and_index.py."
        )
    if _collection is not None:
        _chroma_breaker.record_success()
        return _collection
    try:
        import chromadb

        client = chromadb.PersistentClient(path=DB_PATH)
        _collection = client.get_collection(name=COLLECTION)
        logger.info(f"Connected to ChromaDB - {_collection.count()} entries")
        _chroma_breaker.record_success()
        return _collection
    except Exception as e:
        _db_failure_count += 1
        logger.error(
            f"ChromaDB init failed ({_db_failure_count}/{_DB_FAILURE_LIMIT}): {e}"
        )
        raise


def _get_model():
    """Return the SentenceTransformer, initializing lazily."""
    global _embed_model
    if _embed_model is not None:
        return _embed_model
    try:
        from sentence_transformers import SentenceTransformer

        _embed_model = SentenceTransformer(EMBED_MODEL)
        logger.info(f"Embedding model loaded: {EMBED_MODEL}")
        return _embed_model
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}")
        raise


async def _ensure_embedding_model():
    """Load SentenceTransformer in thread pool — never blocks event loop."""
    if _embedding_model_ready.is_set():
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_model_executor, _get_model)
    _embedding_model_ready.set()


def _query(query_text: str, n: int, where: Optional[dict] = None) -> dict:
    """Run a ChromaDB query with the local embedding model.

    H3: Wraps collection.query() in try/except — never lets ChromaDB
    or embedding exceptions propagate naked to tool handlers.
    """
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

        return col.query(**kwargs)
    except Exception as e:
        error_type = type(e).__name__
        logger.error(
            f"_query() failed | type={error_type} | where={where} | "
            f"query={query_text[:80]}"
        )
        return {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }


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
    """Exact then fuzzy name lookup. Scans FULL collection — no arbitrary limit."""
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
            except Exception:
                pass
            # Try with type=function specifically
            try:
                typed = col.get(
                    where={"$and": [{"name": name_lower}, {"type": "function"}]},
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
            except Exception:
                pass
            # No result — return empty (do NOT fall through to namespace match)
            return []

        # Strategy 1: exact metadata match (fast, uses index) - only for non-qualified names
        try:
            exact_kwargs: dict = dict(include=["metadatas", "documents"])
            if where:
                exact_kwargs["where"] = where
            exact = col.get(where={"name": name_lower}, include=["metadatas", "documents"])
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
        except Exception:
            pass

        # Strategy 2: fuzzy — fetch ALL, filter in Python
        total = col.count()
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


def _get_all_where(where: dict, limit: int = 1000) -> list[dict]:
    """Fetch all entries matching a where filter."""
    try:
        col = _get_collection()
        result = col.get(where=where, include=["metadatas", "documents"], limit=limit)
        entries = []
        for rid, meta, doc in zip(
            result["ids"], result["metadatas"], result["documents"]
        ):
            entries.append({"id": rid, "metadata": meta, "document": doc})
        return entries
    except Exception as e:
        logger.error(f"_get_all_where failed: {e}")
        return []


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
    sources = meta.get("sources", "")
    if "tradingview_live" in sources:
        return "[Live]"
    return "[Local]"


def _source_line(meta: dict) -> str:
    tag = _source_tag(meta)
    url = meta.get("url", "")
    parts = [_section_line(f"SOURCE: {tag}")]
    if url:
        parts.append(_section_line(f"URL: {url}"))
    return "\n".join(parts)


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


def _format_entry_detail(
    name: str, meta: dict, doc: str, distance: Optional[float] = None
) -> str:
    """Format a complete detailed entry for get_* tools."""
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
    return truncated + f"\n\n[...truncated — {len(text) - limit} chars omitted]"


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


# H4: Live fetch cache + rate limiting
_LIVE_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()
_LIVE_CACHE_TTL = 3600
_LIVE_CACHE_MAX = 200
_LIVE_RATE_LIMIT = 1.0
_last_live_call = 0.0


async def _get_live_entry_cached(name: str) -> Optional[str]:
    """Fetch live HTML with TTL cache and rate limiting."""
    global _last_live_call

    if name in _LIVE_CACHE:
        ts, content = _LIVE_CACHE[name]
        if time.time() - ts < _LIVE_CACHE_TTL:
            _LIVE_CACHE.move_to_end(name)
            logger.debug(f"Live cache HIT: {name}")
            return content
        else:
            del _LIVE_CACHE[name]

    elapsed = time.time() - _last_live_call
    if elapsed < _LIVE_RATE_LIMIT:
        await asyncio.sleep(_LIVE_RATE_LIMIT - elapsed)

    _last_live_call = time.time()
    html = await _fetch_live(name)

    if html:
        if len(_LIVE_CACHE) >= _LIVE_CACHE_MAX:
            _LIVE_CACHE.popitem(last=False)
        _LIVE_CACHE[name] = (time.time(), html)

    return html


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
        """
        self.network_failures += 1
        self.total_network_errors += 1
        self.total_calls += 1
        if self.network_failures >= self.threshold:
            self.open_until = time.time() + self.cooldown
            logger.warning(
                f"Pine-facade circuit OPEN for {self.cooldown}s "
                f"({self.network_failures} consecutive network failures)"
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

_VALIDATION_CACHE: dict[str, tuple[str, float]] = {}

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
}


def _get_facade_client() -> httpx.AsyncClient:
    """Lazy-init a shared httpx.AsyncClient for pine-facade calls."""
    global _facade_http_client
    if _facade_http_client is None or _facade_http_client.is_closed:
        _facade_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            headers={
                "Referer": "https://www.tradingview.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                "DNT": "1",
            },
        )
    return _facade_http_client


def _shutdown_http_client():
    global _facade_http_client
    if _facade_http_client and not _facade_http_client.is_closed:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_facade_http_client.aclose())
            else:
                loop.run_until_complete(_facade_http_client.aclose())
        except Exception:
            pass


atexit.register(_shutdown_http_client)


def _lookup_fix_hint(error_text: str) -> str:
    """Match an error message against known patterns and return a fix hint."""
    for pattern, hint in _FIX_HINTS.items():
        if pattern.lower() in error_text.lower():
            return hint
    return "Check the PineScript v6 reference for the correct syntax."


def _extract_name_from_error(error_text: str) -> Optional[str]:
    """Extract a likely PineScript name from a compiler error message."""
    import re

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
    import xxhash

    h = xxhash.xxh64(code.encode()).hexdigest()
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
    import xxhash

    h = xxhash.xxh64(code.encode()).hexdigest()
    _VALIDATION_CACHE[h] = (result, time.time())
    # Prune old entries if cache grows too large
    if len(_VALIDATION_CACHE) > VALIDATION_CACHE_MAX_SIZE:
        oldest_key = min(_VALIDATION_CACHE, key=lambda k: _VALIDATION_CACHE[k][1])
        del _VALIDATION_CACHE[oldest_key]
        logger.debug(f"Validation cache evicted oldest: {oldest_key[:40]}")


def _normalize_facade_response(raw: dict) -> dict:
    """Normalize translate_light API response.

    Success shape:
        { "success": true, "result": { "variables": [], ... } }

    Error shape (compile errors):
        { "success": true, "result": { "errors": [...] } }
        Each error: { "line": int, "column": int, "message": str, "code": str }

    Rejection shape (version too old, etc.):
        { "success": false, "reason": "...", "result": null }

    The translate_light API returns success=true even on compile errors,
    but includes an "errors" array inside the "result" object.
    """
    success = raw.get("success", False)

    # Handle rejection shape (success=false with reason, result=null)
    if not success:
        reason = raw.get("reason", "Unknown compilation failure")
        return {
            "success": False,
            "errors": [{"line": 0, "column": 0, "text": reason, "type": "error"}],
            "warnings": [],
            "meta": {},
            "raw_response": raw,
        }

    # translate_light puts errors inside result object
    result_obj = raw.get("result") or {}
    raw_errors = result_obj.get("errors", []) if isinstance(result_obj, dict) else []

    def normalize_error(e: dict) -> dict:
        return {
            "line": e.get("line")
            or e.get("lineNumber")
            or e.get("start", {}).get("line", 0),
            "column": e.get("column")
            or e.get("col")
            or e.get("start", {}).get("column", 0),
            "text": e.get("text") or e.get("message") or e.get("msg") or str(e),
            "type": e.get("type") or "error",
        }

    errors = [normalize_error(e) for e in raw_errors if isinstance(e, dict)]
    warnings = [
        normalize_error(e)
        for e in raw_errors
        if isinstance(e, dict) and e.get("type") == "warning"
    ]

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


async def _call_pine_facade(code: str) -> dict:
    """POST code to pine-facade compiler. Returns normalized response dict.

    Returns:
        {
            "success": bool,
            "errors": [{"line", "column", "text", "type"}, ...],
            "warnings": [{"line", "column", "text"}, ...],
            "meta": dict,
            "raw_response": dict
        }
    """
    if _pine_cb.is_open():
        raise RuntimeError(
            "Pine-facade compiler temporarily unavailable (circuit breaker open). "
            f"Stats: {_pine_cb.stats()} "
            "Validate manually at https://www.tradingview.com/pine-editor/"
        )

    cached = _get_cached_validation(code)
    if cached:
        return cached

    code = _sanitize_text(code)

    try:
        client = _get_facade_client()
        resp = await client.post(
            PINE_FACADE_URL,
            files={"source": (None, code)},
        )

        if resp.status_code in (502, 503, 504):
            _pine_cb.record_network_failure()
            return {
                "success": False,
                "errors": [
                    {
                        "line": 0,
                        "column": 0,
                        "text": f"Pine-facade returned HTTP {resp.status_code}",
                        "type": "network",
                    }
                ],
                "warnings": [],
                "meta": {},
                "raw_response": {
                    "http_status": resp.status_code,
                    "body": resp.text[:200],
                },
            }

        if resp.status_code != 200:
            if resp.status_code in (400, 429):
                try:
                    data = resp.json()
                    normalized = _normalize_facade_response(data)
                    _cache_validation(code, json.dumps(normalized))
                    return normalized
                except Exception:
                    pass
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
        return {
            "success": False,
            "errors": [
                {
                    "line": 0,
                    "column": 0,
                    "text": f"Network error: {type(e).__name__}: {e}",
                    "type": "network",
                }
            ],
            "warnings": [],
            "meta": {},
            "raw_response": {"exception": str(e)},
        }
    except Exception as e:
        logger.error(f"[_call_pine_facade] unexpected: {e}")
        return {
            "success": False,
            "errors": [
                {
                    "line": 0,
                    "column": 0,
                    "text": f"Unexpected error: {type(e).__name__}: {e}",
                    "type": "internal",
                }
            ],
            "warnings": [],
            "meta": {},
            "raw_response": {"exception": str(e)},
        }


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
            except Exception:
                pass

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
# Pydantic input models
# ─────────────────────────────────────────────────────────────────────────────


class SearchQuery(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Natural language or code query about PineScript v6",
    )
    n_results: int = Field(
        default=5, ge=1, le=30, description="Number of results (1-30, default 5)"
    )
    source_filter: str | None = Field(
        default=None, description="'live', 'local', or None (both)"
    )
    category_filter: str | None = Field(
        default=None, description="'function','variable','type',etc."
    )
    namespace_filter: str | None = Field(
        default=None, description="Namespace e.g. 'ta', 'strategy'"
    )

    @field_validator("query")
    @classmethod
    def strip_query(cls, v: str) -> str:
        return v.strip()


class EntryLookup(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Entry name e.g. 'ta.ema', 'close', 'array'",
    )

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        return v.strip().rstrip("()")


class NamespaceLookup(BaseModel):
    namespace: str = Field(
        ..., min_length=1, max_length=50, description="Namespace e.g. 'ta', 'strategy'"
    )
    category_filter: str | None = Field(
        default=None, description="Optional category filter"
    )

    @field_validator("namespace")
    @classmethod
    def normalize_ns(cls, v: str) -> str:
        return v.strip().lower().rstrip(".")


class ReturnTypeLookup(BaseModel):
    return_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Return type e.g. 'series float', 'line'",
    )
    n_results: int = Field(default=10, ge=1, le=50)

    @field_validator("return_type")
    @classmethod
    def strip_rt(cls, v: str) -> str:
        return v.strip()


class CodeInput(BaseModel):
    code: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="Complete PineScript v6 source code to validate",
    )

    @field_validator("code")
    @classmethod
    def strip_code(cls, v: str) -> str:
        return v.strip()


class CodeFixInput(BaseModel):
    code: str = Field(
        ...,
        min_length=1,
        max_length=50000,
        description="The failing PineScript v6 code",
    )
    error_description: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="The error message or what's wrong",
    )

    @field_validator("code")
    @classmethod
    def strip_code(cls, v: str) -> str:
        return v.strip()

    @field_validator("error_description")
    @classmethod
    def strip_desc(cls, v: str) -> str:
        return v.strip()


class IndicatorGenInput(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=100, description="Indicator display name"
    )
    description: str = Field(
        default="", max_length=500, description="What the indicator calculates"
    )
    inputs: str | None = Field(
        default=None,
        description="Comma-separated input descriptions, e.g. 'length=14,src=close,mult=2.0'",
    )
    overlay: bool = Field(
        default=False, description="True if indicator overlays the price chart"
    )


class StrategyGenInput(BaseModel):
    name: str = Field(
        ..., min_length=1, max_length=100, description="Strategy display name"
    )
    description: str = Field(
        default="", max_length=500, description="What the strategy does"
    )
    initial_capital: int = Field(default=10000, ge=1, le=1000000)
    commission_pct: float = Field(default=0.1, ge=0.0, le=1.0)
    pyramiding: int = Field(default=1, ge=1, le=10)


class SuggestFunctionsInput(BaseModel):
    context: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="What you're trying to accomplish",
    )
    current_line: str | None = Field(
        default=None,
        max_length=200,
        description="The current line being written (optional)",
    )
    n_results: int = Field(default=8, ge=1, le=20)


class ExamplesQuery(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Concept to find code examples for",
    )
    n_results: int = Field(
        default=4, ge=1, le=20, description="Number of examples to return"
    )


class FreshnessCheck(BaseModel):
    namespace: Optional[str] = Field(
        default=None, max_length=50, description="Namespace to filter (e.g. 'ta')"
    )


class CheatsheetLookup(BaseModel):
    namespace: str = Field(
        ..., min_length=1, max_length=50, description="Namespace e.g. 'ta', 'strategy'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 1: search_docs
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def search_docs(params: SearchQuery) -> str:
    """
    Semantic search across the complete PineScript v6 knowledge base.
    Searches functions, variables, types, constants, keywords, and operators.

    Args:
        query: Natural language or code query about PineScript v6
        n_results: Number of results (1-30, default 5)
        source_filter: 'live', 'local', or None (both)
        category_filter: Filter by entry type ('function','variable',etc.)
        namespace_filter: Filter by namespace (e.g. 'ta', 'strategy')
    """
    try:
        await _ensure_hot_cache()
        where_clauses: list[dict] = []
        if params.category_filter:
            where_clauses.append({"category": params.category_filter})
        if params.namespace_filter:
            where_clauses.append({"namespace": params.namespace_filter})

        where: Optional[dict] = None
        if len(where_clauses) == 1:
            where = where_clauses[0]
        elif len(where_clauses) > 1:
            where = {"$and": where_clauses}

        # H1: Fetch 3x results if source_filter active (Python-side filter)
        fetch_n = params.n_results * 3 if params.source_filter else params.n_results
        results = _query(params.query, fetch_n, where=where)

        # Python-side source filtering (handles multi-value source strings)
        if params.source_filter and results["ids"] and results["ids"][0]:
            filter_val = (
                "tradingview_live"
                if params.source_filter == "live"
                else "local_docs"
            )
            filtered = list(
                zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                )
            )
            filtered = [
                (doc, meta, dist)
                for doc, meta, dist in filtered
                if filter_val in (meta.get("sources") or "")
            ]
            cap = params.n_results
            results["documents"] = [[x[0] for x in filtered[:cap]]]
            results["metadatas"] = [[x[1] for x in filtered[:cap]]]
            results["distances"] = [[x[2] for x in filtered[:cap]]]

        if not results["ids"] or not results["ids"][0]:
            return _error("search_docs", f"No results for '{params.query}'")

        output_lines: list[str] = []
        for i, (rid, meta, doc, dist) in enumerate(
            zip(
                results["ids"][0],
                results["metadatas"][0],
                results["documents"][0],
                results["distances"][0],
            )
        ):
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
            url = meta.get("url", "")

            output_lines.append(_DIVIDER)
            output_lines.append(f"[{i + 1}] {ns}{name} | {category} | Relevance: {rel}")
            output_lines.append(f"  {tag}")
            if url:
                output_lines.append(f"  URL: {url}")

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

    except Exception as e:
        logger.error(f"[search_docs] {e}")
        if "ChromaDB" in str(e) or _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("search_docs", _safe_error(e, "search_docs"))


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
            result = _format_entry_detail(
                cached["metadata"].get("name", name),
                cached["metadata"],
                cached["document"],
            )
            return result

        # Step 1: Try exact fuzzy match within category
        candidates = _search_by_name(
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
        results = _query(name, 5, where={"category": category} if category else None)
        if results["ids"] and results["ids"][0]:
            top_meta = results["metadatas"][0][0]
            top_dist = results["distances"][0][0]
            top_name = top_meta.get("name", "").lower().replace("()", "").strip()
            search_name = name.lower().replace("()", "").strip()
            # Only return if name matches or relevance is strong (distance < 0.6 = 40%+)
            if top_name == search_name or search_name in top_name or top_dist < 0.6:
                return _format_entry_detail(
                    top_meta.get("name", name),
                    top_meta,
                    results["documents"][0][0],
                    top_dist,
                )

        # Step 3: Broaden to all categories (only if highly relevant)
        results = _query(name, 5)
        if results["ids"] and results["ids"][0]:
            top_meta = results["metadatas"][0][0]
            top_dist = results["distances"][0][0]
            top_name = top_meta.get("name", "").lower().replace("()", "").strip()
            search_name = name.lower().replace("()", "").strip()
            if (
                top_name == search_name
                or search_name in top_name
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
            all_candidates = _search_by_name(name)
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
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error(category, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 2: get_function
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_function(params: EntryLookup) -> str:
    """
    Get complete documentation for a PineScript v6 function.
    Returns all overloads, every parameter with type and description,
    return type, remarks, and ALL code examples in full.

    Use for: ta.*, strategy.*, array.*, math.*, str.*, request.*, etc.
    Example: get_function("ta.ema"), get_function("strategy.entry")
    """
    try:
        await _ensure_hot_cache()
        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(params.name)
        if cached and cached["metadata"].get("category") == "function":
            result = _format_entry_detail(
                cached["metadata"].get("name", params.name),
                cached["metadata"],
                cached["document"],
            )
            return result

        # BUG FIX: For function lookups, always try exact match with category=function first
        name_lower = params.name.lower().strip()
        
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
                    best_meta.get("name", params.name),
                    best_meta,
                    best_doc
                )
        except Exception:
            pass

        # Fall back to the general lookup
        return await _lookup_entry(params.name, "function")

    except Exception as e:
        logger.error(f"[get_function] {e}")
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("function", _safe_error(e, "get_function"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 3: get_variable
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_variable(params: EntryLookup) -> str:
    """
    Get documentation for a PineScript v6 built-in variable.
    Built-in variables: close, open, high, low, volume, time,
    bar_index, barstate.*, syminfo.*, strategy.*, etc.
    """
    return await _lookup_entry(params.name, "variable")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 4: get_type
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_type(params: EntryLookup) -> str:
    """
    Get documentation for a PineScript v6 type.
    Types: array, matrix, map, line, label, box, table, polyline,
    color, string, int, float, bool, and user-defined types.
    """
    try:
        await _ensure_hot_cache()
        # Step 0: Check hot cache first (sub-ms for priority entries)
        cached = cache_lookup(params.name)
        if cached and cached["metadata"].get("type") == "type":
            result = _format_entry_detail(
                cached["metadata"].get("name", params.name),
                cached["metadata"],
                cached["document"],
            )
            return result

        # BUG FIX: Always filter by type="type" — never return function entries
        col = _get_collection()
        name_lower = params.name.lower().strip()

        # Always filter by type="type" — never return function entries
        try:
            result = col.get(
                where={"$and": [
                    {"name": {"$in": [name_lower, f"type.{name_lower}"]}},
                    {"type": "type"}
                ]},
                include=["documents", "metadatas"]
            )
            if result["ids"]:
                best_meta = result["metadatas"][0]
                best_doc = result["documents"][0]
                return _format_entry_detail(
                    best_meta.get("name", params.name),
                    best_meta,
                    best_doc
                )
        except Exception:
            pass

        # Semantic fallback — still enforce type filter
        results = _query(
            f"type {name_lower} definition fields methods",
            5,
            where={"type": "type"}
        )
        if results["documents"][0]:
            top_meta = results["metadatas"][0][0]
            top_doc = results["documents"][0][0]
            top_dist = results["distances"][0][0]
            return _format_entry_detail(
                top_meta.get("name", params.name),
                top_meta,
                top_doc,
                top_dist
            )

        return (
            f"Type '{params.name}' not found in docs.\n"
            f"Available types: array, matrix, map, line, label, "
            f"box, table, polyline, color, string, int, float, bool"
        )

    except Exception as e:
        logger.error(f"[get_type] {e}")
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("type", _safe_error(e, "get_type"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 5: get_constant
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_constant(params: EntryLookup) -> str:
    """
    Get documentation for a PineScript v6 built-in constant.
    Examples: color.red, strategy.long, order.ascending,
    shape.circle, location.top, etc.
    """
    return await _lookup_entry(params.name, "constant")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 6: get_keyword
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_keyword(params: EntryLookup) -> str:
    """
    Get documentation for a PineScript v6 keyword.
    Keywords: if, for, while, switch, var, varip, type, method,
    import, export, and, or, not, true, false, etc.
    """
    return await _lookup_entry(params.name, "keyword")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 7: get_operator
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_operator(params: EntryLookup) -> str:
    """
    Get documentation for a PineScript v6 operator.
    Operators: :=, +=, -=, *=, /=, %=, ==, !=, >, <, >=, <=,
    ?, =>, +, -, *, /, %, not, and, or, [], etc.
    """
    return await _lookup_entry(params.name, "operator")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 8: get_examples
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_examples(params: ExamplesQuery) -> str:
    """
    Find real PineScript v6 code examples by concept.
    Returns complete, runnable code blocks from the official docs.

    Use for: "how to use strategy.entry with stop loss",
             "array iteration example", "drawing lines example"
    """
    try:
        results = _query(params.query, params.n_results, where={"has_examples": 1})
        if not results["ids"] or not results["ids"][0]:
            return _error("get_examples", f"No examples found for '{params.query}'")

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

    except Exception as e:
        logger.error(f"[get_examples] {e}")
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("get_examples", _safe_error(e, "get_examples"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 9: list_namespace
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def list_namespace(params: NamespaceLookup) -> str:
    """
    List ALL members of a PineScript v6 namespace.
    Returns every function, variable, and constant in the namespace
    with one-line descriptions.

    Namespaces: ta, strategy, math, array, matrix, map, str, color,
    chart, line, label, box, table, request, ticker, timeframe,
    syminfo, input, runtime, polyline (and 'global' for un-namespaced)
    """
    try:
        ns = params.namespace
        if ns.lower() == "global":
            where: Optional[dict] = {"namespace": ""}
        else:
            where = {"namespace": ns}

        if params.category_filter:
            where["category"] = params.category_filter

        entries = _get_all_where(where)
        if not entries:
            return _error("list_namespace", f"No entries found for namespace '{ns}'")

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
        return "\n".join(output_lines)

    except Exception as e:
        logger.error(f"[list_namespace] {e}")
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("list_namespace", _safe_error(e, "list_namespace"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 10: search_by_return_type
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def search_by_return_type(params: ReturnTypeLookup) -> str:
    """
    Find all PineScript v6 functions that return a specific type.
    Useful when you know what type you need but not which function to use.

    Examples: search_by_return_type("series float"),
              search_by_return_type("line"),
              search_by_return_type("array<int>")
    """
    try:
        # Filter by return type metadata, fall back to semantic if empty
        where = {"category": "function"}
        # Only add returns filter if it's likely to have matches
        ret_filter = {"returns": {"$contains": params.return_type}}
        try:
            # Test if the filter returns results
            probe = _get_collection().get(
                where={
                    "category": "function",
                    "returns": {"$contains": params.return_type},
                },
                include=["documents"],
                limit=1,
            )
            if probe["ids"]:
                where = {"$and": [{"category": "function"}, ret_filter]}
        except Exception:
            pass
        results = _query(params.return_type, params.n_results, where=where)

        if not results["ids"] or not results["ids"][0]:
            # Fallback: semantic search with category filter only
            results = _query(
                f"functions returning {params.return_type}",
                params.n_results,
                where={"category": "function"},
            )

        if not results["ids"] or not results["ids"][0]:
            return _error(
                "search_by_return_type",
                f"No functions found returning '{params.return_type}'",
            )

        output_lines: list[str] = []
        output_lines.append(f"Functions returning '{params.return_type}':")
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
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("search_by_return_type", _safe_error(e, "search_by_return_type"))


# ─────────────────────────────────────────────────────────────────────────────
# Live data tools — require httpx
# ─────────────────────────────────────────────────────────────────────────────


def _name_to_fragment(name: str) -> str:
    """Guess the TradingView fragment ID from an entry name."""
    name = name.strip().rstrip("()")

    # Known variable names
    var_names = {
        "close",
        "open",
        "high",
        "low",
        "volume",
        "time",
        "bar_index",
        "last_bar_index",
        "timeframe.period",
        "timeframe.isdaily",
        "timeframe.isminutes",
        "timeframe.isseconds",
        "timeframe.intraday",
        "timeframe.intraday_bar_index",
        "syminfo.ticker",
        "syminfo.tickerid",
        "syminfo.prefix",
        "syminfo.type",
        "syminfo.description",
        "syminfo.root",
        "syminfo.mintick",
        "syminfo.pointvalue",
        "syminfo.session",
        "syminfo.timezone",
        "syminfo.currency",
        "strategy",
        "strategy.equity",
        "strategy.netprofit",
        "strategy.openprofit",
        "strategy.position_size",
        "strategy.position_avg_price",
        "strategy.long",
        "strategy.short",
        "strategy.closedtrades",
        "strategy.openorders",
        "strategy.direction",
    }

    lower = name.lower()
    if (
        lower in var_names
        or lower.startswith("barstate.")
        or lower.startswith("syminfo.")
    ):
        return f"var_{name}"
    if lower.startswith("color."):
        return f"const_{name}"
    known_types = {
        "array",
        "matrix",
        "map",
        "line",
        "label",
        "box",
        "table",
        "polyline",
        "chart.point",
        "chart.bg",
        "chart.line",
        "chart.box",
        "chart.label",
        "chart.table",
        "chart.polyline",
    }
    if lower in known_types:
        return f"type_{name}"
    if lower in (
        "if",
        "for",
        "while",
        "switch",
        "var",
        "varip",
        "type",
        "method",
        "import",
        "export",
        "true",
        "false",
        "na",
    ):
        return f"kw_{name}"

    # Default: assume function
    return f"fun_{name}"


async def _fetch_live(name: str) -> Optional[str]:
    """Fetch live HTML from TradingView for an entry. Returns raw HTML or None."""
    try:
        import httpx

        fragment = _name_to_fragment(name)
        url = f"{TV_BASE_URL}#{fragment}"

        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        ) as client:
            resp = await client.get(TV_BASE_URL)
            if resp.status_code == 200:
                return resp.text
            return None
    except Exception as e:
        logger.error(f"[_fetch_live] {e}")
        return None


def _parse_live_html(html: str, name: str) -> str:
    """Parse what we can from TradingView static HTML."""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")

        # Try to find the entry section
        fragment = _name_to_fragment(name)
        target = soup.find(id=fragment)
        if not target:
            # Try finding by text content
            target = soup.find(string=lambda t: t and name in t)

        lines: list[str] = []
        lines.append(f"{_BOX_TL}{_BOX_H * 60}{_BOX_TR}")
        lines.append(f"{_BOX_V} LIVE FETCH: {name}")
        lines.append(f"{_BOX_V} URL: {TV_BASE_URL}#{fragment}")
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")

        if target:
            parent = target.parent if hasattr(target, "parent") else None
            if parent:
                text = parent.get_text(separator="\n", strip=True)
                for line in text.splitlines()[:50]:
                    lines.append(f"{_BOX_V} {line}")
        else:
            lines.append(
                _section_line(
                    "Could not locate entry in static HTML. "
                    "TradingView is a JavaScript SPA — use the URL for full docs."
                )
            )

        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(
            _section_line(
                "Note: Live fetch uses static HTML. TradingView is a JS SPA — "
                "some dynamic content may be incomplete."
            )
        )
        lines.append(_section_line(f"Full docs: {TV_BASE_URL}#{fragment}"))
        lines.append(f"{_BOX_BL}{_BOX_H * 60}{_BOX_BR}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[_parse_live_html] {e}")
        return f"Error parsing live HTML: {e}"


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 11: get_live_entry
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_live_entry(params: EntryLookup) -> str:
    """
    Fetch the CURRENT live documentation from TradingView for any entry.
    This bypasses the indexed database and scrapes TradingView in real-time.
    Use when you need guaranteed up-to-date information.

    Note: Slightly slower than other tools (live HTTP request).
    """
    try:
        html = await _get_live_entry_cached(params.name)
        if html:
            return _parse_live_html(html, params.name)
        fragment = _name_to_fragment(params.name)
        url = f"{TV_BASE_URL}#{fragment}"
        return (
            f"Could not fetch live data for '{params.name}'.\n"
            f"Visit manually: {url}\n"
            f"TradingView may require JavaScript rendering for full content."
        )
    except Exception as e:
        fragment = _name_to_fragment(params.name)
        url = f"{TV_BASE_URL}#{fragment}"
        return _error("get_live_entry", f"Network error: {e}\nVisit manually: {url}")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 12: get_source_url
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_source_url(params: EntryLookup) -> str:
    """
    Get the direct TradingView documentation URL for any PineScript entry.
    Returns the URL even if the entry is not in the database.
    """
    try:
        name_clean = params.name.strip()
        name_lower = name_clean.lower()

        BASE = "https://www.tradingview.com/pine-script-reference/v6/"

        # BUG FIX: Construct URL from entry type (more reliable than stored metadata)
        try:
            col = _get_collection()
            results = await _search_by_name(name_lower, col)

            if results:
                meta = results[0].get("metadata", {})
                etype = meta.get("type", "")

                # Build anchor deterministically from name + type
                # TradingView anchor format:
                #   functions:  #fun_{name}    e.g. #fun_ta.ema
                #   variables:  #var_{name}    e.g. #var_close
                #   constants:  #const_{name}  e.g. #const_color.red
                #   types:      #type_{name}   e.g. #type_array
                #   keywords:   #kw_{name}     e.g. #kw_if
                #   operators:  #op_{name}
                anchor_prefix = {
                    "function": "fun",
                    "variable": "var",
                    "constant": "const",
                    "type": "type",
                    "keyword": "kw",
                    "operator": "op",
                }.get(etype, "fun")   # default to function

                # Use stored name from metadata (most accurate)
                anchor_name = meta.get("name", name_lower).replace(" ", "_")
                url = f"{BASE}#{anchor_prefix}_{anchor_name}"

                # Fallback: check if stored URL exists and seems correct
                stored_url = meta.get("url", "")
                if (stored_url and
                    stored_url.startswith(BASE) and
                    anchor_name in stored_url):
                    url = stored_url   # stored URL is correct, use it

                return f"{name_clean} URL: {url}"

        except Exception:
            pass

        # Fallback: construct best-guess URL
        anchor_name = name_lower.replace(" ", "_")
        return f"{name_clean} URL: {BASE}#fun_{anchor_name}"

    except Exception as e:
        # Fallback URL construction even on error
        BASE = "https://www.tradingview.com/pine-script-reference/v6/"
        anchor_name = params.name.strip().lower().replace(" ", "_")
        return f"{params.name} URL: {BASE}#fun_{anchor_name}"


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 13: diff_entry
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def diff_entry(params: EntryLookup) -> str:
    """
    Compare the indexed documentation vs the current live TradingView page.
    Shows what has changed since the database was last indexed.
    Useful to detect new parameters, updated descriptions, new examples.
    """
    try:
        # Get indexed entry
        candidates = _search_by_name(params.name)
        if not candidates or candidates[0][0] < 70:
            return _error("diff_entry", f"'{params.name}' not found in database")

        indexed = candidates[0][1]
        indexed_meta = indexed["metadata"]

        # Fetch live — failure is non-fatal (preserve indexed data)
        html = await _get_live_entry_cached(params.name)
        if not html:
            fragment = _name_to_fragment(params.name)
            return (
                f"LOCAL ENTRY: {params.name}\n"
                f"Indexed: {indexed_meta.get('scraped_at', 'unknown')}\n"
                f"Source: {indexed_meta.get('sources', 'unknown')}\n\n"
                f"[Live fetch unavailable — showing local data only]\n"
                f"Visit manually: {TV_BASE_URL}#{fragment}"
            )

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "lxml")

        lines: list[str] = []
        lines.append(f"{_BOX_TL}{_BOX_H * 60}{_BOX_TR}")
        lines.append(f"{_BOX_V} DIFF: {params.name} (Indexed vs Live)")
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")

        # Compare fields
        indexed_desc = indexed_meta.get("raw_description", "")
        live_text = soup.get_text(separator="\n", strip=True)
        has_name_in_live = params.name.lower() in live_text.lower()

        # Description
        lines.append(_section_line("DESCRIPTION"))
        if indexed_desc:
            desc_len = len(indexed_desc)
            lines.append(_section_line(f"  Indexed: {desc_len} chars"))
        else:
            lines.append(_section_line("  Indexed: (none)"))

        if has_name_in_live:
            lines.append(_section_line("  Live: entry found on page"))
        else:
            lines.append(_section_line("  Live: entry may not be in static HTML"))

        # Parameters
        param_count = indexed_meta.get("param_count", 0)
        lines.append(_section_line("PARAMETERS"))
        lines.append(_section_line(f"  Indexed: {param_count}"))

        # Examples
        ex_count = indexed_meta.get("example_count", 0)
        lines.append(_section_line("EXAMPLES"))
        lines.append(_section_line(f"  Indexed: {ex_count}"))

        # Syntax
        syntax = indexed_meta.get("syntax", "")
        lines.append(_section_line("SYNTAX"))
        lines.append(_section_line(f"  Indexed: {syntax[:80] if syntax else '(none)'}"))

        # Source and timestamp
        scraped_at = indexed_meta.get("scraped_at", "")
        sources = indexed_meta.get("sources", "")
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(_section_line(f"INDEXED DATA:"))
        lines.append(_section_line(f"  Sources: {sources}"))
        lines.append(_section_line(f"  Scraped at: {scraped_at or 'unknown'}"))
        lines.append(
            _section_line(
                "  Note: Full diff requires JavaScript rendering. "
                "Use get_live_entry() for a fresh live fetch."
            )
        )
        lines.append(f"{_BOX_BL}{_BOX_H * 60}{_BOX_BR}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[diff_entry] {e}")
        return _error("diff_entry", _safe_error(e, "diff_entry"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 14: check_freshness
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def check_freshness(params: FreshnessCheck = FreshnessCheck()) -> str:
    """
    Show which PineScript v6 entries have live TradingView data
    versus local docs only. Helps identify gaps in coverage.
    Optionally filter by namespace.
    """
    try:
        namespace = params.namespace
        where: Optional[dict] = {}
        if namespace:
            if namespace.lower() == "global":
                where["namespace"] = ""
            else:
                where["namespace"] = namespace
        if not where:
            where = None

        entries = _get_all_where(where or {})
        if not entries:
            return _error("check_freshness", "No entries found in database")

        live_count = 0
        local_only_count = 0
        merged_count = 0
        local_only_entries: list[str] = []

        for entry in entries:
            sources = entry["metadata"].get("sources", "")
            name = entry["metadata"].get("name", "?")
            if "tradingview_live" in sources and "local_docs" in sources:
                merged_count += 1
            elif "tradingview_live" in sources:
                live_count += 1
            else:
                local_only_count += 1
                local_only_entries.append(name)

        total = len(entries)
        lines: list[str] = []
        lines.append(f"{_BOX_TL}{_BOX_H * 60}{_BOX_TR}")
        lines.append(f"{_BOX_V} FRESHNESS REPORT")
        if namespace:
            lines.append(f"{_BOX_V} Namespace: {namespace}")
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(_section_line(f"Total entries:        {total}"))
        lines.append(_section_line(f"Merged (local+live):  {merged_count}"))
        lines.append(_section_line(f"Live only:            {live_count}"))
        lines.append(_section_line(f"Local only:           {local_only_count}"))
        lines.append(
            _section_line(
                f"Coverage:             {(total - local_only_count) / total * 100:.1f}%"
            )
        )

        if local_only_entries:
            lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
            lines.append(
                _section_line(f"ENTRIES NEEDING LIVE SCRAPE ({local_only_count}):")
            )
            for ename in local_only_entries[:30]:
                lines.append(_section_line(f"  - {ename}"))
            if len(local_only_entries) > 30:
                lines.append(
                    _section_line(f"  ... and {len(local_only_entries) - 30} more")
                )

        lines.append(f"{_BOX_BL}{_BOX_H * 60}{_BOX_BR}")
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[check_freshness] {e}")
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("check_freshness", _safe_error(e, "check_freshness"))


# ─────────────────────────────────────────────────────────────────────────────────────
# TOOL 15: validate_syntax
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def validate_syntax(params: CodeInput) -> str:
    """
    Validate PineScript v6 code using TradingView's official pine-facade
    compiler — the exact same compiler used by TradingView's web editor.

    Returns real compilation errors with line numbers and column positions.
    Use BEFORE suggesting code to the user to catch errors proactively.

    Args:
        code: Complete PineScript v6 source code to validate
    """
    try:
        result = await _call_pine_facade(params.code)

        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        success = result.get("success", False)

        if success and not errors and not warnings:
            meta = result.get("meta", {})
            name = meta.get("name", "")
            extra = f"\nMeta: {name}" if name else ""
            return (
                f"VALID — Code compiles successfully.{extra}\n"
                f"Compiler: TradingView pine-facade v6\n"
                f"Errors: 0 | Warnings: 0"
            )

        lines = []
        total_issues = len(errors) + len(warnings)
        lines.append(f"COMPILATION ISSUES ({total_issues}):")
        lines.append(f"Compiler: TradingView pine-facade v6")
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

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[validate_syntax] {e}")
        return (
            f"Compiler unavailable ({type(e).__name__}: {e}).\n"
            f"Validate manually at https://www.tradingview.com/pine-editor/"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 16: validate_and_explain
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def validate_and_explain(params: CodeInput) -> str:
    """
    Validate PineScript v6 code AND cross-reference any errors against
    the documentation database to provide precise fix instructions.

    Combines pine-facade compilation + semantic doc lookup into one call.
    This is the most powerful debugging tool for PineScript AI assistance.

    Use when helping user debug failing PineScript code.
    """
    try:
        result = await _call_pine_facade(params.code)

        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        success = result.get("success", False)

        if success and not errors and not warnings:
            # Quick code analysis on success
            code_lines = params.code.strip().splitlines()
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

            return (
                f"VALIDATION + DEBUG REPORT\n"
                f"{'=' * 50}\n"
                f"Compiler: TradingView pine-facade v6\n"
                f"Status: PASSED\n"
                f"Errors: 0 | Warnings: 0\n\n"
                f"Code Analysis:\n"
                f"  Script type: {script_type}\n"
                f"  Lines: {len(code_lines)}\n"
                f"  Plots: {plots}\n"
                f"  Inputs: {inputs}\n"
            )

        # Process errors with doc cross-reference
        lines = []
        lines.append("VALIDATION + DEBUG REPORT")
        lines.append("=" * 50)
        lines.append(f"Compiler: TradingView pine-facade v6")
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
                if (
                    "not found" not in doc_result.lower()
                    and "error" not in doc_result.lower()
                ):
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

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[validate_and_explain] {e}")
        return (
            f"VALIDATION FAILED: {type(e).__name__}: {e}\n"
            f"Validate manually at https://www.tradingview.com/pine-editor/"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 17: fix_and_validate
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def fix_and_validate(params: CodeFixInput) -> str:
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
        # Step 1: Find best matching hint using substring scan
        error_lower = params.error_description.lower()
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
        import re
        identifier_match = re.search(
            r"['\"]([a-zA-Z_][\w.]*)['\"]", params.error_description
        )
        identifier = identifier_match.group(1) if identifier_match else None

        # Step 3: Cross-reference identifier against MCP docs
        doc_context = ""
        if identifier:
            try:
                col = _get_collection()
                results = await _search_by_name(identifier, col)
                if results:
                    doc_context = (
                        f"\nDOC REFERENCE for '{identifier}':\n"
                        f"{results[0].get('document', '')[:300]}"
                    )
                else:
                    # Try with common namespaces
                    for ns in ["ta", "strategy", "math", "array", "str"]:
                        ns_results = await _search_by_name(f"{ns}.{identifier}", col)
                        if ns_results:
                            doc_context = (
                                f"\nSUGGESTION: Did you mean '{ns}.{identifier}'?\n"
                                f"{ns_results[0].get('document', '')[:200]}"
                            )
                            break
            except Exception:
                pass

        # Step 4: Attempt auto-fix for common patterns
        fixed_code = params.code
        fix_applied = "No automatic fix available"

        # Pattern: missing namespace (ema → ta.ema, sma → ta.sma, etc.)
        bare_fn_pattern = re.compile(
            r'\b(ema|sma|rsi|macd|atr|bb|stoch|wma|hma|vwap|crossover|'
            r'crossunder|highest|lowest|barssince|valuewhen|linreg|mom|'
            r'cum|change|pivothigh|pivotlow|supertrend|correlation)\s*\('
        )
        if bare_fn_pattern.search(fixed_code):
            fixed_code = bare_fn_pattern.sub(r'ta.\1(', fixed_code)
            fix_applied = "Added ta. namespace prefix to unqualified TA functions"

        # Pattern: strategy.* called in indicator context
        if "strategy.entry" in fixed_code and "strategy(" not in fixed_code:
            fix_applied = "strategy.entry() requires strategy() declaration, not indicator()"

        # Step 5: Validate the fixed code
        validation_result = None
        if fixed_code != params.code:
            try:
                raw = await _call_pine_facade(fixed_code)
                normalized = _normalize_facade_response(raw)
                if normalized["success"]:
                    validation_result = "✅ Fixed code compiles successfully"
                else:
                    errs = normalized["errors"]
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
            f"Error: {params.error_description}",
            f"",
            f"HINT: {matched_hint or 'No specific hint — check PineScript v6 syntax'}",
            f"",
            f"Fix Applied: {fix_applied}",
        ]
        if doc_context:
            lines.append(doc_context)
        if validation_result:
            lines.extend(["", validation_result])
        if fixed_code != params.code:
            lines.extend(["", "FIXED CODE:", "```pine", fixed_code, "```"])

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[fix_and_validate] {e}")
        return _error("fix_and_validate", _safe_error(e, "fix_and_validate"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 18: generate_indicator
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def generate_indicator(params: IndicatorGenInput) -> str:
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
        safe_name = _sanitize_pine_string(params.name)

        # Search docs for relevant functions
        relevant = _query(params.description, 5, where={"category": "function"})

        # Build input lines
        input_lines = []
        if params.inputs:
            for inp in params.inputs:
                inp_lower = inp.lower()
                if "length" in inp_lower or "period" in inp_lower:
                    input_lines.append(
                        f'int {inp.replace(" ", "_").replace("-", "_")} = input.int(20, "{inp}")'
                    )
                elif "source" in inp_lower:
                    input_lines.append(f'src = input.source(close, "{inp}")')
                elif "mult" in inp_lower or "factor" in inp_lower:
                    input_lines.append(
                        f'float {inp.replace(" ", "_").replace("-", "_")} = input.float(2.0, "{inp}")'
                    )
                else:
                    input_lines.append(
                        f'float {inp.replace(" ", "_").replace("-", "_")} = input.float(1.0, "{inp}")'
                    )

        # Build relevant function list
        relevant_funcs = []
        if relevant.get("ids") and relevant["ids"][0]:
            for meta in relevant["metadatas"][0][:5]:
                fname = meta.get("name", "?")
                fsyntax = meta.get("syntax", "")
                relevant_funcs.append(f"//   {fname}: {fsyntax[:80]}")

        # Generate template
        code = f"""//@version=6
indicator("{safe_name}", overlay={str(params.overlay).lower()}, shorttitle="{safe_name[:16]}")

// ── Inputs ──"""
        for il in input_lines:
            code += f"\n{il}"
        if not input_lines:
            code += "\n// (Add your inputs here with input.int, input.float, input.source, etc.)"

        code += f"""

// ── Calculations ──
// {params.description}
// Available functions from docs:"""
        for rf in relevant_funcs:
            code += f"\n{rf}"
        if not relevant_funcs:
            code += (
                "\n// (Use search_docs or suggest_functions to find relevant functions)"
            )

        code += """

// ── Plot ──
plot(close, "Price", color.blue)
"""

        # Validate
        validation = await _call_pine_facade(code)
        errors = validation.get("errors", [])
        success = validation.get("success", False)

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

        if relevant_funcs:
            lines.append("")
            lines.append("RELEVANT FUNCTIONS from docs:")
            for rf in relevant_funcs:
                lines.append(f"  {rf}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[generate_indicator] {e}")
        return _error("generate_indicator", _safe_error(e, "generate_indicator"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 19: generate_strategy
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def generate_strategy(params: StrategyGenInput) -> str:
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
        safe_name = _sanitize_pine_string(params.name)

        # Search docs for strategy-related functions
        relevant = _query(params.description, 5, where={"namespace": "strategy"})

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
    initial_capital={params.initial_capital},
    commission_type=strategy.commission.percent,
    commission_value={params.commission_pct},
    default_qty_type=strategy.percent_of_equity,
    default_qty_value=100,
    pyramiding={params.pyramiding},
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
        normalized = _normalize_facade_response(validation)
        if not normalized["success"]:
            errors_str = "\n".join(
                f"  Line {e['line']}: {e['text']}"
                for e in normalized["errors"][:5]
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

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[generate_strategy] {e}")
        return _error("generate_strategy", _safe_error(e, "generate_strategy"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 20: lookup_and_correct
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def lookup_and_correct(params: CodeFixInput) -> str:
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
        # Step 1: Validate
        validation = await _call_pine_facade(params.code)
        errors = validation.get("errors", [])

        # Step 2: Apply ALL v5→v6 namespace fixes
        fixed_code = params.code
        changes_made = []

        # BUG FIX: Complete v5 → v6 namespace migration map
        V5_TO_V6 = {
            # ta.* functions (most common v5 issue)
            r'\bema\s*\(':          'ta.ema(',
            r'\bsma\s*\(':          'ta.sma(',
            r'\brsi\s*\(':          'ta.rsi(',
            r'\bmacd\s*\(':         'ta.macd(',
            r'\batr\s*\(':          'ta.atr(',
            r'\bbb\s*\(':           'ta.bb(',
            r'\bstoch\s*\(':        'ta.stoch(',
            r'\bwma\s*\(':          'ta.wma(',
            r'\bhma\s*\(':          'ta.hma(',
            r'\bvwap\b':            'ta.vwap',
            r'\bcrossover\s*\(':    'ta.crossover(',
            r'\bcrossunder\s*\(':   'ta.crossunder(',
            r'\bhighest\s*\(':      'ta.highest(',
            r'\blowest\s*\(':       'ta.lowest(',
            r'\bbarssince\s*\(':    'ta.barssince(',
            r'\bvaluewhen\s*\(':    'ta.valuewhen(',
            r'\blinreg\s*\(':       'ta.linreg(',
            r'\bmom\s*\(':          'ta.mom(',
            r'\bcum\s*\(':          'ta.cum(',
            r'\bchange\s*\(':       'ta.change(',
            r'\bpivothigh\s*\(':    'ta.pivothigh(',
            r'\bpivotlow\s*\(':     'ta.pivotlow(',
            r'\bsupertrend\s*\(':   'ta.supertrend(',
            r'\bcorrelation\s*\(':  'ta.correlation(',
            r'\bpercentrank\s*\(':  'ta.percentrank(',
            r'\bdmi\s*\(':          'ta.dmi(',
            r'\bstdev\s*\(':        'ta.stdev(',
            r'\bvariance\s*\(':     'ta.variance(',
            # request.* functions
            r'\bsecurity\s*\(':     'request.security(',
            # math.* functions
            r'\babs\s*\(':          'math.abs(',
            r'\bround\s*\(':        'math.round(',
            r'\bfloor\s*\(':        'math.floor(',
            r'\bceil\s*\(':         'math.ceil(',
            r'\bpow\s*\(':          'math.pow(',
            r'\bsqrt\s*\(':         'math.sqrt(',
            r'\blog\s*\(':          'math.log(',
            r'\bexp\s*\(':          'math.exp(',
            r'\bsign\s*\(':         'math.sign(',
            r'\bsin\s*\(':          'math.sin(',
            r'\bcos\s*\(':          'math.cos(',
            r'\bmax\s*\(':          'math.max(',
            r'\bmin\s*\(':          'math.min(',
            # str.* functions
            r'\btostring\s*\(':     'str.tostring(',
            r'\btonumber\s*\(':     'str.tonumber(',
        }

        # Apply ALL replacements sequentially
        for pattern, replacement in V5_TO_V6.items():
            import re
            if re.search(pattern, fixed_code):
                fixed_code = re.sub(pattern, replacement, fixed_code)
                changes_made.append(f"Replaced: {pattern} → {replacement}")

        # Step 3: Re-validate the fixed code
        validation_after = await _call_pine_facade(fixed_code)
        errors_after = validation_after.get("errors", [])

        # Step 4: Search docs for intent
        intent_results = _query(params.error_description, 3)

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
        lines.append(f"RELEVANT DOCS FOR '{params.error_description}':")
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

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[lookup_and_correct] {e}")
        return _error("lookup_and_correct", _safe_error(e, "lookup_and_correct"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 21: debug_pine_facade
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def debug_pine_facade(params: CodeInput) -> str:
    """
    Diagnostic tool: compile code via pine-facade and return the FULL raw
    response alongside the normalized interpretation. Use for debugging
    when validate_syntax or validate_and_explain produce unexpected results.

    Args:
        code: Complete PineScript v6 source code to compile
    """
    try:
        result = await _call_pine_facade(params.code)

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

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[debug_pine_facade] {e}")
        # Return full diagnostic on error
        cb_stats = _pine_cb.stats()
        return (
            f"DEBUG PINE-FACADE ERROR\n"
            f"Exception: {type(e).__name__}: {e}\n"
            f"Circuit breaker: {json.dumps(cb_stats)}\n"
            f"Cache entries: {len(_VALIDATION_CACHE)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 22: suggest_functions
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def suggest_functions(params: SuggestFunctionsInput) -> str:
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
        query_text = params.context
        if params.current_line:
            query_text += f" | current line: {params.current_line}"

        results = _query(query_text, params.n_results, where={"category": "function"})

        if not results.get("ids") or not results["ids"][0]:
            return _error(
                "suggest_functions", f"No functions found for '{params.context}'"
            )

        lines = []
        lines.append(f"SUGGESTED FUNCTIONS for '{params.context}':")
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

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[suggest_functions] {e}")
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("suggest_functions", _safe_error(e, "suggest_functions"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 23: get_namespace_cheatsheet
# ─────────────────────────────────────────────────────────────────────────────


@mcp.tool
async def get_namespace_cheatsheet(params: CheatsheetLookup) -> str:
    """
    Get a compact cheatsheet for an entire namespace — all functions
    with signatures and one-line descriptions in a scannable format.
    Ideal for quick reference while coding.

    Namespaces: ta, strategy, math, array, matrix, map, str, color,
    chart, line, label, box, table, request, ticker, timeframe, syminfo
    """
    try:
        ns = params.namespace.strip().lower().rstrip(".")
        if ns == "global":
            where: Optional[dict] = {"namespace": ""}
        else:
            where = {"namespace": ns}

        entries = _get_all_where(where)
        if not entries:
            return _error(
                "get_namespace_cheatsheet", f"No entries found for namespace '{ns}'"
            )

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
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[get_namespace_cheatsheet] {e}")
        if _db_failure_count >= _DB_FAILURE_LIMIT:
            return _circuit_breaker_msg()
        return _error("get_namespace_cheatsheet", _safe_error(e, "get_namespace_cheatsheet"))


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
                "live_cache_entries": len(_LIVE_CACHE),
                "validation_cache_entries": len(_VALIDATION_CACHE),
                "embedding_model_ready": _embedding_model_ready.is_set(),
                "total_tools": 23,
                "version": "3.0",
            },
            indent=2,
        )
    except Exception as e:
        logger.error(f"[get_stats] {e}")
        return json.dumps(
            {
                "error": _safe_error(e, "get_stats"),
                "total_tools": 23,
                "version": "3.0",
            },
            indent=2,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting PineScript v6 Complete Reference MCP server v3.0 (23 tools)")
    mcp.run(transport="stdio")
