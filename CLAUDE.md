# PineScript v6 MCP Server — Project Rules

## Architecture

```
server.py          ← MCP entry point (FastMCP 3.0 + FileSystemProvider)
core/              ← Infrastructure: ChromaDB, embeddings, caches, pine-facade, hot cache
formatters/        ← Pure functions: entry formatting, error utilities
templates/         ← Data: indicator templates, v5→v6 migration map
tools/             ← 20 @tool + 1 @resource (auto-discovered by FileSystemProvider)
  lookup.py        6 lookup tools: get_function/variable/type/constant/keyword/operator
  search.py        4 search tools: search_docs, get_examples, search_by_return_type, list_namespace
  validation.py    5 validation tools: validate_syntax/and_explain, fix_and_validate, debug/validate_file
  codegen.py       3 codegen tools: generate_indicator/strategy, lookup_and_correct
  context.py       2 context tools: suggest_functions, get_namespace_cheatsheet
  resources/stats.py  pinescript://stats resource
tests/             ← 134 pytest tests
pipeline/          ← Data pipeline: discover → scrape → merge+index
data/              ← Large data files + source docs (pinescriptv6/)
```

## Dev Commands
- `make test` — run 134 tests
- `make serve` — start MCP server
- `make check` — verify 20 tools + 1 resource registered
- `make lint` — ruff lint source packages
- `make index` — re-index ChromaDB (skip scraping)

## File Types
- `.ps` and `.pine` → Pine Script v6. Always use `pinescript-v6` MCP tools.

## Built-in First (Non-Negotiable)
Before writing ANY calculation logic:
1. `suggest_functions(description)` — search for a built-in first
2. Check `ta.*` (60+), `math.*` (40+), `request.*`, `str.*` — TradingView ships everything
3. Only write custom logic when no built-in exists after search

Common traps: manual EMA loops → `ta.ema()`, hand-rolled RSI → `ta.rsi()`, crossover with history → `ta.crossover()`, cumulative sum → `ta.cum()`, multi-TF → `request.security()`.

## MCP Consultation (Non-Negotiable)
When working on PineScript files, you MUST consult the MCP before writing code.
Do NOT rely on training data — PineScript v6 has breaking changes from v5 and
subtle type system rules that training data gets wrong.

1. **Before using any function**: `get_function(name)` — verify exact params, types, return type
2. **Before using any variable/constant**: `get_variable(name)` or `get_constant(name)`
3. **Before using a type**: `get_type(name)` — get all fields, methods, constructors
4. **When unsure what exists**: `suggest_functions(description)` or `search_docs(query)`
5. **When exploring a namespace**: `get_namespace_cheatsheet(namespace)` for quick scan
6. **After every edit**: `validate_syntax(code)` — confirm it compiles
7. **On compilation error**: `validate_and_explain(code)` — get error + doc-referenced fix

The MCP has 3,400+ entries. Use it liberally — it costs nothing and prevents bugs.

## Workflow
- **Read .ps/.pine** → auto-validate with `validate_syntax`
- **Edit .ps/.pine** → re-validate after every edit
- **New .ps/.pine** → scaffold with `generate_indicator`/`generate_strategy`, then validate
- **Fix errors** → `validate_and_explain` for diagnostics, `get_function`/`get_variable` for correct syntax
- **If pine-facade is down** → local linter catches ~50% of errors automatically
- **Large files** → use `validate_file(file_path="/absolute/path.ps")` — no size limits

## Pine Script v6 Quick Reference
- Namespaces: `ta.*`, `math.*`, `str.*`, `request.*`, `array.*`, `matrix.*`, `map.*`
- `indicator()` not `study()`, `request.security()` not `security()`
- `:=` for reassignment, `=` for declaration
- `var` persists across bars, `varip` persists + updates per tick
- First line: `//@version=6`
- Booleans cannot be `na` in v6 — use `int` or explicit comparison
- `transp=` removed — use `color.new(color, transparency)` instead
- `when=` removed from `strategy.entry/exit` — use `if` block
