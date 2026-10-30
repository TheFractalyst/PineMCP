"""Tests for pipeline/audit_symbols.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.audit_symbols import (  # noqa: E402
    extract_symbol_refs,
    load_db_names,
    normalize_symbol,
)


class TestNormalizeSymbol:
    def test_strips_parens_and_template(self):
        assert normalize_symbol('array.new<type>()') == 'array.new'
        assert normalize_symbol('ta.ema') == 'ta.ema'

    def test_lowercase(self):
        assert normalize_symbol('Request.Security') == 'request.security'


class TestExtractSymbolRefs:
    def test_dotted_and_called(self):
        text = 'Use ta.ema(close, 20) and array.new<float>() plus plot(close).'
        refs = extract_symbol_refs(text, {'ta', 'array', 'plot'})
        assert 'ta.ema' in refs
        assert 'array.new' in refs
        assert 'plot' in refs

    def test_ignores_urls_and_prose(self):
        text = 'Visit www.tradingview.com or see e.g. the docs at https://x.io/a.b'
        refs = extract_symbol_refs(text, {'ta', 'array', 'plot'})
        assert not any('tradingview' in r for r in refs)
        assert not any(r.startswith('http') for r in refs)

    def test_method_call_noise_skipped(self):
        text = 'this.get() and myObj.setValue(1)'
        refs = extract_symbol_refs(text, {'ta', 'array', 'plot'})
        assert 'this.get' not in refs
        assert 'myobj.setvalue' not in refs


class TestLoadDbNames:
    def test_variants(self):
        entries = [
            {'name': 'ta.ema()', 'category': 'function'},
            {'name': 'array.new<type>()', 'category': 'function'},
            {'name': 'close', 'category': 'variable'},
        ]
        names = load_db_names(entries)
        assert 'ta.ema' in names
        assert 'array.new' in names
        assert 'close' in names
