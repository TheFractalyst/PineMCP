"""Tests for pipeline/scrape_entries.py parsing (offline, fixture-based)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.scrape_entries import (  # noqa: E402
    canonical_name,
    extract_name,
    parse_raw_items,
)


def raw_item(**overrides):
    """Minimal raw item in the shape the in-page JS extractor emits."""
    base = {
        "id": "fun_ta.sma",
        "header": "ta.sma(source, length)",
        "section": "functions",
        "syntax_blocks": ["ta.sma(source, length) → series float"],
        "descriptions": ["The sma function returns the moving average."],
        "parameter_lines": ["source (series int/float) Series of values to process."],
        "returns_text": "Series float",
        "remarks_text": "",
        "type_text": "",
        "example_blocks": ["//@version=6\nindicator(\"x\")\nplot(ta.sma(close, 14))"],
        "see_also": ["ta.ema()"],
        "type_fields": [],
        "type_methods": [],
        "all_text": ["some text"],
    }
    base.update(overrides)
    return base


class TestCanonicalName:
    def test_strips_signature(self):
        assert canonical_name("ta.sma(source, length)") == "ta.sma"

    def test_strips_empty_parens(self):
        assert canonical_name("array.sort()") == "array.sort"

    def test_strips_generic_tag(self):
        assert canonical_name("array.new<type>()") == "array.new"
        assert canonical_name("map.new<type,type>()") == "map.new"

    def test_keeps_operators(self):
        assert canonical_name("<") == "<"
        assert canonical_name("?:") == "?:"


class TestParseRawItems:
    def test_basic_entry(self):
        entries = parse_raw_items([raw_item()])
        assert len(entries) == 1
        e = entries[0]
        assert e["name"] == "ta.sma"
        assert e["category"] == "function"
        assert e["namespace"] == "ta"
        assert e["parameters"][0]["name"] == "source"
        assert e["url"].endswith("#fun_ta.sma")

    def test_overloads_stored(self):
        item = raw_item(
            id="fun_array.sort",
            header="array.sort()",
            syntax_blocks=[
                "array.sort(id, order) → void",
                "array.sort(id, order, sort_field) → void",
            ],
        )
        e = parse_raw_items([item])[0]
        assert e["syntax"] == "array.sort(id, order) → void"
        assert e["overloads"] == ["array.sort(id, order, sort_field) → void"]

    def test_type_section_becomes_returns(self):
        item = raw_item(
            id="var_close",
            header="close",
            syntax_blocks=[],
            returns_text="",
            type_text="series float",
            descriptions=["Close price."],
        )
        e = parse_raw_items([item])[0]
        assert e["category"] == "variable"
        assert e["returns"] == "series float"

    def test_examples_keep_newlines(self):
        e = parse_raw_items([raw_item()])[0]
        assert "\n" in e["examples"][0]
        assert "\u00a0" not in e["examples"][0]

    def test_empty_headers_skipped(self):
        entries = parse_raw_items([raw_item(header="", id="fun_x")])
        assert entries == []

    def test_overload_variants_collapse(self):
        # Two scrape rows for the same symbol must merge into one entry.
        a = raw_item(id="fun_array.sort", header="array.sort()", syntax_blocks=["a", "b"])
        b = raw_item(id="fun_array.sort", header="array.sort()", syntax_blocks=["a", "b"])
        assert len(parse_raw_items([a, b])) == 1
