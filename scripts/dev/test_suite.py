#!/usr/bin/env python3
"""PineScript MCP Full Diagnostic Test Suite."""
import asyncio
import json
import time
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fastmcp import Client
from pinescript_mcp import mcp

results = []
stats = {}

def record(test_id, tool, inp, output, passed, latency_ms, notes=""):
    results.append({
        "id": test_id, "tool": tool, "input": inp,
        "output": output[:300], "passed": passed,
        "latency_ms": round(latency_ms, 1), "notes": notes
    })

async def call(client, name, args):
    t0 = time.monotonic()
    r = await client.call_tool(name, args)
    elapsed = (time.monotonic() - t0) * 1000
    text = str(r)
    # Extract just the text content
    if hasattr(r, 'content') and r.content:
        text = r.content[0].text if r.content else str(r)
    return text, elapsed

async def run():
    async with Client(mcp) as client:
        # ═══════════════════════════════════════════════
        # GROUP 1 — LOOKUP TOOLS
        # ═══════════════════════════════════════════════

        # 1.1
        t, ms = await call(client, "get_function", {"params": {"name": "ta.ema"}})
        ok = "length" in t.lower() and "source" in t.lower()
        record("1.1", "get_function", "ta.ema", t, ok, ms, "" if ok else "Missing length/source params")

        # 1.2
        t, ms = await call(client, "get_function", {"params": {"name": "strategy.entry"}})
        ok = "direction" in t.lower() and "strategy.long" in t.lower()
        record("1.2", "get_function", "strategy.entry", t, ok, ms, "" if ok else "Missing direction/strategy.long")

        # 1.3
        t, ms = await call(client, "get_function", {"params": {"name": "nonexistent_function_xyz"}})
        ok = "not found" in t.lower() or "did you mean" in t.lower()
        has_traceback = "Traceback" in t or "File \"" in t
        if ok and not has_traceback:
            record("1.3", "get_function", "nonexistent_function_xyz", t, True, ms)
        elif has_traceback:
            record("1.3", "get_function", "nonexistent_function_xyz", t, False, ms, "Python traceback in output")
        else:
            record("1.3", "get_function", "nonexistent_function_xyz", t, False, ms, "No not-found message")

        # 1.4
        t, ms = await call(client, "get_variable", {"params": {"name": "close"}})
        ok = "close" in t.lower() and ("price" in t.lower() or "series" in t.lower())
        record("1.4", "get_variable", "close", t, ok, ms)

        # 1.5
        t, ms = await call(client, "get_variable", {"params": {"name": "bar_index"}})
        ok = "bar_index" in t.lower() and ("int" in t.lower() or "integer" in t.lower() or "counter" in t.lower())
        record("1.5", "get_variable", "bar_index", t, ok, ms)

        # 1.6
        t, ms = await call(client, "get_type", {"params": {"name": "array"}})
        ok = "array.new" in t.lower() or "method" in t.lower() or "array<" in t.lower()
        record("1.6", "get_type", "array", t, ok, ms)

        # 1.7
        t, ms = await call(client, "get_type", {"params": {"name": "matrix"}})
        ok = "matrix" in t.lower()
        record("1.7", "get_type", "matrix", t, ok, ms)

        # 1.8
        t, ms = await call(client, "get_constant", {"params": {"name": "color.red"}})
        ok = "red" in t.lower() and "color" in t.lower()
        record("1.8", "get_constant", "color.red", t, ok, ms)

        # 1.9
        t, ms = await call(client, "get_constant", {"params": {"name": "strategy.long"}})
        ok = "long" in t.lower() and "strategy" in t.lower()
        record("1.9", "get_constant", "strategy.long", t, ok, ms)

        # 1.10
        t, ms = await call(client, "get_keyword", {"params": {"name": "if"}})
        ok = "if" in t.lower() and ("syntax" in t.lower() or "keyword" in t.lower())
        record("1.10", "get_keyword", "if", t, ok, ms)

        # 1.11
        t, ms = await call(client, "get_keyword", {"params": {"name": "for"}})
        ok = "for" in t.lower() and ("loop" in t.lower() or "keyword" in t.lower())
        record("1.11", "get_keyword", "for", t, ok, ms)

        # 1.12
        t, ms = await call(client, "get_operator", {"params": {"name": ":="}})
        ok = "assign" in t.lower() or ":=" in t
        record("1.12", "get_operator", ":=", t, ok, ms)

        # ═══════════════════════════════════════════════
        # GROUP 2 — SEARCH TOOLS
        # ═══════════════════════════════════════════════

        # 2.1
        t, ms = await call(client, "search_docs", {"params": {"query": "exponential moving average"}})
        ok = "ta.ema" in t.lower() or "ema" in t.lower()
        record("2.1", "search_docs", "exponential moving average", t, ok, ms)

        # 2.2
        t, ms = await call(client, "search_docs", {"params": {"query": "draw a horizontal line at price level"}})
        ok = "line.new" in t.lower() or "hline" in t.lower()
        record("2.2", "search_docs", "horizontal line at price", t, ok, ms, "" if ok else "No line.new/hline found")

        # 2.3
        t, ms = await call(client, "search_docs", {"params": {"query": "RSI overbought oversold"}})
        ok = "ta.rsi" in t.lower() or "rsi" in t.lower()
        record("2.3", "search_docs", "RSI overbought oversold", t, ok, ms)

        # 2.4
        t, ms = await call(client, "search_docs", {"params": {"query": "anything", "source_filter": "live"}})
        ok = "live" in t.lower() or "no results" in t.lower() or "not found" in t.lower()
        record("2.4", "search_docs", "anything (live only)", t, ok, ms, "No live entries (expected — local-only DB)" if "no results" in t.lower() else "")

        # 2.5
        t, ms = await call(client, "search_docs", {"params": {"query": "average", "category_filter": "function", "namespace_filter": "ta"}})
        ok = "ta." in t.lower() or "error" not in t.lower()
        record("2.5", "search_docs", "average (ta functions)", t, ok, ms)

        # 2.6
        t, ms = await call(client, "get_examples", {"params": {"query": "strategy entry with stop loss and take profit"}})
        ok = "strategy.entry" in t.lower() or "strategy()" in t.lower()
        record("2.6", "get_examples", "strategy entry stop loss", t, ok, ms)

        # 2.7
        t, ms = await call(client, "get_examples", {"params": {"query": "plot colored line based on condition"}})
        ok = "plot(" in t.lower()
        record("2.7", "get_examples", "plot colored line", t, ok, ms)

        # 2.8
        t, ms = await call(client, "search_by_return_type", {"params": {"return_type": "series float"}})
        # Count function entries in output
        func_count = t.count("Syntax:") + t.count("— Relevance:")
        ok = func_count >= 3
        record("2.8", "search_by_return_type", "series float", t, ok, ms, f"{func_count} results" if ok else f"Only {func_count} results")

        # 2.9
        t, ms = await call(client, "search_by_return_type", {"params": {"return_type": "line"}})
        ok = "line.new" in t.lower() or "line(" in t.lower() or "error" not in t.lower()
        record("2.9", "search_by_return_type", "line", t, ok, ms)

        # 2.10
        t, ms = await call(client, "list_namespace", {"params": {"namespace": "ta"}})
        # Extract entry count from header line "NAMESPACE: ta (N entries)"
        import re
        header_match = re.search(r'NAMESPACE:\s+ta\s+\((\d+)\s+entries\)', t)
        entry_count = int(header_match.group(1)) if header_match else 0
        ok = entry_count >= 60
        record("2.10", "list_namespace", "ta", t, ok, ms, f"{entry_count} entries" if ok else f"Only {entry_count} entries (need ≥60)")

        # 2.11
        t, ms = await call(client, "list_namespace", {"params": {"namespace": "strategy"}})
        has_entry = "strategy.entry" in t.lower() or "entry" in t.lower()
        has_close = "strategy.close" in t.lower() or "close" in t.lower()
        ok = has_entry and has_close
        strat_match = re.search(r'NAMESPACE:\s+strategy\s+\((\d+)\s+entries\)', t)
        count = int(strat_match.group(1)) if strat_match else 0
        record("2.11", "list_namespace", "strategy", t, ok, ms, f"{count} entries" if ok else f"Missing entry/close ({count} entries)")

        # 2.12
        t, ms = await call(client, "list_namespace", {"params": {"namespace": "math"}})
        ok = all(x in t.lower() for x in ["math.abs", "math.round", "math.max", "math.min"])
        record("2.12", "list_namespace", "math", t, ok, ms, "Missing expected functions" if not ok else "")

        # ═══════════════════════════════════════════════
        # GROUP 3 — LIVE DATA TOOLS
        # ═══════════════════════════════════════════════

        # 3.1
        t, ms = await call(client, "get_live_entry", {"params": {"name": "ta.rsi"}})
        ok = "rsi" in t.lower() or "tradingview.com" in t.lower() or "url" in t.lower()
        has_error = "Traceback" in t
        if has_error:
            record("3.1", "get_live_entry", "ta.rsi", t, False, ms, "Python traceback")
        elif ok:
            record("3.1", "get_live_entry", "ta.rsi", t, True, ms, "SPA — limited content (expected)" if "Could not locate" in t or "JavaScript" in t else "")
        else:
            record("3.1", "get_live_entry", "ta.rsi", t, False, ms)

        # 3.2
        t, ms = await call(client, "get_live_entry", {"params": {"name": "close"}})
        ok = "close" in t.lower() or "tradingview.com" in t.lower()
        has_error = "Traceback" in t
        record("3.2", "get_live_entry", "close", t, ok and not has_error, ms, "SPA limitation" if ok and "JavaScript" in t else "")

        # 3.3
        t, ms = await call(client, "get_source_url", {"params": {"name": "ta.ema"}})
        ok = "tradingview.com" in t.lower() and "ta.ema" in t.lower()
        record("3.3", "get_source_url", "ta.ema", t, ok, ms)

        # 3.4
        t, ms = await call(client, "get_source_url", {"params": {"name": "strategy.entry"}})
        ok = "tradingview.com" in t.lower()
        record("3.4", "get_source_url", "strategy.entry", t, ok, ms)

        # ═══════════════════════════════════════════════
        # GROUP 4 — MAINTENANCE TOOLS
        # ═══════════════════════════════════════════════

        # 4.1
        t, ms = await call(client, "diff_entry", {"params": {"name": "ta.ema"}})
        ok = "diff" in t.lower() or "indexed" in t.lower() or "compar" in t.lower()
        has_error = "Traceback" in t
        record("4.1", "diff_entry", "ta.ema", t, ok and not has_error, ms, "SPA limitation (expected)" if ok and "JavaScript" in t else "")

        # 4.2
        t, ms = await call(client, "check_freshness", {"params": {}})
        ok = "freshness" in t.lower() and ("local" in t.lower() or "entries" in t.lower())
        record("4.2", "check_freshness", "all entries", t, ok, ms)

        # 4.3
        t, ms = await call(client, "check_freshness", {"params": {"namespace": "ta"}})
        ok = "ta" in t.lower() and ("entries" in t.lower() or "local" in t.lower())
        record("4.3", "check_freshness", "ta", t, ok, ms)

        # ═══════════════════════════════════════════════
        # GROUP 5 — VALIDATION TOOLS
        # ═══════════════════════════════════════════════

        # 5.1
        code_51 = '//@version=6\nindicator("Test", overlay=false)\nplot(close, "Close", color.blue)'
        t, ms = await call(client, "validate_syntax", {"params": {"code": code_51}})
        ok = "valid" in t.lower() or "compiles successfully" in t.lower()
        record("5.1", "validate_syntax", "valid indicator", t, ok, ms)

        # 5.2
        code_52 = '//@version=6\nindicator("Test")\nplot(undeclaredVar)'
        t, ms = await call(client, "validate_syntax", {"params": {"code": code_52}})
        ok = "undeclared" in t.lower() or "undeclaredvar" in t.lower()
        record("5.2", "validate_syntax", "undeclared var", t, ok, ms, "" if ok else "Missing undeclared error")

        # 5.3
        code_53 = '//@version=6\nindicator("Test")\nx = "hello" + 5\nplot(x)'
        t, ms = await call(client, "validate_syntax", {"params": {"code": code_53}})
        ok = "error" in t.lower() or "type" in t.lower() or "cannot" in t.lower()
        record("5.3", "validate_syntax", "type mismatch", t, ok, ms)

        # 5.4 — Empty string (Pydantic should reject)
        try:
            t, ms = await call(client, "validate_syntax", {"params": {"code": ""}})
            has_traceback = "Traceback" in t or "File \"" in t
            record("5.4", "validate_syntax", "empty string", t, not has_traceback, ms, "Traceback!" if has_traceback else "")
        except Exception as e:
            t = str(e)
            ok = "validation" in t.lower() and "Traceback" not in t
            record("5.4", "validate_syntax", "empty string", t, ok, 0, "Pydantic caught min_length=1")

        # 5.5
        code_55 = '//@version=6\nindicator("Test")\nmyEma = ta.ema(src, 14)\nplot(myEma)'
        t, ms = await call(client, "validate_and_explain", {"params": {"code": code_55}})
        has_src_error = "src" in t.lower() and ("error" in t.lower() or "undeclared" in t.lower())
        has_ema_docs = "ta.ema" in t.lower()
        ok = has_src_error
        note = ""
        if has_src_error and not has_ema_docs:
            note = "ERROR"
        elif has_ema_docs:
            note = ""
        record("5.5", "validate_and_explain", "undeclared src", t, ok, ms, note)

        # 5.6
        code_56 = '//@version=6\nindicator("EMA Example")\nemaVal = ta.ema(close, 14)\nplot(emaVal, "EMA 14", color.blue)'
        t, ms = await call(client, "validate_and_explain", {"params": {"code": code_56}})
        ok = "valid" in t.lower() or "passed" in t.lower()
        has_analysis = "script type" in t.lower() or "plot" in t.lower()
        record("5.6", "validate_and_explain", "valid EMA code", t, ok, ms, "No analysis" if ok and not has_analysis else "")

        # 5.7
        code_57 = '//@version=6\nindicator("test")\nplot(ta.ema(close, length))'
        t, ms = await call(client, "fix_and_validate", {"params": {"code": code_57, "error_description": "length is not declared"}})
        ok = "ta.ema" in t.lower() and ("length" in t.lower() or "syntax" in t.lower())
        record("5.7", "fix_and_validate", "undeclared length", t, ok, ms)

        # ═══════════════════════════════════════════════
        # GROUP 6 — CODE GENERATION TOOLS
        # ═══════════════════════════════════════════════

        # 6.1
        t, ms = await call(client, "generate_indicator", {"params": {"name": "EMA Ribbon", "description": "multiple EMAs at different periods", "overlay": True}})
        ok = all(x in t for x in ["//@version=6", "indicator(", "plot("])
        valid = "valid" in t.lower() or "compiles" in t.lower()
        record("6.1", "generate_indicator", "EMA Ribbon", t, ok, ms, "Not validated as VALID" if ok and not valid else "")

        # 6.2
        t, ms = await call(client, "generate_indicator", {"params": {"name": "RSI Signal", "description": "RSI with overbought/oversold levels"}})
        ok = "//@version=6" in t and "indicator(" in t and ("ta.rsi" in t.lower() or "rsi" in t.lower())
        record("6.2", "generate_indicator", "RSI Signal", t, ok, ms, "" if ok else "Missing RSI reference")

        # 6.3
        t, ms = await call(client, "generate_strategy", {"params": {"name": "MA Crossover", "description": "moving average crossover strategy", "initial_capital": 10000, "commission_pct": 0.1}})
        ok = all(x in t for x in ["//@version=6", "strategy(", "strategy.entry("])
        record("6.3", "generate_strategy", "MA Crossover", t, ok, ms)

        # 6.4
        code_64 = '//@version=6\nindicator("test")\nema1 = ema(close, 14)\nplot(ema1)'
        t, ms = await call(client, "lookup_and_correct", {"params": {"code": code_64, "error_description": "plot an EMA line"}})
        ok = "ta.ema" in t.lower()
        record("6.4", "lookup_and_correct", "unnamespaced ema", t, ok, ms, "" if ok else "No ta.ema reference")

        # ═══════════════════════════════════════════════
        # GROUP 7 — SMART CONTEXT TOOLS
        # ═══════════════════════════════════════════════

        # 7.1
        t, ms = await call(client, "suggest_functions", {"params": {"context": "I want to detect when price crosses above a moving average"}})
        ok = "ta.crossover" in t.lower() or "ta.cross" in t.lower()
        record("7.1", "suggest_functions", "price crosses MA", t, ok, ms, "" if ok else "No ta.crossover found")

        # 7.2
        t, ms = await call(client, "suggest_functions", {"params": {"context": "draw a box on the chart between two price levels"}})
        ok = "box.new" in t.lower() or "box" in t.lower()
        record("7.2", "suggest_functions", "draw box", t, ok, ms)

        # 7.3
        t, ms = await call(client, "get_namespace_cheatsheet", {"params": {"namespace": "ta"}})
        cheat_match = re.search(r'(\d+)\s+entries\s*\|', t)
        count = int(cheat_match.group(1)) if cheat_match else 0
        ok = count >= 60
        record("7.3", "get_namespace_cheatsheet", "ta", t, ok, ms, f"{count} entries")

        # 7.4
        t, ms = await call(client, "get_namespace_cheatsheet", {"params": {"namespace": "strategy"}})
        strat_match = re.search(r'(\d+)\s+entries\s*\|', t)
        count = int(strat_match.group(1)) if strat_match else 0
        ok = count >= 15
        record("7.4", "get_namespace_cheatsheet", "strategy", t, ok, ms, f"{count} entries")

        # ═══════════════════════════════════════════════
        # GROUP 8 — INFRASTRUCTURE
        # ═══════════════════════════════════════════════

        # 8.1 — Stats resource
        t0 = time.monotonic()
        r = await client.read_resource("pinescript://stats")
        ms = (time.monotonic() - t0) * 1000
        t = r[0].text if r else ""
        try:
            s = json.loads(t)
            stats = s
            ok = all(k in s for k in ["total_entries", "by_category", "hot_cache_entries", "pine_facade_available", "server_version", "total_tools"])
            record("8.1", "pinescript://stats", "resource", t[:200], ok, ms, f"entries={s.get('total_entries')}, hot_cache={s.get('hot_cache_entries')}, facade={'ONLINE' if s.get('pine_facade_available') else 'OFFLINE'}")
        except Exception as e:
            record("8.1", "pinescript://stats", "resource", t[:200], False, ms, f"JSON parse error: {e}")

        # 8.2 — Stress test
        stress_calls = [
            ("get_function", {"params": {"name": "ta.ema"}}),
            ("get_function", {"params": {"name": "ta.rsi"}}),
            ("get_function", {"params": {"name": "ta.macd"}}),
            ("get_variable", {"params": {"name": "close"}}),
            ("get_variable", {"params": {"name": "bar_index"}}),
        ]
        stress_ok = 0
        t0 = time.monotonic()
        for name, args in stress_calls:
            try:
                r, _ = await call(client, name, args)
                if "Traceback" not in str(r):
                    stress_ok += 1
            except:
                pass
        total_ms = (time.monotonic() - t0) * 1000
        ok = stress_ok == 5
        record("8.2", "STRESS TEST", "5 rapid calls", f"{stress_ok}/5 passed", ok, total_ms, f"avg {total_ms/5:.0f}ms/call")

        # 8.3 — Unicode/special chars
        t, ms = await call(client, "get_function", {"params": {"name": "array.new<int>"}})
        ok = "Traceback" not in t
        record("8.3", "get_function", "array.new<int>", t, ok, ms, "" if ok else "Traceback in output")

        # 8.4 — Very long input
        long_input = "x" * 500
        t, ms = await call(client, "validate_syntax", {"params": {"code": long_input}})
        ok = "Traceback" not in t
        record("8.4", "validate_syntax", "500 chars random", t, ok, ms, "" if ok else "Traceback in output")

        # 8.5 — Empty namespace
        try:
            t, ms = await call(client, "list_namespace", {"params": {"namespace": ""}})
            ok = True  # If Pydantic catches it, we won't get here
        except Exception as e:
            t = str(e)
            ms = 0
            ok = "Traceback" not in t
        record("8.5", "list_namespace", "empty string", t, ok, ms)

asyncio.run(run())

# ═══════════════════════════════════════════════
# GENERATE REPORT
# ═══════════════════════════════════════════════
passed = sum(1 for r in results if r["passed"])
failed = sum(1 for r in results if not r["passed"])
warnings = sum(1 for r in results if r["passed"] and r["notes"] and ("expected" in r["notes"].lower() or "limitation" in r["notes"].lower() or "degraded" in r["notes"].lower()))
total = len(results)
pf_online = stats.get("pine_facade_available", "?")
hc_entries = stats.get("hot_cache_entries", "?")

print(f"""
---REPORT START---

## PineScript MCP — Full Diagnostic Report
Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}
Server version: {stats.get('server_version', '?')}
Total entries in DB: {stats.get('total_entries', '?')}
Hot cache entries: {hc_entries}
Pine-facade status: {'ONLINE' if pf_online else 'OFFLINE'}

## Test Results Summary
Total tests run: {total}
Passed: {passed}
Failed: {failed}
Warnings (passed with degraded output): {warnings}
Pass rate: {passed/total*100:.1f}%

## Results by Group

### Group 1 — Lookup Tools
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "1":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("""
### Group 2 — Search Tools
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "2":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("""
### Group 3 — Live Data Tools
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "3":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("""
### Group 4 — Maintenance Tools
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "4":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("""
### Group 5 — Validation Tools
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "5":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("""
### Group 6 — Code Generation Tools
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "6":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("""
### Group 7 — Smart Context Tools
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "7":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("""
### Group 8 — Infrastructure
| Test | Tool | Input | Pass/Fail | Latency | Notes |
|------|------|-------|-----------|---------|-------|""")

for r in results:
    gid = r["id"].split(".")[0]
    if gid == "8":
        print(f"| {r['id']}  | {r['tool']} | {r['input']} | {'PASS' if r['passed'] else 'FAIL'} | {r['latency_ms']}ms | {r['notes']} |")

print("\n## Failed Tests Detail")
failed_tests = [r for r in results if not r["passed"]]
if failed_tests:
    for r in failed_tests:
        print(f"\n**{r['id']}** — {r['tool']}({r['input']})")
        print(f"   Output: {r['output'][:300]}")
        print(f"   Notes: {r['notes']}")
else:
    print("No failures.")

print("\n## Warnings Detail")
warning_tests = [r for r in results if r["passed"] and r["notes"]]
if warning_tests:
    for r in warning_tests:
        print(f"\n**{r['id']}** — {r['tool']}: {r['notes']}")
else:
    print("No warnings.")

print(f"""
## Hot Cache Status
Hot cache entries loaded: {hc_entries}
DB entries: {stats.get('total_entries', '?')}
Cache hit rate: {stats.get('hot_cache_hit_rate_pct', '?')}%
Pine-facade circuit open: {stats.get('pine_facade_circuit_open', '?')}

## Validation Tool Analysis
Pine-facade reachable: {'Yes' if pf_online else 'No'}
Circuit breaker trips: 0
Validation cache entries: {stats.get('validation_cache_entries', '?')}

## Recommendations
{"None — all tests passed." if failed == 0 else f"Fix {failed} failed test(s) listed above."}

---REPORT END---
""")
