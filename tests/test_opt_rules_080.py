"""
test_opt_rules_080.py — Tests for optimization rules OPT-080 through OPT-090.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.optimizer import analyze_code  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# OPT-080: Division by input without runtime.error() guard
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT080DivisionGuard:
    """OPT-080: Division by input without runtime.error() guard."""

    def test_detects_division_by_input(self):
        code = """\
//@version=6
indicator("test")
int length = input.int(10, "Length")
result = close / length
plot(result)
"""
        results = analyze_code(code)
        opt080 = [r for r in results if r.rule_id == "OPT-080"]
        assert len(opt080) >= 1

    def test_detects_division_by_input_in_parens(self):
        code = """\
//@version=6
indicator("test")
float mult = input.float(2.0, "Multiplier")
result = close / (mult * 2)
plot(result)
"""
        results = analyze_code(code)
        opt080 = [r for r in results if r.rule_id == "OPT-080"]
        assert len(opt080) >= 1

    def test_no_false_positive_with_guard(self):
        code = """\
//@version=6
indicator("test")
int length = input.int(10, "Length")
if length == 0
    runtime.error("Length must be > 0")
result = close / length
plot(result)
"""
        results = analyze_code(code)
        opt080 = [r for r in results if r.rule_id == "OPT-080"]
        assert len(opt080) == 0

    def test_no_false_positive_no_input_divisor(self):
        code = """\
//@version=6
indicator("test")
int length = input.int(10, "Length")
result = close / 2.0
plot(result)
"""
        results = analyze_code(code)
        opt080 = [r for r in results if r.rule_id == "OPT-080"]
        assert len(opt080) == 0

    def test_no_false_positive_input_not_in_divisor(self):
        code = """\
//@version=6
indicator("test")
int length = input.int(10, "Length")
result = length * close
plot(result)
"""
        results = analyze_code(code)
        opt080 = [r for r in results if r.rule_id == "OPT-080"]
        assert len(opt080) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-081: Conditional plot() without display parameter
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT081PlotDisplay:
    """OPT-081: Conditional plot() without display parameter."""

    def test_detects_conditional_plot_without_display(self):
        code = """\
//@version=6
indicator("test")
plot(show ? close : na, "Price")
"""
        results = analyze_code(code)
        opt081 = [r for r in results if r.rule_id == "OPT-081"]
        assert len(opt081) >= 1

    def test_detects_conditional_plot_with_ternary(self):
        code = """\
//@version=6
indicator("test")
myColor = input.bool(true, "Show")
plot(myColor ? high : na, "High")
"""
        results = analyze_code(code)
        opt081 = [r for r in results if r.rule_id == "OPT-081"]
        assert len(opt081) >= 1

    def test_no_false_positive_with_display(self):
        code = """\
//@version=6
indicator("test")
plot(show ? close : na, "Price", display = display.data_window)
"""
        results = analyze_code(code)
        opt081 = [r for r in results if r.rule_id == "OPT-081"]
        assert len(opt081) == 0

    def test_no_false_positive_regular_plot(self):
        code = """\
//@version=6
indicator("test")
plot(close, "Price")
"""
        results = analyze_code(code)
        opt081 = [r for r in results if r.rule_id == "OPT-081"]
        assert len(opt081) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-082: request.security() may repaint
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT082RequestSecurityRepainting:
    """OPT-082: request.security() without anti-repainting safeguards."""

    def test_detects_naive_request_security(self):
        code = """\
//@version=6
indicator("test")
htfClose = request.security(syminfo.tickerid, "60", close)
plot(htfClose)
"""
        results = analyze_code(code)
        opt082 = [r for r in results if r.rule_id == "OPT-082"]
        assert len(opt082) >= 1

    def test_no_false_positive_with_lookahead(self):
        code = """\
//@version=6
indicator("test")
htfClose = request.security(syminfo.tickerid, "60", close[1], lookahead = barmerge.lookahead_on)
plot(htfClose)
"""
        results = analyze_code(code)
        opt082 = [r for r in results if r.rule_id == "OPT-082"]
        assert len(opt082) == 0

    def test_no_false_positive_with_offset(self):
        code = """\
//@version=6
indicator("test")
htfClose = request.security(syminfo.tickerid, "60", close[1])
plot(htfClose)
"""
        results = analyze_code(code)
        opt082 = [r for r in results if r.rule_id == "OPT-082"]
        assert len(opt082) == 0

    def test_no_false_positive_in_function(self):
        """request.security() inside a user-defined function (indented) is skipped."""
        code = """\
//@version=6
indicator("test")
getHtfData() =>
    request.security(syminfo.tickerid, "60", close)
htfClose = getHtfData()
plot(htfClose)
"""
        results = analyze_code(code)
        opt082 = [r for r in results if r.rule_id == "OPT-082"]
        assert len(opt082) == 0

    def test_severity_is_high(self):
        code = """\
//@version=6
indicator("test")
htfClose = request.security(syminfo.tickerid, "60", close)
plot(htfClose)
"""
        results = analyze_code(code)
        opt082 = [r for r in results if r.rule_id == "OPT-082"]
        assert len(opt082) >= 1
        assert opt082[0].severity == "high"


# ─────────────────────────────────────────────────────────────────────────────
# OPT-083: request.security() without timeframe validation
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT083RequestNoTfValidation:
    """OPT-083: request.security() without timeframe validation."""

    def test_detects_request_security_without_tf_check(self):
        code = """\
//@version=6
indicator("test")
htfClose = request.security(syminfo.tickerid, "60", close)
plot(htfClose)
"""
        results = analyze_code(code)
        opt083 = [r for r in results if r.rule_id == "OPT-083"]
        assert len(opt083) >= 1

    def test_no_false_positive_with_tf_validation(self):
        code = """\
//@version=6
indicator("test")
if timeframe.in_seconds("60") <= timeframe.in_seconds()
    runtime.error("Requested TF must be higher than chart TF")
htfClose = request.security(syminfo.tickerid, "60", close)
plot(htfClose)
"""
        results = analyze_code(code)
        opt083 = [r for r in results if r.rule_id == "OPT-083"]
        assert len(opt083) == 0

    def test_no_false_positive_no_request_security(self):
        code = """\
//@version=6
indicator("test")
plot(close)
"""
        results = analyze_code(code)
        opt083 = [r for r in results if r.rule_id == "OPT-083"]
        assert len(opt083) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-084: Input-dependent function result recalculated every bar
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT084InputOnlyCalc:
    """OPT-084: Input-dependent function result recalculated every bar."""

    def test_detects_input_only_function_call(self):
        code = """\
//@version=6
indicator("test")
myVal = myCustomFunc(input.int(14, "Length"), input.float(2.0, "Mult"))
plot(myVal)
"""
        results = analyze_code(code)
        opt084 = [r for r in results if r.rule_id == "OPT-084"]
        assert len(opt084) >= 1

    def test_no_false_positive_with_series_dep(self):
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length")
myVal = myCustomFunc(length, close)
plot(myVal)
"""
        results = analyze_code(code)
        opt084 = [r for r in results if r.rule_id == "OPT-084"]
        assert len(opt084) == 0

    def test_no_false_positive_with_var(self):
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length")
var myVal = myCustomFunc(length)
plot(myVal)
"""
        results = analyze_code(code)
        opt084 = [r for r in results if r.rule_id == "OPT-084"]
        assert len(opt084) == 0

    def test_no_false_positive_excluded_prefix(self):
        """Functions with excluded prefixes (ta., math., etc.) are not flagged."""
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length")
myVal = ta.sma(close, length)
plot(myVal)
"""
        results = analyze_code(code)
        opt084 = [r for r in results if r.rule_id == "OPT-084"]
        assert len(opt084) == 0

    def test_no_false_positive_indented(self):
        """Indented (non-global) assignments are not flagged."""
        code = """\
//@version=6
indicator("test")
length = input.int(14, "Length")
if barstate.islast
    myVal = myCustomFunc(length)
plot(close)
"""
        results = analyze_code(code)
        opt084 = [r for r in results if r.rule_id == "OPT-084"]
        assert len(opt084) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-085: table.cell() content updates on every bar
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT085TableCellEveryBar:
    """OPT-085: table.cell() content updates running on every bar."""

    def test_detects_table_cell_with_str_format(self):
        code = """\
//@version=6
indicator("test", overlay = true)
var table myTable = table.new(position.top_right, 2, 2)
table.cell(myTable, 0, 0, str.tostring(close, format.mintick))
"""
        results = analyze_code(code)
        opt085 = [r for r in results if r.rule_id == "OPT-085"]
        assert len(opt085) >= 1

    def test_detects_table_cell_with_str_tostring(self):
        code = """\
//@version=6
indicator("test", overlay = true)
var table myTable = table.new(position.top_right, 2, 2)
table.cell(myTable, 0, 0, str.tostring(close))
"""
        results = analyze_code(code)
        opt085 = [r for r in results if r.rule_id == "OPT-085"]
        assert len(opt085) >= 1

    def test_no_false_positive_with_islast(self):
        code = """\
//@version=6
indicator("test", overlay = true)
var table myTable = table.new(position.top_right, 2, 2)
if barstate.islast
    table.cell(myTable, 0, 0, str.tostring(close))
"""
        results = analyze_code(code)
        opt085 = [r for r in results if r.rule_id == "OPT-085"]
        assert len(opt085) == 0

    def test_no_false_positive_no_formatting(self):
        """table.cell() without str.tostring/str.format/format.mintick is not flagged."""
        code = """\
//@version=6
indicator("test", overlay = true)
var table myTable = table.new(position.top_right, 2, 2)
table.cell(myTable, 0, 0, "Hello", bgcolor = color.green)
"""
        results = analyze_code(code)
        opt085 = [r for r in results if r.rule_id == "OPT-085"]
        assert len(opt085) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-086: chart.visible_bar_time with heavy calculations
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT086VisibleChartHeavyCalc:
    """OPT-086: Using chart.visible_bar_time with heavy calculation design."""

    def test_detects_visible_bar_time_with_for_loop(self):
        code = """\
//@version=6
indicator("test")
leftTime = chart.left_visible_bar_time
for i = 0 to 100
    result := result + i
plot(result)
"""
        results = analyze_code(code)
        opt086 = [r for r in results if r.rule_id == "OPT-086"]
        assert len(opt086) >= 1

    def test_detects_right_visible_bar_with_request(self):
        code = """\
//@version=6
indicator("test")
rightTime = chart.right_visible_bar_time
val = request.security(syminfo.tickerid, "D", close)
plot(val)
"""
        results = analyze_code(code)
        opt086 = [r for r in results if r.rule_id == "OPT-086"]
        assert len(opt086) >= 1

    def test_detects_visible_bar_with_array_ops(self):
        code = """\
//@version=6
indicator("test")
leftTime = chart.left_visible_bar_time
myArr = array.new_float()
array.push(myArr, close)
plot(close)
"""
        results = analyze_code(code)
        opt086 = [r for r in results if r.rule_id == "OPT-086"]
        assert len(opt086) >= 1

    def test_no_false_positive_no_visible_bar(self):
        code = """\
//@version=6
indicator("test")
for i = 0 to 100
    result := result + i
plot(result)
"""
        results = analyze_code(code)
        opt086 = [r for r in results if r.rule_id == "OPT-086"]
        assert len(opt086) == 0

    def test_severity_is_high(self):
        code = """\
//@version=6
indicator("test")
leftTime = chart.left_visible_bar_time
for i = 0 to 100
    result := result + i
plot(result)
"""
        results = analyze_code(code)
        opt086 = [r for r in results if r.rule_id == "OPT-086"]
        assert len(opt086) >= 1
        assert opt086[0].severity == "high"


# ─────────────────────────────────────────────────────────────────────────────
# OPT-087: varip without barstate.isnew reset
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT087VaripNoReset:
    """OPT-087: varip variables without barstate.isnew reset."""

    def test_detects_varip_without_reset(self):
        code = """\
//@version=6
indicator("test")
varip float myVar = 0.0
myVar := myVar + close
plot(myVar)
"""
        results = analyze_code(code)
        opt087 = [r for r in results if r.rule_id == "OPT-087"]
        assert len(opt087) >= 1

    def test_detects_varip_untyped_without_reset(self):
        code = """\
//@version=6
indicator("test")
varip myCounter = 0
myCounter := myCounter + 1
plot(myCounter)
"""
        results = analyze_code(code)
        opt087 = [r for r in results if r.rule_id == "OPT-087"]
        assert len(opt087) >= 1

    def test_no_false_positive_with_isnew_reset(self):
        code = """\
//@version=6
indicator("test")
varip float myVar = 0.0
if barstate.isnew
    myVar := 0
myVar := myVar + close
plot(myVar)
"""
        results = analyze_code(code)
        opt087 = [r for r in results if r.rule_id == "OPT-087"]
        assert len(opt087) == 0

    def test_no_false_positive_no_varip(self):
        code = """\
//@version=6
indicator("test")
var float myVar = 0.0
myVar := myVar + close
plot(myVar)
"""
        results = analyze_code(code)
        opt087 = [r for r in results if r.rule_id == "OPT-087"]
        assert len(opt087) == 0

    def test_severity_is_high(self):
        code = """\
//@version=6
indicator("test")
varip float myVar = 0.0
myVar := myVar + close
plot(myVar)
"""
        results = analyze_code(code)
        opt087 = [r for r in results if r.rule_id == "OPT-087"]
        assert len(opt087) >= 1
        assert opt087[0].severity == "high"


# ─────────────────────────────────────────────────────────────────────────────
# OPT-088: Dynamic-length function needs max_bars_back
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT088DynamicLengthNeedsMbb:
    """OPT-088: Dynamic-length functions needing max_bars_back()."""

    def test_detects_ta_barssince_without_mbb(self):
        code = """\
//@version=6
indicator("test")
sinceLow = ta.barssince(low == ta.lowest(low, 20))
plot(sinceLow)
"""
        results = analyze_code(code)
        opt088 = [r for r in results if r.rule_id == "OPT-088"]
        assert len(opt088) >= 1

    def test_detects_ta_valuewhen_without_mbb(self):
        code = """\
//@version=6
indicator("test")
valWhen = ta.valuewhen(close > open, high, 0)
plot(valWhen)
"""
        results = analyze_code(code)
        opt088 = [r for r in results if r.rule_id == "OPT-088"]
        assert len(opt088) >= 1

    def test_detects_ta_lowestsince_without_mbb(self):
        code = """\
//@version=6
indicator("test")
ls = ta.lowestsince(close > open, low, 1)
plot(ls)
"""
        results = analyze_code(code)
        opt088 = [r for r in results if r.rule_id == "OPT-088"]
        assert len(opt088) >= 1

    def test_detects_ta_highestsince_without_mbb(self):
        code = """\
//@version=6
indicator("test")
hs = ta.highestsince(close > open, high, 1)
plot(hs)
"""
        results = analyze_code(code)
        opt088 = [r for r in results if r.rule_id == "OPT-088"]
        assert len(opt088) >= 1

    def test_no_false_positive_with_mbb_param(self):
        code = """\
//@version=6
indicator("test", max_bars_back=5000)
sinceLow = ta.barssince(low == ta.lowest(low, 20))
plot(sinceLow)
"""
        results = analyze_code(code)
        opt088 = [r for r in results if r.rule_id == "OPT-088"]
        assert len(opt088) == 0

    def test_no_false_positive_with_mbb_function(self):
        code = """\
//@version=6
indicator("test")
sinceLow = ta.barssince(low == ta.lowest(low, 20))
max_bars_back(sinceLow, 5000)
plot(sinceLow)
"""
        results = analyze_code(code)
        opt088 = [r for r in results if r.rule_id == "OPT-088"]
        assert len(opt088) == 0

    def test_severity_is_high(self):
        code = """\
//@version=6
indicator("test")
sinceLow = ta.barssince(low == ta.lowest(low, 20))
plot(sinceLow)
"""
        results = analyze_code(code)
        opt088 = [r for r in results if r.rule_id == "OPT-088"]
        assert len(opt088) >= 1
        assert opt088[0].severity == "high"


# ─────────────────────────────────────────────────────────────────────────────
# OPT-089: String ops at global scope
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT089StringOpsGlobalScope:
    """OPT-089: String operations at global scope without islast/var optimization."""

    def test_detects_str_tostring_at_global_scope(self):
        code = """\
//@version=6
indicator("test", overlay = true)
var table myTable = table.new(position.top_right, 2, 2)
priceStr = str.tostring(close, format.mintick)
table.cell(myTable, 0, 0, priceStr)
"""
        results = analyze_code(code)
        opt089 = [r for r in results if r.rule_id == "OPT-089"]
        assert len(opt089) >= 1

    def test_detects_str_format_at_global_scope(self):
        code = """\
//@version=6
indicator("test", overlay = true)
var label myLabel = label.new(bar_index, high, "")
priceStr = str.format("Price: {0}", close)
label.new(bar_index, high, priceStr)
"""
        results = analyze_code(code)
        opt089 = [r for r in results if r.rule_id == "OPT-089"]
        assert len(opt089) >= 1

    def test_no_false_positive_with_islast(self):
        code = """\
//@version=6
indicator("test", overlay = true)
var table myTable = table.new(position.top_right, 2, 2)
if barstate.islast
    priceStr = str.tostring(close, format.mintick)
    table.cell(myTable, 0, 0, priceStr)
"""
        results = analyze_code(code)
        opt089 = [r for r in results if r.rule_id == "OPT-089"]
        assert len(opt089) == 0

    def test_no_false_positive_no_table_or_label(self):
        code = """\
//@version=6
indicator("test")
priceStr = str.tostring(close, format.mintick)
plot(close)
"""
        results = analyze_code(code)
        opt089 = [r for r in results if r.rule_id == "OPT-089"]
        assert len(opt089) == 0

    def test_no_false_positive_with_var(self):
        code = """\
//@version=6
indicator("test", overlay = true)
var table myTable = table.new(position.top_right, 2, 2)
var prefix = str.tostring(syminfo.ticker)
table.cell(myTable, 0, 0, prefix)
"""
        results = analyze_code(code)
        opt089 = [r for r in results if r.rule_id == "OPT-089"]
        assert len(opt089) == 0


# ─────────────────────────────────────────────────────────────────────────────
# OPT-090: Forward drawing missing xloc.bar_time
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT090ForwardDrawingNoXloc:
    """OPT-090: Forward drawings missing xloc.bar_time."""

    def test_detects_line_new_forward_without_xloc(self):
        code = """\
//@version=6
indicator("test", overlay = true)
line.new(bar_index, high, bar_index + 10, high, color = color.red)
"""
        results = analyze_code(code)
        opt090 = [r for r in results if r.rule_id == "OPT-090"]
        assert len(opt090) >= 1

    def test_detects_box_new_forward_without_xloc(self):
        code = """\
//@version=6
indicator("test", overlay = true)
box.new(bar_index, high, bar_index + 5, low, border_color = color.blue)
"""
        results = analyze_code(code)
        opt090 = [r for r in results if r.rule_id == "OPT-090"]
        assert len(opt090) >= 1

    def test_no_false_positive_with_xloc_bar_time(self):
        code = """\
//@version=6
indicator("test", overlay = true)
line.new(bar_index, high, bar_index + 10, high, xloc = xloc.bar_time, color = color.red)
"""
        results = analyze_code(code)
        opt090 = [r for r in results if r.rule_id == "OPT-090"]
        assert len(opt090) == 0

    def test_no_false_positive_no_forward_offset(self):
        code = """\
//@version=6
indicator("test", overlay = true)
line.new(bar_index, high, bar_index, low, color = color.red)
"""
        results = analyze_code(code)
        opt090 = [r for r in results if r.rule_id == "OPT-090"]
        assert len(opt090) == 0

    def test_no_false_positive_no_drawing(self):
        code = """\
//@version=6
indicator("test")
plot(close)
"""
        results = analyze_code(code)
        opt090 = [r for r in results if r.rule_id == "OPT-090"]
        assert len(opt090) == 0

    def test_severity_is_high(self):
        code = """\
//@version=6
indicator("test", overlay = true)
line.new(bar_index, high, bar_index + 10, high, color = color.red)
"""
        results = analyze_code(code)
        opt090 = [r for r in results if r.rule_id == "OPT-090"]
        assert len(opt090) >= 1
        assert opt090[0].severity == "high"
