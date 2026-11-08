"""Tests for pipeline/scrape_docs.py HTML parsing (offline, fixture-based)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.scrape_docs import (  # noqa: E402
    clean_text,
    doc_urls,
    parse_page,
    tag_error_codes,
)

SITEMAP = """<urlset>
<url><loc>https://www.tradingview.com/pine-script-docs/language/conditional-structures/</loc></url>
<url><loc>https://www.tradingview.com/pine-script-docs/release-notes/</loc></url>
<url><loc>https://www.tradingview.com/pine-script-docs/concepts/</loc></url>
<url><loc>https://www.tradingview.com/pine-script-docs/v5/release-notes/</loc></url>
</urlset>"""

PAGE_HTML = """<html><body><div id="slot-container">
<h1 id="conditional-structures" class="md-heading">Conditional structures</h1>
<p>Structures control execution.</p>
<h2 id="once-structure" class="md-heading">once structure</h2>
<p>The once structure runs its block a single time.</p>
<div class="pine-colorizer not-content">//@version=6
indicator("demo")
once barstate.isfirst
    run_setup := true</div>
<h3 id="once-on-the-realtime-bar">once on the realtime bar</h3>
<p>Rollback resets the structure.</p>
</div></body></html>"""


class TestDocUrls:
    def test_keeps_v6_leaf_pages(self):
        pages = doc_urls(SITEMAP)
        assert ("https://www.tradingview.com/pine-script-docs/language/conditional-structures/", "language", "conditional-structures") in pages

    def test_release_notes_top_level_included(self):
        pages = doc_urls(SITEMAP)
        assert ("https://www.tradingview.com/pine-script-docs/release-notes/", "release", "") in pages

    def test_legacy_versions_skipped(self):
        assert not any("v5" in u for u, _, _ in doc_urls(SITEMAP))

    def test_index_redirect_stub_skipped(self):
        # /concepts/ is a meta-refresh redirect to its first child page.
        assert not any(u.rstrip("/").endswith("/concepts") for u, _, _ in doc_urls(SITEMAP))


class TestParsePage:
    def test_headings_become_entries(self):
        entries = parse_page(PAGE_HTML, "https://x/language/foo/", "language", "foo")
        paths = [e["path"] for e in entries]
        assert "language/conditional-structures" in paths
        assert "language/conditional-structures/once-structure" in paths
        assert "language/conditional-structures/once-structure/once-on-the-realtime-bar" in paths

    def test_code_block_preserved(self):
        entries = parse_page(PAGE_HTML, "https://x/language/foo/", "language", "foo")
        once = next(e for e in entries if e["path"].endswith("once-structure"))
        assert once["examples"], "code block lost"
        assert "once barstate.isfirst" in once["examples"][0]
        assert "\n" in once["examples"][0]
        assert "once barstate.isfirst" in once["description"]

    def test_see_also_lists_children(self):
        entries = parse_page(PAGE_HTML, "https://x/language/foo/", "language", "foo")
        page = entries[0]
        assert "once structure" in page["see_also"]

    def test_urls_on_every_entry(self):
        for e in parse_page(PAGE_HTML, "https://x/language/foo/", "language", "foo"):
            assert e["url"] == "https://x/language/foo/"


class TestCleanText:
    def test_strips_zero_width(self):
        assert clean_text("once\u200b structure") == "once structure"

    def test_collapses_blank_lines(self):
        assert clean_text("a\n\n\n\nb") == "a\n\nb"


class TestErrorCodes:
    def test_leaf_error_page_renamed(self):
        entries = [
            {"name": "Memory limits exceeded", "namespace": "errors", "path": "errors/RE10139/memory-limits-exceeded", "description": "body"},
            {"name": "How do I fix this", "namespace": "errors", "path": "errors/RE10139/memory-limits-exceeded/how-do-i-fix-this", "description": "body"},
            {"name": "Overview", "namespace": "errors", "path": "errors/overview", "description": "body"},
        ]
        n = tag_error_codes(entries)
        assert n == 1
        assert entries[0]["name"] == "RE10139 - Memory limits exceeded"
        assert entries[1]["name"] == "How do I fix this"
        assert entries[2]["name"] == "Overview"
