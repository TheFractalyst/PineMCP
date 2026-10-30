"""
pipeline/ingest_manual.py
-----------------------------------------------------------------------------
STAGE: Ingest the Pine Script v6 User Manual PDF into guide entries.

Reads the official Chrome print-to-PDF of pine-script-docs, uses its embedded
outline (1318 nodes) plus pypdf per-page text to slice every section's content
at natural heading boundaries, and emits data/pine_manual_entries.json.

Completeness guarantee: sum of all entry texts must cover >= 98% of all page
text (whitespace-insensitive), or the script exits non-zero.

Usage:
    .venv/bin/python pipeline/ingest_manual.py --pdf /path/to/manual.pdf
    (or set PINESCRIPT_MANUAL_PDF)

Output: data/pine_manual_entries.json (~1318 guide entries)
Requires: pypdf (pipeline extra)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger

logger.remove()
logger.add(sys.stderr, format='{time:HH:mm:ss} | {level:<8} | {message}', level='INFO')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = os.environ.get('PINESCRIPT_MANUAL_PDF') or None
DEFAULT_OUTPUT = PROJECT_ROOT / 'data' / 'pine_manual_entries.json'
MIN_ENTRIES = 1000
DEFAULT_MIN_COVERAGE = 98.0

# Outline titles to skip (front matter, not documentation)
SKIP_TITLES = {'contents'}

# Top-level part titles -> namespace slugs
PART_SLUGS = {
    'Welcome to Pine Script® v6': 'welcome',
    'Pine Script® primer': 'primer',
    'Language': 'language',
    'Visuals': 'visuals',
    'Concepts': 'concepts',
    'Writing scripts': 'writing',
    'Errors and warnings': 'errors',
    'FAQ': 'faq',
    'Release notes': 'release',
    'Migration guides': 'migration',
}

FOOTER_RE = re.compile(r'Pine Script® v6 User Manual\s+\d+\s*/\s*2066')
_HEADER_LINE = 'Pine Script®'


def norm_title(s: str) -> str:
    """Normalize a title for matching: strip ZWSP/(R), punctuation to spaces."""
    s = s.replace('\u200b', '').replace('\u00ae', '').replace('\u2019', "'")
    s = re.sub(r'[^0-9a-zA-Z]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip().lower()


def squeeze(s: str) -> str:
    """Space-insensitive form for heading matching.

    Chrome-built outline titles concatenate wrapped heading lines without
    separators ('...in myalerts?'), while page text keeps spaces; comparing
    squeezed forms matches both.
    """
    return norm_title(s).replace(' ', '')


def slugify(s: str) -> str:
    """Title to path slug: lowercase, alphanumerics + dashes."""
    s = s.replace('\u200b', '').replace('\u00ae', '')
    s = re.sub(r'\s*\([^)]*\)', '', s)  # drop parenthetical suffixes
    s = re.sub(r'[^0-9a-zA-Z]+', '-', s).strip('-').lower()
    return s


def parse_outline(reader: Any) -> list[dict[str, Any]]:
    """Flatten the PDF outline into document-ordered nodes.

    pypdf represents outline children as a nested list following the parent
    item. `rec` walks those nested lists with depth+1. Depth-0 nodes are the
    manual's pages (h1 level); parts are assigned later by `assign_parts`.
    """
    flat: list[dict[str, Any]] = []

    def rec(its: list[Any], depth: int) -> None:
        for it in its:
            if isinstance(it, list):
                rec(it, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(it)
            except Exception:
                continue
            title = (getattr(it, 'title', '') or '').strip()
            if not title:
                continue
            if norm_title(title) in SKIP_TITLES:
                continue
            if 'User Manual' in title and depth == 0 and not flat:
                continue  # document title node
            flat.append({
                'depth': depth,
                'title': title,
                'page': page,
                'slug': slugify(title),
            })

    rec(reader.outline, 0)
    return flat


def parse_toc_slots(
    pages: dict[int, list[str]], last_toc_page: int
) -> list[tuple[str, str]]:
    """Flatten the Contents pages into (part_slug, title) slots.

    A TOC line that matches a known part title opens that part; every other
    line is a child page of the current part. Order is preserved so a later
    cursor-walk can assign parts to outline pages unambiguously.
    """
    part_by_norm = {norm_title(t): slug for t, slug in PART_SLUGS.items()}
    slots: list[tuple[str, str]] = []
    current = ''
    for page_idx in sorted(p for p in pages if p <= last_toc_page):
        for ln in pages[page_idx]:
            s = ln.strip()
            if not s or s == 'Contents' or FOOTER_RE.fullmatch(s):
                continue
            if s == _HEADER_LINE:
                continue
            norm = norm_title(s)
            if norm in part_by_norm:
                current = part_by_norm[norm]
                slots.append((current, s))
            elif current:
                slots.append((current, s))
    return slots


def assign_parts(nodes: list[dict[str, Any]], slots: list[tuple[str, str]]) -> None:
    """Assign part + path to every node.

    Depth-0 pages consume TOC slots in document order (matching by normalized
    title); a page with no slot inherits the current part. Deeper nodes take
    the part of their depth-0 ancestor and a path chained from it.
    """
    cursor = 0
    current_part = ''
    stack: list[dict[str, Any]] = []
    for node in nodes:
        if node['depth'] == 0:
            norm = norm_title(node['title'])
            match_idx = None
            for k in range(cursor, len(slots)):
                if norm_title(slots[k][1]) == norm:
                    match_idx = k
                    break
            if match_idx is not None:
                current_part = slots[match_idx][0]
                cursor = match_idx + 1
            elif not current_part:
                current_part = 'misc'
            node['part'] = current_part
            node['path'] = current_part + '/' + node['slug']
            stack = [node]
        else:
            while stack and stack[-1]['depth'] >= node['depth']:
                stack.pop()
            node['part'] = current_part
            node['path'] = stack[-1]['path'] + '/' + node['slug']
            stack.append(node)


def extract_pages(reader: Any) -> dict[int, list[str]]:
    """Per-page cleaned lines: footers and bare running headers removed."""
    pages: dict[int, list[str]] = {}
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ''
        lines: list[str] = []
        for ln in text.splitlines():
            s = ln.strip()
            if not s:
                lines.append('')
                continue
            if FOOTER_RE.fullmatch(s):
                continue
            if s == _HEADER_LINE:
                continue  # bare running header
            lines.append(ln.rstrip())
        pages[i] = lines
    return pages


def locate_starts(
    nodes: list[dict[str, Any]], pages: dict[int, list[str]]
) -> tuple[list[tuple[int, int]], int]:
    """Locate each node's (page, line) start via title match after a cursor.

    Long titles wrap across several rendered lines, so matching tries: the
    exact line, then windows of consecutive lines joined (2..6 lines), then a
    prefix match on a joined window (headings with trailing decorations).
    When a window match hits exactly, the node's title is repaired in place
    from the page lines: Chrome-built outline titles concatenate wrapped
    lines without spaces, the rendered text has them.
    """
    starts: list[tuple[int, int]] = []
    cursors: dict[int, int] = {}
    misses = 0

    def find_from(lines: list[str], cur: int, norm_t: str) -> tuple[int, str] | None:
        sq_t = norm_t.replace(' ', '')
        for i in range(cur, len(lines)):
            if norm_title(lines[i].strip()) == norm_t:
                return i, lines[i].strip()
        for k in (2, 3, 4, 5, 6):
            for i in range(cur, max(cur, len(lines) - k + 1)):
                parts = [lines[i + j].strip() for j in range(k)]
                sq_j = squeeze(' '.join(parts))
                if sq_j == sq_t:
                    return i, ' '.join(parts)
                if len(sq_t) > 12 and sq_j.startswith(sq_t):
                    return i, ' '.join(parts)
        return None

    for node in nodes:
        page_idx = node['page']
        norm_t = norm_title(node['title'])
        found: tuple[int, int] | None = None
        if norm_t:
            lines = pages.get(page_idx, [])
            cur = cursors.get(page_idx, 0)
            hit = find_from(lines, cur, norm_t)
            if hit is None:  # maybe outline page off by one
                nxt = pages.get(page_idx + 1, [])
                hit = find_from(nxt, 0, norm_t)
                if hit is not None:
                    page_idx += 1
            if hit is not None:
                i, matched = hit
                found = (page_idx, i)
                if matched != node['title']:
                    node['title'] = matched
        if found is None:
            misses += 1
            found = (page_idx, cursors.get(page_idx, 0))
        starts.append(found)
        cursors[found[0]] = found[1] + 1
    return starts, misses


def slice_contents(
    nodes: list[dict[str, Any]],
    starts: list[tuple[int, int]],
    pages: dict[int, list[str]],
) -> list[str]:
    """Disjoint per-node content: node start line to next node start line."""
    last_page = max(pages) if pages else 0
    contents: list[str] = []
    for idx in range(len(nodes)):
        p, ln0 = starts[idx]
        if idx + 1 < len(nodes):
            np_, nl = starts[idx + 1]
        else:
            np_, nl = last_page + 1, 0
        lines: list[str] = []
        for page in range(p, np_ + 1):
            if page > last_page:
                break
            page_lines = pages.get(page, [])
            if page == p and page == np_:
                seg = page_lines[ln0:max(nl, ln0)]
            elif page == p:
                seg = page_lines[ln0:]
            elif page == np_:
                seg = page_lines[:nl]
            else:
                seg = page_lines
            lines.extend(seg)
        text = '\n'.join(lines).strip()
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+\n', '\n', text)
        contents.append(text)
    return contents


_CODE_RE = re.compile(r'(ce|cw|re)\d{5}')


def tag_error_codes(
    nodes: list[dict[str, Any]], names: list[str], contents: list[str]
) -> None:
    """Rename errors-part entries to '<code> - <message>' via the error
    reference table, so pine_lookup('CE10101') resolves.

    Table rows are '<code> <message> <solution>'; wrapped-cell artifacts are
    handled by squeeze() comparison (see norm_title docstring).
    """
    table_idx = next(
        (i for i, n in enumerate(nodes)
         if n.get('path') == 'errors/overview/error-reference-table'),
        None,
    )
    if table_idx is None:
        return
    sq_table = squeeze(contents[table_idx])
    codes = [(m.start(), m.group(0).upper()) for m in _CODE_RE.finditer(sq_table)]
    if not codes:
        return
    for i, node in enumerate(nodes):
        if node.get('part') != 'errors' or node['depth'] < 1:
            continue
        sq_title = squeeze(node['title'])
        if len(sq_title) < 10:
            continue
        # Full title first; the table's message cells can be truncated where
        # the solution column interleaves, so fall back to title prefixes.
        pos = sq_table.find(sq_title)
        if pos < 0:
            for plen in (40, 20):
                if len(sq_title) > plen:
                    pos = sq_table.find(sq_title[:plen])
                    if pos >= 0:
                        break
        if pos < 0:
            continue
        prior = [c for c in codes if c[0] < pos]
        if prior:
            names[i] = f"{prior[-1][1]} - {node['title']}"


def build_entries(
    nodes: list[dict[str, Any]], contents: list[str]
) -> list[dict[str, Any]]:
    """Build guide entries; duplicate titles get part-suffixed names."""
    title_counts = Counter(norm_title(n['title']) for n in nodes)
    # Part display titles: known parts first, else the part's first page title
    slug_to_title = {slug: title for title, slug in PART_SLUGS.items()}
    first_page_title: dict[str, str] = {}
    for n in nodes:
        if n['depth'] == 0 and n['part'] not in first_page_title:
            first_page_title[n['part']] = n['title']
    part_titles = {
        part: slug_to_title.get(part, first_page_title.get(part, part))
        for part in {n['part'] for n in nodes}
    }

    def display_name(node: dict[str, Any]) -> str:
        key = norm_title(node['title'])
        if title_counts[key] <= 1:
            return node['title']
        part_title = part_titles.get(node['part'], node['part'])
        return f"{node['title']} ({part_title})"

    # Pre-compute names so see_also uses final display names
    names = [display_name(n) for n in nodes]
    tag_error_codes(nodes, names, contents)

    # children lookup: stack walk in document order (children follow parents)
    children_of: dict[str, list[str]] = {}
    stack: list[dict[str, Any]] = []
    for node, name in zip(nodes, names):
        while stack and stack[-1]['depth'] >= node['depth']:
            stack.pop()
        if stack:
            children_of.setdefault(stack[-1]['path'], []).append(name)
        stack.append(node)

    entries: list[dict[str, Any]] = []
    for node, content, name in zip(nodes, contents, names):
        entries.append({
            'name': name,
            'category': 'guide',
            'namespace': node['part'],
            'path': node['path'],
            'description': content,
            'examples': [],
            'parameters': [],
            'returns': '',
            'remarks': '',
            'see_also': children_of.get(node['path'], []),
            'url': '',
            'sources': ['user_manual_pdf'],
            'id': 'manual_' + node['path'].replace('/', '_'),
        })
    return entries


def coverage_ratio(pages: dict[int, list[str]], contents: list[str]) -> float:
    """Whitespace-insensitive character coverage of entries over all pages."""
    total = sum(len(re.sub(r'\s+', '', '\n'.join(pl))) for pl in pages.values())
    covered = sum(len(re.sub(r'\s+', '', c)) for c in contents)
    return covered / total if total else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, default=DEFAULT_PDF,
                        help='Path to the Pine Script v6 User Manual PDF '
                             '(or set PINESCRIPT_MANUAL_PDF)')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--min-coverage', type=float, default=DEFAULT_MIN_COVERAGE)
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    if args.pdf is None or not args.pdf.exists():
        logger.error(
            'Manual PDF not found - pass --pdf /path/to/manual.pdf '
            '(or set PINESCRIPT_MANUAL_PDF). This tool does not distribute '
            'the PDF; download it from TradingView.'
        )
        return 2

    from pypdf import PdfReader

    logger.info(f'Reading {args.pdf}...')
    reader = PdfReader(str(args.pdf))
    logger.info(f'Pages: {len(reader.pages)}')

    nodes = parse_outline(reader)
    logger.info(f'Outline nodes: {len(nodes)}')
    if len(nodes) < MIN_ENTRIES:
        logger.error(f'Only {len(nodes)} outline nodes - aborting')
        return 1

    pages = extract_pages(reader)

    first_content_page = min(n['page'] for n in nodes)
    slots = parse_toc_slots(pages, last_toc_page=first_content_page - 1)
    logger.info(f'TOC slots parsed: {len(slots)}')
    assign_parts(nodes, slots)

    starts, misses = locate_starts(nodes, pages)
    logger.info(f'Title misalignments (fallback used): {misses}')
    contents = slice_contents(nodes, starts, pages)

    cov = coverage_ratio(pages, contents)
    cov_pct = cov * 100
    logger.info(f'Coverage: {cov_pct:.2f}%')

    entries = build_entries(nodes, contents)
    part_counts = Counter(e['namespace'] for e in entries)
    for part, n in sorted(part_counts.items()):
        logger.info(f'  {part:<12} {n:>5} entries')
    logger.info(f'Total guide entries: {len(entries)}')

    empty = [e['name'] for e in entries if not e['description'].strip()]
    if empty:
        logger.warning(f'{len(empty)} entries with empty content: {empty[:10]}')

    if cov_pct < args.min_coverage:
        logger.error(
            f'Coverage {cov_pct:.2f}% below minimum {args.min_coverage}% - NOT writing output'
        )
        return 1
    if len(entries) < MIN_ENTRIES:
        logger.error(f'{len(entries)} entries below minimum {MIN_ENTRIES}')
        return 1

    if args.report_only:
        return 0

    args.output.write_text(
        json.dumps(entries, ensure_ascii=False, indent=1), encoding='utf-8'
    )
    size_mb = args.output.stat().st_size / 1e6
    logger.info(f'Wrote {len(entries)} entries to {args.output} ({size_mb:.1f} MB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
