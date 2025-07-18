"""
core/build_db.py
Auto-build ChromaDB from shipped JSON data on first run.

Ships data/pine_merged_entries.json (2.1MB, 1751 entries) as package data.
On first server start, if no ChromaDB exists at DB_PATH, builds it automatically.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from core.config import COLLECTION, DB_PATH, EMBED_MODEL


def _data_json_path() -> Path:
    """Locate pine_merged_entries.json relative to this module."""
    return Path(__file__).resolve().parent.parent / "data" / "pine_merged_entries.json"


def _extract_return_type(syntax: str) -> str:
    """Extract return type from syntax like 'ta.ema(source, length) -> series float'."""
    if not syntax:
        return ""
    m = re.search(r"(?:\u2192|->)\s*(.+)$", syntax)
    return m.group(1).strip() if m else ""


def _build_document_text(entry: dict[str, Any]) -> str:
    """Build the text that gets embedded for semantic search."""
    parts: list[str] = []
    name = entry.get("name", "")
    namespace = entry.get("namespace") or ""
    category = entry.get("category", "")
    parts.append(f"{category.upper()}: {name}")
    if namespace:
        parts.append(f"Namespace: {namespace}")
    syntax = entry.get("syntax") or ""
    if syntax:
        parts.append(f"Syntax: {syntax}")
    description = entry.get("description") or ""
    if description:
        parts.append(description)
    returns = entry.get("returns") or ""
    if returns:
        parts.append(f"Returns: {returns}")
    remarks = entry.get("remarks") or ""
    if remarks:
        parts.append(f"Remarks: {remarks}")
    examples = entry.get("examples") or []
    if examples:
        parts.append("EXAMPLES:")
        for ex in examples:
            parts.append(ex)
    see_also = entry.get("see_also") or []
    if see_also:
        parts.append("See also: " + ", ".join(str(s) for s in see_also))
    return "\n\n".join(parts)


def _flatten_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """Build flat metadata dict for ChromaDB storage."""
    meta: dict[str, Any] = {}
    meta["name"] = entry.get("name", "")
    meta["category"] = entry.get("category", "")
    meta["namespace"] = entry.get("namespace") or ""
    meta["syntax"] = entry.get("syntax") or ""
    raw_returns = entry.get("returns") or ""
    extracted = _extract_return_type(meta["syntax"])
    meta["returns"] = extracted if extracted else raw_returns
    prose = entry.get("_raw_returns_prose") or raw_returns
    meta["raw_returns_description"] = prose if prose != meta["returns"] else ""
    meta["remarks"] = entry.get("remarks") or ""
    meta["deprecated"] = 1 if entry.get("deprecated") else 0
    sources = entry.get("sources", [])
    meta["sources"] = ", ".join(sources) if isinstance(sources, list) else str(sources)
    meta["url"] = entry.get("url") or ""
    meta["scraped_at"] = entry.get("scraped_at") or ""
    examples = entry.get("examples") or []
    parameters = entry.get("parameters") or []
    overloads = entry.get("overloads") or []
    meta["has_examples"] = 1 if examples else 0
    meta["example_count"] = len(examples)
    meta["param_count"] = len(parameters)
    meta["overload_count"] = len(overloads)
    meta["raw_description"] = entry.get("description") or ""
    if isinstance(examples, list):
        meta["raw_examples"] = " ||| ".join(str(ex) for ex in examples)
    elif examples:
        meta["raw_examples"] = str(examples)
    else:
        meta["raw_examples"] = ""
    meta["raw_parameters"] = json.dumps(parameters, ensure_ascii=False) if parameters else ""
    meta["raw_overloads"] = json.dumps(overloads, ensure_ascii=False) if overloads else ""
    type_fields = entry.get("type_fields") or []
    meta["raw_type_fields"] = json.dumps(type_fields, ensure_ascii=False) if type_fields else ""
    see_also = entry.get("see_also") or []
    if isinstance(see_also, list):
        meta["raw_see_also"] = ", ".join(str(s) for s in see_also)
    else:
        meta["raw_see_also"] = str(see_also)
    meta["file"] = entry.get("file") or ""
    meta["heading"] = entry.get("heading") or ""
    return meta


def _upsert_batch(
    collection: Any,
    ids: list[str],
    docs: list[str],
    metas: list[dict[str, Any]],
    embeddings: list[list[float]],
) -> int:
    """Upsert one batch; split and retry on compaction errors."""
    if not ids:
        return 0
    try:
        collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embeddings)
        return len(ids)
    except Exception as e:
        if len(ids) <= 1:
            logger.error(f"Upsert failed for {ids[0]}: {e}")
            return 0
        mid = len(ids) // 2
        logger.warning(f"Upsert split {len(ids)} -> {mid}+{len(ids) - mid}: {e}")
        a = _upsert_batch(collection, ids[:mid], docs[:mid], metas[:mid], embeddings[:mid])
        b = _upsert_batch(collection, ids[mid:], docs[mid:], metas[mid:], embeddings[mid:])
        return a + b


def db_exists() -> bool:
    """Check if a usable ChromaDB collection exists at DB_PATH."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=DB_PATH)
        col = client.get_collection(name=COLLECTION)
        return col.count() > 0
    except Exception:
        return False


def build_db(force: bool = False) -> int:
    """Build ChromaDB from shipped JSON data. Returns entry count.

    Args:
        force: If True, wipe and rebuild. If False, only build if DB is empty/missing.
    """
    if not force and db_exists():
        logger.info(f"ChromaDB already exists at {DB_PATH} ({COLLECTION}), skipping build")
        return 0

    json_path = _data_json_path()
    if not json_path.exists():
        logger.error(f"PineScript data file not found: {json_path}")
        raise FileNotFoundError(f"Cannot build DB: {json_path} not found")

    logger.info(f"Building ChromaDB from {json_path}...")
    entries = json.loads(json_path.read_text(encoding="utf-8"))
    logger.info(f"Loaded {len(entries)} entries from JSON")

    import chromadb
    from sentence_transformers import SentenceTransformer

    logger.info(f"Loading embedding model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)
    logger.info("Embedding model loaded")

    client = chromadb.PersistentClient(path=DB_PATH)
    if force:
        try:
            client.delete_collection(name=COLLECTION)
            logger.info("Deleted existing collection")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )

    batch_size = 25
    total = len(entries)
    indexed = 0

    for i in range(0, total, batch_size):
        batch = entries[i : i + batch_size]
        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict[str, Any]] = []

        for entry in batch:
            name = entry.get("name", "").lower().strip().replace(" ", "").replace("()", "").replace("`", "")
            category = entry.get("category", "unknown")
            sources = entry.get("sources", [])
            if "user_docs" in sources or entry.get("source") == "user_docs":
                entry_id = f"doc_{category}_{name}"
            else:
                entry_id = f"{category}_{name}"
            entry["id"] = entry_id
            ids.append(entry_id)
            docs.append(_build_document_text(entry))
            metas.append(_flatten_metadata(entry))

        if docs:
            try:
                vecs = model.encode(docs, show_progress_bar=False)
                embeddings = [v.tolist() for v in vecs]
            except Exception as e:
                logger.error(f"Embedding failed on batch {i // batch_size + 1}: {e}")
                continue
            indexed += _upsert_batch(collection, ids, docs, metas, embeddings)

        if (i // batch_size + 1) % 10 == 0:
            logger.info(f"Progress: {min(i + batch_size, total)}/{total} entries processed")

    count = collection.count()
    logger.info(f"ChromaDB build complete: {count} entries indexed at {DB_PATH}")
    return count


def build_db_if_needed() -> bool:
    """Build DB if it doesn't exist. Returns True if DB is ready."""
    try:
        if db_exists():
            return True
        logger.info("ChromaDB not found - building from shipped data (first run)...")
        logger.info("This may take 30-60 seconds (embedding model download + indexing)")
        build_db(force=False)
        return True
    except Exception as e:
        logger.error(f"Auto-build failed: {e}")
        logger.info("You can build manually: pinescript-mcp build")
        return False
