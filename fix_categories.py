#!/usr/bin/env python3
"""
Fix category misclassifications in PineScript MCP database.

This script fixes:
1. footprint.* entries misclassified as "type" instead of "function"
2. volume_row.* entries misclassified as "type" instead of "function"
"""

import chromadb
import sys

def fix_categories():
    """Fix misclassified entries in the database."""
    try:
        client = chromadb.PersistentClient(path='pinescript_db')
        collection = client.get_collection(name='pinescript_v6')
        
        fixes_made = 0
        
        # Fix footprint entries (excluding volume_row type itself)
        print("=== FIXING FOOTPRINT ENTRIES ===")
        result = collection.get(where={'namespace': 'footprint'}, include=['metadatas', 'documents'])
        
        for i, meta in enumerate(result['metadatas']):
            name = meta.get('name', '')
            category = meta.get('category', '')
            
            # Skip the actual volume_row type definition
            if name == 'volume_row':
                print(f"  SKIP: {name} (actual type definition)")
                continue
                
            # Fix functions misclassified as types
            if category == 'type' or category == '?':
                print(f"  FIX: {name} -> category: function (was: {category})")
                collection.update(
                    ids=[result['ids'][i]],
                    metadatas=[{**meta, 'category': 'function'}]
                )
                fixes_made += 1
            else:
                print(f"  OK: {name} -> category: {category}")
        
        print()
        print("=== FIXING VOLUME_ROW ENTRIES ===")
        # Fix volume_row entries
        result = collection.get(where={'namespace': 'volume_row'}, include=['metadatas', 'documents'])
        
        for i, meta in enumerate(result['metadatas']):
            name = meta.get('name', '')
            category = meta.get('category', '')
            
            # Fix functions misclassified as types
            if category == 'type':
                print(f"  FIX: {name} -> category: function (was: {category})")
                collection.update(
                    ids=[result['ids'][i]],
                    metadatas=[{**meta, 'category': 'function'}]
                )
                fixes_made += 1
            else:
                print(f"  OK: {name} -> category: {category}")
        
        print()
        print(f"=== SUMMARY ===")
        print(f"Total fixes made: {fixes_made}")
        print("Category misclassifications fixed successfully!")
        
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    fix_categories()
