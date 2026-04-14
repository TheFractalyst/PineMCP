# PineScript-v6 MCP | © 2025-2026 @Fractalyst
# ruff: noqa: E501
"""
mcp/tools/validation.py
──────────────────────────────────────────────────────────────────────────────
VALIDATE tools (5): validate_syntax, validate_and_explain, fix_and_validate,
                    debug_pine_facade, validate_file
"""

from __future__ import annotations

import json
import os
import re
from typing import Annotated

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

import core.caches as _caches_module
import core.db as _db
from core.caches import get_cached_file_validation, set_cached_file_validation
from core.config import _ALLOWED_BASE_DIRS
from core.db import _COMMON_PARAM_NAMES
from core.hot_cache import cache_lookup
from core.pine_facade import call_pine_facade, enrich_error_with_code, pine_cb
from formatters.errors import (
    cap_response,
    extract_name_from_error,
    lookup_fix_hint,
    safe_error,
)
from tools.lookup import _lookup_entry

# ─────────────────────────────────────────────────────────────────────────────
# TOOL 11: validate_syntax
# ─────────────────────────────────────────────────────────────────────────────


@tool(annotations=ToolAnnotations(title="Validate PineScript Code", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def validate_syntax(
    code: Annotated[str, Field(
        min_length=1,
        max_length=50000,
        description="Complete PineScript v6 source code to validate",
    )],
) -> str:
    """
    Validate PineScript v6 code using TradingView's official pine-facade
    compiler — the exact same compiler used by TradingView's web editor.

    Returns real compilation errors with line numbers and column positions.
    Use BEFORE suggesting code to the user to catch errors proactively.

    Note: When the remote compiler is unreachable, falls back to a local
    linter (Tier 1) that catches ~50% of common errors. The response
    indicates which compiler was used.

    Args:
        code: Complete PineScript v6 source code to validate
    """
    try:
        code = code.strip()
        if not code:
            return "ERROR: No code provided. Pass the complete PineScript v6 source code to validate."

        result = await call_pine_facade(code)

        errors = enrich_error_with_code(result.get("errors", []), code)
        warnings = result.get("warnings", [])
        success = result.get("success", False)
        meta = result.get("meta", {})
        is_fallback = meta.get("fallback") == "local_linter_tier1"
        compiler_label = "Local Linter (Tier 1)" if is_fallback else "TradingView pine-facade v6"

        if success and not errors and not warnings:
            name = meta.get("name", "")
            extra = f"\nMeta: {name}" if name else ""
            fallback_note = "\nNote: Validated by local linter (remote compiler unavailable)." if is_fallback else ""

            # Add quick code analysis for richer output
            code_lines = code.strip().splitlines()
            code_analysis = []
            is_strategy = any("strategy(" in line for line in code_lines)
            is_indicator = any("indicator(" in line for line in code_lines)
            is_library = any("library(" in line for line in code_lines)
            script_type = "strategy" if is_strategy else ("indicator" if is_indicator else ("library" if is_library else "unknown"))
            plots = sum(1 for line in code_lines if line.strip().startswith("plot(") or line.strip().startswith("plotshape(") or line.strip().startswith("plotchar("))
            inputs = sum(1 for line in code_lines if "input." in line)
            has_request = any("request." in line for line in code_lines)
            imports = [line.strip() for line in code_lines if line.strip().startswith("import ")]
            var_count = sum(1 for line in code_lines if line.strip().startswith("var ") or line.strip().startswith("varip "))
            has_methods = any("method " in line for line in code_lines)
            has_types = any("type " in line and "//" not in line.split("type ")[0][-3:] for line in code_lines if not line.strip().startswith("//"))

            code_analysis.append(f"Script type: {script_type}")
            code_analysis.append(f"Lines: {len(code_lines)}")
            if plots:
                code_analysis.append(f"Plots: {plots}")
            if inputs:
                code_analysis.append(f"Inputs: {inputs}")
            if var_count:
                code_analysis.append(f"Persistent vars (var/varip): {var_count}")
            if has_request:
                code_analysis.append("Uses request.*() (external data)")
            if imports:
                code_analysis.append(f"Imports: {len(imports)}")
                for imp in imports[:5]:
                    code_analysis.append(f"  {imp[:80]}")
            if has_types:
                code_analysis.append("Uses custom types (UDT)")
            if has_methods:
                code_analysis.append("Uses method definitions")

            analysis_block = "\n".join(f"  {a}" for a in code_analysis)

            return cap_response(
                f"VALID — Code compiles successfully.{extra}{fallback_note}\n"
                f"Compiler: {compiler_label}\n"
                f"Errors: 0 | Warnings: 0\n\n"
                f"Code Analysis:\n{analysis_block}"
            )

        lines = []
        total_issues = len(errors) + len(warnings)
        lines.append(f"COMPILATION ISSUES ({total_issues}):")
        lines.append(f"Compiler: {compiler_label}")
        if is_fallback:
            note = meta.get("note", "Local linter catches ~50% of common errors.")
            lines.append(f"Note: {note}")
        lines.append(f"Errors: {len(errors)} | Warnings: {len(warnings)}")
        lines.append("")

        for i, err in enumerate(errors, 1):
            line_num = err.get("line", "?")
            col_num = err.get("column", "?")
            text = err.get("text", "Unknown error")
            err_type = err.get("type", "error").upper()
            hint = lookup_fix_hint(text)
            lines.append(f"  ERROR {i} — Line {line_num}, Col {col_num} [{err_type}]")
            lines.append(f"    {text}")
            if hint:
                lines.append(f"    Fix hint: {hint}")
            lines.append("")

        for i, warn in enumerate(warnings, 1):
            line_num = warn.get("line", "?")
            col_num = warn.get("column", "?")
            text = warn.get("text", "Unknown warning")
            hint = lookup_fix_hint(text)
            lines.append(f"  WARNING {i} — Line {line_num}, Col {col_num}")
            lines.append(f"    {text}")
            if hint:
                lines.append(f"    Fix hint: {hint}")
            lines.append("")

        return cap_response("\n".join(lines))

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[validate_syntax] {e}")
        raise ToolError(safe_error(e, "validate_syntax"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 12: validate_and_explain
# ─────────────────────────────────────────────────────────────────────────────


@tool(annotations=ToolAnnotations(title="Validate and Explain Errors", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def validate_and_explain(
    code: Annotated[str, Field(
        min_length=1,
        max_length=50000,
        description="Complete PineScript v6 source code to validate",
    )],
) -> str:
    """
    Validate PineScript v6 code AND cross-reference any errors against
    the documentation database to provide precise fix instructions.

    Combines pine-facade compilation + semantic doc lookup into one call.
    For each error, extracts the relevant identifier and looks up the
    correct syntax from the PineScript v6 docs.

    Use when helping user debug failing PineScript code. For pure
    validation without doc lookups, use validate_syntax() instead.
    """
    try:
        code = code.strip()
        if not code:
            return "ERROR: No code provided. Pass the complete PineScript v6 source code to validate and explain."

        result = await call_pine_facade(code)

        errors = enrich_error_with_code(result.get("errors", []), code)
        warnings = result.get("warnings", [])
        success = result.get("success", False)
        meta = result.get("meta", {})
        is_fallback = meta.get("fallback") == "local_linter_tier1"
        compiler_label = "Local Linter (Tier 1)" if is_fallback else "TradingView pine-facade v6"

        if success and not errors and not warnings:
            # Quick code analysis on success
            code_lines = code.strip().splitlines()
            plots = sum(
                1
                for line in code_lines
                if line.strip().startswith("plot(") or line.strip().startswith("plotshape(")
            )
            inputs = sum(1 for line in code_lines if "input." in line)
            is_strategy = any("strategy(" in line for line in code_lines)
            is_indicator = any("indicator(" in line for line in code_lines)
            script_type = (
                "strategy"
                if is_strategy
                else ("indicator" if is_indicator else "library")
            )
            fallback_note = "\nNote: Validated by local linter (remote compiler unavailable)." if is_fallback else ""

            return cap_response(
                f"VALIDATION + DEBUG REPORT\n"
                f"{'=' * 50}\n"
                f"Compiler: {compiler_label}\n"
                f"Status: PASSED\n"
                f"Errors: 0 | Warnings: 0\n\n"
                f"Code Analysis:\n"
                f"  Script type: {script_type}\n"
                f"  Lines: {len(code_lines)}\n"
                f"  Plots: {plots}\n"
                f"  Inputs: {inputs}\n"
                f"{fallback_note}"
            )

        # Process errors with doc cross-reference
        lines = []
        lines.append("VALIDATION + DEBUG REPORT")
        lines.append("=" * 50)
        lines.append(f"Compiler: {compiler_label}")
        if is_fallback:
            note = meta.get("note", "Local linter catches ~50% of common errors.")
            lines.append(f"Note: {note}")
        lines.append("Status: FAILED")
        lines.append(f"Errors: {len(errors)} | Warnings: {len(warnings)}")
        lines.append("")

        for i, err in enumerate(errors, 1):
            line_num = err.get("line", "?")
            col_num = err.get("column", "?")
            text = err.get("text", "Unknown error")

            lines.append(f"ERROR {i} — Line {line_num}, Col {col_num}:")
            lines.append(f"  Compiler says: {text}")

            # Try to extract a name from the error and look it up
            extracted_name = extract_name_from_error(text)
            if extracted_name:
                lines.append(f"  Docs lookup for '{extracted_name}':")
                doc_result = await _lookup_entry(extracted_name, None)
                if "not found" not in doc_result[:80].lower():
                    # Show first 5 lines of the doc result
                    doc_lines = doc_result.splitlines()[:5]
                    for dl in doc_lines:
                        lines.append(f"    {dl}")
                else:
                    lines.append(
                        "    Not found in docs — may be misspelled or v5-only syntax"
                    )

            hint = lookup_fix_hint(text)
            if hint:
                lines.append(f"  Fix hint: {hint}")
            lines.append("")

        for i, warn in enumerate(warnings, 1):
            text = warn.get("text", "Unknown warning")
            lines.append(f"WARNING {i} — Line {warn.get('line', '?')}:")
            lines.append(f"  {text}")
            hint = lookup_fix_hint(text)
            if hint:
                lines.append(f"  Fix hint: {hint}")
            lines.append("")

        return cap_response("\n".join(lines))

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[validate_and_explain] {e}")
        raise ToolError(safe_error(e, "validate_and_explain"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 13: fix_and_validate
# ─────────────────────────────────────────────────────────────────────────────


@tool(annotations=ToolAnnotations(title="Fix and Validate Code", readOnlyHint=True, openWorldHint=True, destructiveHint=False, idempotentHint=False))
async def fix_and_validate(
    code: Annotated[str, Field(
        min_length=1,
        max_length=50000,
        description="The failing PineScript v6 code",
    )],
    error_description: Annotated[str, Field(
        min_length=1,
        max_length=500,
        description="The compiler error message (verbatim) or a description of the problem",
    )],
) -> str:
    """
    Given PineScript code and a description of what's wrong (or the
    compiler error text), look up the correct syntax in the docs and
    return the precise fix with validation confirmation.

    Use when the user has a specific error they want fixed.
    For code that may have v5 namespace issues or broader syntax problems
    without a specific error, use lookup_and_correct() instead.
    For pure validation without fixes, use validate_syntax().

    Args:
        code: The failing PineScript v6 code
        error_description: The error message or description of the problem
    """
    from formatters.errors import _FIX_HINTS
    try:
        code = code.strip()
        error_description = error_description.strip()
        if not code:
            return "ERROR: No code provided. Pass the failing PineScript v6 source code."
        if not error_description:
            return "ERROR: No error description provided. Describe the error or paste the compiler message."

        # Step 1: Find best matching hint using substring scan
        error_lower = error_description.lower()
        matched_hint = None
        best_score = 0

        for pattern, hint in _FIX_HINTS.items():
            pattern_lower = pattern.lower()
            # Score: longer pattern match = more specific = higher score
            if pattern_lower in error_lower:
                score = len(pattern_lower)
                if score > best_score:
                    best_score = score
                    matched_hint = hint

        # Step 2: Extract identifier from error if present
        identifier_match = re.search(
            r"['\"]([a-zA-Z_][\w.]*)['\"]", error_description
        )
        identifier = identifier_match.group(1) if identifier_match else None

        # Interpolate {name} placeholder in hints with the actual identifier
        if matched_hint and identifier:
            matched_hint = matched_hint.replace("{name}", identifier)

        # Step 3: Cross-reference identifier against MCP docs (fast path)
        doc_context = ""
        if identifier and identifier.lower() not in _COMMON_PARAM_NAMES:
            try:
                # Fast path: try hot cache first (sub-ms)
                cached_entry = cache_lookup(identifier)
                if not cached_entry:
                    # Try with common namespace prefixes
                    for ns in ["ta", "strategy", "math", "array", "str"]:
                        cached_entry = cache_lookup(f"{ns}.{identifier}")
                        if cached_entry:
                            break
                if cached_entry:
                    doc = cached_entry.get("document", "")
                    doc_context = (
                        f"\nDOC REFERENCE for '{identifier}':\n"
                        f"{doc[:300]}"
                    )
                elif _db._name_index_built:
                    # Name index lookup (fast, no fuzzy scan)
                    key = identifier.lower()
                    hits = _db._name_index.get(key)
                    if not hits:
                        for ns in ["ta", "strategy", "math", "array", "str"]:
                            hits = _db._name_index.get(f"{ns}.{key}")
                            if hits:
                                break
                    if hits:
                        doc_context = (
                            f"\nDOC REFERENCE for '{identifier}':\n"
                            f"{hits[0]['document'][:300]}"
                        )
            except Exception as e:
                logger.debug(f"Doc lookup for fix failed: {e}")

        # Step 4: Attempt auto-fix for common patterns
        fixed_code = code
        fix_applied = "No automatic fix available"
        fixes_list = []

        # Pattern 1: missing namespace (ema → ta.ema, sma → ta.sma, etc.)
        bare_fn_pattern = re.compile(
            r'(?<!\.)\b(ema|sma|rsi|macd|atr|bb|stoch|wma|hma|vwap|crossover|'
            r'crossunder|highest|lowest|barssince|valuewhen|linreg|mom|'
            r'cum|change|pivothigh|pivotlow|supertrend|correlation)\s*\('
        )
        if bare_fn_pattern.search(fixed_code):
            fixed_code = bare_fn_pattern.sub(r'ta.\1(', fixed_code)
            fixes_list.append("Added ta. namespace prefix to unqualified TA functions")

        # Pattern 2: v6 breaking change — transp= parameter removed
        transp_pattern = re.compile(r',\s*transp\s*=\s*\d+')
        if transp_pattern.search(fixed_code):
            fixed_code = transp_pattern.sub('', fixed_code)
            fixes_list.append("Removed transp= parameter (v6: use color.new() instead)")

        # Pattern 3: v6 breaking change — when= parameter removed from strategy.*
        # Use function-based replacement to handle arbitrarily nested parens.
        def _remove_when_param(code: str) -> str:
            """Remove when= parameter from strategy.entry/exit/close calls.

            Handles multiple calls and avoids matching when= inside nested
            function arguments (e.g., calcQty(when=true) inside a strategy call).
            """
            # Find all strategy.entry/exit/close( call boundaries
            removals: list[tuple[int, int]] = []  # (start, end) in code coords
            call_re = re.compile(r'strategy\.(entry|exit|close)\s*\(')
            for call_match in call_re.finditer(code):
                # Walk forward from opening ( to find matching )
                open_end = call_match.end()  # position after (
                depth = 1
                pos = open_end
                while pos < len(code) and depth > 0:
                    if code[pos] == '(':
                        depth += 1
                    elif code[pos] == ')':
                        depth -= 1
                    pos += 1
                if depth != 0:
                    continue  # unbalanced — skip

                # Scan the call body for a top-level , when= parameter
                body = code[open_end:pos - 1]
                when_info = _find_toplevel_when(body)
                if when_info is None:
                    continue
                arg_off_start, arg_off_end = when_info
                removals.append((open_end + arg_off_start, open_end + arg_off_end))

            if not removals:
                return code

            # Remove from end to start to preserve offsets
            result = code
            for start, end in sorted(removals, reverse=True):
                result = result[:start] + result[end:]
            return result

        def _find_toplevel_when(body: str) -> tuple[int, int] | None:
            """Find ', when=' at top level (depth 0) in a call body."""
            depth = 0
            i = 0
            while i < len(body):
                ch = body[i]
                if ch == '(':
                    depth += 1
                    i += 1
                elif ch == ')':
                    depth -= 1
                    i += 1
                elif depth == 0 and ch == ',':
                    m = re.match(r',\s*when\s*=', body[i:])
                    if m:
                        # Found top-level , when=. Find end of argument value.
                        val_pos = i + m.end()
                        val_depth = 0
                        while val_pos < len(body):
                            c = body[val_pos]
                            if c == '(':
                                val_depth += 1
                            elif c == ')':
                                if val_depth == 0:
                                    break
                                val_depth -= 1
                            elif c == ',' and val_depth == 0:
                                break
                            val_pos += 1
                        return (i, val_pos)
                    i += 1
                else:
                    i += 1
            return None

        if re.search(r'strategy\.(entry|exit|close)\s*\(', fixed_code) and re.search(r',\s*when\s*=', fixed_code):
            prev = fixed_code
            fixed_code = _remove_when_param(fixed_code)
            if fixed_code != prev:
                fixes_list.append("Removed when= parameter (v6: wrap in if block instead)")

        # Pattern 4: strategy.* called in indicator context
        if "strategy.entry" in fixed_code and "strategy(" not in fixed_code:
            fixes_list.append("strategy.entry() requires strategy() declaration, not indicator()")

        # Pattern 5: Implicit bool — if volume, if close (v6 needs explicit comparison)
        implicit_bool_pattern = re.compile(r'\bif\s+(volume|close|open|high|low)\b(?!\s*[<>=!])')
        if implicit_bool_pattern.search(fixed_code):
            fixed_code = implicit_bool_pattern.sub(r'if \1 > 0', fixed_code)
            fixes_list.append("Added explicit > 0 comparison (v6: implicit bool casting removed)")

        # Pattern 6: bool x = na (v6: bools can't be na)
        bool_na_pattern = re.compile(r'\bbool\s+(\w+)\s*=\s*na\b')
        if bool_na_pattern.search(fixed_code):
            fixed_code = bool_na_pattern.sub(r'var bool \1 = false', fixed_code)
            fixes_list.append("Changed 'bool x = na' to 'var bool x = false' (v6: bool can't be na)")

        if fixes_list:
            fix_applied = " | ".join(fixes_list)

        # Step 5: Validate the fixed code using local linter (Tier 1 only).
        # Note: We do NOT call the remote pine-facade here for speed.
        # The local linter catches ~50% of errors. For full validation,
        # call validate_syntax() on the fixed code after this tool returns.
        validation_result = None
        if fixed_code != code:
            try:
                from pine_linter import lint as _pine_lint
                lint_result = _pine_lint(fixed_code)
                lint_dict = lint_result.to_dict()
                if lint_dict["success"]:
                    validation_result = "✅ Fixed code passes local linter (Tier 1)"
                else:
                    errs = lint_dict.get("errors", [])
                    if errs:
                        validation_result = (
                            f"⚠️ Fixed code still has {len(errs)} error(s):\n" +
                            "\n".join(f"  Line {e['line']}: {e['text']}" for e in errs[:3])
                        )
                    else:
                        validation_result = "✅ Fixed code passes local linter (Tier 1)"
            except Exception:
                validation_result = "⚠️ Could not validate fix (linter unavailable)"

        # Build response
        lines = [
            "FIX AND VALIDATE REPORT",
            f"{'='*50}",
            f"Error: {error_description}",
            "",
            f"HINT: {matched_hint or 'No specific hint — check PineScript v6 syntax'}",
            "",
            f"Fix Applied: {fix_applied}",
        ]
        if doc_context:
            lines.append(doc_context)
        if validation_result:
            lines.extend(["", validation_result])
        if fixed_code != code:
            lines.extend(["", "FIXED CODE:", "```pine", fixed_code, "```"])

        return cap_response("\n".join(lines))

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[fix_and_validate] {e}")
        raise ToolError(safe_error(e, "fix_and_validate"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 17: debug_pine_facade
# ─────────────────────────────────────────────────────────────────────────────


@tool(annotations=ToolAnnotations(title="Debug Pine Facade", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def debug_pine_facade(
    code: Annotated[str, Field(
            min_length=1,
            max_length=50000,
            description="Complete PineScript v6 source code to compile",
    )],
) -> str:
    """
    Diagnostic tool: compile code via pine-facade and return the FULL raw
    response alongside the normalized interpretation. Use for debugging
    when validate_syntax or validate_and_explain produce unexpected results.

    Do not use for normal validation or debugging user code — use
    validate_syntax() or validate_and_explain() instead. This tool is
    for diagnosing unexpected compiler behavior only.

    Args:
        code: Complete PineScript v6 source code to compile
    """
    try:
        code = code.strip()
        if not code:
            return "ERROR: No code provided. Pass the PineScript v6 source code to debug."

        result = await call_pine_facade(code)

        lines = []
        lines.append("DEBUG PINE-FACADE REPORT")
        lines.append("=" * 60)
        lines.append("")

        # Circuit breaker stats
        cb_stats = pine_cb.stats()
        lines.append("CIRCUIT BREAKER:")
        for k, v in cb_stats.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

        # Normalized result
        lines.append("NORMALIZED RESULT:")
        lines.append(f"  success: {result.get('success', '?')}")
        lines.append(f"  errors: {len(result.get('errors', []))}")
        lines.append(f"  warnings: {len(result.get('warnings', []))}")
        lines.append("")

        errors = result.get("errors", [])
        if errors:
            lines.append("ERRORS (normalized):")
            for i, err in enumerate(errors, 1):
                lines.append(
                    f"  [{i}] line={err.get('line')} col={err.get('column')} type={err.get('type')}"
                )
                lines.append(f"      text: {err.get('text', '?')}")
            lines.append("")

        warnings = result.get("warnings", [])
        if warnings:
            lines.append("WARNINGS (normalized):")
            for i, warn in enumerate(warnings, 1):
                lines.append(
                    f"  [{i}] line={warn.get('line')} col={warn.get('column')}"
                )
                lines.append(f"      text: {warn.get('text', '?')}")
            lines.append("")

        # Raw response
        raw = result.get("raw_response", {})
        raw_str = json.dumps(raw, indent=2, default=str)
        if len(raw_str) > 2000:
            raw_str = raw_str[:2000] + "\n  [...truncated — use debug_pine_facade for full output]"
        lines.append("RAW RESPONSE:")
        lines.append(raw_str)
        lines.append("")

        # Validation cache
        lines.append(f"Validation cache entries: {len(_caches_module._VALIDATION_CACHE)}")

        return cap_response("\n".join(lines))

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[debug_pine_facade] {e}")
        raise ToolError(safe_error(e, "debug_pine_facade"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 20: validate_file
# ─────────────────────────────────────────────────────────────────────────────


@tool(annotations=ToolAnnotations(title="Validate PineScript File", readOnlyHint=True, openWorldHint=True, idempotentHint=True))
async def validate_file(
    file_path: Annotated[str, Field(
        min_length=1,
        max_length=4096,
        description="Absolute path to PineScript v6 file to validate"
    )]
) -> str:
    """
    Validate a PineScript v6 file by path instead of content.

    This tool bypasses MCP parameter size limitations by reading the file
    directly on the server side. Use this for large files (>30KB) that
    cannot be passed as inline parameters through Claude Code.

    Optimization: caches results keyed on (path, mtime_ns, size). Re-validating
    an unchanged file returns the cached result in <1ms instead of ~2800ms.
    Runs local linter as fast-reject before remote compile.

    Args:
        file_path: Absolute path to the .ps file to validate

    Returns:
        Validation results in the same format as validate_syntax
    """
    if not file_path:
        return "ERROR: No file path provided. Provide an absolute path to a PineScript file."

    # Path safety: resolve symlinks, enforce .ps extension, allowlist directories
    try:
        resolved = os.path.realpath(file_path)
    except Exception:
        return "ERROR: Invalid path provided. Could not resolve the file path. Please provide a valid absolute path."

    # Display name: use basename only to avoid leaking absolute paths in response
    display_name = os.path.basename(resolved)

    if not resolved.endswith('.ps') and not resolved.endswith('.pine'):
        return "ERROR: Only .ps and .pine files are accepted. Please provide a PineScript file with a .ps or .pine extension."

    # Security: path must be within an allowed base directory
    # Use base + "/" to prevent prefix attacks (e.g. /home/user/Documents_evil)
    allowed = any(
        resolved.startswith(base + "/") or resolved == base
        for base in _ALLOWED_BASE_DIRS
    )
    if not allowed:
        safe_dirs = ", ".join(os.path.basename(str(d)) for d in _ALLOWED_BASE_DIRS)
        return (
            "ERROR: Access denied. File must be in an allowed directory.\n"
            f"Allowed directories: {safe_dirs}"
        )

    # Check file existence
    if not os.path.isfile(resolved):
        return f"ERROR: File not found: {display_name}"

    try:
        # Get file stats for cache key
        stat = os.stat(resolved)
        mtime_ns = stat.st_mtime_ns
        fsize = stat.st_size

        # Check file-level cache first (keyed on path+mtime+size)
        cached = get_cached_file_validation(resolved, mtime_ns, fsize)
        if cached:
            return cached

        # Reject oversized files before reading into memory
        if fsize > 500_000:
            return f"ERROR: File too large ({fsize:,} bytes). Maximum is 500KB."

        # Read file contents
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()

        file_size = len(code.encode("utf-8"))
        line_count = code.count("\n") + 1

        # Fast-reject: run local linter first (catches common errors in ~5ms)
        try:
            from pine_linter import lint as _pine_lint
            lint_result = _pine_lint(code)
            lint_dict = lint_result.to_dict()
            lint_errors = lint_dict.get("errors", [])
            lint_warnings = lint_dict.get("warnings", [])

            # Fast-reject: local linter found errors — skip expensive remote compile
            if lint_errors:
                response = f"FILE: {display_name}\n"
                response += f"Size: {file_size:,} bytes | Lines: {line_count:,}\n"
                response += "=" * 80 + "\n\n"

                total_issues = len(lint_errors) + len(lint_warnings)
                response += f"COMPILATION ISSUES ({total_issues})\n"
                response += "Compiler: Local Linter (Tier 1) -- fast-reject\n"
                response += f"Errors: {len(lint_errors)} | Warnings: {len(lint_warnings)}\n\n"

                for idx, err in enumerate(lint_errors, 1):
                    line = err.get("line", "?")
                    col = err.get("column", "?")
                    text = err.get("text", "Unknown error")
                    err_type = err.get("type", "error")
                    response += f"  ERROR {idx} -- Line {line}, Col {col} [{err_type.upper()}]\n"
                    response += f"    {text}\n"
                    hint = lookup_fix_hint(text)
                    if hint:
                        response += f"    Fix hint: {hint}\n"
                    response += "\n"

                for idx, warn in enumerate(lint_warnings, 1):
                    line = warn.get("line", "?")
                    col = warn.get("column", "?")
                    text = warn.get("text", "Unknown warning")
                    response += f"  WARNING {idx} -- Line {line}, Col {col}\n"
                    response += f"    {text}\n\n"

                # Do NOT cache linter-only failures — remote compiler may find
                # different/additional errors. Only cache confirmed remote failures.
                return cap_response(response)

        except Exception as e:
            logger.warning(f"Local linter failed for {display_name}: {e}")
            # Continue to remote compile even if linter failed

        # -- Linter passed clean -- proceed to remote compiler --
        # skip_lint=True avoids running the linter again inside call_pine_facade
        result = await call_pine_facade(code, skip_lint=True)

        errors = enrich_error_with_code(result.get("errors", []), code)
        warnings = result.get("warnings", [])
        success = result.get("success", False)
        meta = result.get("meta", {})
        is_fallback = meta.get("fallback") == "local_linter_tier1"
        compiler_label = "Local Linter (Tier 1)" if is_fallback else "TradingView pine-facade v6"

        # Build response with file info
        response = f"FILE: {display_name}\n"
        response += f"Size: {file_size:,} bytes | Lines: {line_count:,}\n"
        response += "=" * 80 + "\n\n"

        if success and not errors and not warnings:
            response += "VALID -- PineScript v6 code compiles successfully.\n\n"
            response += f"Compiler: {compiler_label}\n"
            response += "Errors: 0 | Warnings: 0\n"
            if is_fallback and meta.get("note"):
                response += f"\nNote: {meta['note']}\n"
            set_cached_file_validation(resolved, mtime_ns, fsize, response)
            return cap_response(response)

        # Has errors or warnings
        total_issues = len(errors) + len(warnings)
        response += f"{'COMPILATION ISSUES' if errors else 'WARNINGS'} ({total_issues})\n"
        response += f"Compiler: {compiler_label}\n"

        if is_fallback and meta.get("note"):
            response += f"Note: {meta['note']}\n"

        response += f"Errors: {len(errors)} | Warnings: {len(warnings)}\n\n"

        # Display errors
        for idx, err in enumerate(errors, 1):
            line = err.get("line", "?")
            col = err.get("column", "?")
            text = err.get("text", "Unknown error")
            err_type = err.get("type", "error")
            response += f"  ERROR {idx} -- Line {line}, Col {col} [{err_type.upper()}]\n"
            response += f"    {text}\n"

            hint = lookup_fix_hint(text)
            if hint:
                response += f"    Fix hint: {hint}\n"
            response += "\n"

        # Display warnings
        for idx, warn in enumerate(warnings, 1):
            line = warn.get("line", "?")
            col = warn.get("column", "?")
            text = warn.get("text", "Unknown warning")
            response += f"  WARNING {idx} -- Line {line}, Col {col}\n"
            response += f"    {text}\n\n"

        set_cached_file_validation(resolved, mtime_ns, fsize, response)
        return cap_response(response)

    except ToolError:
        raise
    except Exception as e:
        logger.exception("Unexpected error in validate_file")
        raise ToolError(safe_error(e, "validate_file"))
