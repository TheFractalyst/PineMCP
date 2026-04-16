"""
test_hot_cache.py — Unit tests for core/hot_cache.py module.

Tests cache_lookup functionality and ensure_hot_cache idempotency.
Uses mocks to avoid real ChromaDB dependencies.
"""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

import core.hot_cache as hot_cache_module
from core.hot_cache import (
    HOT_CACHE,
    PRIORITY_NAMESPACES,
    PRIORITY_GLOBALS,
    build_hot_cache,
    cache_lookup,
    ensure_hot_cache,
)


# ─────────────────────────────────────────────────────────────────────────────
# Setup and Teardown
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_hot_cache():
    """Clear hot cache before each test."""
    HOT_CACHE.clear()
    hot_cache_module._hot_cache_built = False
    hot_cache_module._cache_hits = 0
    hot_cache_module._cache_misses = 0
    yield
    # Cleanup after test
    HOT_CACHE.clear()
    hot_cache_module._hot_cache_built = False
    hot_cache_module._cache_hits = 0
    hot_cache_module._cache_misses = 0


# ─────────────────────────────────────────────────────────────────────────────
# cache_lookup Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestCacheLookupBasic:
    """Test basic cache_lookup functionality."""

    def test_exact_match_hit(self):
        """Exact key match should return entry and increment hits."""
        HOT_CACHE["ema"] = {"id": "id1", "document": "doc", "metadata": {}}

        result = cache_lookup("ema")

        assert result is not None
        assert result["id"] == "id1"

    def test_case_insensitive_lookup(self):
        """Lookup should be case-insensitive."""
        HOT_CACHE["ema"] = {"id": "id1", "document": "doc", "metadata": {}}

        assert cache_lookup("EMA") is not None
        assert cache_lookup("Ema") is not None
        assert cache_lookup("ema") is not None

    def test_whitespace_stripping(self):
        """Lookup should strip whitespace."""
        HOT_CACHE["ema"] = {"id": "id1", "document": "doc", "metadata": {}}

        assert cache_lookup("  ema  ") is not None
        assert cache_lookup("ema  ") is not None
        assert cache_lookup("  ema") is not None

    def test_miss_returns_none(self):
        """Miss should return None and increment misses."""
        result = cache_lookup("nonexistent")

        assert result is None

    def test_miss_increments_counter(self):
        """Cache miss should increment _cache_misses."""
        hot_cache_module._cache_misses = 0

        cache_lookup("nonexistent")

        assert hot_cache_module._cache_misses == 1

    def test_hit_increments_counter(self):
        """Cache hit should increment _cache_hits."""
        hot_cache_module._cache_hits = 0

        HOT_CACHE["ema"] = {"id": "id1", "document": "doc", "metadata": {}}
        cache_lookup("ema")

        assert hot_cache_module._cache_hits == 1


class TestCacheLookupDotBehavior:
    """Test cache_lookup behavior with dotted names."""

    def test_qualified_name_exact_match(self):
        """Fully qualified names should match exactly if present."""
        HOT_CACHE["ta.ema"] = {"id": "id1", "document": "TA.EMA doc", "metadata": {}}

        result = cache_lookup("ta.ema")

        assert result is not None
        assert result["document"] == "TA.EMA doc"

    def test_qualified_name_miss_if_not_in_cache(self):
        """Dotted names miss if not in cache (no automatic fallback)."""
        HOT_CACHE["ema"] = {"id": "id1", "document": "doc", "metadata": {}}

        # Looking up "ta.ema" should miss because only "ema" is in cache
        result = cache_lookup("ta.ema")

        assert result is None

    def test_dot_in_name_exact_only(self):
        """Names with dots require exact match in cache."""
        HOT_CACHE["barstate.isconfirmed"] = {"id": "id1", "document": "doc", "metadata": {}}

        result = cache_lookup("barstate.isconfirmed")

        assert result is not None
        assert result["id"] == "id1"


# ─────────────────────────────────────────────────────────────────────────────
# build_hot_cache Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildHotCacheIdempotency:
    """Test ensure_hot_cache and build_hot_cache idempotency."""

    @pytest.mark.asyncio
    async def test_build_is_idempotent(self):
        """Multiple calls to build_hot_cache should be idempotent."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_col = MagicMock()
            mock_col.get.return_value = {"ids": [], "metadatas": [], "documents": []}
            mock_get_collection.return_value = mock_col

            # First build
            result1 = await build_hot_cache()
            assert result1 is True

            # Second build should return immediately (already built)
            result2 = await build_hot_cache()
            assert result2 is True

            # get_collection should only be called once
            assert mock_get_collection.call_count == 1

    @pytest.mark.asyncio
    async def test_ensure_hot_cache_builds_once(self):
        """Multiple calls to ensure_hot_cache should build only once."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_col = MagicMock()
            mock_col.get.return_value = {"ids": [], "metadatas": [], "documents": []}
            mock_get_collection.return_value = mock_col

            # Multiple ensures
            await ensure_hot_cache()
            await ensure_hot_cache()
            await ensure_hot_cache()

            # Should only build once
            assert mock_get_collection.call_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_builds_are_serialized(self):
        """Concurrent calls should be serialized by lock."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_col = MagicMock()
            mock_col.get.return_value = {"ids": [], "metadatas": [], "documents": []}
            mock_get_collection.return_value = mock_col

            # Clear built flag
            hot_cache_module._hot_cache_built = False

            # Launch concurrent builds
            async def build():
                return await build_hot_cache()

            results = await asyncio.gather(build(), build(), build())

            # All should succeed
            assert all(results)
            # But get_collection should only be called once
            assert mock_get_collection.call_count == 1


class TestBuildHotCacheFunctionality:
    """Test build_hot_cache loads data correctly."""

    @pytest.mark.asyncio
    async def test_loads_namespace_entries(self):
        """Should load entries from priority namespaces."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_col = MagicMock()
            mock_col.get.return_value = {
                "ids": ["id1"],
                "metadatas": [{"name": "ema", "namespace": "ta"}],
                "documents": ["EMA documentation"],
            }
            mock_get_collection.return_value = mock_col

            HOT_CACHE.clear()
            hot_cache_module._hot_cache_built = False

            result = await build_hot_cache()

            assert result is True
            assert "ema" in HOT_CACHE
            assert HOT_CACHE["ema"]["document"] == "EMA documentation"

    @pytest.mark.asyncio
    async def test_handles_duplicate_names(self):
        """Should keep entry with richer documentation on duplicate."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_col = MagicMock()
            # First namespace returns short doc
            # Second namespace (in loop) returns longer doc
            call_count = [0]
            def side_effect(**kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    return {
                        "ids": ["id1"],
                        "metadatas": [{"name": "test_func", "namespace": "ta"}],
                        "documents": ["Short"],
                    }
                else:
                    return {
                        "ids": ["id2"],
                        "metadatas": [{"name": "test_func", "namespace": "math"}],
                        "documents": ["Much longer documentation here"],
                    }

            mock_col.get.side_effect = side_effect
            mock_get_collection.return_value = mock_col

            HOT_CACHE.clear()
            hot_cache_module._hot_cache_built = False

            await build_hot_cache()

            # Should have the longer doc
            assert len(HOT_CACHE["test_func"]["document"]) > 10

    @pytest.mark.asyncio
    async def test_loads_global_variables(self):
        """Should load priority global variables."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_col = MagicMock()
            mock_col.get.return_value = {
                "ids": ["id1"],
                "metadatas": [{"name": "close"}],
                "documents": ["Close price"],
            }
            mock_get_collection.return_value = mock_col

            HOT_CACHE.clear()
            hot_cache_module._hot_cache_built = False

            await build_hot_cache()

            assert "close" in HOT_CACHE

    @pytest.mark.asyncio
    async def test_handles_namespace_load_failure(self):
        """Should continue if one namespace fails to load."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_col = MagicMock()
            call_count = [0]
            def side_effect(**kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("DB error")
                return {
                    "ids": ["id1"],
                    "metadatas": [{"name": "ema", "namespace": "ta"}],
                    "documents": ["EMA"],
                }
            mock_col.get.side_effect = side_effect
            mock_get_collection.return_value = mock_col

            HOT_CACHE.clear()
            hot_cache_module._hot_cache_built = False

            result = await build_hot_cache()

            assert result is True  # Should still succeed

    @pytest.mark.asyncio
    async def test_returns_false_on_total_failure(self):
        """Should return False if get_collection fails completely."""
        with patch.object(hot_cache_module, 'get_collection') as mock_get_collection:
            mock_get_collection.side_effect = Exception("DB down")

            HOT_CACHE.clear()
            hot_cache_module._hot_cache_built = False

            result = await build_hot_cache()

            assert result is False


# ─────────────────────────────────────────────────────────────────────────────
# Thread Safety Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestHotCacheThreadSafety:
    """Test thread safety of cache_lookup."""

    def test_concurrent_lookups_thread_safe(self):
        """Concurrent lookups should not corrupt counters."""
        HOT_CACHE["test"] = {"id": "id1", "document": "doc", "metadata": {}}
        hot_cache_module._cache_hits = 0
        hot_cache_module._cache_misses = 0

        import threading
        results = []

        def lookup_hit():
            for _ in range(100):
                results.append(cache_lookup("test"))

        def lookup_miss():
            for _ in range(100):
                results.append(cache_lookup("nonexistent"))

        threads = []
        for _ in range(5):
            threads.append(threading.Thread(target=lookup_hit))
            threads.append(threading.Thread(target=lookup_miss))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have 500 hits and 500 misses
        assert hot_cache_module._cache_hits == 500
        assert hot_cache_module._cache_misses == 500
