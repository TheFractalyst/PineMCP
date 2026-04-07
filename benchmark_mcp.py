#!/usr/bin/env python3
"""
Benchmark all 21 PineScript MCP server tools via JSON-RPC over stdio.
Uses newline-delimited JSON (MCP stdio transport protocol).

Measures cold/warm response times, response size, and quality score.
Outputs results as a markdown table sorted by warm response time (slowest first).
"""

import json
import math
import os
import subprocess
import time

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SERVER_CMD = [
    "/Users/fractalyst/pinescript_mcp/.venv/bin/python3",
    "/Users/fractalyst/pinescript_mcp/pinescript_mcp.py",
]

ITERATIONS = 3  # first=cold, rest=warm
SERVER_STARTUP_DELAY = 4  # seconds to wait for ChromaDB + model loading

VALID_CODE = (
    '//@version=6\n'
    'indicator("Test", overlay=true)\n'
    'src = ta.sma(close, 20)\n'
    'plot(src, color=color.blue)\n'
)

INVALID_CODE = (
    '//@version=6\n'
    'indicator("Test", overlay=true)\n'
    'src = sma(close, 20)\n'
    'plot(src, color=color.blue)\n'
)

FIX_CODE = (
    '//@version=6\n'
    'indicator("Test", overlay=true)\n'
    'src = sma(close, 20)\n'
    'plot(src)\n'
)

LOOKUP_CODE = (
    '//@version=6\n'
    'indicator("Test")\n'
    'myVal = ta.highest(high, 20)\n'
    'if myVal > close\n'
    '    label.new(bar_index, high, "Peak")\n'
)

DEBUG_CODE = (
    '//@version=6\n'
    'strategy("Test", overlay=true)\n'
    'longCondition = ta.crossover(ta.sma(close, 14), ta.sma(close, 28))\n'
    'if longCondition\n'
    '    strategy.entry("Long", strategy.long)\n'
)

# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions: (display_name, tool_name, kwargs)
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    ("search_docs", "search_docs", {"query": "moving average crossover"}),
    ("get_function", "get_function", {"name": "ta.ema"}),
    ("get_variable", "get_variable", {"name": "close"}),
    ("get_type", "get_type", {"name": "array"}),
    ("get_constant", "get_constant", {"name": "color.red"}),
    ("get_keyword", "get_keyword", {"name": "if"}),
    ("get_operator", "get_operator", {"name": "[]"}),
    ("get_examples", "get_examples", {"query": "strategy entry stop loss"}),
    ("list_namespace", "list_namespace", {"namespace": "ta"}),
    ("search_by_return_type", "search_by_return_type", {"return_type": "series float"}),
    ("validate_syntax (valid)", "validate_syntax", {"code": VALID_CODE}),
    ("validate_syntax (invalid)", "validate_syntax", {"code": INVALID_CODE}),
    ("validate_and_explain", "validate_and_explain", {"code": INVALID_CODE}),
    ("fix_and_validate", "fix_and_validate", {
        "code": FIX_CODE,
        "error_description": "sma is undefined, missing ta. prefix",
    }),
    ("generate_indicator", "generate_indicator", {
        "name": "RSI Indicator",
        "description": "Relative Strength Index",
        "overlay": False,
    }),
    ("generate_strategy", "generate_strategy", {
        "name": "MA Crossover",
        "description": "Moving average crossover strategy",
        "initial_capital": 10000,
        "commission_pct": 0.1,
        "pyramiding": 1,
    }),
    ("lookup_and_correct", "lookup_and_correct", {
        "code": LOOKUP_CODE,
        "error_description": "find highest high and label peaks",
    }),
    ("debug_pine_facade", "debug_pine_facade", {"code": DEBUG_CODE}),
    ("suggest_functions", "suggest_functions", {"context": "calculate moving average"}),
    ("get_namespace_cheatsheet", "get_namespace_cheatsheet", {"namespace": "strategy"}),
    ("validate_file", "validate_file", {
        "file_path": "/Users/fractalyst/Documents/Quantify - Deeptest/Strategies/DCA.ps",
    }),
]


# ─────────────────────────────────────────────────────────────────────────────
# MCP stdio transport helpers
# ─────────────────────────────────────────────────────────────────────────────

def send_msg(proc, msg_dict):
    """Send a newline-delimited JSON-RPC message."""
    line = json.dumps(msg_dict) + "\n"
    proc.stdin.write(line.encode("utf-8"))
    proc.stdin.flush()


def recv_msg(proc, timeout=120.0):
    """Read a single newline-delimited JSON-RPC response. Returns (parsed_json, raw_text)."""
    line = proc.stdout.readline().decode("utf-8").strip()
    if not line:
        raise RuntimeError("Empty response from server")
    return json.loads(line), line


def initialize_server(proc):
    """Perform MCP initialize handshake."""
    send_msg(proc, {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "benchmark", "version": "1.0.0"},
        },
    })
    resp, _ = recv_msg(proc)
    if "error" in resp:
        raise RuntimeError(f"Init failed: {resp['error']}")

    # initialized notification (no response expected)
    send_msg(proc, {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    })


def call_tool(proc, tool_name, arguments, req_id):
    """Call a tool and measure round-trip time. Returns (text_content, elapsed_ms)."""
    t0 = time.perf_counter()
    send_msg(proc, {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    })
    resp, raw = recv_msg(proc)
    elapsed = (time.perf_counter() - t0) * 1000  # ms

    # Extract text content
    text_parts = []
    is_error = False

    # Check for JSON-RPC error
    if "error" in resp:
        text_parts.append(f"JSON-RPC ERROR: {resp['error']}")
        is_error = True
    else:
        result = resp.get("result", {})
        content = result.get("content", [])
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                if item.get("type") == "resource":
                    text_parts.append(json.dumps(item.get("resource", {})))
        # Check for tool-level error flag
        if result.get("isError", False):
            is_error = True

    full_text = "\n".join(text_parts)

    # Detect internal errors in text (NameError, ImportError, etc.)
    if any(err in full_text for err in ["NameError:", "ImportError:", "Traceback", "Exception:"]):
        is_error = True

    return full_text, elapsed, is_error


def quality_score(tool_name, text, elapsed_ms, is_error):
    """Score response quality 1-10 based on content and speed.

    Rubric:
      10 = comprehensive, fast response (detailed docs/code, <500ms)
       9 = comprehensive but slightly slower, or fast but slightly less detailed
       8 = good response, adequate detail
       7 = usable but brief or slow
       6 = minimal response or notably slow
       5 = sparse, but not an error
       3 = error response that still contains useful info
       1 = empty or hard error
    """
    if is_error:
        if len(text) > 200:
            return 3  # error but has diagnostic info
        if len(text) > 0:
            return 2
        return 1

    if len(text) == 0:
        return 1

    score = 5

    # Content depth
    if len(text) > 100:
        score += 1
    if len(text) > 500:
        score += 1
    if len(text) > 1500:
        score += 1
    if len(text) > 5000:
        score += 1

    # Speed bonus (for warm-call range)
    if elapsed_ms < 5:
        score += 1
    elif elapsed_ms < 50:
        score += 0  # neutral
    elif elapsed_ms > 5000:
        score -= 1

    return min(10, max(1, score))


# ─────────────────────────────────────────────────────────────────────────────
# Main benchmark
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("PineScript MCP Server Benchmark")
    print("=" * 70)
    print()
    print(f"Server: {SERVER_CMD[1]}")
    print(f"Iterations: {ITERATIONS} (1 cold + {ITERATIONS - 1} warm)")
    print(f"Tools: {len(TOOLS)}")
    print()

    # Start server
    env = os.environ.copy()
    env["LOG_LEVEL"] = "ERROR"

    print(f"Starting server (waiting {SERVER_STARTUP_DELAY}s for initialization)...")
    proc = subprocess.Popen(
        SERVER_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        time.sleep(SERVER_STARTUP_DELAY)

        print("Performing MCP handshake...")
        initialize_server(proc)
        print("Connected. Running benchmarks...\n")

        results = []
        req_id = 1

        for display_name, tool_name, kwargs in TOOLS:
            timings = []
            sizes = []
            errors = []

            for i in range(ITERATIONS):
                label = "COLD" if i == 0 else f"WARM-{i}"

                try:
                    text, elapsed_ms, is_error = call_tool(proc, tool_name, kwargs, req_id)
                    req_id += 1

                    timings.append(elapsed_ms)
                    sizes.append(len(text))
                    errors.append(is_error)

                    err_flag = " ERR" if is_error else ""
                    print(
                        f"  {display_name:35s} [{label:6s}] "
                        f"{elapsed_ms:8.1f} ms  {len(text):6d} chars{err_flag}"
                    )
                except Exception as e:
                    req_id += 1
                    timings.append(float("nan"))
                    sizes.append(0)
                    errors.append(True)
                    print(f"  {display_name:35s} [{label:6s}] FAILED: {e}")

            cold_ms = timings[0]
            warm_ms_list = [t for t in timings[1:] if not math.isnan(t)]
            warm_ms = sum(warm_ms_list) / len(warm_ms_list) if warm_ms_list else float("nan")
            avg_size = sum(sizes) / len(sizes)

            # Quality based on last warm call (most representative)
            last_text_size = sizes[-1]
            last_elapsed = timings[-1] if not math.isnan(timings[-1]) else 99999
            last_error = errors[-1]
            q = quality_score(tool_name, "x" * last_text_size, last_elapsed, last_error)

            results.append({
                "tool": display_name,
                "cold_ms": cold_ms,
                "warm_ms": warm_ms,
                "resp_chars": int(avg_size),
                "quality": q,
            })

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Sort by warm_ms descending (slowest first); NaN goes to end
    def sort_key(r):
        if math.isnan(r["warm_ms"]):
            return -1
        return r["warm_ms"]
    results.sort(key=sort_key, reverse=True)

    # Generate markdown
    lines = []
    lines.append("# PineScript MCP Server Benchmark Results")
    lines.append("")
    lines.append(f"**Server**: `{SERVER_CMD[1]}`")
    lines.append(f"**Iterations per tool**: {ITERATIONS} (1 cold + {ITERATIONS - 1} warm)")
    lines.append(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Tools tested**: {len(TOOLS)}")
    lines.append("")
    lines.append("| # | Tool | Cold (ms) | Warm Avg (ms) | Response (chars) | Quality (1-10) |")
    lines.append("|--:|------|-----------|---------------|------------------|----------------|")

    for idx, r in enumerate(results, 1):
        cold_str = f"{r['cold_ms']:.0f}" if not math.isnan(r["cold_ms"]) else "ERR"
        warm_str = f"{r['warm_ms']:.0f}" if not math.isnan(r["warm_ms"]) else "ERR"
        lines.append(
            f"| {idx} | {r['tool']} | {cold_str} | {warm_str} | {r['resp_chars']:,} | {r['quality']} |"
        )

    lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append("- **Cold**: First invocation after server startup (includes lazy-loading, ChromaDB cold query, embedding computation)")
    lines.append("- **Warm Avg**: Mean of subsequent invocations (L1/L2 caches may be populated)")
    lines.append("- **Response (chars)**: Average character count of returned text content across all iterations")
    lines.append("- **Quality (1-10)**: Heuristic combining response depth (size tiers) and speed (<5ms bonus, >5s penalty). Error responses score 1-3.")
    lines.append("- Tools calling `pine-facade` (TradingView remote compiler) include network round-trip latency (~200-700ms cold, ~4-5ms cached)")
    lines.append("- `validate_file` reads a 4999-bar strategy file from disk, so it includes both file I/O and pine-facade compilation")
    lines.append("")

    md = "\n".join(lines)

    output_path = "/Users/fractalyst/pinescript_mcp/benchmark_results.md"
    with open(output_path, "w") as f:
        f.write(md)

    print(f"\n{'=' * 70}")
    print(f"Results written to {output_path}")
    print(f"{'=' * 70}\n")
    print(md)


if __name__ == "__main__":
    main()
