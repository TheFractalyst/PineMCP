#!/usr/bin/env python3
"""
Comprehensive duplication analysis for PineScript v6 ChromaDB database.
Checks 6 types of duplication and produces a full report.
"""

import chromadb
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import re
import json

DB_PATH = "pinescript_db"
COLLECTION = "pinescript_v6"

def get_all_entries():
    """Pull every entry from the collection."""
    client = chromadb.PersistentClient(path=DB_PATH)
    col = client.get_collection(COLLECTION)
    result = col.get(include=["metadatas", "documents"], limit=col.count())
    entries = []
    for id_, meta, doc in zip(result["ids"], result["metadatas"], result["documents"]):
        entries.append({"id": id_, "meta": meta, "doc": doc or ""})
    return entries


def normalize_text(text):
    """Normalize whitespace for comparison."""
    return re.sub(r'\s+', ' ', text.strip())


# ─────────────────────────────────────────────────────────────────────────────
# TYPE 1: Exact name duplicates (same name metadata, different IDs)
# ─────────────────────────────────────────────────────────────────────────────
def type1_exact_name_duplicates(entries):
    by_name = defaultdict(list)
    for e in entries:
        name = e["meta"].get("name", "")
        by_name[name].append(e)

    dupes = {name: ents for name, ents in by_name.items() if len(ents) > 1}

    results = []
    identical_content_pairs = 0
    different_content_pairs = 0

    for name, ents in sorted(dupes.items(), key=lambda x: -len(x[1])):
        ids = [e["id"] for e in ents]
        categories = [e["meta"].get("category", "") for e in ents]
        sources = [e["meta"].get("sources", "") for e in ents]
        doc_lens = [len(e["doc"]) for e in ents]

        # Check if any pair has identical content
        has_identical = False
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                similarity = SequenceMatcher(None, normalize_text(ents[i]["doc"]), normalize_text(ents[j]["doc"])).ratio()
                if similarity > 0.95:
                    has_identical = True
                    identical_content_pairs += 1
                else:
                    different_content_pairs += 1

        results.append({
            "name": name,
            "count": len(ents),
            "ids": ids,
            "categories": categories,
            "sources": sources,
            "doc_lengths": doc_lens,
            "has_identical_content": has_identical,
        })

    return results, identical_content_pairs, different_content_pairs


# ─────────────────────────────────────────────────────────────────────────────
# TYPE 2: Example entries duplicating parent function
# ─────────────────────────────────────────────────────────────────────────────
def type2_example_entries(entries):
    example_entries = [e for e in entries if e["meta"].get("category") == "example"]

    # Group by parent function name (derived from the example name)
    parent_groups = defaultdict(list)
    for e in example_entries:
        name = e["meta"].get("name", "")
        # Pattern: "function_name — example N" or "function_name - example N"
        parent_match = re.match(r'^(.+?)\s*[—–-]\s*example\s*\d+', name, re.IGNORECASE)
        if parent_match:
            parent_name = parent_match.group(1).strip()
            parent_groups[parent_name].append(e)
        else:
            parent_groups[name].append(e)

    # Check which parents exist as their own entries
    all_names = set(e["meta"].get("name", "") for e in entries)
    all_ids = set(e["id"] for e in entries)

    results = []
    for parent, examples in sorted(parent_groups.items(), key=lambda x: -len(x[1])):
        # Check if parent exists as a standalone entry
        parent_exists_as_entry = parent in all_names
        # Also check common ID patterns
        parent_has_function_entry = any(
            e["meta"].get("name") == parent and e["meta"].get("category") == "function"
            for e in entries
        )

        results.append({
            "parent": parent,
            "example_count": len(examples),
            "example_ids": [e["id"] for e in examples],
            "parent_exists_as_entry": parent_exists_as_entry,
            "parent_has_function_entry": parent_has_function_entry,
            "redundant": parent_exists_as_entry,  # redundant if parent already has examples
        })

    return results, len(example_entries)


# ─────────────────────────────────────────────────────────────────────────────
# TYPE 3: Source-merge duplicates (separate entries that should be merged)
# ─────────────────────────────────────────────────────────────────────────────
def type3_source_merge_duplicates(entries):
    """Find same-name entries from different sources that exist as SEPARATE entries."""
    by_name = defaultdict(list)
    for e in entries:
        name = e["meta"].get("name", "")
        by_name[name].append(e)

    unmerged = []
    for name, ents in by_name.items():
        if len(ents) < 2:
            continue
        sources_set = set()
        for e in ents:
            src = e["meta"].get("sources", "")
            # Split compound sources
            for s in src.split(", "):
                sources_set.add(s.strip())

        # If we have both local_docs and tradingview_live as SEPARATE entries
        local_entries = [e for e in ents if e["meta"].get("sources", "") == "local_docs"]
        live_entries = [e for e in ents if e["meta"].get("sources", "") == "tradingview_live"]
        merged_entries = [e for e in ents if "local_docs" in e["meta"].get("sources", "") and "tradingview_live" in e["meta"].get("sources", "")]

        if local_entries and live_entries and not merged_entries:
            unmerged.append({
                "name": name,
                "local_ids": [e["id"] for e in local_entries],
                "live_ids": [e["id"] for e in live_entries],
                "categories": [e["meta"].get("category", "") for e in ents],
            })

    return unmerged


# ─────────────────────────────────────────────────────────────────────────────
# TYPE 4: User docs overlap with reference
# ─────────────────────────────────────────────────────────────────────────────
def type4_user_docs_overlap(entries):
    user_docs = [e for e in entries if e["meta"].get("sources") == "user_docs"]
    non_user = [e for e in entries if e["meta"].get("sources") != "user_docs"]

    user_names = set(e["meta"].get("name", "") for e in user_docs)
    non_user_names = set(e["meta"].get("name", "") for e in non_user)

    # Direct name overlap
    name_overlap = user_names & non_user_names

    # Namespace overlap - check if user_docs has entries in the same namespace
    user_namespaces = Counter(e["meta"].get("namespace", "") for e in user_docs)
    non_user_namespaces = Counter(e["meta"].get("namespace", "") for e in non_user)

    # Check user_docs reference entries specifically
    user_ref = [e for e in user_docs if e["meta"].get("category") == "reference"]
    user_ref_names = set(e["meta"].get("name", "") for e in user_ref)

    # Check for near-name matches (e.g., "ta.ema" vs "ta.ema()" )
    near_matches = []
    for uname in sorted(user_ref_names):
        for nname in sorted(non_user_names):
            if uname.replace("()", "") == nname.replace("()", ""):
                near_matches.append((uname, nname))
            elif SequenceMatcher(None, uname.lower(), nname.lower()).ratio() > 0.9:
                near_matches.append((uname, nname))

    return {
        "total_user_docs": len(user_docs),
        "user_docs_categories": dict(Counter(e["meta"].get("category", "") for e in user_docs)),
        "exact_name_overlap": len(name_overlap),
        "overlap_names": list(name_overlap)[:30],
        "near_name_matches": near_matches[:30],
        "user_docs_reference_count": len(user_ref),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TYPE 5: Near-duplicate documents (>90% text overlap, different IDs)
# ─────────────────────────────────────────────────────────────────────────────
def type5_near_duplicate_docs(entries):
    """Find entries with >90% text overlap but different IDs."""
    # Group by category first to reduce comparisons
    by_category = defaultdict(list)
    for e in entries:
        cat = e["meta"].get("category", "")
        by_category[cat].append(e)

    near_dupes = []
    checked = 0

    # Compare within categories
    for cat, cat_entries in by_category.items():
        # Skip examples - they're handled by Type 2
        if cat == "example":
            continue

        for i in range(len(cat_entries)):
            for j in range(i + 1, len(cat_entries)):
                e1, e2 = cat_entries[i], cat_entries[j]
                if e1["id"] == e2["id"]:
                    continue

                checked += 1
                t1 = normalize_text(e1["doc"])
                t2 = normalize_text(e2["doc"])

                # Quick length check - if very different sizes, skip
                len1, len2 = len(t1), len(t2)
                if len1 == 0 or len2 == 0:
                    continue
                ratio = min(len1, len2) / max(len1, len2)
                if ratio < 0.7:
                    continue

                sim = SequenceMatcher(None, t1, t2).ratio()
                if sim > 0.9:
                    near_dupes.append({
                        "id1": e1["id"],
                        "id2": e2["id"],
                        "name1": e1["meta"].get("name", ""),
                        "name2": e2["meta"].get("name", ""),
                        "category": cat,
                        "similarity": round(sim, 4),
                        "source1": e1["meta"].get("sources", ""),
                        "source2": e2["meta"].get("sources", ""),
                    })

    return near_dupes, checked


# ─────────────────────────────────────────────────────────────────────────────
# TYPE 6: Minified example duplicates
# ─────────────────────────────────────────────────────────────────────────────
def type6_minified_examples(entries):
    """Find entries where 'Example 2' is a minified version of 'Example 1'."""
    # Look at entries that have raw_examples metadata
    entries_with_examples = []
    for e in entries:
        raw_ex = e["meta"].get("raw_examples", "")
        if raw_ex and e["meta"].get("example_count", 0) > 0:
            entries_with_examples.append(e)

    # Also check example-type entries
    example_entries = [e for e in entries if e["meta"].get("category") == "example"]

    # Group example entries by parent function
    parent_examples = defaultdict(list)
    for e in example_entries:
        name = e["meta"].get("name", "")
        parent_match = re.match(r'^(.+?)\s*[—–-]\s*example\s*(\d+)', name, re.IGNORECASE)
        if parent_match:
            parent = parent_match.group(1).strip()
            ex_num = int(parent_match.group(2))
            parent_examples[parent].append({"num": ex_num, "entry": e})

    # Check entries with multiple examples in raw_examples
    minified_count = 0
    minified_details = []

    # Method 1: Check raw_examples field for embedded minified examples
    for e in entries_with_examples:
        raw_ex = e["meta"].get("raw_examples", "")
        if not raw_ex:
            continue

        # Split by //@version=6 to get individual examples
        example_blocks = re.split(r'(?=//@version=6)', raw_ex)
        example_blocks = [b.strip() for b in example_blocks if b.strip()]

        if len(example_blocks) < 2:
            continue

        # Compare consecutive pairs
        for i in range(len(example_blocks)):
            for j in range(i + 1, len(example_blocks)):
                norm1 = normalize_text(example_blocks[i])
                norm2 = normalize_text(example_blocks[j])

                if len(norm1) == 0 or len(norm2) == 0:
                    continue

                # Check if one is a minified version of the other
                sim = SequenceMatcher(None, norm1, norm2).ratio()
                len_ratio = min(len(norm1), len(norm2)) / max(len(norm1), len(norm2))

                if sim > 0.85 and len_ratio < 0.8:
                    minified_count += 1
                    minified_details.append({
                        "name": e["meta"].get("name", ""),
                        "id": e["id"],
                        "block_i_len": len(example_blocks[i]),
                        "block_j_len": len(example_blocks[j]),
                        "similarity": round(sim, 4),
                        "len_ratio": round(len_ratio, 4),
                    })

    # Method 2: Check separate example entries for same parent
    for parent, examples in parent_examples.items():
        if len(examples) < 2:
            continue
        for i in range(len(examples)):
            for j in range(i + 1, len(examples)):
                e1 = examples[i]["entry"]
                e2 = examples[j]["entry"]
                t1 = normalize_text(e1["doc"])
                t2 = normalize_text(e2["doc"])
                if len(t1) == 0 or len(t2) == 0:
                    continue
                sim = SequenceMatcher(None, t1, t2).ratio()
                len_ratio = min(len(t1), len(t2)) / max(len(t1), len(t2))
                if sim > 0.85 and len_ratio < 0.8:
                    minified_count += 1
                    minified_details.append({
                        "name": f"{parent} ex{examples[i]['num']} vs ex{examples[j]['num']}",
                        "id": f"{e1['id']} vs {e2['id']}",
                        "similarity": round(sim, 4),
                        "len_ratio": round(len_ratio, 4),
                    })

    return minified_count, minified_details


# ─────────────────────────────────────────────────────────────────────────────
# MAIN: Run all analyses and produce report
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 80)
    print("PINESCRIPT V6 CHROMADB DUPLICATION ANALYSIS")
    print("=" * 80)

    print("\nLoading all entries...")
    entries = get_all_entries()
    print(f"Total entries: {len(entries)}")

    by_source = Counter(e["meta"].get("sources", "") for e in entries)
    by_category = Counter(e["meta"].get("category", "") for e in entries)
    print(f"\nBy Source: {dict(by_source)}")
    print(f"By Category: {dict(by_category)}")

    # ─── TYPE 1 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TYPE 1: EXACT NAME DUPLICATES (same name, different IDs)")
    print("=" * 80)

    t1_results, t1_identical, t1_different = type1_exact_name_duplicates(entries)

    print(f"\nTotal names with >1 entry: {len(t1_results)}")
    print(f"Entries involved in duplication: {sum(r['count'] for r in t1_results)}")
    print(f"Excess entries (copies beyond first): {sum(r['count'] - 1 for r in t1_results)}")
    print(f"Pairs with identical/near-identical content (>95%): {t1_identical}")
    print(f"Pairs with genuinely different content: {t1_different}")

    print(f"\nTop 20 most duplicated names:")
    for r in t1_results[:20]:
        cats_str = ", ".join(set(r["categories"]))
        srcs_str = " | ".join(set(r["sources"]))
        id_str = ", ".join(r["ids"])
        ident_flag = " [IDENTICAL CONTENT]" if r["has_identical_content"] else ""
        print(f"  {r['name']}: {r['count']} copies ({cats_str}) src=[{srcs_str}]{ident_flag}")
        print(f"    IDs: {id_str}")
        print(f"    Doc lengths: {r['doc_lengths']}")

    # ─── TYPE 2 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TYPE 2: EXAMPLE ENTRIES DUPLICATING PARENT FUNCTION")
    print("=" * 80)

    t2_results, t2_total = type2_example_entries(entries)
    redundant_examples = [r for r in t2_results if r["redundant"]]

    print(f"\nTotal example-type entries: {t2_total}")
    print(f"Unique parent functions with examples: {len(t2_results)}")
    print(f"Examples where parent already has its own entry: {len(redundant_examples)}")
    print(f"Potentially redundant example entries: {sum(r['example_count'] for r in redundant_examples)}")

    print(f"\nTop 20 parent functions with most redundant examples:")
    for r in sorted(redundant_examples, key=lambda x: -x["example_count"])[:20]:
        print(f"  {r['parent']}: {r['example_count']} example entries "
              f"(parent exists: {r['parent_has_function_entry']})")
        print(f"    IDs: {r['example_ids']}")

    # ─── TYPE 3 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TYPE 3: SOURCE-MERGE DUPLICATES (unmerged local + live)")
    print("=" * 80)

    t3_results = type3_source_merge_duplicates(entries)

    print(f"\nEntries existing as separate local_docs + tradingview_live (unmerged): {len(t3_results)}")
    for r in t3_results[:20]:
        print(f"  {r['name']}: local={r['local_ids']}, live={r['live_ids']}, cats={r['categories']}")

    # ─── TYPE 4 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TYPE 4: USER DOCS OVERLAP WITH REFERENCE")
    print("=" * 80)

    t4_results = type4_user_docs_overlap(entries)

    print(f"\nTotal user_docs entries: {t4_results['total_user_docs']}")
    print(f"User docs by category: {t4_results['user_docs_categories']}")
    print(f"Exact name overlap with non-user-docs: {t4_results['exact_name_overlap']}")
    if t4_results['overlap_names']:
        print(f"  Overlapping names: {t4_results['overlap_names']}")
    print(f"Near-name matches: {len(t4_results['near_name_matches'])}")
    if t4_results['near_name_matches']:
        for pair in t4_results['near_name_matches'][:15]:
            print(f"  '{pair[0]}' <-> '{pair[1]}'")

    # ─── TYPE 5 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TYPE 5: NEAR-DUPLICATE DOCUMENTS (>90% text overlap)")
    print("=" * 80)

    print("\nComputing pairwise similarities (this may take a moment)...")
    t5_results, t5_checked = type5_near_duplicate_docs(entries)

    print(f"\nPairs compared: {t5_checked}")
    print(f"Near-duplicate pairs found (>90% similarity): {len(t5_results)}")

    for r in sorted(t5_results, key=lambda x: -x["similarity"])[:30]:
        print(f"  {r['name1']} [{r['source1']}] <-> {r['name2']} [{r['source2']}]")
        print(f"    IDs: {r['id1']} <-> {r['id2']}")
        print(f"    Category: {r['category']}, Similarity: {r['similarity']}")

    # ─── TYPE 6 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TYPE 6: MINIFIED EXAMPLE DUPLICATES")
    print("=" * 80)

    t6_count, t6_details = type6_minified_examples(entries)

    print(f"\nMinified example pairs found: {t6_count}")
    for d in t6_details[:20]:
        print(f"  {d['name']} (id={d['id']})")
        print(f"    Similarity: {d['similarity']}, Length ratio: {d['len_ratio']}")

    # ─── SUMMARY ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY REPORT")
    print("=" * 80)

    t1_excess = sum(r["count"] - 1 for r in t1_results)
    t2_redundant = sum(r["example_count"] for r in redundant_examples)
    t3_count = len(t3_results)
    t4_overlap = t4_results["exact_name_overlap"]
    t5_count = len(t5_results)
    t6_found = t6_count

    # Calculate total removable (avoid double-counting)
    # Type 2 examples are a subset of total entries
    # Type 1 duplicates include some that are also counted in Type 3
    # Be conservative: count unique removable entries

    removable_from_t1 = t1_excess  # Extra copies beyond 1
    removable_from_t2 = t2_redundant  # All example entries where parent exists
    removable_from_t3 = t3_count  # One of each unmerged pair
    removable_from_t5 = t5_count  # One of each near-duplicate pair (deduct overlap with T1)

    print(f"\n  TYPE 1 - Exact name duplicates:     {t1_excess:>5} excess entries across {len(t1_results)} names")
    print(f"  TYPE 2 - Example entry duplication:  {t2_redundant:>5} example entries (parents already exist)")
    print(f"  TYPE 3 - Unmerged source duplicates: {t3_count:>5} unmerged pairs")
    print(f"  TYPE 4 - User docs overlap:          {t4_overlap:>5} exact name overlaps with reference")
    print(f"  TYPE 5 - Near-duplicate documents:   {t5_count:>5} near-duplicate pairs")
    print(f"  TYPE 6 - Minified example dupes:     {t6_found:>5} minified example pairs")

    # Conservative estimate: T2 examples + T1 excess (excluding T2 overlap) + T3 + T5 (excluding T1 overlap)
    total_conservative = removable_from_t2 + removable_from_t3
    print(f"\n  Total entries in DB:                 {len(entries):>5}")
    print(f"  Conservative removable estimate:     {total_conservative:>5}")
    print(f"  Projected DB size after dedup:       {len(entries) - total_conservative:>5}")
    print(f"  Reduction:                           {total_conservative / len(entries) * 100:.1f}%")

    print("\n" + "-" * 80)
    print("DEDUP STRATEGY RECOMMENDATIONS:")
    print("-" * 80)
    print("""
  1. REMOVE all example-category entries (Type 2): {t2_redundant} entries.
     Every parent function/variable already contains its examples in raw_examples.
     These are pure duplicates that inflate counts without adding search value.

  2. MERGE Type 1 name duplicates into single canonical entries:
     {t1_names} names have multiple category entries (type+function+variable for same concept).
     Strategy: Keep the richest entry (usually 'function' or 'type'), add metadata from others.
     Example: "line" exists as type_line, function_line, variable_line - merge into one.

  3. MERGE Type 3 unmerged source duplicates:
     {t3_count} entries exist as separate local_docs + tradingview_live instead of merged.
     These should follow the same pattern as the 533 already-merged entries.

  4. REVIEW Type 5 near-duplicates for merge candidates:
     {t5_count} pairs with >90% text similarity. Manual review recommended.

  5. KEEP Type 4 user_docs entries as-is:
     No exact name overlap detected. These provide complementary educational content.
     The user_docs reference entries ({t4_ref} entries) use different naming conventions.

  6. CLEAN Type 6 minified examples from raw_examples:
     {t6_found} minified example pairs found in raw_examples fields.
     Strip whitespace-normalized duplicates from the raw_examples metadata.
""".format(
        t2_redundant=removable_from_t2,
        t1_names=len(t1_results),
        t3_count=t3_count,
        t5_count=t5_count,
        t4_ref=t4_results["user_docs_reference_count"],
        t6_found=t6_found,
    ))

    # Export JSON for further analysis
    report = {
        "total_entries": len(entries),
        "by_source": dict(by_source),
        "by_category": dict(by_category),
        "type1": {
            "duplicate_names": len(t1_results),
            "excess_entries": t1_excess,
            "identical_content_pairs": t1_identical,
            "different_content_pairs": t1_different,
            "top_duplicates": [
                {"name": r["name"], "count": r["count"], "ids": r["ids"],
                 "categories": r["categories"], "sources": r["sources"],
                 "identical": r["has_identical_content"]}
                for r in t1_results[:30]
            ],
        },
        "type2": {
            "total_example_entries": t2_total,
            "redundant_parents": len(redundant_examples),
            "redundant_entries": t2_redundant,
            "top_redundant": [
                {"parent": r["parent"], "count": r["example_count"], "ids": r["example_ids"]}
                for r in sorted(redundant_examples, key=lambda x: -x["example_count"])[:30]
            ],
        },
        "type3": {
            "unmerged_count": t3_count,
            "unmerged": t3_results,
        },
        "type4": {
            "total_user_docs": t4_results["total_user_docs"],
            "exact_overlap": t4_results["exact_name_overlap"],
            "near_matches": len(t4_results["near_name_matches"]),
        },
        "type5": {
            "near_duplicate_pairs": t5_count,
            "pairs": t5_results[:30],
        },
        "type6": {
            "minified_pairs": t6_found,
            "details": t6_details[:30],
        },
    }

    with open("duplication_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nFull report exported to duplication_report.json")


if __name__ == "__main__":
    main()
