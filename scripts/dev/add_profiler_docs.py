"""
Add Pine Script v6 profiler/optimization documentation to ChromaDB.

Chunks the profiler docs into semantic sections and indexes them
with appropriate metadata for search retrieval.

Usage:
    python3 scripts/add_profiler_docs.py [--dry-run]
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

# The profiler docs file
PROFILER_DOCS = ROOT / ".playwright-mcp" / "pine_profiling_complete.txt"


def chunk_by_sections(text: str) -> list[dict]:
    """Split the profiler docs into semantic chunks by major sections.

    Each chunk preserves context (parent section) and includes all code examples.
    """
    lines = text.split("\n")

    # Identify section boundaries by looking for short lines that look like headers
    # In the extracted text, headers appear as standalone lines without indentation
    sections = []
    current_section = {"title": "Introduction", "level": 1, "start": 0}

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        # Skip very long lines (not headers)
        if len(stripped) > 80:
            continue
        # Skip lines that start with common non-header patterns
        if stripped.startswith(("//", "@", "Play", "Copied", "Note", "Warning", "Tip", "Caution")):
            continue
        # Skip lines that are clearly code or data
        if any(c in stripped for c in ["=", "(", "{", "[", "<", ">", "|", "/", "*"]):
            if not stripped.endswith(":"):  # Could be a section label
                continue
        # Check if this looks like a section header
        # Headers in the extracted text are short, standalone lines
        if len(stripped) < 60 and not stripped.startswith(" ") and not stripped[0].islower():
            # Check if next line is empty or different (header pattern)
            if i + 1 < len(lines) and (not lines[i + 1].strip() or lines[i + 1].strip().startswith("Pine")):
                sections.append({
                    "title": stripped,
                    "start": i,
                })

    # Build chunks from sections
    chunks = []
    for idx, sec in enumerate(sections):
        start = sec["start"]
        end = sections[idx + 1]["start"] if idx + 1 < len(sections) else len(lines)
        content = "\n".join(lines[start:end]).strip()

        if len(content) < 50:
            continue

        # Count code examples
        code_blocks = re.findall(r"//@version=6.*?(?=\n\n|\Z)", content, re.DOTALL)

        chunks.append({
            "title": sec["title"],
            "content": content,
            "code_count": len(code_blocks),
            "char_count": len(content),
        })

    return chunks


def chunk_by_topics(text: str) -> list[dict]:
    """Alternative: chunk by major topics with overlap for better search.

    Creates larger chunks that preserve context around each topic.
    """
    # Define the major topics from the page
    topics = [
        {
            "title": "Pine Profiler — Overview and Setup",
            "keywords": ["profiler", "profiling", "profiling a script", "profiler mode"],
            "max_chars": 4000,
        },
        {
            "title": "Pine Profiler — Interpreting Results",
            "keywords": ["interpreting", "single-line results", "code block results", "tooltip"],
            "max_chars": 5000,
        },
        {
            "title": "Pine Profiler — User-Defined Functions",
            "keywords": ["user-defined function", "function call", "request.*()"],
            "max_chars": 4000,
        },
        {
            "title": "Pine Profiler — Insignificant and Redundant Code",
            "keywords": ["insignificant", "unused", "redundant", "compiler"],
            "max_chars": 4000,
        },
        {
            "title": "Pine Profiler — Inner Workings",
            "keywords": ["inner workings", "pseudocode", "System.timeNow", "registerPerf"],
            "max_chars": 3000,
        },
        {
            "title": "Optimization — Using Built-ins",
            "keywords": ["built-in", "ta.highest", "internal optimization", "optimized"],
            "max_chars": 5000,
        },
        {
            "title": "Optimization — Reducing Repetition",
            "keywords": ["repetition", "repeated", "assign", "variable"],
            "max_chars": 3000,
        },
        {
            "title": "Optimization — Minimizing request.*() Calls",
            "keywords": ["request.security", "request.*()", "tuple", "calc_bars_count"],
            "max_chars": 4000,
        },
        {
            "title": "Optimization — Avoiding Redrawing",
            "keywords": ["redraw", "box.new", "box.set_", "setter", "drawing"],
            "max_chars": 4000,
        },
        {
            "title": "Optimization — Reducing Drawing Updates",
            "keywords": ["drawing update", "barstate.islast", "historical drawing"],
            "max_chars": 4000,
        },
        {
            "title": "Optimization — Storing Calculated Values",
            "keywords": ["storing", "var ", "varip", "pre-comput", "weights", "matrix.mult"],
            "max_chars": 5000,
        },
        {
            "title": "Optimization — Eliminating Loops",
            "keywords": ["eliminating loop", "loop-free", "mathematical simplification", "math.sum"],
            "max_chars": 5000,
        },
        {
            "title": "Optimization — Loop Optimization",
            "keywords": ["loop calculation", "loop-invariant", "array.indexof", "for [index"],
            "max_chars": 5000,
        },
        {
            "title": "Optimization — Historical Buffer and max_bars_back",
            "keywords": ["max_bars_back", "buffer", "historical buffer", "calc_bars_count", "244 bars"],
            "max_chars": 5000,
        },
    ]

    chunks = []
    text_lower = text.lower()

    for topic in topics:
        # Find the best starting point for this topic
        best_pos = -1
        for kw in topic["keywords"]:
            pos = text_lower.find(kw.lower())
            if pos >= 0:
                if best_pos == -1 or pos < best_pos:
                    best_pos = pos

        if best_pos == -1:
            continue

        # Find the start of the line containing the match
        line_start = text.rfind("\n", 0, best_pos)
        if line_start == -1:
            line_start = 0

        # Extract the chunk
        end_pos = min(line_start + topic["max_chars"], len(text))
        # Don't cut mid-line
        if end_pos < len(text):
            next_newline = text.find("\n", end_pos)
            if next_newline != -1 and next_newline - end_pos < 200:
                end_pos = next_newline

        chunk_text = text[line_start:end_pos].strip()

        if len(chunk_text) < 100:
            continue

        # Count code examples
        code_blocks = re.findall(r"//@version=6", chunk_text)

        chunks.append({
            "title": topic["title"],
            "content": chunk_text,
            "code_count": len(code_blocks),
            "char_count": len(chunk_text),
        })

    return chunks


def main():
    dry_run = "--dry-run" in sys.argv

    if not PROFILER_DOCS.exists():
        print(f"ERROR: Profiler docs not found at {PROFILER_DOCS}")
        sys.exit(1)

    text = PROFILER_DOCS.read_text(encoding="utf-8")
    print(f"Profiler docs: {len(text):,} chars, {text.count(chr(10)):,} lines")

    # Use topic-based chunking for better search
    chunks = chunk_by_topics(text)
    print(f"Created {len(chunks)} topic chunks")

    for chunk in chunks:
        print(f"  {chunk['title']}: {chunk['char_count']:,} chars, {chunk['code_count']} code blocks")

    if dry_run:
        print("\nDRY RUN — no changes written to DB.")
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

    for chunk in chunks:
        entry_id = f"doc_profiler_{hashlib.md5(chunk['title'].encode()).hexdigest()[:8]}"

        # Build document text for embedding
        doc_text = f"USER GUIDE: {chunk['title']}\n\n{chunk['content']}"

        # Build metadata
        meta = {
            "name": chunk["title"],
            "category": "user_guide",
            "namespace": "profiler",
            "syntax": "",
            "returns": "",
            "remarks": "",
            "deprecated": 0,
            "sources": "user_docs",
            "url": "https://www.tradingview.com/pine-script-docs/writing/profiling-and-optimization/",
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
            "file": "profiling-and-optimization",
            "heading": chunk["title"],
        }

        ids.append(entry_id)
        docs.append(doc_text)
        metas.append(meta)

    # Compute embeddings
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

    print(f"\nDone — indexed {len(ids)} profiler doc chunks.")
    print(f"Collection now has {collection.count()} entries.")


if __name__ == "__main__":
    main()
