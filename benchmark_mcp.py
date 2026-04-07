#!/usr/bin/env python3
"""
Benchmark all 21 PineScript MCP server tools via JSON-RPC over stdio.

Measures cold/warm response times, response size, and quality score.
Outputs results as a markdown table sorted by response time (slowest first).
"""

import json
import subprocess
import sys
import time
import os

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SERVER_CMD = [
    "/Users/fractalyst/pinescript_mcp/.venv/bin/python3",
    "/Users/fractalyst/pinescript_mcp/pinescript_mcp.py",
]

ITERATIONS = 3  # first=cold, rest=warm
REQUEST_ID = 1

VALID_CODE = """//@version=6
indicator("Test", overlay=true)
src = ta.sma(close, 20)
plot(src, color=color.blue)
"""

INVALID_CODE = """//@version=6
indicator("Test", overlay=true)
src = sma(close, 20)
plot(src, color=color.blue)
"""

FIX_CODE = """//@version=6
indicator("Test", overlay=true)
src = sma(close, 20)
plot(src)
"""

LOOKUP_CODE = """//@version=6
indicator("Test")
myVal = ta.highest(high, 20)
if myVal > close
    label.new(bar_index, high, "Peak")
"""

DEBUG_CODE = """//@version=6
strategy("Test", overlay=true)
longCondition = ta.crossover(ta.sma(close, 14), ta.sma(close, 28))
if longCondition
    strategy.entry("Long", strategy.long)
"""

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


def make_request(tool_name: str, arguments: dict, req_id: int) -> str:
    """Build a JSON-RPC tools/call request."""
    msg = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    return json.dumps(msg)


def send_and_recv(proc: subprocess.Popen, request_str: str, timeout: float = 120.0) -> tuple[str, float]:
    """Send a JSON-RPC message via stdin, read response from stdout. Returns (response_text, elapsed_s)."""
    payload = request_str + "\n"
    t0 = time.perf_counter()
    proc.stdin.write(payload.encode("utf-8"))
    proc.stdin.flush()

    # Read until we get a complete JSON response
    response_line = proc.stdout.readline().decode("utf-8")
    elapsed = time.perf_counter() - t0

    if not response_line:
        raise RuntimeError("Empty response from server (process may have crashed)")

    return response_line.strip(), elapsed


def initialize_server(proc: subprocess.Popen) -> None:
    """Send MCP initialize + initialized sequence."""
    # initialize
    init_msg = json.dumps({
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "benchmark", "version": "1.0.0"},
        },
    }) + "\n"
    proc.stdin.write(init_msg.encode("utf-8"))
    proc.stdin.flush()
    resp = proc.stdout.readline().decode("utf-8").strip()
    if not resp:
        raise RuntimeError("Server failed to initialize")

    # initialized notification (no id)
    notif = json.dumps({
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }) + "\n"
    proc.stdin.write(notif.encode("utf-8"))
    proc.stdin.flush()


def parse_response_size(raw: str) -> int:
    """Parse JSON-RPC response and return character count of result text."""
    try:
        obj = json.loads(raw)
        result = obj.get("result", {})
        content = result.get("content", [])
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        full_text = "\n".join(texts)
        return len(full_text)
    except (json.JSONDecodeError, KeyError):
        return len(raw)


def quality_heuristic(tool_name: str, response_chars: int, elapsed: float) -> int:
    """Score response quality 1-10 based on heuristics.

    Criteria:
    - Non-empty response (>0 chars): base 5
    - Substantial response (>100 chars): +2
    - Detailed response (>500 chars): +1
    - Fast response (<2s): +1
    - Very slow (>15s): -1
    - Empty/error: 1
    """
    if response_chars == 0:
        return 1

    score = 5
    if response_chars > 100:
        score += 2
    if response_chars > 500:
        score += 1
    if response_chars > 2000:
        score += 1

    if elapsed < 1.0:
        score += 1
    elif elapsed > 15.0:
        score -= 1

    # Validation/codegen tools that call pine-facade get benefit for not timing out
    if tool_name in (
        "validate_syntax", "validate_and_explain", "fix_and_validate",
        "generate_indicator", "generate_strategy", "lookup_and_correct",
        "debug_pine_facade", "validate_file",
    ):
        if response_chars > 100 and elapsed < 30:
            score += 1

    return min(10, max(1, score))


def main():
    print("Starting MCP server process...")
    env = os.environ.copy()
    env["LOG_LEVEL"] = "ERROR"  # suppress loguru noise

    proc = subprocess.Popen(
        SERVER_CMD,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    try:
        print("Initializing MCP connection...")
        initialize_server(proc)
        print("Server initialized. Running benchmarks...\n")

        results = []
        req_id = 1

        for display_name, tool_name, kwargs in TOOLS:
            timings = []
            resp_sizes = []

            for i in range(ITERATIONS):
                label = "COLD" if i == 0 else f"WARM-{i}"
                req_str = make_request(tool_name, kwargs, req_id)
                req_id += 1

                try:
                    raw_resp, elapsed = send_and_recv(proc, req_str)
                    resp_size = parse_response_size(raw_resp)
                    timings.append(elapsed * 1000)  # ms
                    resp_sizes.append(resp_size)
                    print(f"  {display_name:35s} [{label}] {elapsed*1000:8.1f} ms  {resp_size:6d} chars")
                except Exception as e:
                    print(f"  {display_name:35s} [{label}] ERROR: {e}")
                    timings.append(-1)
                    resp_sizes.append(0)

            cold_ms = timings[0] if timings[0] > 0 else float("nan")
            warm_ms_list = [t for t in timings[1:] if t > 0]
            warm_ms = sum(warm_ms_list) / len(warm_ms_list) if warm_ms_list else float("nan")
            avg_resp_size = sum(resp_sizes) / len(resp_sizes) if resp_sizes else 0

            # Use last response size for quality eval (most representative)
            last_size = resp_sizes[-1] if resp_sizes else 0
            last_time = timings[-1] if timings else 0
            quality = quality_heuristic(tool_name, last_size, last_time / 1000.0 if last_time > 0 else 999)

            results.append({
                "tool": display_name,
                "cold_ms": cold_ms,
                "warm_ms": warm_ms,
                "resp_chars": int(avg_resp_size),
                "quality": quality,
                "warm_timings": warm_ms_list,
            })

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    # Sort by warm_ms descending (slowest first)
    results.sort(key=lambda r: r["warm_ms"] if r["warm_ms"] == r["warm_ms"] else 0, reverse=True)

    # Generate markdown
    lines = []
    lines.append("# PineScript MCP Server Benchmark Results")
    lines.append("")
    lines.append(f"**Server**: `{SERVER_CMD[0]}`")
    lines.append(f"**Iterations per tool**: {ITERATIONS} (1 cold + {ITERATIONS-1} warm)")
    lines.append(f"**Date**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Tools tested**: {len(TOOLS)}")
    lines.append("")
    lines.append("| Tool | Cold (ms) | Warm Avg (ms) | Response (chars) | Quality (1-10) |")
    lines.append("|------|-----------|---------------|------------------|----------------|")

    for r in results:
        cold_str = f"{r['cold_ms']:.0f}" if r["cold_ms"] == r["cold_ms"] else "ERR"
        warm_str = f"{r['warm_ms']:.0f}" if r["warm_ms"] == r["warm_ms"] else "ERR"
        lines.append(
            f"| {r['tool']} | {cold_str} | {warm_str} | {r['resp_chars']} | {r['quality']} |"
        )

    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- **Cold**: First invocation (includes any lazy-loading / ChromaDB query overhead)")
    lines.append("- **Warm Avg**: Average of subsequent invocations (cache may be populated)")
    lines.append("- **Response (chars)**: Average character count of returned text content")
    lines.append("- **Quality**: Heuristic score (1-10) based on response size, speed, and tool type")
    lines.append("- Tools calling `pine-facade` (TradingView remote compiler) depend on network latency")
    lines.append("")

    md = "\n".join(lines)

    output_path = "/Users/fractalyst/pinescript_mcp/benchmark_results.md"
    with open(output_path, "w") as f:
        f.write(md)

    print(f"\nResults written to {output_path}")
    print(md)


if __name__ == "__main__":
    main()
