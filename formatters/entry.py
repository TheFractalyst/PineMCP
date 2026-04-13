# PineScript-v6 MCP | © 2025-2026 @Fractalyst
"""
formatters/entry.py
──────────────────────────────────────────────────────────────────────────────
Entry formatting helpers — box drawing, relevance display, params/examples.
Pure functions — no shared state.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from formatters.errors import cap_response

# ─────────────────────────────────────────────────────────────────────────────
# Box-drawing characters
# ─────────────────────────────────────────────────────────────────────────────

_BOX_TL = "\u2554"
_BOX_TR = "\u2557"
_BOX_BL = "\u255a"
_BOX_BR = "\u255d"
_BOX_H = "\u2550"
_BOX_V = "\u2562"
_BOX_MID = "\u2564"
_DIVIDER = "\u2500" * 70


# ─────────────────────────────────────────────────────────────────────────────
# Utility formatters
# ─────────────────────────────────────────────────────────────────────────────

def relevance_pct(distance: float) -> str:
    """Convert cosine distance to human-readable relevance %."""
    relevance = max(0.0, 1.0 - distance) * 100
    return f"{relevance:.0f}%"


def section_line(text: str = "") -> str:
    return f"{_BOX_V} {text}"


def source_tag(meta: dict) -> str:
    return "[Local]"


def is_function_like(meta: dict) -> bool:
    """Check if an entry has function characteristics regardless of stored category.

    Many TradingView entries are scraped as 'variable' but take parameters
    (e.g. strategy.closedtrades.profit(trade_num), request.security(...)).
    """
    syntax = meta.get("syntax") or ""
    has_parens = "(" in syntax and ")" in syntax
    has_params = bool(meta.get("raw_parameters"))
    return has_parens or has_params


def source_line(meta: dict) -> str:
    return section_line("SOURCE: [Local]")


# ─────────────────────────────────────────────────────────────────────────────
# Parameter / type / example formatters
# ─────────────────────────────────────────────────────────────────────────────

def format_params_text(meta: dict) -> str:
    """Format parameters from raw_parameters metadata."""
    raw_params = meta.get("raw_parameters", "")
    if not raw_params:
        param_count = meta.get("param_count", 0)
        if param_count:
            return section_line(f"({param_count} parameters — see raw_parameters)")
        return ""

    try:
        params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
    except (json.JSONDecodeError, TypeError):
        return ""

    if not params or not isinstance(params, list):
        return ""

    lines = [section_line(f"PARAMETERS ({len(params)})")]
    for p in params:
        if not isinstance(p, dict):
            continue
        pname = p.get("name", "?")
        ptype = p.get("type", "")
        pdesc = p.get("description", "")
        opt = " [optional]" if p.get("optional") else ""
        default = f" = {p['default']}" if p.get("default") else ""
        ptype_str = f" ({ptype})" if ptype else ""
        lines.append(f"  {pname}{ptype_str}{opt}{default}")
        if pdesc:
            lines.append(f"    {pdesc}")
    return "\n".join(lines)


def dedup_examples(examples: list[str]) -> list[str]:
    """Remove duplicate examples by comparing whitespace-normalized content.
    Prefers formatted versions (more newlines) over collapsed ones."""
    seen: dict[str, str] = {}  # normalized_key -> best example
    for ex in examples:
        key = re.sub(r'\s+', '', ex).lower()[:120]
        existing = seen.get(key)
        if existing is None:
            seen[key] = ex
        else:
            # Prefer the version with more newlines (formatted over collapsed)
            if ex.count("\n") > existing.count("\n"):
                seen[key] = ex
    return list(seen.values())


def format_examples_text(meta: dict) -> str:
    """Format examples from raw_examples metadata."""
    raw_ex = meta.get("raw_examples", "")
    if not raw_ex:
        ex_count = meta.get("example_count", 0)
        if ex_count:
            return section_line(f"({ex_count} examples — see raw_examples)")
        return ""

    blocks = [b.strip() for b in raw_ex.split(" ||| ") if b.strip()]
    if not blocks:
        return ""

    blocks = dedup_examples(blocks)

    lines = [section_line(f"EXAMPLES ({len(blocks)})")]
    for i, ex in enumerate(blocks, 1):
        lines.append(f"  {'─' * 50}")
        lines.append(f"  Example {i}")
        for code_line in ex.splitlines():
            lines.append(f"  {code_line}")
        lines.append("")
    return "\n".join(lines)


def format_type_info(meta: dict) -> str:
    """Format type fields and methods."""
    raw_fields = meta.get("raw_type_fields", "")
    if not raw_fields:
        return ""

    lines = []
    try:
        fields = json.loads(raw_fields) if isinstance(raw_fields, str) else raw_fields
    except (json.JSONDecodeError, TypeError):
        return ""

    if fields:
        lines.append(section_line("FIELDS"))
        for f in fields:
            if not isinstance(f, dict):
                continue
            fname = f.get("name", "?")
            ftype = f.get("type", "")
            fdesc = f.get("description", "")
            ftype_str = f" ({ftype})" if ftype else ""
            lines.append(f"  {fname}{ftype_str}")
            if fdesc:
                lines.append(f"    {fdesc}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Full entry detail formatter
# ─────────────────────────────────────────────────────────────────────────────

def format_entry_detail(
    name: str, meta: dict, doc: str, distance: Optional[float] = None
) -> str:
    """Format a complete detailed entry for get_* tools."""

    # Check for hollow results
    if not doc or len(doc) < 10:
        return (
            f"'{name}' was found but has no local documentation.\n"
            f"This is likely a newer v6 feature not yet indexed."
        )

    lines: list[str] = []

    category = meta.get("category", "?").upper()
    namespace = meta.get("namespace") or ""
    syntax = meta.get("syntax") or ""
    description = meta.get("raw_description", "")
    returns = meta.get("returns") or ""
    remarks = meta.get("remarks") or ""
    see_also_raw = meta.get("raw_see_also", "")
    rel = f"  (Relevance: {relevance_pct(distance)})" if distance is not None else ""
    ns = f"{namespace}." if namespace and not name.startswith(f"{namespace}.") else ""

    lines.append(f"{_BOX_TL}{_BOX_H * 60}{_BOX_TR}")
    lines.append(f"{_BOX_V} {category}: {ns}{name}{rel}")
    lines.append(source_line(meta))
    lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")

    if syntax:
        lines.append(f"{_BOX_V} SYNTAX")
        lines.append(f"{_BOX_V} {syntax}")

    if description:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(f"{_BOX_V} DESCRIPTION")
        for dline in description.splitlines():
            lines.append(f"{_BOX_V} {dline}" if dline.strip() else _BOX_V)

    param_text = format_params_text(meta)
    if param_text:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(param_text)

    if returns:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(section_line(f"RETURNS: {returns}"))

    if remarks:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(section_line("REMARKS"))
        for rline in remarks.splitlines():
            lines.append(f"{_BOX_V} {rline}" if rline.strip() else _BOX_V)

    # Type fields
    type_text = format_type_info(meta)
    if type_text:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(type_text)

    # Examples
    ex_text = format_examples_text(meta)
    if ex_text:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(ex_text)

    if see_also_raw:
        lines.append(f"{_BOX_MID}{_BOX_H * 60}{_BOX_MID}")
        lines.append(section_line(f"SEE ALSO: {see_also_raw}"))

    lines.append(f"{_BOX_BL}{_BOX_H * 60}{_BOX_BR}")
    return cap_response("\n".join(lines))
