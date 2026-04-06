"""
promote_live_to_local.py
─────────────────────────────────────────────────────────────────────────────
Takes tradingview_live-only entries from tv_scraped_entries.json and
persists them into pinescript_chunks.json so they have full local coverage.

After running, re-index with: python merge_and_index.py --reset
"""

from __future__ import annotations

import json
from pathlib import Path
from loguru import logger
import sys

logger.remove()
logger.add(sys.stderr, format="{time:HH:mm:ss} | {level:<8} | {message}", level="INFO")

BASE = Path(__file__).parent
LOCAL_FILE = BASE / "pinescript_chunks.json"
LIVE_FILE = BASE / "tv_scraped_entries.json"
USER_FILE = BASE / "user_docs_chunks.json"


def normalize_key(entry: dict) -> str:
    name = entry.get("name", "").lower().strip().replace(" ", "").replace("()", "").replace("`", "")
    category = entry.get("category", "")
    return f"{name}__{category}" if category else name


def convert_live_to_local_format(live_entry: dict) -> dict:
    """Convert a live-scraped entry to local_chunks format."""
    return {
        "id": live_entry.get("id", live_entry.get("name", "")),
        "name": live_entry.get("name", ""),
        "category": live_entry.get("category", "function"),
        "namespace": live_entry.get("namespace") or "",
        "syntax": live_entry.get("syntax") or "",
        "description": live_entry.get("description") or "",
        "parameters": live_entry.get("parameters") or [],
        "returns": live_entry.get("returns") or "",
        "remarks": live_entry.get("remarks") or "",
        "examples": live_entry.get("examples") or [],
        "see_also": live_entry.get("see_also") or [],
        "raw_text": _build_raw_text(live_entry),
        # Preserve live-specific fields for traceability
        "url": live_entry.get("url", ""),
        "overloads": live_entry.get("overloads") or [],
        "type_fields": live_entry.get("type_fields") or [],
        "type_methods": live_entry.get("type_methods") or [],
        "promoted_from_live": True,
        "promoted_at": "2026-04-04T00:00:00",
    }


def _build_raw_text(entry: dict) -> str:
    parts = [f"## {entry.get('name', '')}"]
    syntax = entry.get("syntax") or ""
    if syntax:
        parts.append(f"\n{syntax}")
    desc = entry.get("description") or ""
    if desc:
        parts.append(f"\n{desc}")
    ret = entry.get("returns") or ""
    if ret:
        parts.append(f"\nReturns: {ret}")
    remarks = entry.get("remarks") or ""
    if remarks:
        parts.append(f"\n{remarks}")
    for ex in entry.get("examples") or []:
        parts.append(f"\n```\n{ex}\n```")
    return "\n".join(parts)


def main():
    logger.info("Loading local chunks...")
    local = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
    local_keys = {normalize_key(e) for e in local}
    logger.info(f"  Local entries: {len(local)}")

    logger.info("Loading user docs chunks...")
    user = json.loads(USER_FILE.read_text(encoding="utf-8"))
    user_keys = {normalize_key(e) for e in user}
    logger.info(f"  User doc entries: {len(user)}")

    logger.info("Loading live scraped entries...")
    live = json.loads(LIVE_FILE.read_text(encoding="utf-8"))
    logger.info(f"  Live entries: {len(live)}")

    # Find live-only entries (not in local or user)
    promoted = []
    for entry in live:
        key = normalize_key(entry)
        if key not in local_keys and key not in user_keys:
            converted = convert_live_to_local_format(entry)
            promoted.append(converted)

    logger.info(f"Live-only entries to promote: {len(promoted)}")

    if not promoted:
        logger.info("Nothing to promote. All live entries already have local coverage.")
        return

    # Show sample
    logger.info("Sample entries being promoted:")
    for e in sorted(promoted, key=lambda x: x["name"])[:10]:
        logger.info(f"  {e['name']} ({e['category']})")
    if len(promoted) > 10:
        logger.info(f"  ... and {len(promoted) - 10} more")

    # Append to local chunks
    updated = local + promoted
    LOCAL_FILE.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Wrote {len(updated)} entries to {LOCAL_FILE}")

    # Verify
    verify = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
    verify_keys = {normalize_key(e) for e in verify}
    live_names = {normalize_key(e) for e in live}
    still_missing = live_names - verify_keys
    if still_missing:
        logger.warning(f"  Still missing after promote: {len(still_missing)}")
        for k in sorted(still_missing)[:5]:
            logger.warning(f"    {k}")
    else:
        logger.info("  ALL live entries now have local coverage. 100%.")

    logger.info("Next step: python merge_and_index.py --reset")


if __name__ == "__main__":
    main()
