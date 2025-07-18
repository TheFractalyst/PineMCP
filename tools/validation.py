# ruff: noqa: E501
"""
mcp/tools/validation.py
------------------------------------------------------------------------------
VALIDATE / REPAIR tools (2):
  - pine_compile  - compile source or an allowlisted file, with optional
                    doc-cross-referenced error explanations.
  - pine_repair   - targeted compiler-error fix or bulk v5->v6 migration.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Annotated, Literal

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

import core.db as _db
from core.caches import get_cached_file_validation, set_cached_file_validation
from core.config import _ALLOWED_BASE_DIRS, _TRANSPORT
from core.db import _COMMON_PARAM_NAMES
from core.hot_cache import cache_lookup
from core.pine_facade import call_pine_facade, enrich_error_with_code
from formatters.errors import (
    cap_response,
    extract_name_from_error,
    lookup_fix_hint,
    safe_error,
    strip_string_literals,
)
from templates.v5_migration import V5_TO_V6
from tools.lookup import lookup_entry

_Mode = Literal["targeted", "migrate"]
_COMPILE_HARD_TIMEOUT_S = max(5, int(os.getenv("PINE_COMPILE_HARD_TIMEOUT", "40")))
_EXPLAIN_LOOKUP_TIMEOUT_S = max(1, int(os.getenv("PINE_EXPLAIN_LOOKUP_TIMEOUT", "2")))
_EXPLAIN_MAX_DOC_LOOKUPS = max(1, int(os.getenv("PINE_EXPLAIN_MAX_DOC_LOOKUPS", "8")))


# -----------------------------------------------------------------------------
# File-branch helpers (internal - absorbed from old validate_file)
# -----------------------------------------------------------------------------

_PINESCRIPT_SIGNATURES = re.compile(
    r"(//@version=6|indicator\s*\(|strategy\s*\(|library\s*\()"
)
_PINESCRIPT_HEADER_LINES = 20


def _is_pinescript_content(resolved_path: str, max_lines: int = _PINESCRIPT_HEADER_LINES) -> bool:
    try:
        with open(resolved_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                if _PINESCRIPT_SIGNATURES.search(line):
                    return True
    except (OSError, UnicodeDecodeError):
        return False
    return False


def _has_pinescript_extension(path: str) -> bool:
    return path.endswith(".ps") or path.endswith(".pine")


def _resolve_file(file_path: str) -> tuple[str, str]:
    """Resolve a file path, returning (resolved, display).
    Raises ToolError on existence or extension issues.
    Validates path stays within allowed base directories.
    """
    if not file_path:
        raise ToolError("No file path provided. Provide an absolute path to a PineScript file.")

    try:
        resolved = os.path.realpath(file_path)
    except Exception:
        raise ToolError(
            "Invalid path provided. Could not resolve the file path. "
            "Please provide a valid absolute path."
        )

    if not any(resolved.startswith(d) for d in _ALLOWED_BASE_DIRS):
        raise ToolError(
            "File path is outside allowed directories. "
            "Provide a path under your home, Documents, Desktop, Projects, or repos directory."
        )

    display_name = os.path.basename(resolved)

    if not os.path.isfile(resolved):
        raise ToolError(f"File not found: {display_name}")

    if not _has_pinescript_extension(resolved):
        if not _is_pinescript_content(resolved):
            raise ToolError(
                "File does not appear to be PineScript. Provide a .ps/.pine file "
                "or a file containing PineScript content (//@version=6, indicator(), "
                "strategy(), or library())."
            )

    return resolved, display_name


# -----------------------------------------------------------------------------
# Shared rendering
# -----------------------------------------------------------------------------


def _render_success_summary(code: str) -> str:
    """Compact post-success analysis - script type, counts, features."""
    code_lines = code.strip().splitlines()
    is_strategy = any("strategy(" in line for line in code_lines)
    is_indicator = any("indicator(" in line for line in code_lines)
    is_library = any("library(" in line for line in code_lines)
    script_type = (
        "strategy" if is_strategy
        else ("indicator" if is_indicator
              else ("library" if is_library else "unknown"))
    )
    plots = sum(
        1 for line in code_lines
        if line.strip().startswith("plot(")
        or line.strip().startswith("plotshape(")
        or line.strip().startswith("plotchar(")
    )
    inputs = sum(1 for line in code_lines if "input." in line)
    has_request = any("request." in line for line in code_lines)
    imports = [line.strip() for line in code_lines if line.strip().startswith("import ")]
    var_count = sum(
        1 for line in code_lines
        if line.strip().startswith("var ") or line.strip().startswith("varip ")
    )
    has_methods = any("method " in line for line in code_lines)
    has_types = any(
        re.search(r'(?<!\w\.)type\s+\w+', line.split("//")[0])
        for line in code_lines if not line.strip().startswith("//")
    )

    analysis = [f"Script type: {script_type}", f"Lines: {len(code_lines)}"]
    if plots:
        analysis.append(f"Plots: {plots}")
    if inputs:
        analysis.append(f"Inputs: {inputs}")
    if var_count:
        analysis.append(f"Persistent vars (var/varip): {var_count}")
    if has_request:
        analysis.append("Uses request.*() (external data)")
    if imports:
        analysis.append(f"Imports: {len(imports)}")
        for imp in imports[:5]:
            analysis.append(f"  {imp[:80]}")
    if has_types:
        analysis.append("Uses custom types (UDT)")
    if has_methods:
        analysis.append("Uses method definitions")

    return "\n".join(f"  {a}" for a in analysis)


async def _render_errors(errors: list, warnings: list, explain: bool) -> list[str]:
    lines: list[str] = []
    explain_lookups_done = 0
    for i, err in enumerate(errors, 1):
        line_num = err.get("line", "?")
        col_num = err.get("column", "?")
        text = err.get("text", "Unknown error")
        err_type = err.get("type", "error").upper()
        lines.append(f"  ERROR {i} - Line {line_num}, Col {col_num} [{err_type}]")
        lines.append(f"    {text}")

        if explain and explain_lookups_done < _EXPLAIN_MAX_DOC_LOOKUPS:
            extracted_name = extract_name_from_error(text)
            if extracted_name:
                try:
                    doc_result = await asyncio.wait_for(
                        lookup_entry(extracted_name, None),
                        timeout=float(_EXPLAIN_LOOKUP_TIMEOUT_S),
                    )
                    if "not found" not in doc_result[:80].lower():
                        doc_lines = doc_result.splitlines()[:5]
                        lines.append(f"    Docs lookup for '{extracted_name}':")
                        for dl in doc_lines:
                            lines.append(f"      {dl}")
                    else:
                        lines.append(
                            f"    Docs lookup for '{extracted_name}': "
                            "not found (may be misspelled or v5-only syntax)"
                        )
                except asyncio.TimeoutError:
                    lines.append(
                        f"    Docs lookup for '{extracted_name}': timeout "
                        f"({_EXPLAIN_LOOKUP_TIMEOUT_S}s)"
                    )
                explain_lookups_done += 1

        hint = lookup_fix_hint(text)
        if hint:
            lines.append(f"    Fix hint: {hint}")
        lines.append("")

    for i, warn in enumerate(warnings, 1):
        line_num = warn.get("line", "?")
        col_num = warn.get("column", "?")
        text = warn.get("text", "Unknown warning")
        lines.append(f"  WARNING {i} - Line {line_num}, Col {col_num}")
        lines.append(f"    {text}")
        hint = lookup_fix_hint(text)
        if hint:
            lines.append(f"    Fix hint: {hint}")
        lines.append("")

    return lines


# -----------------------------------------------------------------------------
# TOOL: pine_compile - compile source or file
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Compile PineScript",
        readOnlyHint=True,
        openWorldHint=True,
        idempotentHint=True,
    )
)
async def pine_compile(
    code: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            max_length=500_000,
            description=(
                "Complete PineScript v6 source code to compile. Pass EITHER "
                "`code` OR `file_path`, not both. Use this for inline code. "
                "For files >500KB, use `file_path` + `file_content` instead."
            ),
        ),
    ] = None,
    file_path: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            max_length=4096,
            description=(
                "Absolute path to a PineScript v6 file. Pass EITHER `code` OR "
                "`file_path`, not both. Use this for scripts hosted on the "
                "same machine as this MCP server. Result is cached by "
                "(path, mtime, size) for sub-millisecond re-validation."
            ),
        ),
    ] = None,
    file_content: Annotated[
        str | None,
        Field(
            default=None,
            min_length=1,
            max_length=2_000_000,
            description=(
                "Remote-safe file payload. Use this when `file_path` points to "
                "a client-side path not reachable by the server. Can be used "
                "alone or with `file_path` as a display label. Cannot be "
                "combined with `code`."
            ),
        ),
    ] = None,
    explain: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "When true, each compiler error is cross-referenced against "
                "the v6 documentation and the matching doc entry is embedded "
                "inline. Use this while debugging; leave false for fast "
                "pre-commit validation."
            ),
        ),
    ] = False,
) -> str:
    """
    Compile PineScript v6 code and return a diagnostic report.

    WHEN TO USE:
      - Before suggesting code to the user, to catch errors proactively.
      - To confirm an edit still compiles.
      - With `explain=True` to debug a failing script alongside doc references.
      - With `file_path=...` for scripts available on the server host (stdio only).
      - With `file_path` + `file_content` for remote clients over SSE/HTTP.
      - With `file_content` alone for remote clients (auto-generates display name).

    WHEN NOT TO USE:
      - You already know a specific error and want an auto-fix -> pine_repair.
      - You want to generate a fresh template from scratch -> pine_scaffold.
    """
    if file_content is not None and code is not None:
        raise ToolError(
            "Pass exactly one input mode: either `code`, or `file_content` "
            "(optionally with `file_path` as a label), not both."
        )
    if code is not None and file_path is not None:
        raise ToolError(
            "Pass exactly one input mode: either `code`, or `file_path` "
            "(optionally with `file_content`), not both."
        )
    if code is None and file_path is None and file_content is None:
        raise ToolError("No input provided. Pass one of: `code`, `file_path`, or `file_content`.")

    try:
        display_header: str | None = None
        cache_key: tuple | None = None

        if file_path is not None:
            if file_content is not None:
                display_name = os.path.basename(file_path.strip()) or file_path.strip()
                file_code = file_content
                file_size = len(file_code.encode("utf-8"))
                line_count = file_code.count("\n") + 1
                display_header = (
                    f"FILE: {display_name}\n"
                    f"Size: {file_size:,} bytes | Lines: {line_count:,}\n"
                    + "=" * 80 + "\n"
                )
                code = file_code
            else:
                if _TRANSPORT in ("http", "sse") and not os.path.isfile(file_path):
                    raise ToolError(
                        f"Remote server cannot access file '{os.path.basename(file_path)}'. "
                        "Remote SSE/HTTP clients must pass file_content alongside file_path, "
                        "or use file_content alone. Example: file_path='script.ps', "
                        "file_content='<your code>'."
                    )
                resolved, display_name = _resolve_file(file_path)
                stat = os.stat(resolved)
                mtime_ns = stat.st_mtime_ns
                fsize = stat.st_size

                cached = get_cached_file_validation(resolved, mtime_ns, fsize)
                if cached:
                    return cached

                with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                    file_code = f.read()

                file_size = len(file_code.encode("utf-8"))
                line_count = file_code.count("\n") + 1
                display_header = (
                    f"FILE: {display_name}\n"
                    f"Size: {file_size:,} bytes | Lines: {line_count:,}\n"
                    + "=" * 80 + "\n"
                )
                cache_key = (resolved, mtime_ns, fsize)
                code = file_code

        elif file_content is not None:
            # file_content without file_path - auto-generate display name
            first_line = file_content.strip().splitlines()[0][:60] if file_content.strip() else ""
            if first_line.startswith("//"):
                display_name = first_line.lstrip("/ ").strip()
            else:
                display_name = f"remote_{int(time.time())}"
            file_size = len(file_content.encode("utf-8"))
            line_count = file_content.count("\n") + 1
            display_header = (
                f"FILE: {display_name}\n"
                f"Size: {file_size:,} bytes | Lines: {line_count:,}\n"
                + "=" * 80 + "\n"
            )
            code = file_content

        assert code is not None
        code = code.strip()
        if not code:
            return "ERROR: No code provided. Pass the complete PineScript v6 source code."

        try:
            result = await asyncio.wait_for(
                call_pine_facade(code),
                timeout=float(_COMPILE_HARD_TIMEOUT_S),
            )
        except asyncio.TimeoutError:
            raise ToolError(
                f"Compile timeout after {_COMPILE_HARD_TIMEOUT_S}s. "
                "Remote compiler may be slow/unavailable; retry shortly."
            )

        errors = enrich_error_with_code(result.get("errors", []), code)
        warnings = result.get("warnings", [])
        success = result.get("success", False)
        meta = result.get("meta", {})
        is_fallback = bool(meta.get("fallback"))
        compiler_name = "Local Syntax Validator (fallback)" if is_fallback else "TradingView v6"
        fallback_notice = (
            "NOTE: Remote compiler unavailable. Using local syntax validation.\n"
            "Full compilation may catch additional errors. Validate in TradingView's Pine Editor.\n\n"
            if is_fallback else ""
        )

        if success and not errors:
            name = meta.get("name", "")
            extra = f"\nMeta: {name}" if name else ""
            analysis_block = _render_success_summary(code)

            if warnings:
                warning_lines = await _render_errors([], warnings, explain=False)
                response = (
                    (display_header or "")
                    + f"VALID - Code compiles with warnings.{extra}\n"
                    f"Compiler: {compiler_name}\n"
                    f"Errors: 0 | Warnings: {len(warnings)}\n\n"
                    f"{fallback_notice}"
                    f"Code Analysis:\n{analysis_block}\n\n"
                    + "\n".join(warning_lines)
                )
            else:
                response = (
                    (display_header or "")
                    + f"VALID - Code compiles successfully.{extra}\n"
                    f"Compiler: {compiler_name}\n"
                    f"Errors: 0 | Warnings: 0\n\n"
                    f"{fallback_notice}"
                    f"Code Analysis:\n{analysis_block}"
                )
            capped = cap_response(response)
            if cache_key:
                set_cached_file_validation(*cache_key, capped)
            return capped

        total_issues = len(errors) + len(warnings)
        status = "FAILED" if explain else None

        lines: list[str] = []
        if display_header:
            lines.append(display_header.rstrip())

        if explain:
            lines.append("VALIDATION + DEBUG REPORT")
            lines.append("=" * 50)
            lines.append(f"Compiler: {compiler_name}")
            lines.append(f"Status: {status}")
            lines.append(f"Errors: {len(errors)} | Warnings: {len(warnings)}")
            if is_fallback:
                lines.append("NOTE: Remote compiler unavailable - local syntax validation only.")
            lines.append("")
        else:
            lines.append(f"COMPILATION ISSUES ({total_issues}):")
            lines.append(f"Compiler: {compiler_name}")
            lines.append(f"Errors: {len(errors)} | Warnings: {len(warnings)}")
            if is_fallback:
                lines.append("NOTE: Remote compiler unavailable - local syntax validation only.")
            lines.append("")

        lines.extend(await _render_errors(errors, warnings, explain))

        rendered = cap_response("\n".join(lines))
        if cache_key:
            set_cached_file_validation(*cache_key, rendered)
        return rendered

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_compile] {e}")
        raise ToolError(safe_error(e, "pine_compile"))


# -----------------------------------------------------------------------------
# pine_repair: mode="targeted" implementation (absorbed from fix_and_validate)
# -----------------------------------------------------------------------------


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


def _remove_when_param(code: str) -> str:
    """Remove when= parameter from strategy.entry/exit/close calls.

    Handles multiple calls and avoids matching when= inside nested function
    arguments (e.g., calcQty(when=true) inside a strategy call)."""
    removals: list[tuple[int, int]] = []
    call_re = re.compile(r'strategy\.(entry|exit|close)\s*\(')
    for call_match in call_re.finditer(code):
        open_end = call_match.end()
        depth = 1
        pos = open_end
        while pos < len(code) and depth > 0:
            if code[pos] == '(':
                depth += 1
            elif code[pos] == ')':
                depth -= 1
            pos += 1
        if depth != 0:
            continue

        body = code[open_end:pos - 1]
        when_info = _find_toplevel_when(body)
        if when_info is None:
            continue
        arg_off_start, arg_off_end = when_info
        removals.append((open_end + arg_off_start, open_end + arg_off_end))

    if not removals:
        return code

    result = code
    for start, end in sorted(removals, reverse=True):
        result = result[:start] + result[end:]
    return result


async def _repair_targeted(code: str, context: str) -> str:
    """Auto-fix driven by a specific compiler error or problem description."""
    from formatters.errors import _FIX_HINTS

    error_lower = context.lower()
    matched_hint = None
    best_score = 0
    for pattern, hint in _FIX_HINTS.items():
        pattern_lower = pattern.lower()
        if pattern_lower in error_lower:
            score = len(pattern_lower)
            if score > best_score:
                best_score = score
                matched_hint = hint

    identifier_match = re.search(r"['\"]([a-zA-Z_][\w.]*)['\"]", context)
    identifier = identifier_match.group(1) if identifier_match else None

    if matched_hint and identifier:
        matched_hint = matched_hint.replace("{name}", identifier)

    doc_context = ""
    if identifier and identifier.lower() not in _COMMON_PARAM_NAMES:
        try:
            cached_entry = cache_lookup(identifier)
            if not cached_entry:
                for ns in ["ta", "strategy", "math", "array", "str"]:
                    cached_entry = cache_lookup(f"{ns}.{identifier}")
                    if cached_entry:
                        break
            if cached_entry:
                doc = cached_entry.get("document", "")
                doc_context = (
                    f"\nDOC REFERENCE for '{identifier}':\n{doc[:300]}"
                )
            elif _db._name_index_built:
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

    fixed_code = code
    fix_applied = "No automatic fix available"
    fixes_list: list[str] = []

    bare_fn_pattern = re.compile(
        r'(?<!\.)\b(ema|sma|rsi|macd|atr|bb|stoch|wma|hma|vwap|crossover|'
        r'crossunder|highest|lowest|barssince|valuewhen|linreg|mom|'
        r'cum|change|pivothigh|pivotlow|supertrend|correlation)\s*\('
    )
    if bare_fn_pattern.search(strip_string_literals(fixed_code)):
        fixed_code = bare_fn_pattern.sub(r'ta.\1(', fixed_code)
        fixes_list.append("Added ta. namespace prefix to unqualified TA functions")

    transp_pattern = re.compile(r',\s*transp\s*=\s*\d+')
    if transp_pattern.search(strip_string_literals(fixed_code)):
        fixed_code = transp_pattern.sub('', fixed_code)
        fixes_list.append("Removed transp= parameter (v6: use color.new() instead)")

    if re.search(r'strategy\.(entry|exit|close)\s*\(', strip_string_literals(fixed_code)) and re.search(r',\s*when\s*=', strip_string_literals(fixed_code)):
        prev = fixed_code
        fixed_code = _remove_when_param(fixed_code)
        if fixed_code != prev:
            fixes_list.append("Removed when= parameter (v6: wrap in if block instead)")

    if "strategy.entry" in fixed_code and "strategy(" not in fixed_code:
        fixes_list.append("strategy.entry() requires strategy() declaration, not indicator()")

    implicit_bool_pattern = re.compile(r'\bif\s+(volume|close|open|high|low)(\[\d+\])?\b(?!\s*[<>=!])')
    if implicit_bool_pattern.search(strip_string_literals(fixed_code)):
        fixed_code = implicit_bool_pattern.sub(r'if \1\2 > 0', fixed_code)
        fixes_list.append("Added explicit > 0 comparison (v6: implicit bool casting removed)")

    bool_na_pattern = re.compile(r'\bbool\s+(\w+)\s*=\s*na\b')
    if bool_na_pattern.search(strip_string_literals(fixed_code)):
        fixed_code = bool_na_pattern.sub(r'var bool \1 = false', fixed_code)
        fixes_list.append("Changed 'bool x = na' to 'var bool x = false' (v6: bool can't be na)")

    if fixes_list:
        fix_applied = " | ".join(fixes_list)

    validation_result = None
    if fixed_code != code:
        try:
            fix_validation = await call_pine_facade(fixed_code)
            fix_fallback = bool(fix_validation.get("meta", {}).get("fallback"))
            if fix_validation.get("success"):
                validation_result = (
                    "[OK] Fixed code passes syntax check (remote compiler unavailable)"
                    if fix_fallback
                    else "[OK] Fixed code compiles successfully"
                )
            else:
                errs = fix_validation.get("errors", [])
                if errs:
                    validation_result = (
                        f"[!] Fixed code still has {len(errs)} error(s):\n" +
                        "\n".join(f"  Line {e.get('line', '?')}: {e.get('text', '?')}" for e in errs[:3])
                    )
                else:
                    validation_result = "[OK] Fixed code compiles successfully"
        except Exception:
            validation_result = "[!] Could not validate fix (compiler unavailable)"

    lines = [
        "REPAIR REPORT (targeted)",
        "=" * 50,
        f"Error: {context}",
        "",
        f"HINT: {matched_hint or 'No specific hint - check PineScript v6 syntax'}",
        "",
        f"Fix Applied: {fix_applied}",
    ]
    if doc_context:
        lines.append(doc_context)
    if validation_result:
        lines.extend(["", validation_result])
    if fixed_code != code:
        _out_code = fixed_code
        lines.extend(["", "FIXED CODE:", "```pine", _out_code, "```"])

    return cap_response("\n".join(lines))


# -----------------------------------------------------------------------------
# pine_repair: mode="migrate" implementation (absorbed from lookup_and_correct)
# -----------------------------------------------------------------------------


async def _repair_migrate(code: str, context: str) -> str:
    """Bulk v5->v6 migration + intent-aware doc context."""
    from core.db import query_async
    from formatters.errors import check_query_error

    validation = await asyncio.wait_for(call_pine_facade(code), timeout=40)
    errors = validation.get("errors", [])

    fixed_code = code
    changes_made: list[str] = []

    transp_pattern = re.compile(r",\s*transp\s*=\s*\d+")
    code_stripped = strip_string_literals(fixed_code)
    if transp_pattern.search(code_stripped):
        fixed_code = transp_pattern.sub("", fixed_code)
        changes_made.append("Removed transp= parameter (v6: use color.new())")

    bool_na = re.compile(r"\bbool\s+(\w+)\s*=\s*na\b")
    code_stripped = strip_string_literals(fixed_code)
    if bool_na.search(code_stripped):
        fixed_code = bool_na.sub(r"var bool \1 = false", fixed_code)
        changes_made.append("Changed 'bool x = na' to 'var bool x = false' (v6)")

    implicit_bool = re.compile(
        r"\bif\s+(volume|close|open|high|low)(\[\d+\])?\b(?!\s*[<>=!])"
    )
    code_stripped = strip_string_literals(fixed_code)
    if implicit_bool.search(code_stripped):
        fixed_code = implicit_bool.sub(r"if \1\2 > 0", fixed_code)
        changes_made.append("Added explicit > 0 (v6: implicit bool removed)")

    study_pattern = re.compile(r"\bstudy\s*\(")
    code_stripped = strip_string_literals(fixed_code)
    if study_pattern.search(code_stripped):
        fixed_code = study_pattern.sub("indicator(", fixed_code)
        changes_made.append("Replaced study() -> indicator() (v6)")

    fixed_stripped = strip_string_literals(fixed_code)
    for pattern, replacement in V5_TO_V6.items():
        if re.search(pattern, fixed_stripped):
            fixed_code = re.sub(pattern, replacement, fixed_code)
            fixed_stripped = strip_string_literals(fixed_code)
            changes_made.append(f"Replaced: {pattern} -> {replacement}")

    validation_after = await asyncio.wait_for(call_pine_facade(fixed_code), timeout=40)
    errors_after = validation_after.get("errors", [])

    intent_results = await query_async(context, 3)
    intent_err = check_query_error(intent_results)
    if intent_err:
        intent_results = {
            "ids": [[]],
            "metadatas": [[]],
            "documents": [[]],
            "distances": [[]],
        }

    lines = [
        "REPAIR REPORT (migrate)",
        "=" * 50,
        "",
    ]

    if errors:
        lines.append(f"BEFORE FIXES: {len(errors)} issue(s) found")
        for i, err in enumerate(errors[:3], 1):
            text = err.get("text", "?")
            line_num = err.get("line", "?")
            col_num = err.get("column", "?")
            err_type = err.get("type", "error")
            lines.append(
                f"  Issue {i} (Line {line_num}, Col {col_num} [{err_type}]): {text}"
            )
        if len(errors) > 3:
            lines.append(f"  ... and {len(errors) - 3} more issues")
        lines.append("")
    else:
        lines.append("BEFORE FIXES: No compilation errors. Code appears correct.")
        lines.append("")

    if changes_made:
        lines.append(f"NAMESPACE FIXES APPLIED: {len(changes_made)}")
        for change in changes_made:
            lines.append(f"  * {change}")
        lines.append("")
    else:
        lines.append("NAMESPACE FIXES: No v5->v6 namespace issues detected.")
        lines.append("")

    if errors_after:
        lines.append(f"AFTER FIXES: {len(errors_after)} issue(s) remain")
        for i, err in enumerate(errors_after[:3], 1):
            text = err.get("text", "?")
            line_num = err.get("line", "?")
            col_num = err.get("column", "?")
            err_type = err.get("type", "error")
            lines.append(
                f"  Issue {i} (Line {line_num}, Col {col_num} [{err_type}]): {text}"
            )
        if len(errors_after) > 3:
            lines.append(f"  ... and {len(errors_after) - 3} more issues")
        lines.append("")
    else:
        lines.append("AFTER FIXES: All issues resolved. Code compiles successfully.")
        lines.append("")

    lines.append(f"RELEVANT DOCS FOR '{context}':")
    lines.append("-" * 40)
    if intent_results.get("ids") and intent_results["ids"][0]:
        for i, (meta, doc, dist) in enumerate(
            zip(
                intent_results["metadatas"][0],
                intent_results["documents"][0],
                intent_results["distances"][0],
            ),
            1,
        ):
            name = meta.get("name", "?")
            syntax = meta.get("syntax", "")
            ns = meta.get("namespace") or ""
            ns_prefix = f"{ns}." if ns and not name.startswith(f"{ns}.") else ""
            url = meta.get("url", "")
            lines.append(f"  {i}. {ns_prefix}{name}")
            if syntax:
                lines.append(f"     {syntax[:100]}")
            if url:
                lines.append(f"     URL: {url}")
    else:
        lines.append("  (No relevant docs found)")

    if fixed_code != code:
        lines.append("")
        lines.append("FIXED CODE:")
        lines.append("```pine")
        lines.append(fixed_code)
        lines.append("```")

    return cap_response("\n".join(lines))


# -----------------------------------------------------------------------------
# TOOL: pine_repair - targeted fix OR bulk v5->v6 migration
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Repair PineScript",
        readOnlyHint=False,
        openWorldHint=True,
        destructiveHint=False,
        idempotentHint=False,
    )
)
async def pine_repair(
    code: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500_000,
            description="The PineScript v6 code to repair (full script or a snippet).",
        ),
    ],
    context: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description=(
                "For mode='targeted': the verbatim compiler error message or a "
                "short description of the problem. For mode='migrate': a plain-"
                "English description of what the code is supposed to do - used "
                "to fetch intent-relevant docs alongside the migration."
            ),
        ),
    ],
    mode: Annotated[
        _Mode,
        Field(
            default="targeted",
            description=(
                "'targeted' (default) - narrow, error-driven fixes for a specific "
                "compiler complaint. 'migrate' - apply every known v5->v6 "
                "replacement in one pass and recompile."
            ),
        ),
    ] = "targeted",
) -> str:
    """
    Repair PineScript v6 source code.

    MODES:
      - targeted: You have a specific compiler error. Returns the minimal
                  fix, validates it, and cites the relevant doc entry.
      - migrate:  You have legacy (v5-ish) code. Applies every known v5->v6
                  replacement and recompiles, plus surfaces intent-relevant
                  doc entries.

    WHEN TO USE:
      - A compile fails and you need the change to make it pass (targeted).
      - You inherited a v5 script and want to modernize it (migrate).

    WHEN NOT TO USE:
      - You just want to know *if* code compiles -> pine_compile.
      - You're writing fresh code from a prompt -> pine_scaffold.
    """
    try:
        code_stripped = code.strip()
        context_stripped = context.strip()
        if not code_stripped:
            raise ToolError("No code provided. Pass the PineScript v6 source to repair.")
        if not context_stripped:
            raise ToolError(
                "No context provided. Describe the error or the code's intent."
            )

        if mode == "migrate":
            return await _repair_migrate(code_stripped, context_stripped)
        return await _repair_targeted(code_stripped, context_stripped)

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_repair] {e}")
        if _db._chroma_breaker.is_open():
            from formatters.errors import circuit_breaker_msg
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "pine_repair"))
