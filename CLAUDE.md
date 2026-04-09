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
- `.ps` and `.pine` are PineScript v6 files. MCP consultation and validation rules are in the project-level CLAUDE.md.
