#!/usr/bin/env python3

# PineScript v6 MCP Audit using Playwright
# Uses Playwright MCP to fetch TradingView docs with JavaScript rendering

import chromadb, re, json, time, hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict
import subprocess

# Connect to database
client = chromadb.PersistentClient(path="./pinescript_db")
try:
    col = client.get_collection("pinescript_v6")
    print("Using existing collection")
except:
    print("No collection found - creating new one")
    from chromadb.utils import embedding_functions
    emb_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="all-MiniLM-L6-v2"
    )
    col = client.create_collection("pinescript_v6", embedding_function=emb_fn)

count_before = col.count()
print(f"Current database: {count_before} entries")

@dataclass
class DocEntry:
    name: str
    namespace: str
    entry_type: str
    signature: str = ""
    description: str = ""
    source_file: str = ""

def parse_reference_file(path: Path, default_type: str) -> List[DocEntry]:
    """Parse a reference markdown file and extract all named entries."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    entries = []
    
    # Find all H3 headings
    h3_pattern = re.compile(r'^### (.+)$', re.MULTILINE)
    h3_matches = list(h3_pattern.finditer(text))
    
    for i, match in enumerate(h3_matches):
        heading = match.group(1).strip()
        start = match.end()
        end = h3_matches[i+1].start() if i+1 < len(h3_matches) else len(text)
        body = text[start:end].strip()
        
        # Extract namespace and name
        if "." in heading:
            ns = heading.split(".")[0].lower()
            name = heading.split(".")[1].lower()
        else:
            ns = path.stem.lower()
            name = heading.lower()
        
        # Clean name
        name = re.sub(r'[`*_\[\]()]', '', name).strip()
        name = re.sub(r'\(.*\)$', '', name).strip()
        
        # Skip "code example" entries
        if "code example" in name.lower():
            continue
            
        # Get first line as signature
        lines = body.split("\n")
        signature = lines[0][:200] if lines else ""
        description = "\n".join(lines[1:6]).strip()
        
        entries.append(DocEntry(
            name=name.lower(),
            namespace=ns,
            entry_type=default_type,
            signature=signature,
            description=description,
            source_file=str(path.relative_to(Path("./pinescriptv6")))
        ))
    return entries

# Parse local reference files first
repo_root = Path("./pinescriptv6")
all_ground_truth = []

reference_files = {
    "reference/functions/ta.md": "function",
    "reference/functions/strategy.md": "function", 
    "reference/functions/drawing.md": "function",
    "reference/functions/general.md": "function",
    "reference/functions/request.md": "function",
    "reference/functions/collections.md": "function",
    "reference/variables.md": "variable",
    "reference/constants.md": "constant",
    "reference/types.md": "type",
    "reference/keywords.md": "keyword",
    "reference/operators.md": "operator",
    "reference/annotations.md": "annotation",
}

print("\nParsing reference files...")
for file_path, entry_type in reference_files.items():
    full_path = repo_root / file_path
    if full_path.exists():
        entries = parse_reference_file(full_path, entry_type)
        all_ground_truth.extend(entries)
        print(f"  📄 {file_path}: {len(entries)} entries")
    else:
        print(f"  ⚠️  Not found: {file_path}")

print(f"\nGround truth from repo: {len(all_ground_truth)} entries")

# Cross-check against database to find missing entries
print("\n" + "="*60)
print("FINDING MISSING ENTRIES")
print("="*60)

missing_entries = []
for gt in all_ground_truth:
    r = col.get(where={"name": gt.name}, include=["documents", "metadatas"])
    
    if not r["ids"]:
        missing_entries.append(gt)

print(f"Missing entries: {len(missing_entries)}")

# Use Playwright to fetch TradingView documentation
def fetch_with_playwright(url: str) -> str:
    """Use Playwright MCP to fetch a URL with JavaScript rendering."""
    try:
        # Create a temporary Playwright script
        script_content = f'''
import asyncio
from playwright.async_api import async_playwright

async def fetch_url():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("{url}", wait_until="networkidle")
        content = await page.content()
        await browser.close()
        return content

result = asyncio.run(fetch_url())
print(result)
'''
        
        # Write script to temp file
        temp_script = "/tmp/fetch_playwright.py"
        with open(temp_script, "w") as f:
            f.write(script_content)
        
        # Run the script
        result = subprocess.run([
            "python3", temp_script
        ], capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            return result.stdout
        else:
            print(f"  Playwright error: {result.stderr}")
            return ""
            
    except Exception as e:
        print(f"  Playwright fetch error: {e}")
        return ""

# Key TradingView documentation pages to fetch
TV_PAGES = [
    ("ta_functions", "https://www.tradingview.com/pine-script-reference/v6/#fun_ta"),
    ("strategy_functions", "https://www.tradingview.com/pine-script-reference/v6/#fun_strategy"),
    ("variables", "https://www.tradingview.com/pine-script-reference/v6/#var_variables"),
    ("constants", "https://www.tradingview.com/pine-script-reference/v6/#const_constants"),
    ("types", "https://www.tradingview.com/pine-script-reference/v6/#type_types"),
    ("keywords", "https://www.tradingview.com/pine-script-reference/v6/#kw_keywords"),
    ("operators", "https://www.tradingview.com/pine-script-reference/v6/#op_operators"),
    ("annotations", "https://www.tradingview.com/pine-script-reference/v6/#annotation_annotations"),
]

print("\n" + "="*60)
print("FETCHING TRADINGVIEW DOCUMENTATION WITH PLAYWRIGHT")
print("="*60)

fetched_content = {}
for page_name, url in TV_PAGES:
    print(f"  🌐 Fetching {page_name}: {url}")
    content = fetch_with_playwright(url)
    if content and len(content) > 1000:
        fetched_content[page_name] = content
        print(f"     ✅ Got {len(content)//1024}KB")
    else:
        print(f"     ⚠️  Empty or failed")

# Extract documentation from fetched content
def extract_docs_from_html(html: str, target_names: List[str]) -> Dict[str, str]:
    """Extract documentation for specific names from HTML."""
    docs = {}
    
    # Convert to lowercase for case-insensitive matching
    html_lower = html.lower()
    
    for name in target_names:
        # Try different patterns to find the documentation
        patterns = [
            rf'{name}.*?<div[^>]*>(.*?)</div>',
            rf'{name}.*?<p[^>]*>(.*?)</p>',
            rf'{name}.*?<pre[^>]*>(.*?)</pre>',
            rf'{name}.*?<code[^>]*>(.*?)</code>',
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html_lower, re.DOTALL | re.IGNORECASE)
            if matches:
                # Clean up the match
                doc = re.sub(r'<[^>]+>', ' ', matches[0])
                doc = re.sub(r'\s+', ' ', doc).strip()
                if len(doc) > 20:
                    docs[name] = doc
                    break
        
        # If still not found, try a broader search
        if name not in docs:
            pos = html_lower.find(name)
            if pos > 0:
                # Extract text around the name
                start = max(0, pos - 100)
                end = min(len(html), pos + 500)
                snippet = html[start:end]
                doc = re.sub(r'<[^>]+>', ' ', snippet)
                doc = re.sub(r'\s+', ' ', doc).strip()
                if len(doc) > 20:
                    docs[name] = doc
    
    return docs

# Process missing entries
new_ids, new_docs, new_metas = [], [], []

if missing_entries:
    print(f"\nProcessing {len(missing_entries)} missing entries...")
    
    # Group by namespace for efficient processing
    by_namespace = {}
    for gt in missing_entries:
        by_namespace.setdefault(gt.namespace, []).append(gt)
    
    for namespace, entries in by_namespace.items():
        print(f"\n  Processing {namespace}: {len(entries)} entries")
        
        # Try to get relevant HTML content
        html_content = ""
        if namespace == "ta" and "ta_functions" in fetched_content:
            html_content = fetched_content["ta_functions"]
        elif namespace == "strategy" and "strategy_functions" in fetched_content:
            html_content = fetched_content["strategy_functions"]
        elif namespace in ["variables", "constants", "types", "keywords", "operators", "annotations"]:
            page_map = {
                "variables": "variables",
                "constants": "constants", 
                "types": "types",
                "keywords": "keywords",
                "operators": "operators",
                "annotations": "annotations"
            }
            page_name = page_map.get(namespace)
            if page_name in fetched_content:
                html_content = fetched_content[page_name]
        
        # Extract docs for this namespace
        if html_content:
            target_names = [gt.name for gt in entries]
            extracted_docs = extract_docs_from_html(html_content, target_names)
            print(f"    Extracted docs for {len(extracted_docs)} entries")
        
        # Create entries
        for gt in entries:
            doc = f"{gt.name} — {gt.entry_type}\n"
            doc += f"Namespace: {gt.namespace}\n"
            
            # Add signature if available
            if gt.signature:
                doc += f"Signature: {gt.signature}\n"
            
            # Add description if available
            if gt.description:
                doc += f"\n{gt.description}\n"
            
            # Add extracted documentation if found
            if html_content and gt.name in extracted_docs:
                doc += f"\nFrom TradingView docs:\n{extracted_docs[gt.name][:400]}"
            
            doc += f"\n\nSource: {gt.source_file}"
            
            # Create unique ID
            uid = f"playwright_{gt.namespace}_{gt.name.replace('.','_')}_{hashlib.md5(f'{gt.name}_{gt.source_file}'.encode()).hexdigest()[:6]}"
            
            new_ids.append(uid)
            new_docs.append(doc)
            new_metas.append({
                "name": gt.name,
                "namespace": gt.namespace,
                "type": gt.entry_type,
                "source": "playwright_audit_v1",
                "version": "v6",
                "file": gt.source_file,
            })

# Also add some concept pages that are missing
concept_pages = [
    ("execution_model", "https://www.tradingview.com/pine-script-docs/concepts/execution-model/"),
    ("strategies", "https://www.tradingview.com/pine-script-docs/concepts/strategies/"),
    ("type_system", "https://www.tradingview.com/pine-script-docs/language/type-system/"),
    ("debugging", "https://www.tradingview.com/pine-script-docs/writing/debugging/"),
]

print(f"\n" + "="*60)
print("FETCHING CONCEPT PAGES")
print("="*60)

for concept_name, url in concept_pages:
    print(f"  🌐 Fetching concept: {concept_name}")
    content = fetch_with_playwright(url)
    
    if content and len(content) > 1000:
        # Clean up the HTML content
        content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
        content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = re.sub(r'\s+', ' ', content).strip()
        
        if len(content) > 100:
            uid = f"concept_{concept_name}_{hashlib.md5(url.encode()).hexdigest()[:6]}"
            new_ids.append(uid)
            new_docs.append(f"{concept_name.replace('_', ' ').title()}\n\n{content[:2000]}")
            new_metas.append({
                "name": concept_name,
                "namespace": "concepts",
                "type": "guide",
                "source": "playwright_concepts",
                "version": "v6",
                "url": url,
            })
            print(f"     ✅ Added {len(content)} chars")
        else:
            print(f"     ⚠️  Content too short")
    else:
        print(f"     ⚠️  Failed to fetch")

# Upsert all new entries
if new_ids:
    print(f"\nUpserting {len(new_ids)} new entries...")
    
    BATCH = 100
    for i in range(0, len(new_ids), BATCH):
        col.upsert(
            ids=new_ids[i:i+BATCH],
            documents=new_docs[i:i+BATCH],
            metadatas=new_metas[i:i+BATCH],
        )
        print(f"  Upserted {min(i+BATCH, len(new_ids))}/{len(new_ids)}...")

count_after = col.count()
print(f"\nBefore: {count_before} → After: {count_after} (+{count_after-count_before})")

# Final report
total_ground_truth = len(all_ground_truth)
total_covered = len(all_ground_truth) - len(missing_entries) + len(new_ids)
coverage_pct = round(total_covered / total_ground_truth * 100, 1)

print(f"""
╔══════════════════════════════════════════════════════╗
║      PINESCRIPT v6 MCP — PLAYWRIGHT AUDIT REPORT     ║
╠══════════════════════════════════════════════════════╣
║  Ground truth entries (from repo):       {total_ground_truth:>5}  ║
║  Now covered in DB:                     {total_covered:>5}  ║
║  Coverage:                              {coverage_pct:>5}%  ║
║  DB total entries:                       {count_after:>5}  ║
║  New entries added:                      {len(new_ids):>5}  ║
╚══════════════════════════════════════════════════════╝
""")

# Save report
report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "db_total": count_after,
    "coverage_pct": coverage_pct,
    "ground_truth": total_ground_truth,
    "covered": total_covered,
    "new_entries": len(new_ids),
    "fetched_pages": list(fetched_content.keys()),
    "missing_before": len(missing_entries),
}

with open("playwright_audit_report.json", "w") as f:
    json.dump(report, f, indent=2)

print(f"\n📊 Report saved: playwright_audit_report.json")
print(f"🎉 Playwright audit complete! Database now has {count_after} entries")
