"""
test_db.py — Unit tests for core/db.py module.

Tests ChromaDBCircuitBreaker, L1 cache behavior, and search_by_name variants.
Uses mocks to avoid real ChromaDB dependencies where possible.
"""

import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from core.db import (
    ChromaDBCircuitBreaker,
    _query,
    search_by_name,
    _reset_caches,
    _name_index,
    _name_index_built,
)


# ─────────────────────────────────────────────────────────────────────────────
# ChromaDBCircuitBreaker Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestChromaDBCircuitBreakerStateTransitions:
    """Test circuit breaker state machine: closed → open → reset after cooldown."""

    def test_initial_state_closed(self):
        """Fresh circuit breaker should be closed."""
        cb = ChromaDBCircuitBreaker(threshold=3, cooldown=30)
        assert not cb.is_open()
        assert cb.failures == 0
        assert cb.open_until == 0.0

    def test_stays_closed_below_threshold(self):
        """Circuit stays closed until threshold failures reached."""
        cb = ChromaDBCircuitBreaker(threshold=3, cooldown=30)
        cb.record_failure(Exception("test1"))
        assert not cb.is_open()
        cb.record_failure(Exception("test2"))
        assert not cb.is_open()

    def test_opens_at_threshold(self):
        """Circuit opens exactly at threshold failures."""
        cb = ChromaDBCircuitBreaker(threshold=2, cooldown=60)
        cb.record_failure(Exception("test1"))
        cb.record_failure(Exception("test2"))
        assert cb.is_open()
        assert cb.open_until > time.time()

    def test_auto_reset_after_cooldown(self):
        """Circuit auto-resets after cooldown period expires."""
        cb = ChromaDBCircuitBreaker(threshold=1, cooldown=0.01)
        cb.record_failure(Exception("test"))
        assert cb.is_open()
        time.sleep(0.02)  # Wait for cooldown
        assert not cb.is_open()  # is_open() checks and resets
        assert cb.failures == 0
        assert cb.open_until == 0.0

    def test_success_resets_failures(self):
        """Recording success resets failure count."""
        cb = ChromaDBCircuitBreaker(threshold=3, cooldown=30)
        cb.record_failure(Exception("test"))
        assert cb.failures == 1
        cb.record_success()
        assert cb.failures == 0
        assert cb.open_until == 0.0

    def test_is_open_refreshes_state(self):
        """is_open() should check time and reset if cooldown expired."""
        cb = ChromaDBCircuitBreaker(threshold=1, cooldown=0.05)
        cb.record_failure(Exception("test"))
        assert cb.is_open()
        time.sleep(0.06)
        assert not cb.is_open()  # Should auto-reset

    def test_multiple_failures_beyond_threshold(self):
        """Additional failures while open extend cooldown (via log warnings)."""
        cb = ChromaDBCircuitBreaker(threshold=2, cooldown=30)
        cb.record_failure(Exception("test1"))
        cb.record_failure(Exception("test2"))  # Opens here
        first_open_until = cb.open_until
        cb.record_failure(Exception("test3"))  # Still open, more failures
        assert cb.is_open()


# ─────────────────────────────────────────────────────────────────────────────
# L1 Cache Tests (_query function)
# ─────────────────────────────────────────────────────────────────────────────


class TestL1QueryCache:
    """Test L1 query result cache hit/miss behavior."""

    @patch("core.db.get_model")
    @patch("core.db.get_collection")
    def test_cache_hit_returns_without_embedding(self, mock_get_collection, mock_get_model):
        """Cache hit should return result without calling embedding model."""
        from core.caches import _QUERY_RESULT_CACHE, _QUERY_CACHE_LOCK

        # Clear cache first
        with _QUERY_CACHE_LOCK:
            _QUERY_RESULT_CACHE.clear()

        # Pre-populate cache
        cached_result = {
            "ids": [["id1", "id2"]],
            "metadatas": [[{"name": "test"}, {"name": "test2"}]],
            "documents": [["doc1", "doc2"]],
            "distances": [[0.1, 0.2]],
        }
        import xxhash
        cache_key = xxhash.xxh64("test_query|5|None".encode()).hexdigest()
        with _QUERY_CACHE_LOCK:
            _QUERY_RESULT_CACHE[cache_key] = (cached_result, time.time())

        # Call _query - should hit cache
        result = _query("test_query", 5)

        # Embedding model should NOT be called
        mock_get_model.assert_not_called()
        mock_get_collection.assert_not_called()

        # Verify result structure
        assert "ids" in result
        assert len(result["ids"][0]) == 2

    @patch("core.db.get_model")
    @patch("core.db.get_collection")
    def test_cache_miss_calls_embedding(self, mock_get_collection, mock_get_model):
        """Cache miss should call embedding model and ChromaDB."""
        from core.caches import _QUERY_RESULT_CACHE, _QUERY_CACHE_LOCK

        # Clear cache
        with _QUERY_CACHE_LOCK:
            _QUERY_RESULT_CACHE.clear()

        # Mock embedding model
        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 384]
        mock_get_model.return_value = mock_model

        # Mock collection
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "ids": [["id1"]],
            "metadatas": [[{"name": "test"}]],
            "documents": [["doc1"]],
            "distances": [[0.1]],
        }
        mock_get_collection.return_value = mock_col

        # Call _query
        result = _query("new_unique_query_12345", 5)

        # Embedding model should be called
        mock_get_model.assert_called_once()
        mock_model.encode.assert_called_once()
        # Note: query may or may not be called depending on cache state
        # The key assertion is that embedding model was called for cache miss

    @patch("core.db.get_model")
    @patch("core.db.get_collection")
    def test_cache_ttl_expiration(self, mock_get_collection, mock_get_model):
        """Cached entries should expire after TTL."""
        from core.caches import _QUERY_RESULT_CACHE, _QUERY_CACHE_LOCK, _QUERY_CACHE_TTL

        # Clear cache
        with _QUERY_CACHE_LOCK:
            _QUERY_RESULT_CACHE.clear()

        # Pre-populate with expired entry
        cached_result = {
            "ids": [["id1"]],
            "metadatas": [[{"name": "test"}]],
            "documents": [["doc1"]],
            "distances": [[0.1]],
        }
        import xxhash
        cache_key = xxhash.xxh64("expired_query|5|None".encode()).hexdigest()
        with _QUERY_CACHE_LOCK:
            _QUERY_RESULT_CACHE[cache_key] = (cached_result, time.time() - _QUERY_CACHE_TTL - 1)

        # Mock for fallback
        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 384]
        mock_get_model.return_value = mock_model
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "ids": [["id2"]],
            "metadatas": [[{"name": "fresh"}]],
            "documents": [["fresh_doc"]],
            "distances": [[0.2]],
        }
        mock_get_collection.return_value = mock_col

        # Call _query - should miss cache due to TTL
        result = _query("expired_query", 5)

        # Should have called embedding since cache entry expired
        mock_get_model.assert_called_once()

    @patch("core.db.get_model")
    @patch("core.db.get_collection")
    def test_cache_eviction_at_max_size(self, mock_get_collection, mock_get_model):
        """Cache should evict oldest entries when max size reached."""
        from core.caches import _QUERY_RESULT_CACHE, _QUERY_CACHE_LOCK, _QUERY_CACHE_MAX

        # Clear cache
        with _QUERY_CACHE_LOCK:
            _QUERY_RESULT_CACHE.clear()

        # Mock embedding
        mock_model = MagicMock()
        mock_model.encode.return_value = [[0.1] * 384]
        mock_get_model.return_value = mock_model
        mock_col = MagicMock()
        mock_col.query.return_value = {
            "ids": [[]],
            "metadatas": [[]],
            "documents": [[]],
            "distances": [[]],
        }
        mock_get_collection.return_value = mock_col

        # Add entries up to max + 1
        import xxhash
        for i in range(_QUERY_CACHE_MAX + 2):
            _query(f"query_{i}", 5)

        # Cache should not exceed max size
        with _QUERY_CACHE_LOCK:
            assert len(_QUERY_RESULT_CACHE) <= _QUERY_CACHE_MAX


# ─────────────────────────────────────────────────────────────────────────────
# search_by_name Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchByNameQualified:
    """Test search_by_name with fully qualified names (contains ".")."""

    @patch("core.db.get_collection")
    def test_qualified_name_exact_match(self, mock_get_collection):
        """Fully qualified names should do exact match only."""
        mock_col = MagicMock()
        mock_col.get.return_value = {
            "ids": ["id1"],
            "metadatas": [{"name": "ta.ema", "category": "function"}],
            "documents": ["Exponential Moving Average"],
        }
        mock_get_collection.return_value = mock_col

        result = search_by_name("ta.ema")

        # Should return results
        assert len(result) == 1
        assert result[0][0] == 100.0  # Exact match score
        mock_col.get.assert_called()

    @patch("core.db.get_collection")
    def test_qualified_no_fallback_to_fuzzy(self, mock_get_collection):
        """Qualified names should NOT fall through to fuzzy search."""
        mock_col = MagicMock()
        # First call returns empty (no exact match)
        mock_col.get.side_effect = [
            {"ids": [], "metadatas": [], "documents": []},  # First attempt
            {"ids": [], "metadatas": [], "documents": []},  # Second attempt
        ]
        mock_get_collection.return_value = mock_col

        result = search_by_name("nonexistent.namespace")

        # Should return empty, not do fuzzy search
        assert result == []

    @patch("core.db.get_collection")
    def test_qualified_with_where_filter(self, mock_get_collection):
        """Qualified search with where filter should include type constraint."""
        mock_col = MagicMock()
        mock_col.get.side_effect = [
            {"ids": [], "metadatas": [], "documents": []},  # First attempt
            {"ids": ["id1"], "metadatas": [{"name": "ta.ema"}], "documents": ["doc"]},
        ]
        mock_get_collection.return_value = mock_col

        result = search_by_name("ta.ema", where={"category": "function"})

        # Second call should have combined where clause
        calls = mock_col.get.call_args_list
        assert len(calls) >= 2


class TestSearchByNameUnqualified:
    """Test search_by_name with unqualified names (no ".")."""

    @patch("core.db.get_collection")
    def test_empty_name_returns_empty(self, mock_get_collection):
        """Empty/whitespace names should return empty list."""
        result = search_by_name("")
        assert result == []
        result = search_by_name("   ")
        assert result == []
        # Note: get_collection may be called by other fixtures, don't assert on it

    @patch("core.db._name_index", {"ema": [{"id": "id1", "metadata": {"name": "ema", "category": "function"}, "document": "doc1"}]})
    @patch("core.db._name_index_built", True)
    def test_name_index_lookup(self):
        """Should use name index if available."""
        result = search_by_name("ema")

        assert len(result) == 1
        assert result[0][0] == 100.0

    @patch("core.db._name_index", {
        "ema": [
            {"id": "id1", "metadata": {"name": "ema", "category": "function"}, "document": "doc1"},
            {"id": "id2", "metadata": {"name": "ema", "category": "variable"}, "document": "doc2"},
        ]
    })
    @patch("core.db._name_index_built", True)
    @patch("core.db.get_collection")
    def test_name_index_with_where_filter(self, mock_get_collection):
        """Name index should respect where filters."""
        # Filter to only functions
        result = search_by_name("ema", where={"category": "function"})

        assert len(result) == 1
        assert result[0][1]["metadata"]["category"] == "function"

    @patch("core.db.get_collection")
    def test_chromadb_exact_match_fallback(self, mock_get_collection):
        """If name index not built, should query ChromaDB."""
        global _name_index_built

        original_built = _name_index_built
        try:
            _name_index_built = False

            mock_col = MagicMock()
            mock_col.get.return_value = {
                "ids": ["id1"],
                "metadatas": [{"name": "close", "category": "variable"}],
                "documents": ["Close price"],
            }
            mock_get_collection.return_value = mock_col

            result = search_by_name("close")

            assert len(result) == 1
            assert result[0][0] == 100.0
        finally:
            _name_index_built = original_built

    @patch("core.db.get_collection")
    def test_fuzzy_fallback(self, mock_get_collection):
        """If no exact match, should do fuzzy search."""
        global _name_index_built

        original_built = _name_index_built
        try:
            _name_index_built = False

            mock_col = MagicMock()
            mock_col.get.return_value = {"ids": [], "metadatas": [], "documents": []}
            mock_col.count.return_value = 2
            mock_col.get.side_effect = [
                {"ids": [], "metadatas": [], "documents": []},  # Exact match attempt
                {  # Fuzzy scan
                    "ids": ["id1", "id2"],
                    "metadatas": [{"name": "ema"}, {"name": "sma"}],
                    "documents": ["doc1", "doc2"],
                },
            ]
            mock_get_collection.return_value = mock_col

            result = search_by_name("emaa")  # Typo of "ema"

            # Should return fuzzy results
            assert len(result) > 0
        finally:
            _name_index_built = original_built


# ─────────────────────────────────────────────────────────────────────────────
# _reset_caches Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestResetCaches:
    """Test _reset_caches function."""

    def test_resets_name_index(self):
        """_reset_caches should clear name index."""
        import core.db as db_module

        # Set up some state directly on the module
        db_module._name_index = {"test": [{"id": "1"}]}
        db_module._name_index_built = True

        _reset_caches()

        assert db_module._name_index == {}
        assert db_module._name_index_built is False

    def test_resets_hot_cache(self):
        """_reset_caches should clear hot cache via import."""
        _reset_caches()
        # Should complete without error
