"""
Add fetched Pine Script v6 documentation pages to ChromaDB.

Processes text files from data/user_docs/ and indexes them into ChromaDB
with appropriate metadata for search retrieval.

Usage:
    python3 scripts/add_user_guide_docs.py [--dry-run]
"""

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "pinescript_db"
COLLECTION_NAME = "pinescript_v6"
EMBED_MODEL = "all-MiniLM-L6-v2"
USER_DOCS_DIR = ROOT / "data" / "user_docs"

# Chunking config
MAX_CHUNK_CHARS = 5000
MIN_CHUNK_CHARS = 200

# URL mapping: filename -> official URL
URL_MAP = {
    "type_system": "language/type-system",
    "script_structure": "language/script-structure",
    "identifiers": "language/identifiers",
    "declaration_statements": "language/declaration-statements",
    "variable_declarations": "language/variable-declarations",
    "conditional_structures": "language/conditional-structures",
    "user_defined_functions": "language/user-defined-functions",
    "enums": "language/enums",
    "arrays": "language/arrays",
    "matrices": "language/matrices",
    "maps": "language/maps",
    "loops": "language/loops",
    "built_ins": "language/built-ins",
    "execution_model": "language/execution-model",
    "methods": "language/methods",
    "objects": "language/objects",
    "operators": "language/operators",
    "strategies": "concepts/strategies",
    "repainting": "concepts/repainting",
    "other_timeframes_and_data": "concepts/other-timeframes-and-data",
    "inputs": "concepts/inputs",
    "alerts": "concepts/alerts",
    "sessions": "concepts/sessions",
    "libraries": "concepts/libraries",
    "bar_states": "concepts/bar-states",
    "chart_information": "concepts/chart-information",
    "non_standard_charts_data": "concepts/non-standard-charts-data",
    "strings": "concepts/strings",
    "time": "concepts/time",
    "timeframes": "concepts/timeframes",
    "debugging": "writing/debugging",
    "limitations": "writing/limitations",
    "publishing": "writing/publishing",
    "style_guide": "writing/style-guide",
    "visuals_overview": "visuals/overview",
    "visuals_backgrounds": "visuals/backgrounds",
    "visuals_bar_coloring": "visuals/bar-coloring",
    "visuals_bar_plotting": "visuals/bar-plotting",
    "visuals_colors": "visuals/colors",
    "visuals_fills": "visuals/fills",
    "visuals_levels": "visuals/levels",
    "visuals_lines_and_boxes": "visuals/lines-and-boxes",
    "visuals_plots": "visuals/plots",
    "visuals_tables": "visuals/tables",
    "visuals_text_and_shapes": "visuals/text-and-shapes",
    "primer_first_indicator": "primer/first-indicator",
    "primer_first_steps": "primer/first-steps",
    "primer_next_steps": "primer/next-steps",
    "migration_overview": "migration-guides/overview",
    "to_pine_v2": "migration-guides/to-pine-version-2",
    "to_pine_v3": "migration-guides/to-pine-version-3",
    "to_pine_v4": "migration-guides/to-pine-version-4",
    "to_pine_v5": "migration-guides/to-pine-version-5",
    "to_pine_v6": "migration-guides/to-pine-version-6",
    "faq_alerts": "faq/alerts",
    "faq_data_structures": "faq/data-structures",
    "faq_functions": "faq/functions",
    "faq_general": "faq/general",
    "faq_indicators": "faq/indicators",
    "faq_other_data": "faq/other-data-and-timeframes",
    "faq_programming": "faq/programming",
    "faq_strategies": "faq/strategies",
    "faq_strings_formatting": "faq/strings-and-formatting",
    "faq_techniques": "faq/techniques",
    "faq_times_dates": "faq/times-dates-and-sessions",
    "faq_variables_operators": "faq/variables-and-operators",
    "faq_visuals": "faq/visuals",
    "errors_overview": "errors/overview",
    "errors_CE10101": "errors/CE10101",
    "errors_CW10003": "errors/CW10003",
    "errors_RE10139": "errors/RE10139",
    "errors_RE10143": "errors/RE10143",
    "release_notes": "release-notes",
}

# Namespace mapping: filename -> namespace for ChromaDB
# Falls back to prefix-based detection for unmapped files
NS_MAP = {
    "type_system": "language",
    "script_structure": "language",
    "identifiers": "language",
    "declaration_statements": "language",
    "variable_declarations": "language",
    "conditional_structures": "language",
    "user_defined_functions": "language",
    "enums": "language",
    "arrays": "language",
    "matrices": "language",
    "maps": "language",
    "loops": "language",
    "built_ins": "language",
    "execution_model": "language",
    "methods": "language",
    "objects": "language",
    "operators": "language",
    "strategies": "concepts",
    "repainting": "concepts",
    "other_timeframes_and_data": "concepts",
    "inputs": "concepts",
    "alerts": "concepts",
    "sessions": "concepts",
    "libraries": "concepts",
    "bar_states": "concepts",
    "chart_information": "concepts",
    "non_standard_charts_data": "concepts",
    "strings": "concepts",
    "time": "concepts",
    "timeframes": "concepts",
    "debugging": "writing",
    "limitations": "writing",
    "publishing": "writing",
    "style_guide": "writing",
    "visuals_overview": "visuals",
    "visuals_backgrounds": "visuals",
    "visuals_bar_coloring": "visuals",
    "visuals_bar_plotting": "visuals",
    "visuals_colors": "visuals",
    "visuals_fills": "visuals",
    "visuals_levels": "visuals",
    "visuals_lines_and_boxes": "visuals",
    "visuals_plots": "visuals",
    "visuals_tables": "visuals",
    "visuals_text_and_shapes": "visuals",
    "primer_first_indicator": "primer",
    "primer_first_steps": "primer",
    "primer_next_steps": "primer",
    "migration_overview": "migration",
    "to_pine_v2": "migration",
    "to_pine_v3": "migration",
    "to_pine_v4": "migration",
    "to_pine_v5": "migration",
    "to_pine_v6": "migration",
    "faq_alerts": "faq",
    "faq_data_structures": "faq",
    "faq_functions": "faq",
    "faq_general": "faq",
    "faq_indicators": "faq",
    "faq_other_data": "faq",
    "faq_programming": "faq",
    "faq_strategies": "faq",
    "faq_strings_formatting": "faq",
    "faq_techniques": "faq",
    "faq_times_dates": "faq",
    "faq_variables_operators": "faq",
    "faq_visuals": "faq",
    "errors_overview": "errors",
    "errors_CE10101": "errors",
    "errors_CW10003": "errors",
    "errors_RE10139": "errors",
    "errors_RE10143": "errors",
    "release_notes": "release_notes",
}


def chunk_text(text: str, title: str) -> list[dict]:
    """Split text into semantic chunks by sections."""
    lines = text.split("\n")
    chunks = []
    current_chunk = []
    current_heading = title
    chunk_start = 0

    def flush_chunk():
        if not current_chunk:
            return
        content = "\n".join(current_chunk).strip()
        if len(content) >= MIN_CHUNK_CHARS:
            # Count code examples
            code_blocks = re.findall(r"//@version=6", content)
            chunks.append({
                "heading": current_heading,
                "content": content,
                "code_count": len(code_blocks),
                "char_count": len(content),
            })

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Detect section headers: short lines (<60 chars), not code, not empty
        is_header = (
            stripped
            and len(stripped) < 60
            and not stripped.startswith(("//", "@", "Copied", "Note", "Play"))
            and not any(c in stripped for c in ["=", "(", "{", "[", "<"])
            and (i + 1 >= len(lines) or not lines[i + 1].strip() or lines[i + 1].strip().startswith("Pine"))
        )

        # Check if current chunk would exceed max size
        would_exceed = len("\n".join(current_chunk)) + len(line) > MAX_CHUNK_CHARS

        if is_header and len(current_chunk) >= MIN_CHUNK_CHARS:
            flush_chunk()
            current_chunk = [line]
            current_heading = f"{title} > {stripped}"
        elif would_exceed and len(current_chunk) >= MIN_CHUNK_CHARS:
            flush_chunk()
            current_chunk = [line]
        else:
            current_chunk.append(line)

    flush_chunk()
    return chunks


def process_file(file_path: Path) -> list[dict]:
    """Process a single text file into chunks."""
    stem = file_path.stem
    text = file_path.read_text(encoding="utf-8").strip()

    if not text:
        return []

    # Get title from first non-empty line
    title = stem.replace("_", " ").title()
    for line in text.split("\n"):
        if line.strip() and len(line.strip()) < 60:
            title = line.strip()
            break

    url_path = URL_MAP.get(stem, f"unknown/{stem}")
    url = f"https://www.tradingview.com/pine-script-docs/{url_path}/"
    namespace = NS_MAP.get(stem, "user_guide")

    chunks = chunk_text(text, title)

    results = []
    for chunk in chunks:
        results.append({
            "title": chunk["heading"],
            "content": chunk["content"],
            "url": url,
            "namespace": namespace,
            "file": str(file_path.relative_to(ROOT)),
            "code_count": chunk["code_count"],
        })

    return results


def main():
    dry_run = "--dry-run" in sys.argv

    if not USER_DOCS_DIR.exists():
        print(f"ERROR: User docs directory not found: {USER_DOCS_DIR}")
        sys.exit(1)

    # Find all text files
    txt_files = sorted(USER_DOCS_DIR.glob("*.txt"))
    print(f"Found {len(txt_files)} text files in {USER_DOCS_DIR}")

    if not txt_files:
        print("No files to process.")
        sys.exit(0)

    # Process all files
    all_chunks = []
    for f in txt_files:
        chunks = process_file(f)
        print(f"  {f.name}: {len(chunks)} chunks")
        all_chunks.extend(chunks)

    print(f"\nTotal chunks: {len(all_chunks)}")

    if not all_chunks:
        print("No chunks to index.")
        return

    if dry_run:
        print("\nDRY RUN — no changes written to DB.")
        for chunk in all_chunks[:10]:
            print(f"  {chunk['title']}: {len(chunk['content']):,} chars")
        return

    # Index into ChromaDB
    import chromadb
    from sentence_transformers import SentenceTransformer

    print(f"\nLoading embedding model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)

    client = chromadb.PersistentClient(path=str(DB_PATH))
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    print(f"Collection ready: {collection.count()} entries")

    ids = []
    docs = []
    metas = []

    for chunk in all_chunks:
        # Generate stable ID (include content hash to avoid duplicates when same heading appears multiple times)
        content_hash = hashlib.md5(chunk['content'].encode()).hexdigest()[:10]
        entry_id = f"doc_{chunk['namespace']}_{content_hash}"

        doc_text = f"USER GUIDE: {chunk['title']}\n\n{chunk['content']}"

        meta = {
            "name": chunk["title"],
            "category": "user_guide",
            "namespace": chunk["namespace"],
            "syntax": "",
            "returns": "",
            "remarks": "",
            "deprecated": 0,
            "sources": "user_docs",
            "url": chunk["url"],
            "scraped_at": "",
            "has_examples": 1 if chunk["code_count"] > 0 else 0,
            "example_count": chunk["code_count"],
            "param_count": 0,
            "overload_count": 0,
            "raw_description": chunk["content"][:500],
            "raw_examples": "",
            "raw_parameters": "",
            "raw_overloads": "",
            "raw_type_fields": "",
            "raw_see_also": "",
            "raw_returns_description": "",
            "file": chunk["file"],
            "heading": chunk["title"],
        }

        ids.append(entry_id)
        docs.append(doc_text)
        metas.append(meta)

    # Compute embeddings in batch
    print("Computing embeddings...")
    vecs = model.encode(docs, show_progress_bar=True)
    embeddings = [v.tolist() for v in vecs]

    # Upsert
    collection.upsert(
        ids=ids,
        documents=docs,
        metadatas=metas,
        embeddings=embeddings,
    )

    print(f"\nDone — indexed {len(ids)} chunks.")
    print(f"Collection now has {collection.count()} entries.")


if __name__ == "__main__":
    main()
