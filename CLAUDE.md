# Claude Code — PineScript v6 Project Brain

## SECTION 1: Project Identity

This project contains PineScript v6 trading strategies and indicators developed for TradingView.

**The `pinescript-v6` MCP server is ALWAYS available and MUST be used for all Pine-related work.**

- Never rely on training knowledge for PineScript syntax — always query the MCP server first
- PineScript v6 has breaking changes from v5 — the MCP database is the authoritative source of truth
- The MCP server contains 1,647 indexed entries sourced from both local docs and live TradingView scrapes
- Every function call, variable, type, and constant must be verified via MCP before writing code
- pine-facade compile validation is the final gate — never show code that has not been validated

---

## SECTION 2: Mandatory MCP Usage Rules

These are hard rules. Every rule must be applied mechanically — no exceptions.

### RULE 1 — BEFORE writing any PineScript function call
Call `get_function("{function_name}")` first. Do not assume syntax from memory.

```
Example: before writing ta.ema(), call get_function("ta.ema")
Example: before writing strategy.entry(), call get_function("strategy.entry")
```

### RULE 2 — BEFORE writing any built-in variable
Call `get_variable("{variable_name}")` to confirm type and behavior.

```
Example: before using bar_index, call get_variable("bar_index")
Example: before using close, call get_variable("close")
```

### RULE 3 — AFTER writing any complete code block (5+ lines)
Call `validate_syntax(code)` using the MCP. Fix ALL errors before showing code to the user. Never show unvalidated PineScript code.

### RULE 4 — WHEN user reports a compile error
Call `validate_and_explain(code)` — NOT `validate_syntax`. This cross-references the error against the docs automatically, giving line-level explanations tied to the reference.

### RULE 5 — WHEN user asks "how do I..." about PineScript
Call `suggest_functions(context)` before answering. Base the answer on MCP results, not training data.

### RULE 6 — WHEN working with a namespace (ta.*, strategy.*, array.*, etc.)
Call `get_namespace_cheatsheet("{namespace}")` at session start for that namespace. Use it as the reference for the entire session.

```
Example: working on an RSI indicator → call get_namespace_cheatsheet("ta")
Example: working on a strategy → call get_namespace_cheatsheet("strategy")
```

### RULE 7 — WHEN user asks about a type (array, matrix, map, line, label, etc.)
Call `get_type("{type_name}")` for fields and methods. Do not guess method names — they differ between v5 and v6.

### RULE 8 — WHEN creating a new indicator from scratch
Use `generate_indicator()` to get a validated template first. Build on the template rather than writing from blank. The `.pine-template/indicator_base.pine` file in this repo is the canonical starting point.

### RULE 9 — WHEN creating a new strategy from scratch
Use `generate_strategy()` to get a validated template first. Always include `commission_type`, `default_qty_type`, `pyramiding`, and `calc_on_every_tick` in `strategy()`. The `.pine-template/strategy_base.pine` file is the canonical starting point.

### RULE 10 — WHEN user asks about a constant (color.*, strategy.*, shape.*, position.*, etc.)
Call `get_constant("{constant}")` — never hardcode constant values. Constants can change between versions.

### RULE 11 — WHEN user shares code with namespace errors (ema vs ta.ema, security vs request.security)
Call `lookup_and_correct(code, intent)` — it handles namespace prefix fixing specifically.

### RULE 12 — WHEN unsure which function to use
Call `search_docs(query)` with a natural language description. Present the top results to the user before writing code.

---

## SECTION 3: PineScript v6 Critical Rules

These are non-negotiable code quality rules for every `.pine` and `.ps` file.

### SYNTAX

- Always start with `//@version=6`
- `indicator()` requires: `title`, `overlay` (explicit, never omit), `shorttitle`
- `strategy()` requires: `title`, `overlay`, `initial_capital`, `commission_type`, `commission_value`, `default_qty_type`, `default_qty_value`, `pyramiding`, `calc_on_every_tick`
- Use `:=` for reassignment, `=` only for initialization
- `var` persists across bars (calculated once per new value); `varip` updates intra-bar
- All function calls must use full namespace: `ta.ema()` not `ema()`, `request.security()` not `security()`
- String concatenation uses `+` not `,`
- Conditional assignment: `x = condition ? a : b` (ternary, not `if/else` inline)

### TYPES

- Pine is strongly typed — verify types with `get_type()` before assignments
- `series` vs `simple` vs `const` vs `input` qualify matter — MCP returns this in parameter docs
- `na` checks: use `na(x)` not `x == na`
- `nz()` for null-safe access: `nz(x, 0)` — also `nz(x, x[1])` to carry forward last value
- Type casting: `int(x)`, `float(x)`, `str.tostring(x)`, `str.tofloat(x)`

### PERFORMANCE

- Never call `request.security()` inside loops — cache the result in a `var` variable outside
- Limit array operations in real-time bars — use `array.push`/`array.pop` pattern with bounded size
- Use `var` for values that persist — avoids recalculation every bar
- `barstate.isconfirmed` for strategy entries — prevents repainting on live bars
- Prefer `math.max()` / `math.min()` over inline ternaries in tight loops
- Do not use `label.new()` / `line.new()` / `box.new()` every bar without deleting — use `label.delete()` or limit with `var`

### STRATEGY QUALITY

- Always include commission in backtests: `commission_type=strategy.commission.percent`
- Set `default_qty_type=strategy.percent_of_equity` not fixed lots
- `calc_on_every_tick=false` unless you specifically need intra-bar fills
- Use `strategy.close_all()` on last bar: `if barstate.islast`
- Separate entry logic from position sizing logic — keep them in distinct blocks
- Add `input.bool("Enable Long", true)` and `input.bool("Enable Short", true)` inputs
- Use `strategy.position_size > 0` for long check, `< 0` for short check

### ANTI-PATTERNS — never write these

- `ema()` without `ta.` prefix
- `sma()` without `ta.` prefix
- `security()` without `request.` prefix
- `strategy.entry` without checking `barstate.isconfirmed`
- Hardcoded colors — use `input.color()` or `color.*` constants
- Magic numbers — all thresholds must be `input.*` variables
- Nested `request.security()` calls — cache in variables first
- `plot()` with hardcoded colors instead of `input.color()`
- `alertcondition()` with static string — use dynamic messages with `str.tostring()`
- `array.get()` without bounds checking (`array.size() > index`)

---

## SECTION 4: Workflow for Common Tasks

Follow these step-by-step workflows exactly. Do not skip steps.

### WORKFLOW A — User asks to create a new indicator

1. `search_docs(user's description)` → identify the most relevant functions
2. `get_namespace_cheatsheet("ta")` if technical analysis functions are involved
3. `generate_indicator(name, description, overlay)` → get MCP-generated template
4. `get_function()` for each function used in the template
5. Build on the template, filling in the user's specific logic with proper inputs
6. `validate_syntax(complete_code)` → fix any errors (repeat until 0 errors)
7. Return validated code + list of MCP sources used

### WORKFLOW B — User asks to create a new strategy

1. `search_docs(strategy description)` → identify entry/exit functions
2. `get_namespace_cheatsheet("strategy")` → full `strategy.*` reference
3. `generate_strategy(name, description, capital, commission)` → template
4. `get_function("strategy.entry")` + `get_function("strategy.close")`
5. `get_function("strategy.exit")` for take-profit/stop-loss wiring
6. Build complete strategy on the template with all required parameters
7. `validate_syntax(complete_code)` → fix all errors
8. Return validated code + note any backtest limitations (repainting, commission, slippage)

### WORKFLOW C — User shares broken or erroring code

1. `validate_and_explain(code)` → get errors + doc cross-reference
2. For each error: show the error message, show relevant docs excerpt, show the fix
3. Apply all fixes in one pass
4. `validate_syntax(fixed_code)` → confirm 0 errors
5. Return fixed code + summary of what was wrong and why

### WORKFLOW D — User asks to review or improve existing code

1. `validate_syntax(code)` → check for hidden errors first (report if found)
2. For each function used: `get_function(name)` → check if better alternatives exist or if parameters are suboptimal
3. Check for all anti-patterns listed in Section 3
4. Suggest improvements with MCP evidence (cite which tool returned the info)
5. Offer to rewrite with `validate_syntax` confirmation before returning

### WORKFLOW E — User asks "what function does X"

1. `suggest_functions(X)` → get candidates ranked by relevance
2. `get_function(top candidate)` → full syntax, parameters, return type
3. `get_examples(X)` → show real usage from TradingView docs
4. Answer with: function name, full syntax, parameter explanation, example

### WORKFLOW F — User opens existing .pine or .ps file for editing

1. Scan file for all function calls (`ta.*`, `strategy.*`, `request.*`, etc.)
2. Silently: `validate_syntax(full_file_content)` — do this before any editing
3. If errors found: report them immediately before proceeding with edits
4. Identify all namespaces in use → load cheatsheets for each namespace
5. Proceed with requested edits, using MCP for every function touched

---

## SECTION 5: Response Format for Pine Work

Every response involving PineScript code must include all of the following:

1. **The complete, validated code block** — no truncations, no placeholders
2. **Validation confirmation**: `Validated with pine-facade: ✅ 0 errors` (or note if pine-facade is offline and local validation was used)
3. **MCP sources used**: brief list of which tools were called and what they returned
4. **Warnings** (if applicable): repainting risk, commission not set, `calc_on_every_tick` impact, performance notes

Example footer format:
```
---
MCP sources used:
- get_function("ta.ema") → syntax confirmed: ta.ema(source, length) → series float
- get_function("strategy.entry") → barstate.isconfirmed guard required
- validate_syntax(code) → ✅ 0 errors, 0 warnings
```

---

## SECTION 6: MCP Tool Quick Reference

| Situation | Tool to call |
|-----------|-------------|
| Need function syntax | `get_function("name")` |
| Need variable info | `get_variable("name")` |
| Need type methods/fields | `get_type("name")` |
| Need constant value | `get_constant("name")` |
| Need keyword syntax | `get_keyword("name")` |
| Don't know which function | `suggest_functions("description")` |
| Need all ta.* functions | `get_namespace_cheatsheet("ta")` |
| Validating code | `validate_syntax(code)` |
| Debugging errors | `validate_and_explain(code)` |
| Fixing specific error | `fix_and_validate(code, error)` |
| New indicator template | `generate_indicator(name, desc, overlay)` |
| New strategy template | `generate_strategy(name, desc, capital)` |
| Wrong namespace (ema vs ta.ema) | `lookup_and_correct(code, intent)` |
| General search | `search_docs("natural language query")` |
| Find code examples | `get_examples("concept")` |
| Find by return type | `search_by_return_type("type")` |
| Check live docs freshness | `check_freshness("namespace")` |
| Debug pine-facade API | `debug_pine_facade(code)` |
| Get TradingView URL | `get_source_url("name")` |

---

## SECTION 7: File Conventions

- Pine Script files use `.pine` or `.ps` extension
- Always `//@version=6` — v5 syntax is incompatible
- Templates live in `.pine-template/` — always start from these
- Snippets in `.pine-template/snippets.pine` — copy-paste ready, pre-validated
- After MCP database update: `python merge_and_index.py --reset` then restart MCP

---

## SECTION 8: MCP Server Health

The MCP server exposes a `pinescript://stats` resource. Check it if behavior seems stale:

```
Expected: total_entries > 1500, pine_facade_circuit_open: false
```

If `pine_facade_circuit_open: true`, validation falls back to local syntax checking. Note this in responses.

To refresh: `python merge_and_index.py --reset` (re-merges local docs + live scrape, re-indexes ChromaDB).
