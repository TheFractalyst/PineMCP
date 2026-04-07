#!/usr/bin/env python3
"""Helper script to validate PineScript files via MCP server.
This bypasses Claude Code limitations with large file parameters.

Usage:
    python validate_file.py <file_path>
    python validate_file.py /Users/fractalyst/Documents/Quantify\ -\ Deeptest/Strategies/VIX.ps
"""

import sys
import os
import json
import asyncio
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Import the validation function directly
from pinescript_mcp import _call_pine_facade


async def validate_file(file_path: str) -> dict:
    """Read a PineScript file and validate it."""
    if not os.path.exists(file_path):
        return {
            "success": False,
            "error": f"File not found: {file_path}",
            "errors": [],
            "warnings": []
        }
    
    # Read file content
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            code = f.read()
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to read file: {e}",
            "errors": [],
            "warnings": []
        }
    
    # Get file stats
    file_size = len(code)
    line_count = code.count('\n') + 1
    
    print(f"{'='*80}")
    print(f"VALIDATING: {file_path}")
    print(f"{'='*80}")
    print(f"File size: {file_size:,} bytes")
    print(f"Line count: {line_count:,} lines")
    print(f"{'='*80}\n")
    
    # Validate using pine-facade
    result = await _call_pine_facade(code)
    
    # Format output
    print("VALIDATION RESULTS:")
    print("-" * 80)
    
    if result.get("success", False):
        print("✅ VALID - Code compiles successfully!")
        print(f"   Errors: 0")
        print(f"   Warnings: {len(result.get('warnings', []))}")
    else:
        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        
        print(f"❌ COMPILATION ISSUES ({len(errors)})")
        
        # Show meta info if available
        meta = result.get("meta", {})
        if meta.get("fallback"):
            print(f"   Compiler: {meta.get('fallback', 'Unknown')}")
            if meta.get("note"):
                print(f"   Note: {meta['note']}")
        
        print(f"   Errors: {len(errors)} | Warnings: {len(warnings)}")
        print()
        
        # Display errors
        for idx, error in enumerate(errors, 1):
            line = error.get("line", "?")
            col = error.get("column", "?")
            text = error.get("text", "Unknown error")
            err_type = error.get("type", "error")
            
            print(f"  ERROR {idx} — Line {line}, Col {col} [{err_type.upper()}]")
            print(f"    {text}")
            
            # Show fix hint if available
            if "Fix hint" in text or "fix hint" in text.lower():
                pass  # Already in text
            print()
        
        # Display warnings if any
        if warnings:
            print(f"\nWARNINGS ({len(warnings)}):")
            for idx, warning in enumerate(warnings, 1):
                line = warning.get("line", "?")
                col = warning.get("column", "?")
                text = warning.get("text", "Unknown warning")
                print(f"  WARNING {idx} — Line {line}, Col {col}")
                print(f"    {text}")
    
    print()
    return result


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python validate_file.py <file_path>")
        print("\nExample:")
        print('  python validate_file.py "/Users/fractalyst/Documents/Quantify - Deeptest/Strategies/VIX.ps"')
        sys.exit(1)
    
    file_path = sys.argv[1]
    result = await validate_file(file_path)
    
    # Exit with appropriate code
    sys.exit(0 if result.get("success", False) else 1)


if __name__ == "__main__":
    asyncio.run(main())
