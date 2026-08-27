#!/usr/bin/env python3
"""
Export pinescript_mcp ChromaDB → pine-reference.json for the Monaco editor.

Produces a compact JSON with all entries needed for:
  - signature help (params, returns, syntax)
  - hover tooltips (description, remarks, examples, see-also)
  - completion items (name, insert snippet, documentation, return type)
  - context-aware filtering (namespace + category)

Output: dashboard/public/pine-reference.json
"""

import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.db import get_collection


def parse_params(raw):
    if not raw:
        return []
    try:
        p = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(p, list):
            return []
        out = []
        for item in p:
            if not isinstance(item, dict):
                continue
            out.append({
                "name": item.get("name", ""),
                "type": item.get("type", ""),
                "desc": item.get("description", ""),
                "opt": bool(item.get("optional", False)),
                "default": item.get("default"),
            })
        return out
    except (json.JSONDecodeError, TypeError):
        return []


def parse_examples(raw):
    return []  # stripped — hover fetches full docs from API


def main():
    col = get_collection()
    total = col.count()
    result = col.get(include=["metadatas", "documents"], limit=total)

    entries = {}
    name_index = {}

    for meta, doc in zip(result["metadatas"], result["documents"]):
        name = meta.get("name", "")
        if not name:
            continue

        cat = meta.get("category", "function")
        ns = meta.get("namespace", "")

        params = parse_params(meta.get("raw_parameters", ""))
        examples = parse_examples(meta.get("raw_examples", ""))

        # Build insert snippet: name(param1, param2, ...)
        # For constants/variables, just the name
        # For functions, name(${1:param1}, ${2:param2}, ...)
        if cat in ("function",) and params:
            snippet_parts = []
            for i, p in enumerate(params, 1):
                pdef = p["name"]
                if p["default"] is not None:
                    pdef = f'{p["name"]}=${{{i}:{json.dumps(p["default"])}}}'
                elif p["type"]:
                    pdef = f'${{{i}:{p["name"]}}}'
                else:
                    pdef = f'${{{i}:{p["name"]}}}'
                snippet_parts.append(pdef)
            insert = f'{name}({", ".join(snippet_parts)})'
        elif cat in ("function",) and meta.get("syntax", "") and "(" in meta.get("syntax", ""):
            insert = f'{name}(${{1}})'
        elif cat == "type" and "." not in name:
            insert = f'{name}(${{1:...}})'
        else:
            insert = name

        entry = {
            "name": name,
            "cat": cat,
            "ns": ns,
            "syn": meta.get("syntax", ""),
            "ret": meta.get("returns", ""),
            "desc": (meta.get("raw_description", "") or "")[:200],
            "params": params,
            "rm": (meta.get("remarks", "") or "")[:150],
            "insert": insert,
        }

        key = name.lower()
        if key not in entries:
            entries[key] = entry
            name_index[key] = name
        else:
            existing = entries[key]
            is_func = bool(params) or ("(" in meta.get("syntax", ""))
            existing_is_func = bool(existing.get("params")) or ("(" in existing.get("syntax", ""))
            if is_func and not existing_is_func:
                entries[key] = entry

    # Build namespace index for fast dot-completion
    ns_index = {}
    for key, entry in entries.items():
        ns = entry.get("ns", "")
        if not ns:
            continue
        if ns not in ns_index:
            ns_index[ns] = []
        ns_index[ns].append(key)

    output = {
        "v": 1,
        "count": len(entries),
        "entries": entries,
        "ns": ns_index,
    }

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "Documents", "PineRustEngine", "dashboard", "public", "pine-reference.json"
    )
    # Fallback: direct path
    alt_path = "/Users/fractalyst/Documents/PineRustEngine/dashboard/public/pine-reference.json"
    out_path = alt_path if os.path.isdir(os.path.dirname(alt_path)) else out_path

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    size = os.path.getsize(out_path)
    print(f"Exported {len(entries)} entries to {out_path}")
    print(f"Size: {size:,} bytes ({size/1024:.1f} KB)")
    print(f"Namespaces: {len(ns_index)}")

    # Sample
    sample = entries.get("ta.sma", {})
    if sample:
        print(f"\nSample (ta.sma):")
        print(json.dumps(sample, indent=2)[:500])


if __name__ == "__main__":
    main()