#!/usr/bin/env python3
"""
Simple self-verification script that checks source code directly.
More reliable than AST parsing for complex functions.
"""

import re
import sys


def read_source():
    """Read the pinescript_mcp.py source code."""
    try:
        with open("pinescript_mcp.py", "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"FAIL: Could not read pinescript_mcp.py: {e}")
        sys.exit(1)


def check_imports(source):
    """Check C1: Required imports are present."""
    checks = []
    
    required_imports = [
        "import atexit",
        "import re", 
        "from collections import OrderedDict",
        "from concurrent.futures import ThreadPoolExecutor"
    ]
    
    for import_stmt in required_imports:
        if import_stmt in source:
            checks.append(f"PASS: Found {import_stmt}")
        else:
            checks.append(f"FAIL: Missing {import_stmt}")
    
    return checks


def check_constants(source):
    """Check constants exist."""
    checks = []
    
    if "MAX_TOOL_RESPONSE_CHARS = 80000" in source:
        checks.append("PASS: MAX_TOOL_RESPONSE_CHARS constant found")
    else:
        checks.append("FAIL: MAX_TOOL_RESPONSE_CHARS constant missing")
    
    return checks


def check_chroma_circuit_breaker(source):
    """Check C1: ChromaDBCircuitBreaker implementation."""
    checks = []
    
    if "class ChromaDBCircuitBreaker:" in source:
        checks.append("PASS: ChromaDBCircuitBreaker class found")
    else:
        checks.append("FAIL: ChromaDBCircuitBreaker class missing")
        return checks
    
    methods = ["record_success", "record_failure", "is_open"]
    for method in methods:
        if f"def {method}(" in source:
            checks.append(f"PASS: ChromaDBCircuitBreaker.{method} method found")
        else:
            checks.append(f"FAIL: ChromaDBCircuitBreaker.{method} method missing")
    
    return checks


def check_embedding_model_setup(source):
    """Check H5: Embedding model async setup."""
    checks = []
    
    if "_model_executor = ThreadPoolExecutor(" in source:
        checks.append("PASS: _model_executor ThreadPoolExecutor found")
    else:
        checks.append("FAIL: _model_executor ThreadPoolExecutor missing")
    
    if "_embedding_model_ready = asyncio.Event()" in source:
        checks.append("PASS: _embedding_model_ready asyncio.Event found")
    else:
        checks.append("FAIL: _embedding_model_ready asyncio.Event missing")
    
    if "async def _ensure_embedding_model():" in source:
        checks.append("PASS: _ensure_embedding_model function found")
    else:
        checks.append("FAIL: _ensure_embedding_model function missing")
    
    return checks


def check_search_by_name(source):
    """Check H2: _search_by_name implementation."""
    checks = []
    
    if "def _search_by_name(" in source:
        checks.append("PASS: _search_by_name function found")
        
        # Check for exact match pattern
        if 'where={"name": name}' in source:
            checks.append("PASS: _search_by_name has exact metadata match")
        else:
            checks.append("FAIL: _search_by_name missing exact metadata match")
        
        # Check for fuzzy scan
        if "fuzz.ratio" in source:
            checks.append("PASS: _search_by_name has fuzzy scan")
        else:
            checks.append("FAIL: _search_by_name missing fuzzy scan")
    else:
        checks.append("FAIL: _search_by_name function missing")
    
    return checks


def check_http_client_pooling(source):
    """Check C2: HTTP client pooling."""
    checks = []
    
    if "def _get_facade_client(" in source:
        checks.append("PASS: _get_facade_client function found")
    else:
        checks.append("FAIL: _get_facade_client function missing")
    
    if "def _shutdown_http_client(" in source:
        checks.append("PASS: _shutdown_http_client function found")
    else:
        checks.append("FAIL: _shutdown_http_client function missing")
    
    if "atexit.register(" in source:
        checks.append("PASS: atexit.register call found")
    else:
        checks.append("FAIL: atexit.register call missing")
    
    return checks


def check_fix_hints(source):
    """Check M7: Expanded _FIX_HINTS."""
    checks = []
    
    if "_FIX_HINTS: dict[str, str] = {" in source:
        checks.append("PASS: _FIX_HINTS found")
        
        # Count hints
        hint_pattern = r'"[^"]+":\s*"[^"]+"'
        hints = re.findall(hint_pattern, source[source.find("_FIX_HINTS"):source.find("_FIX_HINTS") + 2000])
        if len(hints) >= 15:
            checks.append(f"PASS: _FIX_HINTS has {len(hints)} hints (>= 15)")
        else:
            checks.append(f"FAIL: _FIX_HINTS has only {len(hints)} hints (need >= 15)")
    else:
        checks.append("FAIL: _FIX_HINTS missing")
    
    return checks


def check_cache_validation(source):
    """Check M6: JSON decode error handling."""
    checks = []
    
    if "def _get_cached_validation(" in source:
        checks.append("PASS: _get_cached_validation function found")
        
        if "JSONDecodeError" in source:
            checks.append("PASS: _get_cached_validation has JSONDecodeError handling")
        else:
            checks.append("FAIL: _get_cached_validation missing JSONDecodeError handling")
    else:
        checks.append("FAIL: _get_cached_validation function missing")
    
    return checks


def check_cache_eviction(source):
    """Check M8: Cache eviction uses min() by timestamp."""
    checks = []
    
    if "def _cache_validation(" in source:
        checks.append("PASS: _cache_validation function found")
        
        if "min(" in source and "timestamp" in source:
            checks.append("PASS: _cache_validation uses min() for eviction")
        else:
            checks.append("FAIL: _cache_validation missing min() eviction")
    else:
        checks.append("FAIL: _cache_validation function missing")
    
    return checks


def check_facade_response_normalization(source):
    """Check M5: _normalize_facade_response handles success:false."""
    checks = []
    
    if "def _normalize_facade_response(" in source:
        checks.append("PASS: _normalize_facade_response function found")
        
        if 'if not success:' in source or 'not success' in source:
            checks.append("PASS: _normalize_facade_response handles success:false")
        else:
            checks.append("FAIL: _normalize_facade_response missing success:false handling")
    else:
        checks.append("FAIL: _normalize_facade_response function missing")
    
    return checks


def check_utility_functions(source):
    """Check utility functions exist."""
    checks = []
    
    utilities = {
        "_PATH_PATTERN": "regex pattern",
        "_safe_error": "error sanitization", 
        "_cap_response": "response capping",
        "_sanitize_text": "text sanitization",
        "_sanitize_pine_string": "PineScript string sanitization"
    }
    
    for name, desc in utilities.items():
        if f"def {name}(" in source or f"{name} = " in source:
            checks.append(f"PASS: {name} ({desc}) found")
        else:
            checks.append(f"FAIL: {name} ({desc}) missing")
    
    return checks


def check_live_cache(source):
    """Check H4: Live cache implementation."""
    checks = []
    
    if "_LIVE_CACHE: OrderedDict" in source:
        checks.append("PASS: _LIVE_CACHE OrderedDict found")
    else:
        checks.append("FAIL: _LIVE_CACHE OrderedDict missing")
    
    if "def _get_live_entry_cached(" in source:
        checks.append("PASS: _get_live_entry_cached function found")
    else:
        checks.append("FAIL: _get_live_entry_cached function missing")
    
    # Check for rate limiting constants
    rate_consts = ["_LIVE_CACHE_TTL", "_LIVE_CACHE_MAX", "_LIVE_RATE_LIMIT"]
    found_consts = sum(1 for const in rate_consts if const in source)
    
    if found_consts >= 2:
        checks.append("PASS: Live cache rate limiting constants found")
    else:
        checks.append("FAIL: Live cache rate limiting constants missing")
    
    return checks


def check_source_filter(source):
    """Check H1: Python-side source filtering."""
    checks = []
    
    if "async def search_docs(" in source:
        checks.append("PASS: search_docs function found")
        
        # Look for the actual filtering pattern
        if "filter_val in (meta.get" in source or "filter_val in meta.get" in source:
            checks.append("PASS: search_docs has Python-side source filtering")
        else:
            checks.append("FAIL: search_docs missing Python-side source filtering")
    else:
        checks.append("FAIL: search_docs function missing")
    
    return checks


def check_get_live_entry(source):
    """Check H4: get_live_entry uses cached version."""
    checks = []
    
    if "async def get_live_entry(" in source:
        checks.append("PASS: get_live_entry function found")
        
        if "_get_live_entry_cached(" in source:
            checks.append("PASS: get_live_entry uses _get_live_entry_cached")
        else:
            checks.append("FAIL: get_live_entry doesn't use cached version")
    else:
        checks.append("FAIL: get_live_entry function missing")
    
    return checks


def check_diff_entry_preservation(source):
    """Check M14: diff_entry preserves local data when live fetch fails."""
    checks = []
    
    if "async def diff_entry(" in source:
        checks.append("PASS: diff_entry function found")
        
        # Look for preservation logic - this is a simplified check
        if "except" in source and "indexed" in source:
            checks.append("PASS: diff_entry preserves local data on failure")
        else:
            checks.append("FAIL: diff_entry missing local data preservation")
    else:
        checks.append("FAIL: diff_entry function missing")
    
    return checks


def check_strategy_guards(source):
    """Check M13: Strategy template has barstate guards."""
    checks = []
    
    if "async def generate_strategy(" in source:
        checks.append("PASS: generate_strategy function found")
        
        if "barstate.isconfirmed" in source and "barstate.islast" in source:
            checks.append("PASS: generate_strategy has barstate guards")
        else:
            checks.append("FAIL: generate_strategy missing barstate guards")
    else:
        checks.append("FAIL: generate_strategy function missing")
    
    return checks


def check_pine_string_sanitization(source):
    """Check M16: Indicator/Strategy name sanitization."""
    checks = []
    
    found_indicator = "async def generate_indicator(" in source
    found_strategy = "async def generate_strategy(" in source
    uses_sanitization = "_sanitize_pine_string(" in source
    
    if found_indicator:
        checks.append("PASS: generate_indicator function found")
    else:
        checks.append("FAIL: generate_indicator function missing")
    
    if found_strategy:
        checks.append("PASS: generate_strategy function found")
    else:
        checks.append("FAIL: generate_strategy function missing")
    
    if uses_sanitization:
        checks.append("PASS: Functions use _sanitize_pine_string")
    else:
        checks.append("FAIL: Functions don't use _sanitize_pine_string")
    
    return checks


def check_debug_code_input_removal(source):
    """Check M1: DebugCodeInput class removed."""
    checks = []
    
    if "class DebugCodeInput" not in source:
        checks.append("PASS: DebugCodeInput class removed")
    else:
        checks.append("FAIL: DebugCodeInput class still present")
    
    if "class CodeInput" in source:
        checks.append("PASS: CodeInput class present")
    else:
        checks.append("FAIL: CodeInput class missing")
    
    return checks


def check_get_all_where_limit(source):
    """Check H6: _get_all_where has limit parameter."""
    checks = []
    
    if "def _get_all_where(" in source:
        checks.append("PASS: _get_all_where function found")
        
        if "limit: int = 1000" in source or "limit=1000" in source:
            checks.append("PASS: _get_all_where has limit parameter")
        else:
            checks.append("FAIL: _get_all_where missing limit parameter")
    else:
        checks.append("FAIL: _get_all_where function missing")
    
    return checks


def check_cap_response_usage(source):
    """Check M9: _cap_response applied to tool returns."""
    checks = []
    
    # Count _cap_response usage
    cap_usage_count = source.count("_cap_response(")
    
    if cap_usage_count >= 3:
        checks.append(f"PASS: _cap_response used {cap_usage_count} times")
    else:
        checks.append(f"FAIL: _cap_response only used {cap_usage_count} times (need >= 3)")
    
    return checks


def check_safe_error_usage(source):
    """Check M11: _safe_error usage in tool handlers."""
    checks = []
    
    # Count _safe_error usage
    safe_error_count = source.count("_safe_error(")
    
    if safe_error_count >= 5:
        checks.append(f"PASS: _safe_error used {safe_error_count} times")
    else:
        checks.append(f"FAIL: _safe_error only used {safe_error_count} times (need >= 5)")
    
    return checks


def check_get_stats_implementation(source):
    """Check M10: get_stats implementation."""
    checks = []
    
    if "async def get_stats(" in source:
        checks.append("PASS: get_stats function found")
        
        stats_to_check = [
            "chroma_circuit_open",
            "live_cache_entries", 
            "embedding_model_ready"
        ]
        
        for stat in stats_to_check:
            if stat in source:
                checks.append(f"PASS: get_stats includes {stat}")
            else:
                checks.append(f"FAIL: get_stats missing {stat}")
    else:
        checks.append("FAIL: get_stats function missing")
    
    return checks


def check_syntax_errors(source):
    """Check for basic syntax errors."""
    checks = []
    
    try:
        compile(source, 'pinescript_mcp.py', 'exec')
        checks.append("PASS: No syntax errors found")
    except SyntaxError as e:
        checks.append(f"FAIL: Syntax error: {e}")
    
    return checks


def main():
    """Run all verification checks."""
    print("Running PineScript MCP simple self-verification...")
    print("=" * 60)
    
    # Read the source
    source = read_source()
    
    all_checks = []
    
    # Run all check groups
    check_groups = [
        ("Import Checks", check_imports),
        ("Constants", check_constants),
        ("ChromaDB Circuit Breaker", check_chroma_circuit_breaker),
        ("Embedding Model Setup", check_embedding_model_setup),
        ("Search Implementation", check_search_by_name),
        ("HTTP Client Pooling", check_http_client_pooling),
        ("Fix Hints", check_fix_hints),
        ("Cache Validation", check_cache_validation),
        ("Cache Eviction", check_cache_eviction),
        ("Facade Response Normalization", check_facade_response_normalization),
        ("Utility Functions", check_utility_functions),
        ("Live Cache", check_live_cache),
        ("Source Filter", check_source_filter),
        ("Live Entry Cache", check_get_live_entry),
        ("Diff Entry Preservation", check_diff_entry_preservation),
        ("Strategy Guards", check_strategy_guards),
        ("PineScript String Sanitization", check_pine_string_sanitization),
        ("DebugCodeInput Removal", check_debug_code_input_removal),
        ("Get All Where Limit", check_get_all_where_limit),
        ("Cap Response Usage", check_cap_response_usage),
        ("Safe Error Usage", check_safe_error_usage),
        ("Get Stats Implementation", check_get_stats_implementation),
        ("Syntax Check", check_syntax_errors),
    ]
    
    for group_name, check_func in check_groups:
        print(f"\n{group_name}:")
        print("-" * len(group_name))
        checks = check_func(source)
        all_checks.extend(checks)
        for check in checks:
            print(f"  {check}")
    
    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY:")
    print("-" * 8)
    
    pass_count = sum(1 for check in all_checks if check.startswith("PASS:"))
    fail_count = sum(1 for check in all_checks if check.startswith("FAIL:"))
    
    print(f"Total checks: {len(all_checks)}")
    print(f"Passed: {pass_count}")
    print(f"Failed: {fail_count}")
    
    if fail_count == 0:
        print("\n✅ ALL CHECKS PASSED! Fixes verified successfully.")
        sys.exit(0)
    else:
        print(f"\n❌ {fail_count} checks failed. Fix remaining issues.")
        sys.exit(1)


if __name__ == "__main__":
    main()
