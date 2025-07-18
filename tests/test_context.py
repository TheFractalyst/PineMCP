"""
test_context.py - Tests for pine_browse (namespace enumeration).

Covers style="list" (absorbs list_namespace) and style="cheatsheet"
(absorbs get_namespace_cheatsheet).
"""

import pytest

from tools.context import pine_browse


# -- style="list" ------------------------------------------------------------

class TestBrowseList:
    @pytest.mark.asyncio
    async def test_ta(self):
        result = await pine_browse(namespace="ta")
        assert "ta" in result.lower()
        assert "ema" in result.lower()
        assert "sma" in result.lower()
        assert "rsi" in result.lower()
        assert "function" in result.lower()
        assert "entries" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy(self):
        result = await pine_browse(namespace="strategy")
        assert "strategy" in result.lower()
        assert "entry" in result.lower() or "function" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_browse(namespace="nonexistent_namespace_xyz")
        assert "no entries" in result.lower()

    @pytest.mark.asyncio
    async def test_math(self):
        result = await pine_browse(namespace="math")
        assert "math" in result.lower()
        assert "function" in result.lower()

    @pytest.mark.asyncio
    async def test_with_category_filter(self):
        result = await pine_browse(namespace="ta", category="function")
        assert (
            ("function" in result.lower() and "ta" in result.lower())
            or "no entries" in result.lower()
        )


# -- style="cheatsheet" ------------------------------------------------------

class TestBrowseCheatsheet:
    @pytest.mark.asyncio
    async def test_math(self):
        result = await pine_browse(namespace="math", style="cheatsheet")
        assert "math" in result.lower()
        assert "cheatsheet" in result.lower()
        assert "function" in result.lower()
        assert "entries" in result.lower()

    @pytest.mark.asyncio
    async def test_ta(self):
        result = await pine_browse(namespace="ta", style="cheatsheet")
        assert "ta" in result.lower()
        assert len(result) > 500

    @pytest.mark.asyncio
    async def test_strategy(self):
        result = await pine_browse(namespace="strategy", style="cheatsheet")
        assert "strategy" in result.lower()
        assert "function" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_browse(
            namespace="nonexistent_namespace_xyz", style="cheatsheet"
        )
        assert "no entries" in result.lower()

    @pytest.mark.asyncio
    async def test_cheatsheet_format(self):
        """Cheatsheet should produce compact signature summary."""
        result = await pine_browse(namespace="math", style="cheatsheet")
        assert "CHEATSHEET" in result
        assert "FUNCTIONS" in result
        assert "->" in result
        assert "Total:" in result
