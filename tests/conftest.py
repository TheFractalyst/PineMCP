"""
conftest.py - Shared fixtures for PineScript MCP test suite.

Preloads ChromaDB, embedding model, hot cache, and name index.
Uses local ./pine_db/ if available, otherwise builds from shipped data.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Ensure project root on sys.path
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))
os.chdir(str(_project_root))

# Use local pine_db if it exists (dev), otherwise use default path (CI/pip install)
_local_db = _project_root / "pine_db"
if _local_db.exists():
    os.environ.setdefault("PINESCRIPT_DB_PATH", str(_local_db))

# ruff: noqa: E402
from core.build_db import build_db_if_needed  # noqa: E402
from core.db import build_name_index as _build_name_index  # noqa: E402
from core.db import get_collection as _get_collection  # noqa: E402
from core.embeddings import _embedding_model_ready  # noqa: E402
from core.embeddings import get_model as _get_model  # noqa: E402
from core.hot_cache import build_hot_cache  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def warmup():
    """Synchronous warmup: preloads all singletons via a temporary event loop."""
    build_db_if_needed()
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
def example_file_path():
    """Path to an example strategy file used in benchmarks.
    Skips tests if the file is not found locally."""
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "tests", "fixtures", "example_strategy.ps")
