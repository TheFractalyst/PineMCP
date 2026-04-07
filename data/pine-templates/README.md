# PineScript v6 Templates

This directory contains canonical, pre-validated starting points and snippets for PineScript v6 development using the `pinescript-v6` MCP server.

## Files Included

1. `indicator_base.pine`: Use this as the starting point for all new indicators. It contains a standard structure (Inputs, Calculations, Plot, Labels, Alerts) and uses standard v6 practices (e.g., `chart.point`).
2. `strategy_base.pine`: Use this as the starting point for all new strategies. It includes the mandatory strategy parameters (commission, percent of equity), entry conditions with `barstate.isconfirmed` guards, and robust position sizing and exit logic.
3. `snippets.pine`: A collection of 20 heavily validated, copy-paste ready PineScript snippets covering MAs, Oscillators, Arrays, HTF data fetching (`request.security`), visual drawings (`label.new`, `box.new`, `table.new`), and Custom Types (`type`, `method`).

## How to Use the Templates

When starting a new script or adding a feature, **do not start from a blank file or write entirely from memory.** 

- **Indicators**: Copy the contents of `indicator_base.pine` and build upon it.
- **Strategies**: Copy the contents of `strategy_base.pine` and build upon it. 
- **Specific Logic**: Check `snippets.pine` for the canonical way to implement common logic (like trailing stops, ATR position sizing, or drawing dynamic tables).

## MCP Tools Available

While editing these templates, Claude Code will automatically use the following tools:
- `get_function("name")` / `get_type("name")` / `get_variable("name")` to verify exact syntax.
- `validate_syntax(code)` to silently check for compilation errors before writing output.
- `validate_and_explain(code)` to fix any errors encountered during development.
- `suggest_functions("description")` if you are unsure how to approach a problem.
- `get_namespace_cheatsheet("ta")` to load available methods for a given namespace.

## Re-generating Templates

If the PineScript v6 language updates or the MCP database gets a fresh live scrape:
1. Run `python merge_and_index.py --reset` at the project root to fetch the latest docs.
2. Restart the MCP server.
3. Ask Claude to re-run `generate_indicator()` and `generate_strategy()`, then overwrite the files in this directory.
