#!/usr/bin/env python3
"""
Comprehensive benchmark of all 20 tools in the PineScript MCP server.

Calls each tool directly (bypassing MCP transport), measures latency over 3 runs,
tests both success and failure paths, and scores each tool on a 1-10 rubric.
"""

import asyncio
import os
import sys
import time
import statistics

# ── Bootstrap ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Must import the module to get access to the tool functions.
# The module-level code initializes ChromaDB and the embedding model on import,
# but the lifespan startup (which preloads caches) only runs when the MCP server
# starts. We'll call the initialization manually.
import pinescript_mcp as pm

# ── Helpers ──────────────────────────────────────────────────────────────────

# Valid PineScript code snippets for validation tools
VALID_CODE = '''//@version=6
indicator("Test", overlay=true)
src = input.source(close, "Source")
len = input.int(14, "Length", minval=1)
ma = ta.ema(src, len)
plot(ma, "EMA", color.orange)
'''

INVALID_CODE = '''//@version=6
indicator("Test")
x = ema(close, 14)
y = ta.sma(close
plot(undeclared_var)
'''

V5_CODE = '''//@version=5
indicator("Old Script")
myEma = ema(close, 14)
plot(myEma)
'''


class BenchResult:
    __slots__ = ('tool', 'category', 'test_input', 'latencies_ms', 'min_ms',
                 'avg_ms', 'max_ms', 'success', 'response_len', 'response_preview',
                 'error', 'correctness', 'speed_score', 'error_handling', 'quality', 'total')

    def __init__(self):
        self.tool = ""
        self.category = ""
        self.test_input = ""
        self.latencies_ms = []
        self.min_ms = 0
        self.avg_ms = 0
        self.max_ms = 0
        self.success = False
        self.response_len = 0
        self.response_preview = ""
        self.error = None
        self.correctness = 0
        self.speed_score = 0
        self.error_handling = 0
        self.quality = 0
        self.total = 0


async def bench_tool(func, kwargs, label, n_runs=3):
    """Run a tool function n_runs times, return BenchResult."""
    r = BenchResult()
    r.tool = func.__name__
    r.test_input = label

    for _ in range(n_runs):
        t0 = time.perf_counter()
        try:
            result = await func(**kwargs)
            elapsed = (time.perf_counter() - t0) * 1000
            r.latencies_ms.append(elapsed)
            r.success = True
            if isinstance(result, str):
                r.response_len = len(result)
                r.response_preview = result[:200]
            else:
                r.response_len = 0
                r.response_preview = str(result)[:200]
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            r.latencies_ms.append(elapsed)
            r.success = False
            r.error = str(e)[:200]

    if r.latencies_ms:
        r.min_ms = min(r.latencies_ms)
        r.avg_ms = statistics.mean(r.latencies_ms)
        r.max_ms = max(r.latencies_ms)

    return r


def speed_score(avg_ms):
    if avg_ms < 1:
        return 3
    elif avg_ms < 100:
        return 2
    elif avg_ms < 1000:
        return 1
    return 0


def assess_correctness(result: BenchResult, expected_substrings=None):
    """Score correctness 0-3 based on response content."""
    text = result.response_preview.lower()
    if not result.success and result.error:
        return 0
    if "error" in text and "not found" in text:
        # Some tools legitimately return "not found" for bad queries
        return 1
    if expected_substrings:
        matches = sum(1 for s in expected_substrings if s.lower() in text)
        if matches >= 2:
            return 3
        elif matches == 1:
            return 2
    if result.response_len > 50:
        return 2
    return 1


def assess_error_handling(result: BenchResult):
    """Score error handling 0-2: does the tool handle bad input gracefully?"""
    if result.error and "Traceback" in str(result.error):
        return 0
    if result.error:
        return 1
    if result.response_preview and ("error" in result.response_preview.lower() or
                                     "not found" in result.response_preview.lower() or
                                     "no " in result.response_preview.lower()):
        return 2
    return 1


def assess_quality(result: BenchResult):
    """Score response quality 0-2: well-formatted, useful content."""
    text = result.response_preview
    if not text:
        return 0
    has_structure = any(c in text for c in ["─", "═", "```", "[1]", "•", "—", ":"])
    has_content = result.response_len > 100
    if has_structure and has_content:
        return 2
    elif has_content:
        return 1
    return 0


# ── Main benchmark ───────────────────────────────────────────────────────────

async def main():
    print("=" * 120)
    print("PineScript MCP Server — 20-Tool Comprehensive Benchmark")
    print("=" * 120)
    print()

    # Warm up: ensure model and ChromaDB are loaded (same as lifespan startup)
    print("Warming up: loading embedding model + ChromaDB + name index + hot cache...")
    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(pm._model_executor, pm._get_model)
    pm._embedding_model_ready.set()
    pm._get_collection()
    pm._build_name_index()
    await pm.build_hot_cache()
    warmup_ms = (time.perf_counter() - t0) * 1000
    print(f"Warmup complete in {warmup_ms:.0f}ms")
    print()

    results: list[BenchResult] = []

    # ── LOOKUP tools (6) ─────────────────────────────────────────────────────

    # 1. get_function
    for name, label in [("ta.ema", "valid:ta.ema"), ("strategy.entry", "valid:strategy.entry"),
                        ("nonexistent_function", "fail:nonexistent")]:
        r = await bench_tool(pm.get_function, {"name": name}, label)
        r.category = "LOOKUP"
        r.correctness = assess_correctness(r, ["ema", "syntax"] if "valid" in label else None)
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = 2 if "not found" in r.response_preview.lower() or "no " in r.response_preview.lower() or r.success else 0
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # 2. get_variable
    for name, label in [("close", "valid:close"), ("barstate.isconfirmed", "valid:barstate.isconfirmed")]:
        r = await bench_tool(pm.get_variable, {"name": name}, label)
        r.category = "LOOKUP"
        r.correctness = assess_correctness(r, [name])
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = assess_error_handling(r)
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # 3. get_type
    for name, label in [("array", "valid:array"), ("map", "valid:map")]:
        r = await bench_tool(pm.get_type, {"name": name}, label)
        r.category = "LOOKUP"
        r.correctness = assess_correctness(r, [name])
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = assess_error_handling(r)
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # 4. get_constant
    for name, label in [("color.red", "valid:color.red"), ("strategy.long", "valid:strategy.long")]:
        r = await bench_tool(pm.get_constant, {"name": name}, label)
        r.category = "LOOKUP"
        r.correctness = assess_correctness(r, [name])
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = assess_error_handling(r)
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # 5. get_keyword
    for name, label in [("var", "valid:var"), ("if", "valid:if")]:
        r = await bench_tool(pm.get_keyword, {"name": name}, label)
        r.category = "LOOKUP"
        r.correctness = assess_correctness(r, [name])
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = assess_error_handling(r)
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # 6. get_operator
    for name, label in [(":=", "valid:=assign"), ("+", "valid:+add")]:
        r = await bench_tool(pm.get_operator, {"name": name}, label)
        r.category = "LOOKUP"
        r.correctness = assess_correctness(r, [name if name != ":=" else "assignment", "operator"])
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = assess_error_handling(r)
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # ── SEARCH tools (4) ─────────────────────────────────────────────────────

    # 7. search_docs
    for query, label in [("moving average", "valid:moving average"),
                         ("how to detect crossover", "valid:crossover")]:
        r = await bench_tool(pm.search_docs, {"query": query}, label)
        r.category = "SEARCH"
        r.correctness = assess_correctness(r, ["moving", "average"] if "moving" in label else ["crossover"])
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = assess_error_handling(r)
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # 8. get_examples
    r = await bench_tool(pm.get_examples, {"query": "strategy with stop loss"}, "valid:strategy stop loss")
    r.category = "SEARCH"
    r.correctness = assess_correctness(r, ["strategy", "stop"])
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 9. search_by_return_type
    r = await bench_tool(pm.search_by_return_type, {"return_type": "series float"}, "valid:series float")
    r.category = "SEARCH"
    r.correctness = assess_correctness(r, ["series float", "float"])
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 10. list_namespace
    for ns, label in [("ta", "valid:ta"), ("strategy", "valid:strategy")]:
        r = await bench_tool(pm.list_namespace, {"namespace": ns}, label)
        r.category = "SEARCH"
        r.correctness = assess_correctness(r, [ns])
        r.speed_score = speed_score(r.avg_ms)
        r.error_handling = assess_error_handling(r)
        r.quality = assess_quality(r)
        r.total = r.correctness + r.speed_score + r.error_handling + r.quality
        results.append(r)

    # ── VALIDATE tools (5) ───────────────────────────────────────────────────

    # 11. validate_syntax
    r = await bench_tool(pm.validate_syntax, {"code": VALID_CODE}, "valid:compiles clean")
    r.category = "VALIDATE"
    r.correctness = 3 if "valid" in r.response_preview.lower() else 1
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    r = await bench_tool(pm.validate_syntax, {"code": INVALID_CODE}, "fail:bad code")
    r.category = "VALIDATE"
    r.correctness = 3 if "error" in r.response_preview.lower() or "issue" in r.response_preview.lower() else 1
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = 2  # Should gracefully report errors
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 12. validate_and_explain
    r = await bench_tool(pm.validate_and_explain, {"code": VALID_CODE}, "valid:compiles clean")
    r.category = "VALIDATE"
    r.correctness = 3 if "passed" in r.response_preview.lower() or "valid" in r.response_preview.lower() else 1
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    r = await bench_tool(pm.validate_and_explain, {"code": INVALID_CODE}, "fail:code with errors")
    r.category = "VALIDATE"
    r.correctness = 3 if "error" in r.response_preview.lower() or "fail" in r.response_preview.lower() else 1
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = 2
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 13. fix_and_validate
    r = await bench_tool(pm.fix_and_validate, {
        "code": "//@version=6\nindicator(\"test\")\nx = ema(close, 14)\nplot(x)",
        "error_description": "Undeclared identifier 'ema'"
    }, "fix:missing ta. namespace")
    r.category = "VALIDATE"
    r.correctness = 3 if "ta.ema" in r.response_preview else (2 if "fix" in r.response_preview.lower() else 1)
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = 2
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 14. debug_pine_facade
    r = await bench_tool(pm.debug_pine_facade, {"code": VALID_CODE}, "valid:simple valid code")
    r.category = "VALIDATE"
    r.correctness = 3 if "success" in r.response_preview.lower() or "debug" in r.response_preview.lower() else 1
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 15. validate_file
    dca_path = "/Users/fractalyst/Documents/Quantify - Deeptest/Strategies/DCA.ps"
    r = await bench_tool(pm.validate_file, {"file_path": dca_path}, "file:DCA.ps")
    r.category = "VALIDATE"
    r.correctness = 3 if r.response_len > 50 else 1
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # ── CODEGEN tools (3) ────────────────────────────────────────────────────

    # 16. generate_indicator
    r = await bench_tool(pm.generate_indicator, {
        "name": "RSI Indicator",
        "description": "relative strength index"
    }, "gen:RSI indicator")
    r.category = "CODEGEN"
    r.correctness = 3 if "ta.rsi" in r.response_preview and "version=6" in r.response_preview else (2 if "indicator" in r.response_preview.lower() else 1)
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 17. generate_strategy
    r = await bench_tool(pm.generate_strategy, {"name": "MA Crossover"}, "gen:MA Crossover strategy")
    r.category = "CODEGEN"
    r.correctness = 3 if "strategy(" in r.response_preview and "version=6" in r.response_preview else (2 if "strategy" in r.response_preview.lower() else 1)
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 18. lookup_and_correct
    r = await bench_tool(pm.lookup_and_correct, {
        "code": "//@version=6\nindicator(\"test\")\nmyEma = ema(close, 14)\nplot(myEma)",
        "error_description": "Calculate EMA of close price"
    }, "fix:v5 ema -> ta.ema")
    r.category = "CODEGEN"
    r.correctness = 3 if "ta.ema" in r.response_preview else (2 if "fix" in r.response_preview.lower() or "namespace" in r.response_preview.lower() else 1)
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = 2
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # ── CONTEXT tools (2) ────────────────────────────────────────────────────

    # 19. suggest_functions
    r = await bench_tool(pm.suggest_functions, {"context": "detect trend direction"}, "ctx:detect trend")
    r.category = "CONTEXT"
    r.correctness = 3 if r.response_len > 100 and "suggest" in r.response_preview.lower() else (2 if r.response_len > 50 else 1)
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # 20. get_namespace_cheatsheet
    r = await bench_tool(pm.get_namespace_cheatsheet, {"namespace": "ta"}, "ctx:ta cheatsheet")
    r.category = "CONTEXT"
    r.correctness = 3 if r.response_len > 200 and "ta." in r.response_preview else (2 if r.response_len > 50 else 1)
    r.speed_score = speed_score(r.avg_ms)
    r.error_handling = assess_error_handling(r)
    r.quality = assess_quality(r)
    r.total = r.correctness + r.speed_score + r.error_handling + r.quality
    results.append(r)

    # ── Print results ────────────────────────────────────────────────────────

    print()
    print("=" * 140)
    print(f"{'#':<3} {'CATEGORY':<10} {'TOOL':<28} {'TEST INPUT':<28} {'MIN':>8} {'AVG':>8} {'MAX':>8} {'TIER':<6} {'COR':>3} {'SPD':>3} {'ERR':>3} {'QTY':>3} {'TOTAL':>5}")
    print("-" * 140)

    tier_map = {3: "sub-ms", 2: "<100ms", 1: "<1s", 0: ">1s"}
    total_score = 0
    max_score = 0

    # Group by category
    for cat in ["LOOKUP", "SEARCH", "VALIDATE", "CODEGEN", "CONTEXT"]:
        cat_results = [r for r in results if r.category == cat]
        for i, r in enumerate(cat_results):
            tier = tier_map.get(r.speed_score, "?")
            print(f"{'':<3} {r.category:<10} {r.tool:<28} {r.test_input:<28} "
                  f"{r.min_ms:>7.2f}ms {r.avg_ms:>7.2f}ms {r.max_ms:>7.2f}ms "
                  f"{tier:<6} {r.correctness:>3} {r.speed_score:>3} {r.error_handling:>3} {r.quality:>3} {r.total:>5}")
            total_score += r.total
            max_score += 10

    print("-" * 140)

    # ── Per-tool summary (unique tools only, averaged over their test cases) ──
    print()
    print("=" * 140)
    print("PER-TOOL SUMMARY (aggregated across test cases)")
    print("=" * 140)
    print(f"{'TOOL':<30} {'CATEGORY':<10} {'AVG LATENCY':>12} {'MIN':>10} {'MAX':>10} {'TESTS':>5} {'AVG SCORE':>10} {'GRADE':>6}")
    print("-" * 140)

    tool_names_unique = []
    seen = set()
    for r in results:
        if r.tool not in seen:
            seen.add(r.tool)
            tool_names_unique.append(r.tool)

    for tool_name in tool_names_unique:
        tool_results = [r for r in results if r.tool == tool_name]
        avg_lat = statistics.mean([r.avg_ms for r in tool_results])
        min_lat = min([r.min_ms for r in tool_results])
        max_lat = max([r.max_ms for r in tool_results])
        avg_score = statistics.mean([r.total for r in tool_results])
        n_tests = len(tool_results)
        cat = tool_results[0].category

        grade = "A" if avg_score >= 9 else "B" if avg_score >= 7 else "C" if avg_score >= 5 else "D" if avg_score >= 3 else "F"

        print(f"{tool_name:<30} {cat:<10} {avg_lat:>10.2f}ms {min_lat:>8.2f}ms {max_lat:>8.2f}ms {n_tests:>5} {avg_score:>10.1f} {grade:>6}")

    print("-" * 140)

    # ── Response quality spot-check ───────────────────────────────────────────
    print()
    print("=" * 140)
    print("RESPONSE QUALITY SPOT-CHECK (first 200 chars of each tool's first test)")
    print("=" * 140)
    seen_tools = set()
    for r in results:
        if r.tool in seen_tools:
            continue
        seen_tools.add(r.tool)
        print(f"\n--- {r.tool} ({r.test_input}) ---")
        if r.error:
            print(f"  ERROR: {r.error}")
        else:
            preview = r.response_preview.replace("\n", "\\n")[:300]
            print(f"  [{r.response_len} chars] {preview}")

    # ── Final summary ────────────────────────────────────────────────────────
    print()
    print("=" * 140)
    overall_avg = total_score / max_score * 10 if max_score else 0
    all_lats = [r.avg_ms for r in results]
    print(f"OVERALL: {total_score}/{max_score} points ({overall_avg:.1f}/10 avg)")
    print(f"Latency range: {min(all_lats):.2f}ms - {max(all_lats):.2f}ms (median: {statistics.median(all_lats):.2f}ms)")
    print(f"Tests: {len(results)} total across {len(tool_names_unique)} unique tools")

    # Grade distribution
    grade_counts = {}
    for tool_name in tool_names_unique:
        tool_results = [r for r in results if r.tool == tool_name]
        avg_score = statistics.mean([r.total for r in tool_results])
        grade = "A" if avg_score >= 9 else "B" if avg_score >= 7 else "C" if avg_score >= 5 else "D" if avg_score >= 3 else "F"
        grade_counts[grade] = grade_counts.get(grade, 0) + 1

    print(f"Grade distribution: " + " | ".join(f"{g}: {c}" for g, c in sorted(grade_counts.items())))
    print("=" * 140)


if __name__ == "__main__":
    asyncio.run(main())
