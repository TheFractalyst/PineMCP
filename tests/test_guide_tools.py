"""Guide-kind tool tests: lookup, formatter PATH line."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_OUT = Path(__file__).resolve().parent.parent / 'data' / 'pine_manual_entries.json'

pytestmark = pytest.mark.skipif(
    not DATA_OUT.exists(), reason='pine_manual_entries.json not generated'
)


def test_kind_literal_includes_guide():
    from tools.lookup import _Kind
    assert 'guide' in _Kind.__args__


async def test_lookup_guide_repainting():
    from tools.lookup import pine_lookup
    result = await pine_lookup('Repainting', kind='guide')
    assert 'GUIDE' in result
    assert 'repaint' in result.lower()


async def test_lookup_guide_by_path_tail():
    from tools.lookup import pine_lookup
    result = await pine_lookup('concepts/repainting', kind='guide')
    assert 'GUIDE' in result


async def test_lookup_execution_model_default_finds_guide():
    from tools.lookup import pine_lookup
    result = await pine_lookup('Execution model')
    assert 'GUIDE: Execution model' in result


def test_format_entry_detail_path_line():
    from formatters.entry import format_entry_detail
    meta = {
        'category': 'guide', 'namespace': 'concepts',
        'path': 'concepts/repainting', 'raw_description': 'Repainting text',
        'raw_see_also': '',
    }
    out = format_entry_detail('Repainting', meta, 'Repainting text body')
    assert 'GUIDE: Repainting' in out
    assert 'PATH: concepts/repainting' in out


async def test_search_category_guide():
    from tools.search import pine_search
    result = await pine_search('how to avoid repainting', category='guide')
    assert 'Repainting' in result


async def test_browse_concepts_lists_guides():
    from tools.context import pine_browse
    result = await pine_browse('concepts')
    assert 'Repainting' in result
    assert 'GUIDE' in result.upper() or 'guide' in result
