"""
dedup_chromadb.py
─────────────────────────────────────────────────────────────────────────────
Deduplicate ChromaDB IN PLACE — source JSON files are NOT modified.

Two operations:
  1. Remove 356 standalone "example" entries whose content already lives in
     their parent entry's raw_examples metadata field.
  2. Merge 253 cross-category duplicate groups (same name, different category)
     into a single entry per name, combining sources and keeping the richest
     metadata from each variant.

Source files (pinescript_chunks.json, tv_scraped_entries.json,
user_docs_chunks.json) are NEVER touched.

Usage:
    python dedup_chromadb.py [--dry-run]
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import chromadb

logger_removed = False

DB_PATH = Path(__file__).parent.parent / "pinescript_db"
COLLECTION_NAME = "pinescript_v6"


def merge_metadata(metas: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge multiple metadata dicts for the same name into one best entry.

    Strategy:
      - category: prefer local_docs classification (more accurate) over
        tradingview_live (which defaults everything to "function").
        Priority: type > keyword > operator > constant > variable > function
        (higher-specificity categories win, local_docs source wins ties).
      - sources: union of all sources
      - raw_description: longest non-empty one
      - raw_examples: concatenate and dedup
      - syntax: longest non-empty one
      - returns: longest non-empty one
      - remarks: concatenate
      - raw_parameters: prefer live (more accurate types)
      - raw_see_also: union
      - url: first non-empty
      - other fields: keep first non-empty value
    """
    # Category priority (lower = higher priority)
    CAT_PRIORITY = {
        "type": 1, "keyword": 2, "operator": 3, "constant": 4,
        "variable": 5, "annotation": 6, "function": 7,
    }

    # Source priority for category classification
    SRC_PRIORITY = {
        "local_docs": 1,
        "local_docs, tradingview_live": 2,
        "tradingview_live": 3,
        "user_docs": 4,
    }

    def src_priority(meta: dict) -> int:
        return SRC_PRIORITY.get(meta.get("sources", ""), 99)

    def cat_priority(meta: dict) -> int:
        return CAT_PRIORITY.get(meta.get("category", ""), 99)

    # Pick category from the entry with the best source (local_docs is most accurate)
    best_cat_entry = min(metas, key=lambda m: (src_priority(m), cat_priority(m)))
    merged: dict[str, Any] = {"category": best_cat_entry.get("category", "")}

    # Sources: union
    all_sources = set()
    for m in metas:
        for s in m.get("sources", "").split(","):
            s = s.strip()
            if s:
                all_sources.add(s)
    merged["sources"] = ", ".join(sorted(all_sources))

    # Simple fields: longest non-empty value wins
    for field in ("raw_description", "syntax", "returns", "url", "namespace"):
        best = ""
        for m in metas:
            val = (m.get(field) or "").strip()
            if len(val) > len(best):
                best = val
        if best:
            merged[field] = best

    # Remarks: concatenate unique
    all_remarks = []
    for m in metas:
        r = (m.get("remarks") or "").strip()
        if r and r not in all_remarks:
            all_remarks.append(r)
    if all_remarks:
        merged["remarks"] = "\n\n".join(all_remarks)

    # raw_examples: concatenate and dedup by first 80 chars
    all_examples = []
    seen_prefixes: set[str] = set()
    for m in metas:
        raw_ex = m.get("raw_examples", "")
        if not raw_ex:
            continue
        for block in raw_ex.split(" ||| "):
            block = block.strip()
            if not block:
                continue
            key = block[:80].lower()
            if key not in seen_prefixes:
                seen_prefixes.add(key)
                all_examples.append(block)
    if all_examples:
        merged["raw_examples"] = " ||| ".join(all_examples)
        merged["example_count"] = len(all_examples)
        merged["has_examples"] = 1

    # raw_parameters: prefer live source (more accurate types)
    best_params = ""
    for m in sorted(metas, key=src_priority):
        p = (m.get("raw_parameters") or "").strip()
        if p and len(p) > len(best_params):
            best_params = p
    if best_params:
        merged["raw_parameters"] = best_params
        try:
            params = json.loads(best_params)
            merged["param_count"] = len(params)
        except (json.JSONDecodeError, TypeError):
            merged["param_count"] = 0

    # raw_see_also: union
    all_see = set()
    for m in metas:
        sa = (m.get("raw_see_also") or "").strip()
        if sa:
            for item in sa.split(","):
                item = item.strip()
                if item:
                    all_see.add(item)
    if all_see:
        merged["raw_see_also"] = ", ".join(sorted(all_see))

    # raw_type_fields: keep if any entry has it
    for m in metas:
        tf = m.get("raw_type_fields", "")
        if tf:
            merged["raw_type_fields"] = tf
            break

    # Copy any remaining scalar fields from the first entry that has them
    carryover_fields = {"scraped_at", "type"}
    for field in carryover_fields:
        for m in metas:
            if field in m:
                merged[field] = m[field]
                break

    return merged


def merge_documents(metas: list[dict[str, Any]], docs: list[str]) -> str:
    """Merge documents — keep the longest one (it has the most content)."""
    if not docs:
        return ""
    # Pick the longest document as base
    best_idx = max(range(len(docs)), key=lambda i: len(docs[i]))
    return docs[best_idx]


def build_merged_document(name: str, meta: dict[str, Any], doc: str) -> str:
    """Rebuild a clean document from merged metadata (if the old doc is stale)."""
    parts = [doc]  # Start with the best original document
    return "\n\n".join(p for p in parts if p.strip())


def main(dry_run: bool = False):
    print("═" * 60)
    print("ChromaDB Deduplication Script")
    print("═" * 60)

    client = chromadb.PersistentClient(path=str(DB_PATH))
    col = client.get_collection(COLLECTION_NAME)
    total_before = col.count()
    print(f"Current entries: {total_before}")

    # Fetch everything
    result = col.get(include=["metadatas", "documents"], limit=total_before)
    ids = result["ids"]
    metas = result["metadatas"]
    docs = result["documents"]
    print(f"Fetched: {len(ids)} entries")

    # ── STEP 1: Remove standalone example entries ──
    example_ids = []
    for i, m in enumerate(metas):
        if m.get("category") == "example":
            example_ids.append(ids[i])

    print("\n── STEP 1: Standalone example entries ──")
    print(f"Found: {len(example_ids)} example entries")

    # Verify: check that parent entries have examples
    name_to_meta = {}
    for i, (rid, m, d) in enumerate(zip(ids, metas, docs)):
        if m.get("category") != "example":
            name_to_meta[m.get("name", "").lower().strip()] = m

    orphans = 0
    for i, m in enumerate(metas):
        if m.get("category") == "example":
            name = m.get("name", "")
            # Example names are like "for.for...in — example 1"
            # Extract parent name by removing " — example N" suffix
            parent_key = name.rsplit(" — example ", 1)[0].lower().strip() if " — example " in name else ""
            if parent_key and parent_key not in name_to_meta:
                orphans += 1
    print(f"Orphan examples (no parent with examples): {orphans}")
    print(f"Safe to remove: {len(example_ids) - orphans}")

    # ── STEP 2: Merge cross-category duplicates ──
    print("\n── STEP 2: Cross-category duplicates ──")

    # Build name groups (excluding example entries)
    name_groups: dict[str, list[tuple[str, dict, str]]] = defaultdict(list)
    for i, (rid, m, d) in enumerate(zip(ids, metas, docs)):
        if m.get("category") == "example":
            continue  # Skip — being removed in step 1
        name = m.get("name", "").lower().strip()
        name_groups[name].append((rid, m, d))

    dup_groups = {k: v for k, v in name_groups.items() if len(v) > 1}
    print(f"Names with multiple entries: {len(dup_groups)}")
    print(f"Entries involved in dups: {sum(len(v) for v in dup_groups.values())}")
    print(f"Entries to merge away: {sum(len(v) - 1 for v in dup_groups.values())}")

    # Categorize the dup patterns
    pattern_counts = Counter()
    for name, entries in dup_groups.items():
        cats = tuple(sorted(set(m.get("category", "") for _, m, _ in entries)))
        pattern_counts[cats] += 1

    print("\nDup patterns:")
    for pattern, count in pattern_counts.most_common(10):
        print(f"  {' + '.join(pattern)}: {count}")

    # Build merge plan
    ids_to_remove: set[str] = set(example_ids)  # Start with example entries
    entries_to_add: list[tuple[str, dict, str]] = []  # (id, metadata, document)

    merge_stats = Counter()

    for name, entries in dup_groups.items():
        if len(entries) == 1:
            continue

        metas_list = [m for _, m, _ in entries]
        docs_list = [d for _, _, d in entries]
        ids_list = [rid for rid, _, _ in entries]

        # Merge metadata
        merged_meta = merge_metadata(metas_list)
        merged_meta["name"] = name

        # Merge document
        merged_doc = merge_documents(metas_list, docs_list)

        # Keep the best ID (prefer merged_ or function_ prefix)
        best_id = ids_list[0]
        for rid in ids_list:
            if rid.startswith("merged_"):
                best_id = rid
                break
            elif rid.startswith("function_"):
                best_id = rid
        # If none match, create a new merged ID
        if not any(best_id == rid for rid in ids_list):
            best_id = f"merged_{name}"

        merged_meta["name"] = name  # Ensure name is set

        # All entries except the best_id get removed
        for rid in ids_list:
            if rid != best_id:
                ids_to_remove.add(rid)
                merge_stats["removed"] += 1

        # Check if the best_id's entry needs updating
        best_idx = next(i for i, rid in enumerate(ids_list) if rid == best_id)
        old_meta = metas_list[best_idx]
        docs_list[best_idx]  # kept for reference if needed later

        # Determine if metadata changed
        needs_update = False
        for key in ("category", "sources", "raw_description", "raw_examples",
                     "syntax", "returns", "remarks", "raw_parameters",
                     "raw_see_also", "example_count", "has_examples"):
            old_val = old_meta.get(key, "")
            new_val = merged_meta.get(key, "")
            if str(old_val) != str(new_val):
                needs_update = True
                break

        if needs_update:
            merge_stats["updated"] += 1
            entries_to_add.append((best_id, merged_meta, merged_doc))

    # ── Summary ──
    print(f"\n{'═' * 60}")
    print(f"{'DRY RUN' if dry_run else 'LIVE RUN'} SUMMARY")
    print(f"{'═' * 60}")
    print(f"Total before:          {total_before}")
    print(f"Example entries to remove: {len(example_ids)}")
    print(f"Cross-cat dups to remove:  {merge_stats['removed']}")
    print(f"Cross-cat dups to update:  {merge_stats['updated']}")
    total_remove = len(ids_to_remove)
    total_after = total_before - total_remove
    print(f"Total to remove:       {total_remove}")
    print(f"Total after:           {total_after}")
    print(f"Reduction:             {total_remove} ({total_remove/total_before*100:.1f}%)")

    if dry_run:
        print("\n── DRY RUN — no changes made ──")
        return

    # ── EXECUTE ──
    print("\n── EXECUTING ──")

    # Remove entries
    ids_to_remove_list = [rid for rid in ids_to_remove if rid in set(ids)]
    if ids_to_remove_list:
        print(f"Removing {len(ids_to_remove_list)} entries...")
        col.delete(ids=ids_to_remove_list)
        print("  Done.")

    # Update merged entries
    for entry_id, meta, doc in entries_to_add:
        col.update(
            ids=[entry_id],
            metadatas=[meta],
            documents=[doc],
        )
    if entries_to_add:
        print(f"Updated {len(entries_to_add)} merged entries.")

    # Verify
    final_count = col.count()
    print("\n── VERIFICATION ──")
    print(f"Expected final count: {total_after}")
    print(f"Actual final count:   {final_count}")
    assert final_count == total_after, f"Mismatch: {final_count} != {total_after}"
    print("✅ Counts match.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    main(dry_run=dry_run)
