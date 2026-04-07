"""
Fix the `returns` metadata field in ChromaDB by extracting type annotations
from the `syntax` field's → pattern.

This fixes search_by_return_type which was matching against prose descriptions
instead of type annotations.

Usage:
    python scripts/fix_returns_field.py [--dry-run]
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "pinescript_db"
COLLECTION_NAME = "pinescript_v6"


def extract_return_type(syntax: str) -> str:
    """Extract return type from syntax like 'ta.ema(source, length) → series float'."""
    if not syntax:
        return ""
    # Match → or -> followed by the type annotation
    arrow_match = re.search(r"(?:→|->)\s*(.+)$", syntax)
    if arrow_match:
        return arrow_match.group(1).strip()
    return ""


def is_prose(text: str) -> bool:
    """Check if the returns field is prose description rather than a type annotation."""
    if not text:
        return False
    # Type annotations are short and contain specific keywords
    type_keywords = [
        "series", "simple", "const", "input", "void", "float", "int", "bool",
        "string", "color", "line", "label", "box", "table", "array", "matrix",
        "map", "tuple", "chart", "polyline", "[", "]", "<", ">",
    ]
    text_lower = text.lower()
    has_type = any(kw in text_lower for kw in type_keywords)
    # Prose descriptions are long sentences
    is_long = len(text) > 80 or text.count(" ") > 8
    # Prose has sentence patterns
    has_sentence = any(
        pat in text
        for pat in [". ", "The ", "the ", "A ", "Returns ", "This ", "It "]
    )
    return is_long or (has_sentence and not has_type)


def main():
    dry_run = "--dry-run" in sys.argv

    import chromadb
    client = chromadb.PersistentClient(path=str(DB_PATH))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    total = collection.count()
    print(f"Collection: {total} entries")

    # Fetch all entries
    all_results = collection.get(include=["metadatas", "documents"])
    ids = all_results["ids"]
    metas = all_results["metadatas"]

    fixed_count = 0
    already_correct = 0
    no_syntax = 0
    no_arrow = 0
    batch_ids = []
    batch_metas = []

    for entry_id, meta in zip(ids, metas):
        syntax = meta.get("syntax", "")
        current_returns = meta.get("returns", "")

        extracted = extract_return_type(syntax)

        if not syntax:
            no_syntax += 1
            continue

        if not extracted:
            no_arrow += 1
            continue

        if current_returns == extracted:
            already_correct += 1
            continue

        # Fix: if syntax has a → type, ALWAYS prefer it over returns field
        # The returns field often contains prose descriptions, not type annotations
        if current_returns != extracted:
            old_returns = current_returns
            new_meta = {**meta}
            new_meta["returns"] = extracted
            if old_returns and old_returns != extracted:
                new_meta["raw_returns_description"] = old_returns

            fixed_count += 1
            batch_ids.append(entry_id)
            batch_metas.append(new_meta)

            if fixed_count <= 20:
                name = meta.get("name", "?")
                print(f"  FIX: {name}")
                print(f"    OLD: {old_returns[:80]}")
                print(f"    NEW: {extracted}")

            # ChromaDB update in batches of 100
            if len(batch_ids) >= 100:
                if not dry_run:
                    collection.update(ids=batch_ids, metadatas=batch_metas)
                batch_ids = []
                batch_metas = []

    # Flush remaining
    if batch_ids and not dry_run:
        collection.update(ids=batch_ids, metadatas=batch_metas)

    print(f"\nResults:")
    print(f"  Total entries:        {total}")
    print(f"  Already correct:      {already_correct}")
    print(f"  Fixed:                {fixed_count}")
    print(f"  No syntax field:      {no_syntax}")
    print(f"  Syntax has no →:      {no_arrow}")

    if dry_run:
        print(f"\nDRY RUN — no changes written to DB.")
    else:
        print(f"\nDone — {fixed_count} entries updated.")


if __name__ == "__main__":
    main()
