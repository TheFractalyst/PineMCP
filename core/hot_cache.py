"""
core/hot_cache.py
------------------------------------------------------------------------------
In-memory hot cache for top-priority PineScript entries.
- Loaded at startup from priority namespaces + global variables
- O(1) lookups for the most-used functions/variables
- ~1028 entries at steady state (~5-10MB memory)
"""

from __future__ import annotations

import asyncio
import copy
import os
import threading
from typing import Optional

from loguru import logger

from core.db import get_collection

# -----------------------------------------------------------------------------
# Priority namespaces and globals
# -----------------------------------------------------------------------------

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
    "currency",
    "session",
    "polynomial",
    "linefill",
    "plot",
    "shape",
    "drawing",
    "footprint",
    "json",
    "concepts",
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
    # Core functions
    "indicator",
    "strategy.entry",
    "strategy.close",
    "strategy.exit",
    "plot",
    "plotshape",
    "plotchar",
    "plotcandle",
    "plotbar",
    "bgcolor",
    "fill",
    "hline",
    # Technical analysis
    "ta.sma",
    "ta.ema",
    "ta.wma",
    "ta.rma",
    "ta.rsi",
    "ta.macd",
    "ta.atr",
    "ta.adx",
    "ta.supertrend",
    "ta.crossover",
    "ta.crossunder",
    "ta.cross",
    "ta.highest",
    "ta.lowest",
    "ta.change",
    "ta.stdev",
    "ta.variance",
    "ta.correlation",
    "ta.bbands",
    "ta.stoch",
    "ta.mfi",
    "ta.vwap",
    # Array functions
    "array.new_float",
    "array.new_int",
    "array.new_bool",
    "array.new_string",
    "array.new_box",
    "array.new_label",
    "array.new_line",
    "array.from",
    "array.copy",
    "array.push",
    "array.pop",
    "array.get",
    "array.set",
    "array.size",
    "array.sort",
    "array.reverse",
    "array.clear",
    "array.fill",
    "array.includes",
    "array.indexof",
    "array.slice",
    "array.concat",
    "array.sum",
    "array.avg",
    "array.min",
    "array.max",
    "array.median",
    "array.stdev",
    "array.abs",
    # Math
    "math.abs",
    "math.max",
    "math.min",
    "math.round",
    "math.floor",
    "math.ceil",
    "math.sqrt",
    "math.pow",
    "math.log",
    "math.exp",
    "math.sin",
    "math.cos",
    "math.tan",
    "math.avg",
    "math.sum",
    "math.sign",
    # String
    "str.tostring",
    "str.tonumber",
    "str.format",
    "str.contains",
    "str.replace",
    "str.split",
    "str.length",
    "str.substring",
    "str.lower",
    "str.upper",
    "str.indexof",
    "str.lastindexof",
    # Input
    "input.int",
    "input.float",
    "input.bool",
    "input.string",
    "input.color",
    "input.source",
    "input.timeframe",
    "input.session",
    "input.symbol",
    "input.price",
    # Color
    "color.new",
    "color.rgb",
    "color.red",
    "color.green",
    "color.blue",
    "color.from_gradient",
    # Drawing
    "line.new",
    "line.set_xy1",
    "line.set_xy2",
    "line.set_color",
    "line.set_width",
    "line.set_style",
    "line.delete",
    "label.new",
    "label.set_text",
    "label.set_xy",
    "label.set_color",
    "label.delete",
    "box.new",
    "box.set_lefttop",
    "box.set_rightbottom",
    "box.set_color",
    "box.delete",
    "table.new",
    "table.cell",
    "table.set_bgcolor",
    "table.delete",
    # Matrix
    "matrix.new",
    "matrix.get",
    "matrix.set",
    "matrix.rows",
    "matrix.columns",
    "matrix.elements_count",
    "matrix.add",
    "matrix.remove",
    "matrix.fill",
    "matrix.copy",
    "matrix.transpose",
    "matrix.det",
    "matrix.inv",
    "matrix.mult",
    # Map
    "map.new",
    "map.put",
    "map.get",
    "map.contains",
    "map.remove",
    "map.size",
    "map.keys",
    "map.values",
    # Strategy
    "strategy.position_size",
    "strategy.position_avg_price",
    "strategy.equity",
    "strategy.profit",
    "strategy.loss",
    "strategy.netprofit",
    "strategy.grossprofit",
    "strategy.grossloss",
    "strategy.max_drawdown",
    "strategy.opentrades",
    "strategy.closedtrades",
    # Request
    "request.security",
    "request.security_lower_tf",
    "request.quandl",
    "request.financial",
    "request.economic",
    # Info
    "syminfo.tickerid",
    "syminfo.mintick",
    "syminfo.pointvalue",
    "syminfo.type",
    "syminfo.session",
    "syminfo.timezone",
    "timeframe.period",
    "timeframe.isdaily",
    "timeframe.isintraday",
    "timeframe.isweekly",
    "timeframe.ismonthly",
    # Types
    "int",
    "float",
    "bool",
    "string",
    "color",
    "line",
    "label",
    "box",
    "table",
    "array",
    "matrix",
    "map",
]

# -----------------------------------------------------------------------------
# Hot cache state
# -----------------------------------------------------------------------------

HOT_CACHE: dict[str, dict] = {}
_hot_cache_built: bool = False
_build_lock = asyncio.Lock()

_cache_hits: int = 0
_cache_misses: int = 0
_cache_counter_lock = threading.Lock()


async def build_hot_cache() -> bool:
    """Load priority entries into memory for sub-millisecond lookups. Returns True on success."""
    global _cache_hits, _cache_misses, _hot_cache_built
    if _hot_cache_built:
        return True
    async with _build_lock:
        if _hot_cache_built:
            return True  # Another coroutine built it while we waited
        logger.info("Building hot cache...")
        HOT_CACHE.clear()  # Prevent partial-build leakage from prior failed attempts
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
                        key = (meta.get("name") or "").lower().strip()
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
    """Check hot cache for exact key match. Returns a deep copy to prevent cache mutation."""
    global _cache_hits, _cache_misses
    key = name.lower().strip()
    entry = HOT_CACHE.get(key)
    if entry:
        with _cache_counter_lock:
            _cache_hits += 1
        return copy.deepcopy(entry)
    with _cache_counter_lock:
        _cache_misses += 1
    return None


async def ensure_hot_cache() -> None:
    """Build hot cache on first call if not already built."""
    await build_hot_cache()
