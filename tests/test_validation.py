"""
test_validation.py — Tests for the 5 validation tools:
  validate_syntax, validate_and_explain, fix_and_validate,
  debug_pine_facade, validate_file

Uses local linter / pine-facade with content-hash caching.
"""

import os

import pytest

from tools.validation import (
    validate_syntax,
    validate_and_explain,
    fix_and_validate,
    debug_pine_facade,
    validate_file,
)


# ── validate_syntax ───────────────────────────────────────────────────────────

class TestValidateSyntax:
    @pytest.mark.asyncio
    async def test_valid_code(self, valid_pine_code):
        result = await validate_syntax(code=valid_pine_code)
        assert "valid" in result.lower() or "compiles" in result.lower()
        assert "compiler" in result.lower()
        assert "error" in result.lower()  # "Errors: 0"

    @pytest.mark.asyncio
    async def test_invalid_code(self, invalid_pine_code):
        result = await validate_syntax(code=invalid_pine_code)
        assert "error" in result.lower() or "issue" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await validate_syntax(code="")
        assert "error" in result.lower() or "no code" in result.lower()

    @pytest.mark.asyncio
    async def test_whitespace_only(self):
        result = await validate_syntax(code="   \n\t  ")
        assert "error" in result.lower() or "no code" in result.lower()

    @pytest.mark.asyncio
    async def test_analysis_on_valid(self, valid_pine_code):
        result = await validate_syntax(code=valid_pine_code)
        assert "analysis" in result.lower() or "script type" in result.lower()


# ── validate_and_explain ─────────────────────────────────────────────────────

class TestValidateAndExplain:
    @pytest.mark.asyncio
    async def test_valid_code(self, valid_pine_code):
        result = await validate_and_explain(code=valid_pine_code)
        assert "passed" in result.lower() or "valid" in result.lower()
        assert "compiler" in result.lower()

    @pytest.mark.asyncio
    async def test_invalid_with_doc_lookup(self):
        code = "//@version=6\nindicator('test')\nema(close, 14)"
        result = await validate_and_explain(code=code)
        assert "error" in result.lower() or "report" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await validate_and_explain(code="")
        assert "error" in result.lower() or "no code" in result.lower()


# ── fix_and_validate ──────────────────────────────────────────────────────────

class TestFixAndValidate:
    @pytest.mark.asyncio
    async def test_ema_namespace_fix(self):
        result = await fix_and_validate(
            code="ema(close, 14)",
            error_description="Undeclared identifier 'ema'"
        )
        assert "fix" in result.lower()
        assert "hint" in result.lower()
        assert "ta.ema" in result

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await fix_and_validate(code="", error_description="test")
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_error_description(self):
        result = await fix_and_validate(code="test", error_description="")
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_transp_removal(self):
        result = await fix_and_validate(
            code='//@version=6\nindicator("test")\nplot(close, color=color.red, transp=50)',
            error_description="transp parameter not supported"
        )
        assert "transp" in result.lower() or "fix" in result.lower()

    @pytest.mark.asyncio
    async def test_produces_fixed_code(self):
        result = await fix_and_validate(
            code="sma(close, 20)",
            error_description="Undeclared identifier 'sma'"
        )
        assert "ta.sma" in result
        assert "FIXED CODE" in result or "fixed code" in result.lower()


# ── debug_pine_facade ─────────────────────────────────────────────────────────

class TestDebugPineFacade:
    @pytest.mark.asyncio
    async def test_valid_code(self, valid_pine_code):
        result = await debug_pine_facade(code=valid_pine_code)
        assert "debug" in result.lower()
        assert "circuit breaker" in result.lower()
        assert "normalized" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_code(self):
        result = await debug_pine_facade(code="")
        assert "error" in result.lower() or "no code" in result.lower()

    @pytest.mark.asyncio
    async def test_circuit_breaker_stats(self, valid_pine_code):
        result = await debug_pine_facade(code=valid_pine_code)
        assert "circuit_open" in result.lower() or "network_failures" in result.lower()


# ── validate_file ─────────────────────────────────────────────────────────────

class TestValidateFile:
    @pytest.mark.asyncio
    async def test_valid_file(self, dca_file_path):
        if not os.path.exists(dca_file_path):
            pytest.skip("DCA.ps not found")
        result = await validate_file(file_path=dca_file_path)
        assert "file" in result.lower()
        assert "compiler" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_path(self):
        result = await validate_file(file_path="")
        assert "error" in result.lower()
        assert len(result) > 50  # Helpful message

    @pytest.mark.asyncio
    async def test_nonexistent_path(self):
        result = await validate_file(file_path="/nonexistent/path/test.ps")
        assert "error" in result.lower()
        assert "access denied" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_system_file_rejected(self):
        result = await validate_file(file_path="/etc/passwd")
        assert "error" in result.lower()
        assert ".ps" in result or ".pine" in result

    @pytest.mark.asyncio
    async def test_non_ps_extension(self):
        result = await validate_file(file_path="/tmp/test.txt")
        assert "error" in result.lower()
        assert ".ps" in result or ".pine" in result

    @pytest.mark.asyncio
    async def test_no_path_leakage(self):
        result = await validate_file(file_path="/nonexistent/path/test.ps")
        # Should NOT contain full absolute paths in output
        assert "/nonexistent/path/test.ps" not in result or "access denied" in result.lower()
