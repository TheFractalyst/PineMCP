"""
parse_docs.py
─────────────────────────────────────────────────────────────────────────────
Reads  pinescript_v6_docs.txt  (or the full reference Markdown file) and
extracts every documented entry into a structured JSON file
pinescript_chunks.json.

The parser processes the canonical single-file reference:
    pinescriptv6_complete_reference.md

Sections detected via top-level `#` headings:
    Variables | Constants | Functions | Keywords | Types | Operators | Annotations

Every `##` heading inside a section becomes one entry.
Multiple overloads (detected by duplicate names) are suffixed _overload1, _overload2 …

Output schema per entry (all keys always present):
{
  "id":          str,
  "name":        str,
  "category":    "function" | "type" | "variable" | "constant"
                 | "keyword" | "operator" | "annotation" | "example",
  "namespace":   str | null,
  "syntax":      str | null,
  "description": str | null,
  "parameters":  [ {"name":str,"type":str,"description":str,"optional":bool} ],
  "returns":     str | null,
  "remarks":     str | null,
  "examples":    [ str, … ],
  "see_also":    [ str, … ],
  "raw_text":    str
}
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
SEARCH_PATHS = [
    ROOT / "data" / "pinescriptv6_complete_reference.md",
    ROOT / "data" / "pinescript_v6_docs.txt",
    Path("../pinescriptv6-main/pinescriptv6_complete_reference.md"),
    ROOT.parent / "pinescriptv6-main" / "pinescriptv6_complete_reference.md",
]

OUTPUT_FILE = ROOT / "data" / "pinescript_chunks.json"

# ─────────────────────────────────────────────────────────────────────────────
# Section → Category mapping
# ─────────────────────────────────────────────────────────────────────────────

SECTION_TO_CATEGORY: dict[str, str] = {
    "variables":   "variable",
    "constants":   "constant",
    "functions":   "function",
    "keywords":    "keyword",
    "types":       "type",
    "operators":   "operator",
    "annotations": "annotation",
}

# ─────────────────────────────────────────────────────────────────────────────
# Namespace detection
# ─────────────────────────────────────────────────────────────────────────────

KNOWN_NAMESPACES = [
    "ta", "strategy", "math", "array", "matrix", "map", "str",
    "color", "chart", "line", "label", "box", "table", "request",
    "ticker", "timeframe", "syminfo", "input", "polyline", "linefill",
    "barstate", "session", "earnings", "dividends", "splits", "alert",
    "log", "runtime", "indicator", "library", "xloc", "yloc",
    "location", "display", "size", "shape", "style", "format",
    "currency", "adjustment", "extend", "font", "hline", "plot",
    "order", "scale", "text", "dayofweek",
]


def detect_namespace(name: str) -> Optional[str]:
    """Return the namespace prefix if the name contains a known one."""
    if "." in name:
        prefix = name.split(".")[0].lstrip("`").rstrip("`()")
        if prefix in KNOWN_NAMESPACES:
            return prefix
        # Still return whatever prefix exists even if not in the list
        return prefix if prefix else None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Slug generation
# ─────────────────────────────────────────────────────────────────────────────

# Symbol → word map for operator/keyword slugs
_SYMBOL_SLUG_MAP = {
    "+":  "op_plus", "-": "op_minus", "*": "op_multiply", "/": "op_divide",
    "%":  "op_modulo", "+=": "op_plus_assign", "-=": "op_minus_assign",
    "*=": "op_multiply_assign", "/=": "op_divide_assign", "%=": "op_modulo_assign",
    "==": "op_equal", "!=": "op_not_equal", "<": "op_less", ">": "op_greater",
    "<=": "op_less_equal", ">=": "op_greater_equal", ":=": "op_reassign",
    "=>": "op_arrow", "?": "op_ternary_question", ":": "op_ternary_colon",
    "[]": "op_subscript", "//": "op_comment_line", "/*": "op_comment_block",
    "not": "kw_not", "and": "kw_and", "or": "kw_or",
}

def make_slug(name: str) -> str:
    """Convert a display name to a safe identifier slug."""
    # First check symbol map (handles operators like - + * / := etc.)
    clean_name = name.strip()
    if clean_name in _SYMBOL_SLUG_MAP:
        return _SYMBOL_SLUG_MAP[clean_name]
    slug = clean_name.lower()
    slug = slug.replace("()", "")
    slug = re.sub(r"[^a-z0-9_.]", "_", slug)
    slug = re.sub(r"_+", "_", slug).strip("_")
    # If slug is empty after cleaning (pure symbols not in map), use hex encoding
    if not slug:
        slug = "op_" + clean_name.encode("utf-8").hex()
    return slug


# ─────────────────────────────────────────────────────────────────────────────
# Block-level text extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

_CODE_FENCE_RE = re.compile(r"```(?:pine|python|bash|json)?(.*?)```", re.DOTALL)
_SEE_ALSO_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_TYPE_LINE_RE = re.compile(r"^\*\*Type:\*\*\s*(.+)$", re.MULTILINE)
_RETURNS_H_RE = re.compile(r"^#{2,4}\s*Returns?\s*$", re.MULTILINE | re.IGNORECASE)
_PARAMS_H_RE = re.compile(r"^#{2,4}\s*(?:Arguments?|Parameters?|Syntax)\s*$", re.MULTILINE | re.IGNORECASE)
_REMARKS_H_RE = re.compile(r"^#{2,4}\s*Remarks?\s*$", re.MULTILINE | re.IGNORECASE)
_SEEALSO_H_RE = re.compile(r"^#{2,4}\s*See\s+[Aa]lso\s*$", re.MULTILINE | re.IGNORECASE)
_EXAMPLE_H_RE = re.compile(r"^#{2,4}\s*(?:Code\s+)?Examples?\s*$", re.MULTILINE | re.IGNORECASE)


def extract_code_examples(text: str) -> list[str]:
    """Pull every fenced code block from the text."""
    return [block.strip() for block in _CODE_FENCE_RE.findall(text)]


def extract_see_also(text: str) -> list[str]:
    """Pull linked names from a See Also section."""
    seealso_match = _SEEALSO_H_RE.search(text)
    if not seealso_match:
        return []
    after = text[seealso_match.end():]
    # Grab up to next heading or end of entry
    next_h = re.search(r"^#{2,4}\s", after, re.MULTILINE)
    section = after[: next_h.start()] if next_h else after
    return [m.group(1).strip() for m in _SEE_ALSO_LINK_RE.finditer(section)]


def extract_returns(text: str) -> Optional[str]:
    """Extract the Returns section body."""
    m = _RETURNS_H_RE.search(text)
    if not m:
        # Also look for bold "Returns" on a standalone line
        simple = re.search(r"^Returns\s*\n+(.+?)(?=\n#{2,}|\Z)", text, re.MULTILINE | re.DOTALL)
        if simple:
            return simple.group(1).strip()
        return None
    after = text[m.end():]
    next_h = re.search(r"^#{2,4}\s", after, re.MULTILINE)
    section = after[: next_h.start()] if next_h else after
    cleaned = _CODE_FENCE_RE.sub("", section).strip()
    return cleaned if cleaned else None


def extract_remarks(text: str) -> Optional[str]:
    """Extract the Remarks section body."""
    m = _REMARKS_H_RE.search(text)
    if not m:
        return None
    after = text[m.end():]
    next_h = re.search(r"^#{2,4}\s", after, re.MULTILINE)
    section = after[: next_h.start()] if next_h else after
    cleaned = _CODE_FENCE_RE.sub("", section).strip()
    return cleaned if cleaned else None


def extract_description(text: str, name: str) -> Optional[str]:
    """
    The description is the prose text that immediately follows the entry
    heading before any sub-headings appear.
    """
    # Strip the heading line itself
    heading_re = re.compile(
        r"^##\s+" + re.escape(name.strip()) + r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = heading_re.search(text)
    if not m:
        return None
    after = text[m.end():].lstrip("\n")
    # Grab text until the first sub-heading (### …) or code fence or end
    first_sub = re.search(r"^#{3,}\s+", after, re.MULTILINE)
    if first_sub:
        prose = after[: first_sub.start()]
    else:
        prose = after
    # Also cut at the first code fence (examples section without explicit heading)
    fence_pos = prose.find("```")
    if fence_pos != -1:
        prose = prose[:fence_pos]
    # Remove Type: lines (they're metadata)
    prose = re.sub(r"\*\*Type:\*\*[^\n]*\n?", "", prose)
    prose = prose.strip()
    return prose if prose else None


def extract_type_annotation(text: str) -> Optional[str]:
    """Pull **Type:** value."""
    m = _TYPE_LINE_RE.search(text)
    return m.group(1).strip() if m else None


def extract_syntax(text: str, name: str, category: str) -> Optional[str]:
    """
    For functions: try to detect `name(arg1, arg2, ...)` pattern.
    For operators/keywords we return the name itself.
    """
    if category in ("operator", "keyword"):
        return name
    # Look for syntax line like:  name(param1, param2, ...)  or  name<type>(...)
    pattern = re.compile(
        r"(`?" + re.escape(name.rstrip("()")) + r"[`(][^`\n]*`?)",
        re.IGNORECASE,
    )
    m = pattern.search(text)
    if m:
        return m.group(1).strip("`")
    return None


def extract_parameters(text: str) -> list[dict]:
    """
    Parse parameter/argument tables or inline descriptions from a block.

    Handles patterns like:
      - `param_name` (type) - Description
      - **param_name** (type): Description
      - Inline lists
    """
    params: list[dict] = []

    # Find Parameters / Arguments / Syntax heading
    ph = _PARAMS_H_RE.search(text)
    if not ph:
        return params

    after = text[ph.end():]
    next_h = re.search(r"^#{2,4}\s", after, re.MULTILINE)
    section = after[: next_h.start()] if next_h else after

    # Remove code fences from parameter section
    section_clean = _CODE_FENCE_RE.sub("", section)

    # Pattern 1: `name` (type) — description
    for m in re.finditer(
        r"`([^`]+)`\s*\(([^)]*)\)\s*[-—]\s*([^\n]+)",
        section_clean,
    ):
        params.append({
            "name": m.group(1).strip(),
            "type": m.group(2).strip(),
            "description": m.group(3).strip(),
            "optional": "optional" in m.group(3).lower() or "optional" in m.group(2).lower(),
        })

    if params:
        return params

    # Pattern 2: - `name` (type): description
    for m in re.finditer(
        r"^\s*[-*]\s*`([^`]+)`\s*\(?([^):\n]*)\)?\s*:?\s*([^\n]+)",
        section_clean,
        re.MULTILINE,
    ):
        params.append({
            "name": m.group(1).strip(),
            "type": m.group(2).strip() if m.group(2).strip() else None,
            "description": m.group(3).strip(),
            "optional": "optional" in m.group(3).lower(),
        })

    if params:
        return params

    # Pattern 3: word: description (simple list items)
    for m in re.finditer(
        r"^\s*([a-zA-Z_][a-zA-Z0-9_]*):\s+([^\n]+)",
        section_clean,
        re.MULTILINE,
    ):
        name_p = m.group(1)
        if name_p.lower() in ("note", "type", "returns", "remarks", "example"):
            continue
        params.append({
            "name": name_p,
            "type": None,
            "description": m.group(2).strip(),
            "optional": "optional" in m.group(2).lower(),
        })

    return params


# ─────────────────────────────────────────────────────────────────────────────
# Main parsing logic
# ─────────────────────────────────────────────────────────────────────────────

def split_into_sections(content: str) -> list[tuple[str, str]]:
    """
    Split the full reference file into (section_name, section_text) pairs
    based on top-level `#` headings.
    """
    # Find all level-1 headings
    h1_re = re.compile(r"^# (.+)$", re.MULTILINE)
    matches = list(h1_re.finditer(content))
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections.append((title, content[start:end]))
    return sections


def split_section_into_entries(section_text: str) -> list[tuple[str, str]]:
    """
    Within a section, split on `##` headings to get individual entries.
    Returns list of (entry_name, entry_raw_text).
    """
    h2_re = re.compile(r"^## (.+)$", re.MULTILINE)
    matches = list(h2_re.finditer(section_text))
    entries: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_text)
        raw = section_text[start:end].rstrip()
        entries.append((name, raw))
    return entries


def parse_entry(
    name: str,
    raw_text: str,
    category: str,
    slug_counter: dict[str, int],
) -> dict:
    """Build a fully-structured entry dict from raw entry text."""

    # ── Slug / overload handling ──────────────────────────────────────────
    base_slug = make_slug(name)
    if base_slug in slug_counter:
        slug_counter[base_slug] += 1
        entry_id = f"{base_slug}_overload{slug_counter[base_slug]}"
    else:
        slug_counter[base_slug] = 1
        entry_id = base_slug

    # ── Namespace ─────────────────────────────────────────────────────────
    namespace = detect_namespace(name)

    # ── Type annotation (for variables / constants) ───────────────────────
    type_ann = extract_type_annotation(raw_text)

    # ── Syntax ────────────────────────────────────────────────────────────
    syntax = extract_syntax(raw_text, name, category)
    if syntax is None and type_ann:
        syntax = f"{name}  →  {type_ann}"

    # ── Description ───────────────────────────────────────────────────────
    description = extract_description(raw_text, name)

    # ── Parameters ────────────────────────────────────────────────────────
    parameters = extract_parameters(raw_text)

    # ── Returns ───────────────────────────────────────────────────────────
    returns = extract_returns(raw_text)
    if returns is None and type_ann and category in ("variable", "constant"):
        returns = type_ann

    # ── Remarks ───────────────────────────────────────────────────────────
    remarks = extract_remarks(raw_text)

    # ── Code examples ─────────────────────────────────────────────────────
    examples = extract_code_examples(raw_text)

    # ── See also ──────────────────────────────────────────────────────────
    see_also = extract_see_also(raw_text)

    return {
        "id":          entry_id,
        "name":        name,
        "category":    category,
        "namespace":   namespace,
        "syntax":      syntax,
        "description": description,
        "parameters":  parameters,
        "returns":     returns,
        "remarks":     remarks,
        "examples":    examples,
        "see_also":    see_also,
        "raw_text":    raw_text,
    }


def parse_docs(source_path: Path) -> list[dict]:
    """Parse the full reference file and return a list of entry dicts."""
    print(f"[parse_docs] Reading: {source_path}")
    content = source_path.read_text(encoding="utf-8")
    print(f"[parse_docs] File size: {len(content):,} characters")

    sections = split_into_sections(content)
    print(f"[parse_docs] Detected {len(sections)} top-level sections")

    all_entries: list[dict] = []
    slug_counter: dict[str, int] = {}

    for section_title, section_text in sections:
        # Normalise section title to category key
        section_key = section_title.lower().split()[0]  # "Variables " → "variables"
        category = SECTION_TO_CATEGORY.get(section_key)

        if category is None:
            # Unknown section — skip structural noise like the TOC title
            print(f"[parse_docs]   Skipping section: '{section_title}'")
            continue

        entries_raw = split_section_into_entries(section_text)
        print(
            f"[parse_docs]   Section '{section_title}': {len(entries_raw)} entries  (category={category})"
        )

        for name, raw_text in entries_raw:
            entry = parse_entry(name, raw_text, category, slug_counter)
            all_entries.append(entry)

            # If the entry contains code examples, also emit synthetic
            # "example" entries so they surface in get_examples() searches.
            for idx, code in enumerate(entry["examples"]):
                if len(code.strip()) < 20:
                    continue  # skip trivially short snippets
                ex_id = f"{entry['id']}__example{idx + 1}"
                all_entries.append({
                    "id":          ex_id,
                    "name":        f"{name} — example {idx + 1}",
                    "category":    "example",
                    "namespace":   entry["namespace"],
                    "syntax":      None,
                    "description": f"Code example for {name}",
                    "parameters":  [],
                    "returns":     None,
                    "remarks":     None,
                    "examples":    [code],
                    "see_also":    [name],
                    "raw_text":    code,
                })

    return all_entries


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def find_source_file() -> Path:
    """Try known paths; raise if none found."""
    for p in SEARCH_PATHS:
        if p.exists():
            return p
    # Also accept first argument as override
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
        if p.exists():
            return p
    raise FileNotFoundError(
        "Could not find the PineScript v6 reference file. "
        "Tried:\n  " + "\n  ".join(str(p) for p in SEARCH_PATHS) + "\n"
        "Pass the path as first argument:  python parse_docs.py /path/to/reference.md"
    )


if __name__ == "__main__":
    source = find_source_file()
    entries = parse_docs(source)

    # ── Summary ──────────────────────────────────────────────────────────
    from collections import Counter
    counts = Counter(e["category"] for e in entries)
    total = len(entries)

    print("\n" + "═" * 60)
    print("  PARSE SUMMARY")
    print("═" * 60)
    for cat, n in sorted(counts.items()):
        print(f"  {cat:<15}  {n:>5}")
    print("─" * 60)
    print(f"  {'TOTAL':<15}  {total:>5}")
    print("═" * 60)

    # ── Write output ─────────────────────────────────────────────────────
    OUTPUT_FILE.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n[parse_docs] Written {total} entries → {OUTPUT_FILE}")
    print("[parse_docs] Done.\n")
