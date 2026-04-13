import chromadb
from chromadb.utils import embedding_functions

client = chromadb.PersistentClient(path="./pinescript_db")
col    = client.get_collection("pinescript_v6")

queries = [
    ("execution model", "How does Pine execute code on each bar"),
    ("type system",     "What are the Pine Script v6 types series simple const"),
    ("common errors",   "Cannot use a non-const value as argument"),
    ("drawing objects", "How to draw a line between two bars"),
    ("debugging",       "How to use log.info to debug a script"),
    ("annotations",     "What does @param annotation do in Pine"),
]

for label, q in queries:
    r = col.query(query_texts=[q], n_results=1)
    doc   = r["documents"][0][0][:120] if r["documents"][0] else "NO RESULT"
    meta  = r["metadatas"][0][0]       if r["metadatas"][0]  else {}
    src   = meta.get("source", "?")
    ns    = meta.get("namespace", "?")
    print(f"\n[{label}]")
    print(f"  Source: {src} | Namespace: {ns}")
    print(f"  Preview: {doc}")

print(f"\nFinal DB total: {col.count()}")
