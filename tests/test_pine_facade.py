"""
test_pine_facade.py — Unit tests for core/pine_facade.py module.

Tests normalize_facade_response, enrich_error_with_code, and circuit breaker.
Uses mocks to avoid real HTTP calls.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.pine_facade import (
    PineFacadeCircuitBreaker,
    normalize_facade_response,
    enrich_error_with_code,
    call_pine_facade,
    get_facade_client,
    shutdown_http_client,
    pine_cb,
    _facade_http_client,
)


# ─────────────────────────────────────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker state before each test."""
    pine_cb.network_failures = 0
    pine_cb.open_until = 0.0
    pine_cb.total_calls = 0
    pine_cb.total_network_errors = 0
    pine_cb.total_compiler_errors = 0
    pine_cb.total_successes = 0
    yield


@pytest.fixture(autouse=True)
def reset_http_client():
    """Reset HTTP client before each test."""
    global _facade_http_client
    _facade_http_client = None
    yield


# ─────────────────────────────────────────────────────────────────────────────
# PineFacadeCircuitBreaker Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPineFacadeCircuitBreakerStates:
    """Test circuit breaker state transitions."""

    def test_initial_state_closed(self):
        """Fresh circuit breaker should be closed."""
        cb = PineFacadeCircuitBreaker(threshold=10, cooldown=60)
        assert not cb.is_open()
        assert cb.network_failures == 0

    def test_opens_at_threshold(self):
        """Circuit opens after threshold network failures."""
        cb = PineFacadeCircuitBreaker(threshold=2, cooldown=60)
        cb.record_network_failure()
        cb.record_network_failure()
        assert cb.is_open()

    def test_compiler_error_does_not_open(self):
        """Compiler errors should NOT trip the breaker."""
        cb = PineFacadeCircuitBreaker(threshold=2, cooldown=60)
        cb.record_compiler_error()
        cb.record_compiler_error()
        assert not cb.is_open()

    def test_success_resets_network_failures(self):
        """Success should reset network failure count."""
        cb = PineFacadeCircuitBreaker(threshold=3, cooldown=60)
        cb.record_network_failure()
        cb.record_network_failure()
        assert cb.network_failures == 2
        cb.record_success()
        assert cb.network_failures == 0

    def test_compiler_error_resets_network_failures(self):
        """Compiler error resets network failures (service is responsive)."""
        cb = PineFacadeCircuitBreaker(threshold=3, cooldown=60)
        cb.record_network_failure()
        assert cb.network_failures == 1
        cb.record_compiler_error()
        assert cb.network_failures == 0

    def test_exponential_backoff(self):
        """Cooldown should increase exponentially."""
        cb = PineFacadeCircuitBreaker(threshold=2, cooldown=60)  # 60s base cooldown

        # First opening
        cb.record_network_failure()
        cb.record_network_failure()  # Opens here
        first_backoff_power = 0  # failures - threshold = 0
        first_expected = 60 * (2 ** first_backoff_power)
        first_cooldown = cb.open_until - time.time()
        assert first_cooldown >= first_expected * 0.85  # -15% jitter

        # Reset and open again with more consecutive failures
        cb.network_failures = 0
        cb.open_until = 0

        # Simulate accumulated failures
        for _ in range(4):  # More failures = higher backoff power
            cb.record_network_failure()

        second_backoff_power = min(4 - 2, 5)  # cap at 5
        second_expected = 60 * (2 ** second_backoff_power)  # 240s
        second_cooldown = cb.open_until - time.time()

        # Second cooldown should be significantly longer (exponential)
        assert second_cooldown > first_cooldown * 2

    def test_backoff_capped_at_10_minutes(self):
        """Exponential backoff should cap at 600 seconds."""
        cb = PineFacadeCircuitBreaker(threshold=1, cooldown=600)
        # Many failures - just check that the calculation caps correctly
        for _ in range(10):
            if not cb.is_open():
                cb.record_network_failure()
            else:
                # Simulate cooldown expiration without waiting
                cb.open_until = 0
                cb.network_failures = 0
                cb.record_network_failure()

        # When circuit is open, cooldown should be capped
        if cb.is_open():
            remaining = cb.open_until - time.time()
            # Should be capped around 600s + jitter (max ~690s)
            assert remaining <= 700

    def test_stats_reporting(self):
        """Stats should return current state."""
        cb = PineFacadeCircuitBreaker(threshold=5, cooldown=60)
        cb.record_network_failure()
        cb.record_compiler_error()
        cb.record_success()

        stats = cb.stats()

        assert stats["circuit_open"] is False
        assert stats["network_failures"] == 0  # Reset by success
        assert stats["total_calls"] == 3
        assert stats["total_network_errors"] == 1
        assert stats["total_compiler_errors"] == 1
        assert stats["total_successes"] == 1
        assert stats["threshold"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# normalize_facade_response Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeFacadeResponse:
    """Test normalize_facade_response with various response shapes."""

    def test_successful_compilation(self):
        """Successful compilation should return success=True."""
        raw = {
            "success": True,
            "result": {
                "errors": [],
                "variables": {},
            }
        }
        result = normalize_facade_response(raw)

        assert result["success"] is True
        assert result["errors"] == []
        assert result["warnings"] == []

    def test_compilation_with_errors(self):
        """Failed compilation should return success=False with errors."""
        raw = {
            "success": True,
            "result": {
                "errors": [
                    {"line": 5, "column": 10, "text": "Undeclared identifier 'ema'", "type": "error"}
                ]
            }
        }
        result = normalize_facade_response(raw)

        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["line"] == 5
        assert result["errors"][0]["column"] == 10

    def test_rejection_shape(self):
        """Handle rejection with reason but no result."""
        raw = {
            "success": False,
            "reason": "Compilation timeout",
            "result": None
        }
        result = normalize_facade_response(raw)

        assert result["success"] is False
        assert len(result["errors"]) == 1
        assert "timeout" in result["errors"][0]["text"].lower()

    def test_errors_at_top_level(self):
        """Handle errors at top level instead of in result."""
        raw = {
            "success": True,
            "result": None,
            "errors": [
                {"line": 1, "column": 0, "text": "Syntax error"}
            ]
        }
        result = normalize_facade_response(raw)

        assert result["success"] is False
        assert len(result["errors"]) == 1

    def test_error_with_message_field(self):
        """Handle error with 'message' instead of 'text'."""
        raw = {
            "success": True,
            "result": {
                "errors": [
                    {"line": 2, "column": 5, "message": "Type mismatch", "type": "error"}
                ]
            }
        }
        result = normalize_facade_response(raw)

        assert result["errors"][0]["text"] == "Type mismatch"

    def test_error_with_start_object(self):
        """Handle error with nested start object for position."""
        raw = {
            "success": True,
            "result": {
                "errors": [
                    {"start": {"line": 3, "column": 8}, "text": "Error here", "type": "error"}
                ]
            }
        }
        result = normalize_facade_response(raw)

        assert result["errors"][0]["line"] == 3
        assert result["errors"][0]["column"] == 8

    def test_warning_separation(self):
        """Warnings should be separated from errors."""
        raw = {
            "success": True,
            "result": {
                "errors": [
                    {"line": 1, "text": "Error 1", "type": "error"},
                    {"line": 2, "text": "Warning 1", "type": "warning"},
                    {"line": 3, "text": "Error 2", "type": "error"},
                ]
            }
        }
        result = normalize_facade_response(raw)

        assert len(result["errors"]) == 2
        assert len(result["warnings"]) == 1
        assert result["warnings"][0]["text"] == "Warning 1"

    def test_placeholder_resolution_in_text(self):
        """Should resolve {kind} style placeholders from error fields."""
        raw = {
            "success": True,
            "result": {
                "errors": [
                    {"line": 1, "text": "Expected {kind} but got {fullName}", "kind": "int", "fullName": "float"}
                ]
            }
        }
        result = normalize_facade_response(raw)

        assert "int" in result["errors"][0]["text"]
        assert "float" in result["errors"][0]["text"]
        assert "{" not in result["errors"][0]["text"]

    def test_meta_extraction(self):
        """Should extract meta fields from result."""
        raw = {
            "success": True,
            "result": {
                "errors": [],
                "variables": {"x": "int"},
                "functions": {"f": "void"},
                "types": {"T": "type"},
            }
        }
        result = normalize_facade_response(raw)

        assert "variables" in result["meta"]
        assert "functions" in result["meta"]
        assert "types" in result["meta"]

    def test_non_dict_error_item(self):
        """Handle non-dict items in errors list."""
        raw = {
            "success": True,
            "result": {
                "errors": ["plain string error"]
            }
        }
        result = normalize_facade_response(raw)

        assert result["errors"][0]["text"] == "plain string error"


# ─────────────────────────────────────────────────────────────────────────────
# enrich_error_with_code Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestEnrichErrorWithCode:
    """Test enrich_error_with_code placeholder resolution."""

    def test_resolves_identifier_placeholder(self):
        """Should resolve {identifier} from code context."""
        errors = [{"line": 3, "column": 6, "text": "Undeclared {identifier}"}]
        code = "//@version=6\nindicator(\"test\")\nplot(xyz)"

        result = enrich_error_with_code(errors, code)

        # Should extract 'xyz' from the code at line 3, column 6
        assert "xyz" in result[0]["text"]
        assert "{" not in result[0]["text"]

    def test_resolves_name_placeholder(self):
        """Should resolve {name} from code context."""
        errors = [{"line": 2, "column": 1, "text": "Unknown {name}"}]
        code = "line1\nmyvar = 1\nplot(myvar)"

        result = enrich_error_with_code(errors, code)

        # Should extract 'myvar' starting at column 1 of line 2
        assert "myvar" in result[0]["text"]

    def test_resolves_multiple_placeholders(self):
        """Should resolve multiple placeholders in one error."""
        errors = [{"line": 1, "column": 1, "text": "{identifier} and {funName} error"}]
        code = "firstFunc secondFunc"

        result = enrich_error_with_code(errors, code)

        # Should extract 'firstFunc' for {identifier} (the first placeholder)
        assert "firstFunc" in result[0]["text"]

    def test_uses_defaults_for_missing_context(self):
        """Should use default values when code context unavailable."""
        errors = [{"line": 100, "column": 50, "text": "{identifier} error"}]
        code = "short code"  # Line 100 doesn't exist

        result = enrich_error_with_code(errors, code)

        assert "value" in result[0]["text"]

    def test_no_change_when_no_placeholders(self):
        """Should not modify text without placeholders."""
        errors = [{"line": 1, "column": 1, "text": "Simple error message"}]
        code = "some code"

        result = enrich_error_with_code(errors, code)

        assert result[0]["text"] == "Simple error message"

    def test_empty_code_returns_original(self):
        """Empty code should return errors unchanged."""
        errors = [{"line": 1, "column": 1, "text": "Error"}]

        result = enrich_error_with_code(errors, "")

        assert result == errors

    def test_removes_unknown_placeholders(self):
        """Should strip unknown placeholders."""
        errors = [{"line": 1, "column": 1, "text": "Error: {unknownPlaceholder}"}]
        code = "test"

        result = enrich_error_with_code(errors, code)

        assert "{" not in result[0]["text"]
        assert "unknownPlaceholder" in result[0]["text"]

    def test_extracts_identifier_with_dot(self):
        """Should extract identifiers containing dots."""
        errors = [{"line": 1, "column": 1, "text": "Unknown {identifier}"}]
        code = "ta.sma(close, 14)"

        result = enrich_error_with_code(errors, code)

        assert "ta.sma" in result[0]["text"]


# ─────────────────────────────────────────────────────────────────────────────
# call_pine_facade Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCallPineFacadeCircuitBreaker:
    """Test circuit breaker integration in call_pine_facade."""

    @pytest.mark.asyncio
    async def test_returns_error_when_circuit_open(self):
        """Should return circuit breaker error when open."""
        pine_cb.open_until = time.time() + 60  # Force open

        result = await call_pine_facade("//@version=6\nindicator(\"test\")")

        assert result["success"] is False
        assert "circuit breaker" in result["errors"][0]["text"].lower()
        assert result["meta"].get("fallback") == "circuit_breaker_open"


class TestCallPineFacadeHTTPStatusHandling:
    """Test HTTP status code handling."""

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_facade_client")
    async def test_http_403_returns_error(self, mock_get_client):
        """HTTP 403 should return user-friendly error."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Access denied"
        mock_response.headers = {}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await call_pine_facade("test code")

        assert result["success"] is False
        assert "403" in result["errors"][0]["text"]
        assert "access denied" in result["errors"][0]["text"].lower()
        assert result["errors"][0]["type"] == "http"

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_facade_client")
    async def test_http_503_returns_error(self, mock_get_client):
        """HTTP 503 should return service unavailable error."""
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await call_pine_facade("test code")

        assert result["success"] is False
        assert "503" in result["errors"][0]["text"]
        assert "unavailable" in result["errors"][0]["text"].lower()

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_facade_client")
    async def test_http_502_504_handled(self, mock_get_client):
        """HTTP 502/504 should be handled like 503."""
        for status in [502, 504]:
            pine_cb.open_until = 0  # Reset circuit breaker
            pine_cb.network_failures = 0

            mock_response = MagicMock()
            mock_response.status_code = status
            mock_response.text = "Error"

            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_get_client.return_value = mock_client

            result = await call_pine_facade("test code")

            assert result["success"] is False
            assert str(status) in result["errors"][0]["text"]


class TestCallPineFacadeNetworkErrors:
    """Test network error handling."""

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_facade_client")
    async def test_connect_error_returns_user_friendly(self, mock_get_client):
        """ConnectError should return user-friendly message."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_get_client.return_value = mock_client

        result = await call_pine_facade("test code")

        assert result["success"] is False
        assert "unreachable" in result["errors"][0]["text"].lower()
        assert "ConnectError" in result["errors"][0]["text"]

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_facade_client")
    async def test_timeout_error_returns_user_friendly(self, mock_get_client):
        """Timeout should return user-friendly message."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ReadTimeout("Timeout")
        mock_get_client.return_value = mock_client

        result = await call_pine_facade("test code")

        assert result["success"] is False
        assert "unreachable" in result["errors"][0]["text"].lower()

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_facade_client")
    async def test_oserror_returns_user_friendly(self, mock_get_client):
        """OSError should return user-friendly message."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = OSError("Network down")
        mock_get_client.return_value = mock_client

        result = await call_pine_facade("test code")

        assert result["success"] is False


class TestCallPineFacadeCaching:
    """Test content-hash caching behavior."""

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_cached_validation")
    @patch("core.pine_facade.get_facade_client")
    async def test_cache_hit_returns_cached_result(self, mock_get_client, mock_get_cached):
        """Cache hit should return cached result without HTTP call."""
        cached = {
            "success": True,
            "errors": [],
            "warnings": [],
            "meta": {},
            "raw_response": {},
        }
        mock_get_cached.return_value = cached

        result = await call_pine_facade("test code")

        assert result == cached
        mock_get_client.assert_not_called()

    @pytest.mark.asyncio
    @patch("core.pine_facade.set_cached_validation")
    @patch("core.pine_facade.get_facade_client")
    async def test_successful_result_cached(self, mock_get_client, mock_set_cached):
        """Successful result should be stored in cache."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"success": True, "result": {"errors": []}}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        await call_pine_facade("test code")

        mock_set_cached.assert_called_once()


class TestCallPineFacadeEmptyInput:
    """Test empty/whitespace input handling."""

    @pytest.mark.asyncio
    async def test_empty_code_returns_error(self):
        """Empty code should return error without HTTP call."""
        result = await call_pine_facade("")

        assert result["success"] is False
        assert "empty" in result["errors"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_whitespace_only_code_returns_error(self):
        """Whitespace-only code should return error."""
        result = await call_pine_facade("   \n\t  ")

        assert result["success"] is False
        assert "empty" in result["errors"][0]["text"].lower()


class TestCallPineFacadeJSONParsing:
    """Test JSON response parsing."""

    @pytest.mark.asyncio
    @patch("core.pine_facade.get_facade_client")
    async def test_non_json_response_handled(self, mock_get_client):
        """Non-JSON response should return error."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = json.JSONDecodeError("test", "", 0)
        mock_response.text = "Not JSON"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = await call_pine_facade("test code")

        assert result["success"] is False
        assert "non-JSON" in result["errors"][0]["text"]


# ─────────────────────────────────────────────────────────────────────────────
# HTTP Client Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestGetFacadeClient:
    """Test get_facade_client lazy initialization."""

    def test_returns_singleton(self):
        """Should return same client instance."""
        client1 = get_facade_client()
        client2 = get_facade_client()

        assert client1 is client2

    def test_creates_new_if_closed(self):
        """Should create new client if existing one is closed."""
        import asyncio
        client1 = get_facade_client()

        # Close the async client properly
        asyncio.run(client1.aclose())

        client2 = get_facade_client()

        assert client2 is not client1
        assert not client2.is_closed


class TestShutdownHttpClient:
    """Test shutdown_http_client cleanup."""

    def test_closes_client(self):
        """Should close the HTTP client."""
        client = get_facade_client()
        assert not client.is_closed

        shutdown_http_client()

        # Client should be None after shutdown
        from core.pine_facade import _facade_http_client
        assert _facade_http_client is None
