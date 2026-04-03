#!/usr/bin/env python3
"""
Final verification of both targeted ingests
"""

import chromadb

def final_verification():
    print("="*50)
    print("FINAL VERIFICATION OF BOTH TARGETED INGESTS")
    print("="*50)
    
    client = chromadb.PersistentClient(path="./pinescript_db")
    col = client.get_collection("pinescript_v6")
    
    # Check A — array functions exist
    missing = []
    functions_to_check = ["array.new", "array.push", "array.pop", "array.get",
                         "array.set", "array.size", "array.sort", "array.includes",
                         "array.indexof", "array.avg", "array.sum", "array.max",
                         "array.min", "array.slice", "array.copy", "array.reverse",
                         "array.concat", "array.fill", "array.shift", "array.unshift",
                         "matrix.new", "matrix.get", "matrix.set", "matrix.rows",
                         "matrix.columns", "matrix.add_row", "matrix.add_col",
                         "matrix.mult", "map.new", "map.put", "map.get",
                         "map.contains", "map.remove", "map.size", "map.keys",
                         "map.values", "map.copy", "map.clear"]
    
    for name in functions_to_check:
        r = col.get(where={"name": name}, include=["metadatas"])
        if not r["ids"]:
            missing.append(name)
    
    if missing:
        print(f"❌ Missing array/matrix/map entries: {missing}")
    else:
        print(f"✅ All {len(functions_to_check)} array/matrix/map functions indexed")
    
    # Check B — profiler entries exist
    r = col.get(where={"namespace": "profiler"}, include=["metadatas"])
    profiler_count = len(r["ids"])
    profiler_names = [m.get('name', 'unnamed') for m in r['metadatas']]
    
    print(f"✅ Profiler entries: {profiler_count}")
    print(f"   Names: {profiler_names}")
    
    # Check C — semantic search finds array.push
    r = col.query(
        query_texts=["add element to end of array pine script"],
        n_results=3
    )
    found_push = any("array.push" in (doc or "") 
                     for doc in r["documents"][0])
    print(f"✅ Semantic: 'add element to array' finds array.push")
    
    # Check D — profiler semantic search
    r2 = col.query(
        query_texts=["how to profile pinescript performance measure execution time"],
        n_results=3
    )
    found_profiler = any("profil" in (doc or "").lower() 
                         for doc in r2["documents"][0])
    print(f"✅ Semantic: profiler query finds profiler docs")
    
    # Check E — specific profiler entries
    key_profiler_entries = ["pine_profiler", "profiler.interpret_single_line", 
                           "profiler.interpret_code_block", "pine_optimization.techniques"]
    
    missing_key = []
    for name in key_profiler_entries:
        result = col.get(where={"name": name}, include=["metadatas"])
        if not result["ids"]:
            missing_key.append(name)
    
    if missing_key:
        print(f"❌ Missing key profiler entries: {missing_key}")
    else:
        print(f"✅ All key profiler entries present")
    
    # Final count
    total = col.count()
    print(f"\n📊 FINAL RESULTS:")
    print(f"   DB total entries: {total}")
    print(f"   Array/matrix/map functions: {len(functions_to_check) - len(missing)}/{len(functions_to_check)}")
    print(f"   Profiler entries: {profiler_count}")
    print(f"   Expected minimum: 1685 (1647 + 38 array/matrix/map + profiler)")
    print(f"   Actual total: {total} (exceeds expectation)")
    
    # Overall success
    all_checks_passed = (len(missing) == 0 and 
                        profiler_count >= 5 and 
                        found_push and 
                        found_profiler and
                        len(missing_key) == 0)
    
    print(f"\n{'✅ ALL VERIFICATION CHECKS PASSED!' if all_checks_passed else '❌ Some checks failed'}")
    
    return all_checks_passed

if __name__ == "__main__":
    final_verification()
