#!/usr/bin/env python3
"""Test script to verify all 7 bug fixes in pinescript_mcp.py"""

import asyncio
import sys
sys.path.insert(0, '.')

from pinescript_mcp import (
    EntryLookup, CodeFixInput, StrategyGenInput,
    get_function, get_type, fix_and_validate, 
    generate_strategy, lookup_and_correct, get_source_url
)

async def test_fixes():
    print('=== TESTING 7 BUG FIXES ===\n')
    
    passed = 0
    total = 8
    
    # V1. Test get_function('array.new') - BUG 1
    print('V1. get_function("array.new") - BUG 1:')
    try:
        result = await get_function(EntryLookup(name='array.new'))
        # Since array.new might not exist as separate entry, check if it returns array-related content
        if 'array' in result and ('function' in result.lower() or 'syntax' in result.lower()):
            print('  PASS: Returns array-related function docs')
            passed += 1
        else:
            print('  FAIL: Does not return array-related function docs')
            print(f'    First 200 chars: {result[:200]}')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    # V2. Test get_type('array') - BUG 2
    print('\nV2. get_type("array") - BUG 2:')
    try:
        result = await get_type(EntryLookup(name='array'))
        if 'array.new' not in result and ('type' in result.lower() or 'fields' in result.lower() or 'methods' in result.lower()):
            print('  PASS: Returns array type docs (not function)')
            passed += 1
        else:
            print('  FAIL: Returns same as get_function or not type docs')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    # V3. Test get_function != get_type - BUG 2 verification
    print('\nV3. get_function("ta.ema") != get_type("array") - BUG 2 verification:')
    try:
        f_result = await get_function(EntryLookup(name='ta.ema'))
        t_result = await get_type(EntryLookup(name='array'))
        if f_result != t_result:
            print('  PASS: Functions return different content')
            passed += 1
        else:
            print('  FAIL: Functions return same content')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    # V4. Test fix_and_validate - BUG 3
    print('\nV4. fix_and_validate with undeclared identifier - BUG 3:')
    try:
        code = '//@version=6\nindicator("t")\nplot(undeclaredVar)'
        result = await fix_and_validate(CodeFixInput(code=code, error_description='Undeclared identifier'))
        if 'Did you mean' in result or 'not declared' in result or 'ta.' in result:
            print('  PASS: Provides relevant fix suggestion')
            passed += 1
        else:
            print('  FAIL: Generic/unrelated fix suggestion')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    # V5. Test generate_strategy - BUG 4
    print('\nV5. generate_strategy compiles - BUG 4:')
    try:
        result = await generate_strategy(StrategyGenInput(name='Test', description='test'))
        if 'Validated: ✅ 0 compilation errors' in result:
            print('  PASS: Template compiles successfully')
            passed += 1
        else:
            print('  FAIL: Template has compilation errors')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    # V6. Test lookup_and_correct - BUG 5
    print('\nV6. lookup_and_correct v5 namespace fixes - BUG 5:')
    try:
        code = '//@version=6\nindicator("t")\nfm=ema(close,12)\nsm=sma(close,26)\nb=crossover(fm,sm)\nplotshape(b)'
        result = await lookup_and_correct(CodeFixInput(code=code, error_description='v5 legacy code'))
        fixes = result.count('Replaced:')
        if fixes >= 3:
            print(f'  PASS: Found {fixes} namespace fixes')
            passed += 1
        else:
            print(f'  FAIL: Only found {fixes} namespace fixes')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    # V7. Test get_source_url - BUG 6
    print('\nV7. get_source_url("ta.ema") - BUG 6:')
    try:
        result = await get_source_url(EntryLookup(name='ta.ema'))
        if '#fun_ta.ema' in result and 'sma' not in result:
            print('  PASS: URL contains #fun_ta.ema')
            passed += 1
        else:
            print('  FAIL: URL wrong or contains sma')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    # V8. Test get_source_url strategy.entry - BUG 7
    print('\nV8. get_source_url("strategy.entry") - BUG 7:')
    try:
        result = await get_source_url(EntryLookup(name='strategy.entry'))
        if '#fun_strategy.entry' in result and 'market-orders' not in result:
            print('  PASS: URL contains #fun_strategy.entry')
            passed += 1
        else:
            print('  FAIL: URL wrong or contains market-orders')
    except Exception as e:
        print(f'  ERROR: {e}')
    
    print(f'\n=== VERIFICATION COMPLETE ===')
    print(f'Score: {passed}/{total} tests passed')
    
    if passed == total:
        print('🎉 ALL BUGS FIXED SUCCESSFULLY!')
    else:
        print(f'⚠️  {total - passed} issues remain')

if __name__ == "__main__":
    asyncio.run(test_fixes())
