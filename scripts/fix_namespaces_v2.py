#!/usr/bin/env python3
"""
fix_namespaces_v2.py
─────────────────────────────────────────────────────────────────────────────
BUG 3 FIX: Fix namespace doubling in ChromaDB entries.

Problem: Some entries have namespace "ta" and name "ta.valuewhen", which
causes display as "ta.ta.valuewhen". This script deduplicates consecutive
dot-separated parts in the name field relative to the namespace.

Also fixes:
  - metadata["name"] — remove namespace prefix if it matches the namespace field
  - metadata["namespace"] — ensure consistent lowercase, no trailing dots
  - metadata["syntax"] — fix doubled namespace prefixes in syntax strings
  - Document text — re-embed after fixing

Usage:
    python fix_namespaces_v2.py [--db PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from loguru import logger

logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<8} | {message}", level="INFO")

DEFAULT_DB = Path(__file__).parent / "pinescript_db"
COLLECTION_NAME = "pinescript_v6"
EMBED_MODEL = "all-MiniLM-L6-v2"


def _dedot_namespace(name: str, namespace: str) -> str:
    """Remove namespace prefix from name if it's already there.

    Examples:
        _dedot_namespace("ta.ema", "ta")       -> "ta.ema"  (keep — ns.name form is correct)
        _dedot_namespace("ta.ta.ema", "ta")     -> "ta.ema"  (remove duplicate)
        _dedot_namespace("strategy.long", "strategy") -> "strategy.long"
    """
    if not namespace or not name:
        return name

    ns = namespace.lower().rstrip(".")
    name_lower = name.lower()

    # Check for repeated namespace: "ta.ta.ema" with ns="ta"
    prefix = f"{ns}.{ns}."
    if name_lower.startswith(prefix):
        return name[len(prefix) - len(ns) - 1:]  # Keep "ta.ema" from "ta.ta.ema"

    return name


def _fix_syntax_doubling(syntax: str, namespace: str) -> str:
    """Fix doubled namespace in syntax strings.

    e.g. "ta.ta.ema(source, length)" -> "ta.ema(source, length)"
    """
    if not namespace or not syntax:
        return syntax

    ns = namespace.lower().rstrip(".")
    doubled = f"{ns}.{ns}."
    if doubled in syntax.lower():
        # Replace case-insensitively
        pattern = re.compile(re.escape(doubled), re.IGNORECASE)
        syntax = pattern.sub(f"{ns}.", syntax)

    return syntax


def _fix_entry(meta: dict[str, Any], doc: str) -> tuple[dict[str, Any], str, bool]:
    """Fix a single entry. Returns (fixed_meta, fixed_doc, changed)."""
    changed = False
    namespace = (meta.get("namespace") or "").lower().rstrip(".")
    name = meta.get("name", "")

    # Fix namespace trailing dots / case
    if meta.get("namespace") != namespace and namespace != "":
        meta["namespace"] = namespace
        changed = True

    # Fix doubled namespace in name
    fixed_name = _dedot_namespace(name, namespace)
    if fixed_name != name:
        logger.debug(f"  name: '{name}' -> '{fixed_name}'")
        meta["name"] = fixed_name
        changed = True

    # Fix doubled namespace in syntax
    syntax = meta.get("syntax", "")
    fixed_syntax = _fix_syntax_doubling(syntax, namespace)
    if fixed_syntax != syntax:
        logger.debug(f"  syntax: '{syntax}' -> '{fixed_syntax}'")
        meta["syntax"] = fixed_syntax
        changed = True

    # Fix raw_description if it has doubled namespace
    raw_desc = meta.get("raw_description", "")
    if raw_desc:
        fixed_desc = _fix_syntax_doubling(raw_desc, namespace)
        if fixed_desc != raw_desc:
            meta["raw_description"] = fixed_desc
            changed = True

    return meta, doc, changed


def fix_all(dry_run: bool = False, db_path: Path = DEFAULT_DB) -> dict[str, int]:
    """Fix all entries in the collection. Returns stats."""
    import chromadb

    logger.info(f"Connecting to ChromaDB at {db_path}")
    client = chromadb.PersistentClient(path=str(db_path))
    collection = client.get_collection(name=COLLECTION_NAME)
    total = collection.count()
    logger.info(f"Collection has {total} entries")

    # Fetch everything
    result = collection.get(include=["documents", "metadatas"])
    ids = result["ids"]
    docs = result["documents"]
    metas = result["metadatas"]

    stats = {"total": total, "fixed": 0, "unchanged": 0, "errors": 0}

    # Collect fixes
    fix_batch_ids: list[str] = []
    fix_batch_metas: list[dict] = []
    fix_batch_docs: list[str] = []

    for rid, doc, meta in zip(ids, docs, metas):
        try:
            fixed_meta, fixed_doc, changed = _fix_entry(meta.copy(), doc)
            if changed:
                stats["fixed"] += 1
                fix_batch_ids.append(rid)
                fix_batch_metas.append(fixed_meta)
                fix_batch_docs.append(fixed_doc)
        except Exception as e:
            logger.error(f"Error fixing {rid}: {e}")
            stats["errors"] += 1

    stats["unchanged"] = stats["total"] - stats["fixed"] - stats["errors"]

    logger.info(f"Fix results: {stats['fixed']} fixed, {stats['unchanged']} unchanged, {stats['errors']} errors")

    if dry_run:
        logger.info("DRY RUN — no changes written to DB")
        # Print some examples of what would be fixed
        for rid, meta in zip(fix_batch_ids[:20], fix_batch_metas[:20]):
            logger.info(f"  Would fix: {rid} -> name={meta.get('name')}")
        if len(fix_batch_ids) > 20:
            logger.info(f"  ... and {len(fix_batch_ids) - 20} more")
        return stats

    # Write fixes in batches of 100
    if fix_batch_ids:
        batch_size = 100
        from sentence_transformers import SentenceTransformer

        logger.info(f"Loading embedding model for re-embed of {len(fix_batch_ids)} fixed entries...")
        model = SentenceTransformer(EMBED_MODEL)

        for i in range(0, len(fix_batch_ids), batch_size):
            batch_ids = fix_batch_ids[i:i + batch_size]
            batch_metas = fix_batch_metas[i:i + batch_size]
            batch_docs = fix_batch_docs[i:i + batch_size]

            # Re-embed fixed documents
            vecs = model.encode(batch_docs, show_progress_bar=False)
            embeddings = [v.tolist() for v in vecs]

            collection.update(
                ids=batch_ids,
                metadatas=batch_metas,
                documents=batch_docs,
                embeddings=embeddings,
            )
            logger.info(f"  Updated batch {i // batch_size + 1}: {len(batch_ids)} entries")

    logger.info("Fix complete.")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Fix namespace doubling in ChromaDB entries")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="ChromaDB path")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed without writing")
    args = parser.parse_args()

    stats = fix_all(dry_run=args.dry_run, db_path=args.db)

    logger.info("=" * 60)
    logger.info("Fix Namespaces v2 — BUG 3 Fix")
    logger.info("=" * 60)

    print(f"\n{'=' * 60}")
    print(f"  FIX NAMESPACES v2 RESULTS")
    print(f"{'=' * 60}")
    print(f"  Total entries:  {stats['total']}")
    print(f"  Fixed:          {stats['fixed']}")
    print(f"  Unchanged:      {stats['unchanged']}")
    print(f"  Errors:         {stats['errors']}")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
