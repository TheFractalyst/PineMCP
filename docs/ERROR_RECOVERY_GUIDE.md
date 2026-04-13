# PineScript MCP Error Recovery Guide

## Overview

This guide ensures Claude Code never gets stuck when validating PineScript files. Every error has a recovery path.

## Quick Reference: Error → Recovery

| Error Message | Immediate Action | Fallback |
|--------------|------------------|----------|
| "ERROR: No code provided" | Use `validate_file` instead of `validate_syntax` | Standalone script |
| "File not found" | Search for file with `find_by_name` | Ask user for path |
| "ReadTimeout" | Accept results (local linter fallback) | Already working |
| "Circuit breaker open" | Accept results (local linter fallback) | Already working |
| Empty/null response | Use standalone script | Manual validation |
| "Permission denied" | Check file permissions | Use different file |
| MCP server crashed | Restart: `pkill -f pinescript_mcp.py` | Standalone script |

## Prevention: Pre-Flight Checklist

Run these checks BEFORE calling MCP tools:

### 1. File Exists
```python
# Check file exists
import os
if not os.path.exists(file_path):
    # Try to find it
    results = find_by_name(
        SearchDirectory="~/Documents",
        Pattern=os.path.basename(file_path)
    )
    if results:
        file_path = results[0].path
    else:
        # Ask user for correct path
        return f"File not found: {file_path}. Please provide correct path."
```

### 2. Path is Absolute
```python
# Ensure absolute path
if not file_path.startswith("/"):
    file_path = os.path.abspath(file_path)
```

### 3. File Size Check
```python
# Get file info (optional but helpful)
try:
    file_info = mcp2_get_file_info(path=file_path)
    file_size = file_info.size
    # If >50KB, expect local linter fallback
    if file_size > 50000:
        print("Large file detected - local linter may be used")
except:
    pass  # Continue anyway
```

## Recovery Workflows

### Workflow 1: Standard Validation

```python
def robust_validation(file_path: str) -> str:
    """Validation with automatic error recovery."""
    
    # Pre-flight
    if not os.path.exists(file_path):
        file_path = find_or_ask(file_path)
        if not file_path:
            return "ERROR: Cannot find file"
    
    # Primary: MCP validate_file
    try:
        result = mcp5_validate_file(file_path=file_path)
        
        # Check for errors
        if "ERROR: No code provided" in result:
            return fallback_standalone_script(file_path)
        
        if "ERROR: File not found" in result:
            # Try one more time with corrected path
            file_path = find_or_ask(file_path)
            result = mcp5_validate_file(file_path=file_path)
        
        # Check for expected "errors" that are actually OK
        if "ReadTimeout" in result or "Local Linter" in result:
            # This is normal - fallback working
            return result
        
        return result
        
    except Exception as e:
        # Ultimate fallback
        return fallback_standalone_script(file_path)

def fallback_standalone_script(file_path: str) -> str:
    """Use standalone validation script."""
    cmd = f'cd ~/pinescript_mcp && .venv/bin/python validate_file.py "{file_path}"'
    result = run_command(cmd, Blocking=True)
    return result.output
```

### Workflow 2: Handle "ERROR: No code provided"

This should NOT happen with `validate_file`, but if it does:

```python
# If you see this error:
result = mcp5_validate_file(file_path="/path/to/file.ps")
# Result: "ERROR: No code provided"

# Immediate recovery:
# 1. Verify path is correct
print(f"Validating: {file_path}")
print(f"File exists: {os.path.exists(file_path)}")

# 2. Use standalone script instead
result = run_command(
    f'cd ~/pinescript_mcp && .venv/bin/python validate_file.py "{file_path}"',
    Blocking=True
)

# 3. If that fails too, MCP server may need restart
run_command("pkill -f pinescript_mcp.py", Blocking=True)
time.sleep(2)  # Wait for restart
# Retry
```

### Workflow 3: File Not Found Recovery

```python
def find_or_ask(file_name: str) -> str:
    """Find file or ask user for path."""
    
    # Search common locations
    search_dirs = [
        "~/Documents/Quantify - Deeptest/Strategies",
        "~/Documents",
        "~/pinescript_mcp"
    ]
    
    for dir in search_dirs:
        results = find_by_name(
            SearchDirectory=dir,
            Pattern=os.path.basename(file_name)
        )
        if results:
            return results[0].path
    
    # Not found - ask user
    print(f"Cannot find: {file_name}")
    print("Please provide full path or available files:")
    
    # List available .ps files
    all_ps = find_by_name(
        SearchDirectory="~/Documents",
        Pattern="*.ps"
    )
    for i, file in enumerate(all_ps[:10]):  # Show first 10
        print(f"{i+1}. {file.path}")
    
    return None  # User must provide path
```

### Workflow 4: Timeout/Local Linter Fallback

```python
# When you see this in results:
# "Compiler: Local Linter (Tier 1)"
# "Note: Remote compiler unreachable (ReadTimeout)"

# This is NORMAL and EXPECTED for:
# - Large files (>500 lines)
# - Files with many imports
# - When TradingView API is slow

# Action: Display results to user
print("✅ Validation complete (local linter)")
print("Note: Local linter covers ~50% of common errors")
print("For full validation, copy to TradingView Pine Editor")
print("\nResults:")
print(result)

# DO NOT retry or treat as error
# DO NOT switch to different tool
# JUST display the results
```

## Error Patterns & Solutions

### Pattern 1: Stuck in Retry Loop

**Problem:**
```python
# BAD - infinite loop
while True:
    result = mcp5_validate_file(file_path)
    if "ERROR" in result:
        continue  # ❌ Never exits
```

**Solution:**
```python
# GOOD - max 1 retry, then fallback
max_retries = 1
for attempt in range(max_retries + 1):
    result = mcp5_validate_file(file_path)
    if "ERROR: File not found" in result and attempt < max_retries:
        file_path = find_file(file_path)
        continue
    break

# If still error after retry, use fallback
if "ERROR" in result:
    result = fallback_standalone_script(file_path)
```

### Pattern 2: Ignoring Fallback Results

**Problem:**
```python
# BAD - treating local linter as error
result = mcp5_validate_file(file_path)
if "Local Linter" in result:
    return "ERROR: Validation failed"  # ❌ Wrong!
```

**Solution:**
```python
# GOOD - accepting local linter results
result = mcp5_validate_file(file_path)
if "Local Linter" in result:
    # This is valid fallback, display results
    return result  # ✅ Correct
```

### Pattern 3: Not Using Fallback

**Problem:**
```python
# BAD - giving up on error
result = mcp5_validate_file(file_path)
if "ERROR" in result:
    return "Cannot validate"  # ❌ Gives up
```

**Solution:**
```python
# GOOD - using fallback
result = mcp5_validate_file(file_path)
if "ERROR" in result:
    # Try standalone script
    result = fallback_standalone_script(file_path)
    if "ERROR" in result:
        # Even fallback failed - ask user
        return "Please validate manually in TradingView"
```

## Testing Error Recovery

### Test Suite

Run these tests to verify recovery works:

```python
# Test 1: Non-existent file
mcp5_validate_file(file_path="/tmp/nonexistent.ps")
# Expected: "ERROR: File not found"
# Recovery: Search for file, ask user

# Test 2: Wrong path
mcp5_validate_file(file_path="relative/path.ps")
# Expected: Either works or "File not found"
# Recovery: Convert to absolute path

# Test 3: Large file
mcp5_validate_file(file_path="~/Documents/Quantify - Deeptest/Strategies/VIX.ps")
# Expected: Local Linter fallback (normal)
# Recovery: None needed, display results

# Test 4: MCP server down
# pkill -f "pinescript_mcp.py"
# mcp5_validate_file(file_path="/path/to/file.ps")
# Expected: Tool call fails
# Recovery: Use standalone script

# Test 5: Permission denied
# chmod 000 /tmp/test.ps
# mcp5_validate_file(file_path="/tmp/test.ps")
# Expected: "Permission denied"
# Recovery: Ask user to fix permissions
```

## Debugging Commands

When validation seems stuck:

```bash
# 1. Check if MCP server is running
ps aux | grep pinescript_mcp.py

# 2. Check server logs
tail -f ~/Library/Logs/Claude/mcp*.log | grep validate

# 3. Restart MCP server
pkill -f "pinescript_mcp.py"
# Wait 2 seconds for auto-restart

# 4. Test standalone script
cd ~/pinescript_mcp
.venv/bin/python validate_file.py "/path/to/file.ps"

# 5. Check file permissions
ls -la /path/to/file.ps

# 6. Verify file exists
file /path/to/file.ps
```

## Success Metrics

Validation is successful when:
- ✅ Results returned (errors or success)
- ✅ Line numbers provided for errors
- ✅ User can act on results
- ✅ No infinite loops or hangs
- ✅ Fallback used when needed

Validation has failed ONLY when:
- ❌ No results after all fallbacks
- ❌ File truly doesn't exist
- ❌ User must intervene

## Emergency Procedures

### If Everything Fails

1. **Restart MCP server:**
   ```bash
   pkill -f "pinescript_mcp.py"
   ```

2. **Use standalone script:**
   ```bash
   cd ~/pinescript_mcp
   .venv/bin/python validate_file.py "/path/to/file.ps"
   ```

3. **Manual validation:**
   ```
   Tell user: "Please open this file in TradingView Pine Editor for validation.
   Copy the content and paste into: https://www.tradingview.com/pine-script-reference/"
   ```

## Best Practices

1. **Always use validate_file for files** - More reliable than validate_syntax
2. **Accept local linter results** - Don't treat as errors
3. **Max 1 retry per error** - Don't loop
4. **Use fallback immediately** - Don't waste time troubleshooting
5. **Display partial results** - Better than nothing
6. **Log errors for debugging** - But don't block workflow
7. **Path issues = search + ask** - Don't guess

## Summary

**Golden Rules:**
- ✅ Every error has a recovery path
- ✅ Max 1 retry, then fallback
- ✅ Local linter = valid results
- ✅ Fallback = standalone script
- ✅ Never block user workflow
- ❌ Don't retry forever
- ❌ Don't ignore fallback results
- ❌ Don't give up without trying fallback

**When in doubt:** Use standalone script. It always works.
