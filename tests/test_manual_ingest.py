"""Tests for pipeline/ingest_manual.py (pure functions + PDF integration)."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.ingest_manual import (  # noqa: E402
    build_entries,
    coverage_ratio,
    locate_starts,
    norm_title,
    slice_contents,
    slugify,
)

# The user manual PDF is not distributed with this project. Set
# PINESCRIPT_MANUAL_PDF to run the PDF integration test locally.
MANUAL_PDF = os.environ.get('PINESCRIPT_MANUAL_PDF') or ''
DATA_OUT = Path(__file__).resolve().parent.parent / 'data' / 'pine_manual_entries.json'


class TestNormTitle:
    def test_strips_zwsp_and_trademark(self):
        assert norm_title('\u200bindicators\u201d vs \u201cstrategies\u201d') == 'indicators vs strategies'

    def test_lowercases_and_collapses_punctuation(self):
        assert norm_title('Where can I get more information?') == 'where can i get more information'

    def test_empty(self):
        assert norm_title('   ') == ''


class TestSlugify:
    def test_basic(self):
        assert slugify('Execution model') == 'execution-model'

    def test_stops_at_parenthetical(self):
        assert slugify('Overview (Visuals)') == 'overview'


class TestLocateStarts:
    def _pages(self):
        return {
            0: ['Intro text', 'Arrays', 'array content line'],
            1: ['Second page', 'Maps', 'map content'],
        }

    def test_same_page_sequential(self):
        nodes = [
            {'title': 'Arrays', 'page': 0, 'depth': 1},
            {'title': 'Maps', 'page': 1, 'depth': 1},
        ]
        starts, misses = locate_starts(nodes, self._pages())
        assert starts == [(0, 1), (1, 1)]
        assert misses == 0

    def test_title_on_next_page_corrects_page(self):
        nodes = [
            {'title': 'Arrays', 'page': 0, 'depth': 1},
            {'title': 'Maps', 'page': 0, 'depth': 1},
        ]
        starts, misses = locate_starts(nodes, self._pages())
        assert starts[1][0] == 1  # corrected to page 1
        assert misses == 0

    def test_missing_title_falls_back_to_cursor(self):
        nodes = [
            {'title': 'Arrays', 'page': 0, 'depth': 1},
            {'title': 'Nonexistent Heading', 'page': 1, 'depth': 1},
        ]
        starts, misses = locate_starts(nodes, self._pages())
        assert misses == 1
        assert starts[1] == (1, 0)


class TestSliceContents:
    def test_disjoint_and_complete(self):
        nodes = [
            {'title': 'Arrays', 'page': 0, 'depth': 1},
            {'title': 'Maps', 'page': 1, 'depth': 1},
        ]
        starts = [(0, 1), (1, 1)]
        pages = {
            0: ['Intro text', 'Arrays', 'array content'],
            1: ['Second page', 'Maps', 'map content'],
        }
        contents = slice_contents(nodes, starts, pages)
        assert contents == ['Arrays\narray content\nSecond page', 'Maps\nmap content']

    def test_cross_page_slice(self):
        nodes = [
            {'title': 'Arrays', 'page': 0, 'depth': 1},
            {'title': 'Maps', 'page': 2, 'depth': 1},
        ]
        starts = [(0, 1), (2, 0)]
        pages = {
            0: ['Intro', 'Arrays', 'a1'],
            1: ['b1', 'b2'],
            2: ['Maps', 'c1'],
        }
        contents = slice_contents(nodes, starts, pages)
        assert contents == ['Arrays\na1\nb1\nb2', 'Maps\nc1']

    def test_last_node_runs_to_document_end(self):
        nodes = [{'title': 'Maps', 'page': 1, 'depth': 1}]
        starts = [(1, 1)]
        pages = {0: ['Intro'], 1: ['Second page', 'Maps', 'tail']}
        contents = slice_contents(nodes, starts, pages)
        assert contents == ['Maps\ntail']


class TestBuildEntries:
    def test_disambiguation_and_paths(self):
        nodes = [
            {'title': 'Language', 'page': 0, 'depth': 0, 'part': 'language',
             'path': 'language', 'slug': 'language', 'parent_titles': []},
            {'title': 'Overview', 'page': 1, 'depth': 1, 'part': 'language',
             'path': 'language/overview', 'slug': 'overview', 'parent_titles': ['Language']},
            {'title': 'Arrays', 'page': 4, 'depth': 1, 'part': 'language',
             'path': 'language/arrays', 'slug': 'arrays', 'parent_titles': ['Language']},
            {'title': 'Visuals', 'page': 2, 'depth': 0, 'part': 'visuals',
             'path': 'visuals', 'slug': 'visuals', 'parent_titles': []},
            {'title': 'Overview', 'page': 3, 'depth': 1, 'part': 'visuals',
             'path': 'visuals/overview', 'slug': 'overview', 'parent_titles': ['Visuals']},
        ]
        contents = ['lang intro', 'overview of language', 'arrays text',
                    'vis intro', 'overview of visuals']
        entries = build_entries(nodes, contents)
        by_name = {e['name']: e for e in entries}
        # Duplicate titles get part suffix
        assert 'Overview (Language)' in by_name
        assert 'Overview (Visuals)' in by_name
        # Unique title stays bare
        assert 'Arrays' in by_name
        arrays = by_name['Arrays']
        assert arrays['category'] == 'guide'
        assert arrays['namespace'] == 'language'
        assert arrays['path'] == 'language/arrays'
        assert arrays['description'] == 'arrays text'
        assert arrays['sources'] == ['user_manual_pdf']
        assert arrays['id'].startswith('manual_')
        # Parent gets see_also children
        lang = by_name['Language']
        assert 'Overview (Language)' in lang['see_also']
        assert 'Arrays' in lang['see_also']


class TestCoverageRatio:
    def test_full_coverage(self):
        pages = {0: ['abc', 'def'], 1: ['ghi']}
        contents = ['abc\ndef', 'ghi']
        assert coverage_ratio(pages, contents) == 1.0

    def test_partial(self):
        pages = {0: ['abcdefgh']}
        contents = ['abcd']
        assert 0 < coverage_ratio(pages, contents) < 1.0


@pytest.mark.skipif(not (MANUAL_PDF and Path(MANUAL_PDF).exists()),
                    reason='set PINESCRIPT_MANUAL_PDF to a local manual PDF')
class TestPdfIntegration:
    def test_ingest_output_exists_and_is_complete(self):
        assert DATA_OUT.exists(), 'run pipeline/ingest_manual.py first'
        entries = json.loads(DATA_OUT.read_text())
        assert len(entries) >= 1000
        assert all(e['category'] == 'guide' for e in entries)
        assert all(e['description'].strip() for e in entries)
        parts = {e['namespace'] for e in entries}
        assert {'language', 'concepts', 'visuals', 'writing', 'faq'} <= parts


class TestBuildDbGuideSupport:
    def test_flatten_metadata_has_path(self):
        from core.build_db import _flatten_metadata
        entry = {
            'name': 'Repainting', 'category': 'guide', 'namespace': 'concepts',
            'path': 'concepts/repainting', 'description': 'text',
            'sources': ['user_manual_pdf'],
        }
        meta = _flatten_metadata(entry)
        assert meta['path'] == 'concepts/repainting'
        assert meta['category'] == 'guide'

    def test_document_text_includes_path(self):
        from core.build_db import _build_document_text
        entry = {
            'name': 'Repainting', 'category': 'guide', 'namespace': 'concepts',
            'path': 'concepts/repainting', 'description': 'Repainting text',
        }
        doc = _build_document_text(entry)
        assert doc.startswith('GUIDE: Repainting')
        assert 'Path: concepts/repainting' in doc

    def test_reference_entry_unchanged(self):
        from core.build_db import _build_document_text
        entry = {'name': 'ta.ema', 'category': 'function', 'namespace': 'ta',
                 'syntax': 'ta.ema(source, length) -> series float',
                 'description': 'EMA'}
        doc = _build_document_text(entry)
        assert doc.startswith('FUNCTION: ta.ema')
        assert 'Path:' not in doc
