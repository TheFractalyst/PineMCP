#!/usr/bin/env python3
"""E2E test: call all 21 tools + 1 resource via FastMCP in-process."""
import asyncio
import os
import sys
import tempfile
import time

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from server import mcp  # noqa: E402


async def main():
    tools = await mcp._list_tools()
    resources = await mcp._list_resources()
    tool_names = sorted(t.name for t in tools)
    print(f"Registered: {len(tool_names)} tools, {len(resources)} resources")
    assert len(tool_names) == 21, f"Expected 21 tools, got {len(tool_names)}"
    assert len(resources) == 1, f"Expected 1 resource, got {len(resources)}"

    passed = 0
    failed = 0

    async def check(label, tool_name, args, check_fn=None):
        nonlocal passed, failed
        try:
            result = await mcp._call_tool_mcp(tool_name, args)
            # Extract text from result
            texts = []
            for item in (result.content if hasattr(result, "content") else [result]):
                if hasattr(item, "text"):
                    texts.append(item.text)
                else:
                    texts.append(str(item))
            txt = "\n".join(texts)
            if check_fn:
                check_fn(txt)
            print(f"  PASS {label} ({len(txt):,} chars)")
            passed += 1
            return txt
        except Exception as e:
            print(f"  FAIL {label}: {e}")
            failed += 1
            return None

    t0 = time.time()

    # ── LOOKUP TOOLS (6) ──
    print("\n── LOOKUP TOOLS ──")
    await check("get_function(ta.ema)", "get_function", {"name": "ta.ema"},
                lambda t: _assert("SYNTAX" in t and "ta.ema" in t, "missing SYNTAX/ta.ema"))
    await check("get_variable(close)", "get_variable", {"name": "close"},
                lambda t: _assert("close" in t.lower(), "missing close"))
    await check("get_type(array)", "get_type", {"name": "array"},
                lambda t: _assert("array" in t.lower(), "missing array"))
    await check("get_constant(color.red)", "get_constant", {"name": "color.red"},
                lambda t: _assert("color" in t.lower(), "missing color"))
    await check("get_keyword(if)", "get_keyword", {"name": "if"},
                lambda t: _assert("if" in t.lower(), "missing if"))
    await check("get_operator(:=)", "get_operator", {"name": ":="},
                lambda t: _assert(":=" in t or "assignment" in t.lower(), "missing :="))

    # ── LOOKUP EDGE CASES ──
    print("\n── LOOKUP EDGE CASES ──")
    await check("get_function(nonexistent)", "get_function", {"name": "ta.nonexistent_xyz"},
                lambda t: _assert("not found" in t.lower(), "should say not found"))
    await check("get_function(sql_inject)", "get_function", {"name": "ta.ema'; DROP TABLE--"})
    await check("search_docs(unicode)", "search_docs", {"query": "移动平均线"})
    await check("search_docs(empty-ish)", "search_docs", {"query": "xyzzyfoobar"})

    # ── SEARCH TOOLS (4) ──
    print("\n── SEARCH TOOLS ──")
    await check("search_docs(ema crossover)", "search_docs", {"query": "exponential moving average crossover"},
                lambda t: _assert("ema" in t.lower(), "no ema in results"))
    await check("get_examples(strategy.entry)", "get_examples", {"query": "strategy.entry with stop loss"},
                lambda t: _assert("strategy" in t.lower(), "no strategy in examples"))
    await check("search_by_return_type(series float)", "search_by_return_type", {"return_type": "series float"},
                lambda t: _assert("ta." in t, "no ta. in results"))
    await check("list_namespace(ta)", "list_namespace", {"namespace": "ta"},
                lambda t: _assert("ta.ema" in t or "ta.sma" in t, "no ta functions"))

    # ── SEARCH EDGE CASES ──
    print("\n── SEARCH EDGE CASES ──")
    await check("search_docs(n_results=1)", "search_docs", {"query": "ema", "n_results": 1})
    await check("search_docs(n_results=30)", "search_docs", {"query": "ema", "n_results": 30})
    await check("search_docs(namespace_filter=ta)", "search_docs", {"query": "average", "namespace_filter": "ta"})
    await check("list_namespace(nonexistent)", "list_namespace", {"namespace": "nonexistent_ns_xyz"})

    # ── VALIDATION TOOLS (5) ──
    valid_code = '''//@version=6
indicator("Test EMA", overlay=true)
len = input.int(20, "Length")
ema = ta.ema(close, len)
plot(ema, "EMA", color.blue)
'''
    broken_code = '''//@version=6
indicator("Broken")
plot(nonexistent_function_xyz(close))
'''

    print("\n── VALIDATION TOOLS ──")
    await check("validate_syntax(valid)", "validate_syntax", {"code": valid_code},
                lambda t: _assert("VALID" in t or "compiles" in t.lower(), "should be valid"))
    await check("validate_syntax(broken)", "validate_syntax", {"code": broken_code},
                lambda t: _assert("error" in t.lower() or "issue" in t.lower(), "should have errors"))
    await check("validate_and_explain(broken)", "validate_and_explain", {"code": broken_code},
                lambda t: _assert("FAILED" in t or "error" in t.lower(), "should fail"))
    await check("fix_and_validate", "fix_and_validate", {"code": broken_code, "error_description": "Undeclared function"},
                lambda t: _assert("FIX" in t or "HINT" in t, "no fix/hint"))
    await check("debug_pine_facade", "debug_pine_facade", {"code": valid_code},
                lambda t: _assert("DEBUG" in t and "CIRCUIT" in t, "no DEBUG/CIRCUIT"))

    # validate_file with temp file
    with tempfile.NamedTemporaryFile(suffix=".ps", mode="w", delete=False, dir=os.getcwd()) as f:
        f.write(valid_code)
        tmp_path = f.name
    try:
        await check("validate_file(.ps)", "validate_file", {"file_path": tmp_path})
    finally:
        os.unlink(tmp_path)
    await check("validate_file(.txt rejected)", "validate_file", {"file_path": "/tmp/test.txt"},
                lambda t: _assert(".ps" in t or ".pine" in t, "should reject"))
    await check("validate_file(nonexistent)", "validate_file",
                {"file_path": os.path.expanduser("~/Documents/nonexistent_test_file_xyz.ps")},
                lambda t: _assert("not found" in t.lower(), "should say not found"))

    # ── CODEGEN TOOLS (3) ──
    print("\n── CODEGEN TOOLS ──")
    await check("generate_indicator(RSI)", "generate_indicator",
                {"name": "My RSI", "description": "RSI oscillator", "inputs": "length=14,src=close", "overlay": False},
                lambda t: _assert("GENERATED" in t and "indicator" in t, "no indicator template"))
    await check("generate_strategy(EMA Cross)", "generate_strategy",
                {"name": "EMA Cross", "description": "EMA crossover strategy", "initial_capital": 5000},
                lambda t: _assert("STRATEGY" in t and "strategy(" in t, "no strategy template"))
    v5_code = '''//@version=6
study("My Indicator")
src = security(syminfo.tickerid, "D", close)
plot(src)
'''
    await check("lookup_and_correct(v5→v6)", "lookup_and_correct",
                {"code": v5_code, "error_description": "v5 to v6 migration"},
                lambda t: _assert("LOOKUP" in t or "FIX" in t or "CORRECT" in t, "no lookup/correct"))

    # ── CONTEXT TOOLS (2) ──
    print("\n── CONTEXT TOOLS ──")
    await check("suggest_functions(crossover)", "suggest_functions",
                {"context": "I need to calculate a moving average crossover"},
                lambda t: _assert("ta.ema" in t or "ta.sma" in t or "crossover" in t, "no ma/crossover"))
    await check("get_namespace_cheatsheet(strategy)", "get_namespace_cheatsheet",
                {"namespace": "strategy"},
                lambda t: _assert("strategy." in t, "no strategy. members"))

    # ── OPTIMIZATION TOOL (1) ──
    print("\n── OPTIMIZATION TOOL ──")
    opt_code = '''//@version=6
indicator("Slow Code")
var arr = array.new_float()
for i = 0 to 5000
    array.push(arr, close[i])
myEma = ta.ema(close, 20)
plot(myEma)
request.security(syminfo.tickerid, "D", close)
'''
    await check("optimize_code", "optimize_code", {"code": opt_code},
                lambda t: _assert(len(t) > 50, "response too short"))

    # ── RESOURCE ──
    print("\n── RESOURCE ──")
    try:
        r = await mcp._get_resource("pinescript://stats")
        rtxt = ""
        if hasattr(r, "__iter__"):
            for item in r:
                if hasattr(item, "text"):
                    rtxt = item.text
                    break
                else:
                    rtxt += str(item)
        else:
            rtxt = str(r)
        print(f"  PASS pinescript://stats ({len(rtxt):,} chars)")
        passed += 1
    except Exception as e:
        print(f"  FAIL pinescript://stats: {e}")
        failed += 1

    elapsed = time.time() - t0
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"E2E COMPLETE: {passed}/{total} passed in {elapsed:.1f}s")
    if failed:
        print(f"FAILURES: {failed}")
        sys.exit(1)
    else:
        print("ALL PASSED")


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


if __name__ == "__main__":
    asyncio.run(main())
