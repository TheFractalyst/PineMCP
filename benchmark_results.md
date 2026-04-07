# PineScript MCP Server Benchmark Results

**Server**: `/Users/fractalyst/pinescript_mcp/.venv/bin/python3`
**Iterations per tool**: 3 (1 cold + 2 warm)
**Date**: 2026-04-07 04:34:51
**Tools tested**: 21

| Tool | Cold (ms) | Warm Avg (ms) | Response (chars) | Quality (1-10) |
|------|-----------|---------------|------------------|----------------|
| validate_and_explain | 199 | 160 | 383 | 9 |
| validate_file | 2035 | 131 | 300 | 9 |
| get_namespace_cheatsheet | 24 | 15 | 14339 | 10 |
| list_namespace | 8 | 7 | 5205 | 10 |
| debug_pine_facade | 736 | 5 | 1021 | 10 |
| lookup_and_correct | 675 | 5 | 88 | 6 |
| validate_syntax (invalid) | 627 | 4 | 228 | 9 |
| validate_syntax (valid) | 661 | 4 | 96 | 6 |
| fix_and_validate | 453 | 4 | 380 | 9 |
| suggest_functions | 3 | 2 | 86 | 6 |
| generate_indicator | 3 | 2 | 88 | 6 |
| generate_strategy | 2 | 2 | 86 | 6 |
| get_type | 3 | 2 | 858 | 9 |
| get_keyword | 33 | 2 | 2927 | 10 |
| search_by_return_type | 2 | 1 | 94 | 6 |
| get_operator | 1 | 1 | 1020 | 9 |
| search_docs | 4 | 1 | 74 | 6 |
| get_examples | 1 | 1 | 76 | 6 |
| get_function | 1 | 1 | 1719 | 9 |
| get_constant | 1 | 1 | 629 | 9 |
| get_variable | 1 | 1 | 718 | 9 |

## Notes

- **Cold**: First invocation (includes any lazy-loading / ChromaDB query overhead)
- **Warm Avg**: Average of subsequent invocations (cache may be populated)
- **Response (chars)**: Average character count of returned text content
- **Quality**: Heuristic score (1-10) based on response size, speed, and tool type
- Tools calling `pine-facade` (TradingView remote compiler) depend on network latency
