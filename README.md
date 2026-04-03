# PineScript v6 Complete Reference — MCP Server

Production-grade MCP (Model Context Protocol) server providing the complete PineScript v6 reference documentation via **22 specialized tools** backed by a local ChromaDB vector store, **TradingView's official pine-facade compiler**, and a memory-first hot cache.

Combines two data sources into one unified knowledge base:
1. **Local parsed documentation** — 1,242 entries from PDF/Markdown
2. **Live TradingView reference** — ~1,360 entries scraped from the official site
3. **Merged total** — ~1,400–1,600 unique entries

## Architecture

```
  PDF docs ──→ parse_docs.py ──→ pinescript_chunks.json ──┐
                                                            ├→ merge_and_index.py ──→ ChromaDB ──→ pinescript_mcp.py ──→ AI
  TradingView ──→ discover_entries.py ──→ scrape_entries.py ──→ tv_scraped_entries.json ──┘

  pine-facade.tradingview.com ──→ compile endpoint ──→ validate_syntax / validate_and_explain ──→ AI
```

**3-stage pipeline:** discover → scrape → merge+index
**Compiler integration:** pine-facade for 100% accurate validation

## Quick Start

```bash
./run.sh                    # Full pipeline
./run.sh --skip-scrape      # Skip scraping, just merge+index
./run.sh --rescrape         # Force re-scrape
```

## Tools (22 total)

### Lookup Tools (6)
| # | Tool | Description |
|---|------|-------------|
| 1 | `get_function(name)` | Full docs: syntax, all overloads, params, examples |
| 2 | `get_variable(name)` | Built-in variable description and behavior |
| 3 | `get_type(name)` | Type definition, fields, methods |
| 4 | `get_constant(name)` | Constant value and usage |
| 5 | `get_keyword(name)` | Keyword syntax and examples |
| 6 | `get_operator(name)` | Operator description and examples |

### Search Tools (4)
| # | Tool | Description |
|---|------|-------------|
| 7 | `search_docs(query)` | Semantic search across everything |
| 8 | `get_examples(query)` | Find real working code by concept |
| 9 | `search_by_return_type(type)` | Find functions returning a type |
| 10 | `list_namespace(namespace)` | All members of a namespace |

### Live Data Tools (2)
| # | Tool | Description |
|---|------|-------------|
| 11 | `get_live_entry(name)` | Real-time fetch from TradingView site |
| 12 | `get_source_url(name)` | Get direct TradingView URL |

### Maintenance Tools (2)
| # | Tool | Description |
|---|------|-------------|
| 13 | `diff_entry(name)` | Compare indexed vs live TradingView data |
| 14 | `check_freshness(namespace?)` | See live vs local data coverage |

### Validation Tools (3) — pine-facade compiler
| # | Tool | Description |
|---|------|-------------|
| 15 | `validate_syntax(code)` | Validate code using TradingView's official compiler |
| 16 | `validate_and_explain(code)` | Validate + cross-reference errors against docs |
| 17 | `fix_and_validate(code, error_description)` | Look up fixes from docs, show validation |

### Code Generation Tools (3) — validated templates
| # | Tool | Description |
|---|------|-------------|
| 18 | `generate_indicator(name, ...)` | Generate validated indicator template |
| 19 | `generate_strategy(name, ...)` | Generate validated strategy template |
| 20 | `lookup_and_correct(code, intent)` | Validate + search docs + explain fixes |

### Smart Context Tools (2)
| # | Tool | Description |
|---|------|-------------|
| 21 | `suggest_functions(context, ...)` | Suggest relevant functions with signatures |
| 22 | `get_namespace_cheatsheet(namespace)` | Compact cheatsheet for an entire namespace |

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
| Total MCP tools | **22** | ~5 | 1 | ~3 |

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
python discover_entries.py [--output FILE] [--headful] [--debug]
```

### scrape_entries.py (Stage 2)
```bash
python scrape_entries.py [--index FILE] [--output FILE] [--headful] [--debug]
python scrape_entries.py --entry fun_ta.ema      # single entry
python scrape_entries.py --retry-failed           # retry failures
```

### merge_and_index.py (Stage 3)
```bash
python merge_and_index.py [--local FILE] [--live FILE] [--db PATH] [--reset] [--dry-run]
```

## Updating Docs

```bash
./run.sh --rescrape --reset-db   # Full re-scrape
python merge_and_index.py --reset # Quick re-index
./run.sh --entry=fun_ta.ema      # Single entry
```

## Troubleshooting

1. **"ChromaDB has failed too many times"** — Run `./run.sh` to rebuild the database.
2. **"No module named 'chromadb'"** — Activate the venv: `source .venv/bin/activate`.
3. **"Playwright install failed"** — Run: `python -m playwright install chromium`.
4. **Low entry count (< 850)** — TradingView may have changed page structure. Use `--headful --debug`.
5. **Scrape timeout** — Increase `SCRAPE_DELAY_MS` to 3000.
6. **Server won't start** — Verify `pinescript_db/` exists. Run `python merge_and_index.py`.
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
