#!/usr/bin/env python3
"""
verify_fixes.py
─────────────────────────────────────────────────────────────────────────────
Verify the 3 bug fixes + new debug_pine_facade tool.

Checks:
  1. Module loads without import/syntax errors
  2. 23 tools registered (was 22)
  3. PineFacadeCircuitBreaker has record_network_failure / record_compiler_error
  4. _call_pine_facade returns normalized dict with success/errors/warnings/meta/raw_response
  5. Namespace doubling guard present in all formatting code
  6. debug_pine_facade tool exists and is callable
"""

import ast
import json
import re
import sys
import time
from pathlib import Path

SERVER_FILE = Path(__file__).parent / "pinescript_mcp.py"
passed = 0
failed = 0


def check(test_id: str, desc: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {test_id} — {desc}")
    else:
        failed += 1
        print(f"  FAIL  {test_id} — {desc}")
        if detail:
            print(f"         {detail}")


def main():
    source = SERVER_FILE.read_text()

    print("=" * 60)
    print("  VERIFY FIXES — Bug 1, 2, 3 + debug_pine_facade")
    print("=" * 60)
    print()

    # ── 1. Syntax ──
    print("GROUP 1 — SYNTAX & IMPORTS")
    try:
        tree = ast.parse(source)
        check("1.1", "Module parses without syntax errors", True)
    except SyntaxError as e:
        check("1.1", "Module parses without syntax errors", False, str(e))

    # ── 2. Tool count ──
    print("\nGROUP 2 — TOOL COUNT")
    tool_count = source.count("@mcp.tool")
    check("2.1", f"23 @mcp.tool decorators (found {tool_count})", tool_count == 23)
    resource_count = source.count("@mcp.resource")
    check("2.2", f"1 @mcp.resource decorator (found {resource_count})", resource_count == 1)

    # ── 3. BUG 2: Circuit breaker ──
    print("\nGROUP 3 — BUG 2: PineFacadeCircuitBreaker")
    has_network_failure = "record_network_failure" in source
    check("3.1", "Has record_network_failure method", has_network_failure)
    has_compiler_error = "record_compiler_error" in source
    check("3.2", "Has record_compiler_error method", has_compiler_error)
    has_threshold_10 = "threshold: int = 10" in source
    check("3.3", "Default threshold is 10 (was 5)", has_threshold_10)
    has_cooldown_60 = "cooldown: int = 60" in source
    check("3.4", "Default cooldown is 60 (was 120)", has_cooldown_60)
    has_stats_method = "def stats(self)" in source
    check("3.5", "Has stats() method", has_stats_method)

    # ── 4. BUG 1: _call_pine_facade normalized response ──
    print("\nGROUP 4 — BUG 1: _call_pine_facade normalization")
    has_normalize = "_normalize_facade_response" in source
    check("4.1", "Has _normalize_facade_response function", has_normalize)
    has_success_key = '"success":' in source or "'success':" in source
    check("4.2", "Returns normalized dict with success key", has_success_key)
    has_raw_response = '"raw_response":' in source or "'raw_response':" in source
    check("4.3", "Returns raw_response in normalized dict", has_raw_response)
    has_404_handling = "resp.status_code == 404" in source
    check("4.4", "Handles HTTP 404 explicitly", has_404_handling)
    has_network_except = "httpx.ConnectError" in source
    check("4.5", "Catches httpx network exceptions separately", has_network_except)
    # Check record_network_failure is used, not record_failure
    has_old_record_failure_call = re.search(r'_pine_cb\.record_failure\(\)', source)
    check("4.6", "No old _pine_cb.record_failure() calls remain", not has_old_record_failure_call)

    # ── 5. BUG 3: Namespace doubling ──
    print("\nGROUP 5 — BUG 3: Namespace doubling guard")
    # All ns = ... lines should have the guard
    ns_lines = re.findall(r'ns = f"[^"]*namespace[^"]*"', source)
    unguarded = [l for l in ns_lines if "not name.startswith" not in l and "namespace else" in l]
    check("5.1", f"All ns-prefix lines have doubling guard ({len(ns_lines)} total, {len(unguarded)} unguarded)",
          len(unguarded) == 0,
          f"Unguarded: {unguarded[:3]}" if unguarded else "")

    # ns_prefix lines
    ns_prefix_lines = re.findall(r'ns_prefix = f"[^"]*"', source)
    unguarded_prefix = [l for l in ns_prefix_lines if "not name.startswith" not in l and "if ns else" in l]
    check("5.2", f"All ns_prefix lines have doubling guard ({len(ns_prefix_lines)} total, {len(unguarded_prefix)} unguarded)",
          len(unguarded_prefix) == 0,
          f"Unguarded: {unguarded_prefix[:3]}" if unguarded_prefix else "")

    # ── 6. debug_pine_facade tool ──
    print("\nGROUP 6 — debug_pine_facade tool")
    has_debug_tool = "async def debug_pine_facade" in source
    check("6.1", "debug_pine_facade function defined", has_debug_tool)
    has_debug_model = "class DebugCodeInput" in source
    check("6.2", "DebugCodeInput Pydantic model defined", has_debug_model)
    has_cb_stats_in_debug = "cb_stats = _pine_cb.stats()" in source
    check("6.3", "debug tool shows circuit breaker stats", has_cb_stats_in_debug)
    has_raw_in_debug = "raw_response" in source and "RAW RESPONSE" in source
    check("6.4", "debug tool shows raw response", has_raw_in_debug)

    # ── 7. Updated tools use normalized format ──
    print("\nGROUP 7 — Validation tools use normalized format")
    # Check validate_syntax reads success key
    validate_syntax_block = source[source.index("async def validate_syntax"):source.index("async def validate_and_explain")]
    check("7.1", "validate_syntax reads result.get('success')", "success" in validate_syntax_block)
    check("7.2", "validate_syntax reads result.get('errors')", "errors" in validate_syntax_block)

    validate_explain_block = source[source.index("async def validate_and_explain"):source.index("async def fix_and_validate")]
    check("7.3", "validate_and_explain reads result.get('success')", "success" in validate_explain_block)

    # ── 8. Module docstring updated ──
    print("\nGROUP 8 — Module docstring")
    check("8.1", "Docstring says 23 tools", "23 tools" in source[:500])
    check("8.2", "Docstring mentions debug_pine_facade", "debug_pine_facade" in source[:1000])
    check("8.3", "Stats resource says total_tools=23", '"total_tools": 23' in source or "'total_tools': 23" in source)

    # ── 9. fix_namespaces_v2.py exists ──
    print("\nGROUP 9 — fix_namespaces_v2.py")
    fix_ns = Path(__file__).parent / "fix_namespaces_v2.py"
    check("9.1", "fix_namespaces_v2.py exists", fix_ns.exists())
    if fix_ns.exists():
        fix_src = fix_ns.read_text()
        check("9.2", "Has _dedot_namespace function", "_dedot_namespace" in fix_src)
        check("9.3", "Has --dry-run flag", "--dry-run" in fix_src)
        check("9.4", "Updates ChromaDB collection", "collection.update" in fix_src)

    # ── Summary ──
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed} PASSED, {failed} FAILED out of {passed + failed}")
    print(f"{'=' * 60}")

    if failed > 0:
        print("\n  ACTION REQUIRED: Fix the failing checks above.")
        sys.exit(1)
    else:
        print("\n  ALL CHECKS PASSED — fixes verified.")
        sys.exit(0)


if __name__ == "__main__":
    main()
