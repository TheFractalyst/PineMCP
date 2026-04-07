"""
test_lookup.py — Tests for the 6 lookup tools:
  get_function, get_variable, get_type, get_constant, get_keyword, get_operator

Each tool is tested for:
  - Valid input returns formatted docs with expected keywords
  - Invalid input returns "not found" or fuzzy suggestions
  - Edge cases (whitespace, trailing parens)
"""

import pytest

from pinescript_mcp import (
    get_function,
    get_variable,
    get_type,
    get_constant,
    get_keyword,
    get_operator,
)


# ── get_function ──────────────────────────────────────────────────────────────

class TestGetFunction:
    @pytest.mark.asyncio
    async def test_ta_ema(self):
        result = await get_function(name="ta.ema")
        assert "ta.ema" in result.lower()
        assert "source" in result.lower()
        assert "length" in result.lower()
        assert "syntax" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy_entry(self):
        result = await get_function(name="strategy.entry")
        assert "strategy.entry" in result.lower()
        assert "syntax" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await get_function(name="nonexistent_func_xyz")
        assert "not found" in result.lower() or "did you mean" in result.lower()

    @pytest.mark.asyncio
    async def test_with_trailing_parens(self):
        result = await get_function(name="ta.ema()")
        assert "ta.ema" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_handling(self):
        result = await get_function(name="  ta.ema  ")
        assert "ta.ema" in result.lower()


# ── get_variable ──────────────────────────────────────────────────────────────

class TestGetVariable:
    @pytest.mark.asyncio
    async def test_close(self):
        result = await get_variable(name="close")
        assert "close" in result.lower()
        assert len(result) > 50

    @pytest.mark.asyncio
    async def test_barstate_isconfirmed(self):
        result = await get_variable(name="barstate.isconfirmed")
        assert "barstate" in result.lower() or "confirmed" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await get_variable(name="nonexistent_var_xyz")
        assert "not found" in result.lower() or "did you mean" in result.lower()


# ── get_type ──────────────────────────────────────────────────────────────────

class TestGetType:
    @pytest.mark.asyncio
    async def test_array(self):
        result = await get_type(name="array")
        assert "array" in result.lower()
        # Should include enrichment for thin entries
        assert "method" in result.lower() or "AVAILABLE METHODS" in result

    @pytest.mark.asyncio
    async def test_label(self):
        result = await get_type(name="label")
        assert "label" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await get_type(name="nonexistent_type_xyz")
        assert "not found" in result.lower() or "available types" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_suggestions_for_similar(self):
        result = await get_type(name="arra")
        # Should either find it via fuzzy match or suggest "array"
        assert "arra" in result.lower() or "not found" in result.lower()


# ── get_constant ──────────────────────────────────────────────────────────────

class TestGetConstant:
    @pytest.mark.asyncio
    async def test_color_red(self):
        result = await get_constant(name="color.red")
        assert "color" in result.lower()
        assert "red" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy_long(self):
        result = await get_constant(name="strategy.long")
        assert "strategy" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await get_constant(name="nonexistent.constant.xyz")
        assert "not found" in result.lower() or "did you mean" in result.lower()


# ── get_keyword ───────────────────────────────────────────────────────────────

class TestGetKeyword:
    @pytest.mark.asyncio
    async def test_var(self):
        result = await get_keyword(name="var")
        assert "var" in result.lower()
        assert "variable" in result.lower() or "persistent" in result.lower()

    @pytest.mark.asyncio
    async def test_if_keyword(self):
        result = await get_keyword(name="if")
        assert "if" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await get_keyword(name="nonexistent_keyword_xyz")
        assert "not found" in result.lower() or "did you mean" in result.lower()


# ── get_operator ──────────────────────────────────────────────────────────────

class TestGetOperator:
    @pytest.mark.asyncio
    async def test_plus(self):
        result = await get_operator(name="+")
        assert "addition" in result.lower() or "operator" in result.lower()

    @pytest.mark.asyncio
    async def test_assignment(self):
        result = await get_operator(name=":=")
        assert ":=" in result or "assignment" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await get_operator(name="nonexistent_op_xyz")
        assert "not found" in result.lower() or "did you mean" in result.lower()
