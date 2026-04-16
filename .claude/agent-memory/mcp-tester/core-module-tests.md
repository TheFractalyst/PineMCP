---
name: Core Module Test Coverage
description: Tests added for core/db.py, core/hot_cache.py, core/pine_facade.py
type: project
---

## Test Coverage Added (2026-04-16)

Generated 80 new tests across 3 files for PineScript v6 MCP server core modules.

### test_db.py (21 tests)
**ChromaDBCircuitBreaker** (7 tests):
- Initial state closed
- Stays closed below threshold
- Opens at threshold
- Auto-reset after cooldown
- Success resets failures
- is_open() refreshes state
- Multiple failures beyond threshold

**L1 Query Cache** (4 tests):
- Cache hit returns without embedding
- Cache miss calls embedding
- TTL expiration
- Eviction at max size

**search_by_name Qualified** (3 tests):
- Exact match for qualified names
- No fallback to fuzzy for qualified names
- Where filter support

**search_by_name Unqualified** (5 tests):
- Empty name returns empty
- Name index lookup
- Name index with where filter
- ChromaDB fallback
- Fuzzy fallback

**Reset Caches** (2 tests):
- Resets name index
- Resets hot cache

### test_hot_cache.py (18 tests)
**Cache Lookup Basic** (6 tests):
- Exact match hit
- Case insensitive lookup
- Whitespace stripping
- Miss returns None
- Miss increments counter
- Hit increments counter

**Cache Lookup Dot Behavior** (3 tests):
- Qualified name exact match
- Qualified name miss if not in cache (no automatic fallback)
- Dot in name exact only

**Build Hot Cache Idempotency** (3 tests):
- Build is idempotent
- ensure_hot_cache builds once
- Concurrent builds are serialized

**Build Hot Cache Functionality** (5 tests):
- Loads namespace entries
- Handles duplicate names (keeps richer doc)
- Loads global variables
- Handles namespace load failure
- Returns false on total failure

**Thread Safety** (1 test):
- Concurrent lookups thread safe

### test_pine_facade.py (41 tests)
**PineFacadeCircuitBreaker States** (8 tests):
- Initial state closed
- Opens at threshold
- Compiler error does not open
- Success resets network failures
- Compiler error resets network failures
- Exponential backoff
- Backoff capped at 10 minutes
- Stats reporting

**Normalize Facade Response** (9 tests):
- Successful compilation
- Compilation with errors
- Rejection shape
- Errors at top level
- Error with message field
- Error with start object
- Warning separation
- Placeholder resolution in text
- Meta extraction
- Non-dict error item

**Enrich Error With Code** (8 tests):
- Resolves identifier placeholder
- Resolves name placeholder
- Resolves multiple placeholders
- Uses defaults for missing context
- No change when no placeholders
- Empty code returns original
- Removes unknown placeholders
- Extracts identifier with dot

**Call Pine Facade Circuit Breaker** (1 test):
- Returns error when circuit open

**Call Pine Facade HTTP Status** (3 tests):
- HTTP 403 returns error
- HTTP 503 returns error
- HTTP 502/504 handled

**Call Pine Facade Network Errors** (3 tests):
- Connect error returns user friendly
- Timeout error returns user friendly
- OSError returns user friendly

**Call Pine Facade Caching** (2 tests):
- Cache hit returns cached result
- Successful result cached

**Call Pine Facade Empty Input** (2 tests):
- Empty code returns error
- Whitespace-only code returns error

**Call Pine Facade JSON Parsing** (1 test):
- Non-JSON response handled

**Get Facade Client** (2 tests):
- Returns singleton
- Creates new if closed

**Shutdown HTTP Client** (1 test):
- Closes client

## Key Patterns Used
- unittest.mock for external dependencies (no real HTTP calls)
- pytest.mark.asyncio for async functions
- Module-level variable patching via `patch.object(module, 'var')`
- Proper global state cleanup in fixtures
- Thread safety tests using threading.Thread

## Test Locations
- `/Users/fractalyst/pinescript_mcp/tests/test_db.py`
- `/Users/fractalyst/pinescript_mcp/tests/test_hot_cache.py`
- `/Users/fractalyst/pinescript_mcp/tests/test_pine_facade.py`
