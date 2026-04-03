import chromadb
from chromadb.utils import embedding_functions

client = chromadb.PersistentClient(path="./pinescript_db")
col    = client.get_collection("pinescript_v6")

tests = [
    ("execution model",  "how does Pine Script execute code on each bar replay order"),
    ("type system",      "series simple const input Pine Script v6 type system"),
    ("common errors",    "cannot use non-const value as argument error fix"),
    ("drawing objects",  "how to draw a horizontal line at a price level"),
    ("debugging",        "log.info log.error how to debug Pine Script"),
    ("annotations",      "what does @param @returns annotation do in Pine"),
    ("array functions",  "add remove elements from array pine script"),
    ("strategy concepts","strategy entry exit commission slippage backtest"),
]

passed = 0
for label, q in tests:
    r    = col.query(query_texts=[q], n_results=1)
    doc  = r["documents"][0][0][:100] if r["documents"][0] else "NO RESULT"
    meta = r["metadatas"][0][0]       if r["metadatas"][0]  else {}
    ok   = doc != "NO RESULT"
    passed += ok
    print(f"{'✅' if ok else '❌'} [{label}]")
    print(f"   source={meta.get('source','?')} ns={meta.get('namespace','?')}")
    print(f"   {doc}\n")

print(f"Score: {passed}/8")
print(f"Final DB total: {col.count()}")
print(f"{'🎉 MCP FULLY LOADED' if passed >= 7 else '⚠️  CHECK FAILURES ABOVE'}")
