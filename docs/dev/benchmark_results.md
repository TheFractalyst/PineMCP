# PineScript MCP Server Benchmark Results

**Server**: `./server.py`
**Iterations per tool**: 3 (1 cold + 2 warm)
**Date**: 2026-04-07 04:44:39
**Tools tested**: 21

| # | Tool | Cold (ms) | Warm Avg (ms) | Response (chars) | Quality (1-10) |
|--:|------|-----------|---------------|------------------|----------------|
| 1 | generate_strategy | 774 | 375 | 1,939 | 8 |
| 2 | generate_indicator | 856 | 367 | 1,199 | 7 |
| 3 | validate_and_explain | 216 | 159 | 600 | 7 |
| 4 | validate_syntax (valid) | 411 | 3 | 96 | 6 |
| 5 | fix_and_validate | 212 | 3 | 380 | 7 |
| 6 | debug_pine_facade | 211 | 3 | 1,023 | 8 |
| 7 | validate_syntax (invalid) | 216 | 3 | 228 | 7 |
| 8 | lookup_and_correct | 355 | 1 | 636 | 8 |
| 9 | validate_file | 120 | 1 | 839 | 8 |
| 10 | suggest_functions | 27 | 1 | 2,118 | 9 |
| 11 | search_docs | 130 | 1 | 1,561 | 9 |
| 12 | get_namespace_cheatsheet | 19 | 1 | 14,339 | 10 |
| 13 | search_by_return_type | 86 | 1 | 2,692 | 9 |
| 14 | get_keyword | 35 | 1 | 2,927 | 9 |
| 15 | get_examples | 134 | 1 | 8,037 | 10 |
| 16 | list_namespace | 9 | 1 | 5,205 | 10 |
| 17 | get_type | 4 | 1 | 858 | 8 |
| 18 | get_variable | 1 | 1 | 718 | 8 |
| 19 | get_function | 2 | 1 | 1,719 | 9 |
| 20 | get_operator | 2 | 1 | 1,020 | 8 |
| 21 | get_constant | 1 | 0 | 629 | 8 |

## Methodology

- **Cold**: First invocation after server startup (includes lazy-loading, ChromaDB cold query, embedding computation)
- **Warm Avg**: Mean of subsequent invocations (L1/L2 caches may be populated)
- **Response (chars)**: Average character count of returned text content across all iterations
- **Quality (1-10)**: Heuristic combining response depth (size tiers) and speed (<5ms bonus, >5s penalty). Error responses score 1-3.
- Tools calling `pine-facade` (TradingView remote compiler) include network round-trip latency (~200-700ms cold, ~4-5ms cached)
- `validate_file` reads a 4999-bar strategy file from disk, so it includes both file I/O and pine-facade compilation
