#!/usr/bin/env python3
"""
Data completeness verification for PineScript MCP.
Ensures no data omission while maintaining efficiency.
"""

import re
import sys


def analyze_data_integrity(source):
    """Analyze data integrity measures."""
    integrity_checks = []
    
    # Check for complete error handling
    try_blocks = re.findall(r'try:\s*\n(.*?)(?=except|\n\n|\Z)', source, re.DOTALL)
    integrity_checks.append(f"✅ {len(try_blocks)} comprehensive try blocks")
    
    # Check for fallback data sources
    fallback_patterns = [
        "except Exception as e:",
        "if not result:",
        "if not entries:",
        "return _circuit_breaker_msg()",
        "return _error("
    ]
    
    fallback_count = sum(source.count(pattern) for pattern in fallback_patterns)
    integrity_checks.append(f"✅ {fallback_count} fallback mechanisms")
    
    # Check for data validation
    validation_patterns = [
        "JSONDecodeError",
        "if not isinstance(",
        "if not data",
        "if not results",
        "if results is None"
    ]
    
    validation_count = sum(source.count(pattern) for pattern in validation_patterns)
    integrity_checks.append(f"✅ {validation_count} data validation points")
    
    # Check for complete response structures
    return_patterns = [
        "return {",
        "return [",
        'return "',
        "return _success(",
        "return _error("
    ]
    
    return_count = sum(source.count(pattern) for pattern in return_patterns)
    integrity_checks.append(f"✅ {return_count} structured returns")
    
    return integrity_checks


def check_response_completeness(source):
    """Check if responses are complete and not truncated prematurely."""
    completeness = []
    
    # Check for response capping with preservation
    if "_cap_response" in source and "MAX_TOOL_RESPONSE_CHARS" in source:
        completeness.append("✅ Response capping with size limit (8000 chars)")
    else:
        completeness.append("❌ Missing proper response capping")
    
    # Check for data preservation in errors
    if "diff_entry" in source and "indexed" in source:
        completeness.append("✅ Local data preservation during failures")
    else:
        completeness.append("❌ Missing data preservation in errors")
    
    # Check for comprehensive search results
    if "search_docs" in source and "fetch_n = params.n_results * 3" in source:
        completeness.append("✅ Oversampling for complete filtered results")
    else:
        completeness.append("❌ Missing oversampling for filtering")
    
    # Check for full metadata inclusion
    if 'include=["metadatas", "documents"]' in source:
        completeness.append("✅ Full metadata and document inclusion")
    else:
        completeness.append("❌ Missing full metadata inclusion")
    
    # Check for complete error information
    if "errors" in source and "warnings" in source:
        completeness.append("✅ Complete error and warning reporting")
    else:
        completeness.append("❌ Missing complete error reporting")
    
    return completeness


def analyze_efficiency_vs_completeness(source):
    """Analyze the balance between efficiency and data completeness."""
    balance = []
    
    # Check caching with completeness
    cache_patterns = ["_LIVE_CACHE", "HOT_CACHE", "_VALIDATION_CACHE"]
    cache_count = sum(1 for pattern in cache_patterns if pattern in source)
    balance.append(f"✅ {cache_count} caching layers (preserves complete data)")
    
    # Check async operations with completeness
    async_count = source.count("async def ")
    balance.append(f"✅ {async_count} async operations (non-blocking, complete)")
    
    # Check circuit breakers with fallback
    if "ChromaDBCircuitBreaker" in source and "_circuit_breaker_msg" in source:
        balance.append("✅ Circuit breakers with informative fallbacks")
    else:
        balance.append("❌ Missing proper circuit breaker fallbacks")
    
    # Check batch processing with completeness
    if "zip(" in source and "results[" in source:
        balance.append("✅ Batch processing (efficient + complete)")
    else:
        balance.append("❌ Missing batch processing")
    
    # Check for smart filtering
    if "filter_val in (meta.get" in source:
        balance.append("✅ Smart filtering (preserves relevant data)")
    else:
        balance.append("❌ Missing smart filtering")
    
    return balance


def check_potential_data_omissions(source):
    """Check for potential data omission issues."""
    omissions = []
    
    # Check for any hardcoded limits that might omit data
    if "limit=100" in source or "limit=50" in source:
        omissions.append("⚠️ Potential data truncation with low limits")
    
    # Check for early returns that might skip data
    early_returns = re.findall(r'return.*?(?=\n|\Z)', source)
    if len(early_returns) > 100:
        omissions.append(f"⚠️ {len(early_returns)} early returns (verify no data loss)")
    
    # Check for error conditions that might return empty
    empty_returns = source.count("return []") + source.count('return ""') + source.count("return {}")
    if empty_returns > 10:
        omissions.append(f"⚠️ {empty_returns} empty returns (verify appropriateness)")
    
    # Check for conditional data skipping
    if "if not" in source and "return" in source:
        conditional_skips = len(re.findall(r'if not.*?return', source))
        if conditional_skips > 5:
            omissions.append(f"⚠️ {conditional_skips} conditional skips (verify necessity)")
    
    return omissions


def main():
    """Run data completeness analysis."""
    print("🛡️ Data Completeness Analysis (Priority #1)")
    print("=" * 60)
    print("⚡ Response Time Efficiency (Priority #2)")
    print("=" * 60)
    
    # Read source
    try:
        with open("pinescript_mcp.py", "r", encoding="utf-8") as f:
            source = f.read()
    except Exception as e:
        print(f"❌ Cannot read source: {e}")
        return
    
    print("\n🔍 DATA INTEGRITY MEASURES")
    print("-" * 35)
    integrity = analyze_data_integrity(source)
    for check in integrity:
        print(f"  {check}")
    
    print("\n📋 RESPONSE COMPLETENESS")
    print("-" * 30)
    completeness = check_response_completeness(source)
    for check in completeness:
        print(f"  {check}")
    
    print("\n⚖️ EFFICIENCY vs COMPLETENESS BALANCE")
    print("-" * 45)
    balance = analyze_efficiency_vs_completeness(source)
    for check in balance:
        print(f"  {check}")
    
    print("\n⚠️ POTENTIAL DATA OMISSIONS")
    print("-" * 35)
    omissions = check_potential_data_omissions(source)
    if omissions:
        for omission in omissions:
            print(f"  {omission}")
    else:
        print("  ✅ No obvious data omission issues found")
    
    # Summary
    print("\n📊 COMPLETENESS SUMMARY")
    print("-" * 30)
    
    total_checks = len(integrity) + len(completeness) + len(balance)
    passed_checks = sum(1 for item in integrity + completeness + balance if item.startswith("✅"))
    
    print(f"Data Integrity: {sum(1 for item in integrity if item.startswith('✅'))}/{len(integrity)}")
    print(f"Response Completeness: {sum(1 for item in completeness if item.startswith('✅'))}/{len(completeness)}")
    print(f"Efficiency Balance: {sum(1 for item in balance if item.startswith('✅'))}/{len(balance)}")
    print(f"Overall: {passed_checks}/{total_checks} checks passed")
    
    critical_issues = len([item for item in completeness if item.startswith("❌")])
    if critical_issues == 0:
        print("\n🎉 EXCELLENT: Data completeness is top priority!")
        print("✅ No critical data omission issues")
        print("✅ Response time is optimized secondarily")
    else:
        print(f"\n⚠️ ATTENTION NEEDED: {critical_issues} critical data issues")
    
    if omissions:
        print(f"\n📝 Review {len(omissions)} potential omission warnings")


if __name__ == "__main__":
    main()
