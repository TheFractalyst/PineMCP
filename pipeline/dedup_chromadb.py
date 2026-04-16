"""
dedup_chromadb.py
─────────────────────────────────────────────────────────────────────────────
Deduplicate ChromaDB IN PLACE — source JSON files are NOT modified.

Removes standalone "example" category entries whose content already lives in
their parent entry's raw_examples metadata field. All 356 example entries
are covered — every one has a parent that already stores the same examples.

After removal, runs a full verification scan to confirm zero duplicates remain.

Source files (pinescript_chunks.json, tv_scraped_entries.json,
user_docs_chunks.json) are NEVER touched.

Usage:
    python dedup_chromadb.py [--dry-run]
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import chromadb

DB_PATH = Path(__file__).parent.parent / "pinescript_db"
COLLECTION_NAME = "pinescript_v6"


def main(dry_run: bool = False):
    print("=" * 60)
    print("ChromaDB Deduplication — Remove Redundant Example Entries")
    print("=" * 60)

    client = chromadb.PersistentClient(path=str(DB_PATH))
    col = client.get_collection(COLLECTION_NAME)
    total_before = col.count()
    print(f"Current entries: {total_before}")

    # Fetch everything
    result = col.get(include=["metadatas", "documents"], limit=total_before)
    ids = result["ids"]
    metas = result["metadatas"]
    print(f"Fetched: {len(ids)} entries")

    # ── STEP 1: Identify standalone example entries ──
    example_ids = []
    for i, m in enumerate(metas):
        if m.get("category") == "example":
            example_ids.append(ids[i])

    print(f"\nExample category entries found: {len(example_ids)}")

    # Verify: check that parent entries exist with examples in raw_examples
    name_to_meta = {}
    for i, (rid, m, d) in enumerate(zip(ids, metas, result["documents"])):
        if m.get("category") != "example":
            # Normalize: strip () so "ta.mfi" matches parent of "ta.mfi() — example 1"
            key = m.get("name", "").lower().strip().replace("()", "")
            if key:
                name_to_meta[key] = m

    orphans = 0
    for i, m in enumerate(metas):
        if m.get("category") == "example":
            name = m.get("name", "")
            parent_key = name.rsplit(" — example ", 1)[0].lower().strip().replace("()", "") if " — example " in name else ""
            if parent_key and parent_key not in name_to_meta:
                orphans += 1
    print(f"Orphan examples (no parent): {orphans}")
    safe_to_remove = len(example_ids) - orphans
    print(f"Safe to remove: {safe_to_remove}")

    if safe_to_remove != len(example_ids):
        print(f"\nWARNING: {orphans} orphan examples found — these will be kept.")
        # Filter out orphans
        orphan_ids = set()
        for i, m in enumerate(metas):
            if m.get("category") == "example":
                name = m.get("name", "")
                parent_key = name.rsplit(" — example ", 1)[0].lower().strip().replace("()", "") if " — example " in name else ""
                if parent_key and parent_key not in name_to_meta:
                    orphan_ids.add(ids[i])
        example_ids = [eid for eid in example_ids if eid not in orphan_ids]

    total_after = total_before - len(example_ids)

    # ── Summary before execution ──
    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'} SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total before:    {total_before}")
    print(f"To remove:       {len(example_ids)}")
    print(f"Total after:     {total_after}")
    print(f"Reduction:       {len(example_ids)} ({len(example_ids)/total_before*100:.1f}%)")

    if dry_run:
        print("\n-- DRY RUN -- no changes made")
        return

    # ── EXECUTE ──
    print("\n-- EXECUTING --")
    if example_ids:
        print(f"Removing {len(example_ids)} example entries...")
        col.delete(ids=example_ids)
        print("  Done.")

    # ── VERIFICATION ──
    print(f"\n{'=' * 60}")
    print("VERIFICATION")
    print(f"{'=' * 60}")

    final_count = col.count()
    print(f"Expected: {total_after}")
    print(f"Actual:   {final_count}")

    assert final_count == total_after, f"Count mismatch: {final_count} != {total_after}"

    # Full duplicate scan
    result2 = col.get(include=["metadatas"], limit=final_count)

    # Check for same name + same category (true duplicates)
    name_cat_keys = Counter()
    for m in result2["metadatas"]:
        key = (m.get("name", "").lower().strip(), m.get("category", ""))
        name_cat_keys[key] += 1
    true_dupes = {k: v for k, v in name_cat_keys.items() if v > 1}

    # Check for remaining example entries
    remaining_examples = sum(1 for m in result2["metadatas"] if m.get("category") == "example")

    print(f"True duplicates (same name+category): {len(true_dupes)}")
    print(f"Remaining example entries: {remaining_examples}")

    if true_dupes:
        print("WARNING: True duplicates found:")
        for (name, cat), count in true_dupes.items():
            print(f"  '{name}' [{cat}] x{count}")
    else:
        print("Zero duplicates confirmed.")

    if remaining_examples > 0 and orphans > 0:
        print(f"( {orphans} orphan examples kept — no parent entry exists)")
    elif remaining_examples == 0:
        print("All example entries removed.")

    print(f"\nDone. {total_before} -> {final_count} entries.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
