# Pine Script v6 MCP Server — Project Rules

## File Types
- `.ps` and `.pine` files are Pine Script v6 source code
- Always use the `pinescript-v6` MCP tools when working with these files

## Workflow Rules

### When reading a .ps/.pine file
- Automatically validate it with `validate_syntax` after reading
- If errors found, use `validate_and_explain` for diagnostics, then `get_function`/`get_variable` to look up correct syntax

### When editing a .ps/.pine file
- After EVERY edit to a Pine Script file, re-validate with `validate_syntax`
- If the edit changes function calls, verify the function signature with `get_function` BEFORE editing
- Never guess Pine Script syntax — always look it up via MCP tools

### When creating a new .ps/.pine file
- Use `generate_indicator` or `generate_strategy` for scaffolding
- Validate the scaffold before adding logic
- All inputs must use typed variants: `input.int()`, `input.float()`, etc.
- First line must be `//@version=6`

### When fixing errors
- Use `fix_and_validate` for automatic namespace fixes
- Use `lookup_and_correct` for v5 → v6 migration
- Use `validate_and_explain` for doc-cross-referenced error diagnosis
- If pine-facade is down (403/network), the local linter catches ~50% of common errors

## Available MCP Tools (all auto-approved)
- `get_function`, `get_variable`, `get_type`, `get_constant`, `get_keyword`, `get_operator` — lookup by exact name
- `search_docs`, `suggest_functions`, `get_examples` — semantic search
- `validate_syntax`, `validate_and_explain`, `fix_and_validate`, `lookup_and_correct` — validation pipeline
- `generate_indicator`, `generate_strategy` — code generation
- `debug_pine_facade` — raw compiler output
- `list_namespace`, `get_namespace_cheatsheet`, `search_by_return_type` — exploration

## Pine Script v6 Conventions
- All technical analysis functions require `ta.` prefix: `ta.ema()`, `ta.rsi()`, `ta.macd()`
- Math functions require `math.` prefix: `math.abs()`, `math.round()`, `math.max()`
- String functions require `str.` prefix: `str.tostring()`, `str.format()`
- `study()` is removed — use `indicator()`
- `security()` is removed — use `request.security()`
- `:=` for reassignment, `=` for initial declaration
- `var` for persistent variables (survive across bars)
- `varip` for persistent variables that update on each tick
