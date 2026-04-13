"""
test_codegen.py — Tests for the 3 codegen tools:
  generate_indicator, generate_strategy, lookup_and_correct

Each tool generates PineScript code and validates it.
"""

import pytest

from tools.codegen import (
    generate_indicator,
    generate_strategy,
    lookup_and_correct,
)

# ── generate_indicator ────────────────────────────────────────────────────────

class TestGenerateIndicator:
    @pytest.mark.asyncio
    async def test_rsi(self):
        result = await generate_indicator(
            name="RSI Test",
            description="relative strength index"
        )
        assert "//@version=6" in result
        assert "indicator(" in result
        assert "rsi" in result.lower()
        assert "validation" in result.lower()

    @pytest.mark.asyncio
    async def test_ema(self):
        result = await generate_indicator(
            name="EMA Test",
            description="exponential moving average",
            inputs="length=20,src=close",
            overlay=True
        )
        assert "//@version=6" in result
        assert "ta.ema" in result or "ema" in result.lower()

    @pytest.mark.asyncio
    async def test_with_inputs(self):
        result = await generate_indicator(
            name="Custom",
            description="custom indicator",
            inputs="length=14,src=close,mult=2.0"
        )
        assert "input." in result

    @pytest.mark.asyncio
    async def test_empty_name(self):
        result = await generate_indicator(name="")
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_bollinger_template(self):
        result = await generate_indicator(
            name="BB Test",
            description="bollinger bands"
        )
        assert "//@version=6" in result
        assert "ta.bb" in result or "bollinger" in result.lower()

    @pytest.mark.asyncio
    async def test_macd_template(self):
        result = await generate_indicator(
            name="MACD Test",
            description="MACD indicator"
        )
        assert "//@version=6" in result
        assert "ta.macd" in result or "macd" in result.lower()


# ── generate_strategy ─────────────────────────────────────────────────────────

class TestGenerateStrategy:
    @pytest.mark.asyncio
    async def test_basic(self):
        result = await generate_strategy(
            name="MA Cross Test",
            description="moving average crossover"
        )
        assert "//@version=6" in result
        assert "strategy(" in result
        assert "ta.ema" in result or "moving" in result.lower()
        assert "validated" in result.lower()

    @pytest.mark.asyncio
    async def test_custom_params(self):
        result = await generate_strategy(
            name="Custom Strategy",
            description="test strategy",
            initial_capital=5000,
            commission_pct=0.05,
            pyramiding=2
        )
        assert "5000" in result
        assert "strategy(" in result

    @pytest.mark.asyncio
    async def test_empty_name(self):
        result = await generate_strategy(name="")
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_has_entry_exit(self):
        result = await generate_strategy(
            name="Entry Exit Test",
            description="test"
        )
        assert "strategy.entry" in result
        assert "strategy.exit" in result or "strategy.close" in result


# ── lookup_and_correct ────────────────────────────────────────────────────────

class TestLookupAndCorrect:
    @pytest.mark.asyncio
    async def test_ema_fix(self):
        result = await lookup_and_correct(
            code="ema(close, 14)",
            error_description="calculate EMA"
        )
        assert "lookup" in result.lower() or "correct" in result.lower()
        assert "ta.ema" in result
        assert "before" in result.lower()
        assert "after" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await lookup_and_correct(code="", error_description="test")
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_description(self):
        result = await lookup_and_correct(code="test", error_description="")
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_v5_to_v6_migration(self):
        result = await lookup_and_correct(
            code="sma(close, 20)\nstudy('test')",
            error_description="calculate SMA"
        )
        assert "ta.sma" in result
        assert "indicator(" in result  # study→indicator fix

    @pytest.mark.asyncio
    async def test_produces_report(self):
        result = await lookup_and_correct(
            code="ema(close, 14)",
            error_description="calculate EMA"
        )
        assert "REPORT" in result.upper()
        assert "FIXES" in result.upper()
