# PineScript v6 Complete Reference — MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)
[![MCP Server](https://img.shields.io/badge/MCP-20%20Tools-green.svg)](tools/)
[![Tests](https://img.shields.io/badge/Tests-134%20passing-brightgreen.svg)](tests/)

Production MCP server providing the complete PineScript v6 reference via **20 tools + 1 resource** backed by a local ChromaDB vector store, TradingView's official `pine-facade` compiler, and a memory-first hot cache.

## What It Does

An AI coding assistant (Claude, Cursor, Windsurf, etc.) calls these tools to:
- Look up any PineScript v6 function, type, variable, constant, keyword, or operator
- Search documentation semantically
- Validate code against TradingView's real compiler
- Generate scaffolded indicators/strategies that compile
- Get namespace cheatsheets and function suggestions

**Data sources merged into one ChromaDB index:**
- Local parsed documentation (PDF/Markdown) — ~1,242 entries
- Live TradingView reference (scraped) — ~1,360 entries
- Merged unique entries — ~3,686 total (per pinescript://stats resource)

## Architecture

```
server.py              ← SINGLE entry point (FastMCP 3.0 + FileSystemProvider)
pine_linter.py         ← Standalone linter (imported by core/pine_facade.py)
Makefile               ← make test/serve/index/lint/bench/check
CLAUDE.md              ← Agent rules + architecture map
README.md              ← 20 tools + 1 resource, server.py entry point
requirements.txt
run.sh                 ← Pipeline orchestrator
config.json            ← IDE registration configs (all point to server.py)
core/                  ← Infrastructure
  config.py, caches.py, embeddings.py, db.py, pine_facade.py, hot_cache.py
formatters/            ← Pure functions
  entry.py, errors.py
templates/             ← PineScript templates + v5→v6 migration
  indicators.py, v5_migration.py
tools/                 ← 20 @tool + 1 resource (FileSystemProvider auto-discovers)
  lookup.py          ← 6 lookup tools
  search.py          ← 4 search tools
  validation.py      ← 5 validation tools
  codegen.py         ← 3 codegen tools
  context.py         ← 2 context tools
  resources/stats.py # pinescript://stats resource
tests/                 ← 134 pytest tests (import directly from modules)
pipeline/              ← Data pipeline: discover → scrape → merge+index
data/                  ← Large data files + pinescriptv6/ source docs
scripts/bench/         ← Benchmarks
docs/                  ← Historical/operational docs
scripts/               ← 31+ maintenance/utility scripts
```

**Data flow:**
```
PDF/MD docs ──→ pipeline/parse_docs.py ──→ data/pinescript_chunks.json ──┐
                                                                          ├→ pipeline/merge_and_index.py ──→ ChromaDB ──→ server.py ──→ AI
TradingView ──→ pipeline/discover_entries.py ──→ pipeline/scrape_entries.py ──┘

pine-facade.tradingview.com ──→ compile endpoint ──→ validation tools ──→ AI
```

**Startup sequence** (composable lifespans from server.py):
1. `db_lifespan` — Open ChromaDB collection + build name index
2. `model_lifespan` — Load SentenceTransformer model (thread pool) + warmup inference
3. `cache_lifespan` — Build hot cache for popular namespaces (ta, math, str, etc.)

**Three-tier caching:**
- MCP-level middleware: `ResponseCachingMiddleware` (1h TTL for lookups, 5m TTL for search)
- Internal LRU caches: `core/caches.py` (validation results, search results)
- Hot cache: Pre-loaded namespace entries for instant `get_function("ta.ema")` etc.

**Middleware stack:**
- ResponseCachingMiddleware (lookup 1h, search 5m)
- ResponseLimitingMiddleware (max response size)
- DetailedTimingMiddleware (DEBUG mode only)

## Standalone Linter

`pine_linter.py` is a standalone PineScript syntax checker that can be used independently of the MCP server:

```bash
python pine_linter.py /path/to/script.ps
```

It provides fast local validation with basic error detection and is imported by `core/pine_facade.py` as a fallback when the remote TradingView compiler is unavailable or for large files.

## Quick Start

```bash
# Clone
git clone https://github.com/TheFractalyst/pinescript-mcp.git
cd pinescript-mcp

# Full setup (venv + deps + Playwright + scrape + index)
chmod +x run.sh
./run.sh

# Or step by step:
make install          # venv + pip install (uses pyproject.toml)
make index            # build ChromaDB index from tracked data files
make index-full       # full re-scrape from TradingView + re-index
make test             # run 134 tests
make serve            # start MCP server (stdio)
make check            # verify 20 tools + 1 resource registered
make lint             # ruff lint
make bench            # benchmark suite
```

**After `make index`, the `pinescript_db/` directory (~82MB) is generated locally.** It is gitignored — rebuild anytime from tracked data in `data/`.

### Pre-built Database (Optional)

Download a pre-built database from [GitHub Releases](https://github.com/TheFractalyst/pinescript-mcp/releases) to skip indexing:

```bash
curl -L https://github.com/TheFractalyst/pinescript-mcp/releases/latest/download/pinescript_db.tar.gz | tar xz
```

## IDE Configuration

All configs use the same pattern — replace `/ABSOLUTE/PATH/TO` with your clone path.

### Claude Code
Create `.mcp.json` in your project root:
```json
{
  "mcpServers": {
    "pinescript-v6": {
      "command": "/ABSOLUTE/PATH/TO/.venv/bin/python",
      "args": ["/ABSOLUTE/PATH/TO/server.py"]
    }
  }
}
```

### Claude Desktop
Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) under `mcpServers`.

### Cursor / Windsurf / OpenCode
See `config.json` in this repo for complete configs for each IDE.

## Tools Reference (20 tools + 1 resource)

### Lookup (6) — `tools/lookup.py`
| Tool | Description |
|------|-------------|
| `get_function(name)` | Full docs: syntax, all overloads, params, examples |
| `get_variable(name)` | Built-in variable description and behavior |
| `get_type(name)` | Type definition, fields, methods |
| `get_constant(name)` | Constant value and usage |
| `get_keyword(name)` | Keyword syntax and examples |
| `get_operator(name)` | Operator description and examples |

### Search (4) — `tools/search.py`
| Tool | Description |
|------|-------------|
| `search_docs(query)` | Semantic search across all entries |
| `get_examples(query)` | Find working code examples by concept |
| `search_by_return_type(type)` | Find all functions returning a given type |
| `list_namespace(namespace)` | All members of a namespace (e.g. `ta`, `math`) |

### Validation (5) — `tools/validation.py`
| Tool | Description |
|------|-------------|
| `validate_syntax(code)` | Compile check via TradingView's pine-facade |
| `validate_and_explain(code)` | Validate + cross-reference errors against docs for fixes |
| `fix_and_validate(code, error)` | Look up doc-referenced fixes, re-validate |
| `debug_pine_facade(code)` | Raw compiler diagnostics |
| `validate_file(file_path)` | Validate by file path (no size limits) |

### Code Generation (3) — `tools/codegen.py`
| Tool | Description |
|------|-------------|
| `generate_indicator(name, ...)` | Scaffold a validated indicator template |
| `generate_strategy(name, ...)` | Scaffold a validated strategy template |
| `lookup_and_correct(code, intent)` | Validate + search docs + explain fixes |

### Context (2) — `tools/context.py`
| Tool | Description |
|------|-------------|
| `suggest_functions(context, ...)` | Suggest relevant functions with signatures |
| `get_namespace_cheatsheet(namespace)` | Compact reference for an entire namespace |

### Resource (1) — `tools/resources/stats.py`
| Resource | Description |
|----------|-------------|
| `pinescript://stats` | Database stats: entry count, collection info |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PINESCRIPT_DB_PATH` | `./pinescript_db` | ChromaDB database path |
| `PINESCRIPT_COLLECTION` | `pinescript_v6` | ChromaDB collection name |
| `PINESCRIPT_EMBED_MODEL` | `all-MiniLM-L6-v2` | SentenceTransformer model |
| `PINESCRIPT_MAX_RESULTS` | `30` | Max results per search query |
| `SCRAPE_DELAY_MS` | `1500` | Delay between scrape requests (ms) |
| `SCRAPE_WORKERS` | `2` | Concurrent scrape workers |
| `LOG_LEVEL` | `INFO` | Loguru log level |
| `PINE_FACADE_TIMEOUT` | `20` | Pine-facade request timeout (seconds) |
| `VALIDATION_CACHE_TTL` | `300` | Validation cache TTL (seconds) |
| `HOT_CACHE_NAMESPACES` | `ta,strategy,...` | Comma-separated namespaces for hot cache |

## Pipeline Scripts

```bash
# Stage 1: Discover entries from TradingView
python pipeline/discover_entries.py [--output FILE] [--headful] [--debug]

# Stage 2: Scrape entry content
python pipeline/scrape_entries.py [--index FILE] [--output FILE] [--headful] [--debug]
python pipeline/scrape_entries.py --entry fun_ta.ema      # single entry
python pipeline/scrape_entries.py --retry-failed           # retry failures

# Stage 3: Merge local + scraped data and index into ChromaDB
python pipeline/merge_and_index.py [--local FILE] [--live FILE] [--db PATH] [--reset] [--dry-run]
```

## Updating Documentation

```bash
./run.sh --rescrape --reset-db           # Full re-scrape from TradingView + rebuild index
python pipeline/merge_and_index.py --reset  # Quick re-index from existing data
./run.sh --entry=fun_ta.ema                 # Scrape + index a single entry
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "ChromaDB has failed too many times" | Run `./run.sh` to rebuild the database |
| "No module named 'chromadb'" | `source .venv/bin/activate` then retry |
| "Playwright install failed" | `python -m playwright install chromium` |
| Low entry count (< 850) | TradingView may have changed page structure. Use `--headful --debug` |
| Scrape timeout | Increase `SCRAPE_DELAY_MS` to 3000 |
| Server won't start | Verify `pinescript_db/` exists. Run `python pipeline/merge_and_index.py` |
| "Entry not found" | Use `search_docs()` for fuzzy matching |
| Pine-facade unavailable | Circuit breaker activates after 5 failures, auto-retries after 2 min |

## Dependencies

```
fastmcp>=3.0.0
chromadb>=0.5.0
sentence-transformers>=3.0.0
pydantic>=2.0.0
python-dotenv>=1.0.0
loguru>=0.7.0
playwright>=1.40.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
httpx>=0.27.0
tenacity>=8.2.0
fake-useragent>=1.5.0
aiofiles>=23.2.0
tqdm>=4.66.0
rapidfuzz>=3.0.0
xxhash>=3.0.0
```

## Disk Usage

| What | Size | In Git? |
|------|------|---------|
| Source code (183 files) | ~13 MB | Yes |
| `data/` (source JSON for indexing) | ~12 MB | Partially |
| `pinescript_db/` (ChromaDB index) | ~82 MB | No — rebuild with `make index` |
| `.git/` | ~2 MB | N/A |
| `.venv/` | ~2 GB | No (gitignored) |

**Total clone from GitHub: ~15 MB. Local after setup + indexing: ~2 GB.**

## License

[MIT](LICENSE)
