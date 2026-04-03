"""
index_user_docs.py
─────────────────────────────────────────────────────────────────────────────
Reads Pine Script v6 user documentation markdown files, chunks them
semantically by headings, and saves them as user_docs_chunks.json for
merge_and_index.py to ingest into ChromaDB.

Usage:
    python index_user_docs.py [--src DIR] [--out FILE]

Options:
    --src   Source directory containing pinescriptv6/ markdown files
    --out   Output JSON path (default: user_docs_chunks.json)
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

DEFAULT_SRC = Path(__file__).parent / "pinescriptv6"
DEFAULT_OUT = Path(__file__).parent / "user_docs_chunks.json"
BASE_URL = "https://www.tradingview.com/pine-script-docs/"

# Skip files that are effectively empty (stub files)
SKIP_THRESHOLD = 10  # bytes

# Chunking thresholds
H2_MAX_CHARS = 2000
H3_MAX_CHARS = 3000
MIN_CHUNK_CHARS = 200


def classify_file(file_path: Path, src_dir: Path) -> tuple[str, str]:
    """Return (category, namespace) for a given markdown file path.

    src_dir is the pinescriptv6/ root directory. file_path is an absolute
    path like /.../pinescriptv6/concepts/execution_model.md.
    """
    rel = file_path.relative_to(src_dir)
    parts = rel.parts

    # Top-level files in pinescriptv6/
    if len(parts) == 1:
        name = parts[0]
        if "release" in name.lower():
            return "release_note", "concepts"
        return "concept", "concepts"

    # Subdirectory files
    if len(parts) >= 2:
        top = parts[0]
        if top == "concepts":
            return "concept", "concepts"
        elif top == "visuals":
            return "visual", "visuals"
        elif top == "writing_scripts":
            return "guide", "writing_scripts"
        elif top == "reference":
            if len(parts) >= 3 and parts[1] == "functions":
                return "reference", "reference/functions"
            return "reference", "reference"

    return "concept", "concepts"


def strip_heading_links(line: str) -> str:
    """Remove Markdown link wrappers from heading lines.

    Converts: '## [Heading Name](https://...)' -> 'Heading Name'
    Also handles bare headings: '## Heading Name'
    """
    # Match [text](url) pattern
    m = re.match(r"^(#{1,6})\s+\[([^\]]+)\]\([^)]+\)\s*$", line)
    if m:
        return m.group(2).strip()
    # Bare heading
    m = re.match(r"^(#{1,6})\s+(.+)$", line)
    if m:
        return m.group(2).strip()
    return line.strip()


def chunk_by_h2(content: str) -> list[tuple[str, str]]:
    """Split content by H2 headings. Returns list of (heading, text) tuples."""
    # Split on ## lines (but not ### or deeper)
    parts = re.split(r"\n(?=##\s(?!#))", content)

    chunks: list[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Extract the H2 heading
        lines = part.split("\n")
        heading = ""
        if lines and lines[0].startswith("## "):
            heading = strip_heading_links(lines[0])
            body = "\n".join(lines[1:]).strip()
        else:
            body = part

        if body:
            chunks.append((heading, body))

    return chunks


def chunk_by_h3(content: str) -> list[tuple[str, str]]:
    """Split content by H3 headings. Returns list of (heading, text) tuples."""
    parts = re.split(r"\n(?=###\s(?!#))", content)

    chunks: list[tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        lines = part.split("\n")
        heading = ""
        if lines and lines[0].startswith("### "):
            heading = strip_heading_links(lines[0])
            body = "\n".join(lines[1:]).strip()
        else:
            body = part

        if body:
            chunks.append((heading, body))

    return chunks


def chunk_by_paragraphs(content: str) -> list[tuple[str, str]]:
    """Split content by double newlines. Returns list of (heading, text) tuples."""
    paragraphs = re.split(r"\n{2,}", content)

    chunks: list[tuple[str, str]] = []
    for p in paragraphs:
        p = p.strip()
        if p:
            chunks.append(("", p))
    return chunks


def process_file(file_path: Path, src_dir: Path) -> list[dict[str, Any]]:
    """Process a single markdown file into semantic chunks."""
    category, namespace = classify_file(file_path, src_dir)
    rel_path = str(file_path.relative_to(src_dir.parent))
    stem = file_path.stem

    content = file_path.read_text(encoding="utf-8").strip()
    if not content:
        return []

    # Build a URL from the file path
    rel_to_src = file_path.relative_to(src_dir)
    url_path = str(rel_to_src).replace(".md", "")
    url = f"{BASE_URL}{url_path}/"

    # Get the H1 title (first line) as the file-level heading
    lines = content.split("\n")
    file_title = ""
    if lines:
        file_title = strip_heading_links(lines[0])

    file_rel = f"pinescriptv6/{rel_to_src}"

    entries: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Step 1: Split by H2
    h2_chunks = chunk_by_h2(content)

    for h2_heading, h2_body in h2_chunks:
        if len(h2_body) <= H2_MAX_CHARS:
            # Use as-is
            entry_name = f"{stem} - {h2_heading}" if h2_heading else stem
            entry = _build_entry(entry_name, category, namespace, h2_body,
                                 file_rel, url, h2_heading)
            if _is_valid(entry, seen_names):
                entries.append(entry)
                seen_names.add(entry["name"].lower())
        else:
            # Step 2: Split by H3
            h3_chunks = chunk_by_h3(h2_body)

            for h3_heading, h3_body in h3_chunks:
                if len(h3_body) <= H3_MAX_CHARS:
                    # Build compound heading
                    heading = f"{h2_heading} > {h3_heading}" if h2_heading and h3_heading else (h2_heading or h3_heading)
                    entry_name = f"{stem} - {heading}" if heading else stem
                    entry = _build_entry(entry_name, category, namespace, h3_body,
                                         file_rel, url, heading)
                    if _is_valid(entry, seen_names):
                        entries.append(entry)
                        seen_names.add(entry["name"].lower())
                else:
                    # Step 3: Split by paragraphs
                    para_chunks = chunk_by_paragraphs(h3_body)
                    para_counter = 0

                    for _, para_text in para_chunks:
                        if len(para_text) < MIN_CHUNK_CHARS:
                            continue

                        para_counter += 1
                        heading = f"{h2_heading} > {h3_heading}" if h2_heading and h3_heading else (h2_heading or h3_heading)
                        entry_name = f"{stem} - {heading} (part {para_counter})"
                        entry = _build_entry(entry_name, category, namespace, para_text,
                                             file_rel, url, heading)
                        if _is_valid(entry, seen_names):
                            entries.append(entry)
                            seen_names.add(entry["name"].lower())

    return entries


def _build_entry(
    name: str,
    category: str,
    namespace: str,
    description: str,
    filename: str,
    url: str,
    heading: str,
) -> dict[str, Any]:
    """Build a chunk entry dict."""
    return {
        "name": name,
        "category": category,
        "namespace": namespace,
        "description": description,
        "syntax": "",
        "parameters": [],
        "returns": "",
        "remarks": "",
        "examples": [],
        "see_also": [],
        "source": "user_docs",
        "url": url,
        "file": filename,
        "heading": heading,
    }


def _is_valid(entry: dict[str, Any], seen_names: set[str]) -> bool:
    """Check if an entry is valid for inclusion."""
    desc = entry.get("description", "")
    name = entry.get("name", "")

    # Skip short chunks
    if len(desc) < MIN_CHUNK_CHARS:
        return False

    # Skip if name already seen (dedup within file)
    if name.lower() in seen_names:
        return False

    return True


def discover_markdown_files(src_dir: Path) -> list[Path]:
    """Find markdown files to index from an explicit allow-list.

    Only includes files specified in the source spec. Skips empty stubs
    (<10 bytes) and aggregate/duplicate files like pinescriptv6_complete_reference.md.
    """
    # Explicit file list relative to src_dir (pinescriptv6/)
    allowed_files: list[str] = [
        # concepts/
        "concepts/colors_and_display.md",
        "concepts/common_errors.md",
        "concepts/execution_model.md",
        "concepts/methods.md",
        "concepts/objects.md",
        "concepts/timeframes.md",
        # visuals/
        "visuals/backgrounds.md",
        "visuals/bar_coloring.md",
        "visuals/bar_plotting.md",
        "visuals/colors.md",
        "visuals/fills.md",
        "visuals/levels.md",
        "visuals/lines_and_boxes.md",
        "visuals/overview.md",
        "visuals/plots.md",
        "visuals/tables.md",
        "visuals/texts_and_shapes.md",
        # writing_scripts/
        "writing_scripts/debugging.md",
        "writing_scripts/limitations.md",
        "writing_scripts/profiling_and_optimization.md",
        "writing_scripts/publishing_scripts.md",
        "writing_scripts/style_guide.md",
        # Top-level
        "release_notes.md",
        "pine_script_execution_model.md",
        # reference/
        "reference/annotations.md",
        "reference/constants.md",
        "reference/keywords.md",
        "reference/operators.md",
        "reference/types.md",
        "reference/variables.md",
        "reference/functions/request.md",
        "reference/functions/ta.md",
    ]

    files: list[Path] = []
    for rel in allowed_files:
        f = src_dir / rel
        if f.is_file() and f.stat().st_size > SKIP_THRESHOLD:
            files.append(f)
        elif f.is_file():
            logger.info(f"Skipping empty stub: {rel} ({f.stat().st_size} bytes)")
        else:
            logger.warning(f"File not found: {rel}")

    return files


def main():
    parser = argparse.ArgumentParser(
        description="Index Pine Script v6 user documentation into chunks"
    )
    parser.add_argument(
        "--src", type=Path, default=DEFAULT_SRC,
        help=f"Source directory (default: {DEFAULT_SRC})",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"Output JSON file (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Pine Script v6 User Docs Indexer")
    logger.info("=" * 60)

    if not args.src.is_dir():
        logger.error(f"Source directory not found: {args.src}")
        sys.exit(1)

    # Discover files
    files = discover_markdown_files(args.src)
    logger.info(f"Found {len(files)} markdown files to index")

    # Process all files
    all_entries: list[dict[str, Any]] = []
    file_stats: list[tuple[str, int]] = []

    for f in files:
        try:
            entries = process_file(f, args.src)
            all_entries.extend(entries)
            rel = f.relative_to(args.src)
            file_stats.append((str(rel), len(entries)))
            if entries:
                logger.info(f"  {str(rel):<55s} {len(entries):>4d} chunks")
        except Exception as e:
            logger.error(f"  Error processing {f}: {e}")

    logger.info("-" * 60)
    logger.info(f"Total chunks: {len(all_entries)}")

    # Category breakdown
    from collections import Counter
    cat_counts = Counter(e["category"] for e in all_entries)
    for cat, count in sorted(cat_counts.items()):
        logger.info(f"  {cat:<15} {count:>5}")

    # Namespace breakdown
    ns_counts = Counter(e["namespace"] for e in all_entries)
    logger.info("\nBy namespace:")
    for ns, count in sorted(ns_counts.items()):
        logger.info(f"  {ns:<30} {count:>5}")

    # Write output
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(all_entries, f, indent=2, ensure_ascii=False)

    logger.info(f"\nWritten to: {args.out}")
    logger.info(f"File size:  {args.out.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
