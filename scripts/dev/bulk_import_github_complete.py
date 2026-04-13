import chromadb, re, hashlib
from chromadb.utils import embedding_functions
from pathlib import Path

client = chromadb.PersistentClient(path="./pinescript_db")
col    = client.get_collection("pinescript_v6")

count_before = col.count()
if count_before < 1000:
    raise RuntimeError(f"SAFETY ABORT: only {count_before} entries — wrong collection or path")
print(f"✅ Safety guard passed: {count_before} entries before import")

SKIP_FILES = {
    "pinescriptv6_complete_reference.md",
    "Pine Script language reference manual.md",
    "pine_script_execution_model.md",
    "README.md",
    "LLM_MANIFEST.md",
    "release_notes.md",
}

DIR_NAMESPACE = {
    "reference": "reference", "concepts": "concepts",
    "visuals": "visuals", "writing scripts": "writing",
    "writing_scripts": "writing", ".": "root",
}

FILE_CATEGORY = {
    "constants.md": "constant", "variables.md": "variable",
    "keywords.md": "keyword", "types.md": "type",
    "operators.md": "operator", "annotations.md": "annotation",
    "execution_model.md": "guide", "common_errors.md": "guide",
    "colors_and_display.md": "guide", "debugging.md": "guide",
}

def chunk_markdown(text, source_file):
    pattern = re.compile(r'^(#{2,3})\s+(.+)$', re.MULTILINE)
    matches = list(pattern.finditer(text))
    if not matches:
        return [{"heading": Path(source_file).stem, "body": text.strip()}]
    chunks = []
    for i, match in enumerate(matches):
        heading = match.group(2).strip()
        start   = match.end()
        end     = matches[i+1].start() if i+1 < len(matches) else len(text)
        body    = text[start:end].strip()
        if len(body) < 30:
            continue
        chunks.append({"heading": heading, "body": f"{heading}\n\n{body}"})
    return chunks

def extract_name(heading):
    m = re.search(r'\b([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+)\s*[(\[]?', heading.lower())
    if m: return m.group(1)
    m2 = re.match(r'^([a-z_][a-z0-9_]{2,})\b', heading.lower())
    if m2: return m2.group(1)
    return heading.lower()[:40].replace(" ", "_")

repo_root   = Path("./pinescriptv6")
all_md      = list(repo_root.rglob("*.md"))
to_process  = [f for f in all_md if f.name not in SKIP_FILES]

print(f"\nFiles to import: {len(to_process)}")
for f in sorted(to_process):
    print(f"  {f.relative_to(repo_root)}")

all_ids, all_docs, all_metas = [], [], []
stats = {}

for md_file in sorted(to_process):
    rel_path  = md_file.relative_to(repo_root)
    dir_name  = rel_path.parent.name if rel_path.parent.name else "."
    namespace = DIR_NAMESPACE.get(dir_name, dir_name)
    category  = FILE_CATEGORY.get(md_file.name, "guide")
    file_slug = md_file.stem.lower().replace(" ", "_")
    text      = md_file.read_text(encoding="utf-8", errors="ignore")

    if len(text) > 256_000:
        print(f"  ⏭️  SKIPPED (too large): {rel_path} ({len(text)//1024}KB)")
        continue

    chunks     = chunk_markdown(text, str(md_file))
    file_count = 0

    for i, chunk in enumerate(chunks):
        name    = extract_name(chunk["heading"])
        c_hash  = hashlib.md5(chunk["body"].encode()).hexdigest()[:8]
        cid     = f"gh_{file_slug}_{i:04d}_{c_hash}"

        all_ids.append(cid)
        all_docs.append(chunk["body"])
        all_metas.append({
            "name":      name,
            "namespace": namespace,
            "type":      category,
            "source":    "github_codenamedevan",
            "version":   "v6",
            "file":      str(rel_path),
            "heading":   chunk["heading"][:80],
        })
        file_count += 1

    stats[str(rel_path)] = file_count
    print(f"  ✅ {rel_path}: {file_count} chunks")

print(f"\nTotal chunks to upsert: {len(all_ids)}")

BATCH = 200
for i in range(0, len(all_ids), BATCH):
    col.upsert(
        ids=       all_ids  [i:i+BATCH],
        documents= all_docs [i:i+BATCH],
        metadatas= all_metas[i:i+BATCH],
    )
    print(f"  Upserted {min(i+BATCH, len(all_ids))}/{len(all_ids)}...")

count_after = col.count()
print(f"\n{'='*50}")
print(f"IMPORT COMPLETE")
print(f"Before: {count_before}  →  After: {count_after}  (+{count_after - count_before} new)")
print(f"\nChunks per file:")
for path, n in sorted(stats.items()):
    print(f"  {path}: {n}")
