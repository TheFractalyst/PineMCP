"""Shipped-data regression tests: the dataset must cover current v6 features.

Every symbol here ships in a TradingView release note. If one of these tests
fails, the scraped data has drifted and needs a pipeline refresh
(scrape_entries.py + merge_reference.py, or scrape_docs.py).
"""

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "data" / "pine_merged_entries.json"
GUIDES = ROOT / "data" / "pine_docs_entries.json"

pytestmark = pytest.mark.skipif(not REF.exists(), reason="pine_merged_entries.json not generated")


@pytest.fixture(scope="module")
def reference():
    return json.loads(REF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def guides():
    if not GUIDES.exists():
        pytest.skip("pine_docs_entries.json not generated")
    return json.loads(GUIDES.read_text(encoding="utf-8"))


def by_name(reference, name, category=None):
    return [
        e
        for e in reference
        if e["name"].lower() == name.lower() and (category is None or e.get("category") == category)
    ]


class TestDatasetShape:
    def test_no_signature_names(self, reference):
        bad = [e["name"] for e in reference if "→" in e["name"] or "->" in e["name"] or "(" in e["name"]]
        assert bad == [], f"signature-named entries pollute the name index: {bad[:5]}"

    def test_no_duplicate_keys(self, reference):
        keys = [(e["name"].lower(), e.get("category", "")) for e in reference]
        assert len(keys) == len(set(keys)), "duplicate (name, category) rows collapse on DB build"

    def test_category_counts(self, reference):
        counts = {}
        for e in reference:
            counts[e["category"]] = counts.get(e["category"], 0) + 1
        assert counts.get("function", 0) >= 450
        assert counts.get("constant", 0) >= 200
        assert counts.get("variable", 0) >= 140
        assert counts.get("keyword", 0) >= 12


class TestReleaseNotesFeatures:
    """Symbols introduced by v6 release notes - regression guard for staleness."""

    def test_once_keyword(self, reference):
        entries = by_name(reference, "once", "keyword")
        assert entries, "once keyword (Aug 2026) missing from reference data"
        assert "condition" in entries[0]["syntax"]

    def test_runtime_calculation_mode(self, reference):
        assert by_name(reference, "runtime.calculation.mode", "variable")
        assert by_name(reference, "runtime.calculation.mode.chart", "constant")
        assert by_name(reference, "runtime.calculation.mode.alert", "constant")

    def test_strategy_calc_on_every_history_tick(self, reference):
        entry = by_name(reference, "strategy", "function")
        assert entry and "calc_on_every_history_tick" in entry[0]["syntax"]

    def test_sort_field_overload(self, reference):
        entry = by_name(reference, "array.sort", "function")[0]
        assert any("sort_field" in o for o in entry.get("overloads") or []), (
            "array.sort is missing the sort_field overload (Apr 2026 UDT sorting)"
        )

    def test_binary_search_sort_field(self, reference):
        entry = by_name(reference, "array.binary_search", "function")[0]
        assert any("sort_field" in o for o in entry.get("overloads") or [])

    def test_input_active_param(self, reference):
        entry = by_name(reference, "input.int", "function")[0]
        assert any(p["name"] == "active" for p in entry.get("parameters") or [])

    def test_plot_linestyle_param(self, reference):
        entry = by_name(reference, "plot", "function")[0]
        assert any(p["name"] == "linestyle" for p in entry.get("parameters") or [])
        assert by_name(reference, "plot.linestyle_dotted", "constant")

    def test_footprint_requests(self, reference):
        assert by_name(reference, "request.footprint", "function")
        assert by_name(reference, "footprint", "type")
        assert by_name(reference, "volume_row", "type")
        assert by_name(reference, "footprint.get_row_by_price", "function")

    def test_bid_ask_variables(self, reference):
        assert by_name(reference, "bid", "variable")
        assert by_name(reference, "ask", "variable")

    def test_syminfo_additions(self, reference):
        assert by_name(reference, "syminfo.isin", "variable")
        assert by_name(reference, "syminfo.current_contract", "variable")

    def test_box_set_xloc(self, reference):
        assert by_name(reference, "box.set_xloc", "function")


class TestGenericConstructors:
    def test_array_new_resolvable(self, reference):
        assert by_name(reference, "array.new", "function"), "pine_lookup('array.new') must resolve"
        assert by_name(reference, "map.new", "function")
        assert by_name(reference, "matrix.new", "function")


class TestParamsAndExamples:
    def test_core_functions_have_params(self, reference):
        for name in ("ta.ema", "strategy.entry", "array.push", "str.tostring", "request.security"):
            entry = by_name(reference, name, "function")[0]
            assert entry.get("parameters"), f"{name} has no parameter docs"

    def test_examples_preserve_newlines(self, reference):
        entry = by_name(reference, "ta.ema", "function")[0]
        assert entry.get("examples"), "ta.ema lost its example"
        assert "\n" in entry["examples"][0], "example lines collapsed (scraper <br> bug)"

    def test_deprecated_entries_are_real(self, reference):
        deprecated = [e["name"] for e in reference if e.get("deprecated")]
        assert len(deprecated) <= 10, f"deprecated flag over-fires: {deprecated[:10]}"


class TestGuideCoverage:
    def test_release_notes_fresh(self, guides):
        release_paths = [e["path"] for e in guides if e["namespace"] == "release"]
        joined = "\n".join(release_paths)
        assert "2026/october-2026" in joined, "release notes are stale - re-run scrape_docs.py"

    def test_guide_urls_present(self, guides):
        missing = [e["path"] for e in guides if not e.get("url")]
        assert missing == [], f"{len(missing)} guide entries without a docs URL"

    def test_once_structure_documented(self, guides):
        entry = next(e for e in guides if e["path"] == "language/conditional-structures/once-structure")
        assert len(entry["description"]) > 200
        assert entry["examples"], "once-structure guide lost its code examples"

    def test_error_codes_resolvable(self, guides):
        names = {e["name"] for e in guides if e["namespace"] == "errors"}
        for code in ("CE10101", "CE10117", "CW10003", "CW10026", "RE10139", "RE10143"):
            assert any(n.startswith(code) for n in names), f"error code {code} not lookup-able"
