"""
pipeline/scrape_docs.py
------------------------------------------------------------------------------
STAGE: Ingest the Pine Script v6 User Manual from the official docs website.

Walks the docs sitemap (v6 pages only), fetches each page with httpx, and
slices its `#slot-container` content at natural heading boundaries (h1-h4).
Code blocks (`div.pine-colorizer`) are preserved verbatim and also stored in
`examples` so pine_search(has_examples=True) can return runnable snippets.

Completeness guarantees (script exits non-zero otherwise):
  - every v6 page yields at least one entry
  - the page-level entry for each part exists (primer, language, visuals,
    concepts, writing, errors, faq, release, migration, welcome)

Usage:
    .venv/bin/python pipeline/scrape_docs.py
    .venv/bin/python pipeline/scrape_docs.py --sitemap URL --output FILE

Output: data/pine_docs_entries.json (~1300 guide entries with real URLs)
Requires: httpx (core dep), beautifulsoup4 + lxml (pipeline extra)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, NavigableString, Tag
from loguru import logger

logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<8} | {message}", level="INFO")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SITEMAP = "https://www.tradingview.com/pine-script-docs/sitemap-0.xml"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "pine_docs_entries.json"
DOCS_BASE = "https://www.tradingview.com"
TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (compatible; PineMCP-docs-sync/1.0)"
REQUEST_DELAY = 0.25

# Top-level part slugs -> guide namespaces (kept in sync with ingest_manual.py)
PART_SLUGS = {
    "welcome": "welcome",
    "primer": "primer",
    "language": "language",
    "visuals": "visuals",
    "concepts": "concepts",
    "writing": "writing",
    "errors": "errors",
    "faq": "faq",
    "release-notes": "release",
    "migration-guides": "migration",
}

REQUIRED_PARTS = set(PART_SLUGS.values()) - {"welcome"}

_CODE_RE = re.compile(r"(ce|cw|re)\d{5}", re.IGNORECASE)


def norm_title(s: str) -> str:
    """Normalize a title: strip ZWSP/(R)/backticks, punctuation to spaces."""
    s = s.replace("\u200b", "").replace("\u00ae", "").replace("\u2019", "'").replace("`", "")
    s = re.sub(r"[^0-9a-zA-Z]+", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def clean_text(s: str) -> str:
    """Collapse PDF/HTML artifacts: zero-width spaces + repeated blank lines."""
    s = s.replace("\u200b", "")
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def block_text(el: Tag) -> str:
    """Plain text for a block element, keeping inline code intact."""
    for sup in el.find_all("sup", class_="footnote-ref"):
        sup.decompose()
    text = el.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def fetch(url: str, client: Any) -> str:
    """GET a page, following meta-refresh redirects; raise on non-200."""
    resp = client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} for {url}")
    # Index pages (/concepts/) redirect via <meta http-equiv=refresh>, not HTTP.
    m = re.search(r"url=([^\"']+)", resp.text[:2000])
    if m and "http-equiv" in resp.text[:2000]:
        target = m.group(1)
        if target.startswith("/"):
            target = DOCS_BASE + target
        logger.debug(f"meta-refresh: {url} -> {target}")
        resp = client.get(target, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} for {target}")
    return resp.text


def doc_urls(sitemap_text: str) -> list[tuple[str, str, str]]:
    """Parse sitemap into (url, part_slug, page_slug) for v6 pages only.

    Part-level index pages (/concepts/, /language/...) are meta-refresh
    redirects to their first child page - they add no content of their own
    and would duplicate every section, so only leaf pages are returned.
    """
    urls = re.findall(r"<loc>([^<]+)</loc>", sitemap_text)
    out: list[tuple[str, str, str]] = []
    for loc in urls:
        path = loc.split("/pine-script-docs", 1)[-1].strip("/")
        if not path or path.startswith(("v3/", "v2/", "v4/", "v5/")):
            continue  # legacy versions - not part of the v6 knowledge base
        segments = [seg for seg in path.split("/") if seg]
        part = segments[0]
        if part not in PART_SLUGS:
            continue
        if len(segments) < 2:
            # Part index page. welcome/ and release-notes/ hold their own
            # content; the other parts are meta-refresh stubs - skip those.
            if part in ("welcome", "release-notes"):
                out.append((loc.rstrip("/") + "/", PART_SLUGS[part], ""))
            continue
        out.append((loc.rstrip("/") + "/", PART_SLUGS[part], segments[1]))
    return sorted(set(out))


def parse_page(html: str, url: str, part: str, page: str) -> list[dict[str, Any]]:
    """Slice a docs page into one entry per heading (h1-h4)."""
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one("#slot-container") or soup.select_one("main.content")
    if container is None:
        raise RuntimeError(f"no content container on {url}")

    entries: list[dict[str, Any]] = []
    # current[i] = entry whose heading is at depth i (depth 0 = h1)
    stack: list[dict[str, Any] | None] = [None]

    for el in container.descendants:
        if isinstance(el, NavigableString):
            continue
        if not isinstance(el, Tag):
            continue
        name = el.name
        if name in ("h1", "h2", "h3", "h4"):
            depth = int(name[1]) - 1
            title = block_text(el)
            if not title:
                continue
            entry = {
                "name": clean_text(title),
                "category": "guide",
                "namespace": part,
                "path": "/".join([part, page]),
                "description": "",
                "examples": [],
                "parameters": [],
                "returns": "",
                "remarks": "",
                "see_also": [],
                "url": url,
                "sources": ["docs_site"],
                "id": "",
            }
            # ancestor path parts (depth > 0 = section under the page)
            if depth > 0:
                anchor = el.get("id") or ""
                parent = stack[min(depth, len(stack)) - 1]
                parent_path = parent["path"] if parent else f"{part}/{page}"
                entry["path"] = f"{parent_path}/{anchor}" if anchor else f"{parent_path}/{slugify(title)}"
            else:
                anchor = el.get("id") or page
                entry["path"] = f"{part}/{anchor}"
            entry["id"] = "docs_" + entry["path"].replace("/", "_")
            del stack[depth + 1 :]
            stack.extend([None] * (depth + 1 - len(stack)))
            stack[depth] = entry
            entries.append(entry)
            continue
        if name in ("nav", "aside", "footer", "button", "svg", "script", "style"):
            continue
        if not any(e is not None for e in stack) or stack[-1] is None:
            continue
        entry = stack[-1]
        if name == "div" and "pine-colorizer" in (el.get("class") or []):
            code = el.get_text().replace("\u00a0", " ").strip("\n")
            entry["examples"].append(code)
            entry["description"] += ("\n" if entry["description"] else "") + code
            continue
        if name in ("p", "ul", "ol", "pre", "blockquote"):
            text = block_text(el)
            if text:
                entry["description"] += ("\n" if entry["description"] else "") + text

    for entry in entries:
        entry["description"] = clean_text(entry["description"])

    # A page's own entry (the h1) often carries no intro text; give it a stub
    # instead of leaving a near-empty row in search results.
    for entry in entries:
        if len(entry["description"]) < 40 and entry["url"]:
            kids = entry.get("see_also") or []
            stub = f"Pine Script v6 User Manual section '{entry['name']}' ({entry['path']})."
            if kids:
                stub += f" Sections: {', '.join(kids[:8])}."
            entry["description"] = stub

    # see_also: nested sections under this section's path
    by_path = {e["path"]: e for e in entries}
    for entry in entries:
        kids = [e["name"] for p, e in by_path.items() if p.startswith(entry["path"] + "/") and p.count("/") == entry["path"].count("/") + 1]
        entry["see_also"] = kids
    return entries


def slugify(s: str) -> str:
    s = s.replace("\u200b", "").replace("\u00ae", "")
    s = re.sub(r"\s*\([^)]*\)", "", s)
    s = re.sub(r"[^0-9a-zA-Z]+", "-", s).strip("-").lower()
    return s


def tag_error_codes(entries: list[dict[str, Any]]) -> int:
    """Rename error pages to '<code> - <message>' so pine_lookup('CE10101') works."""
    n = 0
    for entry in entries:
        if entry["namespace"] != "errors":
            continue
        parts = entry["path"].split("/")
        # Error page entries sit at '<code>/<slug>' - match the slug (not the
        # URL segment) because long messages get truncated when slugged.
        m = re.match(r"^(CE|CW|RE)\d{5}$", parts[1] if len(parts) > 1 else "", re.IGNORECASE)
        if not m or len(parts) != 3:
            continue
        code = m.group(0).upper()
        msg = entry["name"]
        entry["name"] = f"{code} - {msg}"
        entry["description"] = f"Compiler error {code}: {msg}\n\n{entry['description']}"
        n += 1
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sitemap", default=DEFAULT_SITEMAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--delay", type=float, default=REQUEST_DELAY)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import httpx

    with httpx.Client(timeout=TIMEOUT) as client:
        logger.info(f"Fetching sitemap {args.sitemap}")
        pages = doc_urls(fetch(args.sitemap, client))
        logger.info(f"v6 doc pages: {len(pages)}")
        if len(pages) < 60:
            logger.error(f"Only {len(pages)} doc pages found - sitemap layout changed?")
            return 1

        all_entries: list[dict[str, Any]] = []
        for i, (url, part, page) in enumerate(pages, 1):
            html = fetch(url, client)
            try:
                entries = parse_page(html, url, part, page)
            except RuntimeError as e:
                logger.error(f"{e}")
                return 1
            if not entries:
                logger.error(f"No entries parsed from {url}")
                return 1
            all_entries.extend(entries)
            if i % 20 == 0:
                logger.info(f"  {i}/{len(pages)} pages, {len(all_entries)} entries")
            if args.delay:
                import time

                time.sleep(args.delay)

    n_errors = tag_error_codes(all_entries)
    parts = Counter(e["namespace"] for e in all_entries)
    for part, n in sorted(parts.items()):
        logger.info(f"  {part:<12} {n:>5} entries")
    logger.info(f"Total guide entries: {len(all_entries)} (error pages tagged: {n_errors})")

    missing_parts = REQUIRED_PARTS - set(parts)
    if missing_parts:
        logger.error(f"Missing required parts: {missing_parts}")
        return 1
    empty = [e["path"] for e in all_entries if len(e["description"]) < 40]
    if len(empty) > 20:
        logger.error(f"{len(empty)} entries with near-empty text, e.g. {empty[:5]}")
        return 1
    if len(all_entries) < 1000:
        logger.error(f"Only {len(all_entries)} guide entries parsed")
        return 1

    for e in all_entries:
        e["scraped_at"] = datetime.now(timezone.utc).isoformat()

    if args.dry_run:
        logger.info("Dry run - not writing")
        return 0

    args.output.write_text(json.dumps(all_entries, indent=1, ensure_ascii=False), encoding="utf-8")
    size_mb = args.output.stat().st_size / 1e6
    logger.info(f"Wrote {len(all_entries)} entries to {args.output} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
