"""
scrape_entries.py
─────────────────────────────────────────────────────────────────────────────
STAGE 2: Scrape EVERY PineScript v6 entry from TradingView's reference page.

KEY OPTIMIZATION: TradingView renders ALL entries on a SINGLE page. Each entry
is a div.tv-pine-reference-item with structured sub-elements. We load the page
ONCE, scroll to trigger lazy-load, then extract all entries via JS.

This is far more efficient than navigating to 1300+ individual URLs.

Output: tv_scraped_entries.json
Also: scrape_report.json, failed_entries.txt

Usage:
    python scrape_entries.py [--index FILE] [--output FILE] [--headful] [--debug]
    python scrape_entries.py --entry fun_ta.ema
    python scrape_entries.py --retry-failed

Options:
    --index       Input index file         (default: tv_entry_index.json)
    --output      Output file path         (default: tv_scraped_entries.json)
    --headful     Show browser window
    --debug       Save full page HTML to debug_scrape.html
    --entry       Scrape single entry by fragment id
    --retry-failed Retry only previously failed entries
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<8} | {message}", level="INFO")

BASE_URL = "https://www.tradingview.com/pine-script-reference/v6/"

FRAGMENT_PREFIX_MAP: dict[str, str] = {
    "an_": "annotation",
    "const_": "constant",
    "fun_": "function",
    "kw_": "keyword",
    "op_": "operator",
    "type_": "type",
    "var_": "variable",
}

CHECKPOINT_INTERVAL = 30


def categorize_fragment(fragment: str) -> str:
    for prefix, category in FRAGMENT_PREFIX_MAP.items():
        if fragment.startswith(prefix):
            return category
    return "unknown"


def detect_namespace(name: str) -> str | None:
    name = name.strip().strip("()`")
    if "." in name:
        prefix = name.split(".")[0]
        return prefix if prefix else None
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Extraction helpers — operate on raw JS-returned item dicts
# ═══════════════════════════════════════════════════════════════════════════════

def extract_name(item_data: dict[str, Any], fallback: str) -> str:
    """Get the entry name from the h3 header."""
    header = item_data.get("header", "")
    if header:
        # Header is like "ta.sma(source, length)" — extract just the name
        paren_idx = header.find("(")
        if paren_idx > 0:
            return header[:paren_idx].strip()
        bracket_idx = header.find("<")
        if bracket_idx > 0:
            return header[:bracket_idx].strip()
        return header.strip()
    return fallback


def extract_syntax_blocks(item_data: dict[str, Any]) -> list[str]:
    """Get all syntax signature blocks."""
    return item_data.get("syntax_blocks", [])


def extract_description(item_data: dict[str, Any]) -> str | None:
    """Get the description text (first text block before any sub-header)."""
    descriptions = item_data.get("descriptions", [])
    if not descriptions:
        return None
    return "\n\n".join(d for d in descriptions if d.strip())


def extract_parameters(item_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse parameter lines: 'name (type) description'."""
    param_lines = item_data.get("parameter_lines", [])
    parameters: list[dict[str, Any]] = []

    for line in param_lines:
        line = line.strip()
        if not line:
            continue

        # Pattern: name (type) description
        # Also handles: name (type, const) description
        m = re.match(
            r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)\s*(.*)",
            line,
        )
        if m:
            param_name = m.group(1)
            param_type = m.group(2).strip()
            param_desc = m.group(3).strip()

            is_optional = (
                "optional" in param_desc.lower()
                or "optional" in param_type.lower()
                or param_desc.startswith("Optional.")
                or param_desc.startswith("Optional ")
            )

            # Check for default value: "description. Default is X" or "= value"
            default_val = None
            default_match = re.search(r"[Dd]efault\s*(?:is|value|=)\s*(.+?)(?:\.|$)", param_desc)
            if default_match:
                default_val = default_match.group(1).strip().rstrip(".")

            parameters.append({
                "name": param_name,
                "type": param_type,
                "description": param_desc,
                "optional": is_optional,
                "default": default_val,
            })

    return parameters


def extract_returns(item_data: dict[str, Any]) -> str | None:
    """Get the returns section text."""
    returns = item_data.get("returns_text", "")
    if returns and returns.strip():
        return returns.strip()
    return None


def extract_remarks(item_data: dict[str, Any]) -> str | None:
    """Get the remarks section text."""
    remarks = item_data.get("remarks_text", "")
    if remarks and remarks.strip():
        return remarks.strip()
    return None


def extract_examples(item_data: dict[str, Any]) -> list[str]:
    """Get all code example blocks."""
    examples = item_data.get("example_blocks", [])
    return [ex.strip() for ex in examples if ex.strip()]


def extract_see_also(item_data: dict[str, Any]) -> list[str]:
    """Get see-also entry names."""
    return item_data.get("see_also", [])


def extract_type_info(item_data: dict[str, Any]) -> dict[str, Any]:
    """Extract type-specific fields and methods."""
    type_fields = item_data.get("type_fields", [])
    type_methods = item_data.get("type_methods", [])
    return {
        "type_fields": type_fields,
        "type_methods": type_methods,
    }


def detect_deprecated(item_data: dict[str, Any]) -> bool:
    """Check if the entry is marked as deprecated."""
    all_text = item_data.get("all_text", "").lower()
    deprecated_markers = ["deprecated", "obsolete", "no longer supported", "removed in"]
    return any(marker in all_text for marker in deprecated_markers)


# ═══════════════════════════════════════════════════════════════════════════════
# Page loading and item extraction
# ═══════════════════════════════════════════════════════════════════════════════

async def load_and_extract_all(headful: bool = False, debug: bool = False) -> list[dict[str, Any]]:
    """Load the reference page and extract all items via JavaScript."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headful,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()
        logger.info(f"Navigating to {BASE_URL}")
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        # Wait for content to render
        for selector in [
            ".tv-script-reference__content-container",
            ".tv-pine-reference-item__header",
            "main",
            "h2",
        ]:
            try:
                await page.wait_for_selector(selector, timeout=15000)
                logger.info(f"Content ready via: {selector}")
                break
            except Exception:
                continue

        # Scroll the main content to trigger lazy-load of all items
        logger.info("Scrolling content to trigger lazy-load...")
        scroll_selectors = [
            ".tv-script-reference__content-container",
            ".tv-script-reference__right-column",
            "main",
        ]

        scroll_target = None
        for sel in scroll_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    scroll_target = sel
                    break
            except Exception:
                continue

        if scroll_target:
            for offset in range(0, 50000, 500):
                await page.evaluate(
                    f"document.querySelector('{scroll_target}')?.scrollTo(0, {offset})"
                )
                await asyncio.sleep(0.05)
        else:
            logger.warning("Could not find content container to scroll.")

        # Also scroll the sidebar/toc
        toc_selectors = [
            ".tv-script-reference__table-of-contents",
            ".tv-accordion",
        ]
        for sel in toc_selectors:
            try:
                el = await page.query_selector(sel)
                if el:
                    for offset in range(0, 15000, 300):
                        await page.evaluate(
                            f"document.querySelector('{sel}')?.scrollTo(0, {offset})"
                        )
                        await asyncio.sleep(0.05)
                    break
            except Exception:
                continue

        await asyncio.sleep(3)
        logger.info("Scroll complete. Extracting items...")

        # Save debug HTML
        if debug:
            html = await page.content()
            Path("debug_scrape.html").write_text(html, encoding="utf-8")
            logger.info("Debug HTML saved to debug_scrape.html")

        # ── Extract ALL items via JavaScript ────────────────────────────────
        # This is the core extraction — it runs in the browser and returns
        # structured data for every reference item on the page.
        js_code = """
        () => {
            const items = document.querySelectorAll('.tv-pine-reference-item');
            const results = [];

            for (const item of items) {
                const content = item.querySelector('.tv-pine-reference-item__content');
                if (!content) continue;

                // Header (h3 with entry name)
                const h3 = item.querySelector('.tv-pine-reference-item__header');
                const header = h3 ? h3.textContent.trim() : '';

                // Determine the current h2 section (category context)
                let sectionH2 = '';
                let prev = item.previousElementSibling;
                while (prev) {
                    if (prev.tagName === 'H2' || prev.classList.contains('tv-pine-reference-header')) {
                        sectionH2 = prev.textContent.trim().toLowerCase();
                        break;
                    }
                    if (prev.classList && prev.classList.contains('tv-pine-reference-item')) {
                        // Skip other items
                    } else if (prev.tagName === 'DIV' && prev.textContent.trim().length > 100) {
                        // This might be an item without class, skip
                    }
                    prev = prev.previousElementSibling;
                }

                // Syntax blocks
                const syntaxBlocks = Array.from(
                    content.querySelectorAll('.tv-pine-reference-item__syntax')
                ).map(el => el.textContent.trim());

                // All children of content for section parsing
                const children = content.children;
                let currentSection = '';
                const descriptions = [];
                const parameterLines = [];
                let returnsText = '';
                let remarksText = '';
                const exampleBlocks = [];
                const seeAlso = [];
                const typeFields = [];
                const typeMethods = [];
                const allTextParts = [];

                for (const child of children) {
                    const cls = child.className || '';
                    const text = child.textContent.trim();

                    // Sub-headers
                    if (cls.includes('sub-header')) {
                        const headerText = text.toLowerCase();
                        if (headerText.includes('syntax') || headerText.includes('signature')) {
                            currentSection = 'syntax';
                        } else if (headerText.includes('argument') || headerText.includes('parameter')) {
                            currentSection = 'arguments';
                        } else if (headerText.includes('return')) {
                            currentSection = 'returns';
                        } else if (headerText.includes('remark') || headerText.includes('note')) {
                            currentSection = 'remarks';
                        } else if (headerText.includes('example')) {
                            currentSection = 'example';
                        } else if (headerText.includes('see also')) {
                            currentSection = 'seealso';
                        } else if (headerText.includes('field')) {
                            currentSection = 'fields';
                        } else if (headerText.includes('method')) {
                            currentSection = 'methods';
                        } else if (headerText.includes('type') && currentSection === '') {
                            currentSection = 'type';
                        } else {
                            // Keep previous section or default to description
                            if (currentSection === '') currentSection = 'description';
                        }
                        continue;
                    }

                    // See-also links
                    if (cls.includes('see-also')) {
                        const links = child.querySelectorAll('a');
                        for (const a of links) {
                            const linkText = a.textContent.trim();
                            if (linkText) seeAlso.push(linkText);
                        }
                        continue;
                    }

                    // Syntax blocks
                    if (cls.includes('syntax')) {
                        continue; // Already extracted above
                    }

                    // Example blocks (pre)
                    if (cls.includes('example') && child.tagName === 'PRE') {
                        const code = child.querySelector('code');
                        exampleBlocks.push(code ? code.textContent : child.textContent);
                        continue;
                    }

                    // Text blocks
                    if (cls.includes('text') || child.tagName === 'P') {
                        allTextParts.push(text);

                        switch (currentSection) {
                            case '':
                            case 'description':
                                if (!cls.includes('sub-header')) {
                                    descriptions.push(text);
                                }
                                break;
                            case 'arguments':
                                parameterLines.push(text);
                                break;
                            case 'returns':
                                if (returnsText) returnsText += '\\n' + text;
                                else returnsText = text;
                                break;
                            case 'remarks':
                                if (remarksText) remarksText += '\\n' + text;
                                else remarksText = text;
                                break;
                            case 'fields':
                                typeFields.push(text);
                                break;
                            case 'methods':
                                typeMethods.push(text);
                                break;
                        }
                        continue;
                    }
                }

                // Extract type fields/methods into structured format
                const parsedTypeFields = [];
                for (const line of typeFields) {
                    const fm = line.match(/^([a-zA-Z_][a-zA-Z0-9_]*)\\s*\\(([^)]*)\\)\\s*(.*)/);
                    if (fm) {
                        parsedTypeFields.push({
                            name: fm[1],
                            type: fm[2].trim(),
                            description: fm[3].trim()
                        });
                    }
                }

                const parsedTypeMethods = [];
                for (const line of typeMethods) {
                    const mm = line.match(/^([a-zA-Z_][a-zA-Z0-9_.]*)\\s*\\(/);
                    if (mm) {
                        parsedTypeMethods.push(mm[1]);
                    }
                }

                // Get the fragment id from any anchor or data attribute
                const anchor = item.querySelector('a[href*="#"]');
                let fragment = '';
                if (anchor) {
                    const href = anchor.getAttribute('href') || '';
                    fragment = href.replace(/^.*#/, '');
                }

                // Also try the item's own id
                if (!fragment) {
                    fragment = item.id || '';
                }

                results.push({
                    header: header,
                    section: sectionH2,
                    fragment: fragment,
                    syntax_blocks: syntaxBlocks,
                    descriptions: descriptions,
                    parameter_lines: parameterLines,
                    returns_text: returnsText,
                    remarks_text: remarksText,
                    example_blocks: exampleBlocks,
                    see_also: seeAlso,
                    type_fields: parsedTypeFields,
                    type_methods: parsedTypeMethods,
                    all_text: allTextParts.join(' ')
                });
            }

            return results;
        }
        """

        raw_items = await page.evaluate(js_code)
        await browser.close()

    logger.info(f"Extracted {len(raw_items)} raw items from page")
    return raw_items


def parse_raw_items(
    raw_items: list[dict[str, Any]],
    index_map: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert raw JS-extracted items into structured entry dicts."""
    entries: list[dict[str, Any]] = []

    for raw in raw_items:
        header = raw.get("header", "")
        if not header:
            continue

        name = extract_name(raw, header)
        if not name:
            continue

        # Determine category from fragment prefix, section heading, or index
        fragment = raw.get("fragment", "")
        section = raw.get("section", "")

        category = categorize_fragment(fragment)

        # Fallback: determine category from section heading
        if category == "unknown":
            section_lower = section.lower().split()[0] if section else ""
            section_map = {
                "variables": "variable",
                "constants": "constant",
                "functions": "function",
                "keywords": "keyword",
                "types": "type",
                "operators": "operator",
                "annotations": "annotation",
            }
            category = section_map.get(section_lower, "unknown")

        # Fallback: check index map for category
        if category == "unknown" and index_map:
            # Try matching by name
            for idx_entry in index_map.values():
                if idx_entry.get("display_name", "").lower().replace("()", "") == name.lower():
                    category = idx_entry.get("category", "unknown")
                    break

        if category == "unknown":
            # Skip concept pages and non-entry items
            continue

        namespace = detect_namespace(name)
        syntax_blocks = extract_syntax_blocks(raw)
        syntax = syntax_blocks[0] if syntax_blocks else None

        description = extract_description(raw)
        parameters = extract_parameters(raw)
        returns = extract_returns(raw)
        remarks = extract_remarks(raw)
        examples = extract_examples(raw)
        see_also = extract_see_also(raw)
        type_info = extract_type_info(raw)
        deprecated = detect_deprecated(raw)

        # Build ID
        entry_id = fragment if fragment else f"{category}_{name.lower().replace('.', '_').replace(' ', '_')}"

        entry: dict[str, Any] = {
            "id": entry_id,
            "name": name,
            "category": category,
            "namespace": namespace,
            "syntax": syntax,
            "overloads": syntax_blocks[1:] if len(syntax_blocks) > 1 else [],
            "description": description,
            "parameters": parameters,
            "returns": returns,
            "remarks": remarks,
            "type_fields": type_info["type_fields"],
            "type_methods": type_info["type_methods"],
            "examples": examples,
            "see_also": see_also,
            "deprecated": deprecated,
            "url": f"{BASE_URL}#{fragment}" if fragment else None,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "source": "tradingview_live",
            "scrape_error": None,
        }
        entries.append(entry)

    return entries


# ═══════════════════════════════════════════════════════════════════════════════
# Verification and reporting
# ═══════════════════════════════════════════════════════════════════════════════

def print_verification(entries: list[dict[str, Any]], discovered_count: int) -> dict[str, Any]:
    """Print and return scrape verification report."""
    cat_counts: dict[str, int] = Counter(e["category"] for e in entries)
    errors = [e for e in entries if e.get("scrape_error")]
    with_examples = [e for e in entries if e.get("examples")]
    with_params = [e for e in entries if e.get("parameters")]
    deprecated_entries = [e for e in entries if e.get("deprecated")]

    total_examples = sum(len(e.get("examples", [])) for e in entries)
    total_params = sum(len(e.get("parameters", [])) for e in entries)

    print()
    print("=" * 50)
    print("  SCRAPE COMPLETE — VERIFICATION")
    print("=" * 50)
    print(f"  Discovered:  {discovered_count} entries")
    print(f"  Scraped:     {len(entries)} ({len(entries) / max(discovered_count, 1) * 100:.0f}%)")
    print(f"  Failed:      {len(errors)}")

    print()
    print("  By category:")
    for cat in ["function", "variable", "constant", "type", "keyword", "operator", "annotation"]:
        n = cat_counts.get(cat, 0)
        print(f"    {cat:<15} {n:>5}")

    print()
    print(f"  Entries with examples:    {len(with_examples)}")
    print(f"  Total examples captured:  {total_examples}")
    print(f"  Entries with parameters:  {len(with_params)}")
    print(f"  Total parameters captured: {total_params}")
    print(f"  Deprecated entries:       {len(deprecated_entries)}")

    if errors:
        print()
        print("  Failed entries:")
        for e in errors[:20]:
            print(f"    - {e.get('id', '?')}: {e.get('scrape_error', '?')[:60]}")

    print("=" * 50)
    print()

    report = {
        "discovered": discovered_count,
        "scraped": len(entries),
        "failed": len(errors),
        "by_category": dict(cat_counts),
        "entries_with_examples": len(with_examples),
        "total_examples": total_examples,
        "entries_with_parameters": len(with_params),
        "total_parameters": total_params,
        "deprecated_count": len(deprecated_entries),
        "failed_ids": [e.get("id", "?") for e in errors],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Scrape all PineScript v6 entries from TradingView"
    )
    parser.add_argument(
        "--index", type=Path, default=Path("data/tv_entry_index.json"),
        help="Input index file (default: data/tv_entry_index.json)",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("data/tv_scraped_entries.json"),
        help="Output file path (default: data/tv_scraped_entries.json)",
    )
    parser.add_argument(
        "--headful", action="store_true",
        help="Show browser window",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Save full page HTML to debug_scrape.html",
    )
    parser.add_argument(
        "--entry", type=str, default=None,
        help="Scrape single entry by fragment id",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="Retry only previously failed entries",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("PineScript v6 Entry Scraper")
    logger.info("=" * 60)

    # Load index map for category lookup
    index_map: dict[str, dict[str, Any]] = {}
    if args.index.exists():
        index_entries = json.loads(args.index.read_text(encoding="utf-8"))
        for entry in index_entries:
            frag = entry.get("fragment", "")
            index_map[frag] = entry
        logger.info(f"Loaded {len(index_map)} index entries for reference")
    else:
        logger.warning(f"Index file not found: {args.index}. Categories may be less accurate.")

    # Load existing scraped entries for single-entry or retry mode
    existing_entries: list[dict[str, Any]] = []
    checkpoint_path = Path("tv_scraped_entries.checkpoint.json")

    if args.retry_failed:
        failed_path = Path("failed_entries.txt")
        if not failed_path.exists():
            logger.error("No failed_entries.txt found. Run a full scrape first.")
            sys.exit(1)
        failed_ids = failed_path.read_text(encoding="utf-8").strip().split("\n")
        logger.info(f"Retrying {len(failed_ids)} failed entries")
    elif args.entry:
        logger.info(f"Scraping single entry: {args.entry}")
    elif checkpoint_path.exists():
        existing_entries = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        logger.info(f"Resuming from checkpoint: {len(existing_entries)} already scraped")

    # ── Scrape ──────────────────────────────────────────────────────────────
    logger.info("Loading TradingView reference page...")
    raw_items = asyncio.run(load_and_extract_all(
        headful=args.headful,
        debug=args.debug,
    ))

    # Parse into structured entries
    entries = parse_raw_items(raw_items, index_map)

    if args.entry:
        # Filter to single entry
        entries = [e for e in entries if e["id"] == args.entry]
        if not entries:
            logger.error(f"Entry '{args.entry}' not found in scraped data.")
            sys.exit(1)
        logger.info(f"Found entry: {entries[0]['name']}")

    logger.info(f"Parsed {len(entries)} structured entries")

    # ── Merge with existing (for resume/retry) ──────────────────────────────
    if existing_entries and not args.entry:
        existing_ids = {e["id"] for e in existing_entries}
        new_entries = [e for e in entries if e["id"] not in existing_ids]
        logger.info(f"Merging: {len(existing_entries)} existing + {len(new_entries)} new")
        entries = existing_entries + new_entries

    # ── Save output ─────────────────────────────────────────────────────────
    output_data = json.dumps(entries, indent=2, ensure_ascii=False)
    args.output.write_text(output_data, encoding="utf-8")
    logger.info(f"Saved {len(entries)} entries to {args.output}")

    # ── Write report ────────────────────────────────────────────────────────
    report = print_verification(entries, len(raw_items))

    report_path = Path("scrape_report.json")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    logger.info(f"Report saved to {report_path}")

    # Write failed entries
    failed = [e for e in entries if e.get("scrape_error")]
    failed_path = Path("failed_entries.txt")
    failed_path.write_text(
        "\n".join(e.get("id", "") for e in failed),
        encoding="utf-8",
    )
    if failed:
        logger.warning(f"{len(failed)} failed entries written to {failed_path}")

    # Clean up checkpoint
    if checkpoint_path.exists():
        checkpoint_path.unlink()

    logger.info("Done.")


if __name__ == "__main__":
    main()
