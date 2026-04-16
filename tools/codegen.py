# PineScript-v6 MCP | © 2025-2026 @Fractalyst
# ruff: noqa: E501
"""
mcp/tools/codegen.py
──────────────────────────────────────────────────────────────────────────────
CODEGEN tools (3): generate_indicator, generate_strategy, lookup_and_correct
"""

from __future__ import annotations

import json
import re
from typing import Annotated

from fastmcp.exceptions import ToolError
from fastmcp.tools import tool
from loguru import logger
from mcp.types import ToolAnnotations
from pydantic import Field

import core.db as _db
from core.caches import codegen_cache_key, get_codegen_cache, set_codegen_cache
from core.db import query_async
from core.pine_facade import call_pine_facade
from formatters.errors import (
    cap_response,
    check_query_error,
    circuit_breaker_msg,
    safe_error,
    sanitize_pine_string,
    strip_string_literals,
)
from templates.indicators import (
    _INDICATOR_TEMPLATES,
    extract_indicator_keywords,
    map_input_to_param,
)
from templates.v5_migration import V5_TO_V6

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_PINE_IDENT = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize_pine_ident(name: str) -> str:
    """Sanitize a string to be a valid PineScript identifier."""
    clean = _PINE_IDENT.sub("_", name).strip("_")
    if not clean:
        return "param"
    # Ensure starts with letter or underscore
    if clean[0].isdigit():
        clean = f"_{clean}"
    return clean


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 14: generate_indicator
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Generate Indicator Template",
        readOnlyHint=False,
        openWorldHint=True,
        destructiveHint=False,
        idempotentHint=False,
    )
)
async def generate_indicator(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Indicator display name",
        ),
    ],
    description: Annotated[
        str,
        Field(
            default="",
            max_length=500,
            description="What the indicator calculates",
        ),
    ] = "",
    inputs: Annotated[
        str | None,
        Field(
            default=None,
            max_length=2000,
            description="Comma-separated input descriptions, e.g. 'length=14,src=close,mult=2.0'",
        ),
    ] = None,
    overlay: Annotated[
        bool,
        Field(
            default=False,
            description="True if indicator overlays the price chart",
        ),
    ] = False,
) -> str:
    """
    Generate a PineScript v6 indicator template with correct boilerplate.
    Searches docs for relevant functions and validates the output.

    Returns a complete indicator() script with inputs, calculation stub,
    and plot(). The template may need manual edits for complex logic.

    Args:
        name: Indicator display name
        description: What the indicator calculates
        inputs: List of input parameter descriptions (optional)
        overlay: True if indicator overlays the price chart
    """
    try:
        name = name.strip()
        if not name:
            raise ToolError("No indicator name provided. Pass a display name for the indicator.")
        safe_name = sanitize_pine_string(name)

        # ── Cache check: avoid re-compiling identical templates ──
        cache_key = codegen_cache_key(
            safe_name, description or "", inputs or "", overlay
        )
        cached_result = get_codegen_cache(cache_key)
        if cached_result:
            return cached_result

        # ── Phase 0: Check for known indicator templates ──
        template_source = "none"
        matched_keywords = extract_indicator_keywords(description or name)
        calc_stub = 'plot(close, "Price", color.blue)'  # safe default
        if matched_keywords:
            family = matched_keywords[0]
            if family in _INDICATOR_TEMPLATES:
                calc_stub, overlay_default = _INDICATOR_TEMPLATES[family]
                if not overlay:
                    overlay = overlay_default
                template_source = f"template:{family}"

        # ── Phase 1: Build input lines ──
        input_lines = []
        if inputs:
            for raw_inp in inputs.split(","):
                raw_inp = raw_inp.strip()
                if not raw_inp:
                    continue
                pine_type = "float"
                default_val = "1.0"
                var_name = ""
                display_name = raw_inp

                if "=" in raw_inp:
                    key, val = raw_inp.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    var_name = _sanitize_pine_ident(key)
                    display_name = key
                    inp_lower = key.lower()

                    if val.startswith('"') or val.startswith("'"):
                        pine_type = "string"
                        default_val = val
                    elif val in (
                        "close",
                        "open",
                        "high",
                        "low",
                        "hl2",
                        "hlc3",
                        "ohlc4",
                    ):
                        pine_type = "source"
                        default_val = val
                    elif val.lower() in ("true", "false"):
                        pine_type = "bool"
                        default_val = val.lower()
                    elif "." in val:
                        pine_type = "float"
                        default_val = val
                    else:
                        try:
                            int(val)
                            pine_type = "int"
                            default_val = val
                        except ValueError:
                            pine_type = "float"
                            default_val = val
                else:
                    inp_lower = raw_inp.lower()
                    var_name = _sanitize_pine_ident(raw_inp)
                    if inp_lower in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
                        pine_type = "source"
                        default_val = inp_lower
                    elif any(k in inp_lower for k in ("length", "period", "len")):
                        pine_type = "int"
                        default_val = "20"
                    elif any(k in inp_lower for k in ("source", "src")):
                        pine_type = "source"
                        default_val = "close"
                    elif any(k in inp_lower for k in ("mult", "factor", "multiplier")):
                        pine_type = "float"
                        default_val = "2.0"
                    elif any(k in inp_lower for k in ("color", "colour")):
                        pine_type = "color"
                        default_val = "color.blue"
                    elif any(k in inp_lower for k in ("enable", "use", "show")):
                        pine_type = "bool"
                        default_val = "true"

                if pine_type == "source":
                    input_lines.append(
                        f'{var_name} = input.source({default_val}, "{display_name}")'
                    )
                elif pine_type == "int":
                    input_lines.append(
                        f'int {var_name} = input.int({default_val}, "{display_name}")'
                    )
                elif pine_type == "float":
                    input_lines.append(
                        f'float {var_name} = input.float({default_val}, "{display_name}")'
                    )
                elif pine_type == "bool":
                    input_lines.append(
                        f'bool {var_name} = input.bool({default_val}, "{display_name}")'
                    )
                elif pine_type == "string":
                    input_lines.append(
                        f'string {var_name} = input.string({default_val}, "{display_name}")'
                    )
                elif pine_type == "color":
                    input_lines.append(
                        f'{var_name} = input.color({default_val}, "{display_name}")'
                    )

        # ── Phase 2: Search docs with namespace-aware queries ──
        relevant_funcs = []
        calc_stub_phase2 = 'plot(close, "Price", color.blue)'  # safe default

        if template_source == "none":
            enrich_terms = {
                "rsi": "ta.rsi relative strength index oscillator",
                "macd": "ta.macd moving average convergence divergence",
                "bollinger": "ta.bb bollinger bands standard deviation",
                "ema": "ta.ema exponential moving average",
                "sma": "ta.sma simple moving average",
                "atr": "ta.atr average true range volatility",
                "stochastic": "ta.stoch stochastic oscillator k d",
                "supertrend": "ta.supertrend super trend",
                "vwap": "ta.vwap volume weighted average price",
                "adl": "ta.accdist accumulation distribution line",
                "obv": "ta.obv on balance volume",
                "cci": "ta.cci commodity channel index",
                "mfi": "ta.mfi money flow index",
                "williams": "ta.wpr williams percent range",
            }
            kw = extract_indicator_keywords(description or name)
            enriched_query = (
                f"{description} {enrich_terms.get(kw[0], '')}" if kw else description
            )

            combined_results = await query_async(
                enriched_query, 10, where={"category": "function"}
            )
            db_err = check_query_error(combined_results)
            if db_err:
                return db_err

            best_meta = None
            best_dist = 1.0
            best_query_label = ""

            if combined_results.get("ids") and combined_results["ids"][0]:
                for i, (meta, dist) in enumerate(
                    zip(
                        combined_results["metadatas"][0],
                        combined_results["distances"][0],
                    )
                ):
                    fname = meta.get("name", "?")
                    fsyntax = meta.get("syntax", "")
                    if any(rf_name == fname for rf_name, _ in relevant_funcs):
                        continue
                    relevant_funcs.append((fname, f"//   {fname}: {fsyntax[:80]}"))

                    label = "ta" if fname.startswith("ta.") else "broad"
                    effective_dist = dist - (0.05 if label == "ta" else 0.0)
                    if best_meta is None or effective_dist < best_dist:
                        best_meta = meta
                        best_dist = effective_dist
                        best_query_label = label

            # Phase 3: Relevance gating
            if best_meta is not None:
                top_name = best_meta.get("name", "")

                strong_name_match = False
                for kw_str in extract_indicator_keywords(description or name):
                    if kw_str in top_name.lower():
                        strong_name_match = True
                        break

                is_ta_ns = top_name.startswith("ta.")
                accept = (
                    best_dist < 0.6
                    or (is_ta_ns and best_dist < 0.75)
                    or strong_name_match
                )

                if accept:
                    input_vars = []
                    for il in input_lines:
                        parts = il.split("=")
                        if len(parts) >= 1:
                            var_part = parts[0].strip()
                            tokens = var_part.split()
                            input_vars.append(tokens[-1] if tokens else var_part)

                    raw_params = best_meta.get("raw_parameters", "")
                    param_names = []
                    if raw_params:
                        try:
                            params = (
                                json.loads(raw_params)
                                if isinstance(raw_params, str)
                                else raw_params
                            )
                            param_names = [
                                p.get("name", "") for p in params if isinstance(p, dict)
                            ]
                        except (json.JSONDecodeError, TypeError):
                            pass

                    args_list = []
                    if input_vars and param_names:
                        for pv in input_vars:
                            matched_param = map_input_to_param(pv, param_names)
                            if matched_param:
                                args_list.append(f"{matched_param}={pv}")
                            else:
                                args_list.append(pv)
                    elif param_names:
                        if top_name.startswith("ta.") and param_names:
                            if param_names[0] in ("source", "src"):
                                args_list.append("source=close")
                            else:
                                args_list.append(f"{param_names[0]}=close")
                        for pn in param_names[1:]:
                            if "length" in pn.lower() or "period" in pn.lower():
                                args_list.append(f"{pn}=14")
                            elif "mult" in pn.lower() or "factor" in pn.lower():
                                args_list.append(f"{pn}=2.0")
                    else:
                        args_list = input_vars if input_vars else ["close"]

                    args = ", ".join(args_list) if args_list else "close"
                    calc_stub_phase2 = (
                        f"result = {top_name}({args})\n"
                        f'plot(result, "Result", color.orange)'
                    )
                    template_source = (
                        f"search:{top_name} (dist={best_dist:.3f}, {best_query_label})"
                    )
                else:
                    template_source = (
                        f"rejected:{top_name} (dist={best_dist:.3f}, too far)"
                    )
        else:
            # Template matched — still run a search to populate relevant_funcs section
            ta_results = await query_async(
                description or name,
                5,
                where={"$and": [{"category": "function"}, {"namespace": "ta"}]},
            )
            if ta_results.get("ids") and ta_results["ids"][0]:
                for meta in ta_results["metadatas"][0][:5]:
                    fname = meta.get("name", "?")
                    fsyntax = meta.get("syntax", "")
                    relevant_funcs.append((fname, f"//   {fname}: {fsyntax[:80]}"))

        # Use template stub if matched, otherwise use search-derived stub
        if not template_source.startswith("template:"):
            calc_stub = calc_stub_phase2

        # Extract just the formatted strings for output
        relevant_func_strings = [rf[1] for rf in relevant_funcs]

        # ── Phase 4: Generate template ──
        code = f"""//@version=6
indicator("{safe_name}", overlay={str(overlay).lower()}, shorttitle="{safe_name[:16]}")

// ── Inputs ──"""
        for il in input_lines:
            code += f"\n{il}"
        if not input_lines:
            code += "\n// (Add your inputs here with input.int, input.float, input.source, etc.)"

        code += f"""

// ── Calculations ──
// {description}
// Available functions from docs:"""
        for rf in relevant_func_strings:
            code += f"\n{rf}"
        if not relevant_func_strings:
            code += (
                "\n// (Use search_docs or suggest_functions to find relevant functions)"
            )

        code += f"""
{calc_stub}
"""

        # Validate
        validation = await call_pine_facade(code)
        errors = validation.get("errors", [])

        lines = []
        lines.append("GENERATED INDICATOR TEMPLATE:")
        lines.append("=" * 50)
        lines.append("```pine")
        lines.append(code)
        lines.append("```")
        lines.append("")

        if errors:
            lines.append(
                f"VALIDATION: {len(errors)} compilation issues (template may need manual fixes)"
            )
            for err in errors:
                lines.append(
                    f"  Line {err.get('line', '?')}, Col {err.get('column', '?')}: {err.get('text', '?')} [{err.get('type', '?')}]"
                )
        else:
            lines.append("VALIDATION: Template compiles successfully.")

        if relevant_func_strings:
            lines.append("")
            lines.append("RELEVANT FUNCTIONS from docs:")
            for rf in relevant_func_strings:
                lines.append(f"  {rf}")

        lines.append(f"\nSOURCE: {template_source}")

        result = cap_response("\n".join(lines))
        set_codegen_cache(cache_key, result)
        return result

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[generate_indicator] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "generate_indicator"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 15: generate_strategy
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Generate Strategy Template",
        readOnlyHint=False,
        openWorldHint=True,
        destructiveHint=False,
        idempotentHint=False,
    )
)
async def generate_strategy(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Strategy display name",
        ),
    ],
    description: Annotated[
        str,
        Field(
            default="",
            max_length=500,
            description="Trading strategy description",
        ),
    ] = "",
    initial_capital: Annotated[
        int,
        Field(
            default=10000,
            ge=1,
            le=1000000,
            description="Starting capital (default 10000)",
        ),
    ] = 10000,
    commission_pct: Annotated[
        float,
        Field(
            default=0.1,
            ge=0.0,
            le=1.0,
            description="Commission percentage (default 0.1)",
        ),
    ] = 0.1,
    pyramiding: Annotated[
        int,
        Field(
            default=1,
            ge=1,
            le=10,
            description="Max simultaneous positions (default 1)",
        ),
    ] = 1,
) -> str:
    """
    Generate a syntactically correct PineScript v6 strategy template.
    Validates the output with pine-facade before returning.
    Includes all required strategy() parameters and entry/exit scaffolding.

    Args:
        name: Strategy display name
        description: Trading strategy description
        initial_capital: Starting capital (default 10000)
        commission_pct: Commission percentage (default 0.1)
        pyramiding: Max simultaneous positions (default 1)
    """
    try:
        name = name.strip()
        if not name:
            raise ToolError("No strategy name provided. Pass a display name for the strategy.")
        safe_name = sanitize_pine_string(name)

        # ── Cache check: avoid re-compiling identical templates ──
        cache_key = codegen_cache_key(
            safe_name,
            description or "",
            f"{initial_capital}|{commission_pct}|{pyramiding}",
            True,
        )
        cached_result = get_codegen_cache(cache_key)
        if cached_result:
            return cached_result

        # Search docs for strategy-related functions
        search_desc = (description or "").strip() or name
        relevant = await query_async(search_desc, 5, where={"namespace": "strategy"})
        db_err = check_query_error(relevant)
        if db_err:
            return db_err
        # Build relevant function list
        relevant_funcs = []
        if relevant.get("ids") and relevant["ids"][0]:
            for meta in relevant["metadatas"][0][:5]:
                fname = meta.get("name", "?")
                fsyntax = meta.get("syntax", "")
                relevant_funcs.append(f"//   {fname}: {fsyntax[:80]}")

        # BUG FIX: Use correct v6 input.bool syntax (default value first, then title)
        desc_comment = f"\n// Description: {description}" if description else ""
        template = f"""//@version=6
strategy("{safe_name}", overlay=true,
    initial_capital={initial_capital},
    commission_type=strategy.commission.percent,
    commission_value={commission_pct},
    default_qty_type=strategy.percent_of_equity,
    default_qty_value=100,
    pyramiding={pyramiding},
    margin_long=0, margin_short=0,
    calc_on_every_tick=false)
{desc_comment}
// ── Inputs ──────────────────────────────────────────────────
enableLong  = input.bool(true,  "Enable Long",  group="Filters")
enableShort = input.bool(false, "Enable Short", group="Filters")
src         = input.source(close, "Source",     group="Settings")
fastLen     = input.int(12, "Fast Length", minval=1, group="Settings")
slowLen     = input.int(26, "Slow Length", minval=2, group="Settings")

// ── Calculations ─────────────────────────────────────────────
fastMA = ta.ema(src, fastLen)
slowMA = ta.ema(src, slowLen)

// ── Conditions ───────────────────────────────────────────────
longCondition  = ta.crossover(fastMA, slowMA)
shortCondition = ta.crossunder(fastMA, slowMA)

// ── Entries ──────────────────────────────────────────────────
if enableLong and longCondition and barstate.isconfirmed
    strategy.entry("Long", strategy.long)

if enableShort and shortCondition and barstate.isconfirmed
    strategy.entry("Short", strategy.short)

// ── Exits ────────────────────────────────────────────────────
strategy.exit("Long Exit",  from_entry="Long",
                profit=na, loss=na)
strategy.exit("Short Exit", from_entry="Short",
                profit=na, loss=na)

// ── Cleanup ──────────────────────────────────────────────────
if barstate.islast
    strategy.close_all()
"""

        # BUG FIX: Compile-guard: validate before returning
        validation = await call_pine_facade(template)
        if not validation["success"]:
            errors_str = "\n".join(
                f"  Line {e['line']}: {e['text']}" for e in validation["errors"][:5]
            )
            return (
                f"WARNING: Template generation failed validation:\n{errors_str}\n\n"
                f"Raw template for manual fix:\n```pine\n{template}\n```"
            )

        lines = []
        lines.append("GENERATED STRATEGY TEMPLATE")
        lines.append("=" * 50)
        lines.append("Validated: 0 compilation errors (OK)")
        lines.append("")
        lines.append("```pine")
        lines.append(template.strip())
        lines.append("```")

        if relevant_funcs:
            lines.append("")
            lines.append("RELEVANT STRATEGY FUNCTIONS from docs:")
            for rf in relevant_funcs:
                lines.append(f"  {rf}")

        result = cap_response("\n".join(lines))
        set_codegen_cache(cache_key, result)
        return result

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[generate_strategy] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "generate_strategy"))


# ─────────────────────────────────────────────────────────────────────────────
# TOOL 16: lookup_and_correct
# ─────────────────────────────────────────────────────────────────────────────


@tool(
    annotations=ToolAnnotations(
        title="Lookup and Correct Code",
        readOnlyHint=False,
        openWorldHint=True,
        destructiveHint=False,
        idempotentHint=False,
    )
)
async def lookup_and_correct(
    code: Annotated[
        str,
        Field(
            min_length=1,
            max_length=50000,
            description="The PineScript code (can be partial or full script)",
        ),
    ],
    error_description: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="What the code is supposed to do",
        ),
    ],
) -> str:
    """
    Given a PineScript code snippet and what it's supposed to do,
    validates it, looks up correct syntax for any issues, and returns
    a corrected version with explanations.

    Use when user shares code and asks 'what's wrong with this'.
    For a targeted fix when you already have a specific compiler error
    message, use fix_and_validate() instead.

    Args:
        code: The PineScript code (can be partial or full script)
        error_description: What the code is supposed to do
    """
    try:
        code = code.strip()
        error_description = error_description.strip()
        if not code:
            raise ToolError("No code provided. Pass the PineScript code snippet to look up and correct.")
        if not error_description:
            raise ToolError("No description provided. Describe what the code is supposed to do.")

        # Step 1: Validate
        validation = await call_pine_facade(code)
        errors = validation.get("errors", [])

        # Step 2: Apply ALL v5→v6 namespace fixes
        fixed_code = code
        code_stripped = strip_string_literals(code)  # for safe search gating
        changes_made = []

        # v6 breaking changes
        transp_pattern = re.compile(r",\s*transp\s*=\s*\d+")
        if transp_pattern.search(code_stripped):
            fixed_code = transp_pattern.sub("", fixed_code)
            changes_made.append("Removed transp= parameter (v6: use color.new())")

        bool_na = re.compile(r"\bbool\s+(\w+)\s*=\s*na\b")
        if bool_na.search(code_stripped):
            fixed_code = bool_na.sub(r"var bool \1 = false", fixed_code)
            changes_made.append("Changed 'bool x = na' to 'var bool x = false' (v6)")

        implicit_bool = re.compile(
            r"\bif\s+(volume|close|open|high|low)(\[\d+\])?\b(?!\s*[<>=!])"
        )
        if implicit_bool.search(code_stripped):
            fixed_code = implicit_bool.sub(r"if \1\2 > 0", fixed_code)
            changes_made.append("Added explicit > 0 (v6: implicit bool removed)")

        study_pattern = re.compile(r"\bstudy\s*\(")
        if study_pattern.search(code_stripped):
            fixed_code = study_pattern.sub("indicator(", fixed_code)
            changes_made.append("Replaced study() → indicator() (v6)")

        # Missing namespace: ema() → ta.ema(), etc.
        bare_fn_pattern = re.compile(
            r'(?<!\.)\b(ema|sma|rsi|macd|atr|bb|stoch|wma|hma|vwap|crossover|'
            r'crossunder|highest|lowest|barssince|valuewhen|linreg|mom|'
            r'cum|change|pivothigh|pivotlow|supertrend|correlation)\s*\('
        )
        if bare_fn_pattern.search(code_stripped):
            fixed_code = bare_fn_pattern.sub(r'ta.\1(', fixed_code)
            changes_made.append("Added ta. namespace prefix to unqualified TA functions")

        # Apply ALL V5→V6 namespace replacements
        for pattern, replacement in V5_TO_V6.items():
            if re.search(pattern, code_stripped):
                fixed_code = re.sub(pattern, replacement, fixed_code)
                changes_made.append(f"Replaced: {pattern} → {replacement}")

        # Step 3: Re-validate the fixed code
        validation_after = await call_pine_facade(fixed_code)
        errors_after = validation_after.get("errors", [])

        # Step 4: Search docs for intent
        intent_results = await query_async(error_description, 3)
        intent_err = check_query_error(intent_results)
        if intent_err:
            intent_results = {
                "ids": [[]],
                "metadatas": [[]],
                "documents": [[]],
                "distances": [[]],
            }

        lines = []
        lines.append("LOOKUP AND CORRECT REPORT")
        lines.append("=" * 50)
        lines.append("")

        # Show validation before
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

        # Show changes made
        if changes_made:
            lines.append(f"NAMESPACE FIXES APPLIED: {len(changes_made)}")
            for change in changes_made:
                lines.append(f"  • {change}")
            lines.append("")
        else:
            lines.append("NAMESPACE FIXES: No v5→v6 namespace issues detected.")
            lines.append("")

        # Show validation after
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
            lines.append(
                "AFTER FIXES: All issues resolved. Code compiles successfully."
            )
            lines.append("")

        # Show relevant docs for intent
        lines.append(f"RELEVANT DOCS FOR '{error_description}':")
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

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[lookup_and_correct] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "lookup_and_correct"))
