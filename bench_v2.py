#!/usr/bin/env python3
"""
bench_v2.py — Comprehensive benchmark of all 20 PineScript MCP tools.

Measures latency (median of 3 calls), accuracy, completeness, and error handling.
Outputs results as a formatted table.

Usage:
    .venv/bin/python3 bench_v2.py
"""

import asyncio
import os
import statistics
import sys
import time
import traceback

# ── Ensure we import from the project root ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ── Import all tool functions directly ──
from pinescript_mcp import (
    search_docs,
    get_function,
    get_variable,
    get_type,
    get_constant,
    get_keyword,
    get_operator,
    get_examples,
    list_namespace,
    search_by_return_type,
    validate_syntax,
    validate_and_explain,
    fix_and_validate,
    generate_indicator,
    generate_strategy,
    lookup_and_correct,
    debug_pine_facade,
    suggest_functions,
    get_namespace_cheatsheet,
    validate_file,
)


# ─────────────────────────────────────────────────────────────────────────────
# Scoring helpers
# ─────────────────────────────────────────────────────────────────────────────

def speed_score(median_ms: float) -> int:
    """Latency → 1-10 score."""
    if median_ms < 50:    return 10
    if median_ms < 100:   return 9
    if median_ms < 200:   return 8
    if median_ms < 500:   return 7
    if median_ms < 1000:  return 6
    if median_ms < 2000:  return 5
    if median_ms < 5000:  return 4
    if median_ms < 10000: return 3
    if median_ms < 20000: return 2
    return 1


def accuracy_score(result: str, expected_keywords: list[str]) -> int:
    """Check if result contains expected keywords. Returns 1-10."""
    if not result or result.startswith("ERROR"):
        return 1
    lower = result.lower()
    hits = sum(1 for kw in expected_keywords if kw.lower() in lower)
    ratio = hits / len(expected_keywords) if expected_keywords else 0
    # Base score from keyword coverage
    base = min(10, int(ratio * 10) + 1)
    # Penalty for empty / very short results
    if len(result.strip()) < 50:
        base = min(base, 3)
    # Penalty for error-like responses on valid input
    if "not found" in lower and ratio < 0.3:
        base = min(base, 4)
    if "database unavailable" in lower:
        base = min(base, 2)
    return base


def completeness_score(result: str, expected_sections: list[str]) -> int:
    """Check if result contains expected sections/structure. Returns 1-10."""
    if not result or len(result.strip()) < 30:
        return 1
    lower = result.lower()
    hits = sum(1 for s in expected_sections if s.lower() in lower)
    ratio = hits / len(expected_sections) if expected_sections else 0
    base = min(10, int(ratio * 10) + 1)
    # Bonus for length (comprehensive response)
    if len(result) > 500:
        base = min(10, base + 1)
    if len(result) > 2000:
        base = min(10, base + 1)
    # Penalty for hollow results
    if len(result) < 100:
        base = min(base, 3)
    return base


async def error_handling_score(tool_fn, good_args: dict, bad_args_list: list[dict]) -> int:
    """Test error handling with bad inputs. Returns 1-10."""
    score = 10
    for bad_args in bad_args_list:
        try:
            result = await tool_fn(**bad_args)
            # Tool should return an error string, not raise
            if result and ("error" in result.lower() or "not found" in result.lower()
                          or "no " in result.lower()[:80]):
                score = min(score, 9)  # Graceful error message
            elif result and len(result) > 20:
                score = min(score, 8)  # Returned something reasonable
            else:
                score = min(score, 5)  # Empty or confusing
        except Exception as e:
            # Exception is less graceful than error string, but acceptable
            if "ToolError" in type(e).__name__:
                score = min(score, 7)
            else:
                score = min(score, 4)
    return score


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark runner
# ─────────────────────────────────────────────────────────────────────────────

async def time_tool(tool_fn, kwargs: dict, n_runs: int = 3) -> list[float]:
    """Run tool n_runs times, return list of latencies in ms."""
    times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        await tool_fn(**kwargs)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0)
    return times


# ─────────────────────────────────────────────────────────────────────────────
# Tool test definitions
# ─────────────────────────────────────────────────────────────────────────────

TOOL_TESTS = [
    {
        "name": "search_docs",
        "fn": search_docs,
        "args": {"query": "ema crossover", "n_results": 3},
        "expected_keywords": ["ema", "crossover", "ta."],
        "expected_sections": ["relevance", "[1]"],
        "bad_args": [
            {"query": "xyznonexistent12345", "n_results": 1},
        ],
    },
    {
        "name": "get_function",
        "fn": get_function,
        "args": {"name": "ta.ema"},
        "expected_keywords": ["ta.ema", "source", "length"],
        "expected_sections": ["syntax", "parameters", "returns", "example"],
        "bad_args": [
            {"name": "nonexistent_func_xyz"},
        ],
    },
    {
        "name": "get_variable",
        "fn": get_variable,
        "args": {"name": "close"},
        "expected_keywords": ["close", "price", "series"],
        "expected_sections": ["description", "syntax"],
        "bad_args": [
            {"name": "nonexistent_var_xyz"},
        ],
    },
    {
        "name": "get_type",
        "fn": get_type,
        "args": {"name": "array"},
        "expected_keywords": ["array", "type"],
        "expected_sections": ["fields", "method"],
        "bad_args": [
            {"name": "nonexistent_type_xyz"},
        ],
    },
    {
        "name": "get_constant",
        "fn": get_constant,
        "args": {"name": "color.red"},
        "expected_keywords": ["color", "red"],
        "expected_sections": ["description", "syntax"],
        "bad_args": [
            {"name": "nonexistent.constant.xyz"},
        ],
    },
    {
        "name": "get_keyword",
        "fn": get_keyword,
        "args": {"name": "var"},
        "expected_keywords": ["var", "persistent", "variable"],
        "expected_sections": ["description", "syntax", "example"],
        "bad_args": [
            {"name": "nonexistent_keyword_xyz"},
        ],
    },
    {
        "name": "get_operator",
        "fn": get_operator,
        "args": {"name": "+"},
        "expected_keywords": ["addition", "operator", "or"],
        "expected_sections": ["description", "syntax"],
        "bad_args": [
            {"name": "nonexistent_op_xyz"},
        ],
    },
    {
        "name": "get_examples",
        "fn": get_examples,
        "args": {"query": "moving average crossover"},
        "expected_keywords": ["moving", "average", "crossover"],
        "expected_sections": ["example", "relevance"],
        "bad_args": [
            {"query": "xyznonexistent12345"},
        ],
    },
    {
        "name": "list_namespace",
        "fn": list_namespace,
        "args": {"namespace": "ta"},
        "expected_keywords": ["ta", "ema", "sma", "rsi"],
        "expected_sections": ["namespace", "function", "entries"],
        "bad_args": [
            {"namespace": "nonexistent_namespace_xyz"},
        ],
    },
    {
        "name": "search_by_return_type",
        "fn": search_by_return_type,
        "args": {"return_type": "series float"},
        "expected_keywords": ["series float", "function"],
        "expected_sections": ["returns", "syntax"],
        "bad_args": [
            {"return_type": "nonexistent_type_xyz"},
        ],
    },
    {
        "name": "validate_syntax",
        "fn": validate_syntax,
        "args": {"code": '//@version=6\nindicator("test")\nplot(close)'},
        "expected_keywords": ["valid", "compiles"],
        "expected_sections": ["compiler", "errors", "analysis"],
        "bad_args": [
            {"code": ""},
            {"code": "this is not pine script at all"},
        ],
    },
    {
        "name": "validate_and_explain",
        "fn": validate_and_explain,
        "args": {"code": '//@version=6\nindicator("test")\nplot(close)'},
        "expected_keywords": ["validation", "report"],
        "expected_sections": ["compiler", "status", "passed"],
        "bad_args": [
            {"code": ""},
            {"code": "ema(close, 14)"},
        ],
    },
    {
        "name": "fix_and_validate",
        "fn": fix_and_validate,
        "args": {"code": "ema(close, 14)", "error_description": "Undeclared identifier 'ema'"},
        "expected_keywords": ["fix", "hint", "ta.ema"],
        "expected_sections": ["error", "hint", "fix applied"],
        "bad_args": [
            {"code": "", "error_description": "test"},
            {"code": "test", "error_description": ""},
        ],
    },
    {
        "name": "generate_indicator",
        "fn": generate_indicator,
        "args": {"name": "RSI Test", "description": "relative strength index"},
        "expected_keywords": ["indicator", "rsi", "//@version=6"],
        "expected_sections": ["generated", "validation", "template"],
        "bad_args": [
            {"name": ""},
        ],
    },
    {
        "name": "generate_strategy",
        "fn": generate_strategy,
        "args": {"name": "MA Cross Test", "description": "moving average crossover"},
        "expected_keywords": ["strategy", "//@version=6", "ta.ema"],
        "expected_sections": ["generated", "validated", "template"],
        "bad_args": [
            {"name": ""},
        ],
    },
    {
        "name": "lookup_and_correct",
        "fn": lookup_and_correct,
        "args": {"code": "ema(close, 14)", "error_description": "calculate EMA"},
        "expected_keywords": ["lookup", "correct", "ta.ema"],
        "expected_sections": ["report", "fixes", "before", "after"],
        "bad_args": [
            {"code": "", "error_description": "test"},
            {"code": "test", "error_description": ""},
        ],
    },
    {
        "name": "debug_pine_facade",
        "fn": debug_pine_facade,
        "args": {"code": '//@version=6\nindicator("test")\nplot(close)'},
        "expected_keywords": ["debug", "pine-facade", "circuit"],
        "expected_sections": ["normalized", "raw response", "circuit breaker"],
        "bad_args": [
            {"code": ""},
        ],
    },
    {
        "name": "suggest_functions",
        "fn": suggest_functions,
        "args": {"context": "calculate moving average"},
        "expected_keywords": ["moving", "average", "suggested"],
        "expected_sections": ["syntax", "returns"],
        "bad_args": [
            {"context": "xyznonexistent12345"},
        ],
    },
    {
        "name": "get_namespace_cheatsheet",
        "fn": get_namespace_cheatsheet,
        "args": {"namespace": "math"},
        "expected_keywords": ["math", "cheatsheet", "entries"],
        "expected_sections": ["function", "variable", "constant"],
        "bad_args": [
            {"namespace": "nonexistent_namespace_xyz"},
        ],
    },
    {
        "name": "validate_file",
        "fn": validate_file,
        "args": {"file_path": "/Users/fractalyst/Documents/Quantify - Deeptest/Strategies/DCA.ps"},
        "expected_keywords": ["file", "valid", "pine"],
        "expected_sections": ["compiler", "errors", "lines"],
        "bad_args": [
            {"file_path": ""},
            {"file_path": "/nonexistent/path/test.ps"},
            {"file_path": "/etc/passwd"},
        ],
    },
]


async def main():
    print("=" * 130)
    print("PineScript MCP v4.0 — Comprehensive 20-Tool Benchmark")
    print("=" * 130)
    print(f"Python: {sys.version.split()[0]}")
    print(f"Working dir: {os.getcwd()}")
    print(f"DB path: {os.getenv('PINESCRIPT_DB_PATH', 'pinescript_db (default)')}")
    print()

    # ── Warm up ──
    print("Warming up (calling search_docs('ema') once)...")
    warmup_t0 = time.perf_counter()
    await search_docs(query="ema", n_results=1)
    warmup_ms = (time.perf_counter() - warmup_t0) * 1000
    print(f"Warmup complete: {warmup_ms:.1f}ms")
    print()

    # ── Run benchmarks ──
    results = []
    for i, test in enumerate(TOOL_TESTS):
        name = test["name"]
        fn = test["fn"]
        args = test["args"]
        n = len(TOOL_TESTS)
        print(f"[{i+1:2d}/{n}] {name:30s} ", end="", flush=True)

        # 1. Latency: 3 runs, take median
        try:
            latencies = await time_tool(fn, args, n_runs=3)
            med_ms = statistics.median(latencies)
            min_ms = min(latencies)
            max_ms = max(latencies)
            spd = speed_score(med_ms)
            print(f"lat={med_ms:8.1f}ms ({min_ms:.0f}-{max_ms:.0f}) ", end="", flush=True)
        except Exception as e:
            med_ms = min_ms = max_ms = float("inf")
            spd = 1
            print(f"lat=  FAILED  ", end="", flush=True)
            traceback.print_exc()
            # Still try to score accuracy etc with a single call
            latencies = []

        # 2. Accuracy: check result content
        try:
            result = await fn(**args)
            acc = accuracy_score(result, test["expected_keywords"])
            comp = completeness_score(result, test["expected_sections"])
        except Exception as e:
            result = f"ERROR: {e}"
            acc = 1
            comp = 1

        # 3. Error handling
        try:
            err = await error_handling_score(fn, args, test["bad_args"])
        except Exception:
            err = 1

        print(f"spd={spd} acc={acc} comp={comp} err={err}  avg={((spd+acc+comp+err)/4):.1f}")

        results.append({
            "name": name,
            "median_ms": med_ms,
            "min_ms": min_ms,
            "max_ms": max_ms,
            "speed": spd,
            "accuracy": acc,
            "completeness": comp,
            "error_handling": err,
            "overall": round((spd + acc + comp + err) / 4, 2),
            "result_preview": result[:200] if result else "",
        })

    # ── Print results table ──
    print()
    print("=" * 130)
    print("BENCHMARK RESULTS")
    print("=" * 130)
    header = (
        f"{'#':>2} {'Tool':<28} {'Median':>9} {'Min':>9} {'Max':>9} "
        f"{'Speed':>6} {'Acc':>5} {'Comp':>5} {'Err':>5} {'Overall':>8} {'Flag':>6}"
    )
    print(header)
    print("-" * 130)

    flagged = []
    for i, r in enumerate(results):
        # Flag any dimension below 9
        flags = []
        if r["speed"] < 9:           flags.append(f"spd={r['speed']}")
        if r["accuracy"] < 9:        flags.append(f"acc={r['accuracy']}")
        if r["completeness"] < 9:    flags.append(f"comp={r['completeness']}")
        if r["error_handling"] < 9:  flags.append(f"err={r['error_handling']}")
        flag_str = ",".join(flags) if flags else ""

        if r["overall"] < 9:
            flagged.append(r["name"])

        med_str = f"{r['median_ms']:.1f}ms" if r['median_ms'] != float('inf') else "FAILED"
        min_str = f"{r['min_ms']:.0f}ms" if r['min_ms'] != float('inf') else "-"
        max_str = f"{r['max_ms']:.0f}ms" if r['max_ms'] != float('inf') else "-"

        print(
            f"{i+1:2d} {r['name']:<28} {med_str:>9} {min_str:>9} {max_str:>9} "
            f"{r['speed']:>6} {r['accuracy']:>5} {r['completeness']:>5} {r['error_handling']:>5} "
            f"{r['overall']:>8.2f} {flag_str:>6}"
        )

    print("-" * 130)

    # ── Summary statistics ──
    valid_results = [r for r in results if r["median_ms"] != float("inf")]
    if valid_results:
        avg_latency = statistics.mean(r["median_ms"] for r in valid_results)
        p50_latency = statistics.median(r["median_ms"] for r in valid_results)
        p99_latency = max(r["median_ms"] for r in valid_results)
        avg_overall = statistics.mean(r["overall"] for r in results)
        avg_speed = statistics.mean(r["speed"] for r in results)
        avg_accuracy = statistics.mean(r["accuracy"] for r in results)
        avg_completeness = statistics.mean(r["completeness"] for r in results)
        avg_error = statistics.mean(r["error_handling"] for r in results)

        print()
        print("AGGREGATE STATISTICS")
        print(f"  Tools tested:       {len(results)}")
        print(f"  Avg latency:        {avg_latency:.1f}ms")
        print(f"  Median latency:     {p50_latency:.1f}ms")
        print(f"  Worst latency:      {p99_latency:.1f}ms")
        print(f"  Avg speed score:    {avg_speed:.2f}/10")
        print(f"  Avg accuracy:       {avg_accuracy:.2f}/10")
        print(f"  Avg completeness:   {avg_completeness:.2f}/10")
        print(f"  Avg error handling: {avg_error:.2f}/10")
        print(f"  Avg overall:        {avg_overall:.2f}/10")

    # ── Flagged tools (below 9 in any dimension) ──
    below_nine = []
    for r in results:
        issues = []
        if r["speed"] < 9:           issues.append(f"speed={r['speed']}")
        if r["accuracy"] < 9:        issues.append(f"accuracy={r['accuracy']}")
        if r["completeness"] < 9:    issues.append(f"completeness={r['completeness']}")
        if r["error_handling"] < 9:  issues.append(f"error_handling={r['error_handling']}")
        if issues:
            below_nine.append((r["name"], issues))

    print()
    if below_nine:
        print(f"TOOLS SCORING BELOW 9 IN ANY DIMENSION ({len(below_nine)}/{len(results)}):")
        for name, issues in below_nine:
            print(f"  {name}: {', '.join(issues)}")
    else:
        print("ALL TOOLS SCORE 9+ IN EVERY DIMENSION.")

    # ── Latency ranking ──
    print()
    print("LATENCY RANKING (fastest to slowest):")
    sorted_results = sorted(results, key=lambda r: r["median_ms"])
    for i, r in enumerate(sorted_results):
        med_str = f"{r['median_ms']:.1f}ms" if r['median_ms'] != float('inf') else "FAILED"
        print(f"  {i+1:2d}. {r['name']:<28} {med_str:>12}")

    print()
    print("=" * 130)
    print("Benchmark complete.")


if __name__ == "__main__":
    asyncio.run(main())
