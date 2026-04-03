#!/usr/bin/env python3
"""
Fix plot functions by updating category from constant to function
"""

import chromadb

# Initialize ChromaDB
client = chromadb.PersistentClient(path="./pinescript_db")
col = client.get_collection("pinescript_v6")

# Find all entries named plot functions that have category=constant
PLOT_FUNCTIONS = [
    "plot", "plotshape", "plotchar", "plotarrow",
    "plotcandle", "plotbar", "plotbgcolor", "hline",
    "fill", "bgcolor", "barcolor"
]

fixed_count = 0
for fn_name in PLOT_FUNCTIONS:
    r = col.get(where={"name": fn_name}, include=["metadatas", "documents"])
    for i, (rid, meta, doc) in enumerate(
        zip(r["ids"], r["metadatas"], r["documents"])
    ):
        if meta.get("category") == "constant":
            # Confirm it's actually a function by checking document
            if ("(" in doc and ("→" in doc or "returns" in doc.lower()
                           or "overlay" in doc.lower()
                           or "series" in doc.lower())):
                meta["category"] = "function"
                meta["type"] = "function"
                col.upsert(ids=[rid], documents=[doc], metadatas=[meta])
                print(f"Fixed: {fn_name} → category=function, type=function")
                fixed_count += 1

print(f"\n✅ Fixed {fixed_count} plot function categories")
print(f"New DB total: {col.count()}")
