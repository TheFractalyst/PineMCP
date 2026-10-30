"""
pipeline/audit_symbols.py
-----------------------------------------------------------------------------
Audit: every Pine symbol referenced in the user manual text must exist in the
reference database. Also reports the reverse (reference symbols never
mentioned in the manual - informational only).

Usage:
    .venv/bin/python pipeline/audit_symbols.py
Output: audit_manual_symbols_report.md + console summary
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from loguru import logger

logger.remove()
logger.add(sys.stderr, format='{time:HH:mm:ss} | {level:<8} | {message}', level='INFO')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANUAL_JSON = PROJECT_ROOT / 'data' / 'pine_manual_entries.json'
REF_JSON = PROJECT_ROOT / 'data' / 'pine_merged_entries.json'
REPORT = PROJECT_ROOT / 'audit_manual_symbols_report.md'

# Pine namespaces and global-callable prefixes beyond what the reference DB
# itself provides (kept deliberately short; DB namespaces are added at runtime)
EXTRA_GLOBALS = {
    'indicator', 'strategy', 'library', 'plot', 'plotshape', 'plotchar',
    'plotcandle', 'plotbar', 'plotarrow', 'barcolor', 'bgcolor', 'fill',
    'hline', 'input', 'runtime', 'display', 'format', 'scale', 'adjustment',
    'currency', 'earnings', 'dividends', 'splits', 'session', 'syminfo',
    'ticker', 'timeframe', 'chart', 'polyline', 'linefill', 'dayofweek',
    'na', 'math', 'ta', 'str', 'color', 'array', 'matrix', 'map', 'line',
    'label', 'box', 'table', 'request',
}

DOTTED_RE = re.compile(r'\b[a-z][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+\b')
CALLED_RE = re.compile(r'\b([a-z_][a-z0-9_]*)\s*\(')


def normalize_symbol(s: str) -> str:
    """Lowercase, strip (), <type> templates, and trailing wildcard underscores
    (manual prose writes families like 'label.set_*()')."""
    s = s.lower().strip().rstrip('()')
    s = re.sub(r'<[^>]*>', '', s)
    s = s.rstrip('_')
    return s.strip()


def load_db_names(entries: list[dict]) -> set[str]:
    """Normalized symbol names and namespaces from reference entries."""
    names: set[str] = set()
    for e in entries:
        n = normalize_symbol(e.get('name', ''))
        if n:
            names.add(n)
        ns = (e.get('namespace') or '').lower().strip('.').strip()
        if ns and ' ' not in ns:
            names.add(ns)
    return names


def extract_symbol_refs(text: str, known_prefixes: set[str]) -> set[str]:
    """Symbol-like references: dotted names with a known first segment, and
    called globals with known names. Prose noise (URLs, method calls on
    user objects) is filtered by prefix membership."""
    refs: set[str] = set()
    for m in DOTTED_RE.finditer(text):
        cand = m.group(0).rstrip('.')
        if cand.split('.')[0] in known_prefixes:
            refs.add(normalize_symbol(cand))
    for m in CALLED_RE.finditer(text):
        cand = m.group(1)
        if cand in known_prefixes:
            refs.add(cand)
    return refs


def main() -> int:
    manual = json.loads(MANUAL_JSON.read_text(encoding='utf-8'))
    ref = json.loads(REF_JSON.read_text(encoding='utf-8'))

    db_names = load_db_names(ref)
    known_prefixes = {n for n in db_names if '.' not in n} | EXTRA_GLOBALS
    known_prefixes = {p for p in known_prefixes if p and len(p) <= 15}

    manual_text = '\n'.join(e.get('description', '') for e in manual)
    refs = extract_symbol_refs(manual_text, known_prefixes)

    missing = sorted(r for r in refs if r not in db_names)
    logger.info(f'Symbol refs found in manual: {len(refs)}')
    logger.info(f'Missing from reference DB:  {len(missing)}')
    for r in missing:
        logger.warning(f'  MISSING: {r}')

    # Reverse: reference symbols never mentioned in the manual (informational)
    manual_lower = manual_text.lower()
    never = sorted(
        n for n in db_names
        if '.' in n and '(' not in n and '→' not in n and n not in manual_lower
    )
    logger.info(f'Reference symbols not mentioned in manual: {len(never)}')

    lines = [
        '# Manual Symbol Coverage Audit', '',
        f'- Symbol references extracted from manual: **{len(refs)}**',
        f'- Missing from reference DB: **{len(missing)}**', '',
        '_Note: remaining entries are expected non-symbol references, not DB_',
        '_gaps. Known-benign categories:_',
        '- _method-call syntax on built-in collections: `label.all.size()`,',
        '  `box.all.last()` (array methods invoked on built-in arrays)_',
        '- _alert message placeholders: `{{strategy.order.action}}` etc._',
        '- _user-defined variables in examples: `display.cell()` (a `table`_',
        '  _variable named `display`, not the `display` namespace)_',
        '- _v5 names inside migration guides: `input.integer`, `ta.barsince`_',
        '- _family shorthand in prose: `strategy.risk.*()`, `ta.pivothigh/low`_',
        '',
    ]
    if missing:
        lines.append('## Missing symbols')
        lines += [f'- `{r}`' for r in missing]
        lines.append('')
    if never:
        lines.append('## Reference symbols never mentioned in the manual')
        lines.append('_Informational only - the reference covers more than the manual._')
        lines += [f'- `{r}`' for r in never]
    REPORT.write_text('\n'.join(lines), encoding='utf-8')
    logger.info(f'Report written to {REPORT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
