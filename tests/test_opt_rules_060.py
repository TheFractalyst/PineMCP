"""
test_opt_rules_060.py — Tests for OPT-060 through OPT-068.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.optimizer import analyze_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# OPT-060: Long if/else chain replaceable with switch
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT060LongIfElse:
    """OPT-060: Long if/else chain replaceable with switch."""

    def test_detects_long_chain(self):
        code = """\
//@version=6
indicator("test")
mode = input.int(1, "Mode")
if mode == 1
    x = close
else if mode == 2
    x = open
else if mode == 3
    x = high
else if mode == 4
    x = low
else if mode == 5
    x = hl2
plot(x)
"""
        results = analyze_code(code)
        opt060 = [r for r in results if r.rule_id == "OPT-060"]
        assert len(opt060) >= 1

    def test_detects_chain_with_string_literals(self):
        code = """\
//@version=6
indicator("test")
mode = input.string("a", "Mode")
if mode == "a"
    x = close
else if mode == "b"
    x = open
else if mode == "c"
    x = high
else if mode == "d"
    x = low
else if mode == "e"
    x = hl2
else if mode == "f"
    x = hlc3
plot(x)
"""
        results = analyze_code(code)
        opt060 = [r for r in results if r.rule_id == "OPT-060"]
        assert len(opt060) >= 1

    def test_no_false_positive_short_chain(self):
        code = """\
//@version=6
indicator("test")
if close > open
    x = 1
else if close < open
    x = -1
else
    x = 0
plot(x)
"""
        results = analyze_code(code)
        opt060 = [r for r in results if r.rule_id == "OPT-060"]
        assert len(opt060) == 0

    def test_no_false_positive_four_branches(self):
        code = """\
//@version=6
indicator("test")
mode = input.int(1, "Mode")
if mode == 1
    x = close
else if mode == 2
    x = open
else if mode == 3
    x = high
else
    x = low
plot(x)
"""
        results = analyze_code(code)
        opt060 = [r for r in results if r.rule_id == "OPT-060"]
        assert len(opt060) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-061: Dead user-defined function
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT061DeadFunction:
    """OPT-061: User-defined function declared but never called."""

    def test_detects_dead_arrow_function(self):
        code = """\
//@version=6
indicator("test")
myFunc(x, y) => x + y
plot(close)
"""
        results = analyze_code(code)
        opt061 = [r for r in results if r.rule_id == "OPT-061"]
        assert len(opt061) >= 1

    def test_detects_dead_multiline_arrow_function(self):
        code = """\
//@version=6
indicator("test")
myUnused(x) =>
    a = x * 2
    a + 1
plot(close)
"""
        results = analyze_code(code)
        opt061 = [r for r in results if r.rule_id == "OPT-061"]
        assert len(opt061) >= 1

    def test_no_false_positive_used_function(self):
        code = """\
//@version=6
indicator("test")
double(x) => x * 2
plot(double(close))
"""
        results = analyze_code(code)
        opt061 = [r for r in results if r.rule_id == "OPT-061"]
        assert len(opt061) == 0

    def test_no_false_positive_keyword_like_name(self):
        """Function names that are keywords should not be flagged."""
        code = """\
//@version=6
indicator("test")
var(x) => x
plot(close)
"""
        results = analyze_code(code)
        opt061 = [r for r in results if r.rule_id == "OPT-061"]
        assert len(opt061) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-062: String concatenation in loop
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT062StringConcatInLoop:
    """OPT-062: String concatenation (+=) inside loop."""

    def test_detects_string_concat_in_for_loop(self):
        code = """\
//@version=6
indicator("test")
s = ""
for i = 0 to 10
    s += "item"
plot(close)
"""
        results = analyze_code(code)
        opt062 = [r for r in results if r.rule_id == "OPT-062"]
        assert len(opt062) >= 1

    def test_detects_string_concat_single_quotes(self):
        code = """\
//@version=6
indicator("test")
s = ""
for i = 0 to 5
    s += 'x'
plot(close)
"""
        results = analyze_code(code)
        opt062 = [r for r in results if r.rule_id == "OPT-062"]
        assert len(opt062) >= 1

    def test_no_false_positive_numeric_concat(self):
        code = """\
//@version=6
indicator("test")
sum = 0.0
for i = 0 to 10
    sum += i
plot(sum)
"""
        results = analyze_code(code)
        opt062 = [r for r in results if r.rule_id == "OPT-062"]
        assert len(opt062) == 0

    def test_no_false_positive_concat_outside_loop(self):
        code = """\
//@version=6
indicator("test")
s = "hello"
s += " world"
plot(close)
"""
        results = analyze_code(code)
        opt062 = [r for r in results if r.rule_id == "OPT-062"]
        assert len(opt062) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-063: str.tostring()/str.format() in loop body
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT063FormattingInLoop:
    """OPT-063: str.tostring()/str.format() inside loop body."""

    def test_detects_str_tostring_in_for_loop(self):
        code = """\
//@version=6
indicator("test")
for i = 0 to 10
    s = str.tostring(i)
plot(close)
"""
        results = analyze_code(code)
        opt063 = [r for r in results if r.rule_id == "OPT-063"]
        assert len(opt063) >= 1

    def test_detects_str_format_in_for_loop(self):
        code = """\
//@version=6
indicator("test")
for i = 0 to 10
    s = str.format("{0}", i)
plot(close)
"""
        results = analyze_code(code)
        opt063 = [r for r in results if r.rule_id == "OPT-063"]
        assert len(opt063) >= 1

    def test_detects_str_tostring_in_while_loop(self):
        code = """\
//@version=6
indicator("test")
i = 0
while i < 10
    s = str.tostring(i)
    i += 1
plot(close)
"""
        results = analyze_code(code)
        opt063 = [r for r in results if r.rule_id == "OPT-063"]
        assert len(opt063) >= 1

    def test_no_false_positive_formatting_outside_loop(self):
        code = """\
//@version=6
indicator("test")
s = str.tostring(close)
plot(close)
"""
        results = analyze_code(code)
        opt063 = [r for r in results if r.rule_id == "OPT-063"]
        assert len(opt063) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-064: array.insert(arr, 0, val) O(n) prepend
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT064ArrayPrepend:
    """OPT-064: array.insert(arr, 0, val) — O(n) prepend operation."""

    def test_detects_array_insert_zero(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
array.insert(arr, 0, close)
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt064 = [r for r in results if r.rule_id == "OPT-064"]
        assert len(opt064) >= 1

    def test_detects_array_insert_zero_with_spaces(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
array.insert( arr , 0 , close )
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt064 = [r for r in results if r.rule_id == "OPT-064"]
        assert len(opt064) >= 1

    def test_no_false_positive_insert_at_end(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
array.insert(arr, array.size(arr), close)
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt064 = [r for r in results if r.rule_id == "OPT-064"]
        assert len(opt064) == 0

    def test_no_false_positive_insert_at_nonzero(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
array.insert(arr, 3, close)
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt064 = [r for r in results if r.rule_id == "OPT-064"]
        assert len(opt064) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-065: plot() with display=display.none
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT065DeadPlot:
    """OPT-065: plot() with display=display.none wastes a plot count slot."""

    def test_detects_display_none(self):
        code = """\
//@version=6
indicator("test")
plot(close, display=display.none)
"""
        results = analyze_code(code)
        opt065 = [r for r in results if r.rule_id == "OPT-065"]
        assert len(opt065) >= 1

    def test_detects_display_none_with_other_args(self):
        code = """\
//@version=6
indicator("test")
plot(close, title="hidden", color=color.red, display=display.none)
"""
        results = analyze_code(code)
        opt065 = [r for r in results if r.rule_id == "OPT-065"]
        assert len(opt065) >= 1

    def test_no_false_positive_normal_plot(self):
        code = """\
//@version=6
indicator("test")
plot(close, title="Close")
"""
        results = analyze_code(code)
        opt065 = [r for r in results if r.rule_id == "OPT-065"]
        assert len(opt065) == 0

    def test_no_false_positive_display_data(self):
        code = """\
//@version=6
indicator("test")
plot(close, display=display.data)
"""
        results = analyze_code(code)
        opt065 = [r for r in results if r.rule_id == "OPT-065"]
        assert len(opt065) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-066: color.new() recomputed every bar
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT066ColorNewEveryBar:
    """OPT-066: color.new() recomputed every bar instead of pre-computed."""

    def test_detects_color_new_global_scope(self):
        code = """\
//@version=6
indicator("test")
myColor = color.new(color.red, 80)
plot(close, color=myColor)
"""
        results = analyze_code(code)
        opt066 = [r for r in results if r.rule_id == "OPT-066"]
        assert len(opt066) >= 1

    def test_no_false_positive_with_var(self):
        code = """\
//@version=6
indicator("test")
var color myColor = color.new(color.red, 80)
plot(close, color=myColor)
"""
        results = analyze_code(code)
        opt066 = [r for r in results if r.rule_id == "OPT-066"]
        assert len(opt066) == 0

    def test_no_false_positive_with_varip(self):
        code = """\
//@version=6
indicator("test")
varip color myColor = color.new(color.red, 80)
plot(close, color=myColor)
"""
        results = analyze_code(code)
        opt066 = [r for r in results if r.rule_id == "OPT-066"]
        assert len(opt066) == 0

    def test_no_false_positive_in_local_scope(self):
        code = """\
//@version=6
indicator("test")
if barstate.islast
    myColor = color.new(color.red, 80)
    plot(close, color=myColor)
"""
        results = analyze_code(code)
        opt066 = [r for r in results if r.rule_id == "OPT-066"]
        assert len(opt066) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-067: Array push in fixed-bounds loop
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT067FixedSizePush:
    """OPT-067: array.push() inside loop with fixed/known bounds."""

    def test_detects_push_in_fixed_loop(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
for i = 0 to 99
    array.push(arr, i * 1.0)
plot(array.size(arr))
"""
        results = analyze_code(code)
        opt067 = [r for r in results if r.rule_id == "OPT-067"]
        assert len(opt067) >= 1

    def test_detects_push_in_nonzero_start_loop(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
for i = 1 to 50
    array.push(arr, close[i])
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt067 = [r for r in results if r.rule_id == "OPT-067"]
        assert len(opt067) >= 1

    def test_no_false_positive_small_loop(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
for i = 0 to 2
    array.push(arr, close[i])
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt067 = [r for r in results if r.rule_id == "OPT-067"]
        assert len(opt067) == 0

    def test_no_false_positive_no_push(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>(100, 0.0)
for i = 0 to 99
    array.set(arr, i, close[i])
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt067 = [r for r in results if r.rule_id == "OPT-067"]
        assert len(opt067) == 0

    def test_no_false_positive_variable_bounds(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
len = input.int(20)
for i = 0 to len
    array.push(arr, close[i])
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt067 = [r for r in results if r.rule_id == "OPT-067"]
        assert len(opt067) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-068: Unnecessary var for always-overwritten variable
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT068UnnecessaryVar:
    """OPT-068: var declared but unconditionally overwritten every bar."""

    def test_detects_var_then_reassign(self):
        code = """\
//@version=6
indicator("test")
var float myVal = 0.0
myVal := close
plot(myVal)
"""
        results = analyze_code(code)
        opt068 = [r for r in results if r.rule_id == "OPT-068"]
        assert len(opt068) >= 1

    def test_detects_var_int_reassign(self):
        code = """\
//@version=6
indicator("test")
var int counter = 0
counter := 5
plot(counter)
"""
        results = analyze_code(code)
        opt068 = [r for r in results if r.rule_id == "OPT-068"]
        assert len(opt068) >= 1

    def test_no_false_positive_var_without_reassign(self):
        code = """\
//@version=6
indicator("test")
var float accum = 0.0
accum += close
plot(accum)
"""
        results = analyze_code(code)
        opt068 = [r for r in results if r.rule_id == "OPT-068"]
        assert len(opt068) == 0

    def test_no_false_positive_normal_declaration(self):
        code = """\
//@version=6
indicator("test")
float myVal = close
plot(myVal)
"""
        results = analyze_code(code)
        opt068 = [r for r in results if r.rule_id == "OPT-068"]
        assert len(opt068) == 0

    def test_no_false_positive_var_bool_reassign_in_local_scope(self):
        """var that's only reassigned inside if block should NOT be flagged."""
        code = """\
//@version=6
indicator("test")
var bool triggered = false
if close > open
    triggered := true
plot(close)
"""
        results = analyze_code(code)
        opt068 = [r for r in results if r.rule_id == "OPT-068"]
        assert len(opt068) == 0
