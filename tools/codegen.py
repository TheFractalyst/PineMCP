# ruff: noqa: E501
"""
mcp/tools/codegen.py
------------------------------------------------------------------------------
SCAFFOLD tool (1): pine_scaffold - generate a validated indicator OR
strategy template, searching docs for relevant functions.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Literal

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
)
from templates.indicators import (
    _INDICATOR_TEMPLATES,
    extract_indicator_keywords,
    map_input_to_param,
)

_Kind = Literal["indicator", "strategy"]

_PINE_IDENT = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize_pine_ident(name: str) -> str:
    """Sanitize a string to a valid PineScript identifier."""
    clean = _PINE_IDENT.sub("_", name).strip("_")
    if not clean:
        return "param"
    if clean[0].isdigit():
        clean = f"_{clean}"
    return clean


# -----------------------------------------------------------------------------
# Indicator scaffold (internal)
# -----------------------------------------------------------------------------


async def _scaffold_indicator(
    name: str,
    description: str,
    inputs: str | None,
    overlay: bool,
) -> str:
    safe_name = sanitize_pine_string(name)

    cache_key = codegen_cache_key(safe_name, description or "", inputs or "", overlay)
    cached_result = get_codegen_cache(cache_key)
    if cached_result:
        return cached_result

    template_source = "none"
    matched_keywords = extract_indicator_keywords(description or name)
    calc_stub = 'plot(close, "Price", color.blue)'
    if matched_keywords:
        family = matched_keywords[0]
        if family in _INDICATOR_TEMPLATES:
            calc_stub, overlay_default = _INDICATOR_TEMPLATES[family]
            if not overlay:
                overlay = overlay_default
            template_source = f"template:{family}"

    input_lines: list[str] = []
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

                if val.startswith('"') or val.startswith("'"):
                    pine_type = "string"
                    default_val = val
                elif val in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
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
                input_lines.append(f'{var_name} = input.source({default_val}, "{display_name}")')
            elif pine_type == "int":
                input_lines.append(f'int {var_name} = input.int({default_val}, "{display_name}")')
            elif pine_type == "float":
                input_lines.append(f'float {var_name} = input.float({default_val}, "{display_name}")')
            elif pine_type == "bool":
                input_lines.append(f'bool {var_name} = input.bool({default_val}, "{display_name}")')
            elif pine_type == "string":
                input_lines.append(f'string {var_name} = input.string({default_val}, "{display_name}")')
            elif pine_type == "color":
                input_lines.append(f'{var_name} = input.color({default_val}, "{display_name}")')

    relevant_funcs: list[tuple[str, str]] = []
    calc_stub_phase2 = 'plot(close, "Price", color.blue)'

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
            for meta, dist in zip(
                combined_results["metadatas"][0],
                combined_results["distances"][0],
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

        if best_meta is not None:
            top_name = best_meta.get("name", "")
            strong_name_match = any(
                kw_str in top_name.lower()
                for kw_str in extract_indicator_keywords(description or name)
            )
            is_ta_ns = top_name.startswith("ta.")
            accept = (
                best_dist < 0.6
                or (is_ta_ns and best_dist < 0.75)
                or strong_name_match
            )

            if accept:
                input_vars: list[str] = []
                for il in input_lines:
                    parts = il.split("=")
                    if len(parts) >= 1:
                        var_part = parts[0].strip()
                        tokens = var_part.split()
                        input_vars.append(tokens[-1] if tokens else var_part)

                raw_params = best_meta.get("raw_parameters", "")
                param_names: list[str] = []
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

                args_list: list[str] = []
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

    if not template_source.startswith("template:"):
        calc_stub = calc_stub_phase2

    relevant_func_strings = [rf[1] for rf in relevant_funcs]

    code = f"""//@version=6
indicator("{safe_name}", overlay={str(overlay).lower()}, shorttitle="{safe_name[:16]}")

// -- Inputs --"""
    for il in input_lines:
        code += f"\n{il}"
    if not input_lines:
        code += "\n// (Add your inputs here with input.int, input.float, input.source, etc.)"

    code += f"""

// -- Calculations --
// {description}
// Available functions from docs:"""
    for rf in relevant_func_strings:
        code += f"\n{rf}"
    if not relevant_func_strings:
        code += (
            "\n// (Use pine_search to find relevant functions)"
        )

    code += f"""
{calc_stub}
"""

    validation = await call_pine_facade(code)
    errors = validation.get("errors", [])

    output_code = code

    lines = [
        "GENERATED INDICATOR TEMPLATE:",
        "=" * 50,
        "```pine",
        output_code,
        "```",
        "",
    ]

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


# -----------------------------------------------------------------------------
# Strategy scaffold (internal)
# -----------------------------------------------------------------------------


async def _scaffold_strategy(
    name: str,
    description: str,
    initial_capital: int,
    commission_pct: float,
    pyramiding: int,
) -> str:
    safe_name = sanitize_pine_string(name)

    cache_key = codegen_cache_key(
        safe_name,
        description or "",
        f"{initial_capital}|{commission_pct}|{pyramiding}",
        True,
    )
    cached_result = get_codegen_cache(cache_key)
    if cached_result:
        return cached_result

    search_desc = (description or "").strip() or name
    relevant = await query_async(search_desc, 5, where={"namespace": "strategy"})
    db_err = check_query_error(relevant)
    if db_err:
        return db_err
    relevant_funcs: list[str] = []
    if relevant.get("ids") and relevant["ids"][0]:
        for meta in relevant["metadatas"][0][:5]:
            fname = meta.get("name", "?")
            fsyntax = meta.get("syntax", "")
            relevant_funcs.append(f"//   {fname}: {fsyntax[:80]}")

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
// -- Inputs --------------------------------------------------
enableLong  = input.bool(true,  "Enable Long",  group="Filters")
enableShort = input.bool(false, "Enable Short", group="Filters")
src         = input.source(close, "Source",     group="Settings")
fastLen     = input.int(12, "Fast Length", minval=1, group="Settings")
slowLen     = input.int(26, "Slow Length", minval=2, group="Settings")

// -- Calculations ---------------------------------------------
fastMA = ta.ema(src, fastLen)
slowMA = ta.ema(src, slowLen)

// -- Conditions -----------------------------------------------
longCondition  = ta.crossover(fastMA, slowMA)
shortCondition = ta.crossunder(fastMA, slowMA)

// -- Entries --------------------------------------------------
if enableLong and longCondition and barstate.isconfirmed
    strategy.entry("Long", strategy.long)

if enableShort and shortCondition and barstate.isconfirmed
    strategy.entry("Short", strategy.short)

// -- Exits ----------------------------------------------------
strategy.exit("Long Exit",  from_entry="Long",
                profit=na, loss=na)
strategy.exit("Short Exit", from_entry="Short",
                profit=na, loss=na)

// -- Cleanup --------------------------------------------------
if barstate.islast
    strategy.close_all()
"""

    validation = await call_pine_facade(template)
    if not validation["success"]:
        errors_str = "\n".join(
            f"  Line {e.get('line', '?')}: {e.get('text', '?')}" for e in validation.get("errors", [])[:5]
        )
        output_code = template.strip()
        return cap_response(
            f"WARNING: Template generation failed validation:\n{errors_str}\n\n"
            f"Raw template for manual fix:\n```pine\n{output_code}\n```"
        )

    lines = [
        "GENERATED STRATEGY TEMPLATE",
        "=" * 50,
        "Validated: 0 compilation errors (OK)",
        "",
        "```pine",
        template.strip(),
        "```",
    ]

    if relevant_funcs:
        lines.append("")
        lines.append("RELEVANT STRATEGY FUNCTIONS from docs:")
        for rf in relevant_funcs:
            lines.append(f"  {rf}")

    result = cap_response("\n".join(lines))
    set_codegen_cache(cache_key, result)
    return result


# -----------------------------------------------------------------------------
# TOOL: pine_scaffold - indicator or strategy template
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Scaffold PineScript Script",
        readOnlyHint=False,
        openWorldHint=True,
        destructiveHint=False,
        idempotentHint=False,
    )
)
async def pine_scaffold(
    kind: Annotated[
        _Kind,
        Field(
            description=(
                "Which kind of script to scaffold. 'indicator' builds an "
                "indicator() script with inputs and plot(); 'strategy' builds "
                "a full strategy() script with entries, exits, and standard "
                "risk parameters."
            ),
        ),
    ],
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="Display name for the generated script.",
        ),
    ],
    description: Annotated[
        str,
        Field(
            default="",
            max_length=500,
            description=(
                "What the script should calculate or how it should trade. "
                "Used to search the docs for relevant functions to wire in."
            ),
        ),
    ] = "",
    inputs: Annotated[
        str | None,
        Field(
            default=None,
            max_length=2000,
            description=(
                "[indicator only] Comma-separated input declarations, e.g. "
                "'length=14,src=close,mult=2.0'. Types are inferred."
            ),
        ),
    ] = None,
    overlay: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "[indicator only] Whether the indicator overlays the price chart."
            ),
        ),
    ] = False,
    initial_capital: Annotated[
        int,
        Field(
            default=10000,
            ge=1,
            le=1000000,
            description="[strategy only] Starting capital.",
        ),
    ] = 10000,
    commission_pct: Annotated[
        float,
        Field(
            default=0.1,
            ge=0.0,
            le=1.0,
            description="[strategy only] Commission percentage (0.1 = 0.1%).",
        ),
    ] = 0.1,
    pyramiding: Annotated[
        int,
        Field(
            default=1,
            ge=1,
            le=10,
            description="[strategy only] Max concurrent positions.",
        ),
    ] = 1,
) -> str:
    """
    Generate a validated PineScript v6 template (indicator or strategy).

    The result is compiled before being returned; the final script is
    production-ready and easy to paste into the editor. Relevant doc snippets
    are included as comments to help the user wire in real logic.

    WHEN TO USE:
      - You need a clean starting point for a new indicator or strategy.
      - You want the skeleton pre-wired with standard inputs + risk params.

    WHEN NOT TO USE:
      - You already have code and just want to fix it -> pine_repair.
      - You want to browse what functions exist first -> pine_search / pine_browse.
    """
    try:
        name = name.strip()
        if not name:
            raise ToolError("No name provided. Pass a display name for the script.")

        if kind == "indicator":
            return await _scaffold_indicator(name, description, inputs, overlay)
        if kind == "strategy":
            return await _scaffold_strategy(
                name, description, initial_capital, commission_pct, pyramiding
            )
        raise ToolError(f"Unknown kind '{kind}'. Use 'indicator' or 'strategy'.")

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_scaffold] {e}")
        if _db._chroma_breaker.is_open():
            return circuit_breaker_msg()
        raise ToolError(safe_error(e, "pine_scaffold"))
