#!/usr/bin/env python3
"""
ChromaDB Duplicate Detection Script for pinescript_v6 collection.
Thoroughly inspects the database for:
  1. Entries with the same normalized name but different IDs
  2. Near-duplicate documents (>90% similar first 200 chars) with different names
  3. Example vs function category overlap
  4. Full statistics
"""

import chromadb
import hashlib
import os
from collections import defaultdict
from difflib import SequenceMatcher

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pinescript_db")
COLLECTION_NAME = "pinescript_v6"


def connect_and_fetch():
    """Connect to ChromaDB and fetch all entries."""
    client = chromadb.PersistentClient(path=DB_PATH)
    col = client.get_collection(name=COLLECTION_NAME)
    count = col.count()
    print(f"Collection '{COLLECTION_NAME}' has {count} entries.\n")

    # Fetch everything in batches to be safe
    batch_size = 5000
    all_ids = []
    all_metadatas = []
    all_documents = []

    offset = 0
    while offset < count:
        batch = col.get(
            include=["metadatas", "documents"],
            limit=batch_size,
            offset=offset,
        )
        all_ids.extend(batch["ids"])
        all_metadatas.extend(batch["metadatas"])
        all_documents.extend(batch["documents"])
        offset += batch_size

    assert len(all_ids) == count, f"Fetched {len(all_ids)} but expected {count}"
    return all_ids, all_metadatas, all_documents


def normalize_name(name):
    """Normalize a name for case-insensitive, whitespace-stripped comparison."""
    if name is None:
        return ""
    return name.strip().lower()


def sha256(text):
    """Return SHA-256 hex digest of text."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def similarity(a, b):
    """Return SequenceMatcher ratio between two strings."""
    return SequenceMatcher(None, a, b).ratio()


# ── Main analysis ──────────────────────────────────────────────────────────

def main():
    ids, metadatas, documents = connect_and_fetch()
    total = len(ids)

    # Build lookup structures
    entries = []
    for i in range(total):
        entry_id = ids[i]
        meta = metadatas[i] if metadatas[i] else {}
        doc = documents[i] if documents[i] else ""
        name = meta.get("name", "")
        category = meta.get("category", meta.get("type", ""))
        source = meta.get("source", "")
        entries.append({
            "id": entry_id,
            "name": name,
            "normalized_name": normalize_name(name),
            "category": category,
            "source": source,
            "doc": doc,
            "doc_hash": sha256(doc),
            "doc_prefix": doc[:200] if doc else "",
        })

    # ── 1. Duplicate names (same normalized name, different IDs) ─────────
    print("=" * 80)
    print("SECTION 1: DUPLICATE NORMALIZED NAMES")
    print("=" * 80)

    by_norm_name = defaultdict(list)
    for e in entries:
        by_norm_name[e["normalized_name"]].append(e)

    dup_name_groups = {
        name: group for name, group in by_norm_name.items()
        if len(group) > 1
    }

    print(f"\nUnique normalized names: {len(by_norm_name)}")
    print(f"Duplicate name groups:   {len(dup_name_groups)}")
    print(f"Total entries involved in duplicate groups: "
          f"{sum(len(g) for g in dup_name_groups.values())}\n")

    for norm_name, group in sorted(dup_name_groups.items()):
        print(f"  Name: '{norm_name}' ({len(group)} entries)")
        docs_identical = len(set(e["doc_hash"] for e in group)) == 1
        for e in group:
            print(f"    ID:       {e['id']}")
            print(f"    Original: '{e['name']}'")
            print(f"    Category: {e['category']}")
            print(f"    Source:   {e['source']}")
            print(f"    Doc hash: {e['doc_hash'][:16]}...")
            print(f"    Doc len:  {len(e['doc'])} chars")
            print()
        print(f"    Docs identical across group: {docs_identical}")
        print(f"    {'─' * 60}")

    # ── 2. Exact hash duplicates (same doc content, different IDs) ───────
    print("\n" + "=" * 80)
    print("SECTION 2: EXACT DOCUMENT HASH COLLISIONS")
    print("=" * 80)

    by_hash = defaultdict(list)
    for e in entries:
        by_hash[e["doc_hash"]].append(e)

    exact_dup_groups = {
        h: group for h, group in by_hash.items()
        if len(group) > 1
    }

    print(f"\nGroups with identical document content: {len(exact_dup_groups)}")
    print(f"Total entries involved: "
          f"{sum(len(g) for g in exact_dup_groups.values())}\n")

    for h, group in sorted(exact_dup_groups.items()):
        print(f"  Hash: {h[:16]}... ({len(group)} entries)")
        names = [e["name"] for e in group]
        categories = [e["category"] for e in group]
        sources = [e["source"] for e in group]
        ids_list = [e["id"] for e in group]
        print(f"    Names:     {names}")
        print(f"    IDs:       {ids_list}")
        print(f"    Categories:{categories}")
        print(f"    Sources:   {sources}")
        print()

    # ── 3. Near-duplicate documents (different names, >90% similar prefix) ─
    print("\n" + "=" * 80)
    print("SECTION 3: NEAR-DUPLICATE DOCS (>90% prefix match, different names)")
    print("=" * 80)

    # Group by prefix for efficient comparison
    by_prefix = defaultdict(list)
    for e in entries:
        if e["doc_prefix"]:
            by_prefix[e["doc_prefix"]].append(e)

    near_dupes = []
    seen_pairs = set()
    for e1 in entries:
        if not e1["doc_prefix"]:
            continue
        for e2 in entries:
            if e2["id"] <= e1["id"]:
                continue
            if not e2["doc_prefix"]:
                continue
            pair_key = (e1["id"], e2["id"])
            if pair_key in seen_pairs:
                continue
            if e1["normalized_name"] == e2["normalized_name"]:
                continue  # already covered in section 1
            sim = similarity(e1["doc_prefix"], e2["doc_prefix"])
            if sim > 0.90:
                seen_pairs.add(pair_key)
                near_dupes.append((e1, e2, sim))

    print(f"\nNear-duplicate pairs found: {len(near_dupes)}\n")
    for e1, e2, sim in sorted(near_dupes, key=lambda x: -x[2])[:50]:
        print(f"  Similarity: {sim:.3f}")
        print(f"    Entry A: name='{e1['name']}' id={e1['id']} "
              f"cat={e1['category']} src={e1['source']}")
        print(f"    Entry B: name='{e2['name']}' id={e2['id']} "
              f"cat={e2['category']} src={e2['source']}")
        print(f"    Doc A prefix: {e1['doc_prefix'][:100]}...")
        print(f"    Doc B prefix: {e2['doc_prefix'][:100]}...")
        print()

    if len(near_dupes) > 50:
        print(f"  ... and {len(near_dupes) - 50} more pairs (truncated)")

    # ── 4. Example vs Function category overlap ──────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 4: EXAMPLE vs FUNCTION CATEGORY OVERLAP")
    print("=" * 80)

    examples = [e for e in entries if e["category"] == "example"]
    functions = [e for e in entries if e["category"] == "function"]
    print(f"\nExample entries: {len(examples)}")
    print(f"Function entries: {len(functions)}")

    # Check if any example has content that's a substring of a function doc
    # or vice versa (using first 200 chars for performance)
    example_prefixes = {e["doc_hash"]: e for e in examples if e["doc"]}
    function_hashes = {e["doc_hash"] for e in functions if e["doc"]}

    overlapping_hashes = set(example_prefixes.keys()) & function_hashes
    print(f"\nExample entries with IDENTICAL content to a function entry: "
          f"{len(overlapping_hashes)}")

    for h in sorted(overlapping_hashes)[:30]:
        ex = example_prefixes[h]
        matching_funcs = [f for f in functions if f["doc_hash"] == h]
        print(f"  Hash: {h[:16]}...")
        print(f"    Example: name='{ex['name']}' id={ex['id']} src={ex['source']}")
        for f in matching_funcs:
            print(f"    Function: name='{f['name']}' id={f['id']} src={f['source']}")
        print()

    # ── 5. Category distribution ─────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SECTION 5: CATEGORY AND SOURCE DISTRIBUTION")
    print("=" * 80)

    cat_counts = defaultdict(int)
    src_counts = defaultdict(int)
    cat_src_matrix = defaultdict(lambda: defaultdict(int))

    for e in entries:
        cat_counts[e["category"]] += 1
        src_counts[e["source"]] += 1
        cat_src_matrix[e["category"]][e["source"]] += 1

    print("\nBy Category:")
    for cat, cnt in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat or '(empty)'}: {cnt}")

    print("\nBy Source:")
    for src, cnt in sorted(src_counts.items(), key=lambda x: -x[1]):
        print(f"  {src or '(empty)'}: {cnt}")

    print("\nCategory x Source matrix:")
    all_sources = sorted(src_counts.keys())
    header = f"  {'Category':<20s}" + "".join(f"{s or '(empty)':>12s}" for s in all_sources)
    print(header)
    for cat in sorted(cat_counts.keys()):
        row = f"  {cat or '(empty)':<20s}"
        for src in all_sources:
            row += f"{cat_src_matrix[cat][src]:>12d}"
        print(row)

    # ── 6. IDs that look auto-generated vs meaningful ────────────────────
    print("\n" + "=" * 80)
    print("SECTION 6: ID FORMAT ANALYSIS")
    print("=" * 80)

    uuid_like = 0
    name_like = 0
    other_ids = []
    for e in entries:
        eid = e["id"]
        if len(eid) == 36 and eid.count("-") == 4:
            uuid_like += 1
        elif len(eid) < 64 and "-" not in eid:
            name_like += 1
        else:
            other_ids.append(eid)

    print(f"\nUUID-style IDs: {uuid_like}")
    print(f"Name-like IDs:  {name_like}")
    print(f"Other IDs:      {len(other_ids)}")
    if other_ids[:10]:
        print("Sample other IDs:")
        for oid in other_ids[:10]:
            print(f"  {oid}")

    # ── 7. Summary statistics ────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)

    unique_names = len(set(e["normalized_name"] for e in entries))
    unique_docs = len(set(e["doc_hash"] for e in entries))
    empty_docs = sum(1 for e in entries if not e["doc"])

    print(f"""
  Total entries:              {total}
  Unique normalized names:    {unique_names}
  Unique document hashes:     {unique_docs}
  Empty documents:            {empty_docs}
  Duplicate name groups:      {len(dup_name_groups)}
  Exact doc hash collisions:  {len(exact_dup_groups)}
  Near-duplicate doc pairs:   {len(near_dupes)}
  Example entries:            {len(examples)}
  Example-function overlaps:  {len(overlapping_hashes)}
""")


if __name__ == "__main__":
    main()
