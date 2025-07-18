"""
test_lookup.py - Tests for pine_lookup (unified lookup tool).

Covers every kind: function, variable, type, constant, keyword, operator.
Each kind is tested for:
  - Valid input returns formatted docs with expected keywords.
  - Invalid input returns "not found" or fuzzy suggestions.
  - Edge cases (whitespace, trailing parens, kind=None auto-detect).
"""

import pytest

from tools.lookup import pine_lookup


# -- function kind ------------------------------------------------------------

class TestLookupFunction:
    @pytest.mark.asyncio
    async def test_ta_ema(self):
        result = await pine_lookup(name="ta.ema", kind="function")
        assert "ta.ema" in result.lower()
        assert "source" in result.lower()
        assert "length" in result.lower()
        assert "syntax" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy_entry(self):
        result = await pine_lookup(name="strategy.entry", kind="function")
        assert "strategy.entry" in result.lower()
        assert "syntax" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_lookup(name="nonexistent_func_xyz", kind="function")
        assert "not found" in result.lower() or "did you mean" in result.lower()

    @pytest.mark.asyncio
    async def test_with_trailing_parens(self):
        result = await pine_lookup(name="ta.ema()", kind="function")
        assert "ta.ema" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_handling(self):
        result = await pine_lookup(name="  ta.ema  ", kind="function")
        assert "ta.ema" in result.lower()

    @pytest.mark.asyncio
    async def test_kind_autodetect(self):
        """When kind is omitted, it should still resolve a known function."""
        result = await pine_lookup(name="ta.ema")
        assert "ta.ema" in result.lower()


# -- variable kind ------------------------------------------------------------

class TestLookupVariable:
    @pytest.mark.asyncio
    async def test_close(self):
        result = await pine_lookup(name="close", kind="variable")
        assert "close" in result.lower()
        assert len(result) > 50

    @pytest.mark.asyncio
    async def test_barstate_isconfirmed(self):
        result = await pine_lookup(name="barstate.isconfirmed", kind="variable")
        assert "barstate" in result.lower() or "confirmed" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_lookup(name="nonexistent_var_xyz", kind="variable")
        assert "not found" in result.lower() or "did you mean" in result.lower()


# -- type kind ----------------------------------------------------------------

class TestLookupType:
    @pytest.mark.asyncio
    async def test_array(self):
        result = await pine_lookup(name="array", kind="type")
        assert "array" in result.lower()
        assert "type" in result.lower()

    @pytest.mark.asyncio
    async def test_label(self):
        result = await pine_lookup(name="label", kind="type")
        assert "label" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_lookup(name="nonexistent_type_xyz", kind="type")
        assert "not found" in result.lower() or "available types" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_suggestions_for_similar(self):
        result = await pine_lookup(name="arra", kind="type")
        assert "arra" in result.lower() or "not found" in result.lower()


# -- constant kind ------------------------------------------------------------

class TestLookupConstant:
    @pytest.mark.asyncio
    async def test_color_red(self):
        result = await pine_lookup(name="color.red", kind="constant")
        assert "color" in result.lower()
        assert "red" in result.lower()

    @pytest.mark.asyncio
    async def test_strategy_long(self):
        result = await pine_lookup(name="strategy.long", kind="constant")
        assert "strategy" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_lookup(name="nonexistent.constant.xyz", kind="constant")
        assert "not found" in result.lower() or "did you mean" in result.lower()


# -- keyword kind -------------------------------------------------------------

class TestLookupKeyword:
    @pytest.mark.asyncio
    async def test_var(self):
        result = await pine_lookup(name="var", kind="keyword")
        assert "var" in result.lower()
        assert "variable" in result.lower() or "persistent" in result.lower()

    @pytest.mark.asyncio
    async def test_if_keyword(self):
        result = await pine_lookup(name="if", kind="keyword")
        assert "if" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_lookup(name="nonexistent_keyword_xyz", kind="keyword")
        assert "not found" in result.lower() or "did you mean" in result.lower()


# -- operator kind ------------------------------------------------------------

class TestLookupOperator:
    @pytest.mark.asyncio
    async def test_plus(self):
        result = await pine_lookup(name="+", kind="operator")
        assert "addition" in result.lower() or "operator" in result.lower()

    @pytest.mark.asyncio
    async def test_assignment(self):
        result = await pine_lookup(name=":=", kind="operator")
        assert ":=" in result or "assignment" in result.lower()

    @pytest.mark.asyncio
    async def test_nonexistent(self):
        result = await pine_lookup(name="nonexistent_op_xyz", kind="operator")
        assert "not found" in result.lower() or "did you mean" in result.lower()
