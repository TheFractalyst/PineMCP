"""
conftest.py — Shared fixtures for PineScript MCP test suite.

Preloads ChromaDB, embedding model, hot cache, and name index.
"""

import asyncio
import os
import sys

import pytest

# Ensure project root on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pinescript_mcp import (
    _get_collection,
    _get_model,
    _build_name_index,
    build_hot_cache,
    _embedding_model_ready,
)


@pytest.fixture(scope="session", autouse=True)
def warmup():
    """Synchronous warmup: preloads all singletons via a temporary event loop."""
    _get_collection()
    _get_model()
    _embedding_model_ready.set()
    _build_name_index()
    # Build hot cache in a fresh event loop
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(build_hot_cache())
    finally:
        loop.close()


@pytest.fixture
def valid_pine_code():
    """Minimal valid PineScript v6 indicator code."""
    return '//@version=6\nindicator("test")\nplot(close)'


@pytest.fixture
def invalid_pine_code():
    """PineScript code with namespace error (ema without ta. prefix)."""
    return 'ema(close, 14)'


@pytest.fixture
def dca_file_path():
    """Path to the DCA.ps strategy file used in benchmarks."""
    return "/Users/fractalyst/Documents/Quantify - Deeptest/Strategies/DCA.ps"
