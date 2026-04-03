"""
index_docs.py
─────────────────────────────────────────────────────────────────────────────
Reads  pinescript_chunks.json  (produced by parse_docs.py) and stores every
entry in a persistent ChromaDB collection "pinescript_v6" using a local
SentenceTransformer embedding model.

Features:
  - Idempotent: existing IDs are skipped (or upserted atomically)
  - Batch upserts in groups of 50 with progress output
  - All JSON fields stored as ChromaDB metadata (lists → comma-separated)
  - Document text constructed to maximise semantic search quality

Usage:
    python index_docs.py [--chunks PATH] [--db PATH] [--reset]

Options:
    --chunks PATH   Path to pinescript_chunks.json  (default: ./pinescript_chunks.json)
    --db     PATH   Path for persistent ChromaDB     (default: ./pinescript_db)
    --reset         Wipe the collection and re-index from scratch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ── Loguru ───────────────────────────────────────────────────────────────────
from loguru import logger

logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<8} | {message}", level="INFO")

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_CHUNKS = Path(__file__).parent / "pinescript_chunks.json"
DEFAULT_DB     = Path(__file__).parent / "pinescript_db"
COLLECTION     = "pinescript_v6"
EMBED_MODEL    = "all-MiniLM-L6-v2"
BATCH_SIZE     = 50

# ─────────────────────────────────────────────────────────────────────────────
# Metadata flattening
# ─────────────────────────────────────────────────────────────────────────────

def _to_meta_value(v: Any) -> str | int | float | bool:
    """
    ChromaDB metadata values must be scalar.
    Convert lists → comma-separated strings; None → ""; dicts → JSON string.
    """
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, list):
        # Each list item might itself be a dict (e.g. parameters)
        stringified = []
        for item in v:
            if isinstance(item, dict):
                stringified.append(json.dumps(item, ensure_ascii=False))
            else:
                stringified.append(str(item) if item is not None else "")
        return ", ".join(stringified)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def flatten_metadata(entry: dict) -> dict:
    """
    Build a flat metadata dict from an entry, excluding 'raw_text'
    (stored separately as the ChromaDB document) and 'examples'
    (too long for metadata; stored as a joined string up to 2000 chars).
    """
    skip = {"raw_text"}
    meta: dict[str, Any] = {}
    for key, val in entry.items():
        if key in skip:
            continue
        if key == "examples":
            joined = " ||| ".join(val) if isinstance(val, list) else (val or "")
            meta["examples"] = joined[:4000]  # ChromaDB metadata size limit
        else:
            meta[key] = _to_meta_value(val)
    return meta


# ─────────────────────────────────────────────────────────────────────────────
# Document text construction
# ─────────────────────────────────────────────────────────────────────────────

def build_document_text(entry: dict) -> str:
    """
    Construct the text that will be embedded for semantic search.
    Includes name, description, syntax, returns, remarks, and examples
    so that all meaningful content is searchable.
    """
    parts: list[str] = []

    # Name + category
    parts.append(f"{entry['category'].upper()}: {entry['name']}")

    if entry.get("namespace"):
        parts.append(f"Namespace: {entry['namespace']}")

    if entry.get("syntax"):
        parts.append(f"Syntax: {entry['syntax']}")

    if entry.get("description"):
        parts.append(entry["description"])

    if entry.get("returns"):
        parts.append(f"Returns: {entry['returns']}")

    if entry.get("remarks"):
        parts.append(f"Remarks: {entry['remarks']}")

    # Parameters
    for p in entry.get("parameters") or []:
        if isinstance(p, dict):
            pname = p.get("name", "")
            ptype = p.get("type", "")
            pdesc = p.get("description", "")
            parts.append(f"Param {pname} ({ptype}): {pdesc}")

    # Examples (first 1 only — keeps document focused)
    examples = entry.get("examples") or []
    if examples:
        parts.append(f"Example:\n{examples[0]}")

    # See also
    if entry.get("see_also"):
        see = entry["see_also"]
        if isinstance(see, list):
            parts.append("See also: " + ", ".join(see))

    return "\n\n".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────────────────────
# Indexing
# ─────────────────────────────────────────────────────────────────────────────

def index_docs(
    chunks_path: Path,
    db_path: Path,
    reset: bool = False,
) -> None:
    # ── Load chunks ──────────────────────────────────────────────────────
    logger.info(f"Loading chunks from {chunks_path}")
    if not chunks_path.exists():
        logger.error(f"Chunks file not found: {chunks_path}")
        sys.exit(1)

    with open(chunks_path, "r", encoding="utf-8") as f:
        entries: list[dict] = json.load(f)

    logger.info(f"Loaded {len(entries)} entries")

    # ── Load embedding model ─────────────────────────────────────────────
    logger.info(f"Loading embedding model: {EMBED_MODEL}")
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBED_MODEL)
        logger.info("Embedding model loaded")
    except Exception as e:
        logger.error(f"Failed to load embedding model: {e}")
        sys.exit(1)

    # ── Init ChromaDB ────────────────────────────────────────────────────
    logger.info(f"Initializing ChromaDB at {db_path}")
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(db_path))
    except Exception as e:
        logger.error(f"Failed to initialize ChromaDB: {e}")
        sys.exit(1)

    # ── Collection setup ─────────────────────────────────────────────────
    if reset:
        logger.warning(f"--reset flag: deleting existing collection '{COLLECTION}'")
        try:
            client.delete_collection(name=COLLECTION)
        except Exception:
            pass  # Collection may not exist yet

    try:
        collection = client.get_or_create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(f"Collection '{COLLECTION}' ready (current count: {collection.count()})")
    except Exception as e:
        logger.error(f"Failed to get/create collection: {e}")
        sys.exit(1)

    # ── Determine which IDs already exist ────────────────────────────────
    existing_ids: set[str] = set()
    if not reset:
        try:
            existing_result = collection.get(include=[])  # Only fetch IDs
            existing_ids = set(existing_result["ids"])
            logger.info(f"Already indexed: {len(existing_ids)} entries — will skip these")
        except Exception as e:
            logger.warning(f"Could not fetch existing IDs: {e} — will upsert all")

    # ── Filter to new entries ─────────────────────────────────────────────
    new_entries = [e for e in entries if e["id"] not in existing_ids]
    logger.info(f"New entries to index: {len(new_entries)}")

    if not new_entries:
        logger.info("Nothing to index. Database is up to date.")
        _print_final_stats(collection)
        return

    # ── Batch upsert ─────────────────────────────────────────────────────
    total_batches = (len(new_entries) + BATCH_SIZE - 1) // BATCH_SIZE
    indexed_count = 0

    for batch_num in range(total_batches):
        batch = new_entries[batch_num * BATCH_SIZE : (batch_num + 1) * BATCH_SIZE]

        ids:       list[str]        = []
        docs:      list[str]        = []
        metas:     list[dict]       = []
        embeddings: list[list[float]] = []

        for entry in batch:
            doc_text = build_document_text(entry)
            ids.append(entry["id"])
            docs.append(doc_text)
            metas.append(flatten_metadata(entry))

        # Compute embeddings for the whole batch at once
        try:
            vecs = model.encode(docs, show_progress_bar=False)
            embeddings = [v.tolist() for v in vecs]
        except Exception as e:
            logger.error(f"Embedding failed on batch {batch_num + 1}: {e}")
            continue

        try:
            collection.upsert(
                ids=ids,
                documents=docs,
                metadatas=metas,
                embeddings=embeddings,
            )
            indexed_count += len(batch)
        except Exception as e:
            logger.error(f"Upsert failed on batch {batch_num + 1}: {e}")
            continue

        progress_pct = (batch_num + 1) / total_batches * 100
        logger.info(
            f"  Batch {batch_num + 1}/{total_batches}  "
            f"({indexed_count}/{len(new_entries)} entries)  "
            f"{progress_pct:.0f}%"
        )

    logger.info(f"Indexing complete. Indexed {indexed_count} new entries.")
    _print_final_stats(collection)


def _print_final_stats(collection) -> None:
    try:
        total = collection.count()
    except Exception:
        total = "unknown"
    print("\n" + "═" * 60)
    print("  CHROMADB INDEX STATS")
    print("═" * 60)
    print(f"  Collection : {COLLECTION}")
    print(f"  Total docs : {total}")
    print(f"  DB path    : {DEFAULT_DB}")
    print(f"  Model      : {EMBED_MODEL}")
    print("═" * 60 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Index PineScript v6 docs into ChromaDB"
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=DEFAULT_CHUNKS,
        help=f"Path to pinescript_chunks.json (default: {DEFAULT_CHUNKS})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path for persistent ChromaDB (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the collection and re-index from scratch",
    )
    args = parser.parse_args()

    index_docs(
        chunks_path=args.chunks,
        db_path=args.db,
        reset=args.reset,
    )
