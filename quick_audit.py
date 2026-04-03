#!/usr/bin/env python3

# Quick PineScript v6 MCP Audit - Add Missing Entries
# Simple script to add missing entries with basic documentation

import chromadb, re, json, time, hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

# Connect to database
client = chromadb.PersistentClient(path="./pinescript_db")
try:
    col = client.get_collection("pinescript_v6")
    print("Using existing collection")
except:
    print("No collection found")
    exit(1)

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
            parts = heading.split(".")
            ns = parts[0].lower()
            name = ".".join(parts[1:]).lower()
        else:
            ns = path.stem.lower()
            name = heading.lower()
        
        # Clean name
        name = re.sub(r'[`*_\[\]()]', '', name).strip()
        name = re.sub(r'\(.*\)$', '', name).strip()
        
        # Skip "code example" and empty entries
        if "code example" in name.lower() or not name or name.strip() == "":
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

# Parse local reference files
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

# Find missing entries
print("\nFinding missing entries...")
missing_entries = []
existing_names = set()

# Get all existing names
all_existing = col.get(include=["metadatas"])
if all_existing["metadatas"]:
    existing_names = set(meta.get("name", "") for meta in all_existing["metadatas"])

for gt in all_ground_truth:
    if gt.name not in existing_names:
        missing_entries.append(gt)

print(f"Missing entries: {len(missing_entries)}")

# Add missing entries with basic documentation
if missing_entries:
    print(f"\nAdding {len(missing_entries)} missing entries...")
    
    new_ids, new_docs, new_metas = [], [], []
    
    for gt in missing_entries:
        # Create basic documentation
        doc = f"{gt.name} — {gt.entry_type}\n"
        doc += f"Namespace: {gt.namespace}\n"
        
        if gt.signature:
            doc += f"Signature: {gt.signature}\n"
        
        if gt.description:
            doc += f"\n{gt.description}\n"
        
        doc += f"\nSource: {gt.source_file}"
        doc += f"\n\nOfficial reference: https://www.tradingview.com/pine-script-reference/v6/"
        
        # Create unique ID
        uid = f"quick_{gt.namespace}_{gt.name.replace('.','_')}_{hashlib.md5(f'{gt.name}_{gt.source_file}'.encode()).hexdigest()[:6]}"
        
        new_ids.append(uid)
        new_docs.append(doc)
        new_metas.append({
            "name": gt.name,
            "namespace": gt.namespace,
            "type": gt.entry_type,
            "source": "quick_audit_v1",
            "version": "v6",
            "file": gt.source_file,
        })
    
    # Upsert in batches
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

# Add some common concept pages manually
concept_docs = [
    ("execution_model", "concepts", """
    Pine Script Execution Model
    
    Pine Script executes on a bar-by-bar basis. Each bar represents a time period
    (like 1 day, 1 hour, 1 minute, etc.). The script processes historical data
    from the oldest to the newest bar, then processes real-time bars as they form.
    
    Key concepts:
    - Bar state: confirmed vs unconfirmed bars
    - Realtime execution: how scripts update on live data
    - Historical vs realtime behavior differences
    """),
    
    ("strategies", "concepts", """
    Pine Script Strategies
    
    Strategies are Pine Script programs that can place and manage trades.
    They include backtesting capabilities and can be deployed for automated trading.
    
    Key components:
    - strategy() function declaration
    - strategy.entry() for opening positions
    - strategy.exit() for closing positions
    - Risk management: stop loss, take profit
    - Performance metrics and reporting
    """),
    
    ("type_system", "language", """
    Pine Script Type System
    
    Pine Script v6 includes a comprehensive type system with:
    - Basic types: int, float, bool, string, color
    - Series types: series<int>, series<float>, etc.
    - Input types: input.int, input.float, etc.
    - User-defined types (UDTs)
    - Type casting and conversion
    - Type annotations and inference
    """),
    
    ("arrays", "language", """
    Pine Script Arrays
    
    Arrays are collections of elements of the same type.
    Key operations:
    - array.new<int>() - create new array
    - array.push() - add element
    - array.pop() - remove element
    - array.get() - access element
    - array.size() - get length
    - array.sort() - sort elements
    """),
    
    ("debugging", "writing", """
    Pine Script Debugging
    
    Debugging techniques for Pine Script:
    - plot() for visual debugging
    - label.new() for marking points
    - runtime.error() for intentional errors
    - log.info() for console output (in Pine Editor)
    - Using barstate.isrealtime for conditional debugging
    """),
]

print(f"\nAdding concept documentation...")
concept_ids, concept_docs_list, concept_metas = [], [], []

for concept_name, namespace, content in concept_docs:
    uid = f"concept_{concept_name}_{hashlib.md5(concept_name.encode()).hexdigest()[:6]}"
    
    concept_ids.append(uid)
    concept_docs_list.append(content.strip())
    concept_metas.append({
        "name": concept_name,
        "namespace": namespace,
        "type": "guide",
        "source": "quick_concepts",
        "version": "v6",
    })

# Upsert concepts
if concept_ids:
    col.upsert(
        ids=concept_ids,
        documents=concept_docs_list,
        metadatas=concept_metas,
    )
    print(f"  Added {len(concept_ids)} concept entries")

# Final count
count_final = col.count()
print(f"\nFinal database: {count_final} entries")

# Coverage report
total_ground_truth = len(all_ground_truth)
total_covered = len(all_ground_truth) - len(missing_entries)
coverage_pct = round(total_covered / total_ground_truth * 100, 1)

print(f"""
╔══════════════════════════════════════════════════════╗
║        PINESCRIPT v6 MCP — QUICK AUDIT REPORT       ║
╠══════════════════════════════════════════════════════╣
║  Ground truth entries (from repo):       {total_ground_truth:>5}  ║
║  Now covered in DB:                     {total_covered:>5}  ║
║  Coverage:                              {coverage_pct:>5}%  ║
║  DB total entries:                       {count_final:>5}  ║
║  New entries added:                      {count_final-count_before:>5}  ║
╚══════════════════════════════════════════════════════╝
""")

# Save report
report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "db_total": count_final,
    "coverage_pct": coverage_pct,
    "ground_truth": total_ground_truth,
    "covered": total_covered,
    "new_entries": count_final - count_before,
    "missing_before": len(missing_entries),
}

with open("quick_audit_report.json", "w") as f:
    json.dump(report, f, indent=2)

print(f"\n📊 Report saved: quick_audit_report.json")
print(f"🎉 Quick audit complete! Database now has {count_final} entries")
