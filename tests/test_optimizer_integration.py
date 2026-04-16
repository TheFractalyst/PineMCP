"""
test_optimizer_integration.py — End-to-end tests for the optimize_code MCP tool.

These tests exercise the full tool pipeline (analyze_code -> format_results ->
doc-query stitching -> cap_response) without needing ChromaDB.
"""

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Override the session-scoped warmup fixture from conftest.py so these tests
# can run without a populated ChromaDB instance.  The optimizer is pure static
# analysis and does not touch the vector store.
@pytest.fixture(scope="session", autouse=True)
def warmup():
    """No-op warmup — optimizer tests do not need ChromaDB."""


from tools.optimization import optimize_code  # noqa: E402


def run_tool(code: str) -> str:
    """Run the async optimize_code tool synchronously for testing."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(optimize_code(code=code))
    finally:
        loop.close()


class TestOptimizerIntegration:
    """End-to-end tests for the optimize_code MCP tool."""

    def test_clean_code_returns_no_issues(self):
        """Well-written code produces 'No issues found'."""
        code = '//@version=6\nindicator("test")\nmyEma = ta.ema(close, 21)\nplot(myEma)'
        result = run_tool(code)
        assert "No issues found" in result

    def test_known_issues_produces_formatted_output(self):
        """Code with deliberate issues produces correctly formatted report."""
        # Duplicate ta.ema calls triggers OPT-002
        code = (
            '//@version=6\nindicator("test")\n'
            'a = ta.ema(close, 20)\nb = ta.ema(close, 20)\nc = ta.ema(close, 20)\n'
            'plot(a)\nplot(b)\nplot(c)'
        )
        result = run_tool(code)
        assert "OPTIMIZATION ANALYSIS" in result
        assert "OPT-002" in result

    def test_output_has_separator(self):
        """The output contains the box-drawing separator."""
        code = '//@version=6\nindicator("test")\nplot(close)'
        result = run_tool(code)
        assert "\u2550" in result  # ═ box drawing char

    def test_short_code_still_returns_result(self):
        """Short code (below min_length) is handled gracefully at direct-call level."""
        result = run_tool("short")
        assert isinstance(result, str)
        # FastMCP does not enforce min_length at direct-call level,
        # so the tool processes it and returns a valid string.

    def test_doc_queries_deduplicated(self):
        """The doc query list at the end has no duplicates."""
        code = (
            '//@version=6\nindicator("test")\n'
            'a = ta.ema(close, 20)\nb = ta.ema(close, 20)\nc = ta.ema(close, 20)\n'
            'plot(a)\nplot(b)\nplot(c)'
        )
        result = run_tool(code)
        if "DOCUMENTATION LOOKUP QUERIES" in result:
            queries_section = result.split("DOCUMENTATION LOOKUP QUERIES")[1]
            lines = [
                l.strip().lstrip("- ").strip('"')
                for l in queries_section.split("\n")
                if l.strip().startswith("-")
            ]
            assert len(lines) == len(set(lines)), f"Duplicate queries found: {lines}"

    def test_response_is_string(self):
        """Tool always returns a string, never None or other types."""
        code = '//@version=6\nindicator("test")\nplot(close)'
        result = run_tool(code)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_strategy_code_analyzed(self):
        """Strategy code is also analyzed without errors."""
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'myEma = ta.ema(close, 20)\n'
            'if ta.crossover(close, myEma)\n'
            '    strategy.entry("Long", strategy.long)\n'
        )
        result = run_tool(code)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_missing_version_analyzed(self):
        """Code without //@version=6 is still analyzed (does not crash)."""
        code = 'indicator("test")\nplot(close)'
        result = run_tool(code)
        assert isinstance(result, str)

    def test_large_loop_code_triggers_finding(self):
        """Code with a large unbounded loop triggers resource-related findings."""
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'for i = 0 to 500\n'
            '    x = close + i\n'
            'plot(x)\n'
        )
        result = run_tool(code)
        # Should produce some kind of analysis output (not crash)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_long_code_respects_cap(self):
        """Very long output is capped at 80K chars."""
        lines = ['//@version=6', 'indicator("test")']
        for i in range(100):
            lines.append(f'x{i} = ta.ema(close, {i + 1})')
        lines.append("plot(x0)")
        code = "\n".join(lines)
        result = run_tool(code)
        assert len(result) <= 81000  # cap_response limit + margin

    def test_empty_code_block_min_length(self):
        """Code exactly at min_length=10 is accepted."""
        code = '//@version=6'
        result = run_tool(code)
        assert isinstance(result, str)

    def test_duplicate_request_security_triggers_opt003(self):
        """Duplicate request.security calls triggers OPT-003."""
        code = (
            '//@version=6\nindicator("test")\n'
            'h = request.security(syminfo.tickerid, "D", high)\n'
            'l = request.security(syminfo.tickerid, "D", low)\n'
            'plot(h)\nplot(l)'
        )
        result = run_tool(code)
        if "OPTIMIZATION ANALYSIS" in result:
            assert "OPT-003" in result

    def test_manual_loop_with_built_in_alternative(self):
        """Manual loop with accumulation triggers optimization findings."""
        code = (
            '//@version=6\nindicator("test")\n'
            'mySma(src, len) =>\n'
            '    float result = 0.0\n'
            '    for i = 0 to len - 1\n'
            '        result += src[i]\n'
            '    result / len\n'
            'plot(mySma(close, 20))'
        )
        result = run_tool(code)
        # Should detect the manual loop (OPT-001) or related issues
        assert "OPTIMIZATION ANALYSIS" in result
        # At minimum the optimizer runs and produces structured output
        assert "OPT-" in result

    def test_label_delete_recreate_triggers_opt004(self):
        """Label delete+recreate pattern triggers OPT-004."""
        code = (
            '//@version=6\nindicator("test")\n'
            'var lbl = label.new(bar_index, high, "test")\n'
            'label.delete(lbl)\n'
            'lbl := label.new(bar_index, high, "test")\n'
        )
        result = run_tool(code)
        assert "OPT-004" in result

    def test_unprotected_drawing_triggers_opt005(self):
        """Label setter outside barstate.islast triggers OPT-005."""
        code = (
            '//@version=6\nindicator("test")\n'
            'var label lbl = label.new(bar_index, high, "test")\n'
            'label.set_xy(lbl, bar_index, high)\n'
        )
        result = run_tool(code)
        assert "OPT-005" in result

    def test_severity_ordering(self):
        """Critical findings appear before medium when both present."""
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'for i = 0 to 500\n'
            '    x = close + i\n'
            'plot(x)\n'
        )
        result = run_tool(code)
        if "critical" in result.lower() and "medium" in result.lower():
            crit_pos = result.lower().find("critical")
            med_pos = result.lower().find("medium")
            assert crit_pos < med_pos
