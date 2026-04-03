#!/usr/bin/env python3
"""
Check existing profiler entries and fix verification
"""

import chromadb

def check_profiler_entries():
    client = chromadb.PersistentClient(path="./pinescript_db")
    col = client.get_collection("pinescript_v6")
    
    # Check all profiler entries
    r = col.get(where={"namespace": "profiler"}, include=["metadatas", "documents"])
    
    print(f"Total profiler entries: {len(r['ids'])}")
    print("\nProfiler entry names:")
    for i, (id, metadata) in enumerate(zip(r['ids'], r['metadatas'])):
        name = metadata.get('name', 'unnamed')
        print(f"  {i+1}. {name} (id: {id})")
    
    # Check if we have the key entries from the previous ingest
    key_entries = ["pine_profiler", "profiler.interpret_single_line", "profiler.interpret_code_block",
                   "profiler.user_defined_functions", "profiler.requesting_contexts",
                   "profiler.unused_redundant_code", "pine_profiler.examples", "pine_optimization.techniques"]
    
    print(f"\nChecking for key entries from previous ingest:")
    for name in key_entries:
        result = col.get(where={"name": name}, include=["metadatas"])
        exists = "EXISTS" if result["ids"] else "MISSING"
        print(f"  {exists}: {name}")
    
    return len(r['ids'])

if __name__ == "__main__":
    count = check_profiler_entries()
    print(f"\nTotal profiler entries in DB: {count}")
