"""
test_validation.py - Tests for pine_compile and pine_repair.

pine_compile absorbs: validate_syntax, validate_and_explain, validate_file.
pine_repair  absorbs: fix_and_validate (mode="targeted"),
                      lookup_and_correct (mode="migrate").

Remote compiler calls are mocked to avoid network dependence.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from fastmcp.exceptions import ToolError
from tools.validation import pine_compile, pine_repair


# -- Shared mock compiler responses ------------------------------------------

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

_FACADE_WARNINGS = {
    "success": True,
    "errors": [],
    "warnings": [
        {"line": 1, "column": 1, "text": "Unused variable 'x'", "type": "warning"}
    ],
    "meta": {},
    "raw_response": {},
}


# -- pine_compile: inline code -----------------------------------------------


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestPineCompileCode:
    async def test_valid_code(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_compile(code=valid_pine_code)
        assert "valid" in result.lower() or "compiles" in result.lower()
        assert "compiler" in result.lower()
        assert "error" in result.lower()  # "Errors: 0"

    async def test_invalid_code(self, mock_facade, invalid_pine_code):
        mock_facade.return_value = _FACADE_ERRORS
        result = await pine_compile(code=invalid_pine_code)
        assert "error" in result.lower() or "issue" in result.lower()

    async def test_empty_code(self, mock_facade):
        result = await pine_compile(code="")
        assert "error" in result.lower() or "no code" in result.lower()

    async def test_whitespace_only(self, mock_facade):
        result = await pine_compile(code="   \n\t  ")
        assert "error" in result.lower() or "no code" in result.lower()

    async def test_analysis_on_valid(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_compile(code=valid_pine_code)
        assert "analysis" in result.lower() or "script type" in result.lower()

    async def test_warnings_shown(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_WARNINGS
        result = await pine_compile(code=valid_pine_code)
        assert "warning" in result.lower()


# -- pine_compile: explain=True ----------------------------------------------


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestPineCompileExplain:
    async def test_valid_code(self, mock_facade, valid_pine_code):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_compile(code=valid_pine_code, explain=True)
        assert "valid" in result.lower() or "compiles" in result.lower()
        assert "compiler" in result.lower()

    async def test_invalid_with_doc_lookup(self, mock_facade):
        mock_facade.return_value = _FACADE_ERRORS
        code = "//@version=6\nindicator('test')\nema(close, 14)"
        result = await pine_compile(code=code, explain=True)
        assert "error" in result.lower() or "report" in result.lower()


# -- pine_compile: file branch -----------------------------------------------


class TestPineCompileFile:
    @pytest.mark.asyncio
    async def test_empty_path_and_code(self):
        with pytest.raises(ToolError, match="No code or file_path|Pass"):
            await pine_compile()

    @pytest.mark.asyncio
    async def test_both_code_and_path(self):
        with pytest.raises(ToolError, match="exactly one"):
            await pine_compile(code="x", file_path="/tmp/x.ps")

    @pytest.mark.asyncio
    async def test_nonexistent_path(self):
        with pytest.raises(ToolError, match="outside allowed directories"):
            await pine_compile(file_path="/nonexistent/path/test.ps")

    @pytest.mark.asyncio
    async def test_system_file_rejected(self):
        with pytest.raises(ToolError, match="outside allowed directories"):
            await pine_compile(file_path="/etc/passwd")

    @pytest.mark.asyncio
    async def test_non_ps_extension_rejected_without_pinescript_content(self):
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_notpine.txt")
        try:
            with open(test_file, "w") as f:
                f.write("this is not pine script code\njust regular text\n")
            with pytest.raises(ToolError, match="does not appear to be PineScript"):
                await pine_compile(file_path=test_file)
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_txt_with_pinescript_content_accepted(self):
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_pine.txt")
        try:
            with open(test_file, "w") as f:
                f.write('//@version=6\nindicator("test")\nplot(close)\n')
            result = await pine_compile(file_path=test_file)
            assert result is not None
            assert "does not appear to be PineScript" not in result
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_txt_with_strategy_declaration_accepted(self):
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_strategy.txt")
        try:
            with open(test_file, "w") as f:
                f.write('//@version=6\nstrategy("test", overlay=true)\nplot(close)\n')
            result = await pine_compile(file_path=test_file)
            assert result is not None
            assert "does not appear to be PineScript" not in result
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_txt_with_library_declaration_accepted(self):
        test_dir = os.path.dirname(os.path.abspath(__file__))
        test_file = os.path.join(test_dir, "_test_temp_lib.txt")
        try:
            with open(test_file, "w") as f:
                f.write('//@version=6\nlibrary("MyLib")\nexport myFunc() => close\n')
            result = await pine_compile(file_path=test_file)
            assert result is not None
            assert "does not appear to be PineScript" not in result
        finally:
            os.unlink(test_file)

    @pytest.mark.asyncio
    async def test_no_path_leakage(self):
        with pytest.raises(ToolError, match="outside allowed directories") as exc_info:
            await pine_compile(file_path="/nonexistent/path/test.ps")
        # Error should NOT expose the resolved real path or internal dir list
        error_msg = str(exc_info.value)
        assert "/private/" not in error_msg

    @pytest.mark.asyncio
    async def test_valid_file(self, example_file_path):
        if not os.path.exists(example_file_path):
            pytest.skip("example_strategy.ps not found")
        result = await pine_compile(file_path=example_file_path)
        assert "file" in result.lower()
        assert "compiler" in result.lower()


# -- pine_repair: mode="targeted" --------------------------------------------


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestPineRepairTargeted:
    async def test_ema_namespace_fix(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_repair(
            code="ema(close, 14)",
            context="Undeclared identifier 'ema'",
        )
        assert "fix" in result.lower()
        assert "hint" in result.lower()
        assert "ta.ema" in result

    async def test_empty_code(self, mock_facade):
        with pytest.raises(ToolError):
            await pine_repair(code="", context="x")

    async def test_empty_context(self, mock_facade):
        with pytest.raises(ToolError):
            await pine_repair(code="x", context="")

    async def test_transp_removal(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_repair(
            code='//@version=6\nindicator("test")\nplot(close, color=color.red, transp=50)',
            context="transp parameter not supported",
        )
        assert "transp" in result.lower() or "fix" in result.lower()

    async def test_produces_fixed_code(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_repair(
            code="sma(close, 20)",
            context="Undeclared identifier 'sma'",
        )
        assert "ta.sma" in result
        assert "FIXED CODE" in result or "fixed code" in result.lower()


# -- pine_repair: mode="migrate" ---------------------------------------------


@patch("tools.validation.call_pine_facade", new_callable=AsyncMock)
class TestPineRepairMigrate:
    async def test_study_to_indicator(self, mock_facade):
        mock_facade.return_value = _FACADE_SUCCESS
        result = await pine_repair(
            code='//@version=4\nstudy("Legacy")\nplot(close)',
            context="modernize to v6",
            mode="migrate",
        )
        assert "migrate" in result.lower() or "namespace" in result.lower()

    async def test_empty_code(self, mock_facade):
        with pytest.raises(ToolError):
            await pine_repair(code="", context="x", mode="migrate")
