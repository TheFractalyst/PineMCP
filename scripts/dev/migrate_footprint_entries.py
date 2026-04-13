#!/usr/bin/env python3
"""
Add footprint and barstate entries to ChromaDB collection
"""

import chromadb
from chromadb.utils import embedding_functions

# Initialize ChromaDB
client = chromadb.PersistentClient(path="./pinescript_db")
col = client.get_collection("pinescript_v6")

# Footprint entries
FOOTPRINT_ENTRIES = [
{
  "id": "func_request_footprint",
  "name": "request.footprint",
  "namespace": "request",
  "type": "function",
  "source": "tradingview_live",
  "version": "v6",
  "document": """request.footprint(ticker, timeframe, expression, ...) → 
  Requests footprint (orderflow) data for a symbol.
  Returns data about buy/sell volume at each price level.
  
  Parameters:
    ticker      - Symbol ticker string
    timeframe   - Timeframe string (e.g., "1D")
    expression  - The footprint expression to evaluate
    gaps        - Optional: bool, whether to fill gaps
    lookahead   - Optional: barmerge.lookahead_on/off
    currency    - Optional: currency string
    ignore_invalid_symbol - Optional: bool

  Returns: Requested footprint value (type depends on expression)

  Common expressions used with request.footprint():
    footprint.buy_volume      - Buy volume at price level
    footprint.sell_volume     - Sell volume at price level  
    footprint.total_volume    - Total volume at price level
    footprint.vah             - Value area high
    footprint.val             - Value area low
    footprint.poc_price       - Point of control price
    footprint.poc_volume      - Point of control volume
    footprint.delta           - Buy vol - sell vol
    footprint.cumulative_delta - Cumulative delta

  Example:
    buyVol = request.footprint(syminfo.tickerid, "1D", 
                               footprint.buy_volume)
    sellVol = request.footprint(syminfo.tickerid, "1D", 
                                footprint.sell_volume)
    delta = request.footprint(syminfo.tickerid, "1D", 
                              footprint.delta)

  Notes:
    - Only works on charts that support footprint data
    - Requires Premium TradingView subscription
    - Returns na if footprint data not available for symbol"""
},
{
  "id": "type_volume_row",
  "name": "volume_row",
  "namespace": "footprint",
  "type": "type",
  "source": "tradingview_live",
  "version": "v6",
  "document": """volume_row — PineScript v6 built-in type

Represents a single price level row in a footprint chart.
Contains buy volume, sell volume, and derived metrics
for one price level within a bar.

Fields:
  buy_volume   (float) - Buy-initiated volume at this price level
  sell_volume  (float) - Sell-initiated volume at this price level
  total_volume (float) - Total volume (buy + sell) at this price level
  delta        (float) - Buy volume minus sell volume

Used with:
  request.footprint() to access per-row data
  footprint.* namespace constants

Example:
  //@version=6
  indicator("Footprint Delta")
  delta = request.footprint(syminfo.tickerid, timeframe.period,
                            footprint.delta)
  plot(delta, "Delta", delta > 0 ? color.green : color.red)"""
},
{
  "id": "const_footprint_buy_volume",
  "name": "footprint.buy_volume",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.buy_volume — series float
  Buy-side volume at the current price level in a footprint chart.
  Use as expression argument in request.footprint()."""
},
{
  "id": "const_footprint_sell_volume",
  "name": "footprint.sell_volume",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.sell_volume — series float
  Sell-side volume at the current price level in a footprint chart."""
},
{
  "id": "const_footprint_total_volume",
  "name": "footprint.total_volume",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.total_volume — series float
  Total volume (buy + sell) at the current price level."""
},
{
  "id": "const_footprint_delta",
  "name": "footprint.delta",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.delta — series float
  Delta = buy_volume - sell_volume at current price level.
  Positive = buying pressure, negative = selling pressure."""
},
{
  "id": "const_footprint_cumulative_delta",
  "name": "footprint.cumulative_delta",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.cumulative_delta — series float
  Running sum of delta across all price levels in the bar."""
},
{
  "id": "const_footprint_vah",
  "name": "footprint.vah",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.vah — series float
  Value Area High — upper price boundary of the value area
  (typically 70% of total volume). Used in volume profile analysis."""
},
{
  "id": "const_footprint_val",
  "name": "footprint.val",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.val — series float
  Value Area Low — lower price boundary of the value area."""
},
{
  "id": "const_footprint_poc_price",
  "name": "footprint.poc_price",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.poc_price — series float
  Point of Control price — price level with the highest traded volume
  in the bar. Key level in volume profile analysis."""
},
{
  "id": "const_footprint_poc_volume",
  "name": "footprint.poc_volume",
  "namespace": "footprint",
  "type": "constant",
  "source": "tradingview_live",
  "version": "v6",
  "document": """footprint.poc_volume — series float
  Volume at the Point of Control price level."""
},
]

# Barstate entries
BARSTATE_ENTRIES = [
{
  "id": "var_barstate_isconfirmed",
  "name": "barstate.isconfirmed",
  "namespace": "barstate",
  "type": "variable",
  "source": "tradingview_live",
  "version": "v6",
  "document": """barstate.isconfirmed — series bool

True when the script is executing on the bar's LAST (closing) tick.
This is the bar that will be committed to history.

Use this to avoid triggering strategy orders on unconfirmed bars:

  if barstate.isconfirmed
      strategy.entry("Long", strategy.long)

CRITICAL for strategies: without barstate.isconfirmed guard, entries
fire on every tick of the current bar (repainting).

Contrast with:
  barstate.isrealtime  - true on ALL ticks of the live bar
  barstate.islast      - true only on the very last bar on chart
  barstate.isnew       - true on the FIRST tick of a new bar"""
},
{
  "id": "var_barstate_isrealtime",
  "name": "barstate.isrealtime",
  "namespace": "barstate",
  "type": "variable",
  "source": "tradingview_live",
  "version": "v6",
  "document": """barstate.isrealtime — series bool

True when the script is executing on a real-time (live, unconfirmed) bar.
False on all historical bars.

Use to separate live logic from backtesting logic:

  if barstate.isrealtime
      // Only runs live, not in backtest
      alert("Price: " + str.tostring(close))"""
},
{
  "id": "var_barstate_islast",
  "name": "barstate.islast",
  "namespace": "barstate",
  "type": "variable",
  "source": "tradingview_live",
  "version": "v6",
  "document": """barstate.islast — series bool

True only on the LAST bar of the chart (the most recent bar).
Use for one-time calculations or drawing that should only happen once:

  if barstate.islast
      label.new(bar_index, high, "Current: " + str.tostring(close))

Note: On historical charts, this is the last loaded bar.
On live charts, barstate.islast and barstate.isrealtime are both true."""
},
{
  "id": "var_barstate_isfirst",
  "name": "barstate.isfirst",
  "namespace": "barstate",
  "type": "variable",
  "source": "tradingview_live",
  "version": "v6",
  "document": """barstate.isfirst — series bool

True only on the FIRST bar of the chart's history (bar_index == 0).
Use to initialize values that should only be set once:

  var float baseline = na
  if barstate.isfirst
      baseline := close"""
},
{
  "id": "var_barstate_isnew",
  "name": "barstate.isnew",
  "namespace": "barstate",
  "type": "variable",
  "source": "tradingview_live",
  "version": "v6",
  "document": """barstate.isnew — series bool

True on the FIRST execution of the current bar (opening tick).
On historical bars this is always true (one execution per bar).
On real-time bars this is true only on the first tick of the bar.

Use with varip to detect bar open:
  varip int tickCount = 0
  if barstate.isnew
      tickCount := 0
  tickCount += 1"""
},
{
  "id": "var_barstate_ishistory",
  "name": "barstate.ishistory",
  "namespace": "barstate",
  "type": "variable",
  "source": "tradingview_live",
  "version": "v6",
  "document": """barstate.ishistory — series bool

True when executing on historical (closed, confirmed) bars.
The logical opposite of barstate.isrealtime.

if barstate.ishistory
    // Backtesting / replay mode
if barstate.isrealtime
    // Live execution"""
},
]

# Drawing enum entries
DRAWING_ENUM_ENTRIES = [
# extend.*
{"id":"const_extend_none",  "name":"extend.none",
 "document":"extend.none — const string\nNo extension. Line/box drawn only between the two specified points.\nDefault for line.new() and box.new() extend parameter.\nExample: line.new(x1, y1, x2, y2, extend=extend.none)"},
{"id":"const_extend_right", "name":"extend.right",
 "document":"extend.right — const string\nExtend line/box to the right infinitely from its rightmost point.\nCommon use: support/resistance levels that should extend to the right.\nExample: line.new(bar_index[10], pivotHigh, bar_index, pivotHigh, extend=extend.right)"},
{"id":"const_extend_left",  "name":"extend.left",
 "document":"extend.left — const string\nExtend line/box to the left infinitely from its leftmost point."},
{"id":"const_extend_both",  "name":"extend.both",
 "document":"extend.both — const string\nExtend line/box infinitely in BOTH directions.\nUseful for drawing infinite horizontal or diagonal reference lines."},

# size.*
{"id":"const_size_tiny",   "name":"size.tiny",
 "document":"size.tiny — const string\nTiny text/shape size. Used in label.new() size parameter and plotshape() size parameter."},
{"id":"const_size_small",  "name":"size.small",
 "document":"size.small — const string\nSmall text/shape size."},
{"id":"const_size_normal", "name":"size.normal",
 "document":"size.normal — const string\nNormal/default text and shape size."},
{"id":"const_size_large",  "name":"size.large",
 "document":"size.large — const string\nLarge text/shape size."},
{"id":"const_size_huge",   "name":"size.huge",
 "document":"size.huge — const string\nHuge text/shape size."},
{"id":"const_size_auto",   "name":"size.auto",
 "document":"size.auto — const string\nAutomatic size — scales with chart zoom level."},

# text.*
{"id":"const_text_align_left",   "name":"text.align_left",
 "document":"text.align_left — const string\nLeft-align text within label. Used in label.new() textalign parameter."},
{"id":"const_text_align_right",  "name":"text.align_right",
 "document":"text.align_right — const string\nRight-align text within label."},
{"id":"const_text_align_center", "name":"text.align_center",
 "document":"text.align_center — const string\nCenter-align text within label."},
{"id":"const_text_wrap_auto",    "name":"text.wrap_auto",
 "document":"text.wrap_auto — const string\nAutomatic text wrapping in labels. Wraps at label boundary."},
{"id":"const_text_wrap_none",    "name":"text.wrap_none",
 "document":"text.wrap_none — const string\nNo text wrapping. Text extends past label boundary if needed."},

# label.style.*
{"id":"const_label_style_none",          "name":"label.style_none",
 "document":"label.style_none — const string\nInvisible label frame, text only. No bubble or arrow shape."},
{"id":"const_label_style_label_up",      "name":"label.style_label_up",
 "document":"label.style_label_up — const string\nLabel with arrow pointing upward (below the label)."},
{"id":"const_label_style_label_down",    "name":"label.style_label_down",
 "document":"label.style_label_down — const string\nLabel with arrow pointing downward (above the label)."},
{"id":"const_label_style_label_left",    "name":"label.style_label_left",
 "document":"label.style_label_left — const string\nLabel with arrow pointing left."},
{"id":"const_label_style_label_right",   "name":"label.style_label_right",
 "document":"label.style_label_right — const string\nLabel with arrow pointing right."},
{"id":"const_label_style_circle",        "name":"label.style_circle",
 "document":"label.style_circle — const string\nCircular label shape."},
{"id":"const_label_style_square",        "name":"label.style_square",
 "document":"label.style_square — const string\nSquare label shape."},
{"id":"const_label_style_diamond",       "name":"label.style_diamond",
 "document":"label.style_diamond — const string\nDiamond label shape."},
{"id":"const_label_style_triangleup",    "name":"label.style_triangleup",
 "document":"label.style_triangleup — const string\nTriangle pointing up."},
{"id":"const_label_style_triangledown",  "name":"label.style_triangledown",
 "document":"label.style_triangledown — const string\nTriangle pointing down."},
{"id":"const_label_style_arrowup",       "name":"label.style_arrowup",
 "document":"label.style_arrowup — const string\nArrow pointing up. Commonly used for buy signals."},
{"id":"const_label_style_arrowdown",     "name":"label.style_arrowdown",
 "document":"label.style_arrowdown — const string\nArrow pointing down. Commonly used for sell signals."},

# line.style.*
{"id":"const_line_style_solid",  "name":"line.style_solid",
 "document":"line.style_solid — const string\nSolid line. Default for line.new() style parameter."},
{"id":"const_line_style_dashed", "name":"line.style_dashed",
 "document":"line.style_dashed — const string\nDashed line style for line.new()."},
{"id":"const_line_style_dotted", "name":"line.style_dotted",
 "document":"line.style_dotted — const string\nDotted line style for line.new()."},
{"id":"const_line_style_arrow_left",  "name":"line.style_arrow_left",
 "document":"line.style_arrow_left — const string\nLine with arrowhead at left end."},
{"id":"const_line_style_arrow_right", "name":"line.style_arrow_right",
 "document":"line.style_arrow_right — const string\nLine with arrowhead at right end."},
{"id":"const_line_style_arrow_both",  "name":"line.style_arrow_both",
 "document":"line.style_arrow_both — const string\nLine with arrowheads at both ends."},
]

# Process drawing enums
for e in DRAWING_ENUM_ENTRIES:
    e.setdefault("namespace", e["name"].split(".")[0])
    e.setdefault("type", "constant")
    e.setdefault("source", "tradingview_live")
    e.setdefault("version", "v6")

# Upsert all entries
all_entries = FOOTPRINT_ENTRIES + BARSTATE_ENTRIES + DRAWING_ENUM_ENTRIES

col.upsert(
    ids=       [e["id"]       for e in all_entries],
    documents= [e["document"] for e in all_entries],
    metadatas= [{k:v for k,v in e.items() if k not in ("id","document")}
                for e in all_entries]
)

print(f"✅ Upserted {len(all_entries)} new entries:")
print(f"  - {len(FOOTPRINT_ENTRIES)} footprint entries")
print(f"  - {len(BARSTATE_ENTRIES)} barstate entries") 
print(f"  - {len(DRAWING_ENUM_ENTRIES)} drawing enum entries")
print(f"New DB total: {col.count()}")
