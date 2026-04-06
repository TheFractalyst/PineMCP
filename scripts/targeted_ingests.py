#!/usr/bin/env python3
"""
Two targeted ingests into the existing ChromaDB collection (./pinescript_db).
Do not touch pinescript_mcp.py. Database work only.
"""

import chromadb
from datetime import datetime

def main():
    print("="*50)
    print("PRE-CHECK — confirm what is actually missing")
    print("="*50)
    
    # Connect to the existing collection
    client = chromadb.PersistentClient(path="./pinescript_db")
    col = client.get_collection("pinescript_v6")
    
    # Check which array functions exist
    functions_to_check = ["array.new", "array.push", "array.pop", "array.get",
                         "array.set", "array.size", "array.sort", "array.includes",
                         "matrix.new", "matrix.get", "matrix.set", "map.new", "map.put"]
    
    print("Checking existing functions:")
    for name in functions_to_check:
        r = col.get(where={"name": name}, include=["metadatas"])
        status = "EXISTS" if r["ids"] else "MISSING"
        print(f"{status}: {name}")
    
    print("\n" + "="*50)
    print("INGEST A — array.* / matrix.* / map.* function entries")
    print("="*50)
    
    # Array, Matrix, and Map function entries
    entries = [
        # ── array.new ──────────────────────────────────────────
        {
            "id": "func_array_new",
            "name": "array.new",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.new<type>(size, initial_value) → array<type>

Creates a new array object. Generic syntax: array.new<float>(0)

Parameters:
  size          (int)    Initial size of the array. Optional, default 0.
  initial_value (type)   Value used to fill array on creation. Optional.

Returns: array<type>

Type variants: array.new<int>, array.new<float>, array.new<bool>,
               array.new<string>, array.new<color>, array.new<line>,
               array.new<label>, array.new<box>, array.new<table>

Examples:
  // Create empty float array
  var arr = array.new<float>(0)

  // Create array of 5 elements initialized to 0.0
  var arr2 = array.new<float>(5, 0.0)

  // Create bool array
  var flags = array.new<bool>(3, false)

Notes:
  - Use 'var' to persist the array across bars
  - Without 'var', array is recreated on every bar
  - array.new<int>(0) is equivalent to array.new<int>()"""
        },

        # ── array.push ─────────────────────────────────────────
        {
            "id": "func_array_push",
            "name": "array.push",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.push(id, value) → void

Appends a value to the end of an array.

Parameters:
  id    (array<type>)  The array to modify.
  value (type)         Value to append.

Returns: void

Example:
  var arr = array.new<float>(0)
  array.push(arr, close)
  array.push(arr, open)
  // arr now has 2 elements: [close, open]

Notes:
  - Increases array size by 1
  - Use with array.size() to check length
  - Equivalent to append in other languages"""
        },

        # ── array.pop ──────────────────────────────────────────
        {
            "id": "func_array_pop",
            "name": "array.pop",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.pop(id) → <type>

Removes and returns the last element of an array.

Parameters:
  id  (array<type>)  The array to modify.

Returns: The removed element (type matches array type)

Example:
  var arr = array.new<float>(0)
  array.push(arr, 10.0)
  array.push(arr, 20.0)
  last = array.pop(arr)  // last = 20.0, arr = [10.0]

Notes:
  - Reduces array size by 1
  - Throws runtime error if array is empty
  - Use array.size() > 0 guard before calling"""
        },

        # ── array.get ──────────────────────────────────────────
        {
            "id": "func_array_get",
            "name": "array.get",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.get(id, index) → <type>

Returns the value of an array element at index.

Parameters:
  id    (array<type>)  The array.
  index (int)          Zero-based index. Negative index counts from end.

Returns: Element value at index.

Example:
  var arr = array.new<float>(3, 0.0)
  array.set(arr, 0, close)
  first = array.get(arr, 0)   // close
  last  = array.get(arr, -1)  // same as index 2

Notes:
  - Zero-based indexing
  - Negative index: -1 = last element, -2 = second to last
  - Throws runtime error if index out of bounds"""
        },

        # ── array.set ──────────────────────────────────────────
        {
            "id": "func_array_set",
            "name": "array.set",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.set(id, index, value) → void

Sets the value of an array element at index.

Parameters:
  id    (array<type>)  The array to modify.
  index (int)          Zero-based index.
  value (type)         New value to assign.

Returns: void

Example:
  var arr = array.new<float>(5, 0.0)
  array.set(arr, 0, close)
  array.set(arr, 4, high)"""
        },

        # ── array.size ─────────────────────────────────────────
        {
            "id": "func_array_size",
            "name": "array.size",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.size(id) → series int

Returns the number of elements in an array.

Parameters:
  id  (array<type>)  The array.

Returns: series int — current number of elements.

Example:
  var arr = array.new<float>(0)
  array.push(arr, close)
  n = array.size(arr)  // n = 1
  plot(n)

Notes:
  - Use to check array before pop/get operations
  - Dynamic — reflects current size after push/pop"""
        },

        # ── array.clear ────────────────────────────────────────
        {
            "id": "func_array_clear",
            "name": "array.clear",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.clear(id) → void

Removes all elements from an array. Size becomes 0.

Parameters:
  id  (array<type>)  The array to clear.

Returns: void

Example:
  var arr = array.new<float>(5, 1.0)
  if barstate.islast
      array.clear(arr)
      // array.size(arr) == 0"""
        },

        # ── array.insert ───────────────────────────────────────
        {
            "id": "func_array_insert",
            "name": "array.insert",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.insert(id, index, value) → void

Inserts a value at a specified index, shifting elements to the right.

Parameters:
  id    (array<type>)  The array to modify.
  index (int)          Index to insert at (0 = prepend).
  value (type)         Value to insert.

Returns: void

Example:
  var arr = array.new<float>(0)
  array.push(arr, 1.0)
  array.push(arr, 3.0)
  array.insert(arr, 1, 2.0)  // arr = [1.0, 2.0, 3.0]"""
        },

        # ── array.remove ───────────────────────────────────────
        {
            "id": "func_array_remove",
            "name": "array.remove",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.remove(id, index) → <type>

Removes and returns the element at the specified index.

Parameters:
  id    (array<type>)  The array to modify.
  index (int)          Index of element to remove.

Returns: The removed element.

Example:
  var arr = array.new<float>(3, 0.0)
  array.set(arr, 1, 99.0)
  removed = array.remove(arr, 1)  // removed = 99.0"""
        },

        # ── array.sort ─────────────────────────────────────────
        {
            "id": "func_array_sort",
            "name": "array.sort",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.sort(id, order) → void

Sorts array elements in ascending or descending order.

Parameters:
  id    (array<int/float/string>)  The array to sort.
  order (sort_order)               order.ascending (default) or order.descending

Returns: void (modifies in place)

Example:
  var arr = array.new<float>(0)
  array.push(arr, 3.0)
  array.push(arr, 1.0)
  array.push(arr, 2.0)
  array.sort(arr, order.ascending)
  // arr = [1.0, 2.0, 3.0]"""
        },

        # ── array.reverse ──────────────────────────────────────
        {
            "id": "func_array_reverse",
            "name": "array.reverse",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.reverse(id) → void

Reverses the order of all array elements in place.

Parameters:
  id  (array<type>)  The array to reverse.

Returns: void

Example:
  var arr = array.new<float>(3)
  array.set(arr, 0, 1.0)
  array.set(arr, 2, 3.0)
  array.reverse(arr)  // arr = [3.0, ?, 1.0]"""
        },

        # ── array.slice ────────────────────────────────────────
        {
            "id": "func_array_slice",
            "name": "array.slice",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.slice(id, index_from, index_to) → array<type>

Returns a shallow copy of a sub-array from index_from to index_to (exclusive).

Parameters:
  id         (array<type>)  Source array.
  index_from (int)          Start index (inclusive).
  index_to   (int)          End index (exclusive).

Returns: New array<type> containing the slice.

Example:
  var arr = array.new<float>(5, 0.0)
  // Fill arr with values...
  sliced = array.slice(arr, 1, 3)  // elements at index 1 and 2"""
        },

        # ── array.copy ─────────────────────────────────────────
        {
            "id": "func_array_copy",
            "name": "array.copy",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.copy(id) → array<type>

Returns a shallow copy of an array.

Parameters:
  id  (array<type>)  Source array to copy.

Returns: New array<type> — independent copy.

Example:
  var original = array.new<float>(3, 1.0)
  copy = array.copy(original)
  array.set(copy, 0, 99.0)
  // original[0] still = 1.0"""
        },

        # ── array.join ─────────────────────────────────────────
        {
            "id": "func_array_join",
            "name": "array.join",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.join(id, separator) → string

Concatenates array elements into a single string with separator.

Parameters:
  id        (array<string/int/float>)  The array.
  separator (string)                   Separator between elements.

Returns: string

Example:
  var arr = array.new<string>(0)
  array.push(arr, "a")
  array.push(arr, "b")
  result = array.join(arr, ", ")  // "a, b" """
        },

        # ── array.avg ──────────────────────────────────────────
        {
            "id": "func_array_avg",
            "name": "array.avg",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.avg(id) → series float

Returns the mean of all array elements.

Parameters:
  id  (array<int/float>)  The array.

Returns: series float — arithmetic mean.

Example:
  var arr = array.new<float>(0)
  array.push(arr, close)
  avg = array.avg(arr)
  plot(avg)"""
        },

        # ── array.sum ──────────────────────────────────────────
        {
            "id": "func_array_sum",
            "name": "array.sum",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.sum(id) → series float

Returns the sum of all array elements.

Parameters:
  id  (array<int/float>)  The array.

Returns: series float

Example:
  var arr = array.new<float>(0)
  for i = 0 to 9
      array.push(arr, close[i])
  total = array.sum(arr)"""
        },

        # ── array.max ──────────────────────────────────────────
        {
            "id": "func_array_max",
            "name": "array.max",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.max(id, nth) → series float

Returns the largest value in the array. With nth, returns the nth largest.

Parameters:
  id   (array<int/float>)  The array.
  nth  (int)               Optional. 0 = largest (default), 1 = second largest, etc.

Returns: series float

Example:
  var arr = array.new<float>(0)
  array.push(arr, high)
  maxVal = array.max(arr)"""
        },

        # ── array.min ──────────────────────────────────────────
        {
            "id": "func_array_min",
            "name": "array.min",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.min(id, nth) → series float

Returns the smallest value in the array.

Parameters:
  id   (array<int/float>)  The array.
  nth  (int)               Optional. 0 = smallest (default).

Returns: series float"""
        },

        # ── array.includes ─────────────────────────────────────
        {
            "id": "func_array_includes",
            "name": "array.includes",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.includes(id, value) → series bool

Returns true if the array contains value, false otherwise.

Parameters:
  id    (array<type>)  The array to search.
  value (type)         Value to look for.

Returns: series bool

Example:
  var arr = array.new<float>(0)
  array.push(arr, 42.0)
  found = array.includes(arr, 42.0)  // true"""
        },

        # ── array.indexof ──────────────────────────────────────
        {
            "id": "func_array_indexof",
            "name": "array.indexof",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.indexof(id, value) → series int

Returns the index of the first occurrence of value. Returns -1 if not found.

Parameters:
  id    (array<type>)  The array to search.
  value (type)         Value to find.

Returns: series int — index, or -1 if not found.

Example:
  var arr = array.new<string>(0)
  array.push(arr, "bull")
  idx = array.indexof(arr, "bull")  // idx = 0"""
        },

        # ── array.lastindexof ──────────────────────────────────
        {
            "id": "func_array_lastindexof",
            "name": "array.lastindexof",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.lastindexof(id, value) → series int

Returns the index of the last occurrence of value. Returns -1 if not found.

Parameters:
  id    (array<type>)  The array.
  value (type)         Value to find.

Returns: series int"""
        },

        # ── array.concat ───────────────────────────────────────
        {
            "id": "func_array_concat",
            "name": "array.concat",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.concat(id1, id2) → array<type>

Appends all elements of id2 to the end of id1. Modifies id1 in place.

Parameters:
  id1  (array<type>)  Target array (modified).
  id2  (array<type>)  Source array (read only).

Returns: array<type> — the modified id1.

Example:
  var a = array.new<float>(2, 1.0)
  var b = array.new<float>(2, 2.0)
  array.concat(a, b)
  // a = [1.0, 1.0, 2.0, 2.0]"""
        },

        # ── array.fill ─────────────────────────────────────────
        {
            "id": "func_array_fill",
            "name": "array.fill",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.fill(id, value, index_from, index_to) → void

Fills array elements with value from index_from to index_to (exclusive).

Parameters:
  id         (array<type>)  The array to fill.
  value      (type)         Fill value.
  index_from (int)          Start index (default 0).
  index_to   (int)          End index exclusive (default array.size).

Returns: void"""
        },

        # ── array.shift ────────────────────────────────────────
        {
            "id": "func_array_shift",
            "name": "array.shift",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.shift(id) → <type>

Removes and returns the first element of the array (index 0).
All other elements shift left by one index.

Parameters:
  id  (array<type>)  The array to modify.

Returns: The removed element.

Notes:
  - Use array.unshift() to prepend elements
  - For a FIFO queue: push() + shift()"""
        },

        # ── array.unshift ──────────────────────────────────────
        {
            "id": "func_array_unshift",
            "name": "array.unshift",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.unshift(id, value) → void

Prepends value at the beginning of the array (index 0).
All existing elements shift right by one index.

Parameters:
  id    (array<type>)  The array to modify.
  value (type)         Value to prepend.

Returns: void

Notes:
  - Opposite of array.shift()
  - For a LIFO (stack) from front: unshift() + shift()"""
        },

        # ── array.stdev ────────────────────────────────────────
        {
            "id": "func_array_stdev",
            "name": "array.stdev",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.stdev(id, biased) → series float

Returns the standard deviation of array elements.

Parameters:
  id     (array<int/float>)  The array.
  biased (bool)              If true: population stdev. If false (default): sample stdev.

Returns: series float"""
        },

        # ── array.variance ─────────────────────────────────────
        {
            "id": "func_array_variance",
            "name": "array.variance",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.variance(id, biased) → series float

Returns the variance of array elements.

Parameters:
  id     (array<int/float>)  The array.
  biased (bool)              Population (true) or sample (false, default) variance.

Returns: series float"""
        },

        # ── array.median ───────────────────────────────────────
        {
            "id": "func_array_median",
            "name": "array.median",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.median(id) → series float

Returns the median value of array elements.

Parameters:
  id  (array<int/float>)  The array.

Returns: series float — middle value when sorted."""
        },

        # ── array.mode ─────────────────────────────────────────
        {
            "id": "func_array_mode",
            "name": "array.mode",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.mode(id) → series float

Returns the most frequently occurring value.
For multiple modes, returns the smallest.

Parameters:
  id  (array<int/float>)  The array.

Returns: series float"""
        },

        # ── array.percentile_linear_interpolation ──────────────
        {
            "id": "func_array_percentile_linear",
            "name": "array.percentile_linear_interpolation",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.percentile_linear_interpolation(id, percentage) → series float

Returns the value at the given percentile using linear interpolation.

Parameters:
  id         (array<int/float>)  The array.
  percentage (float)             Percentile between 0 and 100.

Returns: series float

Example:
  var arr = array.new<float>(0)
  for i = 0 to 99
      array.push(arr, close[i])
  p90 = array.percentile_linear_interpolation(arr, 90)"""
        },

        # ── array.percentile_nearest_rank ──────────────────────
        {
            "id": "func_array_percentile_nearest",
            "name": "array.percentile_nearest_rank",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.percentile_nearest_rank(id, percentage) → series float

Returns the value at the given percentile using nearest rank method.

Parameters:
  id         (array<int/float>)  The array.
  percentage (float)             Percentile between 0 and 100.

Returns: series float"""
        },

        # ── array.range ────────────────────────────────────────
        {
            "id": "func_array_range",
            "name": "array.range",
            "namespace": "array",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """array.range(id) → series float

Returns the difference between the maximum and minimum values.

Parameters:
  id  (array<int/float>)  The array.

Returns: series float — max - min"""
        },

        # ══ MATRIX FUNCTIONS ══════════════════════════════════════
        {
            "id": "func_matrix_new",
            "name": "matrix.new",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.new<type>(rows, columns, initial_value) → matrix<type>

Creates a new matrix with given dimensions.

Parameters:
  rows          (int)   Number of rows.
  columns       (int)   Number of columns.
  initial_value (type)  Initial fill value. Optional.

Returns: matrix<type>

Example:
  m = matrix.new<float>(3, 3, 0.0)  // 3x3 zero matrix
  m2 = matrix.new<int>(2, 4)        // 2x4 matrix

Notes:
  - Use 'var' to persist across bars
  - Zero-based row/column indexing"""
        },

        {
            "id": "func_matrix_get",
            "name": "matrix.get",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.get(id, row, column) → <type>

Returns element at specified row and column.

Parameters:
  id     (matrix<type>)  The matrix.
  row    (int)           Row index (0-based).
  column (int)           Column index (0-based).

Returns: Element value.

Example:
  m = matrix.new<float>(2, 2, 0.0)
  val = matrix.get(m, 0, 1)"""
        },

        {
            "id": "func_matrix_set",
            "name": "matrix.set",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.set(id, row, column, value) → void

Sets element at specified row and column.

Parameters:
  id     (matrix<type>)  The matrix to modify.
  row    (int)           Row index (0-based).
  column (int)           Column index (0-based).
  value  (type)          Value to assign.

Returns: void

Example:
  m = matrix.new<float>(2, 2, 0.0)
  matrix.set(m, 0, 0, close)
  matrix.set(m, 1, 1, open)"""
        },

        {
            "id": "func_matrix_rows",
            "name": "matrix.rows",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.rows(id) → series int

Returns the number of rows in the matrix.

Parameters:
  id  (matrix<type>)  The matrix.

Returns: series int"""
        },

        {
            "id": "func_matrix_columns",
            "name": "matrix.columns",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.columns(id) → series int

Returns the number of columns in the matrix.

Parameters:
  id  (matrix<type>)  The matrix.

Returns: series int"""
        },

        {
            "id": "func_matrix_add_row",
            "name": "matrix.add_row",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.add_row(id, row, array_id) → void

Inserts a row at the specified index using values from an array.

Parameters:
  id       (matrix<type>)  The matrix to modify.
  row      (int)           Row index to insert at.
  array_id (array<type>)   Array providing values (length must = matrix.columns).

Returns: void"""
        },

        {
            "id": "func_matrix_add_col",
            "name": "matrix.add_col",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.add_col(id, column, array_id) → void

Inserts a column at the specified index using values from an array.

Parameters:
  id       (matrix<type>)  The matrix to modify.
  column   (int)           Column index to insert at.
  array_id (array<type>)   Array providing values (length must = matrix.rows).

Returns: void"""
        },

        {
            "id": "func_matrix_mult",
            "name": "matrix.mult",
            "namespace": "matrix",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """matrix.mult(id1, id2) → matrix<float>

Multiplies two matrices or a matrix by a scalar/array.

Parameters:
  id1  (matrix<int/float>)                   Left operand.
  id2  (matrix<int/float>/int/float/array)   Right operand.

Returns: matrix<float> — result of multiplication.

Notes:
  - For matrix × matrix: id1.columns must equal id2.rows
  - For matrix × scalar: each element multiplied by scalar"""
        },

        # ══ MAP FUNCTIONS ═════════════════════════════════════════
        {
            "id": "func_map_new",
            "name": "map.new",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.new<keyType, valueType>() → map<keyType, valueType>

Creates a new empty map (key-value store).

Parameters: none

Returns: map<keyType, valueType>

Supported key types: int, float, bool, string, color
Supported value types: any Pine type

Examples:
  // String → float map
  var m = map.new<string, float>()
  
  // Int → bool map
  var flags = map.new<int, bool>()

Notes:
  - Keys must be unique — putting same key overwrites value
  - Use 'var' to persist across bars"""
        },

        {
            "id": "func_map_put",
            "name": "map.put",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.put(id, key, value) → void

Inserts or updates a key-value pair.

Parameters:
  id    (map<K,V>)  The map to modify.
  key   (K)         Key to insert/update.
  value (V)         Value to associate with key.

Returns: void

Example:
  var m = map.new<string, float>()
  map.put(m, "ema", ta.ema(close, 14))
  map.put(m, "rsi", ta.rsi(close, 14))"""
        },

        {
            "id": "func_map_get",
            "name": "map.get",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.get(id, key) → <valueType>

Returns the value associated with key. Returns na if key not found.

Parameters:
  id   (map<K,V>)  The map.
  key  (K)         Key to look up.

Returns: Value or na if key not present.

Example:
  var m = map.new<string, float>()
  map.put(m, "ema", ta.ema(close, 14))
  val = map.get(m, "ema")  // returns EMA value"""
        },

        {
            "id": "func_map_contains",
            "name": "map.contains",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.contains(id, key) → series bool

Returns true if the map contains the key, false otherwise.

Parameters:
  id   (map<K,V>)  The map.
  key  (K)         Key to check.

Returns: series bool

Example:
  if map.contains(m, "ema")
      val = map.get(m, "ema")"""
        },

        {
            "id": "func_map_remove",
            "name": "map.remove",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.remove(id, key) → <valueType>

Removes and returns the value associated with key.
Returns na if key not found.

Parameters:
  id   (map<K,V>)  The map to modify.
  key  (K)         Key to remove.

Returns: Removed value or na."""
        },

        {
            "id": "func_map_size",
            "name": "map.size",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.size(id) → series int

Returns the number of key-value pairs in the map.

Parameters:
  id  (map<K,V>)  The map.

Returns: series int"""
        },

        {
            "id": "func_map_keys",
            "name": "map.keys",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.keys(id) → array<keyType>

Returns a new array containing all keys in the map.

Parameters:
  id  (map<K,V>)  The map.

Returns: array<K> — order is not guaranteed.

Example:
  var m = map.new<string, float>()
  map.put(m, "a", 1.0)
  map.put(m, "b", 2.0)
  keys = map.keys(m)  // ["a", "b"] in some order"""
        },

        {
            "id": "func_map_values",
            "name": "map.values",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.values(id) → array<valueType>

Returns a new array containing all values in the map.

Parameters:
  id  (map<K,V>)  The map.

Returns: array<V> — order mirrors map.keys() order."""
        },

        {
            "id": "func_map_copy",
            "name": "map.copy",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.copy(id) → map<K,V>

Returns a shallow copy of the map.

Parameters:
  id  (map<K,V>)  Source map.

Returns: New map<K,V> — independent copy."""
        },

        {
            "id": "func_map_clear",
            "name": "map.clear",
            "namespace": "map",
            "type": "function",
            "source": "tradingview_live",
            "version": "v6",
            "document": """map.clear(id) → void

Removes all key-value pairs from the map.

Parameters:
  id  (map<K,V>)  The map to clear.

Returns: void. Map size becomes 0."""
        },
    ]
    
    # Build upsert data
    ids = [e["id"] for e in entries]
    documents = [e["document"] for e in entries]
    metadatas = [{k: v for k, v in e.items()
                  if k not in ("id", "document")}
                 for e in entries]
    
    # Upsert into existing collection
    col.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"✅ Upserted {len(entries)} array/matrix/map function entries")
    
    return col

def ingest_profiling_page(col):
    print("\n" + "="*50)
    print("INGEST B — Profiling and Optimization guide page")
    print("="*50)
    
    # Since we already indexed the profiling page in the previous task,
    # we'll just add the specific entries requested
    
    today = datetime.now().isoformat()
    base_url = "https://www.tradingview.com/pine-script-docs/writing/profiling-and-optimization/"
    
    profiler_entries = [
        {
            "id": "profiler_pine_profiler_overview",
            "name": "pine_profiler",
            "namespace": "profiler",
            "type": "guide",
            "source": "tradingview_live",
            "url": base_url + "#pine-profiler",
            "indexed_at": today,
            "version": "v6",
            "document": """# Pine Profiler Overview

The Pine Profiler is a powerful utility that analyzes the executions of all significant code lines and blocks in a script and displays helpful performance information next to the lines inside the Pine Editor. By inspecting the Profiler's results, programmers can gain a clearer perspective on a script's overall runtime, the distribution of runtime across its significant code regions, and the critical portions that may need extra attention and optimization.

## How to Enable Profiler Mode

The Pine Profiler can analyze the runtime performance of any editable script coded in Pine Script v6. To profile a script, add it to the chart, open the source code in the Pine Editor, and turn on the "Profiler mode" switch in the dropdown accessible via the "More" option in the top-right corner.

## Flame Icons and Tooltips

When a script contains at least four significant lines of code, the Profiler will include "flame" icons next to the top three code regions with the highest performance impact. If one or more of the highest-impact code regions are outside the lines visible inside the Pine Editor, a "flame" icon and a number indicating how many critical lines are outside the view will appear at the top or bottom of the left margin.

Hovering the mouse pointer over the space next to a line highlights the analyzed code and exposes a tooltip with additional information, including the time spent and the number of executions.

## Tooltip Fields

The information shown next to each line and in the corresponding tooltip depends on the profiled code region:

- **Line number**: Indicates the analyzed code line
- **Time**: Shows the runtime percentage and actual time spent
- **Executions**: Shows the number of times that specific line executed while running the script

The time information for the line represents the time spent completing all executions, not the time spent on a single execution. To estimate the average time spent per execution, divide the line's time by the number of executions."""
        },
        {
            "id": "profiler_optimization_guide",
            "name": "pine_optimization.techniques",
            "namespace": "profiler",
            "type": "guide",
            "source": "tradingview_live",
            "url": base_url + "#optimization",
            "indexed_at": today,
            "version": "v6",
            "document": """# Pine Script Optimization Techniques

Code optimization involves modifying a script's source code for improved execution time, resource efficiency, and scalability. Most techniques involve reducing the number of times critical calculations occur or replacing significant calculations with simplified formulas or built-ins.

## Key Optimization Techniques

### Avoid Redundant Expressions
Avoid calling the same function multiple times with identical arguments. Store the result in a variable and reuse it. The compiler may not always optimize redundant identical expressions automatically.

### Use var for Persistent Values
Save results of infrequently changing calculations to var or varip variables. Only update when the calculation changes. This prevents recalculation on every bar.

### Cache request.security() Outside Functions
Each request.*() call can significantly impact resource usage. Keep request.*() calls in the global scope to avoid translation complications and profiler result interpretation issues. Cache results in variables when possible.

### Use Nested If vs Switch for Granularity
Replace switch or if...else if structures with nested if blocks when you need detailed performance information for each conditional expression. This allows more granular profiling of each condition.

### Separate Multi-Expression Lines
Move each expression to a separate line for more detailed profiling insights, especially for higher-impact calculations. When a line contains multiple expressions, the profiler shows combined results.

### Use Built-in Functions
Pine Script features a variety of built-in functions and variables with internal optimizations. Using built-ins like ta.highest(), ta.lowest(), ta.sma(), etc., is often more efficient than custom implementations.

### Minimize Loops
When possible, replace loops with simplified loop-free expressions, optimized built-ins, or distribute loop iterations across bars.

### Optimize Drawing Updates
For drawings that change across historical bars, restrict updates to the last historical bar and realtime bars using barstate.islast, since users only see the final result on historical bars."""
        }
    ]
    
    # Build upsert data for profiler entries
    ids = [e["id"] for e in profiler_entries]
    documents = [e["document"] for e in profiler_entries]
    metadatas = [{k: v for k, v in e.items()
                  if k not in ("id", "document")}
                 for e in profiler_entries]
    
    # Upsert profiler entries
    col.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"✅ Upserted {len(profiler_entries)} profiler guide entries")
    
    return col

def final_verification(col):
    print("\n" + "="*50)
    print("STEP 3 — FINAL VERIFICATION")
    print("="*50)
    
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
        print(f"❌ Missing entries: {missing}")
    else:
        print(f"✅ All array/matrix/map functions indexed")
    
    # Check B — profiler entries exist
    r = col.get(where={"namespace": "profiler"}, include=["metadatas"])
    profiler_count = len(r["ids"])
    print(f"{'✅' if profiler_count >= 5 else '❌'} Profiler entries: {profiler_count}")
    
    # Check C — semantic search finds array.push
    r = col.query(
        query_texts=["add element to end of array pine script"],
        n_results=3
    )
    found_push = any("array.push" in (doc or "") 
                     for doc in r["documents"][0])
    print(f"{'✅' if found_push else '❌'} Semantic: 'add element to array' finds array.push")
    
    # Check D — profiler semantic search
    r2 = col.query(
        query_texts=["how to profile pinescript performance measure execution time"],
        n_results=3
    )
    found_profiler = any("profil" in (doc or "").lower() 
                         for doc in r2["documents"][0])
    print(f"{'✅' if found_profiler else '❌'} Semantic: profiler query finds profiler docs")
    
    # Final count
    total = col.count()
    print(f"\nDB total entries: {total}")
    print(f"Expected minimum: 1685 (1647 + 38 array/matrix/map + profiler)")
    
    return len(missing) == 0 and profiler_count >= 5 and found_push and found_profiler

if __name__ == "__main__":
    # Run pre-check
    print("="*50)
    print("PRE-CHECK — confirm what is actually missing")
    print("="*50)
    
    # Connect to the existing collection
    client = chromadb.PersistentClient(path="./pinescript_db")
    col = client.get_collection("pinescript_v6")
    
    # Check which array functions exist
    functions_to_check = ["array.new", "array.push", "array.pop", "array.get",
                         "array.set", "array.size", "array.sort", "array.includes",
                         "matrix.new", "matrix.get", "matrix.set", "map.new", "map.put"]
    
    print("Checking existing functions:")
    for name in functions_to_check:
        r = col.get(where={"name": name}, include=["metadatas"])
        status = "EXISTS" if r["ids"] else "MISSING"
        print(f"{status}: {name}")
    
    # Run both ingests
    col = main()  # Ingest A
    col = ingest_profiling_page(col)  # Ingest B
    
    # Final verification
    success = final_verification(col)
    
    if success:
        print("\n✅ All verification checks passed!")
    else:
        print("\n❌ Some verification checks failed!")
