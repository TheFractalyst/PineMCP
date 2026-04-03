"""
discover_entries.py
─────────────────────────────────────────────────────────────────────────────
STAGE 1: Build the definitive master index of EVERY entry on TradingView's
PineScript v6 reference page.

Uses Playwright (Chromium, headless) to load the single-page reference,
scroll the sidebar to force lazy-load, then extract all sidebar anchor links.

Output: tv_entry_index.json

Usage:
    python discover_entries.py [--output FILE] [--headful] [--debug]

Options:
    --output   Output file path            (default: tv_entry_index.json)
    --headful  Show browser window
    --debug    Save full page HTML to debug_sidebar.html
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
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

EXPECTED_RANGES: dict[str, tuple[int, int]] = {
    "function": (470, 520),
    "variable": (150, 180),
    "constant": (180, 220),
    "type": (15, 25),
    "keyword": (15, 25),
    "operator": (15, 20),
    "annotation": (5, 10),
}


def categorize_fragment(fragment: str) -> str:
    """Determine category from fragment prefix."""
    for prefix, category in FRAGMENT_PREFIX_MAP.items():
        if fragment.startswith(prefix):
            return category
    return "unknown"


def detect_namespace(name: str) -> str | None:
    """Extract namespace prefix from entry name (everything before first dot)."""
    name = name.strip().strip("()`")
    if "." in name:
        prefix = name.split(".")[0]
        return prefix if prefix else None
    return None


async def discover_entries(headful: bool = False, debug: bool = False) -> list[dict[str, Any]]:
    """Load TradingView reference page, extract all sidebar entries."""
    from playwright.async_api import async_playwright

    entries_by_fragment: dict[str, dict[str, Any]] = {}

    async with async_playwright() as p:
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]

        browser = await p.chromium.launch(
            headless=not headful,
            args=browser_args,
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36",
        )

        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()
        logger.info(f"Navigating to {BASE_URL}")

        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        # Wait strategy: try selectors in order
        wait_selectors = [
            ("nav", 30000),
            ("[class*='sidebar']", 30000),
            ("[class*='menu']", 30000),
            ("[class*='toc']", 30000),
        ]

        loaded = False
        for selector, timeout in wait_selectors:
            try:
                await page.wait_for_selector(selector, timeout=timeout)
                logger.info(f"Page loaded via selector: {selector}")
                loaded = True
                break
            except Exception:
                continue

        if not loaded:
            logger.warning("Selectors timed out. Waiting for networkidle as fallback.")
            try:
                await page.wait_for_load_state("networkidle", timeout=45000)
                loaded = True
            except Exception:
                pass

        if not loaded:
            logger.error("Page failed to load. Attempting to continue anyway.")

        # Scroll the sidebar to force lazy-load of all items
        logger.info("Scrolling sidebar to trigger lazy-load...")
        scroll_selectors = [
            ".tv-script-reference__table-of-contents",
            ".tv-accordion",
            "[class*='toc']",
            "nav",
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
            for offset in range(0, 12000, 300):
                await page.evaluate(
                    f"document.querySelector('{scroll_target}')?.scrollTo(0, {offset})"
                )
                await asyncio.sleep(0.1)
        else:
            logger.warning("Could not find sidebar element to scroll.")

        await asyncio.sleep(2)
        logger.info("Scroll complete. Extracting entries...")

        # ── Method 1: BeautifulSoup HTML parsing ────────────────────────────
        html = await page.content()

        if debug:
            debug_path = Path("debug_sidebar.html")
            debug_path.write_text(html, encoding="utf-8")
            logger.info(f"Debug HTML saved to {debug_path}")

        soup = BeautifulSoup(html, "lxml")

        bs_entries: dict[str, dict[str, Any]] = {}
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]

            # Match both full URL fragments and relative # fragments
            fragment = None
            if "/pine-script-reference/v6/#" in href:
                fragment = href.split("#")[1]
            elif href.startswith("#") and len(href) > 3:
                fragment = href[1:]

            if not fragment:
                continue

            # Only process known prefixes
            category = categorize_fragment(fragment)
            if category == "unknown":
                continue

            display_name = a_tag.get_text(strip=True)

            if fragment not in bs_entries:
                bs_entries[fragment] = {
                    "fragment": fragment,
                    "url": f"{BASE_URL}#{fragment}",
                    "display_name": display_name,
                    "category": category,
                    "namespace": detect_namespace(display_name),
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                }

        logger.info(f"BeautifulSoup found {len(bs_entries)} unique entries")

        # ── Method 2: JavaScript extraction (backup) ───────────────────────
        js_links = await page.evaluate("""
            () => {
                const anchors = document.querySelectorAll('a[href*="#"]');
                return Array.from(anchors).map(a => ({
                    href: a.getAttribute('href') || '',
                    text: a.textContent.trim()
                }));
            }
        """)

        js_entries: dict[str, dict[str, Any]] = {}
        for link in js_links:
            href = link["href"]
            fragment = None

            if "/pine-script-reference/v6/#" in href:
                fragment = href.split("#")[1]
            elif href.startswith("#") and len(href) > 3:
                fragment = href[1:]

            if not fragment:
                continue

            category = categorize_fragment(fragment)
            if category == "unknown":
                continue

            if fragment not in js_entries:
                js_entries[fragment] = {
                    "fragment": fragment,
                    "url": f"{BASE_URL}#{fragment}",
                    "display_name": link["text"],
                    "category": category,
                    "namespace": detect_namespace(link["text"]),
                    "discovered_at": datetime.now(timezone.utc).isoformat(),
                }

        logger.info(f"JavaScript found {len(js_entries)} unique entries")

        # ── Merge results ───────────────────────────────────────────────────
        for fragment, entry in bs_entries.items():
            entries_by_fragment[fragment] = entry

        for fragment, entry in js_entries.items():
            if fragment not in entries_by_fragment:
                entries_by_fragment[fragment] = entry

        await browser.close()

    # ── Build output list ───────────────────────────────────────────────────
    result = list(entries_by_fragment.values())
    result.sort(key=lambda e: (e["category"], e["display_name"]))

    return result


def verify_counts(entries: list[dict[str, Any]]) -> bool:
    """Verify discovered counts against expected ranges. Returns True if OK."""
    counts: dict[str, int] = {}
    for entry in entries:
        cat = entry["category"]
        counts[cat] = counts.get(cat, 0) + 1

    total = len(entries)
    logger.info(f"Discovered {total} total entries:")

    all_ok = True
    for cat in sorted(counts.keys()):
        n = counts[cat]
        expected = EXPECTED_RANGES.get(cat)
        status = ""
        if expected:
            lo, hi = expected
            if n < lo * 0.7:
                status = "  LOW"
                all_ok = False
            elif n > hi * 1.5:
                status = "  HIGH"
        logger.info(f"  {cat:<15}  {n:>5}{status}")

    # Unknown entries
    unknown_count = counts.get("unknown", 0)
    if unknown_count > 0:
        logger.info(f"  {'unknown':<15}  {unknown_count:>5}  (concept pages, not entries)")

    if total < 850:
        logger.warning(
            f"Low count ({total}). Sidebar may not have fully rendered. "
            f"Expected 900-1000+ documented entries."
        )
        return False

    if total < 700:
        logger.error(
            f"Discovery severely incomplete ({total}). "
            f"Check site structure manually."
        )
        return False

    return all_ok


def main():
    parser = argparse.ArgumentParser(
        description="Discover all PineScript v6 entries from TradingView"
    )
    parser.add_argument(
        "--output", type=Path, default=Path("tv_entry_index.json"),
        help="Output file path (default: tv_entry_index.json)",
    )
    parser.add_argument(
        "--headful", action="store_true",
        help="Show browser window during discovery",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Save full page HTML to debug_sidebar.html",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("PineScript v6 Entry Discovery")
    logger.info("=" * 60)

    entries = asyncio.run(discover_entries(headful=args.headful, debug=args.debug))

    ok = verify_counts(entries)

    args.output.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"Saved {len(entries)} entries to {args.output}")

    if not ok:
        logger.warning("Some counts are outside expected ranges. Review output.")

    # Print summary
    namespaces = sorted({e["namespace"] for e in entries if e["namespace"]})
    logger.info(f"Namespaces found ({len(namespaces)}): {', '.join(namespaces)}")
    logger.info("Done.")


if __name__ == "__main__":
    main()
