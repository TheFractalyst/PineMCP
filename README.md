# PineScript v6 Complete Reference — MCP Server

Production-grade MCP (Model Context Protocol) server providing the complete PineScript v6 reference documentation via **20 specialized tools** backed by a local ChromaDB vector store, **TradingView's official pine-facade compiler**, and a memory-first hot cache.

Combines two data sources into one unified knowledge base:
1. **Local parsed documentation** — 1,242 entries from PDF/Markdown
2. **Live TradingView reference** — ~1,360 entries scraped from the official site
3. **Merged total** — ~1,400–1,600 unique entries

## Architecture

```
  server.py                ← FastMCP 3.0 entry point (FileSystemProvider + composable lifespans)
  core/                    ← ChromaDB, embeddings, caches, pine-facade, hot cache
  formatters/              ← Pure formatting functions (entry detail, errors, box drawing)
  templates/               ← Indicator templates, v5→v6 migration map
  tools/                   ← 20 @tool + 1 @resource (auto-discovered)
  pipeline/                ← Data pipeline: discover → scrape → merge+index
  data/                    ← ChromaDB source data, pinescriptv6/ docs
```

**Data pipeline:**
```
  PDF docs ──→ pipeline/parse_docs.py ──→ data/pinescript_chunks.json ──┐
                                                                         ├→ pipeline/merge_and_index.py ──→ ChromaDB ──→ server.py ──→ AI
  TradingView ──→ pipeline/discover_entries.py ──→ pipeline/scrape_entries.py ──┘

  pine-facade.tradingview.com ──→ compile endpoint ──→ validate_syntax / validate_and_explain ──→ AI
```

**3-stage pipeline:** discover → scrape → merge+index
**Compiler integration:** pine-facade for 100% accurate validation

## Quick Start

```bash
make install                # Setup venv + deps
make index                  # Re-index ChromaDB (skip scraping)
make test                   # Run 134 tests
make serve                  # Start MCP server
make check                  # Verify 20 tools + 1 resource

# Or use run.sh directly:
./run.sh                    # Full pipeline
./run.sh --skip-scrape      # Skip scraping, just merge+index
./run.sh --rescrape         # Force re-scrape
```

## Tools (20 total)

### Lookup Tools (6) — `tools/lookup.py`
| Tool | Description |
|------|-------------|
| `get_function(name)` | Full docs: syntax, all overloads, params, examples |
| `get_variable(name)` | Built-in variable description and behavior |
| `get_type(name)` | Type definition, fields, methods |
| `get_constant(name)` | Constant value and usage |
| `get_keyword(name)` | Keyword syntax and examples |
| `get_operator(name)` | Operator description and examples |

### Search Tools (4) — `tools/search.py`
| Tool | Description |
|------|-------------|
| `search_docs(query)` | Semantic search across everything |
| `get_examples(query)` | Find real working code by concept |
| `search_by_return_type(type)` | Find functions returning a type |
| `list_namespace(namespace)` | All members of a namespace |

### Validation Tools (5) — `tools/validation.py`
| Tool | Description |
|------|-------------|
| `validate_syntax(code)` | Validate code using TradingView's official compiler |
| `validate_and_explain(code)` | Validate + cross-reference errors against docs |
| `fix_and_validate(code, error)` | Look up fixes from docs, show validation |
| `debug_pine_facade(code)` | Raw compiler diagnostics for debugging |
| `validate_file(file_path)` | Validate by file path (no size limits) |

### Code Generation Tools (3) — `tools/codegen.py`
| Tool | Description |
|------|-------------|
| `generate_indicator(name, ...)` | Generate validated indicator template |
| `generate_strategy(name, ...)` | Generate validated strategy template |
| `lookup_and_correct(code, intent)` | Validate + search docs + explain fixes |

### Smart Context Tools (2) — `tools/context.py`
| Tool | Description |
|------|-------------|
| `suggest_functions(context, ...)` | Suggest relevant functions with signatures |
| `get_namespace_cheatsheet(namespace)` | Compact cheatsheet for an entire namespace |

## Competitive Comparison

| Feature | This MCP | iamrichardD | erevus | pinescript-mcp |
|---------|----------|-------------|--------|----------------|
| Doc entries | ~1,600 | 884 | 0 | ~200 |
| Semantic search | Yes | Yes | No | No |
| Live TradingView scraping | Yes | No | No | No |
| pine-facade validation | Yes | No | Yes | No |
| Validation + docs combined | Yes | No | No | No |
| Code generation (validated) | Yes | No | No | No |
| Memory-first cache | Yes | Yes | No | No |
| Namespace cheatsheets | Yes | No | No | No |
| Overload tracking | Yes | No | No | No |
| Total MCP tools | **20** | ~5 | 1 | ~3 |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PINESCRIPT_DB_PATH` | `./pinescript_db` | ChromaDB database path |
| `PINESCRIPT_COLLECTION` | `pinescript_v6` | ChromaDB collection name |
| `PINESCRIPT_EMBED_MODEL` | `all-MiniLM-L6-v2` | SentenceTransformer model |
| `PINESCRIPT_MAX_RESULTS` | `30` | Maximum results per query |
| `SCRAPE_DELAY_MS` | `1500` | Delay between scrape requests |
| `SCRAPE_WORKERS` | `2` | Concurrent scrape workers |
| `LOG_LEVEL` | `INFO` | Loguru log level |
| `PINE_FACADE_TIMEOUT` | `20` | Pine-facade request timeout (seconds) |
| `VALIDATION_CACHE_TTL` | `300` | Validation cache TTL (seconds) |
| `HOT_CACHE_NAMESPACES` | `ta,strategy,...` | Namespaces loaded into hot cache |

## Pipeline Scripts

### discover_entries.py (Stage 1)
```bash
python pipeline/discover_entries.py [--output FILE] [--headful] [--debug]
```

### scrape_entries.py (Stage 2)
```bash
python pipeline/scrape_entries.py [--index FILE] [--output FILE] [--headful] [--debug]
python pipeline/scrape_entries.py --entry fun_ta.ema      # single entry
python pipeline/scrape_entries.py --retry-failed           # retry failures
```

### merge_and_index.py (Stage 3)
```bash
python pipeline/merge_and_index.py [--local FILE] [--live FILE] [--db PATH] [--reset] [--dry-run]
```

## Updating Docs

```bash
./run.sh --rescrape --reset-db           # Full re-scrape
python pipeline/merge_and_index.py --reset  # Quick re-index
./run.sh --entry=fun_ta.ema                 # Single entry
```

## Troubleshooting

1. **"ChromaDB has failed too many times"** — Run `./run.sh` to rebuild the database.
2. **"No module named 'chromadb'"** — Activate the venv: `source .venv/bin/activate`.
3. **"Playwright install failed"** — Run: `python -m playwright install chromium`.
4. **Low entry count (< 850)** — TradingView may have changed page structure. Use `--headful --debug`.
5. **Scrape timeout** — Increase `SCRAPE_DELAY_MS` to 3000.
6. **Server won't start** — Verify `pinescript_db/` exists. Run `python pipeline/merge_and_index.py`.
7. **"Entry not found"** — Use `search_docs()` for fuzzy matching.
8. **Pine-facade unavailable** — Circuit breaker activates after 5 failures. Waits 2 min before retrying.

## IDE Configuration

See `config.json` for complete configurations. Replace `/ABSOLUTE/PATH/` with the actual path.

### Claude Desktop
Add to `~/Library/Application Support/Claude/claude_desktop_config.json`.

### Claude Code
Add `.mcp.json` to your project root.

### Cursor / Windsurf / OpenCode
Add the respective config files — see `config.json` for exact paths.
