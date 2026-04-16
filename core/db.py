# PineScript-v6 MCP | © 2025-2026 @Fractalyst
"""
core/db.py
──────────────────────────────────────────────────────────────────────────────
ChromaDB collection management, circuit breaker, and query helpers.
- Lazy singleton initialization with double-check locking
- CircuitBreaker with configurable threshold + cooldown
- In-memory name index for O(1) exact lookups
- L1 query result cache (avoids re-embedding identical queries)
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Optional

import xxhash
from loguru import logger

if TYPE_CHECKING:
    import chromadb

from core.caches import (
    _QUERY_CACHE_LOCK,
    _QUERY_CACHE_MAX,
    _QUERY_CACHE_TTL,
    _QUERY_RESULT_CACHE,
)
from core.config import (
    COLLECTION,
    DB_PATH,
    EMBED_DIM,
    MAX_FUZZY_SCAN_ENTRIES,
    MAX_RESULTS,
)
from core.embeddings import get_model

# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB circuit breaker
# ─────────────────────────────────────────────────────────────────────────────


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
        # NOTE: Must remain synchronous — called from get_collection() which is sync
        self.failures += 1
        logger.warning(
            f"ChromaDB failure {self.failures}/{self.threshold}: {type(exc).__name__}"
        )
        if self.failures >= self.threshold:
            self.open_until = time.time() + self.cooldown
            logger.error(f"ChromaDB circuit OPEN — cooldown {self.cooldown}s")

    def record_success(self) -> None:
        if self.failures:
            self.failures = 0
            self.open_until = 0.0


_chroma_breaker = ChromaDBCircuitBreaker(threshold=3, cooldown=30)

# ─────────────────────────────────────────────────────────────────────────────
# Singletons + locks
# ─────────────────────────────────────────────────────────────────────────────

_collection = None
_db_init_lock = threading.Lock()

# Name index for O(1) exact lookups — built at startup
_name_index: dict[str, list[dict]] = {}
_name_index_built: bool = False

# Common PineScript parameter names that should NOT trigger doc lookups
_COMMON_PARAM_NAMES = frozenset(
    {
        "length",
        "len",
        "period",
        "source",
        "src",
        "mult",
        "multiplier",
        "factor",
        "offset",
        "basis",
        "dev",
        "deviation",
        "signal",
        "fast",
        "slow",
        "size",
        "threshold",
        "limit",
        "color",
        "title",
        "minval",
        "maxval",
        "step",
        "defval",
        "group",
        "inline",
        "confirm",
        "options",
        "tooltip",
        "bar_index",
        "gap",
        "style",
        "width",
        "transparency",
    }
)

# ─────────────────────────────────────────────────────────────────────────────
# Collection initialization
# ─────────────────────────────────────────────────────────────────────────────


def _reset_caches() -> None:
    """Reset name index and query caches after collection reconnects.

    Called when the collection is invalidated (stale UUID, re-index, etc.)
    so subsequent lookups rebuild from the fresh collection.
    """
    global _name_index, _name_index_built
    _name_index = {}
    _name_index_built = False

    # Clear L1 query result cache — entries reference the old collection
    with _QUERY_CACHE_LOCK:
        _QUERY_RESULT_CACHE.clear()

    # Hot cache entries (core.hot_cache) reference old collection IDs.
    # They'll still work for reads but should be rebuilt for consistency.
    # The hot cache has its own _hot_cache_built flag; we flip it here
    # so the next ensure_hot_cache() call rebuilds it.
    try:
        import core.hot_cache as _hc
        _hc._hot_cache_built = False
        _hc.HOT_CACHE.clear()
    except Exception:
        pass

    logger.info("Caches invalidated after collection reconnect")


def get_collection() -> chromadb.Collection:
    """Return the ChromaDB collection, initializing lazily. Circuit-breaker aware.

    Thread-safe: uses _db_init_lock to prevent concurrent initialization.
    Auto-recovers from stale UUID after database re-index: catches NotFoundError
    on the cached singleton and reconnects with a fresh PersistentClient.
    """
    global _collection
    if _chroma_breaker.is_open():
        raise RuntimeError(
            "ChromaDB circuit breaker is open (cooldown). Please wait and try again."
        )
    # Fast path: already initialized (no lock needed for read)
    if _collection is not None:
        try:
            # Health check: verify the cached collection still exists.
            # A database re-index creates a new collection UUID, so the
            # cached reference becomes stale and every call returns NotFoundError.
            # count() is lightweight and hits the HNSW index (already in RAM).
            _collection.count()
            _chroma_breaker.record_success()
            return _collection
        except Exception as e:
            err_name = type(e).__name__
            if "NotFound" in err_name or "not exist" in str(e).lower():
                logger.warning(
                    f"Stale ChromaDB collection detected ({err_name}), reconnecting"
                )
                _collection = None
                _reset_caches()
                # Fall through to re-initialize below
            else:
                # Unexpected error — don't retry, let circuit breaker handle it
                _chroma_breaker.record_failure(e)
                raise
    # Slow path: initialize under lock to prevent duplicate init
    with _db_init_lock:
        # Double-check after acquiring lock (another thread may have initialized)
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
                        query_embeddings=[[0.0] * EMBED_DIM],
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


# ─────────────────────────────────────────────────────────────────────────────
# Name index
# ─────────────────────────────────────────────────────────────────────────────


def build_name_index() -> None:
    """Build in-memory name->entry index for O(1) exact lookups."""
    global _name_index, _name_index_built
    if _name_index_built:
        return
    try:
        col = get_collection()
        total = col.count()
        result = col.get(include=["metadatas", "documents"], limit=total)
        for rid, meta, doc in zip(
            result["ids"], result["metadatas"], result["documents"]
        ):
            key = (meta.get("name") or "").lower().strip()
            if key:
                entry = {"id": rid, "metadata": meta, "document": doc}
                if key not in _name_index:
                    _name_index[key] = []
                _name_index[key].append(entry)
        _name_index_built = True
        logger.info(
            f"Name index built: {len(_name_index)} unique names from {total} entries"
        )
    except Exception as e:
        logger.error(f"Failed to build name index: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Query helpers
# ─────────────────────────────────────────────────────────────────────────────


def _query(query_text: str, n: int, where: Optional[dict] = None) -> dict:
    """Run a ChromaDB query with the local embedding model.

    L1 LRU cache on query results for sub-ms repeat lookups.
    Falls through to ChromaDB on cache miss.

    NOTE: This is a synchronous function. For use from async tool handlers,
    use `await query_async(...)` instead to avoid blocking the event loop.
    """
    # L1 cache: deterministic key from query text + n + where
    _cache_key = xxhash.xxh64(f"{query_text}|{n}|{where}".encode()).hexdigest()
    with _QUERY_CACHE_LOCK:
        if _cache_key in _QUERY_RESULT_CACHE:
            cached_result, cached_ts = _QUERY_RESULT_CACHE[_cache_key]
            if time.time() - cached_ts < _QUERY_CACHE_TTL:
                logger.debug(f"L1 cache hit: {query_text[:40]}")
                # Return deep copy to prevent callers from mutating cached data
                return {
                    "ids": [list(cached_result["ids"][0])] if cached_result.get("ids") else [[]],
                    "metadatas": [list(cached_result["metadatas"][0])] if cached_result.get("metadatas") else [[]],
                    "documents": [list(cached_result["documents"][0])] if cached_result.get("documents") else [[]],
                    "distances": [list(cached_result["distances"][0])] if cached_result.get("distances") else [[]],
                }
            else:
                del _QUERY_RESULT_CACHE[_cache_key]
    try:
        model = get_model()
        col = get_collection()
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


async def query_async(query_text: str, n: int, where: Optional[dict] = None) -> dict:
    """Async wrapper for _query() — runs in thread pool to avoid blocking event loop.

    Embedding model inference + ChromaDB query can take 10-200ms.
    Without this wrapper, every _query() call from async tool handlers
    blocks the entire event loop, preventing concurrent tool execution.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _query, query_text, n, where)


def get_by_id(entry_id: str) -> Optional[dict]:
    """Fetch a single entry by exact ID."""
    try:
        col = get_collection()
        result = col.get(ids=[entry_id], include=["documents", "metadatas"])
        if result["ids"]:
            return {
                "id": entry_id,
                "metadata": result["metadatas"][0],
                "document": result["documents"][0],
            }
        return None
    except Exception as e:
        logger.error(f"get_by_id({entry_id}) failed: {e}")
        return None


def search_by_name(name: str, where: Optional[dict] = None) -> list[tuple[float, dict]]:
    """Exact then fuzzy name lookup. Scans up to MAX_FUZZY_SCAN_ENTRIES for fuzzy match."""
    try:
        from rapidfuzz import fuzz

        col = get_collection()
        name_preserved = name.strip()
        name_lower = name.lower().strip()

        # Guard: reject empty/whitespace-only names to avoid returning all entries
        if not name_lower:
            return []

        # BUG FIX: If name contains ".", it's fully qualified — exact match only, no namespace fallback
        if "." in name_lower:
            # Fully qualified — exact match only, no namespace fuzzy fallback
            # Use $in to match both original case and lowercase (DB stores mixed case)
            name_variants = list({name_preserved, name_lower})
            try:
                exact = col.get(
                    where={"name": {"$in": name_variants}},
                    include=["metadatas", "documents"],
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
                logger.warning(f"Qualified lookup failed, falling through: {e}")
            # Try with type=function specifically
            try:
                if where:
                    existing_clauses = where.get("$and", [where]) if isinstance(where, dict) else [where]
                    typed_where = {"$and": [{"name": {"$in": name_variants}}] + list(existing_clauses)}
                else:
                    # Skip $and with single element — ChromaDB rejects it
                    # (already covered by the exact match above)
                    raise RuntimeError("skip")
                typed = col.get(
                    where=typed_where,
                    include=["metadatas", "documents"],
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
                logger.warning(f"Typed lookup failed, returning empty: {e}")
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
                            hits = [
                                h
                                for h in hits
                                if h["metadata"].get("category") == clause["category"]
                            ]
                if hits:
                    return [(100.0, h) for h in hits]

        # Strategy 1: exact metadata match (fast, uses ChromaDB index)
        # Use $in with both case variants since DB stores mixed-case names
        try:
            name_variants = list({name_preserved, name_lower})
            exact_where: dict = {"name": {"$in": name_variants}}
            if where:
                cat = where.get("category")
                if cat:
                    existing_clauses = where.get("$and", [where])
                    exact_where = {
                        "$and": [{"name": {"$in": name_variants}}, {"category": cat}] + [
                            c for c in existing_clauses if "category" not in c
                        ]
                    }
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
        logger.error(f"search_by_name({name}) failed: {e}")
        return []


def get_all_where(where: dict | None, limit: int | None = None) -> list[dict]:
    """Fetch all entries matching a where filter. Defaults to full collection."""
    try:
        col = get_collection()
        if limit is None:
            limit = col.count()
        # Handle empty where clause - ChromaDB doesn't accept {} as where
        if where:
            result = col.get(
                where=where, include=["metadatas", "documents"], limit=limit
            )
        else:
            result = col.get(include=["metadatas", "documents"], limit=limit)
        entries = []
        for rid, meta, doc in zip(
            result["ids"], result["metadatas"], result["documents"]
        ):
            entries.append({"id": rid, "metadata": meta, "document": doc})
        return entries
    except Exception as e:
        logger.error(f"get_all_where failed: {e}")
        return []


async def search_by_name_async(
    name: str, where: Optional[dict] = None
) -> list[tuple[float, dict]]:
    """Async wrapper for search_by_name — avoids blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, search_by_name, name, where)


async def get_all_where_async(
    where: dict | None, limit: int | None = None
) -> list[dict]:
    """Async wrapper for get_all_where — avoids blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_all_where, where, limit)


def get_by_names(names: list[str]) -> dict:
    """Synchronous: fetch entries by exact name list using $in filter. Returns ChromaDB get() result."""
    try:
        col = get_collection()
        return col.get(
            where={"name": {"$in": names}}, include=["metadatas", "documents"]
        )
    except Exception as e:
        logger.debug(f"get_by_names failed: {e}")
        return {"ids": [], "metadatas": [], "documents": []}


async def get_by_names_async(names: list[str]) -> dict:
    """Async wrapper for get_by_names — avoids blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_by_names, names)


def get_type_by_name(name: str) -> dict:
    """Synchronous: fetch type entries by name. Returns ChromaDB get() result."""
    try:
        col = get_collection()
        name_lower = name.lower().strip()
        return col.get(
            where={
                "$and": [
                    {"name": {"$in": [name_lower, f"type.{name_lower}"]}},
                    {"category": "type"},
                ]
            },
            include=["documents", "metadatas"],
        )
    except Exception as e:
        logger.debug(f"get_type_by_name failed: {e}")
        return {"ids": [], "metadatas": [], "documents": []}


async def get_type_by_name_async(name: str) -> dict:
    """Async wrapper for get_type_by_name — avoids blocking event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_type_by_name, name)
