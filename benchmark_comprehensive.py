#!/usr/bin/env python3
"""
Comprehensive benchmark of all 20 MCP tools in the PineScript MCP server.

Scores each tool on 4 dimensions (0-10 each):
  1. Correctness   -- Returns accurate, non-misleading results
  2. Completeness  -- Returns all relevant data (not truncated)
  3. Error Handling -- Handles edge cases gracefully
  4. Speed         -- Response time is reasonable bounds

Total: 40 per tool, Overall: 800 max

Usage:
  /Users/fractalyst/pinescript_mcp/.venv/bin/python benchmark_comprehensive.py
"""

import asyncio
import os
import sys
import time
import statistics

# -- Bootstrap ----------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pinescript_mcp as pm

from fastmcp.exceptions import ToolError

# -- Test Data -----------------------------------------------------------------

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

BROKEN_CODE = '''//@version=6
indicator("test")
myEma = ema(close, 14)
plot(myEma)
'''

# -- Scoring helpers --------------------------------------------------------------

def score_correctness(result_text: str, success: bool, error: str | None,
                       expected_present: list[str] | None = None,
                       expected_absent: list[str] | None = None) -> int:
    """Score 0-10: Does the tool return accurate, non-misleading results?"""
    if error:
        if "Traceback" in str(error) or "NameError" in str(error) or "UnboundLocalError" in str(error):
            return 0  # Crash
        return 2  # Raised exception but caught

    text = result_text.lower() if result_text else ""

    # Check for runtime crash indicators leaking into output
    crash_indicators = ["name 'lines' is not defined", "unboundlocalerror",
                        "attributeerror", "traceback (most recent call last)"]
    for ci in crash_indicators:
        if ci.lower() in text:
            return 0

    if not result_text or len(result_text) < 20:
        return 1

    # Expected substrings present
    present_score = 0
    if expected_present:
        matches = sum(1 for s in expected_present if s.lower() in text)
        present_score = matches / len(expected_present)

    # Expected substrings absent
    absent_penalty = 0
    if expected_absent:
        violations = sum(1 for s in expected_absent if s.lower() in text)
        absent_penalty = violations / len(expected_absent)

    score = 4  # base for having non-empty output

    if present_score >= 0.66:
        score += 3
    elif present_score > 0:
        score += 1

    if absent_penalty > 0:
        score -= 3 * absent_penalty

    # Bonus for detailed, structured responses
    if any(c in result_text for c in ["SYNTAX", "PARAMETERS", "DESCRIPTION", "RETURNS", "EXAMPLES"]):
        score += 1
    if len(result_text) > 200:
        score += 1

    return max(0, min(10, round(score)))


def score_completeness(result_text: str, success: bool, error: str | None,
                       min_expected_length: int = 100) -> int:
    """Score 0-10: Does the tool return all relevant data without truncation?"""
    if error:
        return 2

    if not result_text or len(result_text) < 10:
        return 0

    text = result_text.lower()
    score = 3

    # Length-based scoring
    if len(result_text) >= min_expected_length:
        score += 2
    if len(result_text) >= min_expected_length * 3:
        score += 1

    # Check for truncation markers
    if "truncated" in text and "omitted" in text:
        score -= 2

    # Rich content indicators
    rich_indicators = ["example", "parameter", "return", "syntax", "description",
                       "remark", "see also"]
    rich_count = sum(1 for i in rich_indicators if i in text)
    score += min(3, rich_count)

    # Actual code examples present
    if "```" in result_text or "plot(" in result_text or "ta." in result_text:
        score += 1

    return max(0, min(10, score))


def score_error_handling(result_text: str, success: bool, error: str | None,
                         is_edge_case: bool = False) -> int:
    """Score 0-10: Does the tool handle edge cases gracefully?"""
    if error:
        if "Traceback" in str(error) or "NameError" in str(error):
            return 1
        return 4

    text = result_text.lower() if result_text else ""
    if not text:
        return 2

    good_indicators = ["not found", "did you mean", "suggestion", "no result",
                      "error", "please", "try", "hint", "fix"]
    bad_indicators = ["traceback", "exception:", "unhandled", "nameerror",
                     "attributeerror", "unboundlocalerror", "name 'lines'"]

    good_score = sum(1 for i in good_indicators if i in text)
    bad_score = sum(1 for i in bad_indicators if i in text)

    result = 5  # base

    if is_edge_case:
        if good_score >= 2:
            result += 3
        elif good_score >= 1:
            result += 1
        if bad_score > 0:
            result -= 4
    else:
        if success and bad_score == 0:
            result += 3
        elif success:
            result += 1
        if good_score >= 1 and not success:
            result += 1

    return max(0, min(10, result))


def score_speed(avg_ms: float, is_validation: bool = False) -> int:
    """Score 0-10: Is response time reasonable?"""
    if is_validation:
        if avg_ms < 500:   return 10
        elif avg_ms < 1000:  return 9
        elif avg_ms < 2000:  return 8
        elif avg_ms < 3000:  return 7
        elif avg_ms < 5000:  return 5
        elif avg_ms < 10000: return 3
        return 1
    else:
        if avg_ms < 1:     return 10
        elif avg_ms < 10:    return 9
        elif avg_ms < 50:    return 8
        elif avg_ms < 100:   return 7
        elif avg_ms < 500:   return 6
        elif avg_ms < 1000:  return 4
        elif avg_ms < 3000:  return 2
        return 1


# -- Benchmark runner --------------------------------------------------------------

class ToolResult:
    __slots__ = ('tool_name', 'test_label', 'category', 'latencies_ms', 'avg_ms',
                 'min_ms', 'max_ms', 'p50_ms', 'success', 'result_text', 'error',
                 'correctness', 'completeness', 'error_handling', 'speed', 'overall',
                 'issues')

    def __init__(self):
        self.tool_name = ""
        self.test_label = ""
        self.category = ""
        self.latencies_ms = []
        self.avg_ms = 0.0
        self.min_ms = 0.0
        self.max_ms = 0.0
        self.p50_ms = 0.0
        self.success = False
        self.result_text = ""
        self.error = None
        self.correctness = 0
        self.completeness = 0
        self.error_handling = 0
        self.speed = 0
        self.overall = 0
        self.issues: list[str] = []


async def run_tool(func, kwargs: dict, label: str, n_runs: int = 3) -> ToolResult:
    """Run a tool function n_runs times and return aggregated results."""
    r = ToolResult()
    r.tool_name = func.__name__
    r.test_label = label

    for run_idx in range(n_runs):
        t0 = time.perf_counter()
        try:
            result = await func(**kwargs)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            r.latencies_ms.append(elapsed_ms)
            r.success = True
            r.result_text = str(result) if result else ""
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            r.latencies_ms.append(elapsed_ms)
            r.success = False
            r.error = str(e)
            if not r.result_text:
                r.result_text = f"EXCEPTION: {type(e).__name__}: {str(e)[:500]}"

    if r.latencies_ms:
        r.min_ms = min(r.latencies_ms)
        r.avg_ms = statistics.mean(r.latencies_ms)
        r.max_ms = max(r.latencies_ms)
        sorted_lats = sorted(r.latencies_ms)
        r.p50_ms = sorted_lats[len(sorted_lats) // 2]

    return r


async def main():
    print("=" * 160)
    print("PineScript MCP Server -- 20-Tool Comprehensive Benchmark")
    print("Scoring: Correctness(0-10) | Completeness(0-10) | Error Handling(0-10) | Speed(0-10) | Overall(0-40)")
    print("=" * 160)

    # -- Warmup ----------------------------------------------------------------
    print("\nWarmup: loading embedding model + ChromaDB + name index + hot cache...")
    t0 = time.perf_counter()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(pm._model_executor, pm._get_model)
    pm._embedding_model_ready.set()
    pm._get_collection()
    pm._build_name_index()
    await pm.build_hot_cache()
    warmup_ms = (time.perf_counter() - t0) * 1000
    print(f"Warmup complete: {warmup_ms:.0f}ms\n")

    # -- Define all 20 test cases ------------------------------------------------
    # Each tuple: (func, kwargs, label, category, is_validation, scoring_hints)
    # scoring_hints: dict with expected_present, expected_absent, min_expected_length

    tests: list[tuple] = []

    # 1. get_function("ta.ema")
    tests.append((
        pm.get_function, {"name": "ta.ema"},
        "get_function('ta.ema')", "LOOKUP", False,
        {"expected_present": ["ema", "source", "length", "syntax"],
         "expected_absent": ["not found"]}
    ))

    # 2. get_variable("close")
    tests.append((
        pm.get_variable, {"name": "close"},
        "get_variable('close')", "LOOKUP", False,
        {"expected_present": ["close", "price"],
         "expected_absent": ["not found"]}
    ))

    # 3. get_type("array")
    tests.append((
        pm.get_type, {"name": "array"},
        "get_type('array')", "LOOKUP", False,
        {"expected_present": ["array"],
         "expected_absent": ["not found"]}
    ))

    # 4. get_constant("color.red")
    tests.append((
        pm.get_constant, {"name": "color.red"},
        "get_constant('color.red')", "LOOKUP", False,
        {"expected_present": ["color", "red"],
         "expected_absent": ["not found"]}
    ))

    # 5. get_keyword("var")
    tests.append((
        pm.get_keyword, {"name": "var"},
        "get_keyword('var')", "LOOKUP", False,
        {"expected_present": ["var"],
         "expected_absent": ["not found"]}
    ))

    # 6. get_operator("=:=")
    tests.append((
        pm.get_operator, {"name": ":="},
        "get_operator(':=')", "LOOKUP", False,
        {"expected_present": [":=", "assign"],
         "expected_absent": []}
    ))

    # 7. search_docs("moving average crossover")
    tests.append((
        pm.search_docs, {"query": "moving average crossover"},
        "search_docs('moving average crossover')", "SEARCH", False,
        {"expected_present": ["moving", "average"],
         "expected_absent": []}
    ))

    # 8. get_examples("strategy entry with stop loss")
    tests.append((
        pm.get_examples, {"query": "strategy entry with stop loss"},
        "get_examples('strategy entry with stop loss')", "SEARCH", False,
        {"expected_present": ["strategy"],
         "expected_absent": []}
    ))

    # 9. search_by_return_type("series float")
    tests.append((
        pm.search_by_return_type, {"return_type": "series float"},
        "search_by_return_type('series float')", "SEARCH", False,
        {"expected_present": ["series float", "float"],
         "expected_absent": []}
    ))

    # 10. list_namespace("ta")
    tests.append((
        pm.list_namespace, {"namespace": "ta"},
        "list_namespace('ta')", "SEARCH", False,
        {"expected_present": ["ta.", "function"],
         "expected_absent": []}
    ))

    # 11. validate_syntax(valid code)
    tests.append((
        pm.validate_syntax, {"code": VALID_CODE},
        "validate_syntax(valid code)", "VALIDATE", True,
        {"expected_present": ["valid", "compiles"],
         "expected_absent": ["error", "issue"]}
    ))

    # 12. validate_syntax(invalid code)
    tests.append((
        pm.validate_syntax, {"code": INVALID_CODE},
        "validate_syntax(invalid code)", "VALIDATE", True,
        {"expected_present": ["error", "issue", "compilation"],
         "expected_absent": ["valid"]}
    ))

    # 13. validate_and_explain(valid code)
    tests.append((
        pm.validate_and_explain, {"code": VALID_CODE},
        "validate_and_explain(valid code)", "VALIDATE", True,
        {"expected_present": ["passed", "valid", "0"],
         "expected_absent": []}
    ))

    # 14. validate_and_explain(invalid code)
    tests.append((
        pm.validate_and_explain, {"code": INVALID_CODE},
        "validate_and_explain(invalid code)", "VALIDATE", True,
        {"expected_present": ["error", "failed", "issue"],
         "expected_absent": ["passed"]}
    ))

    # 15. fix_and_validate(broken code)
    tests.append((
        pm.fix_and_validate, {
            "code": BROKEN_CODE,
            "error_description": "Undeclared identifier 'ema'"
        },
        "fix_and_validate(missing ta. namespace)", "VALIDATE", True,
        {"expected_present": ["ta.ema", "namespace", "fix"],
         "expected_absent": []}
    ))

    # 16. generate_indicator("RSI Indicator", "relative strength index")
    tests.append((
        pm.generate_indicator, {
            "name": "RSI Indicator",
            "description": "relative strength index"
        },
        "generate_indicator('RSI Indicator')", "CODEGEN", True,
        {"expected_present": ["ta.rsi", "version=6", "indicator("],
         "expected_absent": []}
    ))

    # 17. generate_strategy("MA Crossover", "moving average crossover strategy")
    tests.append((
        pm.generate_strategy, {
            "name": "MA Crossover",
            "description": "moving average crossover strategy"
        },
        "generate_strategy('MA Crossover')", "CODEGEN", True,
        {"expected_present": ["strategy(", "version=6", "ta.ema"],
         "expected_absent": []}
    ))

    # 18. lookup_and_correct(broken code)
    tests.append((
        pm.lookup_and_correct, {
            "code": BROKEN_CODE,
            "error_description": "Calculate EMA of close price"
        },
        "lookup_and_correct(v5 ema -> ta.ema)", "CODEGEN", True,
        {"expected_present": ["ta.ema"],
         "expected_absent": []}
    ))

    # 19. suggest_functions("calculate moving average")
    tests.append((
        pm.suggest_functions, {"context": "calculate moving average"},
        "suggest_functions('calculate moving average')", "CONTEXT", False,
        {"expected_present": ["moving", "average", "suggest"],
         "expected_absent": []}
    ))

    # 20. get_namespace_cheatsheet("ta")
    tests.append((
        pm.get_namespace_cheatsheet, {"namespace": "ta"},
        "get_namespace_cheatsheet('ta')", "CONTEXT", False,
        {"expected_present": ["ta", "cheatsheet", "function"],
         "expected_absent": []}
    ))

    # -- Execute all tests --------------------------------------------------------
    print(f"Running {len(tests)} tool tests (3 runs each)...\n")
    print(f"{'#':>2} {'CATEGORY':<10} {'TOOL':<28} {'TEST':<40} {'AVG ms':>8} "
          f"{'CORR':>4} {'COMP':>4} {'ERR':>4} {'SPD':>4} {'TOTAL':>6}")
    print("-" * 160)

    results: list[tuple[ToolResult, dict]] = []
    all_issues: list[str] = []

    for idx, (func, kwargs, label, category, is_validation, hints) in enumerate(tests, 1):
        r = await run_tool(func, kwargs, label, n_runs=3)
        r.category = category

        # Score each dimension
        r.correctness = score_correctness(
            r.result_text, r.success, r.error,
            expected_present=hints.get("expected_present"),
            expected_absent=hints.get("expected_absent"),
        )
        r.completeness = score_completeness(
            r.result_text, r.success, r.error,
            min_expected_length=hints.get("min_expected_length", 100),
        )
        r.error_handling = score_error_handling(
            r.result_text, r.success, r.error,
            is_edge_case=("invalid" in label or "broken" in label),
        )
        r.speed = score_speed(r.avg_ms, is_validation=is_validation)
        r.overall = r.correctness + r.completeness + r.error_handling + r.speed

        # Detect issues
        if r.error:
            r.issues.append(f"CRASHED: {r.error[:120]}")
            all_issues.append(f"[{idx}] {func.__name__}: CRASH -- {r.error[:100]}")
        if r.correctness <= 3:
            r.issues.append(f"Low correctness: {r.correctness}/10")
            all_issues.append(f"[{idx}] {func.__name__} ({label}): Low correctness ({r.correctness}/10)")
        if r.completeness <= 3:
            r.issues.append(f"Low completeness: {r.completeness}/10")
            all_issues.append(f"[{idx}] {func.__name__} ({label}): Low completeness ({r.completeness}/10)")
        if r.avg_ms > 5000:
            r.issues.append(f"Slow: {r.avg_ms:.0f}ms")
            all_issues.append(f"[{idx}] {func.__name__} ({label}): Slow ({r.avg_ms:.0f}ms)")

        print(f"{idx:>2} {r.category:<10} {r.tool_name:<28} {r.test_label:<40} "
              f"{r.avg_ms:>7.1f} "
              f"{r.correctness:>4} {r.completeness:>4} {r.error_handling:>4} {r.speed:>4} "
              f"{r.overall:>5}/40")

        results.append((r, hints))

    # -- Detailed Results ---------------------------------------------------------
    print("\n" + "=" * 160)
    print("DETAILED RESULTS")
    print("=" * 160)
    for idx, (r, hints) in enumerate(results, 1):
        print(f"\n  [{idx}] {r.tool_name} -- {r.test_label}")
        print(f"      Latency: min={r.min_ms:.1f}ms  avg={r.avg_ms:.1f}ms  max={r.max_ms:.1f}ms  p50={r.p50_ms:.1f}ms")
        print(f"      Scores:  correctness={r.correctness}/10  completeness={r.completeness}/10  "
              f"error_handling={r.error_handling}/10  speed={r.speed}/10")
        print(f"      Overall: {r.overall}/40")
        if r.error:
            print(f"      ERROR: {r.error[:300]}")
        if r.issues:
            for iss in r.issues:
                print(f"      ISSUE: {iss}")
        preview = r.result_text[:400].replace("\n", "\\n") if r.result_text else "(empty)"
        print(f"      Preview ({len(r.result_text)} chars): {preview}")

    # -- Summary Table ------------------------------------------------------------
    print("\n" + "=" * 160)
    print("SUMMARY TABLE")
    print("=" * 160)
    print(f"{'#':<3} {'CATEGORY':<10} {'TOOL':<28} {'TEST':<40} {'AVG ms':>8} "
          f"{'CORR':>5} {'COMP':>5} {'ERR':>5} {'SPD':>5} {'TOTAL':>6}")
    print("-" * 160)

    total_overall = 0
    for idx, (r, _) in enumerate(results, 1):
        print(f"{idx:<3} {r.category:<10} {r.tool_name:<28} {r.test_label:<40} {r.avg_ms:>7.1f} "
              f"{r.correctness:>5} {r.completeness:>5} {r.error_handling:>5} {r.speed:>5} {r.overall:>6}")
        total_overall += r.overall

    print("-" * 160)
    max_possible = len(results) * 40
    print(f"\nOVERALL SCORE: {total_overall}/{max_possible} ({total_overall/max_possible*10:.1f}/10 avg)")

    # -- Category Breakdown -------------------------------------------------------
    print("\n" + "=" * 160)
    print("CATEGORY BREAKDOWN")
    print("=" * 160)
    categories = {}
    for r, _ in results:
        categories.setdefault(r.category, []).append(r)

    for cat, cat_results in categories.items():
        cat_total = sum(r.overall for r in cat_results)
        cat_max = len(cat_results) * 40
        cat_avg_ms = statistics.mean([r.avg_ms for r in cat_results])
        cat_avg_corr = statistics.mean([r.correctness for r in cat_results])
        cat_avg_comp = statistics.mean([r.completeness for r in cat_results])
        cat_avg_err = statistics.mean([r.error_handling for r in cat_results])
        cat_avg_spd = statistics.mean([r.speed for r in cat_results])
        print(f"  {cat:<12} {len(cat_results):>2} tools | "
              f"Score: {cat_total:>3}/{cat_max:>3} ({cat_total/cat_max*10:.1f}/10) | "
              f"Corr={cat_avg_corr:.1f} Comp={cat_avg_comp:.1f} Err={cat_avg_err:.1f} Spd={cat_avg_spd:.1f} | "
              f"Avg latency: {cat_avg_ms:.1f}ms")

    # -- Issues Found -------------------------------------------------------------
    if all_issues:
        print("\n" + "=" * 160)
        print("ISSUES FOUND")
        print("=" * 160)
        for issue in all_issues:
            print(f"  {issue}")
    else:
        print("\n  No issues found.")

    # -- Grade Distribution -------------------------------------------------------
    print("\n" + "=" * 160)
    print("GRADE DISTRIBUTION")
    print("=" * 160)

    def grade(score):
        pct = score / 40 * 100
        if pct >= 90: return "A"
        if pct >= 80: return "B"
        if pct >= 70: return "C"
        if pct >= 60: return "D"
        return "F"

    grade_counts = {}
    for r, _ in results:
        g = grade(r.overall)
        grade_counts[g] = grade_counts.get(g, 0) + 1
    print("  " + " | ".join(f"{g}: {c}" for g, c in sorted(grade_counts.items())))

    # -- Latency Statistics -------------------------------------------------------
    print("\n" + "=" * 160)
    print("LATENCY STATISTICS")
    print("=" * 160)
    all_avg_ms = [r.avg_ms for r, _ in results]
    print(f"  Min avg: {min(all_avg_ms):.1f}ms")
    print(f"  Max avg: {max(all_avg_ms):.1f}ms")
    print(f"  Median avg: {statistics.median(all_avg_ms):.1f}ms")
    print(f"  Mean avg: {statistics.mean(all_avg_ms):.1f}ms")

    fast_tools = [r for r, _ in results if r.avg_ms < 50]
    slow_tools = [r for r, _ in results if r.avg_ms > 2000]
    print(f"  Fast (<50ms): {len(fast_tools)} tools")
    print(f"  Slow (>2s): {len(slow_tools)} tools")
    if slow_tools:
        for r in slow_tools:
            print(f"    - {r.tool_name}: {r.avg_ms:.0f}ms ({r.test_label})")

    print("\n" + "=" * 160)
    print("BENCHMARK COMPLETE")
    print("=" * 160)


if __name__ == "__main__":
    asyncio.run(main())
