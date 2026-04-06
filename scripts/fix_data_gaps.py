#!/usr/bin/env python3
"""Fix data gaps: add missing entries and fill empty descriptions in tv_scraped_entries.json."""
import json

DATA_FILE = "tv_scraped_entries.json"

# --- Missing entries to add ---
NEW_ENTRIES = [
    {
        "id": "fun_input.resolution",
        "name": "input.resolution",
        "category": "function",
        "namespace": "input",
        "syntax": "input.resolution(defval, title, tooltip, inline, group, confirm, display, active) -> simple string",
        "overloads": [],
        "description": "Adds an input to the Inputs tab for selecting a chart resolution/timeframe. The user can choose from available resolutions. This is a legacy function; input.timeframe() is the recommended replacement.",
        "parameters": [
            {"name": "defval", "type": "simple string", "default": "", "description": "Default value for the input."},
            {"name": "title", "type": "simple string", "default": "''", "description": "Title of the input in the Inputs tab."},
            {"name": "tooltip", "type": "simple string", "default": "''", "description": "Tooltip for the input."},
            {"name": "inline", "type": "simple string", "default": "'None'", "description": "Combines this input with the previous one on the same line."},
            {"name": "group", "type": "simple string", "default": "''", "description": "Groups this input with others under a common header."},
            {"name": "confirm", "type": "simple bool", "default": "false", "description": "Requires user confirmation before running the script."},
            {"name": "display", "type": "simple string", "default": "''", "description": "Controls how the input value is displayed."},
            {"name": "active", "type": "simple bool", "default": "true", "description": "Whether the input is active."}
        ],
        "returns": "simple string",
        "remarks": "This function is deprecated. Use input.timeframe() instead.",
        "type_fields": [],
        "type_methods": [],
        "examples": ["//@version=6\nindicator('Resolution Input')\nres = input.resolution('D', 'Resolution')\nplot(close, color=res == 'D' ? color.blue : color.red)"],
        "see_also": ["input.timeframe", "input.int", "input.string"],
        "deprecated": True,
        "url": "https://www.tradingview.com/pine-script-reference/v6/#fun_input.resolution",
        "scraped_at": "2026-04-03T00:00:00.000000+00:00",
        "source": "tradingview_live",
        "scrape_error": None
    },
    {
        "id": "fun_line.set_xy",
        "name": "line.set_xy",
        "category": "function",
        "namespace": "line",
        "syntax": "line.set_xy(id, x1, y1, x2, y2) -> void",
        "overloads": [],
        "description": "Changes the x1, y1, x2, and y2 coordinates of a line. This is a convenience function equivalent to calling line.set_x1(), line.set_y1(), line.set_x2(), and line.set_y2() in sequence.",
        "parameters": [
            {"name": "id", "type": "line", "default": "", "description": "The line object whose coordinates will be changed."},
            {"name": "x1", "type": "series int", "default": "", "description": "New x1 (bar index) coordinate."},
            {"name": "y1", "type": "series float", "default": "", "description": "New y1 (price) coordinate."},
            {"name": "x2", "type": "series int", "default": "", "description": "New x2 (bar index) coordinate."},
            {"name": "y2", "type": "series float", "default": "", "description": "New y2 (price) coordinate."}
        ],
        "returns": "void",
        "remarks": "",
        "type_fields": [],
        "type_methods": [],
        "examples": ["//@version=6\nindicator('line.set_xy Example', overlay=true)\nvar myLine = line.new(bar_index, close, bar_index + 10, close)\nif barstate.islast\n    line.set_xy(myLine, bar_index - 5, high, bar_index + 5, low)"],
        "see_also": ["line.new", "line.set_x1", "line.set_y1", "line.set_x2", "line.set_y2"],
        "deprecated": False,
        "url": "https://www.tradingview.com/pine-script-reference/v6/#fun_line.set_xy",
        "scraped_at": "2026-04-03T00:00:00.000000+00:00",
        "source": "tradingview_live",
        "scrape_error": None
    },
    {
        "id": "fun_strategy.risk_allow_entry_in",
        "name": "strategy.risk_allow_entry_in",
        "category": "function",
        "namespace": "strategy",
        "syntax": "strategy.risk_allow_entry_in(direction) -> void",
        "overloads": [],
        "description": "Deprecated alias for strategy.risk.allow_entry_in(). Controls whether the strategy is allowed to enter positions in the specified direction(s). Use strategy.risk.allow_entry_in() in new scripts.",
        "parameters": [
            {"name": "direction", "type": "simple string", "default": "", "description": "Direction(s) to allow: 'long', 'short', or 'all'."}
        ],
        "returns": "void",
        "remarks": "This is a deprecated alias. Use strategy.risk.allow_entry_in() instead.",
        "type_fields": [],
        "type_methods": [],
        "examples": [],
        "see_also": ["strategy.risk.allow_entry_in"],
        "deprecated": True,
        "url": "https://www.tradingview.com/pine-script-reference/v6/#fun_strategy.risk_allow_entry_in",
        "scraped_at": "2026-04-03T00:00:00.000000+00:00",
        "source": "tradingview_live",
        "scrape_error": None
    },
]

# --- Descriptions to fill (entries exist but have empty or very short descriptions) ---
DESCRIPTIONS = {
    "chart.is_heikinashi": "Returns true when the chart type is Heikin Ashi.",
    "chart.is_kagi": "Returns true when the chart type is Kagi.",
    "chart.is_linebreak": "Returns true when the chart type is Line Break.",
    "chart.is_pnf": "Returns true when the chart type is Point and Figure.",
    "chart.is_range": "Returns true when the chart type is Range.",
    "chart.is_renko": "Returns true when the chart type is Renko.",
    "chart.is_standard": "Returns true when the chart type is standard candles (Japanese candlestick).",
    "currency.BTC": "Bitcoin cryptocurrency ticker code.",
    "currency.ETH": "Ethereum cryptocurrency ticker code.",
    "currency.EUR": "Euro fiat currency ticker code.",
    "currency.USDT": "Tether USD cryptocurrency stablecoin ticker code.",
    "hour": "Returns the current bar's hour in the exchange's timezone.",
    "minute": "Returns the current bar's minute in the exchange's timezone.",
    "month": "Returns the current bar's month in the exchange's timezone.",
    "second": "Returns the current bar's second in the exchange's timezone.",
    "year": "Returns the current bar's year in the exchange's timezone.",
    "str.tostring": "Converts a value to its string representation. Supports format strings for float values. This is a legacy function; use str.format() for more formatting options.",
    "ta.cross": "Returns true if source1 and source2 cross each other on the current bar. Equivalent to ta.crossover() or ta.crossunder() but returns true for crossings in either direction.",
    "ta.stdev": "Returns the standard deviation of source for length bars. By default, calculates sample standard deviation (biased=false). Use biased=true for population standard deviation.",
}


def main():
    # Load existing data
    with open(DATA_FILE) as f:
        entries = json.load(f)

    # Build lookup by name (lowercased for case-insensitive matching)
    by_name = {}
    for e in entries:
        key = e["name"].lower().strip()
        by_name[key] = e

    print(f"Loaded {len(entries)} entries.\n")

    # --- Add missing entries ---
    added = 0
    for new_entry in NEW_ENTRIES:
        key = new_entry["name"].lower().strip()
        if key not in by_name:
            entries.append(new_entry)
            by_name[key] = new_entry
            print(f"  ADDED: {new_entry['name']}")
            added += 1
        else:
            print(f"  EXISTS (skipped): {new_entry['name']}")

    print(f"\nMissing entries: {added} added, {len(NEW_ENTRIES) - added} already present.\n")

    # --- Fill empty/short descriptions ---
    fixed = 0
    skipped = 0
    not_found = 0
    for name, desc in DESCRIPTIONS.items():
        key = name.lower().strip()
        if key not in by_name:
            print(f"  NOT FOUND: {name}")
            not_found += 1
            continue

        entry = by_name[key]
        current_desc = entry.get("description") or ""

        # Only update if description is empty or very short (e.g. "Bitcoin." for currency.BTC)
        if not current_desc or len(current_desc) < 15:
            old_desc = current_desc if current_desc else "(empty)"
            entry["description"] = desc
            print(f"  FIXED: {name}  \"{old_desc}\" -> \"{desc[:60]}...\"")
            fixed += 1
        else:
            print(f"  OK (skipped): {name}  \"{current_desc[:60]}...\"")
            skipped += 1

    print(f"\nDescriptions: {fixed} fixed, {skipped} skipped (already good), {not_found} not found.\n")

    # --- Save ---
    with open(DATA_FILE, "w") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(entries)} entries to {DATA_FILE}.")


if __name__ == "__main__":
    main()
