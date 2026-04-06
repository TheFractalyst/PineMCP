# Pine Script v6 MCP Server — Project Rules

## File Types
- `.ps` and `.pine` → Pine Script v6. Always use `pinescript-v6` MCP tools.

## Built-in First (Non-Negotiable)
Before writing ANY calculation logic:
1. `suggest_functions(description)` — search for a built-in first
2. Check `ta.*` (60+), `math.*` (40+), `request.*`, `str.*` — TradingView ships everything
3. Only write custom logic when no built-in exists after search

Common traps: manual EMA loops → `ta.ema()`, hand-rolled RSI → `ta.rsi()`, crossover with history → `ta.crossover()`, cumulative sum → `ta.cum()`, multi-TF → `request.security()`.

## Workflow
- **Read .ps/.pine** → auto-validate with `validate_syntax`
- **Edit .ps/.pine** → re-validate after every edit
- **New .ps/.pine** → scaffold with `generate_indicator`/`generate_strategy`, then validate
- **Fix errors** → `validate_and_explain` for diagnostics, `get_function`/`get_variable` for correct syntax
- **If pine-facade is down** → local linter catches ~50% of errors automatically

## Pine Script v6 Quick Reference
- Namespaces: `ta.*`, `math.*`, `str.*`, `request.*`, `array.*`, `matrix.*`, `map.*`
- `indicator()` not `study()`, `request.security()` not `security()`
- `:=` for reassignment, `=` for declaration
- `var` persists across bars, `varip` persists + updates per tick
- First line: `//@version=6`
