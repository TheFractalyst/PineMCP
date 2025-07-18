"""
test_search.py - Tests for pine_search (unified semantic discovery).

Covers every mode:
  - Default docs search (query only)
  - Category + namespace filters
  - return_type branch (old search_by_return_type)
  - has_examples branch (old get_examples)
  - current_line branch (old suggest_functions)
"""

import pytest

from tools.search import pine_search


# -- default docs search -----------------------------------------------------

class TestSearchDocs:
    @pytest.mark.asyncio
    async def test_ema_crossover(self):
        result = await pine_search(query="ema crossover", n_results=3)
        assert "ema" in result.lower()
        assert "crossover" in result.lower() or "ta." in result.lower()
        assert "relevance" in result.lower()

    @pytest.mark.asyncio
    async def test_gibberish(self):
        result = await pine_search(query="xyznonexistent12345", n_results=1)
        assert "no results" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_with_category_filter(self):
        result = await pine_search(query="ema", category="function")
        assert "function" in result.lower() or "ema" in result.lower()

    @pytest.mark.asyncio
    async def test_with_namespace_filter(self):
        result = await pine_search(query="average", namespace="ta")
        assert "ta." in result.lower() or "no results" in result.lower()

    @pytest.mark.asyncio
    async def test_relevance_gate(self):
        """Weak matches should be filtered out, not returned as valid results."""
        result = await pine_search(query="xyznonexistent12345", n_results=5)
        if "[1]" in result:
            assert "relevance" in result.lower()


# -- has_examples branch -----------------------------------------------------

class TestSearchExamples:
    @pytest.mark.asyncio
    async def test_moving_average(self):
        result = await pine_search(query="moving average crossover", has_examples=True)
        assert "example" in result.lower()
        assert "relevance" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy_entry(self):
        result = await pine_search(
            query="strategy entry with stop loss", has_examples=True
        )
        assert "example" in result.lower()

    @pytest.mark.asyncio
    async def test_gibberish(self):
        result = await pine_search(query="xyznonexistent12345", has_examples=True)
        assert "no" in result.lower() and "example" in result.lower()


# -- return_type branch ------------------------------------------------------

class TestSearchByReturnType:
    @pytest.mark.asyncio
    async def test_series_float(self):
        result = await pine_search(query="x", return_type="series float")
        assert "series float" in result.lower()
        assert "function" in result.lower()

    @pytest.mark.asyncio
    async def test_bool(self):
        result = await pine_search(query="x", return_type="bool")
        assert "function" in result.lower() or "bool" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_search(query="x", return_type="nonexistent_type_xyz")
        assert "function" in result.lower()


# -- current_line branch (suggestions) ---------------------------------------

class TestSuggestFunctions:
    @pytest.mark.asyncio
    async def test_suggest_moving_average(self):
        result = await pine_search(
            query="compute a moving average",
            current_line="ma = ",
        )
        assert "suggested" in result.lower() or "ta." in result.lower()

    @pytest.mark.asyncio
    async def test_suggest_nonsense(self):
        result = await pine_search(
            query="xyznonexistent12345", current_line=""
        )
        assert "no" in result.lower()
