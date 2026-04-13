#!/usr/bin/env python3

# ==================== PHASE 0 — IMPORTS AND SAFETY SETUP ====================

import chromadb, re, json, time, hashlib
from chromadb.utils import embedding_functions
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

client = chromadb.PersistentClient(path="./pinescript_db")
# Try to get existing collection without embedding function first
try:
    col = client.get_collection("pinescript_v6")
    print("Using existing collection")
except:
    # If doesn't exist, create with embedding function
    emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    col = client.create_collection("pinescript_v6", embedding_function=emb_fn)
    print("Created new collection with embedding function")

count_before = col.count()
assert count_before >= 2000, f"SAFETY ABORT: only {count_before} entries"
print(f"✅ Safety guard: {count_before} entries confirmed\n")

# Rate limiting — be respectful to TradingView
FETCH_DELAY = 1.2  # seconds between requests

# Use urllib for HTTP requests
import urllib.request, urllib.error

def fetch(url: str, retries=3) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PineScript-MCP-Auditor/1.0)"
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as response:
                time.sleep(FETCH_DELAY)
                if response.status == 200:
                    return response.read().decode('utf-8', errors='ignore')
            print(f"  HTTP {response.status} for {url}")
        except Exception as e:
            print(f"  Fetch error attempt {attempt+1}: {e}")
            time.sleep(3)
    return ""

# ==================== PHASE 1 — BUILD GROUND TRUTH FROM OFFICIAL SOURCES ====================

# GROUND TRUTH STRATEGY (in priority order):
# 1. Parse the already-cloned GitHub repo markdown files (most reliable)
# 2. Fetch TradingView reference index page for any namespace not in repo
# 3. Cross-check result counts per namespace

# Step 1A — Extract all named entries from cloned GitHub repo
repo_root = Path("./pinescriptv6")

@dataclass
class DocEntry:
    name: str
    namespace: str
    entry_type: str   # function | variable | constant | type | keyword
    signature: str = ""
    description: str = ""
    source_file: str = ""

def parse_reference_file(path: Path, default_type: str) -> list[DocEntry]:
    """
    Parse a reference markdown file and extract all named entries.
    Each H3 heading is treated as one entry name.
    """
    text    = path.read_text(encoding="utf-8", errors="ignore")
    entries = []

    # Split on H2 (namespace sections) and H3 (individual entries)
    h2_pattern = re.compile(r'^## (.+)$', re.MULTILINE)
    h3_pattern = re.compile(r'^### (.+)$', re.MULTILINE)

    # Find all H3 headings and their content
    h3_matches = list(h3_pattern.finditer(text))
    for i, match in enumerate(h3_matches):
        heading = match.group(1).strip()
        start   = match.end()
        end     = h3_matches[i+1].start() if i+1 < len(h3_matches) else len(text)
        body    = text[start:end].strip()

        # Extract namespace from heading (e.g. "ta.ema" → "ta")
        if "." in heading:
            ns = heading.split(".")[0].lower()
        else:
            ns = path.stem.lower()

        # Extract clean name (strip markdown formatting)
        name = re.sub(r'[`*_\[\]()]', '', heading).strip()
        name = re.sub(r'\(.*\)$', '', name).strip()  # remove "()" suffix

        # Get first line as signature, rest as description
        lines       = body.split("\n")
        signature   = lines[0][:200] if lines else ""
        description = "\n".join(lines[1:6]).strip()

        entries.append(DocEntry(
            name        = name.lower(),
            namespace   = ns,
            entry_type  = default_type,
            signature   = signature,
            description = description,
            source_file = str(path.relative_to(repo_root))
        ))
    return entries

# Parse all reference files
all_ground_truth: list[DocEntry] = []

reference_files = {
    repo_root / "reference" / "functions" / "ta.md":       "function",
    repo_root / "reference" / "functions" / "strategy.md": "function",
    repo_root / "reference" / "functions" / "drawing.md":  "function",
    repo_root / "reference" / "functions" / "general.md":  "function",
    repo_root / "reference" / "functions" / "request.md":  "function",
    repo_root / "reference" / "functions" / "collections.md": "function",
    repo_root / "reference" / "variables.md":              "variable",
    repo_root / "reference" / "constants.md":              "constant",
    repo_root / "reference" / "types.md":                  "type",
    repo_root / "reference" / "keywords.md":               "keyword",
    repo_root / "reference" / "operators.md":              "operator",
    repo_root / "reference" / "annotations.md":            "annotation",
}

for file_path, entry_type in reference_files.items():
    if not file_path.exists():
        # Try flat structure if subdirectory doesn't exist
        alt = repo_root / "reference" / file_path.name
        if alt.exists():
            file_path = alt
        else:
            print(f"  ⚠️  Not found: {file_path}")
            continue
    entries = parse_reference_file(file_path, entry_type)
    all_ground_truth.extend(entries)
    print(f"  📄 {file_path.name}: {len(entries)} entries")

print(f"\nGround truth total: {len(all_ground_truth)} entries from GitHub repo")

# Step 1B — Also parse ALL markdown files in repo for any entries missed above
for md_file in sorted(repo_root.rglob("*.md")):
    # Skip files already parsed above
    if md_file in reference_files:
        continue
    # Skip giant files
    if md_file.stat().st_size > 200_000:
        continue
    # Skip non-reference files
    if md_file.name in ("README.md", "LLM_MANIFEST.md", "release_notes.md"):
        continue

    text = md_file.read_text(encoding="utf-8", errors="ignore")
    # Extract any H3 entries that look like Pine identifiers
    h3_pattern = re.compile(r'^### (`?)([a-zA-Z_][a-zA-Z0-9_.]*)\1', re.MULTILINE)
    for match in h3_pattern.finditer(text):
        name = match.group(2).lower()
        if "." in name:
            ns = name.split(".")[0]
        else:
            ns = md_file.stem.lower()
        # Only add if not already in ground truth
        if not any(e.name == name for e in all_ground_truth):
            all_ground_truth.append(DocEntry(
                name=name, namespace=ns,
                entry_type="guide",
                source_file=str(md_file.relative_to(repo_root))
            ))

print(f"Ground truth after full scan: {len(all_ground_truth)} entries")

# ==================== PHASE 2 — CROSS-CHECK DB AGAINST GROUND TRUTH ====================

print("\n" + "="*60)
print("PHASE 2: CROSS-CHECKING DB vs GROUND TRUTH")
print("="*60)

missing_entries  = []
present_entries  = []
hollow_entries   = []  # exist but document < 80 chars

for gt in all_ground_truth:
    r = col.get(where={"name": gt.name}, include=["documents", "metadatas"])

    if not r["ids"]:
        missing_entries.append(gt)
    else:
        doc = r["documents"][0] if r["documents"] else ""
        if len(doc) < 80:
            hollow_entries.append(gt)
        else:
            present_entries.append(gt)

print(f"\n✅ Present with full docs: {len(present_entries)}")
print(f"⚠️  Present but hollow (<80 chars): {len(hollow_entries)}")
print(f"❌ Missing entirely: {len(missing_entries)}")

# Print missing by namespace
missing_by_ns = {}
for e in missing_entries:
    missing_by_ns.setdefault(e.namespace, []).append(e.name)

print(f"\nMissing entries by namespace:")
for ns, names in sorted(missing_by_ns.items(), key=lambda x: -len(x[1])):
    print(f"  {ns}: {len(names)} missing")
    for name in names[:5]:
        print(f"    - {name}")
    if len(names) > 5:
        print(f"    ... and {len(names)-5} more")

# ==================== PHASE 3 — FETCH MISSING ENTRIES FROM OFFICIAL TV DOCS ====================

print("\n" + "="*60)
print("PHASE 3: FETCHING MISSING ENTRIES FROM TRADINGVIEW")
print("="*60)

BASE_TV  = "https://www.tradingview.com/pine-script-reference/v6/"
BASE_DOCS = "https://www.tradingview.com/pine-script-docs/"

# Namespace → URL map for bulk reference pages
# TradingView reference is a SPA but we can try static fallbacks
TV_NAMESPACE_URLS = {
    "ta":        BASE_TV + "?query=ta.",
    "strategy":  BASE_TV + "?query=strategy.",
    "array":     BASE_TV + "?query=array.",
    "matrix":    BASE_TV + "?query=matrix.",
    "map":       BASE_TV + "?query=map.",
    "request":   BASE_TV + "?query=request.",
    "math":      BASE_TV + "?query=math.",
    "str":       BASE_TV + "?query=str.",
    "color":     BASE_TV + "?query=color.",
    "line":      BASE_TV + "?query=line.",
    "label":     BASE_TV + "?query=label.",
    "box":       BASE_TV + "?query=box.",
    "table":     BASE_TV + "?query=table.",
    "chart":     BASE_TV + "?query=chart.",
    "syminfo":   BASE_TV + "?query=syminfo.",
    "ticker":    BASE_TV + "?query=ticker.",
    "timeframe": BASE_TV + "?query=timeframe.",
    "input":     BASE_TV + "?query=input.",
    "polyline":  BASE_TV + "?query=polyline.",
    "runtime":   BASE_TV + "?query=runtime.",
    "barstate":  BASE_TV + "?query=barstate.",
    "extend":    BASE_TV + "?query=extend.",
    "footprint": BASE_TV + "?query=footprint.",
}

# For each missing entry: try to build best-effort document
# from ground truth data + attempt live fetch
def build_entry_document(gt: DocEntry, live_text: str = "") -> str:
    """Build a document string from ground truth data and optional live content."""

    # Determine anchor type
    anchor_map = {
        "function":  "fun",
        "variable":  "var",
        "constant":  "const",
        "type":      "type",
        "keyword":   "kw",
        "operator":  "op",
        "annotation": "annotation",
    }
    anchor = anchor_map.get(gt.entry_type, "fun")
    url    = f"{BASE_TV}#{anchor}_{gt.name}"

    parts = [
        f"{gt.name} — {gt.entry_type}",
        f"Namespace: {gt.namespace}",
    ]
    if gt.signature:
        parts.append(f"Signature: {gt.signature}")
    if gt.description:
        parts.append(f"\n{gt.description}")

    # Extract relevant section from live HTML if available
    if live_text:
        # Look for a section containing the entry name
        pattern = re.compile(
            rf'({re.escape(gt.name)}[^<]{{0,500}})',
            re.IGNORECASE | re.DOTALL
        )
        match = pattern.search(live_text)
        if match:
            snippet = re.sub(r'<[^>]+>', ' ', match.group(1))
            snippet = re.sub(r'\s+', ' ', snippet).strip()[:400]
            parts.append(f"\nFrom official docs:\n{snippet}")

    parts.append(f"\nOfficial reference: {url}")
    return "\n".join(parts)

# Process all missing entries
new_ids, new_docs, new_metas = [], [], []
fetch_cache = {}  # URL → HTML text cache

for i, gt in enumerate(missing_entries):
    # Try to get live content for this namespace (cached)
    ns_url = TV_NAMESPACE_URLS.get(gt.namespace, "")
    live_html = ""
    if ns_url and ns_url not in fetch_cache:
        print(f"  🌐 Fetching namespace docs: {gt.namespace}")
        fetch_cache[ns_url] = fetch(ns_url)
    if ns_url:
        live_html = fetch_cache.get(ns_url, "")

    # Try individual entry URL
    anchor = 'fun' if gt.entry_type == 'function' else 'var'
    entry_url = f"{BASE_TV}#{anchor}_{gt.name}"
    
    doc  = build_entry_document(gt, live_html)
    uid  = f"audit_{gt.name.replace('.','_')}_{hashlib.md5(gt.name.encode()).hexdigest()[:6]}"

    new_ids.append(uid)
    new_docs.append(doc)
    new_metas.append({
        "name":      gt.name,
        "namespace": gt.namespace,
        "type":      gt.entry_type,
        "source":    "audit_sync_v1",
        "version":   "v6",
        "file":      gt.source_file,
    })

    if (i+1) % 50 == 0:
        print(f"  Prepared {i+1}/{len(missing_entries)} entries...")

# Also add hollow entries with enriched content
for gt in hollow_entries:
    doc = build_entry_document(gt)
    uid = f"audit_enrich_{gt.name.replace('.','_')}_{hashlib.md5(gt.name.encode()).hexdigest()[:6]}"
    new_ids.append(uid)
    new_docs.append(doc)
    new_metas.append({
        "name":      gt.name,
        "namespace": gt.namespace,
        "type":      gt.entry_type,
        "source":    "audit_sync_enriched",
        "version":   "v6",
        "file":      gt.source_file,
    })

print(f"\nTotal entries to upsert: {len(new_ids)}")
print(f"  New (missing): {len(missing_entries)}")
print(f"  Enriched (hollow): {len(hollow_entries)}")

# ==================== PHASE 4 — FETCH CRITICAL CONCEPT PAGES NOT IN REPO ====================

print("\n" + "="*60)
print("PHASE 4: FETCHING CONCEPT PAGES MISSING FROM REPO")
print("="*60)

# These concept pages are not in the GitHub repo
# Fetch them directly and chunk them
MISSING_CONCEPT_PAGES = [
    ("execution_model_full",
     "https://www.tradingview.com/pine-script-docs/concepts/execution-model/",
     "concepts"),
    ("strategies_full",
     "https://www.tradingview.com/pine-script-docs/concepts/strategies/",
     "concepts"),
    ("type_system_full",
     "https://www.tradingview.com/pine-script-docs/language/type-system/",
     "language"),
    ("other_timeframes",
     "https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/",
     "concepts"),
    ("debugging_full",
     "https://www.tradingview.com/pine-script-docs/writing/debugging/",
     "writing"),
    ("enums",
     "https://www.tradingview.com/pine-script-docs/language/enums/",
     "language"),
    ("user_defined_types",
     "https://www.tradingview.com/pine-script-docs/language/user-defined-types/",
     "language"),
    ("methods",
     "https://www.tradingview.com/pine-script-docs/language/methods/",
     "language"),
    ("objects",
     "https://www.tradingview.com/pine-script-docs/language/objects/",
     "language"),
    ("loops",
     "https://www.tradingview.com/pine-script-docs/language/loops/",
     "language"),
    ("conditional_structures",
     "https://www.tradingview.com/pine-script-docs/language/conditional-structures/",
     "language"),
    ("arrays",
     "https://www.tradingview.com/pine-script-docs/language/arrays/",
     "language"),
    ("maps",
     "https://www.tradingview.com/pine-script-docs/language/maps/",
     "language"),
    ("matrices",
     "https://www.tradingview.com/pine-script-docs/language/matrices/",
     "language"),
    ("alerts",
     "https://www.tradingview.com/pine-script-docs/concepts/alerts/",
     "concepts"),
    ("bar_coloring",
     "https://www.tradingview.com/pine-script-docs/concepts/bar-coloring/",
     "concepts"),
    ("fills",
     "https://www.tradingview.com/pine-script-docs/concepts/fills/",
     "concepts"),
    ("levels",
     "https://www.tradingview.com/pine-script-docs/concepts/levels/",
     "concepts"),
    ("lines_and_boxes",
     "https://www.tradingview.com/pine-script-docs/concepts/lines-and-boxes/",
     "concepts"),
    ("plots",
     "https://www.tradingview.com/pine-script-docs/concepts/plots/",
     "concepts"),
    ("tables",
     "https://www.tradingview.com/pine-script-docs/concepts/tables/",
     "concepts"),
    ("text_and_shapes",
     "https://www.tradingview.com/pine-script-docs/concepts/text-and-shapes/",
     "concepts"),
    ("time",
     "https://www.tradingview.com/pine-script-docs/concepts/time/",
     "concepts"),
]

def extract_text_from_html(html: str) -> str:
    """Strip HTML tags and clean up whitespace."""
    # Remove script and style blocks
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
    html = re.sub(r'<style[^>]*>.*?</style>',  '', html, flags=re.DOTALL)
    # Convert headings to markdown-style
    html = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', html, flags=re.DOTALL)
    html = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', html, flags=re.DOTALL)
    html = re.sub(r'<h4[^>]*>(.*?)</h4>', r'\n#### \1\n', html, flags=re.DOTALL)
    # Convert code blocks
    html = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>',
                  lambda m: '\n```\n' + m.group(1) + '\n```\n',
                  html, flags=re.DOTALL)
    # Strip remaining tags
    html = re.sub(r'<[^>]+>', ' ', html)
    # Decode entities
    html = html.replace('&lt;','<').replace('&gt;','>').replace('&amp;','&')
    html = html.replace('&quot;','"').replace('&#39;',"'").replace('&nbsp;',' ')
    # Clean whitespace
    html = re.sub(r'\n{3,}', '\n\n', html)
    html = re.sub(r'[ \t]{2,}', ' ', html)
    return html.strip()

def chunk_text(text: str, page_slug: str, namespace: str) -> list[dict]:
    """Split text on ## or ### headings into chunks."""
    pattern = re.compile(r'^#{2,3}\s+(.+)$', re.MULTILINE)
    matches = list(pattern.finditer(text))

    if not matches:
        return [{"id": f"concept_{page_slug}_full",
                 "doc": text[:2000],
                 "name": page_slug,
                 "ns": namespace}]

    chunks = []
    for i, match in enumerate(matches):
        heading = re.sub(r'<[^>]+>', '', match.group(1)).strip()
        start   = match.end()
        end     = matches[i+1].start() if i+1 < len(matches) else len(text)
        body    = text[start:end].strip()

        if len(body) < 40:
            continue

        slug = re.sub(r'[^a-z0-9_]', '_', heading.lower())[:40]
        uid  = f"concept_{page_slug}_{slug}_{i:03d}"
        doc  = f"{heading}\n\n{body[:1500]}"

        # Extract best name from heading
        m = re.search(r'\b([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+)', heading.lower())
        name = m.group(1) if m else slug

        chunks.append({"id": uid, "doc": doc, "name": name, "ns": namespace})

    return chunks

for page_slug, url, ns in MISSING_CONCEPT_PAGES:
    # Skip if we already have entries for this page
    r = col.get(where={"source": f"concept_{page_slug}"}, include=["metadatas"])
    if r["ids"]:
        print(f"  ⏭️  Already indexed: {page_slug} ({len(r['ids'])} entries)")
        continue

    print(f"  🌐 Fetching: {url}")
    html = fetch(url)

    if not html or len(html) < 500:
        print(f"     ⚠️  Empty response (SPA or network issue)")
        # Still add a stub entry pointing to the URL
        new_ids.append(f"concept_{page_slug}_stub")
        new_docs.append(
            f"{page_slug.replace('_',' ').title()}\n\n"
            f"Official documentation: {url}\n\n"
            f"Note: This page uses JavaScript rendering. "
            f"Visit the URL directly for full content."
        )
        new_metas.append({
            "name": page_slug,
            "namespace": ns,
            "type": "guide",
            "source": f"concept_{page_slug}",
            "version": "v6",
            "url": url
        })
        continue

    text   = extract_text_from_html(html)
    chunks = chunk_text(text, page_slug, ns)
    print(f"     ✅ {len(chunks)} chunks extracted ({len(text)//1024}KB)")

    for chunk in chunks:
        new_ids.append(chunk["id"])
        new_docs.append(chunk["doc"])
        new_metas.append({
            "name":      chunk["name"],
            "namespace": chunk["ns"],
            "type":      "guide",
            "source":    f"concept_{page_slug}",
            "version":   "v6",
            "url":       url,
        })

print(f"\nConcept page entries to add: {len([i for i in new_ids if i.startswith('concept_')])}")

# ==================== PHASE 5 — UPSERT ALL NEW ENTRIES ====================

print("\n" + "="*60)
print("PHASE 5: UPSERTING ALL NEW ENTRIES")
print("="*60)

if not new_ids:
    print("Nothing to upsert — DB is already complete.")
else:
    BATCH = 200
    for i in range(0, len(new_ids), BATCH):
        col.upsert(
            ids=       new_ids  [i:i+BATCH],
            documents= new_docs [i:i+BATCH],
            metadatas= new_metas[i:i+BATCH],
        )
        print(f"  Upserted {min(i+BATCH, len(new_ids))}/{len(new_ids)}...")

count_after = col.count()
print(f"\nBefore: {count_before} → After: {count_after} (+{count_after-count_before})")

# ==================== PHASE 6 — GENERATE FULL COVERAGE REPORT ====================

print("\n" + "="*60)
print("PHASE 6: FINAL COVERAGE REPORT")
print("="*60)

# Re-run cross-check after upsert
still_missing = []
now_present   = []
for gt in missing_entries + hollow_entries:
    r = col.get(where={"name": gt.name}, include=["documents"])
    if not r["ids"] or len(r["documents"][0]) < 80:
        still_missing.append(gt)
    else:
        now_present.append(gt)

total_ground_truth = len(all_ground_truth)
total_covered      = len(present_entries) + len(now_present)
coverage_pct       = round(total_covered / total_ground_truth * 100, 1)

print(f"""
╔══════════════════════════════════════════════════════╗
║         PINESCRIPT v6 MCP — COVERAGE REPORT         ║
╠══════════════════════════════════════════════════════╣
║  Ground truth entries (from official docs):  {total_ground_truth:>5}  ║
║  Covered in DB:                              {total_covered:>5}  ║
║  Coverage:                                  {coverage_pct:>5}%  ║
║  DB total entries:                           {count_after:>5}  ║
╠══════════════════════════════════════════════════════╣
║  Still missing:                              {len(still_missing):>5}  ║
╚══════════════════════════════════════════════════════╝
""")

# Namespace coverage breakdown
print("Coverage by namespace:")
ns_gt     = {}
ns_covered = {}
for gt in all_ground_truth:
    ns_gt[gt.namespace] = ns_gt.get(gt.namespace, 0) + 1
for gt in present_entries + now_present:
    ns_covered[gt.namespace] = ns_covered.get(gt.namespace, 0) + 1

for ns in sorted(ns_gt.keys()):
    total = ns_gt[ns]
    covered = ns_covered.get(ns, 0)
    pct = round(covered/total*100) if total > 0 else 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    status = "✅" if pct >= 90 else "⚠️ " if pct >= 70 else "❌"
    print(f"  {status} {ns:<15} {bar} {covered:>3}/{total:<3} ({pct}%)")

if still_missing:
    print(f"\nRemaining gaps ({len(still_missing)} entries):")
    by_ns = {}
    for e in still_missing:
        by_ns.setdefault(e.namespace, []).append(e.name)
    for ns, names in sorted(by_ns.items(), key=lambda x: -len(x[1])):
        print(f"  {ns}: {names[:10]}")

# Save full report to file
report = {
    "timestamp":        time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "db_total":         count_after,
    "coverage_pct":     coverage_pct,
    "ground_truth":     total_ground_truth,
    "covered":          total_covered,
    "still_missing":    [(e.name, e.namespace) for e in still_missing],
    "namespace_coverage": {
        ns: {"covered": ns_covered.get(ns,0), "total": ns_gt[ns]}
        for ns in ns_gt
    }
}
with open("mcp_coverage_report.json", "w") as f:
    json.dump(report, f, indent=2)
print(f"\n📊 Full report saved: mcp_coverage_report.json")

# ==================== PHASE 7 — SEMANTIC SMOKE TESTS ====================

print("\n" + "="*60)
print("PHASE 7: SEMANTIC SMOKE TESTS")
print("="*60)

smoke_tests = [
    ("ta functions",         "exponential moving average length source"),
    ("strategy entries",     "enter long position market order barstate confirmed"),
    ("array methods",        "add element to end of array push pop"),
    ("matrix operations",    "multiply two matrices linear algebra"),
    ("map operations",       "key value store map put get contains"),
    ("request.security",     "fetch data from other timeframe symbol"),
    ("barstate variables",   "confirmed bar avoid repainting"),
    ("type system",          "series simple const input type casting"),
    ("execution model",      "bar replay order history realtime"),
    ("drawing lines",        "draw horizontal line extend right price level"),
    ("strategy exits",       "stop loss take profit trailing stop"),
    ("profiling",            "measure script execution time performance bottleneck"),
    ("common errors",        "undeclared identifier cannot call non-function"),
    ("footprint orderflow",  "buy sell volume delta footprint poc value area"),
    ("alerts",               "alert condition trigger message"),
    ("enums",                "enum type user defined enumeration v6"),
    ("methods",              "user defined method dot notation type"),
    ("debugging",            "log info error debug pine script"),
    ("extend enum",          "extend line right both none draw"),
    ("label styles",         "label arrow up down shape style"),
]

passed = 0
for label, query in smoke_tests:
    r    = col.query(query_texts=[query], n_results=1)
    doc  = r["documents"][0][0] if r["documents"][0] else ""
    meta = r["metadatas"][0][0] if r["metadatas"][0] else {}
    ok   = len(doc) > 50
    passed += ok
    ns   = meta.get("namespace", "?")
    src  = meta.get("source", "?")[:20]
    prev = doc[:80].replace("\n"," ")
    print(f"  {'✅' if ok else '❌'} {label:<25} ns={ns} src={src}")
    if not ok:
        print(f"       EMPTY RESULT — '{query}' returned nothing")

print(f"\nSmoke tests: {passed}/{len(smoke_tests)}")
print(f"{'🎉 MCP FULLY AUDITED AND LOADED' if passed >= 18 else '⚠️  SOME GAPS REMAIN'}")
print(f"\nFinal DB total: {col.count()}")
