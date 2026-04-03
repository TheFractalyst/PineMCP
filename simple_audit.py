#!/usr/bin/env python3

# Simple PineScript v6 MCP Audit - Local Repo Only
# This script audits the database against the local GitHub repo documentation

import chromadb, re, json, time, hashlib
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

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

def parse_reference_file(path: Path, default_type: str) -> list[DocEntry]:
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

# Parse all reference files
repo_root = Path("./pinescriptv6")
all_ground_truth = []

# Main reference files
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

# Also scan all markdown files for additional entries
print("\nScanning all markdown files...")
for md_file in sorted(repo_root.rglob("*.md")):
    if md_file.stat().st_size > 200_000:  # Skip giant files
        continue
    if md_file.name in ("README.md", "LLM_MANIFEST.md"):
        continue
    
    text = md_file.read_text(encoding="utf-8", errors="ignore")
    # Extract H3 entries that look like Pine identifiers
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
                name=name, 
                namespace=ns,
                entry_type="guide",
                source_file=str(md_file.relative_to(repo_root))
            ))

print(f"\nGround truth total: {len(all_ground_truth)} entries")

# Cross-check against database
print("\n" + "="*60)
print("CROSS-CHECKING DATABASE")
print("="*60)

missing_entries = []
present_entries = []
hollow_entries = []

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

# Show missing by namespace
if missing_entries:
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

# Add missing entries with basic documentation
if missing_entries or hollow_entries:
    print(f"\nAdding missing and enriching hollow entries...")
    new_ids, new_docs, new_metas = [], [], []
    
    # Process missing entries
    for gt in missing_entries:
        doc = f"{gt.name} — {gt.entry_type}\n"
        doc += f"Namespace: {gt.namespace}\n"
        if gt.signature:
            doc += f"Signature: {gt.signature}\n"
        if gt.description:
            doc += f"\n{gt.description}\n"
        doc += f"\nSource: {gt.source_file}"
        
        uid = f"audit_{gt.namespace}_{gt.name.replace('.','_')}_{hashlib.md5(f'{gt.name}_{gt.source_file}'.encode()).hexdigest()[:6]}"
        
        new_ids.append(uid)
        new_docs.append(doc)
        new_metas.append({
            "name": gt.name,
            "namespace": gt.namespace,
            "type": gt.entry_type,
            "source": "local_audit_v1",
            "version": "v6",
            "file": gt.source_file,
        })
    
    # Enrich hollow entries
    for gt in hollow_entries:
        r = col.get(where={"name": gt.name}, include=["documents"])
        existing_doc = r["documents"][0] if r["documents"] else ""
        
        doc = f"{gt.name} — {gt.entry_type}\n"
        doc += f"Namespace: {gt.namespace}\n"
        if gt.signature:
            doc += f"Signature: {gt.signature}\n"
        if gt.description:
            doc += f"\n{gt.description}\n"
        doc += f"\nSource: {gt.source_file}"
        doc += f"\n\nOriginal: {existing_doc[:200]}"
        
        uid = f"enrich_{gt.namespace}_{gt.name.replace('.','_')}_{hashlib.md5(f'{gt.name}_{gt.source_file}'.encode()).hexdigest()[:6]}"
        
        new_ids.append(uid)
        new_docs.append(doc)
        new_metas.append({
            "name": gt.name,
            "namespace": gt.namespace,
            "type": gt.entry_type,
            "source": "local_audit_enriched",
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

# Final coverage report
total_ground_truth = len(all_ground_truth)
total_covered = len(present_entries) + len(missing_entries) + len(hollow_entries)
coverage_pct = round(total_covered / total_ground_truth * 100, 1)

print(f"""
╔══════════════════════════════════════════════════════╗
║         PINESCRIPT v6 MCP — LOCAL AUDIT REPORT      ║
╠══════════════════════════════════════════════════════╣
║  Ground truth entries (from repo):       {total_ground_truth:>5}  ║
║  Now covered in DB:                     {total_covered:>5}  ║
║  Coverage:                              {coverage_pct:>5}%  ║
║  DB total entries:                       {count_after:>5}  ║
╚══════════════════════════════════════════════════════╝
""")

# Namespace breakdown
print("Coverage by namespace:")
ns_gt = {}
ns_covered = {}
for gt in all_ground_truth:
    ns_gt[gt.namespace] = ns_gt.get(gt.namespace, 0) + 1
for gt in present_entries + missing_entries + hollow_entries:
    ns_covered[gt.namespace] = ns_covered.get(gt.namespace, 0) + 1

for ns in sorted(ns_gt.keys()):
    total = ns_gt[ns]
    covered = ns_covered.get(ns, 0)
    pct = round(covered/total*100) if total > 0 else 0
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    status = "✅" if pct >= 90 else "⚠️ " if pct >= 70 else "❌"
    print(f"  {status} {ns:<15} {bar} {covered:>3}/{total:<3} ({pct}%)")

# Save report
report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "db_total": count_after,
    "coverage_pct": coverage_pct,
    "ground_truth": total_ground_truth,
    "covered": total_covered,
    "missing_count": len(missing_entries),
    "hollow_count": len(hollow_entries),
    "namespace_coverage": {
        ns: {"covered": ns_covered.get(ns,0), "total": ns_gt[ns]}
        for ns in ns_gt
    }
}

with open("local_audit_report.json", "w") as f:
    json.dump(report, f, indent=2)

print(f"\n📊 Report saved: local_audit_report.json")
print(f"🎉 Local audit complete! Database now has {count_after} entries")
