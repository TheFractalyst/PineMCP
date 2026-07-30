# ruff: noqa: E501
"""
tools/facade.py
------------------------------------------------------------------------------
Pine-facade REST endpoints (6 tools):
  - pine_get_script      - Fetch full Pine source by script ID (anonymous)
  - pine_search_community- Search community scripts by keyword (with source)
  - pine_list_builtins   - List TradingView built-in indicators/studies
  - pine_get_metadata    - Get full study metadata (inputs, plots, styles)
  - pine_eval            - Evaluate a script with inputs (rootValues)
  - pine_list_libraries  - List published Pine libraries

All endpoints are anonymous (no auth required), verified 2026-07-28.
See docs/tradingview-endpoints.md sections 30.3, 31.1-31.8 for details.
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

from core.pine_facade import get_facade_client

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

_FACADE_BASE = "https://pine-facade.tradingview.com/pine-facade"
_PUBSCRIPTS_URL = "https://www.tradingview.com/pubscripts-suggest-json"
_MAX_COMMUNITY_RESULTS = 20
_MAX_BUILTIN_RESULTS = 50
_MAX_SOURCE_CHARS = 30000  # truncate long community script sources


# -----------------------------------------------------------------------------
# Helper: encode script ID for URL path
# -----------------------------------------------------------------------------

def _encode_script_id(script_id: str) -> str:
    """Encode a script ID for use in a URL path.
    Per docs section 31.1: spaces and literal % become %25, ; stays literal.
    e.g. 'STD;RSI%1Strategy' -> 'STD;RSI%251Strategy'
    """
    return script_id.replace("%", "%25").replace(" ", "%25")


# -----------------------------------------------------------------------------
# Helper: parse JSON from text/plain response (pine-facade quirk)
# -----------------------------------------------------------------------------

async def _facade_get(url: str, *, timeout: float = 15.0) -> dict | list:
    """GET a pine-facade endpoint and parse JSON (handles text/plain quirk)."""
    client = get_facade_client()
    resp = await client.get(url, timeout=timeout)

    if resp.status_code == 404:
        raise ToolError(f"Script not found (404). Check the script ID and try again.")
    if resp.status_code != 200:
        raise ToolError(f"pine-facade returned HTTP {resp.status_code}: {resp.text[:200]}")

    # pine-facade returns content-type: text/plain even for JSON
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError as e:
            raise ToolError(f"Failed to parse pine-facade response as JSON: {e}")


async def _facade_post(url: str, *, data: dict, timeout: float = 15.0) -> dict:
    """POST form data to a pine-facade endpoint."""
    client = get_facade_client()
    resp = await client.post(url, data=data, timeout=timeout)

    if resp.status_code != 200:
        raise ToolError(f"pine-facade returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError as e:
            raise ToolError(f"Failed to parse pine-facade response as JSON: {e}")


# -----------------------------------------------------------------------------
# TOOL 1: pine_get_script — fetch full Pine source by script ID
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Fetch Community Script Source",
        readOnlyHint=True,
        openWorldHint=True,
        idempotentHint=True,
    )
)
async def pine_get_script(
    script_id: Annotated[
        str,
        Field(
            description=(
                "TradingView script ID (scriptIdPart). Examples: 'PUB;175' "
                "(community), 'STD;RSI' (built-in). Use pine_search_community "
                "to find IDs by keyword. The ID format uses ';' as separator "
                "and '%' for sub-categories (e.g. 'STD;RSI%1Strategy')."
            ),
            min_length=1,
            max_length=200,
        ),
    ],
    version: Annotated[
        str,
        Field(
            default="-1",
            description=(
                "Script version. Use '-1' (default) for the latest version, "
                "or a specific version like '45.0'. For built-in (STD;) "
                "scripts, the version is ignored — /last is always returned."
            ),
        ),
    ] = "-1",
) -> str:
    """
    Fetch the full Pine Script source code of a TradingView community or
    built-in script by its scriptIdPart. Anonymous, no auth required.

    WHEN TO USE:
      - After pine_search_community finds a script ID and you want its source.
      - To fetch a known community script by ID (e.g. from a TV URL).
      - To study how a popular indicator/strategy is implemented.

    WHEN NOT TO USE:
      - To search by keyword -> use pine_search_community instead.
      - To get built-in metadata (inputs, plots) -> use pine_get_metadata.
      - To compile your own code -> use pine_compile.

    RETURNS:
      - Script name, access level, version, and full source code.
      - For closed-source/invite-only scripts, source may be empty.
    """
    encoded_id = _encode_script_id(script_id)
    url = f"{_FACADE_BASE}/get/{encoded_id}/{version}?no_4xx=true"

    try:
        data = await _facade_get(url)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_get_script] {e}")
        raise ToolError(f"Failed to fetch script: {e}")

    if not isinstance(data, dict):
        raise ToolError(f"Unexpected response type: {type(data).__name__}")

    source = data.get("source", "") or data.get("scriptSource", "")
    name = data.get("scriptName", "Unknown")
    access = data.get("scriptAccess", "unknown")
    ver = data.get("version", version)
    extra = data.get("extra", {})
    kind = extra.get("kind", "unknown")
    is_price_study = extra.get("is_price_study")
    stats = extra.get("stats", {})

    if not source:
        return (
            f"Script: {name}\n"
            f"ID: {script_id}\n"
            f"Version: {ver}\n"
            f"Access: {access}\n"
            f"Kind: {kind}\n\n"
            f"Source: NOT AVAILABLE (closed-source or invite-only script).\n"
            f"Only open-access scripts include source code."
        )

    # Truncate very long sources
    truncated = False
    if len(source) > _MAX_SOURCE_CHARS:
        source = source[:_MAX_SOURCE_CHARS] + "\n\n... [truncated, full source was {} chars]".format(len(source))
        truncated = True

    lines = [
        f"Script: {name}",
        f"ID: {script_id}",
        f"Version: {ver}",
        f"Access: {access}",
        f"Kind: {kind}",
    ]
    if is_price_study is not None:
        lines.append(f"Overlay: {is_price_study}")
    if stats:
        stat_parts = [f"{k}: {v}" for k, v in stats.items()]
        lines.append(f"Stats: {', '.join(stat_parts)}")
    if truncated:
        lines.append("(source truncated)")
    lines.append("")
    lines.append(source)

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# TOOL 2: pine_search_community — search community scripts by keyword
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Search Community Scripts",
        readOnlyHint=True,
        openWorldHint=True,
        idempotentHint=True,
    )
)
async def pine_search_community(
    query: Annotated[
        str,
        Field(
            description=(
                "Search query (keyword or phrase). Examples: 'RSI divergence', "
                "'EMA crossover strategy', 'volume profile', 'supertrend'."
            ),
            min_length=1,
            max_length=200,
        ),
    ],
    limit: Annotated[
        int,
        Field(
            default=10,
            description="Max results to return (1-20). Default 10.",
            ge=1,
            le=20,
        ),
    ] = 10,
    include_source: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "If true, include the full scriptSource for open-access scripts. "
                "Useful for studying implementations. Set false for a compact "
                "result list (IDs + names only)."
            ),
        ),
    ] = False,
) -> str:
    """
    Search TradingView community scripts by keyword. Returns script names,
    IDs, authors, and optionally full source code. Anonymous, no auth.

    WHEN TO USE:
      - To find real-world Pine Script examples by topic.
      - To discover popular community indicators/strategies.
      - To fetch source code of open-access scripts for study or adaptation.

    WHEN NOT TO USE:
      - To search the Pine v6 reference docs -> use pine_search.
      - To fetch a script you already have the ID for -> use pine_get_script.
      - To list built-in indicators -> use pine_list_builtins.

    RETURNS:
      - List of matching scripts with: name, shortTitle, scriptIdPart,
        author, agreeCount (likes), access level (1=open, 2=closed, 3=invite).
      - If include_source=true, full scriptSource for open-access scripts.
    """
    url = f"{_PUBSCRIPTS_URL}/?search={query}"
    limit = min(limit, _MAX_COMMUNITY_RESULTS)

    try:
        data = await _facade_get(url)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_search_community] {e}")
        raise ToolError(f"Failed to search community scripts: {e}")

    # Response shape: {"results": [...]} or {"suggestions": [...]}
    results = (
        data.get("results", [])
        if isinstance(data, dict)
        else data if isinstance(data, list)
        else []
    )

    if not results:
        return f"No community scripts found for '{query}'."

    # Access mapping: 1=open_source, 2=closed_source, 3=invite_only
    access_labels = {1: "open", 2: "closed", 3: "invite-only"}

    lines = [f"Community script search: '{query}' ({len(results[:limit])} results)", ""]

    for i, r in enumerate(results[:limit], 1):
        name = r.get("scriptName") or r.get("title", "Unknown")
        short = r.get("shortTitle", "")
        script_id = r.get("scriptIdPart") or r.get("pineId", "")
        # author can be a dict {id, username} or a string
        author_raw = r.get("author", "") or r.get("userName", "")
        if isinstance(author_raw, dict):
            author = author_raw.get("username", str(author_raw.get("id", "")))
        else:
            author = author_raw
        agree = r.get("agreeCount", 0)
        access = access_labels.get(r.get("access", 0), "unknown")
        source = r.get("scriptSource", "")

        lines.append(f"{i}. {name}")
        if short:
            lines.append(f"   Short: {short}")
        if script_id:
            lines.append(f"   ID: {script_id}")
        if author:
            lines.append(f"   Author: {author}")
        lines.append(f"   Likes: {agree} | Access: {access}")

        if include_source and source and access == "open":
            if len(source) > _MAX_SOURCE_CHARS:
                source = source[:_MAX_SOURCE_CHARS] + "\n   ... [truncated]"
            lines.append("   Source:")
            for src_line in source.splitlines():
                lines.append(f"   {src_line}")
        elif include_source and not source:
            lines.append("   Source: (not available — closed/invite-only)")

        lines.append("")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# TOOL 3: pine_list_builtins — list TradingView built-in indicators
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="List Built-in Indicators",
        readOnlyHint=True,
        openWorldHint=True,
        idempotentHint=True,
    )
)
async def pine_list_builtins(
    filter: Annotated[
        Literal["standard", "fundamental", "candlestick"],
        Field(
            default="standard",
            description=(
                "Filter category: 'standard' (145 built-in indicators like RSI, "
                "MACD, EMA), 'fundamental' (1442 fundamental data studies like "
                "P/E, EPS, Revenue), 'candlestick' (45 candlestick pattern "
                "detectors)."
            ),
        ),
    ] = "standard",
    limit: Annotated[
        int,
        Field(
            default=20,
            description="Max results to return (1-50). Default 20.",
            ge=1,
            le=50,
        ),
    ] = 20,
) -> str:
    """
    List TradingView built-in indicators/studies. Returns script names, IDs,
    and metadata (plot counts, kind, description). Anonymous, no auth.

    WHEN TO USE:
      - To discover what built-in indicators TradingView offers.
      - To find the scriptIdPart for a built-in (needed for pine_get_metadata).
      - To check available fundamental data studies or candlestick patterns.

    WHEN NOT TO USE:
      - To search community scripts -> use pine_search_community.
      - To get full metadata (inputs, plots, defaults) -> use pine_get_metadata.
      - To search Pine v6 reference docs -> use pine_search.

    RETURNS:
      - List of built-ins with: scriptName, scriptIdPart, version, kind,
        shortDescription, stats (plot/plotshape/alertcondition counts).
    """
    url = f"{_FACADE_BASE}/list?filter={filter}"
    limit = min(limit, _MAX_BUILTIN_RESULTS)

    try:
        data = await _facade_get(url)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_list_builtins] {e}")
        raise ToolError(f"Failed to list built-ins: {e}")

    if not isinstance(data, list):
        raise ToolError(f"Unexpected response type: {type(data).__name__}")

    if not data:
        return f"No built-ins found for filter='{filter}'."

    lines = [f"Built-in {filter} indicators ({len(data[:limit])} of {len(data)})", ""]

    for i, entry in enumerate(data[:limit], 1):
        name = entry.get("scriptName", "Unknown")
        script_id = entry.get("scriptIdPart", "")
        version = entry.get("version", "")
        extra = entry.get("extra", {})
        kind = extra.get("kind", "")
        short = extra.get("shortDescription", "")
        stats = extra.get("stats", {})

        lines.append(f"{i}. {name}")
        if short and short != name:
            lines.append(f"   Short: {short}")
        if script_id:
            lines.append(f"   ID: {script_id}")
        if version:
            lines.append(f"   Version: {version}")
        if kind:
            lines.append(f"   Kind: {kind}")
        if stats:
            stat_parts = [f"{k}: {v}" for k, v in stats.items()]
            lines.append(f"   Stats: {', '.join(stat_parts)}")
        lines.append("")

    if len(data) > limit:
        lines.append(f"(showing {limit} of {len(data)} — increase limit to see more)")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# TOOL 4: pine_get_metadata — full study metadata (inputs, plots, styles)
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Get Study Metadata",
        readOnlyHint=True,
        openWorldHint=True,
        idempotentHint=True,
    )
)
async def pine_get_metadata(
    script_id: Annotated[
        str,
        Field(
            description=(
                "TradingView script ID (scriptIdPart). Examples: 'STD;RSI', "
                "'STD;MACD', 'STD;Supertrend'. Use pine_list_builtins to find IDs."
            ),
            min_length=1,
            max_length=200,
        ),
    ],
    include_inputs: Annotated[
        bool,
        Field(
            default=True,
            description="Include the full inputs list (id, name, type, defval, min/max, options).",
        ),
    ] = True,
    include_plots: Annotated[
        bool,
        Field(
            default=True,
            description="Include the full plots list (id, type, target, palette).",
        ),
    ] = True,
    include_defaults: Annotated[
        bool,
        Field(
            default=False,
            description="Include default styles and colors for each plot.",
        ),
    ] = False,
) -> str:
    """
    Fetch full study metadata for a TradingView script: inputs, plots,
    styles, bands, defaults, and description. This is the canonical
    metaInfo payload TV uses when creating a study on a chart.

    WHEN TO USE:
      - To get exact input IDs (in_0, in_1) for TV-parity backtests.
      - To see default values, min/max bounds, and option lists for inputs.
      - To understand plot types (line, histogram, colorer) and their targets.
      - To get default colors and line widths for each plot.

    WHEN NOT TO USE:
      - To fetch the Pine source code -> use pine_get_script.
      - To list available built-ins -> use pine_list_builtins.
      - To compile code -> use pine_compile.

    RETURNS:
      - Description, shortDescription, pine version, is_price_study.
      - Inputs: id, name, type, defval, min, max, group, options.
      - Plots: id, type, target (for colorers), palette.
      - Bands: id, name, source.
      - Styles (if include_defaults): colors, linewidth, plottype per plot.
    """
    encoded_id = _encode_script_id(script_id)
    url = f"{_FACADE_BASE}/translate/{encoded_id}/last"

    try:
        data = await _facade_get(url)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_get_metadata] {e}")
        raise ToolError(f"Failed to fetch metadata: {e}")

    result = data.get("result", {}) if isinstance(data, dict) else {}
    mi = result.get("metaInfo", {})
    warnings = data.get("reason2", {}).get("warnings", []) if isinstance(data.get("reason2"), dict) else []

    if not mi:
        return f"No metadata found for script ID '{script_id}'. It may not exist."

    lines = [
        f"Study Metadata: {script_id}",
        f"Description: {mi.get('description', 'Unknown')}",
        f"Short: {mi.get('shortDescription', '')}",
        f"Pine version: {mi.get('pine', {}).get('version', 'unknown')}",
        f"Overlay: {mi.get('is_price_study', 'unknown')}",
    ]

    # Bands
    bands = mi.get("bands", [])
    if bands:
        lines.append(f"\nBands ({len(bands)}):")
        for b in bands:
            lines.append(f"  {b.get('id', '?')}: {b.get('name', '?')} (source: {b.get('source', '?')})")

    # Inputs
    if include_inputs:
        inputs = mi.get("inputs", [])
        if inputs:
            lines.append(f"\nInputs ({len(inputs)}):")
            for inp in inputs:
                inp_id = inp.get("id", "?")
                inp_name = inp.get("name", "?")
                inp_type = inp.get("type", "?")
                defval = inp.get("defval", "")
                group = inp.get("group", "")
                is_fake = inp.get("isFake", False)
                # Skip hidden meta inputs (text/pineId/pineVersion/pineFeatures
                # are internal TV metadata, not user-facing inputs)
                if inp_id in ("text", "pineId", "pineVersion", "pineFeatures"):
                    continue
                line = f"  {inp_id}: {inp_name} (type={inp_type}"
                if defval != "":
                    line += f", defval={defval!r}"
                if "min" in inp:
                    line += f", min={inp['min']}"
                if "max" in inp:
                    line += f", max={inp['max']}"
                if group:
                    line += f", group={group}"
                options = inp.get("options")
                if options:
                    line += f", options={options[:8]}"
                line += ")"
                lines.append(line)

    # Plots
    if include_plots:
        plots = mi.get("plots", [])
        if plots:
            lines.append(f"\nPlots ({len(plots)}):")
            for p in plots:
                p_id = p.get("id", "?")
                p_type = p.get("type", "?")
                target = p.get("target", "")
                palette = p.get("palette", "")
                line = f"  {p_id}: type={p_type}"
                if target:
                    line += f", target={target}"
                if palette:
                    line += f", palette={palette}"
                lines.append(line)

    # Styles (defaults)
    if include_defaults:
        styles = mi.get("styles", {})
        defaults = mi.get("defaults", {}).get("styles", {})
        if styles:
            lines.append(f"\nDefault Styles ({len(styles)}):")
            for sid, sdef in styles.items():
                title = sdef.get("title", "?")
                d = defaults.get(sid, {})
                color = d.get("color", "?")
                linewidth = d.get("linewidth", "?")
                plottype = d.get("plottype", "?")
                lines.append(f"  {sid}: {title} (color={color}, linewidth={linewidth}, plottype={plottype})")

    # Warnings
    if warnings:
        lines.append(f"\nWarnings ({len(warnings)}):")
        for w in warnings[:5]:
            lines.append(f"  {w.get('code', '?')}: {w.get('message', '?')}")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# TOOL 5: pine_eval — evaluate a script with inputs
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="Evaluate Pine Script",
        readOnlyHint=True,
        openWorldHint=True,
        idempotentHint=True,
    )
)
async def pine_eval(
    code: Annotated[
        str,
        Field(
            description=(
                "Pine Script v6 source code to evaluate. Must include //@version=6 "
                "and an indicator() or strategy() declaration."
            ),
            min_length=1,
            max_length=100_000,
        ),
    ],
    inputs: Annotated[
        str,
        Field(
            default="",
            description=(
                "Input overrides as JSON key-value pairs. Keys are input names "
                "(not IDs), values are the input values. Example: "
                "'{\"length\": 21, \"source\": \"close\"}'. Leave empty to use "
                "default input values."
            ),
        ),
    ] = "",
) -> str:
    """
    Evaluate a Pine Script with given inputs and return the rootValues
    (indicator output values). Uses TradingView's eval_pine_ex endpoint.

    WHEN TO USE:
      - To check what values an indicator produces without running a full backtest.
      - To verify input types and defaults by seeing the output.
      - To quickly test a script's behavior with different inputs.

    WHEN NOT TO USE:
      - To compile/check for errors -> use pine_compile.
      - To run a full backtest with P&L -> use run_backtest.
      - To get study metadata (inputs, plots) -> use pine_get_metadata.

    RETURNS:
      - Root values (indicator outputs) for the given inputs.
      - Any evaluation errors or warnings.
    """
    # Parse inputs JSON
    inputs_dict: dict = {}
    if inputs.strip():
        try:
            inputs_dict = json.loads(inputs)
            if not isinstance(inputs_dict, dict):
                raise ValueError("inputs must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            raise ToolError(f'Invalid inputs JSON: {e}. Example: \'{{"length": 21}}\'')

    url = f"{_FACADE_BASE}/eval_pine_ex/"
    form_data = {"source": code}
    if inputs_dict:
        form_data["inputs"] = json.dumps(inputs_dict)

    try:
        data = await _facade_post(url, data=form_data)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_eval] {e}")
        raise ToolError(f"Failed to evaluate script: {e}")

    result = data.get("result", {}) if isinstance(data, dict) else {}
    root_values = result.get("rootValues", {})
    errors = data.get("errors", []) or result.get("errors", [])

    lines = ["Pine Script Evaluation", ""]

    if errors:
        lines.append(f"Errors ({len(errors)}):")
        for err in errors[:10]:
            if isinstance(err, dict):
                code_val = err.get("code", "")
                msg = err.get("message", err.get("text", str(err)))
                line_num = err.get("start", {}).get("line", "?") if isinstance(err.get("start"), dict) else "?"
                lines.append(f"  Line {line_num} [{code_val}]: {msg}")
            else:
                lines.append(f"  {err}")
        lines.append("")

    if root_values:
        lines.append("Root Values:")
        if isinstance(root_values, dict):
            for k, v in root_values.items():
                val_str = str(v)
                if len(val_str) > 200:
                    val_str = val_str[:200] + "..."
                lines.append(f"  {k}: {val_str}")
        else:
            lines.append(f"  {root_values}")
    elif not errors:
        lines.append("No root values returned (script may not produce output values).")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# TOOL 6: pine_list_libraries — list published Pine libraries
# -----------------------------------------------------------------------------


@tool(
    annotations=ToolAnnotations(
        title="List Pine Libraries",
        readOnlyHint=True,
        openWorldHint=True,
        idempotentHint=True,
    )
)
async def pine_list_libraries(
    lib_id_prefix: Annotated[
        str,
        Field(
            default="PUB",
            description=(
                "Library ID prefix to filter by. 'PUB' (default) lists published "
                "community libraries. Other prefixes may exist but are unverified."
            ),
        ),
    ] = "PUB",
    limit: Annotated[
        int,
        Field(
            default=20,
            description="Max results to return (1-50). Default 20.",
            ge=1,
            le=50,
        ),
    ] = 20,
) -> str:
    """
    List published Pine Script libraries. Libraries are reusable Pine
    code packages that other scripts can import via `import` statements.

    WHEN TO USE:
      - To discover available Pine libraries for import.
      - To find library IDs for use in import statements.
      - To browse what reusable functionality the community has published.

    WHEN NOT TO USE:
      - To search for indicators/strategies -> use pine_search_community.
      - To list built-in indicators -> use pine_list_builtins.
      - To fetch a library's source -> use pine_get_script with its ID.

    RETURNS:
      - List of libraries with: scriptName, scriptIdPart, version, author.
    """
    url = f"{_FACADE_BASE}/lib_list?lib_id_prefix={lib_id_prefix}"
    limit = min(limit, _MAX_BUILTIN_RESULTS)

    try:
        data = await _facade_get(url)
    except ToolError:
        raise
    except Exception as e:
        logger.error(f"[pine_list_libraries] {e}")
        raise ToolError(f"Failed to list libraries: {e}")

    if not isinstance(data, list):
        raise ToolError(f"Unexpected response type: {type(data).__name__}")

    if not data:
        return f"No libraries found for prefix '{lib_id_prefix}'."

    lines = [f"Pine Libraries (prefix={lib_id_prefix}, {len(data[:limit])} of {len(data)})", ""]

    for i, entry in enumerate(data[:limit], 1):
        # lib_list uses different field names than /list
        name = entry.get("scriptName") or entry.get("lib", "Unknown")
        script_id = entry.get("scriptIdPart", "")
        version = entry.get("version", "")
        user = entry.get("user", "") or entry.get("userId", "")
        docs = entry.get("docs", "")
        lib_id = entry.get("libId", "")

        lines.append(f"{i}. {name}")
        if lib_id:
            lines.append(f"   Import ID: {lib_id}")
        if script_id:
            lines.append(f"   Script ID: {script_id}")
        if version:
            lines.append(f"   Version: {version}")
        if user:
            lines.append(f"   Author: {user}")
        if docs:
            lines.append(f"   Description: {docs}")
        lines.append("")

    if len(data) > limit:
        lines.append(f"(showing {limit} of {len(data)} — increase limit to see more)")

    return "\n".join(lines)
