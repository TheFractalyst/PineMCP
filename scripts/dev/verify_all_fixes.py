#!/usr/bin/env python3
"""
Verification script for all 7 MCP fixes
"""

import chromadb
from chromadb.utils import embedding_functions

# Initialize ChromaDB
client = chromadb.PersistentClient(path="./pinescript_db")
col = client.get_collection("pinescript_v6")

passed = 0

print("🔍 VERIFYING ALL 7 FIXES")
print("=" * 50)

# V1 — check_freshness works
r = col.get(where={"namespace": "barstate"}, include=["metadatas"])
v1 = len(r["ids"]) >= 5
passed += v1
print(f"{'✅' if v1 else '❌'} V1 barstate.* entries: {len(r['ids'])} (need ≥5)")

# V2 — request.footprint exists and has content
r2 = col.get(where={"name": "request.footprint"}, include=["documents"])
v2 = bool(r2["ids"]) and len(r2["documents"][0]) > 100
passed += v2
print(f"{'✅' if v2 else '❌'} V2 request.footprint doc length: {len(r2['documents'][0]) if r2['ids'] else 0}")

# V3 — extend enum exists
r3 = col.get(where={"name": "extend.right"}, include=["documents"])
v3 = bool(r3["ids"])
passed += v3
print(f"{'✅' if v3 else '❌'} V3 extend.right exists")

# V4 — line.style_solid exists
r4 = col.get(where={"name": "line.style_solid"}, include=["documents"])
v4 = bool(r4["ids"])
passed += v4
print(f"{'✅' if v4 else '❌'} V4 line.style_solid exists")

# V5 — footprint semantic search
r5 = col.query(query_texts=["orderflow buy sell volume delta footprint"], n_results=3)
v5 = any("footprint" in (d or "").lower() for d in r5["documents"][0])
passed += v5
print(f"{'✅' if v5 else '❌'} V5 semantic 'footprint delta' finds footprint docs")

# V6 — barstate.isconfirmed semantic search
r6 = col.query(query_texts=["avoid repainting strategy entry confirmed bar"], n_results=3)
v6 = any("isconfirmed" in (d or "").lower() for d in r6["documents"][0])
passed += v6
print(f"{'✅' if v6 else '❌'} V6 semantic 'confirmed bar' finds barstate.isconfirmed")

# V7 — plot() type is now function
r7 = col.get(where={"name": "plot"}, include=["metadatas"])
v7 = any(m.get("type") == "function" for m in r7["metadatas"])
passed += v7
print(f"{'✅' if v7 else '❌'} V7 plot() type=function (not constant)")

print(f"\n{'='*50}")
print(f"Final score: {passed}/7")
print(f"New DB total: {col.count()}")
print(f"{'🎉 ALL FIXES VERIFIED' if passed == 7 else '⚠️  CHECK FAILURES'}")

# Additional tests
print(f"\n{'='*50}")
print("ADDITIONAL VERIFICATION:")

# Test deduplication
print("\n📝 Testing deduplication...")
r_dup = col.query(query_texts=["ta.ema example"], n_results=5)
print(f"  ta.ema results: {len(r_dup['documents'][0])} (should be deduped)")

# Test drawing enums
drawing_enums = ["extend.both", "size.huge", "label.style_arrowup", "line.style_dotted"]
for enum_name in drawing_enums:
    r_enum = col.get(where={"name": enum_name}, include=["documents"])
    exists = bool(r_enum["ids"])
    print(f"  {enum_name}: {'✅' if exists else '❌'}")

print(f"\n✅ Verification complete!")
