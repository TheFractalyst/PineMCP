"""
test_helpers.py — Unit tests for internal helper functions.

Tests formatting, sanitization, caching, circuit breaker, and error handling.
"""

import pytest

from pinescript_mcp import (
    _norm_name,
    _norm_ns,
    _relevance_pct,
    _sanitize_text,
    _sanitize_pine_string,
    _lookup_fix_hint,
    _extract_name_from_error,
    _cap_response,
    _dedup_examples,
    _format_params_text,
    _format_examples_text,
    _format_entry_detail,
    _check_query_error,
    _safe_error,
    cache_lookup,
    HOT_CACHE,
    _name_index_built,
    _name_index,
    ChromaDBCircuitBreaker,
    PineFacadeCircuitBreaker,
)


# ── Name normalization ────────────────────────────────────────────────────────

class TestNormName:
    def test_strip(self):
        assert _norm_name("  ta.ema  ") == "ta.ema"

    def test_trailing_parens(self):
        assert _norm_name("ta.ema()") == "ta.ema"

    def test_no_change(self):
        assert _norm_name("close") == "close"


class TestNormNs:
    def test_strip(self):
        assert _norm_ns("  ta  ") == "ta"

    def test_trailing_dot(self):
        assert _norm_ns("ta.") == "ta"

    def test_uppercase(self):
        assert _norm_ns("TA") == "ta"


# ── Relevance percentage ──────────────────────────────────────────────────────

class TestRelevancePct:
    def test_perfect_match(self):
        assert _relevance_pct(0.0) == "100%"

    def test_no_match(self):
        assert _relevance_pct(1.0) == "0%"

    def test_partial(self):
        result = _relevance_pct(0.3)
        assert result == "70%"


# ── Sanitization ──────────────────────────────────────────────────────────────

class TestSanitizeText:
    def test_null_bytes(self):
        assert "\x00" not in _sanitize_text("hello\x00world")

    def test_control_chars(self):
        assert "\x01" not in _sanitize_text("hello\x01world")

    def test_newlines_preserved(self):
        result = _sanitize_text("hello\nworld")
        assert "\n" in result

    def test_non_string(self):
        result = _sanitize_text(42)
        assert result == "42"

    def test_whitespace_strip(self):
        assert _sanitize_text("  hello  ") == "hello"


class TestSanitizePineString:
    def test_quotes(self):
        result = _sanitize_pine_string('he said "hello"')
        assert '"' not in result

    def test_backslash(self):
        result = _sanitize_pine_string("path\\to\\file")
        assert "\\" not in result

    def test_length_limit(self):
        result = _sanitize_pine_string("x" * 200)
        assert len(result) <= 100


# ── Fix hints ─────────────────────────────────────────────────────────────────

class TestLookupFixHint:
    def test_undeclared_identifier(self):
        hint = _lookup_fix_hint("Undeclared identifier 'ema'")
        assert "var" in hint.lower() or "ta." in hint.lower()

    def test_transp(self):
        hint = _lookup_fix_hint("transp parameter removed")
        assert "color.new" in hint

    def test_no_match(self):
        hint = _lookup_fix_hint("something completely unknown xyz")
        assert "reference" in hint.lower() or "syntax" in hint.lower()


class TestExtractNameFromError:
    def test_quoted_identifier(self):
        assert _extract_name_from_error("Undeclared identifier 'ta.supertrend'") == "ta.supertrend"

    def test_cannot_call(self):
        result = _extract_name_from_error("Cannot call 'ta.ema'")
        assert result is not None

    def test_no_match(self):
        assert _extract_name_from_error("random error text") is None


# ── Response capping ──────────────────────────────────────────────────────────

class TestCapResponse:
    def test_short_text(self):
        text = "hello"
        assert _cap_response(text) == text

    def test_long_text_truncated(self):
        text = "x" * 20000
        result = _cap_response(text, limit=1000)
        assert len(result) < len(text)
        assert "truncated" in result.lower()

    def test_custom_limit(self):
        text = "x" * 500
        result = _cap_response(text, limit=100)
        assert len(result) < 200


# ── Example deduplication ─────────────────────────────────────────────────────

class TestDedupExamples:
    def test_removes_duplicates(self):
        examples = ["plot(close)", "plot(close)"]
        assert len(_dedup_examples(examples)) == 1

    def test_preserves_unique(self):
        examples = ["plot(close)", "plot(open)"]
        assert len(_dedup_examples(examples)) == 2

    def test_prefers_formatted(self):
        collapsed = "plot(close) plot(open)"
        formatted = "plot(close)\nplot(open)"
        result = _dedup_examples([collapsed, formatted])
        assert formatted in result


# ── Query error check ─────────────────────────────────────────────────────────

class TestCheckQueryError:
    def test_valid_result(self):
        assert _check_query_error({"ids": [[]], "documents": [[]]}) is None

    def test_error_result(self):
        result = _check_query_error({"_error": "ConnectionError: refused"})
        assert "unavailable" in result.lower()

    def test_no_error_key(self):
        assert _check_query_error({"ids": [[]]}) is None


# ── Safe error ────────────────────────────────────────────────────────────────

class TestSafeError:
    def test_path_removal(self):
        err = ValueError("file not found: /Users/test/secret/path/file.py")
        result = _safe_error(err)
        assert "/Users/test/secret/path/file.py" not in result
        assert "[path]" in result

    def test_length_cap(self):
        err = ValueError("x" * 500)
        result = _safe_error(err)
        assert len(result) <= 300

    def test_context_prefix(self):
        err = ValueError("test error")
        result = _safe_error(err, context="tool1")
        assert "[tool1]" in result


# ── Cache lookup ──────────────────────────────────────────────────────────────

class TestCacheLookup:
    def test_case_insensitive(self):
        if "close" in HOT_CACHE or "close" in {e.lower() for e in HOT_CACHE}:
            assert cache_lookup("CLOSE") is not None or cache_lookup("close") is not None

    def test_miss(self):
        result = cache_lookup("xyznonexistent12345_cache_test")
        assert result is None

    def test_dotted_name_short_fallback(self):
        """cache_lookup should try short name after dot."""
        # If "ema" is in hot cache, "ta.ema" should find it via short fallback
        if "ema" in HOT_CACHE:
            result = cache_lookup("ta.ema")
            assert result is not None


# ── Circuit breakers ──────────────────────────────────────────────────────────

class TestChromaDBCircuitBreaker:
    def test_initial_state(self):
        cb = ChromaDBCircuitBreaker(threshold=3, cooldown=30)
        assert not cb.is_open()
        assert cb.failures == 0

    def test_opens_after_threshold(self):
        cb = ChromaDBCircuitBreaker(threshold=2, cooldown=1)
        cb.record_failure(Exception("test1"))
        assert not cb.is_open()
        cb.record_failure(Exception("test2"))
        assert cb.is_open()

    def test_success_resets(self):
        cb = ChromaDBCircuitBreaker(threshold=3, cooldown=30)
        cb.record_failure(Exception("test"))
        cb.record_success()
        assert cb.failures == 0
        assert not cb.is_open()


class TestPineFacadeCircuitBreaker:
    def test_initial_state(self):
        cb = PineFacadeCircuitBreaker()
        assert not cb.is_open()
        assert cb.network_failures == 0

    def test_compiler_error_resets_network(self):
        cb = PineFacadeCircuitBreaker(threshold=2, cooldown=1)
        cb.record_network_failure()
        assert cb.network_failures == 1
        cb.record_compiler_error()
        assert cb.network_failures == 0

    def test_stats(self):
        cb = PineFacadeCircuitBreaker()
        stats = cb.stats()
        assert "circuit_open" in stats
        assert "total_calls" in stats
        assert "threshold" in stats


# ── Format helpers ────────────────────────────────────────────────────────────

class TestFormatParamsText:
    def test_empty(self):
        assert _format_params_text({}) == ""

    def test_with_params(self):
        meta = {
            "raw_parameters": '[{"name":"source","type":"series float","description":"Source"}]'
        }
        result = _format_params_text(meta)
        assert "source" in result.lower()
        assert "PARAMETERS" in result

    def test_param_count_only(self):
        result = _format_params_text({"param_count": 3})
        assert "3" in result


class TestFormatExamplesText:
    def test_empty(self):
        assert _format_examples_text({}) == ""

    def test_with_examples(self):
        meta = {"raw_examples": "plot(close) ||| plot(open)"}
        result = _format_examples_text(meta)
        assert "plot" in result
        assert "EXAMPLES" in result


class TestFormatEntryDetail:
    def test_hollow_entry(self):
        result = _format_entry_detail("test", {}, "")
        assert "no local documentation" in result.lower() or "not found" in result.lower()

    def test_full_entry(self):
        meta = {
            "category": "function",
            "namespace": "ta",
            "syntax": "ta.ema(source, length)",
            "raw_description": "Exponential Moving Average",
            "returns": "series float",
        }
        result = _format_entry_detail("ta.ema", meta, "Exponential Moving Average" + " " * 200)
        assert "ta.ema" in result
        assert "FUNCTION" in result
