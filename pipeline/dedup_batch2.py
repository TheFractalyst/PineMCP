"""
dedup_batch2.py
─────────────────────────────────────────────────────────────────────────────
Comprehensive ChromaDB deduplication — Phase 2.

Re-derives all findings from 4 parallel audit agents against current DB state,
deduplicates the removal list, cleans JS contamination in-place, then removes
all redundant entries.

Findings covered:
  1. 61 entries with scraped JS boilerplate contamination (clean in-place)
  2. 22 duplicate concept topic entries (pine_script_execution_model)
  3. 67 guide same-page overlap entries from chunker
  4. 95 hollow function-category stubs (name exists as richer constant/var/type)
  5. 73 true duplicate chunks (>99% content overlap after normalization)
  6. 53 name() vs name function dupes
  7. 135 redundant reference entries (>60% Jaccard with non-ref counterpart)

Usage:
    python pipeline/dedup_batch2.py [--dry-run]
"""

from __future__ import annotations

import re
import sys
import hashlib
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.db import get_collection


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def shingle_hash(doc: str, k: int = 5) -> set[str]:
    norm = re.sub(r'\s+', ' ', doc.lower().strip())
    norm = re.sub(r'[^a-z0-9 ]', '', norm)
    words = norm.split()
    if len(words) < k:
        return {hashlib.md5(norm.encode()).hexdigest()}
    return {hashlib.md5(' '.join(words[i:i+k]).encode()).hexdigest() for i in range(len(words) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


JS_PATTERN = re.compile(r'(let s=!1.*$)', re.DOTALL)


def strip_js(doc: str) -> str:
    """Remove scraped TradingView navigation JS from document."""
    return JS_PATTERN.sub('', doc).rstrip()


def main(dry_run: bool = False):
    print('=' * 70)
    print('ChromaDB Comprehensive Dedup — Phase 2')
    print('=' * 70)

    col = get_collection()
    total_before = col.count()
    print(f'Current entries: {total_before}')

    # Fetch everything
    result = col.get(include=['metadatas', 'documents'], limit=total_before)
    ids = result['ids']
    metas = result['metadatas']
    docs = result['documents']
    print(f'Fetched: {len(ids)} entries')

    # Build entry list
    entries = []
    for rid, doc, meta in zip(ids, docs, metas):
        entries.append({
            'id': rid,
            'name': meta.get('name', ''),
            'cat': meta.get('category', ''),
            'ns': meta.get('namespace', ''),
            'doc': doc or '',
            'doc_len': len(doc or ''),
        })

    remove_set: set[str] = set()  # IDs to remove
    clean_list: list[tuple[str, str]] = []  # (id, cleaned_doc) to update

    # ── 1. JS CONTAMINATION CLEANING ──────────────────────────────────────
    print('\n── 1. JS Contamination Cleanup ──')
    js_count = 0
    for e in entries:
        if 'let s=!1' in e['doc']:
            cleaned = strip_js(e['doc'])
            if len(cleaned) < len(e['doc']):
                js_count += 1
                clean_list.append((e['id'], cleaned))
    print(f'  Entries with JS to clean: {js_count}')

    # ── 2. DUPLICATE CONCEPT TOPIC (execution_model vs pine_script_execution_model)
    print('\n── 2. Duplicate Concept Topic ──')
    from difflib import SequenceMatcher

    concepts = [e for e in entries if e['cat'] == 'concept']
    em_entries = [e for e in concepts if e['name'].startswith('execution_model')]
    psem_entries = [e for e in concepts if e['name'].startswith('pine_script_execution_model')]
    print(f'  execution_model entries: {len(em_entries)}')
    print(f'  pine_script_execution_model entries: {len(psem_entries)}')

    psem_dupes = 0
    for pe in psem_entries:
        pe_norm = re.sub(r'\s+', ' ', pe['doc'].lower().strip())
        best_sim = 0.0
        for ee in em_entries:
            ee_norm = re.sub(r'\s+', ' ', ee['doc'].lower().strip())
            sim = SequenceMatcher(None, pe_norm, ee_norm).ratio()
            if sim > best_sim:
                best_sim = sim
        if best_sim > 0.5:
            remove_set.add(pe['id'])
            psem_dupes += 1
    print(f'  psem entries to remove (>50% overlap): {psem_dupes}')

    # ── 3. GUIDE SAME-PAGE OVERLAPS ──────────────────────────────────────
    print('\n── 3. Guide Same-Page Overlaps ──')
    guides = [e for e in entries if e['cat'] == 'guide']

    # Group by topic prefix
    guide_topics = defaultdict(list)
    for e in guides:
        if ' - ' in e['name']:
            topic = e['name'].split(' - ')[0].strip()
        else:
            topic = e['name']
        guide_topics[topic].append(e)

    # For topics with many entries, check for overlap
    guide_dupe_count = 0
    for topic, topic_entries in guide_topics.items():
        if len(topic_entries) < 3:
            continue
        # Build shingle sets
        for e in topic_entries:
            e['shingles'] = shingle_hash(e['doc'])

        # Find overlapping pairs
        overlap_pairs = []
        for i in range(len(topic_entries)):
            for j in range(i + 1, len(topic_entries)):
                sim = jaccard(topic_entries[i]['shingles'], topic_entries[j]['shingles'])
                if sim > 0.7:
                    overlap_pairs.append((sim, i, j))

        if not overlap_pairs:
            continue

        # Greedy removal: remove shorter doc from each pair until no overlaps
        removed_indices = set()
        for sim, i, j in sorted(overlap_pairs, key=lambda x: -x[0]):
            if i in removed_indices or j in removed_indices:
                continue
            # Remove the shorter one
            if topic_entries[i]['doc_len'] <= topic_entries[j]['doc_len']:
                removed_indices.add(i)
            else:
                removed_indices.add(j)

        for idx in removed_indices:
            remove_set.add(topic_entries[idx]['id'])
            guide_dupe_count += 1

    print(f'  Guide overlap entries to remove: {guide_dupe_count}')

    # ── 4. HOLLOW FUNCTION STUBS ──────────────────────────────────────────
    print('\n── 4. Hollow Function Stubs ──')
    # Build name index (lowercase, stripped)
    name_index = defaultdict(list)
    for e in entries:
        name_index[e['name'].lower().strip()].append(e)

    stub_count = 0
    for e in entries:
        if e['cat'] != 'function':
            continue
        name_lower = e['name'].lower().strip()

        # Check if this name also exists as constant, variable, or type
        has_richer = False
        for other in name_index.get(name_lower, []):
            if other['id'] == e['id']:
                continue
            if other['cat'] in ('constant', 'variable', 'type'):
                if other['doc_len'] >= e['doc_len'] * 0.8:
                    has_richer = True
                    break

        if not has_richer:
            continue

        # Verify this function entry is hollow (no syntax or very short)
        doc = e['doc']
        has_syntax = 'Syntax:' in doc
        body = re.sub(r'^FUNCTION:.*$', '', doc, flags=re.MULTILINE)
        body = re.sub(r'^Namespace:.*$', '', body, flags=re.MULTILINE)
        body = re.sub(r'^See also:.*$', '', body, flags=re.MULTILINE)
        body = re.sub(r'^Syntax:.*$', '', body, flags=re.MULTILINE)
        body = body.strip()

        if not has_syntax and len(body) < 200:
            remove_set.add(e['id'])
            stub_count += 1
    print(f'  Hollow function stubs to remove: {stub_count}')

    # ── 5. TRUE DUPLICATE CHUNKS (normalized >99% overlap) ────────────────
    print('\n── 5. True Duplicate Chunks ──')
    # Group by topic prefix + check for near-identical chunks
    all_topics = defaultdict(list)
    for e in entries:
        if e['id'] in remove_set:
            continue  # Skip already marked
        if ' - ' in e['name']:
            topic = e['name'].split(' - ')[0].strip().lower()
        else:
            topic = e['name'].lower()
        all_topics[topic].append(e)

    chunk_dupe_count = 0
    for topic, topic_entries in all_topics.items():
        if len(topic_entries) < 2:
            continue
        # Check for exact normalized content matches
        norm_map: dict[str, list] = {}
        for e in topic_entries:
            norm = re.sub(r'\s+', ' ', e['doc'].lower().strip())
            norm = re.sub(r'[^a-z0-9 ]', '', norm)
            h = hashlib.sha256(norm.encode()).hexdigest()
            if h not in norm_map:
                norm_map[h] = []
            norm_map[h].append(e)

        for h, group in norm_map.items():
            if len(group) > 1:
                # Keep first, remove rest
                for e in group[1:]:
                    if e['id'] not in remove_set:
                        remove_set.add(e['id'])
                        chunk_dupe_count += 1
    print(f'  True duplicate chunks to remove: {chunk_dupe_count}')

    # ── 6. name() vs name FUNCTION DUPES ─────────────────────────────────
    print('\n── 6. name() vs name Function Dupes ──')
    # Build map: bare_name -> entry (for non-function categories)
    bare_map = defaultdict(list)
    for e in entries:
        if e['id'] in remove_set:
            continue
        bare_map[e['name'].lower().strip()].append(e)

    parens_dupe_count = 0
    for e in entries:
        if e['id'] in remove_set:
            continue
        if not e['name'].endswith('()') or e['cat'] != 'function':
            continue
        bare = e['name'][:-2].lower().strip()
        bare_entries = bare_map.get(bare, [])
        if not bare_entries:
            continue

        # Check for overlap with bare version
        e_shingles = shingle_hash(e['doc'])
        for other in bare_entries:
            if other['id'] == e['id'] or other['id'] in remove_set:
                continue
            sim = jaccard(e_shingles, shingle_hash(other['doc']))
            if sim > 0.7:
                # Remove the () version (e), keep bare
                remove_set.add(e['id'])
                parens_dupe_count += 1
                break
    print(f'  name() dupes to remove: {parens_dupe_count}')

    # ── 7. REDUNDANT REFERENCE ENTRIES ────────────────────────────────────
    print('\n── 7. Redundant Reference Entries ──')
    ref_entries = [e for e in entries if e['cat'] == 'reference' and e['id'] not in remove_set]
    non_ref = [e for e in entries if e['cat'] != 'reference' and e['id'] not in remove_set]

    # Build banding index for non-ref
    nr_bands = defaultdict(list)
    for i, e in enumerate(non_ref):
        shingles = shingle_hash(e['doc'])
        e['shingles'] = shingles
        for s in list(shingles)[:5]:
            nr_bands[s].append(i)

    ref_dupe_count = 0
    for ref in ref_entries:
        ref_shingles = shingle_hash(ref['doc'])
        candidates = set()
        for s in list(ref_shingles)[:5]:
            for idx in nr_bands.get(s, []):
                candidates.add(idx)

        best_sim = 0.0
        best_match = None
        for idx in candidates:
            sim = jaccard(ref_shingles, non_ref[idx]['shingles'])
            if sim > best_sim:
                best_sim = sim
                best_match = non_ref[idx]

        if best_sim > 0.6:
            # Reference is redundant — remove it
            remove_set.add(ref['id'])
            ref_dupe_count += 1
    print(f'  Redundant reference entries to remove: {ref_dupe_count}')

    # ── DEDUP VERIFICATION ────────────────────────────────────────────────
    print(f'\n{"=" * 70}')
    print(f'{"DRY RUN" if dry_run else "LIVE RUN"} SUMMARY')
    print(f'{"=" * 70}')
    print(f'Current entries:      {total_before}')
    print(f'Entries to clean JS:  {len(clean_list)}')
    print(f'Entries to remove:    {len(remove_set)}')
    print(f'  - psem concept:     {sum(1 for e in entries if e["id"] in remove_set and e["name"].startswith("pine_script_execution"))}')
    print(f'  - guide overlap:    {guide_dupe_count}')
    print(f'  - hollow stubs:     {stub_count}')
    print(f'  - chunk dupes:      {chunk_dupe_count}')
    print(f'  - name() dupes:     {parens_dupe_count}')
    print(f'  - ref redundant:    {ref_dupe_count}')
    print(f'Entries after:        {total_before - len(remove_set)}')
    print(f'Reduction:            {len(remove_set)} ({len(remove_set)/total_before*100:.1f}%)')

    if dry_run:
        print('\n-- DRY RUN -- no changes made')
        return

    # ── EXECUTE ───────────────────────────────────────────────────────────
    print('\n-- EXECUTING --')

    # Phase 1: Clean JS contamination
    if clean_list:
        print(f'Cleaning JS from {len(clean_list)} entries...')
        for eid, cleaned_doc in clean_list:
            try:
                col.update(ids=[eid], documents=[cleaned_doc])
            except Exception as ex:
                print(f'  WARNING: Failed to clean {eid}: {ex}')
        print('  Done.')

    # Phase 2: Remove redundant entries
    if remove_set:
        remove_list = list(remove_set)
        print(f'Removing {len(remove_list)} redundant entries...')
        # ChromaDB delete in batches of 1000
        for i in range(0, len(remove_list), 1000):
            batch = remove_list[i:i+1000]
            col.delete(ids=batch)
        print('  Done.')

    # ── VERIFICATION ──────────────────────────────────────────────────────
    print(f'\n{"=" * 70}')
    print('VERIFICATION')
    print(f'{"=" * 70}')

    final_count = col.count()
    expected = total_before - len(remove_set)
    print(f'Expected: {expected}')
    print(f'Actual:   {final_count}')
    assert final_count == expected, f'Count mismatch: {final_count} != {expected}'

    # Verify no JS contamination remains
    result2 = col.get(include=['metadatas', 'documents'], limit=final_count)
    js_remaining = sum(1 for d in result2['documents'] if 'let s=!1' in (d or ''))
    print(f'JS-contaminated entries remaining: {js_remaining}')

    # Quick dupe scan
    keys = Counter()
    for m in result2['metadatas']:
        key = (m.get('name', '').lower().strip(), m.get('category', ''))
        keys[key] += 1
    true_dupes = {k: v for k, v in keys.items() if v > 1}

    cats = Counter(m.get('category', '') for m in result2['metadatas'])
    print(f'True duplicates (same name+category): {len(true_dupes)}')
    if true_dupes:
        for (name, cat), count in true_dupes.items():
            print(f'  WARNING: "{name}" [{cat}] x{count}')
    else:
        print('  Zero duplicates confirmed.')

    print(f'\nCategory breakdown:')
    for cat, cnt in cats.most_common():
        print(f'  {cat:20s} {cnt}')

    print(f'\nDone. {total_before} -> {final_count} entries.')


if __name__ == '__main__':
    dry_run = '--dry-run' in sys.argv
    main(dry_run=dry_run)
