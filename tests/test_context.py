"""
test_context.py — Tests for the 2 context tools:
  suggest_functions, get_namespace_cheatsheet
"""

import pytest

from tools.context import (
    get_namespace_cheatsheet,
    suggest_functions,
)

# ── suggest_functions ─────────────────────────────────────────────────────────

class TestSuggestFunctions:
    @pytest.mark.asyncio
    async def test_moving_average(self):
        result = await suggest_functions(context="calculate moving average")
        assert "moving" in result.lower() or "average" in result.lower()
        assert "suggested" in result.lower()
        assert "syntax" in result.lower()

    @pytest.mark.asyncio
    async def test_with_current_line(self):
        result = await suggest_functions(
            context="draw a line on chart",
            current_line="line.new("
        )
        assert "line" in result.lower()

    @pytest.mark.asyncio
    async def test_gibberish(self):
        result = await suggest_functions(context="xyznonexistent12345")
        assert "no" in result.lower() or "function" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy_context(self):
        result = await suggest_functions(context="enter a long position")
        assert "strategy" in result.lower() or "entry" in result.lower()


# ── get_namespace_cheatsheet ──────────────────────────────────────────────────

class TestGetNamespaceCheatsheet:
    @pytest.mark.asyncio
    async def test_math(self):
        result = await get_namespace_cheatsheet(namespace="math")
        assert "math" in result.lower()
        assert "cheatsheet" in result.lower()
        assert "function" in result.lower()
        assert "entries" in result.lower()

    @pytest.mark.asyncio
    async def test_ta(self):
        result = await get_namespace_cheatsheet(namespace="ta")
        assert "ta" in result.lower()
        assert len(result) > 500

    @pytest.mark.asyncio
    async def test_strategy(self):
        result = await get_namespace_cheatsheet(namespace="strategy")
        assert "strategy" in result.lower()
        assert "function" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await get_namespace_cheatsheet(namespace="nonexistent_namespace_xyz")
        assert "no entries" in result.lower()

    @pytest.mark.asyncio
    async def test_box_drawing(self):
        """Cheatsheet should use box-drawing characters."""
        result = await get_namespace_cheatsheet(namespace="math")
        assert "╔" in result or "║" in result or "╚" in result
