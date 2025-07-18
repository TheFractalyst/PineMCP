"""
test_codegen.py - Tests for pine_scaffold.

pine_scaffold absorbs generate_indicator (kind="indicator") and
generate_strategy (kind="strategy"). Mocks the remote compiler.
"""

from unittest.mock import AsyncMock, patch

import pytest

from fastmcp.exceptions import ToolError
from tools.codegen import pine_scaffold


_FACADE_SUCCESS = {
    "success": True,
    "errors": [],
    "warnings": [],
    "meta": {},
    "raw_response": {"success": True},
}


# -- kind="indicator" --------------------------------------------------------


@patch("tools.codegen.call_pine_facade", new_callable=AsyncMock)
class TestScaffoldIndicator:
    async def test_rsi(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="indicator",
            name="RSI Test",
            description="relative strength index",
        )
        assert "//@version=6" in result
        assert "indicator(" in result
        assert "rsi" in result.lower()
        assert "validation" in result.lower()

    async def test_ema_with_inputs(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="indicator",
            name="EMA Test",
            description="exponential moving average",
            inputs="length=20,src=close",
            overlay=True,
        )
        assert "//@version=6" in result
        assert "ta.ema" in result or "ema" in result.lower()

    async def test_with_inputs(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="indicator",
            name="Custom",
            description="custom indicator",
            inputs="length=14,src=close,mult=2.0",
        )
        assert "input." in result

    async def test_empty_name(self, mock_facade):
        with pytest.raises(ToolError, match="No name"):
            await pine_scaffold(kind="indicator", name="")

    async def test_bollinger_template(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="indicator",
            name="BB Test",
            description="bollinger bands",
        )
        assert "//@version=6" in result
        assert "ta.bb" in result or "bollinger" in result.lower()

    async def test_macd_template(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="indicator",
            name="MACD Test",
            description="MACD indicator",
        )
        assert "//@version=6" in result
        assert "ta.macd" in result or "macd" in result.lower()


# -- kind="strategy" ---------------------------------------------------------


@patch("tools.codegen.call_pine_facade", new_callable=AsyncMock)
class TestScaffoldStrategy:
    async def test_basic(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="strategy",
            name="MA Cross Test",
            description="moving average crossover",
        )
        assert "//@version=6" in result
        assert "strategy(" in result
        assert "ta.ema" in result or "moving" in result.lower()
        assert "validated" in result.lower()

    async def test_custom_params(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="strategy",
            name="Custom Strategy",
            description="test strategy",
            initial_capital=5000,
            commission_pct=0.05,
            pyramiding=2,
        )
        assert "5000" in result
        assert "strategy(" in result

    async def test_empty_name(self, mock_facade):
        with pytest.raises(ToolError, match="No name"):
            await pine_scaffold(kind="strategy", name="")

    async def test_has_entry_exit(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_scaffold(
            kind="strategy",
            name="Entry Exit Test",
            description="test",
        )
        assert "strategy.entry" in result
        assert "strategy.exit" in result or "strategy.close" in result
