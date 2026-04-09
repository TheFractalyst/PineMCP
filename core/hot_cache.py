"""
core/hot_cache.py
──────────────────────────────────────────────────────────────────────────────
In-memory hot cache for top-priority PineScript entries.
- Loaded at startup from priority namespaces + global variables
- O(1) lookups for the most-used functions/variables
- ~1028 entries at steady state (~5-10MB memory)
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from loguru import logger

from core.db import get_collection

# ─────────────────────────────────────────────────────────────────────────────
# Priority namespaces and globals
# ─────────────────────────────────────────────────────────────────────────────

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

# ─────────────────────────────────────────────────────────────────────────────
# Hot cache state
# ─────────────────────────────────────────────────────────────────────────────

HOT_CACHE: dict[str, dict] = {}
_hot_cache_built: bool = False

_cache_hits: int = 0
_cache_misses: int = 0
_cache_counter_lock = threading.Lock()


async def build_hot_cache() -> bool:
    """Load priority entries into memory for sub-millisecond lookups. Returns True on success."""
    global _cache_hits, _cache_misses, _hot_cache_built
    logger.info("Building hot cache...")
    try:
        col = get_collection()
        count = 0
        dupes = 0

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
                        if key in HOT_CACHE:
                            # Keep the entry with richer documentation
                            existing_doc = HOT_CACHE[key]["document"] or ""
                            if len(doc or "") > len(existing_doc):
                                HOT_CACHE[key] = {"id": rid, "document": doc, "metadata": meta}
                                dupes += 1
                        else:
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

        logger.info(
            f"Hot cache ready: {count} entries loaded, {len(HOT_CACHE)} unique keys ({dupes} key collisions)"
        )
        _hot_cache_built = True
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
        with _cache_counter_lock:
            _cache_hits += 1
        return entry
    # Try just the last part after a dot
    if "." in key:
        short = key.split(".")[-1]
        entry = HOT_CACHE.get(short)
        if entry:
            with _cache_counter_lock:
                _cache_hits += 1
            return entry
    with _cache_counter_lock:
        _cache_misses += 1
    return None


async def ensure_hot_cache():
    """Build hot cache on first call if not already built."""
    global _hot_cache_built
    if not _hot_cache_built:
        success = await build_hot_cache()
        if success:
            _hot_cache_built = True
