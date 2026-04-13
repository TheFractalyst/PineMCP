# Claude Code Integration Guide for PineScript MCP

## Issue: "ERROR: No code provided"

When Claude Code tries to validate large PineScript files (>10KB), the MCP tools may receive empty parameters due to serialization issues in the IDE's MCP integration layer.

### Root Cause

Claude Code has limitations when passing large string parameters (>30KB) to MCP tools. The parameter gets truncated or lost during serialization, causing the MCP server to receive an empty string.

### Solution: Use the Helper Script

A standalone validation script bypasses Claude Code's parameter limitations by directly invoking the MCP server's validation logic.

## Quick Fix

### 1. Direct Validation Script

```bash
# Navigate to MCP directory
cd ~/pinescript_mcp

# Validate any file
.venv/bin/python validate_file.py "/path/to/your/file.ps"

# Example: Validate my_strategy.ps
.venv/bin/python validate_file.py "~/Documents/my_strategy.ps"
```

### 2. Create Workflow Command

Create `@~/.windsurf/workflows/pine-validate.md`:

```markdown
---
description: Validate PineScript file using direct MCP call
---

# PineScript File Validation Workflow

// turbo
1. Run validation script:
   ```bash
   cd ~/pinescript_mcp && .venv/bin/python validate_file.py "$FILE_PATH"
   ```

2. Review validation results and fix any errors reported.

3. If needed, use validate_and_explain for detailed fix guidance.
```

## Workarounds for Claude Code

### Option 1: Chunk Validation (Small Files <10KB)

For files under 10KB, Claude Code can pass the content directly:

```python
# Claude reads the file
content = read_file("/path/to/file.ps")

# Then calls MCP tool with content
mcp5_validate_syntax(code=content)
```

### Option 2: Bash Wrapper (Medium Files 10-30KB)

```bash
# Create a wrapper that handles the file reading
cat file.ps | python -c "
import sys
import json
# Read stdin
code = sys.stdin.read()
# Output JSON for MCP
print(json.dumps({'code': code}))
"
```

### Option 3: Direct Script (Large Files >30KB) ✅ RECOMMENDED

Use `validate_file.py` as shown above. This bypasses Claude Code entirely and directly calls the MCP validation logic.

## Why This Happens

1. **MCP Protocol Limits**: JSON-RPC has practical limits on parameter sizes
2. **IDE Serialization**: Claude Code may truncate large parameters during serialization
3. **Network Buffer**: MCP communication may have buffer size constraints

## Permanent Fix for Claude Code

### Update Claude Code Skills

Claude Code should:

1. **Detect large files** before calling MCP tools:
   ```python
   if file_size > 10_000:  # 10KB threshold
       use_validation_script()
   else:
       call_mcp_tool_directly()
   ```

2. **Use streaming** for large content:
   - Break file into chunks
   - Validate incrementally
   - Aggregate results

3. **File path support**: Request MCP server to accept file paths instead of content:
   ```python
   # Ideal future API
   mcp5_validate_file(file_path="/path/to/file.ps")
   ```

## Testing the Fix

### Test 1: Small File (Should Work via MCP)
```bash
# Create small test file
echo '//@version=6
indicator("test")
plot(close)' > /tmp/small.ps

# Test via script
.venv/bin/python validate_file.py /tmp/small.ps
```

### Test 2: Large File (Use Script)
```bash
# Test my_strategy.ps (781 lines, 34KB)
.venv/bin/python validate_file.py "~/Documents/my_strategy.ps"
```

### Test 3: Verify MCP Tool Still Works
```python
# In Claude Code, try with small inline code:
code = """
//@version=6
indicator("test")
undefined_function()
"""

# This should work
mcp5_validate_syntax(code=code)
```

## Monitoring

Check MCP server logs for parameter size issues:

```bash
# View MCP server logs
tail -f ~/Library/Logs/Claude/mcp*.log | grep validate_syntax
```

Look for:
- Empty parameter warnings
- Truncation errors
- JSON parse failures

## Feature Request for MCP Server

### Add File-Based Validation Tool

```python
@mcp.tool
async def validate_file(
    file_path: Annotated[str, Field(description="Absolute path to PineScript file")]
) -> str:
    """Validate a PineScript file by path instead of content.
    
    This avoids MCP parameter size limitations for large files.
    """
    if not os.path.exists(file_path):
        return "ERROR: File not found"
    
    with open(file_path, 'r') as f:
        code = f.read()
    
    return await validate_syntax(code=code)
```

Benefits:
- ✅ No parameter size limits
- ✅ Simpler Claude Code integration
- ✅ Better performance (no serialization overhead)
- ✅ Works with any file size

## Summary

### Current State
- ❌ Claude Code cannot pass large files (>30KB) to MCP tools
- ✅ MCP server validation logic works perfectly
- ✅ Timeout fix working (20s default)
- ✅ Empty parameter handling working

### Immediate Solution
Use `validate_file.py` script for large files:
```bash
.venv/bin/python validate_file.py "/path/to/file.ps"
```

### Long-term Solution
1. Add file-path-based validation tool to MCP server
2. Update Claude Code to detect large files and use appropriate method
3. Implement streaming validation for incremental feedback

---

**Status**: Workaround implemented ✅  
**Next Steps**: Test validation script with my_strategy.ps  
**Updated**: April 6, 2026
