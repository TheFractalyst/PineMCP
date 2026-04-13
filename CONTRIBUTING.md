# Contributing to PineScript MCP

Thanks for your interest. Contributions welcome.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,pipeline]"
```

## Development

```bash
make test          # Run 134 tests
make lint          # Ruff check (line-length 120)
make check         # Verify 20 tools + 1 resource registered
```

Run a single test file:
```bash
.venv/bin/python -m pytest tests/test_validation.py -v
```

## Code Style

- **Linter:** Ruff with `line-length = 120`, rules E/F/W/I
- **Types:** All public functions need return type annotations
- **Error handling:** `raise ToolError(safe_error(e, tool_name))` in tool catch blocks
- **Imports:** `from __future__ import annotations` in every file, absolute imports only

## Pull Requests

1. Fork the repo
2. Create a feature branch
3. Run `make lint && make test` before pushing
4. Open a PR with a clear description of what changed and why

## Project Structure

```
core/           Infrastructure (config, db, caches, embeddings, pine_facade)
tools/          20 MCP tools (lookup, search, validation, codegen, context)
formatters/     Response formatting (entries, errors)
templates/      Indicator stubs, v5 migration patterns
pipeline/       Data ingestion (scrape, parse, index, dedup)
tests/          pytest suite with session-scoped ChromaDB warmup
```
