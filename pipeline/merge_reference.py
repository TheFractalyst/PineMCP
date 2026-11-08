"""
merge_reference.py
------------------------------------------------------------------------------
STAGE 3: Merge a live reference scrape into the shipped reference dataset.

Reads the raw scrape (tv_scraped_entries.json, from scrape_entries.py) and the
current data/pine_merged_entries.json, then rewrites the shipped file:

  - one canonical entry per (name, category) - overload variants collapse
  - live data wins for syntax/overloads/params/returns/remarks/description
  - examples: live ones (newline-clean) when present, legacy ones otherwise
  - legacy entries not on the live page are kept unless their name is a
    signature string (e.g. 'color.t(color) -> series float')

Usage:
    python pipeline/merge_reference.py [--live FILE] [--out FILE] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<8} | {message}", level="INFO")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIVE = ROOT / "data" / "tv_scraped_entries.json"
DEFAULT_CURRENT = ROOT / "data" / "pine_merged_entries.json"

MIN_LIVE_ENTRIES = 850
# Sanity symbols: shipped data must always contain these (regression guard).
REQUIRED_SYMBOLS = [
    ("once", "keyword"),
    ("runtime.calculation.mode", "variable"),
    ("array.sort", "function"),
    ("strategy", "function"),
    ("ta.ema", "function"),
]


def is_signature_name(name: str) -> bool:
    """True for legacy entries whose name embeds a full signature."""
    n = name.strip()
    return "→" in n or "->" in n or "(" in n


def merge(
    live: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Merge live canonical entries over the current dataset."""
    current_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for e in current:
        current_by_key.setdefault((e["name"].lower(), e.get("category", "")), e)

    stats = {"live_new": 0, "live_updated": 0, "legacy_kept": 0, "legacy_dropped": 0}

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    live_categories: dict[str, set[str]] = {}
    for live_e in live:
        key = (live_e["name"].lower(), live_e.get("category", ""))
        old = current_by_key.get(key)
        if old is None:
            stats["live_new"] += 1
        else:
            stats["live_updated"] += 1
        entry = dict(live_e)

        # Fall back to legacy content the live page does not carry.
        if old:
            if not entry.get("examples") and old.get("examples"):
                entry["examples"] = old["examples"]
            if not entry.get("parameters") and old.get("parameters"):
                entry["parameters"] = old["parameters"]
            if not entry.get("remarks") and old.get("remarks"):
                entry["remarks"] = old["remarks"]
            legacy_see = set(old.get("see_also") or [])
            entry["see_also"] = list(dict.fromkeys((entry.get("see_also") or []) + sorted(legacy_see)))
            sources = list(dict.fromkeys((old.get("sources") or []) + ["tradingview_live"]))
            entry["sources"] = sources
        else:
            entry["sources"] = ["tradingview_live"]
        merged[key] = entry
        live_categories.setdefault(key[0], set()).add(key[1])

    for key, old in current_by_key.items():
        if key in merged:
            continue
        # The live page moved this symbol to another category (e.g. strategy
        # constant -> function); keeping the stale row would shadow the fresh one.
        if old["name"].lower() in live_categories:
            stats["legacy_dropped"] += 1
            continue
        if is_signature_name(old["name"]):
            stats["legacy_dropped"] += 1
            continue
        stats["legacy_kept"] += 1
        merged[key] = dict(old)

    out = sorted(merged.values(), key=lambda e: (e.get("category", ""), e["name"].lower()))
    return out, stats


def verify(entries: list[dict[str, Any]], live_count: int) -> list[str]:
    """Return a list of invariant violations (empty list = healthy dataset)."""
    problems: list[str] = []
    if len(entries) < live_count:
        problems.append(f"entry count {len(entries)} < live count {live_count}")
    keys = [(e["name"].lower(), e.get("category", "")) for e in entries]
    if len(keys) != len(set(keys)):
        problems.append("duplicate (name, category) keys")
    for e in entries:
        if is_signature_name(e["name"]):
            problems.append(f"signature-named entry survived: {e['name']!r}")
            break
    for name, cat in REQUIRED_SYMBOLS:
        if not any(e["name"].lower() == name and e.get("category") == cat for e in entries):
            problems.append(f"required symbol missing: {name} ({cat})")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge live scrape into the shipped reference data")
    parser.add_argument("--live", type=Path, default=DEFAULT_LIVE, help="Scrape file from scrape_entries.py")
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT, help="Current shipped dataset")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    live = json.loads(args.live.read_text(encoding="utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    logger.info(f"Live entries:   {len(live)}")
    logger.info(f"Current entries: {len(current)}")

    merged, stats = merge(live, current)
    logger.info(f"Merge: {stats}")

    problems = verify(merged, len(live))
    for p in problems:
        logger.error(f"VERIFY FAIL: {p}")
    if problems:
        logger.error("Dataset failed verification - NOT writing output")
        return 1

    # Tag the refresh so consumers can see data age.
    now = datetime.now(timezone.utc).isoformat()
    for e in merged:
        if e.get("source") == "tradingview_live" or "tradingview_live" in (e.get("sources") or []):
            e["scraped_at"] = e.get("scraped_at") or now

    if args.dry_run:
        logger.info("Dry run - not writing")
        return 0

    args.current.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    size_mb = args.current.stat().st_size / 1e6
    logger.info(f"Wrote {len(merged)} entries to {args.current} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
