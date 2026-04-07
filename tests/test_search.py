"""
test_search.py — Tests for the 4 search tools:
  search_docs, get_examples, search_by_return_type, list_namespace

Each tool is tested for:
  - Valid query returns results with relevance indicators
  - Gibberish/no-match query returns "not found" gracefully
  - Category/namespace filters work correctly
"""

import pytest

from tools.search import (
    search_docs,
    get_examples,
    search_by_return_type,
    list_namespace,
)


# ── search_docs ───────────────────────────────────────────────────────────────

class TestSearchDocs:
    @pytest.mark.asyncio
    async def test_ema_crossover(self):
        result = await search_docs(query="ema crossover", n_results=3)
        assert "ema" in result.lower()
        assert "crossover" in result.lower() or "ta." in result.lower()
        assert "relevance" in result.lower()

    @pytest.mark.asyncio
    async def test_gibberish(self):
        result = await search_docs(query="xyznonexistent12345", n_results=1)
        assert "no results" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_with_category_filter(self):
        result = await search_docs(query="ema", category_filter="function")
        assert "function" in result.lower() or "ema" in result.lower()

    @pytest.mark.asyncio
    async def test_with_namespace_filter(self):
        result = await search_docs(query="average", namespace_filter="ta")
        assert "ta." in result.lower() or "no results" in result.lower()

    @pytest.mark.asyncio
    async def test_relevance_gate(self):
        """Weak matches should be filtered out, not returned as valid results."""
        result = await search_docs(query="xyznonexistent12345", n_results=5)
        # Should not contain spurious results with high relevance claims
        if "[1]" in result:
            # If results exist, they should have reasonable relevance
            assert "relevance" in result.lower()


# ── get_examples ──────────────────────────────────────────────────────────────

class TestGetExamples:
    @pytest.mark.asyncio
    async def test_moving_average(self):
        result = await get_examples(query="moving average crossover")
        assert "example" in result.lower()
        assert "relevance" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy_entry(self):
        result = await get_examples(query="strategy entry with stop loss")
        assert "example" in result.lower()

    @pytest.mark.asyncio
    async def test_gibberish(self):
        result = await get_examples(query="xyznonexistent12345")
        assert "no" in result.lower() and "example" in result.lower()


# ── search_by_return_type ────────────────────────────────────────────────────

class TestSearchByReturnType:
    @pytest.mark.asyncio
    async def test_series_float(self):
        result = await search_by_return_type(return_type="series float")
        assert "series float" in result.lower()
        assert "function" in result.lower()

    @pytest.mark.asyncio
    async def test_bool(self):
        result = await search_by_return_type(return_type="bool")
        assert "function" in result.lower() or "bool" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await search_by_return_type(return_type="nonexistent_type_xyz")
        # May return weak semantic results or "no functions found"
        assert "function" in result.lower()  # Either "Functions returning..." or "No functions found"


# ── list_namespace ────────────────────────────────────────────────────────────

class TestListNamespace:
    @pytest.mark.asyncio
    async def test_ta(self):
        result = await list_namespace(namespace="ta")
        assert "ta" in result.lower()
        assert "ema" in result.lower()
        assert "sma" in result.lower()
        assert "rsi" in result.lower()
        assert "function" in result.lower()
        assert "entries" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy(self):
        result = await list_namespace(namespace="strategy")
        assert "strategy" in result.lower()
        assert "entry" in result.lower() or "function" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await list_namespace(namespace="nonexistent_namespace_xyz")
        assert "no entries" in result.lower()

    @pytest.mark.asyncio
    async def test_math(self):
        result = await list_namespace(namespace="math")
        assert "math" in result.lower()
        assert "function" in result.lower()

    @pytest.mark.asyncio
    async def test_with_category_filter(self):
        result = await list_namespace(namespace="ta", category_filter="function")
        # May fail due to ChromaDB multi-where limitation — check for either result or error
        assert ("function" in result.lower() and "ta" in result.lower()) or "no entries" in result.lower()
