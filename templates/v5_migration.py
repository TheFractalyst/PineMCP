"""
templates/v5_migration.py
------------------------------------------------------------------------------
Complete v5 -> v6 namespace migration map and breaking-change patterns.
Used by lookup_and_correct tool for automatic code fixing.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# v5 -> v6 namespace migration map
# NOTE: (?<!\.) prevents double-prefixing; \b ensures whole-word match
# -----------------------------------------------------------------------------

V5_TO_V6: dict[str, str] = {
    # ta.* functions (most common v5 issue)
    r'(?<!\.)\bema\s*\(':          'ta.ema(',
    r'(?<!\.)\bsma\s*\(':          'ta.sma(',
    r'(?<!\.)\brsi\s*\(':          'ta.rsi(',
    r'(?<!\.)\bmacd\s*\(':         'ta.macd(',
    r'(?<!\.)\batr\s*\(':          'ta.atr(',
    r'(?<!\.)\bbb\s*\(':           'ta.bb(',
    r'(?<!\.)\bstoch\s*\(':        'ta.stoch(',
    r'(?<!\.)\bwma\s*\(':          'ta.wma(',
    r'(?<!\.)\bhma\s*\(':          'ta.hma(',
    r'(?<!\.)\bvwap\b':            'ta.vwap',
    r'(?<!\.)\bcrossover\s*\(':    'ta.crossover(',
    r'(?<!\.)\bcrossunder\s*\(':   'ta.crossunder(',
    r'(?<!\.)\bhighest\s*\(':      'ta.highest(',
    r'(?<!\.)\blowest\s*\(':       'ta.lowest(',
    r'(?<!\.)\bbarssince\s*\(':    'ta.barssince(',
    r'(?<!\.)\bvaluewhen\s*\(':    'ta.valuewhen(',
    r'(?<!\.)\blinreg\s*\(':       'ta.linreg(',
    r'(?<!\.)\bmom\s*\(':          'ta.mom(',
    r'(?<!\.)\bcum\s*\(':          'ta.cum(',
    r'(?<!\.)\bchange\s*\(':       'ta.change(',
    r'(?<!\.)\bpivothigh\s*\(':    'ta.pivothigh(',
    r'(?<!\.)\bpivotlow\s*\(':     'ta.pivotlow(',
    r'(?<!\.)\bsupertrend\s*\(':   'ta.supertrend(',
    r'(?<!\.)\bcorrelation\s*\(':  'ta.correlation(',
    r'(?<!\.)\bpercentrank\s*\(':  'ta.percentrank(',
    r'(?<!\.)\bdmi\s*\(':          'ta.dmi(',
    r'(?<!\.)\bstdev\s*\(':        'ta.stdev(',
    r'(?<!\.)\bvariance\s*\(':     'ta.variance(',
    r'(?<!\.)\brising\s*\(':       'ta.rising(',
    r'(?<!\.)\bfalling\s*\(':      'ta.falling(',
    r'(?<!\.)\balma\s*\(':         'ta.alma(',
    r'(?<!\.)\bkama\s*\(':         'ta.kama(',
    r'(?<!\.)\bswma\s*\(':         'ta.swma(',
    r'(?<!\.)\bpercentile_nearest_rank\s*\(':  'ta.percentile_nearest_rank(',
    r'(?<!\.)\bpercentile_linear_interpolation\s*\(': 'ta.percentile_linear_interpolation(',
    # request.* functions
    r'(?<!\.)\bsecurity\s*\(':     'request.security(',
    # math.* functions
    r'(?<!\.)\babs\s*\(':          'math.abs(',
    r'(?<!\.)\bround\s*\(':        'math.round(',
    r'(?<!\.)\bfloor\s*\(':        'math.floor(',
    r'(?<!\.)\bceil\s*\(':         'math.ceil(',
    r'(?<!\.)\bpow\s*\(':          'math.pow(',
    r'(?<!\.)\bsqrt\s*\(':         'math.sqrt(',
    r'(?<!\.)\blog\s*\(':          'math.log(',
    r'(?<!\.)\bexp\s*\(':          'math.exp(',
    r'(?<!\.)\bsin\s*\(':          'math.sin(',
    r'(?<!\.)\bcos\s*\(':          'math.cos(',
    r'(?<!\.)\btan\s*\(':          'math.tan(',
    r'(?<!\.)\basin\s*\(':         'math.asin(',
    r'(?<!\.)\bacos\s*\(':         'math.acos(',
    r'(?<!\.)\batan\s*\(':         'math.atan(',
    r'(?<!\.)\bsign\s*\(':         'math.sign(',
    r'(?<!\.)\bmin\s*\(':          'math.min(',
    r'(?<!\.)\bmax\s*\(':          'math.max(',
    r'(?<!\.)\bavg\s*\(':          'math.avg(',
    # str.* functions
    r'(?<!\.)\btostring\s*\(':     'str.tostring(',
    r'(?<!\.)\btonumber\s*\(':     'str.tonumber(',
}
