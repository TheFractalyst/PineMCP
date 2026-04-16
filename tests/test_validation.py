"""
test_validation.py — Tests for the 5 validation tools:
  validate_syntax, validate_and_explain, fix_and_validate,
  debug_pine_facade, validate_file

Uses pine-facade (TradingView's remote compiler) with content-hash caching.
Tests that call the remote compiler mock call_pine_facade to avoid
network dependencies in CI.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from fastmcp.exceptions import ToolError
from tools.validation import (
    debug_pine_facade,
    fix_and_validate,
    validate_and_explain,
    validate_file,
    validate_syntax,
)

# ── Shared mock responses for call_pine_facade ─────────────────────────────────

_FACADE_SUCCESS = {
    "success": True,
    "errors": [],
    "warnings": [],
    "meta": {"name": "test"},
    "raw_response": {"success": True},
}

_FACADE_ERRORS = {
    "success": False,
    "errors": [{"line": 1, "column": 1, "text": "Cannot call 'ema'", "type": "error"}],
    "warnings": [],
    "meta": {},
    "raw_response": {},
}

_FACADE_DEBUG = {
    "success": True,
    "errors": [],
    "warnings": [],
    "meta": {},
    "raw_response": {"result": {}, "success": True},
}

_FACADE_WARNINGS = {
    "success": True,
    "errors": [],
    "warnings": [{"line": 1, "column": 1, "text": "Unused variable 'x'", "type": "warning"}],
    "meta": {},
    "raw_response": {},
}


# ── validate_syntax ───────────────────────────────────────────────────────────


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestValidateSyntax:
    async def test_valid_code(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await validate_syntax(code=valid_pine_code)
        assert "valid" in result.lower() or "compiles" in result.lower()
        assert "compiler" in result.lower()
        assert "error" in result.lower()  # "Errors: 0"

    async def test_invalid_code(self, mock_facade, invalid_pine_code):
        mock_facade.return_value = _FACADE_ERRORS
        result = await validate_syntax(code=invalid_pine_code)
        assert "error" in result.lower() or "issue" in result.lower()

    async def test_empty_code(self, mock_facade):
        result = await validate_syntax(code="")
        assert "error" in result.lower() or "no code" in result.lower()

    async def test_whitespace_only(self, mock_facade):
        result = await validate_syntax(code="   \n\t  ")
        assert "error" in result.lower() or "no code" in result.lower()

    async def test_analysis_on_valid(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await validate_syntax(code=valid_pine_code)
        assert "analysis" in result.lower() or "script type" in result.lower()

    async def test_warnings_shown(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_WARNINGS
        result = await validate_syntax(code=valid_pine_code)
        assert "warning" in result.lower()


# ── validate_and_explain ─────────────────────────────────────────────────────


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestValidateAndExplain:
    async def test_valid_code(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await validate_and_explain(code=valid_pine_code)
        assert "passed" in result.lower() or "valid" in result.lower()
        assert "compiler" in result.lower()

    async def test_invalid_with_doc_lookup(self, mock_facade):
        mock_facade.return_value = _FACADE_ERRORS
        code = "//@version=6\nindicator('test')\nema(close, 14)"
        result = await validate_and_explain(code=code)
        assert "error" in result.lower() or "report" in result.lower()

    async def test_empty_code(self, mock_facade):
        result = await validate_and_explain(code="")
        assert "error" in result.lower() or "no code" in result.lower()


# ── fix_and_validate ──────────────────────────────────────────────────────────


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestFixAndValidate:
    async def test_ema_namespace_fix(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await fix_and_validate(
            code="ema(close, 14)",
            error_description="Undeclared identifier 'ema'"
        )
        assert "fix" in result.lower()
        assert "hint" in result.lower()
        assert "ta.ema" in result

    async def test_empty_code(self, mock_facade):
        result = await fix_and_validate(code="", error_description="test")
        assert "error" in result.lower()

    async def test_empty_error_description(self, mock_facade):
        result = await fix_and_validate(code="test", error_description="")
        assert "error" in result.lower()

    async def test_transp_removal(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await fix_and_validate(
            code='//@version=6\nindicator("test")\nplot(close, color=color.red, transp=50)',
            error_description="transp parameter not supported"
        )
        assert "transp" in result.lower() or "fix" in result.lower()

    async def test_produces_fixed_code(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await fix_and_validate(
            code="sma(close, 20)",
            error_description="Undeclared identifier 'sma'"
        )
        assert "ta.sma" in result
        assert "FIXED CODE" in result or "fixed code" in result.lower()


# ── debug_pine_facade ─────────────────────────────────────────────────────────


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestDebugPineFacade:
    async def test_valid_code(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_DEBUG
        result = await debug_pine_facade(code=valid_pine_code)
        assert "debug" in result.lower()
        assert "circuit breaker" in result.lower()
        assert "normalized" in result.lower()

    async def test_empty_code(self, mock_facade):
        result = await debug_pine_facade(code="")
        assert "error" in result.lower() or "no code" in result.lower()

    async def test_circuit_breaker_stats(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_DEBUG
        result = await debug_pine_facade(code=valid_pine_code)
        assert "circuit_open" in result.lower() or "network_failures" in result.lower()


# ── validate_file ─────────────────────────────────────────────────────────────

# validate_file tests for path validation don't hit the network,
# so they don't need mocking. Tests that compile file content are
# skipped if the example file doesn't exist.


class TestValidateFile:
    @pytest.mark.asyncio
    async def test_valid_file(self, example_file_path):
        if not os.path.exists(example_file_path):
            pytest.skip("example_strategy.ps not found")
        result = await validate_file(file_path=example_file_path)
        assert "file" in result.lower()
        assert "compiler" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_path(self):
        with pytest.raises(ToolError, match="No file path"):
            await validate_file(file_path="")

    @pytest.mark.asyncio
    async def test_nonexistent_path(self):
        with pytest.raises(ToolError, match="Access denied"):
            await validate_file(file_path="/nonexistent/path/test.ps")

    @pytest.mark.asyncio
    async def test_system_file_rejected(self):
        with pytest.raises(ToolError, match=r"Access denied|\.ps"):
            await validate_file(file_path="/etc/passwd")

    @pytest.mark.asyncio
    async def test_non_ps_extension_rejected_without_pinescript_content(self):
        """Non-.ps file without PineScript content should be rejected."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_notpine.txt")
        try:
            with open(test_file, "w") as f:
                f.write("this is not pine script code\njust regular text\n")
            with pytest.raises(ToolError, match="does not appear to be PineScript"):
                await validate_file(file_path=test_file)
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_txt_with_pinescript_content_accepted(self):
        """Non-.ps file WITH PineScript content should be accepted."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_pine.txt")
        try:
            with open(test_file, "w") as f:
                f.write('//@version=6\nindicator("test")\nplot(close)\n')
            result = await validate_file(file_path=test_file)
            assert result is not None
            assert "does not appear to be PineScript" not in result
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_txt_with_strategy_declaration_accepted(self):
        """Non-.ps file with strategy() declaration should be accepted."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_strategy.txt")
        try:
            with open(test_file, "w") as f:
                f.write('//@version=6\nstrategy("test", overlay=true)\nplot(close)\n')
            result = await validate_file(file_path=test_file)
            assert result is not None
            assert "does not appear to be PineScript" not in result
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_txt_with_library_declaration_accepted(self):
        """Non-.ps file with library() declaration should be accepted."""
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_lib.txt")
        try:
            with open(test_file, "w") as f:
                f.write('//@version=6\nlibrary("MyLib")\nexport myFunc() => close\n')
            result = await validate_file(file_path=test_file)
            assert result is not None
            assert "does not appear to be PineScript" not in result
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_no_path_leakage(self):
        with pytest.raises(ToolError, match="Access denied") as exc_info:
            await validate_file(file_path="/nonexistent/path/test.ps")
        # Should NOT contain full absolute paths in error message
        assert "/nonexistent/path/test.ps" not in str(exc_info.value)
