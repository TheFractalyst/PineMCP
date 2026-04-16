"""
test_codegen.py — Tests for the 3 codegen tools:
  generate_indicator, generate_strategy, lookup_and_correct

Each tool generates PineScript code and validates it.
Tests mock call_pine_facade to avoid network dependencies in CI.
"""

from unittest.mock import AsyncMock, patch

import pytest

from fastmcp.exceptions import ToolError
from tools.codegen import (
    generate_indicator,
    generate_strategy,
    lookup_and_correct,
)

# ── Shared mock for call_pine_facade ──────────────────────────────────────────

_FACADE_SUCCESS = {
    "success": True,
    "errors": [],
    "warnings": [],
    "meta": {},
    "raw_response": {"success": True},
}

_FACADE_ERRORS = {
    "success": False,
    "errors": [{"line": 1, "column": 1, "text": "Cannot call 'ema'", "type": "error"}],
    "warnings": [],
    "meta": {},
    "raw_response": {},
}


# ── generate_indicator ────────────────────────────────────────────────────────


@patch("tools.codegen.call_pine_facade", new_callable=AsyncMock)
class TestGenerateIndicator:
    async def test_rsi(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_indicator(
            name="RSI Test",
            description="relative strength index"
        )
        assert "//@version=6" in result
        assert "indicator(" in result
        assert "rsi" in result.lower()
        assert "validation" in result.lower()

    async def test_ema(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_indicator(
            name="EMA Test",
            description="exponential moving average",
            inputs="length=20,src=close",
            overlay=True
        )
        assert "//@version=6" in result
        assert "ta.ema" in result or "ema" in result.lower()

    async def test_with_inputs(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_indicator(
            name="Custom",
            description="custom indicator",
            inputs="length=14,src=close,mult=2.0"
        )
        assert "input." in result

    async def test_empty_name(self, mock_facade):
        with pytest.raises(ToolError, match="No indicator name"):
            await generate_indicator(name="")

    async def test_bollinger_template(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_indicator(
            name="BB Test",
            description="bollinger bands"
        )
        assert "//@version=6" in result
        assert "ta.bb" in result or "bollinger" in result.lower()

    async def test_macd_template(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_indicator(
            name="MACD Test",
            description="MACD indicator"
        )
        assert "//@version=6" in result
        assert "ta.macd" in result or "macd" in result.lower()


# ── generate_strategy ─────────────────────────────────────────────────────────


@patch("tools.codegen.call_pine_facade", new_callable=AsyncMock)
class TestGenerateStrategy:
    async def test_basic(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_strategy(
            name="MA Cross Test",
            description="moving average crossover"
        )
        assert "//@version=6" in result
        assert "strategy(" in result
        assert "ta.ema" in result or "moving" in result.lower()
        assert "validated" in result.lower()

    async def test_custom_params(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_strategy(
            name="Custom Strategy",
            description="test strategy",
            initial_capital=5000,
            commission_pct=0.05,
            pyramiding=2
        )
        assert "5000" in result
        assert "strategy(" in result

    async def test_empty_name(self, mock_facade):
        with pytest.raises(ToolError, match="No strategy name"):
            await generate_strategy(name="")

    async def test_has_entry_exit(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await generate_strategy(
            name="Entry Exit Test",
            description="test"
        )
        assert "strategy.entry" in result
        assert "strategy.exit" in result or "strategy.close" in result


# ── lookup_and_correct ────────────────────────────────────────────────────────


@patch("tools.codegen.call_pine_facade", new_callable=AsyncMock)
class TestLookupAndCorrect:
    async def test_ema_fix(self, mock_facade):
        # First call returns errors (before fix), second returns success (after fix)
        mock_facade.side_effect = [_FACADE_ERRORS, _FACADE_SUCCESS]
        result = await lookup_and_correct(
            code="ema(close, 14)",
            error_description="calculate EMA"
        )
        assert "lookup" in result.lower() or "correct" in result.lower()
        assert "ta.ema" in result
        assert "before" in result.lower()
        assert "after" in result.lower()

    async def test_empty_code(self, mock_facade):
        with pytest.raises(ToolError, match="No code provided"):
            await lookup_and_correct(code="", error_description="test")

    async def test_empty_description(self, mock_facade):
        with pytest.raises(ToolError, match="No description provided"):
            await lookup_and_correct(code="test", error_description="")

    async def test_v5_to_v6_migration(self, mock_facade):
        mock_facade.side_effect = [_FACADE_ERRORS, _FACADE_SUCCESS]
        result = await lookup_and_correct(
            code="sma(close, 20)\nstudy('test')",
            error_description="calculate SMA"
        )
        assert "ta.sma" in result
        assert "indicator(" in result  # study→indicator fix

    async def test_produces_report(self, mock_facade):
        mock_facade.side_effect = [_FACADE_ERRORS, _FACADE_SUCCESS]
        result = await lookup_and_correct(
            code="ema(close, 14)",
            error_description="calculate EMA"
        )
        assert "REPORT" in result.upper()
        assert "FIXES" in result.upper()
