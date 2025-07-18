"""
formatters/entry.py
Entry formatting helpers - plain ASCII for token efficiency.
Pure functions, no shared state.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from formatters.errors import cap_response


def relevance_pct(distance: float) -> str:
    relevance = max(0.0, 1.0 - distance) * 100
    return f"{relevance:.0f}%"


def is_function_like(meta: dict) -> bool:
    syntax = meta.get("syntax") or ""
    has_parens = "(" in syntax and ")" in syntax
    has_params = bool(meta.get("raw_parameters"))
    return has_parens or has_params


def format_params_text(meta: dict) -> str:
    raw_params = meta.get("raw_parameters", "")
    if not raw_params:
        param_count = meta.get("param_count", 0)
        if param_count:
            return f"PARAMETERS: {param_count} parameters"
        return ""

    try:
        params = json.loads(raw_params) if isinstance(raw_params, str) else raw_params
    except (json.JSONDecodeError, TypeError):
        return ""

    if not params or not isinstance(params, list):
        return ""

    lines = [f"PARAMETERS ({len(params)}):"]
    for p in params:
        if not isinstance(p, dict):
            continue
        pname = p.get("name", "?")
        ptype = p.get("type", "")
        pdesc = p.get("description", "")
        opt = " [optional]" if p.get("optional") else ""
        default = f" = {p['default']}" if p.get("default") is not None else ""
        ptype_str = f" ({ptype})" if ptype else ""
        lines.append(f"  {pname}{ptype_str}{opt}{default}")
        if pdesc:
            lines.append(f"    {pdesc}")
    return "\n".join(lines)


def dedup_examples(examples: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for ex in examples:
        key = re.sub(r'\s+', '', ex).lower()[:120]
        existing = seen.get(key)
        if existing is None:
            seen[key] = ex
        else:
            if ex.count("\n") > existing.count("\n"):
                seen[key] = ex
    return list(seen.values())


def format_examples_text(meta: dict) -> str:
    raw_ex = meta.get("raw_examples", "")
    if not raw_ex or not isinstance(raw_ex, str):
        ex_count = meta.get("example_count", 0)
        if ex_count:
            return f"EXAMPLES: {ex_count} examples available"
        return ""

    blocks = [b.strip() for b in raw_ex.split(" ||| ") if b.strip()]
    if not blocks:
        return ""

    blocks = dedup_examples(blocks)
    if len(blocks) > 3:
        blocks = blocks[:3]

    lines = [f"EXAMPLES ({len(blocks)}):"]
    for i, ex in enumerate(blocks, 1):
        lines.append(f"--- Example {i} ---")
        for code_line in ex.splitlines():
            lines.append(code_line)
    return "\n".join(lines)


def format_type_info(meta: dict) -> str:
    raw_fields = meta.get("raw_type_fields", "")
    if not raw_fields:
        return ""

    try:
        fields = json.loads(raw_fields) if isinstance(raw_fields, str) else raw_fields
    except (json.JSONDecodeError, TypeError):
        return ""

    if isinstance(fields, list) and fields:
        lines = ["FIELDS:"]
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
    return ""


def format_entry_detail(
    name: str, meta: dict, doc: str, distance: Optional[float] = None
) -> str:
    if not meta:
        meta = {}

    if not doc or not doc.strip() or len(doc.strip()) < 10:
        return f"'{name}' was found but has no local documentation."

    lines: list[str] = []

    category = meta.get("category", "?").upper()
    namespace = meta.get("namespace") or ""
    syntax = meta.get("syntax") or ""
    description = meta.get("raw_description", "") or doc.strip()
    returns = meta.get("returns") or ""
    remarks = meta.get("remarks") or ""
    see_also_raw = meta.get("raw_see_also", "")
    rel = f" (Relevance: {relevance_pct(distance)})" if distance is not None else ""

    # Header line - compact
    ns_prefix = f"{namespace}." if namespace and not name.startswith(f"{namespace}.") else ""
    lines.append(f"{category}: {ns_prefix}{name}{rel}")

    if syntax:
        lines.append(f"SYNTAX: {syntax}")

    if description:
        lines.append("DESCRIPTION:")
        for dline in description.splitlines():
            if dline.strip():
                lines.append(dline.strip())

    param_text = format_params_text(meta)
    if param_text:
        lines.append(param_text)

    if returns:
        lines.append(f"RETURNS: {returns}")

    if remarks:
        lines.append("REMARKS:")
        for rline in remarks.splitlines():
            if rline.strip():
                lines.append(rline.strip())

    type_text = format_type_info(meta)
    if type_text:
        lines.append(type_text)

    ex_text = format_examples_text(meta)
    if ex_text:
        lines.append(ex_text)

    if see_also_raw:
        lines.append(f"SEE ALSO: {see_also_raw}")

    return cap_response("\n".join(lines))
