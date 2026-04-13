#!/usr/bin/env python3
"""
Check which collections exist and consolidate profiler entries
"""

import chromadb

def check_collections():
    client = chromadb.PersistentClient(path="./pinescript_db")
    
    # List all collections
    collections = client.list_collections()
    print(f"Available collections:")
    for col in collections:
        print(f"  - {col.name} (count: {col.count()})")
    
    # Check if profiler entries exist in pinescript_docs
    try:
        docs_col = client.get_collection("pinescript_docs")
        profiler_docs = docs_col.get(where={"namespace": "profiler"}, include=["metadatas"])
        print(f"\nProfiler entries in pinescript_docs: {len(profiler_docs['ids'])}")
        
        # Move profiler entries from pinescript_docs to pinescript_v6 if needed
        if len(profiler_docs['ids']) > 0:
            print("Moving profiler entries to pinescript_v6...")
            v6_col = client.get_collection("pinescript_v6")
            
            # Get all profiler entries with documents
            profiler_with_docs = docs_col.get(where={"namespace": "profiler"}, include=["metadatas", "documents"])
            
            # Upsert to pinescript_v6
            v6_col.upsert(
                ids=profiler_with_docs['ids'],
                documents=profiler_with_docs['documents'],
                metadatas=profiler_with_docs['metadatas']
            )
            
            print(f"✅ Moved {len(profiler_with_docs['ids'])} profiler entries to pinescript_v6")
            
    except Exception as e:
        print(f"No pinescript_docs collection: {e}")
    
    # Final verification
    try:
        v6_col = client.get_collection("pinescript_v6")
        final_check = v6_col.get(where={"namespace": "profiler"}, include=["metadatas"])
        print(f"\nFinal profiler entries in pinescript_v6: {len(final_check['ids'])}")
        
        print("\nProfiler entry names:")
        for metadata in final_check['metadatas']:
            name = metadata.get('name', 'unnamed')
            print(f"  - {name}")
            
    except Exception as e:
        print(f"Error checking pinescript_v6: {e}")

if __name__ == "__main__":
    check_collections()
