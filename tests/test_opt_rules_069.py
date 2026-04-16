"""
test_opt_rules_069.py — Tests for OPT-069 through OPT-079.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.optimizer import analyze_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# OPT-069: Matrix operations in per-bar loop
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT069MatrixInLoop:
    """OPT-069: Matrix operations inside per-bar loops."""

    def test_detects_matrix_in_for_loop(self):
        code = """\
//@version=6
indicator("test")
m = matrix.new<float>(3, 3, 0.0)
for i = 0 to 10
    matrix.set(m, 0, 0, close * i)
plot(close)
"""
        results = analyze_code(code)
        opt069 = [r for r in results if r.rule_id == "OPT-069"]
        assert len(opt069) >= 1

    def test_detects_matrix_in_while_loop(self):
        code = """\
//@version=6
indicator("test")
m = matrix.new<float>(3, 3, 0.0)
var i = 0
while i < 10
    matrix.add(m, 0, 0, 1.0)
    i += 1
plot(close)
"""
        results = analyze_code(code)
        opt069 = [r for r in results if r.rule_id == "OPT-069"]
        assert len(opt069) >= 1

    def test_no_false_positive_matrix_outside_loop(self):
        code = """\
//@version=6
indicator("test")
m = matrix.new<float>(3, 3, 0.0)
matrix.set(m, 0, 0, close)
plot(close)
"""
        results = analyze_code(code)
        opt069 = [r for r in results if r.rule_id == "OPT-069"]
        assert len(opt069) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-070: input.*() in local scope
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT070InputInLocalScope:
    """OPT-070: input.*() calls inside local scopes (if/for/function)."""

    def test_detects_input_in_if_block(self):
        code = """\
//@version=6
indicator("test")
if close > open
    x = input.int(10, "Length")
plot(close)
"""
        results = analyze_code(code)
        opt070 = [r for r in results if r.rule_id == "OPT-070"]
        assert len(opt070) >= 1

    def test_detects_input_in_for_loop(self):
        code = """\
//@version=6
indicator("test")
for i = 0 to 5
    src = input.source(close, "Source")
plot(close)
"""
        results = analyze_code(code)
        opt070 = [r for r in results if r.rule_id == "OPT-070"]
        assert len(opt070) >= 1

    def test_detects_input_float_in_local_scope(self):
        code = """\
//@version=6
indicator("test")
if true
    mult = input.float(2.0, "Multiplier")
plot(close)
"""
        results = analyze_code(code)
        opt070 = [r for r in results if r.rule_id == "OPT-070"]
        assert len(opt070) >= 1

    def test_no_false_positive_input_at_global_scope(self):
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length")
src = input.source(close, "Source")
plot(ta.sma(src, length))
"""
        results = analyze_code(code)
        opt070 = [r for r in results if r.rule_id == "OPT-070"]
        assert len(opt070) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-071: input.int() missing minval/maxval bounds
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT071InputMissingBounds:
    """OPT-071: input.int() without minval or maxval bounds."""

    def test_detects_unbounded_input_int(self):
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length")
plot(ta.sma(close, length))
"""
        results = analyze_code(code)
        opt071 = [r for r in results if r.rule_id == "OPT-071"]
        assert len(opt071) >= 1

    def test_no_false_positive_input_int_with_minval(self):
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length", minval=1)
plot(ta.sma(close, length))
"""
        results = analyze_code(code)
        opt071 = [r for r in results if r.rule_id == "OPT-071"]
        assert len(opt071) == 0

    def test_no_false_positive_input_int_with_maxval(self):
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length", maxval=200)
plot(ta.sma(close, length))
"""
        results = analyze_code(code)
        opt071 = [r for r in results if r.rule_id == "OPT-071"]
        assert len(opt071) == 0

    def test_no_false_positive_input_int_with_both_bounds(self):
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length", minval=1, maxval=200)
plot(ta.sma(close, length))
"""
        results = analyze_code(code)
        opt071 = [r for r in results if r.rule_id == "OPT-071"]
        assert len(opt071) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-072: syminfo.ticker in request.security
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT072TickerVsTickerid:
    """OPT-072: syminfo.ticker in request.security() instead of syminfo.tickerid."""

    def test_detects_ticker_in_request_security(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.ticker, "1D", close)
plot(s)
"""
        results = analyze_code(code)
        opt072 = [r for r in results if r.rule_id == "OPT-072"]
        assert len(opt072) >= 1
        assert opt072[0].severity == "high"

    def test_no_false_positive_tickerid(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.tickerid, "1D", close)
plot(s)
"""
        results = analyze_code(code)
        opt072 = [r for r in results if r.rule_id == "OPT-072"]
        assert len(opt072) == 0

    def test_detects_ticker_with_extra_spaces(self):
        code = """\
//@version=6
indicator("test")
s = request.security(  syminfo.ticker, "1D", close)
plot(s)
"""
        results = analyze_code(code)
        opt072 = [r for r in results if r.rule_id == "OPT-072"]
        assert len(opt072) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# OPT-073: Redundant strategy.cancel_all/close_all
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT073RedundantCancelClose:
    """OPT-073: Multiple strategy.cancel_all() or strategy.close_all() calls."""

    def test_detects_duplicate_cancel_all(self):
        code = """\
//@version=6
strategy("test", overlay=true)
if barstate.islast
    strategy.cancel_all()
    strategy.cancel_all()
plot(close)
"""
        results = analyze_code(code)
        opt073 = [r for r in results if r.rule_id == "OPT-073"]
        assert len(opt073) >= 1

    def test_detects_duplicate_close_all(self):
        code = """\
//@version=6
strategy("test", overlay=true)
if close < open
    strategy.close_all()
    strategy.close_all()
plot(close)
"""
        results = analyze_code(code)
        opt073 = [r for r in results if r.rule_id == "OPT-073"]
        assert len(opt073) >= 1

    def test_no_false_positive_single_cancel_all(self):
        code = """\
//@version=6
strategy("test", overlay=true)
if barstate.islast
    strategy.cancel_all()
plot(close)
"""
        results = analyze_code(code)
        opt073 = [r for r in results if r.rule_id == "OPT-073"]
        assert len(opt073) == 0

    def test_no_false_positive_single_close_all(self):
        code = """\
//@version=6
strategy("test", overlay=true)
if close < open
    strategy.close_all()
plot(close)
"""
        results = analyze_code(code)
        opt073 = [r for r in results if r.rule_id == "OPT-073"]
        assert len(opt073) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-074: request.security() for lower timeframe data
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT074LowerTfRequestSecurity:
    """OPT-074: request.security() for lower timeframe data."""

    def test_detects_1min_timeframe(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.tickerid, "1", close)
plot(s)
"""
        results = analyze_code(code)
        opt074 = [r for r in results if r.rule_id == "OPT-074"]
        assert len(opt074) >= 1
        assert opt074[0].severity == "high"

    def test_detects_5min_timeframe(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.tickerid, "5", close)
plot(s)
"""
        results = analyze_code(code)
        opt074 = [r for r in results if r.rule_id == "OPT-074"]
        assert len(opt074) >= 1

    def test_detects_15min_timeframe(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.tickerid, "15", close)
plot(s)
"""
        results = analyze_code(code)
        opt074 = [r for r in results if r.rule_id == "OPT-074"]
        assert len(opt074) >= 1

    def test_detects_1S_suffix(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.tickerid, "1S", close)
plot(s)
"""
        results = analyze_code(code)
        opt074 = [r for r in results if r.rule_id == "OPT-074"]
        assert len(opt074) >= 1

    def test_no_false_positive_daily_timeframe(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.tickerid, "D", close)
plot(s)
"""
        results = analyze_code(code)
        opt074 = [r for r in results if r.rule_id == "OPT-074"]
        assert len(opt074) == 0

    def test_no_false_positive_1D_timeframe(self):
        code = """\
//@version=6
indicator("test")
s = request.security(syminfo.tickerid, "1D", close)
plot(s)
"""
        results = analyze_code(code)
        opt074 = [r for r in results if r.rule_id == "OPT-074"]
        assert len(opt074) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-075: Missing const for literal values
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT075MissingConst:
    """OPT-075: Variables assigned literal values without const keyword."""

    def test_detects_int_literal_without_const(self):
        code = """\
//@version=6
indicator("test")
int maxBars = 100
plot(close)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) >= 1

    def test_detects_string_literal_without_const(self):
        code = """\
//@version=6
indicator("test")
string label = "Hello"
plot(close)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) >= 1

    def test_detects_bool_literal_without_const(self):
        code = """\
//@version=6
indicator("test")
bool enabled = true
plot(close)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) >= 1

    def test_detects_float_literal_without_const(self):
        code = """\
//@version=6
indicator("test")
float factor = 2.5
plot(close)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) >= 1

    def test_detects_color_literal_without_const(self):
        code = """\
//@version=6
indicator("test")
color bgCol = color.green
plot(close)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) >= 1

    def test_no_false_positive_with_const_keyword(self):
        code = """\
//@version=6
indicator("test")
const int maxBars = 100
plot(close)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) == 0

    def test_no_false_positive_with_var_keyword(self):
        code = """\
//@version=6
indicator("test")
var int counter = 0
counter += 1
plot(counter)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) == 0

    def test_no_false_positive_in_local_scope(self):
        code = """\
//@version=6
indicator("test")
if close > open
    int maxBars = 100
plot(close)
"""
        results = analyze_code(code)
        opt075 = [r for r in results if r.rule_id == "OPT-075"]
        assert len(opt075) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-076: Unused variable
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT076UnusedVariable:
    """OPT-076: Declared but unreferenced variables."""

    def test_detects_unused_typed_variable(self):
        code = """\
//@version=6
indicator("test")
float myVal = close * 2.0
plot(close)
"""
        results = analyze_code(code)
        opt076 = [r for r in results if r.rule_id == "OPT-076"]
        assert len(opt076) >= 1

    def test_detects_unused_var_declaration(self):
        code = """\
//@version=6
indicator("test")
var float accum = 0.0
plot(close)
"""
        results = analyze_code(code)
        opt076 = [r for r in results if r.rule_id == "OPT-076"]
        assert len(opt076) >= 1

    def test_no_false_positive_used_variable(self):
        code = """\
//@version=6
indicator("test")
float myVal = close * 2.0
plot(myVal)
"""
        results = analyze_code(code)
        opt076 = [r for r in results if r.rule_id == "OPT-076"]
        assert len(opt076) == 0

    def test_no_false_positive_plot_assignment(self):
        code = """\
//@version=6
indicator("test")
p = plot(close, "Price")
plot(close)
"""
        results = analyze_code(code)
        opt076 = [r for r in results if r.rule_id == "OPT-076"]
        assert len(opt076) == 0

    def test_no_false_positive_local_scope_variable(self):
        code = """\
//@version=6
indicator("test")
if close > open
    float inner = high - low
plot(close)
"""
        results = analyze_code(code)
        opt076 = [r for r in results if r.rule_id == "OPT-076"]
        assert len(opt076) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-077: Manual cumulative sum instead of ta.cum()
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT077ManualCum:
    """OPT-077: Manual cumulative sum instead of ta.cum()."""

    def test_detects_manual_cum_with_pluseq(self):
        code = """\
//@version=6
indicator("test")
var float myCum = 0
myCum += close
plot(myCum)
"""
        results = analyze_code(code)
        opt077 = [r for r in results if r.rule_id == "OPT-077"]
        assert len(opt077) >= 1

    def test_detects_manual_cum_with_assign_plus(self):
        code = """\
//@version=6
indicator("test")
var float myCum = 0
myCum := myCum + close
plot(myCum)
"""
        results = analyze_code(code)
        opt077 = [r for r in results if r.rule_id == "OPT-077"]
        assert len(opt077) >= 1

    def test_no_false_positive_var_without_cumulative_update(self):
        code = """\
//@version=6
indicator("test")
var float lastClose = 0
lastClose := close
plot(lastClose)
"""
        results = analyze_code(code)
        opt077 = [r for r in results if r.rule_id == "OPT-077"]
        assert len(opt077) == 0

    def test_no_false_positive_ta_cum(self):
        code = """\
//@version=6
indicator("test")
c = ta.cum(close)
plot(c)
"""
        results = analyze_code(code)
        opt077 = [r for r in results if r.rule_id == "OPT-077"]
        assert len(opt077) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-078: Multiple array.push() instead of array.from()
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT078PushLoopToArrayFrom:
    """OPT-078: Multiple array.push() calls that could use array.from()."""

    def test_detects_three_consecutive_pushes(self):
        code = """\
//@version=6
indicator("test")
a = array.new<float>()
array.push(a, 1.0)
array.push(a, 2.0)
array.push(a, 3.0)
plot(close)
"""
        results = analyze_code(code)
        opt078 = [r for r in results if r.rule_id == "OPT-078"]
        assert len(opt078) >= 1

    def test_detects_many_consecutive_pushes(self):
        code = """\
//@version=6
indicator("test")
a = array.new<float>()
array.push(a, 1.0)
array.push(a, 2.0)
array.push(a, 3.0)
array.push(a, 4.0)
array.push(a, 5.0)
plot(close)
"""
        results = analyze_code(code)
        opt078 = [r for r in results if r.rule_id == "OPT-078"]
        assert len(opt078) >= 1

    def test_no_false_positive_two_pushes(self):
        code = """\
//@version=6
indicator("test")
a = array.new<float>()
array.push(a, 1.0)
array.push(a, 2.0)
plot(close)
"""
        results = analyze_code(code)
        opt078 = [r for r in results if r.rule_id == "OPT-078"]
        assert len(opt078) == 0

    def test_no_false_positive_pushes_to_different_arrays(self):
        code = """\
//@version=6
indicator("test")
a = array.new<float>()
b = array.new<float>()
array.push(a, 1.0)
array.push(b, 2.0)
array.push(a, 3.0)
plot(close)
"""
        results = analyze_code(code)
        opt078 = [r for r in results if r.rule_id == "OPT-078"]
        assert len(opt078) == 0

    def test_detects_pushes_at_end_of_file(self):
        """Pushes at the end of the file (no trailing non-push line) should still be detected."""
        code = """\
//@version=6
indicator("test")
a = array.new<float>()
array.push(a, 1.0)
array.push(a, 2.0)
array.push(a, 3.0)"""
        results = analyze_code(code)
        opt078 = [r for r in results if r.rule_id == "OPT-078"]
        assert len(opt078) >= 1


# ─────────────────────────────────────────────────────────────────────────────
# OPT-079: Manual midpoint (a+b)/2 instead of math.avg()
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT079ManualMidpoint:
    """OPT-079: Manual midpoint (a+b)/2 instead of math.avg()."""

    def test_detects_simple_midpoint(self):
        code = """\
//@version=6
indicator("test")
mid = (high + low) / 2
plot(mid)
"""
        results = analyze_code(code)
        opt079 = [r for r in results if r.rule_id == "OPT-079"]
        assert len(opt079) >= 1

    def test_detects_variable_midpoint(self):
        code = """\
//@version=6
indicator("test")
a = close
b = open
mid = (a + b) / 2
plot(mid)
"""
        results = analyze_code(code)
        opt079 = [r for r in results if r.rule_id == "OPT-079"]
        assert len(opt079) >= 1

    def test_detects_dotted_midpoint(self):
        code = """\
//@version=6
indicator("test")
float s = ta.sma(close, 10)
float e = ta.ema(close, 10)
mid = (s + e) / 2
plot(mid)
"""
        results = analyze_code(code)
        opt079 = [r for r in results if r.rule_id == "OPT-079"]
        assert len(opt079) >= 1

    def test_no_false_positive_math_avg(self):
        code = """\
//@version=6
indicator("test")
mid = math.avg(high, low)
plot(mid)
"""
        results = analyze_code(code)
        opt079 = [r for r in results if r.rule_id == "OPT-079"]
        assert len(opt079) == 0

    def test_no_false_positive_division_by_other_than_2(self):
        code = """\
//@version=6
indicator("test")
val = (high + low) / 3
plot(val)
"""
        results = analyze_code(code)
        opt079 = [r for r in results if r.rule_id == "OPT-079"]
        assert len(opt079) == 0
