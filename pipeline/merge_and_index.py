"""
merge_and_index.py
─────────────────────────────────────────────────────────────────────────────
STAGE 3: Merge local docs (1,242 entries) + TradingView live scrape + user
documentation chunks into a unified ChromaDB collection. One collection. All data.

INPUT FILES:
  - pinescript_chunks.json     (local, already exists)
  - tv_scraped_entries.json    (live scrape, from scrape_entries.py)
  - user_docs_chunks.json      (user docs, from index_user_docs.py)

Usage:
    python merge_and_index.py [--local FILE] [--live FILE] [--docs FILE] [--db PATH]
    python merge_and_index.py --reset
    python merge_and_index.py --dry-run

Options:
    --local    Local chunks file         (default: pinescript_chunks.json)
    --live     Live scrape file          (default: tv_scraped_entries.json)
    --docs     User docs chunks file     (default: user_docs_chunks.json)
    --db       ChromaDB path             (default: ./pinescript_db)
    --reset    Wipe collection and re-index from scratch
    --dry-run  Print merge stats without writing to DB
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from loguru import logger

logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<8} | {message}", level="INFO")

ROOT = Path(__file__).parent.parent
DEFAULT_LOCAL = ROOT / "data" / "pinescript_chunks.json"
DEFAULT_LIVE = ROOT / "data" / "tv_scraped_entries.json"
DEFAULT_DOCS = ROOT / "data" / "user_docs_chunks.json"
DEFAULT_DB = ROOT / "pinescript_db"
COLLECTION_NAME = "pinescript_v6"
EMBED_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 50


def normalize_key(entry: dict[str, Any]) -> str:
    """Normalize entry name for deduplication matching.

    Uses name + category to disambiguate entries that exist as both
    function and variable (e.g., dayofmonth() vs dayofmonth).
    """
    name = entry.get("name", "")
    category = entry.get("category", "")
    base = name.lower().strip().replace(" ", "").replace("()", "").replace("`", "")
    return f"{base}__{category}" if category else base


def deduplicate_examples(examples: list[str]) -> list[str]:
    """Deduplicate example code blocks by content hash (deterministic)."""
    import hashlib
    seen: set[str] = set()
    result: list[str] = []
    for ex in examples:
        h = hashlib.sha256(ex.encode()).hexdigest()
        if h not in seen:
            seen.add(h)
            result.append(ex)
    return result


def merge_entries(
    local_entries: list[dict[str, Any]],
    live_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge local and live entries into unified list."""
    logger.info(f"Local entries:  {len(local_entries)}")
    logger.info(f"Live entries:   {len(live_entries)}")

    # Build lookup maps keyed by normalized name
    local_map: dict[str, dict[str, Any]] = {}
    for entry in local_entries:
        key = normalize_key(entry)
        local_map[key] = entry

    live_map: dict[str, dict[str, Any]] = {}
    for entry in live_entries:
        key = normalize_key(entry)
        live_map[key] = entry

    all_keys = set(local_map.keys()) | set(live_map.keys())
    logger.info(f"Unique keys:    {len(all_keys)}")

    merged: list[dict[str, Any]] = []
    stats = {"both": 0, "local_only": 0, "live_only": 0}

    for key in all_keys:
        in_local = key in local_map
        in_live = key in live_map

        if in_local and in_live:
            stats["both"] += 1
            local_entry = local_map[key]
            live_entry = live_map[key]

            merged_entry: dict[str, Any] = {}
            merged_entry.update(local_entry)
            merged_entry.update(live_entry)

            # Preserve richer documentation: if local had more content, keep it
            local_doc = (local_entry.get("description") or "") + (local_entry.get("syntax") or "")
            live_doc = (live_entry.get("description") or "") + (live_entry.get("syntax") or "")
            if len(local_doc) > len(live_doc):
                for field in ("description", "syntax", "remarks", "returns"):
                    if local_entry.get(field) and (
                        not live_entry.get(field)
                        or len(local_entry[field]) > len(live_entry.get(field, ""))
                    ):
                        merged_entry[field] = local_entry[field]

            # Examples: concatenate and deduplicate
            local_examples = local_entry.get("examples", [])
            live_examples = live_entry.get("examples", [])
            merged_entry["examples"] = deduplicate_examples(
                local_examples + live_examples
            )

            # Parameters: prefer live (more accurate types)
            merged_entry["parameters"] = (
                live_entry.get("parameters")
                or local_entry.get("parameters")
                or []
            )

            # See also: union
            local_see = local_entry.get("see_also", [])
            live_see = live_entry.get("see_also", [])
            merged_entry["see_also"] = list(set(local_see + live_see))

            # Overloads: prefer live
            merged_entry["overloads"] = (
                live_entry.get("overloads")
                or local_entry.get("overloads")
                or []
            )

            merged_entry["sources"] = ["local_docs", "tradingview_live"]
            merged_entry["id"] = f"merged_{key}"

        elif in_local:
            stats["local_only"] += 1
            merged_entry = local_map[key].copy()
            merged_entry["sources"] = ["local_docs"]

        else:
            stats["live_only"] += 1
            merged_entry = live_map[key].copy()
            merged_entry["sources"] = ["tradingview_live"]

        merged.append(merged_entry)

    logger.info(f"Merge stats: {stats}")
    cat_counts = Counter(e.get("category", "unknown") for e in merged)
    for cat, n in sorted(cat_counts.items()):
        logger.info(f"  {cat:<15} {n:>5}")
    logger.info(f"Total merged: {len(merged)}")

    return merged


def _base_name(name: str) -> str:
    """Strip trailing () and lowercase for cross-category matching."""
    return name.lower().strip().replace(" ", "").replace("()", "").replace("`", "")


def enrich_truncated_syntax(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cross-reference entries sharing a base name to fill truncated syntax/returns.

    The local docs parser produces entries like:
        name='map.put()', syntax='map.put()', returns='prose...', category='function'
    while the live scrape produces:
        name='map.put', syntax='map.put(id, key, value) → <value_type>', category='variable'

    This function finds such pairs and copies the full syntax + extracted return type
    into the truncated entry, preserving the original prose in 'returns' as a fallback.
    """
    # Group by base name
    from collections import defaultdict
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        base = _base_name(entry.get("name", ""))
        groups[base].append(entry)

    enriched = 0
    for base, group in groups.items():
        if len(group) < 2:
            continue

        # Find the entry with the richest syntax (has arrow)
        best_syntax = ""
        best_syntax_entry = None
        for e in group:
            syn = e.get("syntax") or ""
            if ("→" in syn or "->" in syn) and len(syn) > len(best_syntax):
                best_syntax = syn
                best_syntax_entry = e

        if not best_syntax:
            continue

        # Enrich truncated siblings
        extracted_return = _extract_return_type_from_syntax(best_syntax)
        for e in group:
            syn = e.get("syntax") or ""
            has_arrow = "→" in syn or "->" in syn
            if not has_arrow and "(" in syn:
                # This entry has truncated syntax — enrich it
                old_returns = e.get("returns", "")

                # Copy full syntax
                e["syntax"] = best_syntax

                # Set returns: prefer extracted type, fall back to existing
                if extracted_return:
                    e["returns"] = extracted_return
                    if old_returns and old_returns != extracted_return:
                        e["_raw_returns_prose"] = old_returns
                elif not e.get("returns"):
                    e["returns"] = ""

                # Copy parameters if missing
                if not e.get("parameters") and best_syntax_entry and best_syntax_entry.get("parameters"):
                    e["parameters"] = best_syntax_entry["parameters"]

                enriched += 1

    if enriched:
        logger.info(f"Enriched {enriched} entries with cross-category syntax/returns")
    return entries


def build_document_text(entry: dict[str, Any]) -> str:
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
        for ex in examples:  # Embed all examples for search
            parts.append(ex)

    see_also = entry.get("see_also") or []
    if see_also:
        parts.append("See also: " + ", ".join(str(s) for s in see_also))

    return "\n\n".join(parts)


def _extract_return_type_from_syntax(syntax: str) -> str:
    """Extract the return type annotation from a syntax string like 'ta.ema(source, length) → series float'.

    Returns the type string (e.g., 'series float') or empty string if not found.
    Handles:
      - 'func(a, b) → series float'
      - 'func(a, b) → [series float, series float, series float]'
      - 'func() → void'
    """
    if not syntax:
        return ""
    # Match → or -> followed by the type annotation
    arrow_match = re.search(r"(?:→|->)\s*(.+)$", syntax)
    if arrow_match:
        return arrow_match.group(1).strip()
    return ""


def flatten_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """Build flat metadata dict for ChromaDB storage."""
    meta: dict[str, Any] = {}

    meta["name"] = entry.get("name", "")
    meta["category"] = entry.get("category", "")
    meta["namespace"] = entry.get("namespace") or ""
    meta["syntax"] = entry.get("syntax") or ""

    # Returns field: prefer type annotation from syntax → over prose description
    raw_returns = entry.get("returns") or ""
    syntax = meta["syntax"]
    extracted_type = _extract_return_type_from_syntax(syntax)
    if extracted_type:
        meta["returns"] = extracted_type
    else:
        meta["returns"] = raw_returns
    # Store original prose for display in lookup tools
    # Check both the 'returns' field and the enrichment-injected '_raw_returns_prose'
    prose = entry.get("_raw_returns_prose") or raw_returns
    meta["raw_returns_description"] = prose if prose != meta["returns"] else ""

    meta["remarks"] = entry.get("remarks") or ""

    # Booleans as int (ChromaDB compatible)
    meta["deprecated"] = 1 if entry.get("deprecated") else 0

    # Sources
    sources = entry.get("sources", [])
    meta["sources"] = ", ".join(sources) if isinstance(sources, list) else str(sources)

    # URL
    meta["url"] = entry.get("url") or ""

    # Timestamp
    meta["scraped_at"] = entry.get("scraped_at") or ""

    # Counts
    examples = entry.get("examples") or []
    parameters = entry.get("parameters") or []
    overloads = entry.get("overloads") or []
    meta["has_examples"] = 1 if examples else 0
    meta["example_count"] = len(examples)
    meta["param_count"] = len(parameters)
    meta["overload_count"] = len(overloads)

    # Long text fields (for retrieval, not embedding)
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

    # User docs specific fields
    meta["file"] = entry.get("file") or ""
    meta["heading"] = entry.get("heading") or ""

    return meta


def index_to_chromadb(
    entries: list[dict[str, Any]],
    db_path: Path,
    reset: bool = False,
) -> None:
    """Index merged entries into ChromaDB."""
    logger.info(f"Loading embedding model: {EMBED_MODEL}")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(EMBED_MODEL)
    logger.info("Embedding model loaded")

    logger.info(f"Connecting to ChromaDB at {db_path}")
    import chromadb
    client = chromadb.PersistentClient(path=str(db_path))

    if reset:
        try:
            client.delete_collection(name=COLLECTION_NAME)
            logger.info("Deleted existing collection")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    logger.info(f"Collection '{COLLECTION_NAME}' ready (count: {collection.count()})")

    # Determine which IDs already exist
    existing_ids: set[str] = set()
    if not reset:
        try:
            result = collection.get(include=[])
            existing_ids = set(result["ids"])
            logger.info(f"Already indexed: {len(existing_ids)} entries")
        except Exception:
            pass

    # Filter to new entries
    new_entries = [e for e in entries if e.get("id", "") not in existing_ids]
    logger.info(f"New entries to index: {len(new_entries)}")

    if not new_entries:
        logger.info("Nothing to index. Database is up to date.")
        _print_stats(collection)
        return

    # Batch upsert
    total_batches = (len(new_entries) + BATCH_SIZE - 1) // BATCH_SIZE
    indexed_count = 0

    from tqdm import tqdm

    for batch_num in tqdm(range(total_batches), desc="Indexing"):
        batch = new_entries[batch_num * BATCH_SIZE : (batch_num + 1) * BATCH_SIZE]

        ids: list[str] = []
        docs: list[str] = []
        metas: list[dict[str, Any]] = []
        embeddings: list[list[float]] = []

        for entry in batch:
            entry_id = entry.get("id", "")
            if not entry_id:
                continue

            # Ensure unique ID by using category + name (source IDs can have collisions)
            name = entry.get("name", "").lower().strip().replace(" ", "").replace("()", "").replace("`", "")
            category = entry.get("category", "unknown")

            # Use separate ID prefix for user_docs to avoid collisions with API entries
            sources = entry.get("sources", [])
            if "user_docs" in sources or entry.get("source") == "user_docs":
                entry_id = f"doc_{category}_{name}"
            else:
                entry_id = f"{category}_{name}"
            # Store back for reference
            entry["id"] = entry_id

            doc_text = build_document_text(entry)
            meta = flatten_metadata(entry)

            ids.append(entry_id)
            docs.append(doc_text)
            metas.append(meta)

        # Compute embeddings
        if docs:
            try:
                vecs = model.encode(docs, show_progress_bar=False)
                embeddings = [v.tolist() for v in vecs]
            except Exception as e:
                logger.error(f"Embedding failed on batch {batch_num + 1}: {e}")
                continue

        # Upsert
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

    logger.info(f"Indexing complete. Indexed {indexed_count} new entries.")
    _print_stats(collection)


def _print_stats(collection) -> None:
    try:
        total = collection.count()
    except Exception:
        total = "unknown"

    print("\n" + "=" * 60)
    print("  CHROMADB INDEX STATS")
    print("=" * 60)
    print(f"  Collection : {COLLECTION_NAME}")
    print(f"  Total docs : {total}")
    print(f"  DB path    : {DEFAULT_DB}")
    print(f"  Model      : {EMBED_MODEL}")
    print("=" * 60 + "\n")


def verify_index(db_path: Path) -> None:
    """Run spot-check queries to verify the index."""
    import chromadb
    from sentence_transformers import SentenceTransformer

    client = chromadb.PersistentClient(path=str(db_path))
    collection = client.get_or_create_collection(name=COLLECTION_NAME)

    model = SentenceTransformer(EMBED_MODEL)

    print("\n" + "=" * 60)
    print("  POST-INDEX VERIFICATION")
    print("=" * 60)

    total = collection.count()
    print(f"\n  Total entries in DB: {total}")

    # Category breakdown
    try:
        all_result = collection.get(include=["metadatas"])
        all_metas = all_result.get("metadatas", [])
        cat_counts = Counter(m.get("category", "unknown") for m in all_metas)
        print("\n  By category:")
        for cat, count in sorted(cat_counts.items()):
            print(f"    {cat:<15} {count:>5}")
    except Exception as e:
        print(f"\n  Could not get category breakdown: {e}")

    # Source breakdown
    try:
        source_counts = Counter(m.get("sources", "") for m in all_metas)
        print("\n  By source:")
        for src, count in sorted(source_counts.items()):
            print(f"    {src:<40} {count:>5}")
    except Exception:
        pass

    # Spot checks
    spot_checks = [
        ("ta.ema", "function"),
        ("strategy.entry", "function"),
        ("close", "variable"),
        ("array", "type"),
        ("color.red", "constant"),
    ]

    print("\n  Spot checks:")
    for query, expected_cat in spot_checks:
        try:
            vec = model.encode([query])[0].tolist()
            results = collection.query(
                query_embeddings=[vec],
                n_results=3,
                include=["metadatas", "documents"],
            )
            if results["ids"] and results["ids"][0]:
                top_name = results["metadatas"][0][0].get("name", "?")
                top_cat = results["metadatas"][0][0].get("category", "?")
                match = "OK" if expected_cat in top_cat or query in top_name.lower() else "MISMATCH"
                print(f"    '{query}' -> {top_name} ({top_cat}) [{match}]")
            else:
                print(f"    '{query}' -> NO RESULTS")
        except Exception as e:
            print(f"    '{query}' -> ERROR: {e}")

    print("\n" + "=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Merge local + live PineScript docs and index into ChromaDB"
    )
    parser.add_argument(
        "--local", type=Path, default=DEFAULT_LOCAL,
        help=f"Local chunks file (default: {DEFAULT_LOCAL})",
    )
    parser.add_argument(
        "--live", type=Path, default=DEFAULT_LIVE,
        help=f"Live scrape file (default: {DEFAULT_LIVE})",
    )
    parser.add_argument(
        "--docs", type=Path, default=DEFAULT_DOCS,
        help=f"User docs chunks file (default: {DEFAULT_DOCS})",
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"ChromaDB path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Wipe collection and re-index from scratch",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print merge stats without writing to DB",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("PineScript v6 Merge & Index")
    logger.info("=" * 60)

    # Load local entries
    local_entries: list[dict[str, Any]] = []
    if args.local.exists():
        local_entries = json.loads(args.local.read_text(encoding="utf-8"))
        logger.info(f"Loaded {len(local_entries)} local entries from {args.local}")
    else:
        logger.warning(f"Local file not found: {args.local}")

    # Load live entries
    live_entries: list[dict[str, Any]] = []
    if args.live.exists():
        live_entries = json.loads(args.live.read_text(encoding="utf-8"))
        logger.info(f"Loaded {len(live_entries)} live entries from {args.live}")
    else:
        logger.warning(f"Live scrape file not found: {args.live}")

    # Load user docs
    docs_entries: list[dict[str, Any]] = []
    if args.docs.exists():
        docs_entries = json.loads(args.docs.read_text(encoding="utf-8"))
        logger.info(f"User docs:       {len(docs_entries)}")
    else:
        logger.warning(f"User docs file not found: {args.docs}")

    if not local_entries and not live_entries and not docs_entries:
        logger.error("No entries to merge. Provide at least one input file.")
        sys.exit(1)

    # Merge local + live
    merged = merge_entries(local_entries, live_entries)

    # Enrich truncated entries with cross-category syntax/returns
    merged = enrich_truncated_syntax(merged)

    # Add user docs entries with source tagging
    if docs_entries:
        logger.info(f"Merging {len(docs_entries)} user doc chunks...")
        for doc_entry in docs_entries:
            entry = doc_entry.copy()
            entry["sources"] = ["user_docs"]
            entry["id"] = f"doc_{entry.get('name', '')}"
            merged.append(entry)
        logger.info(f"Total after docs: {len(merged)}")

    if args.dry_run:
        logger.info("Dry run — skipping ChromaDB indexing.")
        return

    # Index
    index_to_chromadb(merged, args.db, reset=args.reset)

    # Verify
    verify_index(args.db)

    logger.info("Done.")


if __name__ == "__main__":
    main()
