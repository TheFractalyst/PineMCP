"""
core/embeddings.py
------------------------------------------------------------------------------
SentenceTransformer embedding model management.
- Lazy initialization with double-check locking
- MPS acceleration on Apple Silicon, ONNX on CPU-only
- Thread-pool executor to avoid blocking the event loop
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

from core.config import EMBED_MODEL

# -- Non-blocking embedding model loader ------------------------------------

_model_executor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="embedding"
)
_embedding_model_ready = asyncio.Event()
_embed_model = None
_model_init_lock = threading.Lock()


def get_model() -> SentenceTransformer:
    """Return the SentenceTransformer, initializing lazily.

    Thread-safe: uses _model_init_lock to prevent concurrent initialization.
    Uses PyTorch with MPS acceleration on Apple Silicon (faster than ONNX
    due to Metal GPU). Falls back to ONNX on CPU-only systems where
    ONNX Runtime is significantly faster than PyTorch-CPU.
    """
    global _embed_model
    # Fast path: already initialized (no lock needed for read)
    if _embed_model is not None:
        return _embed_model
    # Slow path: initialize under lock
    with _model_init_lock:
        # Double-check after acquiring lock
        if _embed_model is not None:
            return _embed_model
        try:
            import torch
            from sentence_transformers import SentenceTransformer

            # Apple Silicon: MPS is faster than ONNX for this model size
            # CPU-only systems: ONNX can be 1.4-3x faster than PyTorch-CPU
            if torch.backends.mps.is_available():
                _embed_model = SentenceTransformer(EMBED_MODEL, device="mps")
                logger.info(f"Embedding model loaded: {EMBED_MODEL} (PyTorch/MPS)")
            elif not torch.cuda.is_available():
                # CPU-only: try ONNX for speedup
                try:
                    _embed_model = SentenceTransformer(EMBED_MODEL, backend="onnx")
                    logger.info(f"Embedding model loaded: {EMBED_MODEL} (ONNX/CPU)")
                except Exception:
                    _embed_model = SentenceTransformer(EMBED_MODEL)
                    logger.info(f"Embedding model loaded: {EMBED_MODEL} (PyTorch/CPU)")
            else:
                _embed_model = SentenceTransformer(EMBED_MODEL)
                logger.info(f"Embedding model loaded: {EMBED_MODEL} (PyTorch)")

            return _embed_model
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise


async def ensure_embedding_model():
    """Load SentenceTransformer in thread pool - never blocks event loop."""
    if _embedding_model_ready.is_set():
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_model_executor, get_model)
    _embedding_model_ready.set()
