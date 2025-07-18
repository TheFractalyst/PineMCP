[![PineMCP](assets/banner.svg)](https://github.com/TheFractalyst/PineMCP)

[![PyPI](https://img.shields.io/pypi/v/pinemcp?color=blue)](https://pypi.org/project/pinemcp/)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://python.org)
[![ChromaDB](https://img.shields.io/badge/ChromaDB-FF6F00)](https://trychroma.com)
[![MCP](https://img.shields.io/badge/MCP-Protocol-blue)](https://modelcontextprotocol.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests](https://github.com/TheFractalyst/PineMCP/actions/workflows/test.yml/badge.svg)](https://github.com/TheFractalyst/PineMCP/actions/workflows/test.yml)

Complete PineScript v6 reference documentation MCP server with 1751 entries
covering all functions, variables, constants, types, keywords, operators, and
annotations from the official TradingView PineScript v6 reference.

[Quick Start](#quick-start) |
[Tools](#tools) |
[Configuration](#configuration) |
[Database](#database) |
[Development](#development)

---

## One Command. Two Minutes. Fully Functional.

```bash
pip install pinemcp
pinemcp
```

That's it. On first run, the server auto-builds the ChromaDB vector store from
shipped JSON data (takes 30-60 seconds for embedding model download + indexing).
Subsequent runs start instantly.

**What you get:**

- 6 MCP tools for PineScript v6 docs lookup, code validation, and code generation
- 1751 entries indexed in a local ChromaDB vector store (100% offline)
- Sub-millisecond hot cache for priority lookups
- Remote TradingView compiler integration for `pine_compile` and `pine_repair`
- Works with Claude Desktop, Cursor, Windsurf, OpenCode, and any MCP client

**What you need:**

- Python 3.10+
- Any MCP-compatible AI client

---

## Why PineMCP?

AI coding assistants hallucinate PineScript v6 syntax. PineScript v6 introduced
breaking changes from v5 (typed `na`, removed `transp`, `when` parameter removal,
bool casting changes, etc.) that models trained on older code get wrong.

PineMCP gives AI assistants **authoritative, real-time access** to the complete
TradingView v6 reference:

- **100% coverage**: 919/919 base entries from the live TradingView v6 reference
- **Semantic search**: Vector embeddings find relevant docs by meaning, not just keywords
- **Remote compilation**: `pine_compile` validates code against the real TradingView compiler
- **v5 to v6 migration**: `pine_repair(mode="migrate")` applies all known v5->v6 replacements
- **Code generation**: `pine_scaffold` generates validated indicator or strategy templates
- **100% local**: No network calls at runtime (except optional compiler validation)

---

## Tools

```
+----------------------+---------------------------------------------+
| Tool                 | Description                                 |
+----------------------+---------------------------------------------+
| pine_lookup          | Get complete docs for a symbol by exact name|
| pine_search          | Semantic search across all docs             |
| pine_browse          | Enumerate all members of a namespace        |
| pine_compile         | Compile PineScript code via TradingView     |
| pine_repair          | Fix compiler errors or migrate v5 to v6     |
| pine_scaffold        | Generate indicator or strategy template     |
+----------------------+---------------------------------------------+
```

### Tool Details

**`pine_lookup(name, kind?)`**
Get complete documentation for a PineScript v6 symbol by exact name.
Auto-detects function, variable, type, constant, keyword, or operator.
Returns syntax, parameters, returns, remarks, examples, and see-also links.

**`pine_search(query, category?, namespace?, return_type?, has_examples?, current_line?, n_results?)`**
Semantic search across the entire v6 reference. Supports category filtering,
namespace filtering, return type matching, example-only mode, and
context-aware function suggestions based on the current line being typed.

**`pine_browse(namespace, category?, style?)`**
Enumerate every member of a namespace (e.g., `ta`, `strategy`, `array`).
`style="cheatsheet"` produces a compact, box-drawn signature summary.

**`pine_compile(code?, file_path?, explain?)`**
Compile PineScript v6 code via the remote TradingView compiler.
Supports inline code, file paths, and `explain=True` for error-specific fix hints.

**`pine_repair(code, context, mode?)`**
Fix compiler errors (`mode="targeted"`) or migrate v5 code to v6
(`mode="migrate"`). Applies all known v5->v6 breaking change replacements,
then recompiles to verify.

**`pine_scaffold(kind, name, description?, inputs?, overlay?, initial_capital?, commission_pct?, pyramiding?)`**
Generate a validated PineScript v6 indicator or strategy template.
Pre-wired with standard inputs, risk parameters, and plot functions.

## MCP Client Configuration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "pinescript-v6": {
      "command": "pinemcp",
      "transport": "stdio"
    }
  }
}
```

### Cursor / Windsurf / OpenCode

Add to your project's `.mcp.json` or global MCP config:

```json
{
  "mcpServers": {
    "pinescript-v6": {
      "command": "pinemcp",
      "transport": "stdio"
    }
  }
}
```

### SSE (HTTP)

```bash
pinemcp --transport sse --port 8080
```

Connect to `http://localhost:8080`. Set `MCP_API_KEY` env var for auth.

## Configuration

```
+------------------------+---------------------+----------------------------------+
| Env Var                | Default             | Description                      |
+------------------------+---------------------+----------------------------------+
| TRANSPORT              | stdio               | Transport: stdio or sse          |
| PORT                   | 8080                | Port for SSE transport           |
| PINESCRIPT_DB_PATH     | ~/.pinescript_mcp/db| ChromaDB path                    |
| PINESCRIPT_COLLECTION  | pine_reference      | ChromaDB collection name         |
| PINE_EMBED_MODEL       | all-MiniLM-L6-v2    | Sentence transformer model       |
| LOG_LEVEL              | INFO                | Logging level                    |
| LAZY_MODEL             | 0                   | Set to 1 to defer model loading  |
| MCP_API_KEY            | (none)              | API key for SSE auth (optional)  |
+------------------------+---------------------+----------------------------------+
```

## Database

- 1751 entries (v6 only)
- 53 namespaces (ta, strategy, array, matrix, math, str, color, etc.)
- 919/919 base entries from the live TradingView v6 reference (100% coverage)
- 832 additional enrichment entries from local documentation parsing
- Hot cache: 1447 entries preloaded in memory for sub-ms lookups
- Auto-built on first run from shipped JSON data (2.1MB)

To rebuild the database:

```bash
pinemcp build
```

## Development

```bash
git clone https://github.com/TheFractalyst/PineMCP.git
cd PineMCP
pip install -e ".[dev]"
pytest tests/ -v
```

### Re-scrape from TradingView

```bash
pip install -e ".[pipeline]"
python -m playwright install chromium
python pipeline/discover_entries.py
python pipeline/scrape_entries.py
python pipeline/merge_and_index.py --reset
```

## Tech Stack

- **FastMCP 3.0** - MCP server framework with FileSystemProvider auto-discovery
- **ChromaDB** - Local vector database for semantic search
- **SentenceTransformers** - `all-MiniLM-L6-v2` embedding model (384-dim)
- **RapidFuzz** - Fuzzy string matching for name lookups
- **Loguru** - Structured logging with daily rotation (SSE mode)

## Links

- [fractalyst.dev](https://fractalyst.dev/) - Portfolio
- [TradingView](https://www.tradingview.com/u/Fractalyst/#published-scripts) - Public PineScript indicators
- [@TheFractalyst](https://x.com/TheFractalyst) - X / Twitter

## License

MIT - see [LICENSE](LICENSE)
