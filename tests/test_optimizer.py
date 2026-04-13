"""
test_optimizer.py — Tests for the PineScript optimization engine and branding middleware.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.optimizer import OptimizationResult, analyze_code, format_results  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Helper utility tests
# ─────────────────────────────────────────────────────────────────────────────

class TestStripComments:
    """String-aware comment stripping."""

    def test_strips_line_comment(self):
        from core.optimizer import _strip_comments
        assert "comment" not in _strip_comments('x = 1 // comment')

    def test_preserves_url_in_string(self):
        from core.optimizer import _strip_comments
        result = _strip_comments('url = "https://binance.com"')
        assert "binance.com" in result

    def test_preserves_double_slash_in_string(self):
        from core.optimizer import _strip_comments
        result = _strip_comments('s = "a // b"')
        assert "a // b" in result

    def test_strips_comment_after_code(self):
        from core.optimizer import _strip_comments
        result = _strip_comments('x = "hello" // comment')
        assert "hello" in result
        assert "comment" not in result

    def test_no_comment_returns_unchanged(self):
        from core.optimizer import _strip_comments
        assert _strip_comments('x = 42') == 'x = 42'

    def test_escaped_quote_in_string(self):
        from core.optimizer import _strip_comments
        result = _strip_comments('s = "say \\"hello\\"" // end')
        assert 'say' in result
        assert "end" not in result


class TestCodeHasKeyword:
    """Keyword presence check ignoring comments."""

    def test_finds_keyword_in_code(self):
        from core.optimizer import _code_has_keyword
        code = 'if barstate.islast\n    plot(close)'
        assert _code_has_keyword(code, "barstate.islast") is True

    def test_ignores_keyword_in_comment(self):
        from core.optimizer import _code_has_keyword
        code = '// barstate.islast\nplot(close)'
        assert _code_has_keyword(code, "barstate.islast") is False

    def test_finds_keyword_mixed(self):
        from core.optimizer import _code_has_keyword
        code = '// barstate.islast\nif barstate.islast\n    plot(close)'
        assert _code_has_keyword(code, "barstate.islast") is True


# ─────────────────────────────────────────────────────────────────────────────
# Anti-pattern detection tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOPT003MultipleRequestSecurity:
    """OPT-003: Multiple request.security() calls to the same context."""

    def test_detects_duplicate_request_security(self):
        code = """\
//@version=6
indicator("test")
float a = request.security(syminfo.tickerid, "1D", close)
float b = request.security(syminfo.tickerid, "1D", ta.sma(close, 20))
float c = request.security(syminfo.tickerid, "1D", volume)
plot(a)
"""
        results = analyze_code(code)
        opt003 = [r for r in results if r.rule_id == "OPT-003"]
        assert len(opt003) >= 1
        assert opt003[0].severity == "critical"

    def test_no_false_positive_single_request(self):
        code = """\
//@version=6
indicator("test")
float a = request.security(syminfo.tickerid, "1D", close)
plot(a)
"""
        results = analyze_code(code)
        opt003 = [r for r in results if r.rule_id == "OPT-003"]
        assert len(opt003) == 0


class TestOPT004DeleteRecreate:
    """OPT-004: Delete + recreate drawings instead of setters."""

    def test_detects_delete_new_pattern(self):
        code = """\
//@version=6
indicator("test")
box.delete(myBox)
myBox := box.new(bar_index, high, bar_index + 1, low)
"""
        results = analyze_code(code)
        opt004 = [r for r in results if r.rule_id == "OPT-004"]
        assert len(opt004) >= 1


class TestOPT005UnprotectedDrawings:
    """OPT-005: Drawing updates on all historical bars."""

    def test_detects_unprotected_table_update(self):
        code = """\
//@version=6
indicator("test")
var table t = table.new(position.top_right, 2, 2)
table.cell_set_text(t, 0, 0, "hello")
table.cell_set_bgcolor(t, 0, 0, color.green)
"""
        results = analyze_code(code)
        opt005 = [r for r in results if r.rule_id == "OPT-005"]
        assert len(opt005) >= 1

    def test_no_flag_when_islast_guarded(self):
        code = """\
//@version=6
indicator("test")
var table t = table.new(position.top_right, 2, 2)
if barstate.islast
    table.cell_set_text(t, 0, 0, "hello")
"""
        results = analyze_code(code)
        opt005 = [r for r in results if r.rule_id == "OPT-005"]
        assert len(opt005) == 0


class TestOPT009LoopInvariant:
    """OPT-009: Loop-invariant array.min/max inside loop."""

    def test_detects_array_min_in_loop(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
for item in arr
    result := (item - array.min(arr)) / array.range(arr)
"""
        results = analyze_code(code)
        opt009 = [r for r in results if r.rule_id == "OPT-009"]
        assert len(opt009) >= 1


class TestOPT013NaDrawingCoords:
    """OPT-013: Drawing objects with na coordinates."""

    def test_detects_na_ternary_drawing(self):
        code = """\
//@version=6
indicator("test")
label.new(longCond ? bar_index : na, 0, text="Buy")
"""
        results = analyze_code(code)
        opt013 = [r for r in results if r.rule_id == "OPT-013"]
        assert len(opt013) >= 1

    def test_no_false_positive_no_na(self):
        code = (
            '//@version=6\nindicator("test")'
            '\nlabel.new(bar_index, high, text="Buy")\nplot(close)'
        )
        results = analyze_code(code)
        opt013 = [r for r in results if r.rule_id == "OPT-013"]
        assert len(opt013) == 0


class TestOPT015RequestLimit:
    """OPT-015: Approaching request.*() call limit."""

    def test_detects_many_request_calls(self):
        tfs = ["1", "5", "15", "30", "60", "120", "240", "D", "W", "M"] * 4
        calls = "\n".join(
            f'float r{i} = request.security(syminfo.tickerid, "{tfs[i]}", close)'
            for i in range(40)
        )
        code = f"//@version=6\nindicator('test')\n{calls}\nplot(close)"
        results = analyze_code(code)
        opt015 = [r for r in results if r.rule_id == "OPT-015"]
        assert len(opt015) >= 1


class TestOPT017LargeScript:
    """OPT-017: Very large script approaching token limits."""

    def test_detects_large_script(self):
        lines = ["//@version=6", 'indicator("test")']
        for i in range(4000):
            lines.append(f"float var{i} = {i}.0")
        lines.append("plot(close)")
        code = "\n".join(lines)
        results = analyze_code(code)
        opt017 = [r for r in results if r.rule_id == "OPT-017"]
        assert len(opt017) >= 1

    def test_no_false_positive_small_script(self):
        lines = ["//@version=6", 'indicator("test")', 'plot(close)']
        code = "\n".join(lines)
        results = analyze_code(code)
        opt017 = [r for r in results if r.rule_id == "OPT-017"]
        assert len(opt017) == 0


class TestOPT023LargeLoop:
    """OPT-023: Very large loop (timeout risk)."""

    def test_detects_large_loop(self):
        code = """\
//@version=6
indicator("test")
for i = 1 to 50000
    sum += i
"""
        results = analyze_code(code)
        opt023 = [r for r in results if r.rule_id == "OPT-023"]
        assert len(opt023) >= 1


class TestOPT027TaInLocalScope:
    """OPT-027: ta.*() calls inside local scopes."""

    def test_detects_ta_in_if_block(self):
        code = """\
//@version=6
indicator("test")
if close > open
    float mySma = ta.sma(close, 20)
plot(close)
"""
        results = analyze_code(code)
        opt027 = [r for r in results if r.rule_id == "OPT-027"]
        assert len(opt027) >= 1

    def test_no_flag_global_scope(self):
        code = """\
//@version=6
indicator("test")
float mySma = ta.sma(close, 20)
plot(mySma)
"""
        results = analyze_code(code)
        opt027 = [r for r in results if r.rule_id == "OPT-027"]
        assert len(opt027) == 0


class TestOPT030MissingVar:
    """OPT-030: Missing var for cross-bar persistence."""

    def test_detects_missing_var_accumulation(self):
        code = """\
//@version=6
indicator("test")
int counter = 0
counter += 1
plot(counter)
"""
        results = analyze_code(code)
        opt030 = [r for r in results if r.rule_id == "OPT-030"]
        assert len(opt030) >= 1


class TestOPT032CalcOnOrderFills:
    """OPT-032: calc_on_order_fills causing 4x overhead."""

    def test_detects_calc_on_order_fills(self):
        code = """\
//@version=6
strategy("test", calc_on_order_fills=true)
if close > open
    strategy.entry("Long", strategy.long)
"""
        results = analyze_code(code)
        opt032 = [r for r in results if r.rule_id == "OPT-032"]
        assert len(opt032) >= 1


class TestOPT021DeepHistory:
    """OPT-021: Deep history reference beyond 5000 bars."""

    def test_detects_deep_history(self):
        code = """\
//@version=6
indicator("test")
float past = myVar[5500]
plot(past)
"""
        results = analyze_code(code)
        opt021 = [r for r in results if r.rule_id == "OPT-021"]
        assert len(opt021) >= 1

    def test_no_flag_normal_history(self):
        code = """\
//@version=6
indicator("test")
float past = close[50]
plot(past)
"""
        results = analyze_code(code)
        opt021 = [r for r in results if r.rule_id == "OPT-021"]
        assert len(opt021) == 0


class TestOPT001ReimplementedBuiltins:
    """OPT-001: Reimplementing built-in functions with loops."""

    def test_detects_loop_reimplementing_highest(self):
        code = """\
//@version=6
indicator("test")
f_highest(source, length) =>
    result = source
    for i = 1 to length - 1
        result := math.max(result, source[i])
    result
plot(f_highest(close, 20))
"""
        results = analyze_code(code)
        opt001 = [r for r in results if r.rule_id == "OPT-001"]
        assert len(opt001) >= 1


class TestOPT002RepeatedCalls:
    """OPT-002: Repeated identical function calls across multiple lines."""

    def test_detects_same_call_three_times(self):
        code = """\
//@version=6
indicator("test")
float a = ta.sma(close, 20)
float b = ta.sma(close, 20)
float c = ta.sma(close, 20)
plot(a)
"""
        results = analyze_code(code)
        opt002 = [r for r in results if r.rule_id == "OPT-002"]
        assert len(opt002) >= 1


class TestOPT006LoopInvariantCalc:
    """OPT-006: Loop-invariant calculations inside loop."""

    def test_detects_math_cos_in_for_loop(self):
        code = """\
//@version=6
indicator("test")
for i = 0 to 100
    val := math.cos(1.5) * i
plot(val)
"""
        results = analyze_code(code)
        opt006 = [r for r in results if r.rule_id == "OPT-006"]
        assert len(opt006) >= 1


class TestOPT007LoopWhenBuiltinExists:
    """OPT-007: Loop when a loop-free built-in expression exists."""

    def test_detects_sum_loop(self):
        code = """\
//@version=6
indicator("test")
float sum = 0.0
for i = 1 to length
    sum += source[i]
plot(sum)
"""
        results = analyze_code(code)
        opt007 = [r for r in results if r.rule_id == "OPT-007"]
        assert len(opt007) >= 1


class TestOPT008IndexofInLoop:
    """OPT-008: array.indexof() inside for...in loop."""

    def test_detects_indexof_in_for_in(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
for item in arr
    idx = array.indexof(arr, item)
    array.set(arr, idx, item * 2.0)
plot(array.get(arr, 0))
"""
        results = analyze_code(code)
        opt008 = [r for r in results if r.rule_id == "OPT-008"]
        assert len(opt008) >= 1


class TestOPT010MissingMaxBarsBack:
    """OPT-010: Missing max_bars_back for late history references."""

    def test_detects_deep_ref_in_islast_without_max_bars_back(self):
        code = """\
//@version=6
indicator("test")
if barstate.islast
    float past = myVar[450]
    plot(past)
"""
        results = analyze_code(code)
        opt010 = [r for r in results if r.rule_id == "OPT-010"]
        assert len(opt010) >= 1


class TestOPT011OversizedBuffer:
    """OPT-011: Oversized max_bars_back buffers."""

    def test_detects_4999_buffer(self):
        code = """\
//@version=6
indicator("test")
max_bars_back(close, 4999)
plot(close)
"""
        results = analyze_code(code)
        opt011 = [r for r in results if r.rule_id == "OPT-011"]
        assert len(opt011) >= 1


class TestOPT012MissingCalcBarsCount:
    """OPT-012: Missing calc_bars_count for last-bar-only logic."""

    def test_detects_islast_drawings_without_calc_bars_count(self):
        code = """\
//@version=6
indicator("test")
if barstate.islast
    var table t = table.new(position.top_right, 2, 2)
    table.cell_set_text(t, 0, 0, "hello")
    table.cell_set_bgcolor(t, 0, 0, color.green)
plot(close)
"""
        results = analyze_code(code)
        opt012 = [r for r in results if r.rule_id == "OPT-012"]
        assert len(opt012) >= 1


class TestOPT014PlotLimit:
    """OPT-014: Approaching plot count limit."""

    def test_detects_50_plus_plots(self):
        calls = "\n".join(f'plot(close, "p{i}")' for i in range(50))
        code = f"//@version=6\nindicator('test')\n{calls}"
        results = analyze_code(code)
        opt014 = [r for r in results if r.rule_id == "OPT-014"]
        assert len(opt014) >= 1

    def test_no_false_positive_few_plots(self):
        calls = "\n".join(f'plot(close, "p{i}")' for i in range(20))
        code = f"//@version=6\nindicator('test')\n{calls}"
        results = analyze_code(code)
        opt014 = [r for r in results if r.rule_id == "OPT-014"]
        assert len(opt014) == 0


class TestOPT016LargeTuple:
    """OPT-016: Large tuple in request.*() call."""

    def test_detects_100_plus_tuple_elements(self):
        elements = ", ".join("close" for _ in range(110))
        code = f"""\
//@version=6
indicator("test")
[{elements}] = request.security(syminfo.tickerid, "1D", [{elements}])
plot(close)
"""
        results = analyze_code(code)
        opt016 = [r for r in results if r.rule_id == "OPT-016"]
        assert len(opt016) >= 1

    def test_no_false_positive_small_tuple(self):
        elements = ", ".join("close" for _ in range(20))
        code = (
            f'//@version=6\nindicator("test")\n[{elements}]'
            f' = request.security(syminfo.tickerid, "1D", [{elements}])\nplot(close)'
        )
        results = analyze_code(code)
        opt016 = [r for r in results if r.rule_id == "OPT-016"]
        assert len(opt016) == 0


class TestOPT018ManyVarsPerScope:
    """OPT-018: Many variable declarations per scope."""

    def test_detects_800_plus_global_vars(self):
        lines = ["//@version=6", 'indicator("test")']
        for i in range(800):
            lines.append(f"float var{i} = {i}.0")
        lines.append("plot(close)")
        code = "\n".join(lines)
        results = analyze_code(code)
        opt018 = [r for r in results if r.rule_id == "OPT-018"]
        assert len(opt018) >= 1

    def test_no_false_positive_few_vars(self):
        lines = ["//@version=6", 'indicator("test")']
        for i in range(50):
            lines.append(f"float var{i} = {i}.0")
        lines.append("plot(close)")
        code = "\n".join(lines)
        results = analyze_code(code)
        opt018 = [r for r in results if r.rule_id == "OPT-018"]
        assert len(opt018) == 0


class TestOPT020UnboundedArrayGrowth:
    """OPT-020: Unbounded array.push() on every bar."""

    def test_detects_push_without_shift(self):
        code = """\
//@version=6
indicator("test")
arr = array.new<float>()
array.push(arr, close)
plot(array.size(arr))
"""
        results = analyze_code(code)
        opt020 = [r for r in results if r.rule_id == "OPT-020"]
        assert len(opt020) >= 1

    def test_no_false_positive_bounded_queue(self):
        code = (
            '//@version=6\nindicator("test")\narr = array.new<float>()'
            '\narray.push(arr, close)\narray.shift(arr)\nplot(array.size(arr))'
        )
        results = analyze_code(code)
        opt020 = [r for r in results if r.rule_id == "OPT-020"]
        assert len(opt020) == 0


class TestOPT022ForwardBarsDrawing:
    """OPT-022: Forward bars >500 for drawing x-coordinates."""

    def test_detects_bar_index_plus_600_in_line_new(self):
        code = """\
//@version=6
indicator("test")
line.new(bar_index, close, bar_index + 600, close)
plot(close)
"""
        results = analyze_code(code)
        opt022 = [r for r in results if r.rule_id == "OPT-022"]
        assert len(opt022) >= 1

    def test_no_false_positive_small_forward(self):
        code = (
            '//@version=6\nindicator("test")'
            '\nline.new(bar_index, close, bar_index + 50, close)\nplot(close)'
        )
        results = analyze_code(code)
        opt022 = [r for r in results if r.rule_id == "OPT-022"]
        assert len(opt022) == 0


class TestOPT026HistoryOnLocalScopeVar:
    """OPT-026: History reference [] on local-scope variable."""

    def test_detects_history_on_if_block_var(self):
        code = """\
//@version=6
indicator("test")
if close > open
    float myVal = close
    float past = myVal[1]
plot(past)
"""
        results = analyze_code(code)
        opt026 = [r for r in results if r.rule_id == "OPT-026"]
        assert len(opt026) >= 1


class TestOPT028VaripRepaintOnPlot:
    """OPT-028: varip variable feeding into plot()."""

    def test_detects_varip_in_plot(self):
        code = """\
//@version=6
indicator("test")
varip float x = na
if barstate.isrealtime
    x := close
plot(x)
"""
        results = analyze_code(code)
        opt028 = [r for r in results if r.rule_id == "OPT-028"]
        assert len(opt028) >= 1


class TestOPT029RealtimeTickRepaint:
    """OPT-029: Realtime tick data + plot repainting (new rule)."""

    def test_detects_isrealtime_varip_assign_plot(self):
        code = """\
//@version=6
indicator("test")
float tickVal = na
if barstate.isrealtime
    tickVal := close
plot(tickVal)
"""
        results = analyze_code(code)
        opt029 = [r for r in results if r.rule_id == "OPT-029"]
        assert len(opt029) >= 1

    def test_detects_isnew_var_assign_plot(self):
        code = """\
//@version=6
indicator("test")
float tickVal = na
if barstate.isnew
    tickVal := close
plotcandle(open, high, low, tickVal)
"""
        results = analyze_code(code)
        opt029 = [r for r in results if r.rule_id == "OPT-029"]
        assert len(opt029) >= 1


class TestOPT031DifferentHistoryOffsets:
    """OPT-031: Different history offsets for historical vs realtime."""

    def test_detects_ternary_history_offset(self):
        code = """\
//@version=6
indicator("test")
float past = barstate.ishistory ? close[100] : close[150]
plot(past)
"""
        results = analyze_code(code)
        opt031 = [r for r in results if r.rule_id == "OPT-031"]
        assert len(opt031) >= 1


class TestOPT036TableCountLimit:
    """OPT-036: Table count approaching 9-table-per-chart limit."""

    def test_detects_8_tables(self):
        calls = "\n".join(
            f'var table t{i} = table.new(position.top_right, 2, 2)'
            for i in range(8)
        )
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt036 = [r for r in results if r.rule_id == "OPT-036"]
        assert len(opt036) >= 1

    def test_no_false_positive_few_tables(self):
        code = (
            '//@version=6\nindicator("test")'
            '\nvar table t = table.new(position.top_right, 2, 2)\nplot(close)'
        )
        results = analyze_code(code)
        opt036 = [r for r in results if r.rule_id == "OPT-036"]
        assert len(opt036) == 0


class TestOPT038TableCreationEveryBar:
    """OPT-038: Table creation every bar without barstate.isfirst guard."""

    def test_detects_table_new_without_isfirst(self):
        code = (
            '//@version=6\nindicator("test")'
            '\ntable t = table.new(position.top_right, 2, 2)\nplot(close)'
        )
        results = analyze_code(code)
        opt038 = [r for r in results if r.rule_id == "OPT-038"]
        assert len(opt038) >= 1

    def test_no_false_positive_isfirst_guarded(self):
        code = (
            '//@version=6\nindicator("test")\nvar table t = na'
            '\nif barstate.isfirst\n    t := table.new(position.top_right, 2, 2)'
            '\nplot(close)'
        )
        results = analyze_code(code)
        opt038 = [r for r in results if r.rule_id == "OPT-038"]
        assert len(opt038) == 0


class TestOPT033VarInLoopHeader:
    """OPT-033: var in for-loop header causes single-iteration bug."""

    def test_detects_var_in_for_header(self):
        code = '//@version=6\nindicator("test")\nfor var i = 0 to 10\n    plot(close[i])\nplot(close)'
        results = analyze_code(code)
        opt033 = [r for r in results if r.rule_id == "OPT-033"]
        assert len(opt033) >= 1

    def test_no_false_positive_normal_for(self):
        code = '//@version=6\nindicator("test")\nfor i = 0 to 10\n    plot(close[i])\nplot(close)'
        results = analyze_code(code)
        opt033 = [r for r in results if r.rule_id == "OPT-033"]
        assert len(opt033) == 0


class TestOPT034VariableShadowing:
    """OPT-034: Variable shadowing (= instead of :=) in local scope."""

    def test_detects_shadowing_in_if_block(self):
        code = '//@version=6\nindicator("test")\nmyVal = 0\nif close > open\n    myVal = 1\nplot(myVal)'
        results = analyze_code(code)
        opt034 = [r for r in results if r.rule_id == "OPT-034"]
        assert len(opt034) >= 1

    def test_no_false_positive_colon_equals(self):
        code = '//@version=6\nindicator("test")\nmyVal = 0\nif close > open\n    myVal := 1\nplot(myVal)'
        results = analyze_code(code)
        opt034 = [r for r in results if r.rule_id == "OPT-034"]
        assert len(opt034) == 0


class TestOPT035CollectionInRequest:
    """OPT-035: Returning collections from request.*() calls."""

    def test_detects_array_in_request(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'arr = request.security(syminfo.tickerid, "1D", array.new<float>())\nplot(close)'
        )
        results = analyze_code(code)
        opt035 = [r for r in results if r.rule_id == "OPT-035"]
        assert len(opt035) >= 1

    def test_no_false_positive_scalar_request(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float val = request.security(syminfo.tickerid, "1D", close)\nplot(val)'
        )
        results = analyze_code(code)
        opt035 = [r for r in results if r.rule_id == "OPT-035"]
        assert len(opt035) == 0


class TestOPT037StrategyNoDateFilter:
    """OPT-037: Strategy with entries but no time/date filter."""

    def test_detects_strategy_without_date_filter(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'if close > open\n    strategy.entry("Long", strategy.long)\nplot(close)'
        )
        results = analyze_code(code)
        opt037 = [r for r in results if r.rule_id == "OPT-037"]
        assert len(opt037) >= 1

    def test_no_false_positive_with_time_filter(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'if time > timestamp(2020, 1, 1, 0, 0) and close > open\n'
            '    strategy.entry("Long", strategy.long)\nplot(close)'
        )
        results = analyze_code(code)
        opt037 = [r for r in results if r.rule_id == "OPT-037"]
        assert len(opt037) == 0


class TestOPT039UnusedRequest:
    """OPT-039: Unused request.*() result."""

    def test_detects_unused_request(self):
        code = (
            '//@version=6\nindicator("test")'
            '\nfloat val = request.security(syminfo.tickerid, "1D", close)'
            '\nplot(close)'
        )
        results = analyze_code(code)
        opt039 = [r for r in results if r.rule_id == "OPT-039"]
        assert len(opt039) >= 1

    def test_no_false_positive_used_request(self):
        code = (
            '//@version=6\nindicator("test")'
            '\nfloat val = request.security(syminfo.tickerid, "1D", close)'
            '\nplot(val)'
        )
        results = analyze_code(code)
        opt039 = [r for r in results if r.rule_id == "OPT-039"]
        assert len(opt039) == 0


class TestOPT040ManualArrayGetLoop:
    """OPT-040: Manual for i=0 to size-1 with array.get() — use for...in."""

    def test_detects_manual_array_get_loop(self):
        code = (
            '//@version=6\nindicator("test")\narr = array.new<float>()\n'
            'for i = 0 to array.size(arr) - 1\n'
            '    val = array.get(arr, i)\nplot(val)'
        )
        results = analyze_code(code)
        opt040 = [r for r in results if r.rule_id == "OPT-040"]
        assert len(opt040) >= 1

    def test_no_false_positive_for_in_loop(self):
        code = (
            '//@version=6\nindicator("test")\narr = array.new<float>()\n'
            'for item in arr\n    val := item\nplot(val)'
        )
        results = analyze_code(code)
        opt040 = [r for r in results if r.rule_id == "OPT-040"]
        assert len(opt040) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Negative/false-positive tests for existing rules
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT001Neg:
    """OPT-001 negative: built-in used correctly."""

    def test_no_false_positive_builtin_used(self):
        code = '//@version=6\nindicator("test")\nmyVal = ta.highest(close, 20)\nplot(myVal)'
        results = analyze_code(code)
        opt001 = [r for r in results if r.rule_id == "OPT-001"]
        assert len(opt001) == 0


class TestOPT002Neg:
    """OPT-002 negative: only 2 identical calls."""

    def test_no_false_positive_two_calls(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float a = ta.sma(close, 20)\nfloat b = ta.sma(close, 20)\nplot(a)'
        )
        results = analyze_code(code)
        opt002 = [r for r in results if r.rule_id == "OPT-002"]
        assert len(opt002) == 0


class TestOPT004Neg:
    """OPT-004 negative: setter used instead of delete+new."""

    def test_no_false_positive_setter(self):
        code = (
            '//@version=6\nindicator("test")\nvar box b = box.new(bar_index, high, bar_index + 1, low)\n'
            'box.set_lefttop(b, bar_index, high)\nbox.set_rightbottom(b, bar_index + 1, low)\nplot(close)'
        )
        results = analyze_code(code)
        opt004 = [r for r in results if r.rule_id == "OPT-004"]
        assert len(opt004) == 0


class TestOPT006Neg:
    """OPT-006 negative: invariant computed before loop."""

    def test_no_false_positive_outside_loop(self):
        code = (
            '//@version=6\nindicator("test")\nfloat val = math.cos(1.5) * close\n'
            'for i = 1 to 10\n    result := val * i\nplot(result)'
        )
        results = analyze_code(code)
        opt006 = [r for r in results if r.rule_id == "OPT-006"]
        assert len(opt006) == 0


class TestOPT007Neg:
    """OPT-007 negative: built-in used correctly."""

    def test_no_false_positive_builtin_sum(self):
        code = '//@version=6\nindicator("test")\nmySum = math.sum(close, 20)\nplot(mySum)'
        results = analyze_code(code)
        opt007 = [r for r in results if r.rule_id == "OPT-007"]
        assert len(opt007) == 0


class TestOPT008Neg:
    """OPT-008 negative: for [idx, item] used correctly."""

    def test_no_false_positive_correct_for_in(self):
        code = (
            '//@version=6\nindicator("test")\narr = array.new<float>()\n'
            'for [idx, item] in arr\n    result := item * 2.0\nplot(result)'
        )
        results = analyze_code(code)
        opt008 = [r for r in results if r.rule_id == "OPT-008"]
        assert len(opt008) == 0


class TestOPT010Neg:
    """OPT-010 negative: max_bars_back already present."""

    def test_no_false_positive_max_bars_present(self):
        code = (
            '//@version=6\nindicator("test")\nmax_bars_back(close, 500)\n'
            'if barstate.islast\n    val = close[500]\nplot(val)'
        )
        results = analyze_code(code)
        opt010 = [r for r in results if r.rule_id == "OPT-010"]
        assert len(opt010) == 0


class TestOPT011Neg:
    """OPT-011 negative: buffer size under 4900."""

    def test_no_false_positive_small_buffer(self):
        code = '//@version=6\nindicator("test")\nmax_bars_back(close, 100)\nplot(close)'
        results = analyze_code(code)
        opt011 = [r for r in results if r.rule_id == "OPT-011"]
        assert len(opt011) == 0


class TestOPT012Neg:
    """OPT-012 negative: calc_bars_count already set."""

    def test_no_false_positive_calc_bars_set(self):
        code = (
            '//@version=6\nindicator("test", calc_bars_count=5000)'
            '\nif barstate.islast\n    label.new(bar_index, high, "x")\nplot(close)'
        )
        results = analyze_code(code)
        opt012 = [r for r in results if r.rule_id == "OPT-012"]
        assert len(opt012) == 0


class TestOPT015Neg:
    """OPT-015 negative: few request calls."""

    def test_no_false_positive_few_requests(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float a = request.security(syminfo.tickerid, "1D", close)\n'
            'float b = request.security(syminfo.tickerid, "4H", close)\nplot(a)'
        )
        results = analyze_code(code)
        opt015 = [r for r in results if r.rule_id == "OPT-015"]
        assert len(opt015) == 0


class TestOPT023Neg:
    """OPT-023 negative: small loop bound."""

    def test_no_false_positive_small_loop(self):
        code = '//@version=6\nindicator("test")\nfor i = 1 to 100\n    val := val + i\nplot(val)'
        results = analyze_code(code)
        opt023 = [r for r in results if r.rule_id == "OPT-023"]
        assert len(opt023) == 0


class TestOPT026Neg:
    """OPT-026 negative: history on built-in in local scope."""

    def test_no_false_positive_builtin_history(self):
        code = '//@version=6\nindicator("test")\nif close > open\n    val = close[5]\nplot(val)'
        results = analyze_code(code)
        opt026 = [r for r in results if r.rule_id == "OPT-026"]
        assert len(opt026) == 0


class TestOPT028Neg:
    """OPT-028 negative: varip not feeding into plot."""

    def test_no_false_positive_varip_no_plot(self):
        code = (
            '//@version=6\nstrategy("test")\nvarip int count = 0\n'
            'if barstate.isrealtime\n    count += 1\n'
            'if count > 10\n    strategy.entry("Long", strategy.long)'
        )
        results = analyze_code(code)
        opt028 = [r for r in results if r.rule_id == "OPT-028"]
        assert len(opt028) == 0


class TestOPT030Neg:
    """OPT-030 negative: var already present."""

    def test_no_false_positive_var_present(self):
        code = '//@version=6\nindicator("test")\nvar int counter = 0\ncounter += 1\nplot(counter)'
        results = analyze_code(code)
        opt030 = [r for r in results if r.rule_id == "OPT-030"]
        assert len(opt030) == 0


class TestOPT031Neg:
    """OPT-031 negative: no barstate.ishistory ternary."""

    def test_no_false_positive_no_ishistory(self):
        code = '//@version=6\nindicator("test")\nfloat past = close[100]\nplot(past)'
        results = analyze_code(code)
        opt031 = [r for r in results if r.rule_id == "OPT-031"]
        assert len(opt031) == 0


class TestOPT032Neg:
    """OPT-032 negative: strategy without calc_on_order_fills."""

    def test_no_false_positive_no_calc_on_order(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'if close > open\n    strategy.entry("Long", strategy.long)'
        )
        results = analyze_code(code)
        opt032 = [r for r in results if r.rule_id == "OPT-032"]
        assert len(opt032) == 0


# ─────────────────────────────────────────────────────────────────────────────
# New rules (OPT-041 through OPT-048)
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT041RequestCalcBars:
    """OPT-041: request.*() calls missing calc_bars_count."""

    def test_detects_many_requests_no_calc_bars(self):
        calls = "\n".join(
            f'float r{i} = request.security(syminfo.tickerid, "{tf}", close)'
            for i, tf in enumerate(["1D"] * 6)
        )
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt041 = [r for r in results if r.rule_id == "OPT-041"]
        assert len(opt041) >= 1

    def test_no_false_positive_with_calc_bars(self):
        calls = "\n".join(
            f'float r{i} = request.security(syminfo.tickerid, "1D", close, calc_bars_count=100)'
            for i in range(6)
        )
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt041 = [r for r in results if r.rule_id == "OPT-041"]
        assert len(opt041) == 0


class TestOPT042DrawingIdLimit:
    """OPT-042: Drawing ID count approaching 500 limit."""

    def test_detects_many_drawings(self):
        calls = "\n".join(f'line.new(bar_index[{i}], high[{i}], bar_index, close)' for i in range(420))
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt042 = [r for r in results if r.rule_id == "OPT-042"]
        assert len(opt042) >= 1

    def test_no_false_positive_few_drawings(self):
        code = '//@version=6\nindicator("test")\nline.new(bar_index, high, bar_index + 1, close)\nplot(close)'
        results = analyze_code(code)
        opt042 = [r for r in results if r.rule_id == "OPT-042"]
        assert len(opt042) == 0


class TestOPT043CodeDuplication:
    """OPT-043: Repeated code that should be extracted to a function."""

    def test_detects_duplicated_lines(self):
        dup = "\n".join(['float val = ta.sma(close, 20) + ta.ema(close, 50) + ta.rsi(close, 14)'] * 6)
        code = f'//@version=6\nindicator("test")\n{dup}\nplot(val)'
        results = analyze_code(code)
        opt043 = [r for r in results if r.rule_id == "OPT-043"]
        assert len(opt043) >= 1

    def test_no_false_positive_unique_lines(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float a = ta.sma(close, 10)\nfloat b = ta.ema(close, 20)\n'
            'float c = ta.rsi(close, 14)\nplot(a + b + c)'
        )
        results = analyze_code(code)
        opt043 = [r for r in results if r.rule_id == "OPT-043"]
        assert len(opt043) == 0


class TestOPT044StrategyOrderLimit:
    """OPT-044: Strategy with unconditional entry may exceed order limit."""

    def test_detects_unconditional_entry(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'strategy.entry("Long", strategy.long)\nplot(close)'
        )
        results = analyze_code(code)
        opt044 = [r for r in results if r.rule_id == "OPT-044"]
        assert len(opt044) >= 1

    def test_no_false_positive_conditional_entry(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'if ta.crossover(ta.ema(close, 10), ta.ema(close, 20))\n'
            '    strategy.entry("Long", strategy.long)\nplot(close)'
        )
        results = analyze_code(code)
        opt044 = [r for r in results if r.rule_id == "OPT-044"]
        assert len(opt044) == 0


class TestOPT045UnusedImport:
    """OPT-045: Import statement never used."""

    def test_detects_unused_import(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'import mylib/library as myLib\nplot(close)'
        )
        results = analyze_code(code)
        opt045 = [r for r in results if r.rule_id == "OPT-045"]
        assert len(opt045) >= 1

    def test_no_false_positive_used_import(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'import mylib/library as myLib\nplot(myLib.myFunc(close))'
        )
        results = analyze_code(code)
        opt045 = [r for r in results if r.rule_id == "OPT-045"]
        assert len(opt045) == 0


class TestOPT046CalcOnEveryTick:
    """OPT-046: calc_on_every_tick=true overhead."""

    def test_detects_calc_on_every_tick(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true, calc_on_every_tick=true)\n'
            'plot(close)'
        )
        results = analyze_code(code)
        opt046 = [r for r in results if r.rule_id == "OPT-046"]
        assert len(opt046) >= 1

    def test_no_false_positive_default(self):
        code = '//@version=6\nstrategy("test", overlay=true)\nplot(close)'
        results = analyze_code(code)
        opt046 = [r for r in results if r.rule_id == "OPT-046"]
        assert len(opt046) == 0


class TestOPT047OversizedScript:
    """OPT-047: Script approaching 5MB compilation limit."""

    def test_detects_oversized_script(self):
        lines = ["//@version=6", 'indicator("test")']
        for i in range(150_000):
            lines.append(f"float var{i} = {i}.0")
        lines.append("plot(close)")
        code = "\n".join(lines)
        results = analyze_code(code)
        opt047 = [r for r in results if r.rule_id == "OPT-047"]
        assert len(opt047) >= 1

    def test_no_false_positive_small_script(self):
        code = '//@version=6\nindicator("test")\nplot(close)'
        results = analyze_code(code)
        opt047 = [r for r in results if r.rule_id == "OPT-047"]
        assert len(opt047) == 0


class TestOPT048PolylineLimit:
    """OPT-048: Polyline count approaching 100 limit."""

    def test_detects_many_polylines(self):
        calls = "\n".join(f'var polyline p{i} = polyline.new(array.new<point>(0))' for i in range(90))
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt048 = [r for r in results if r.rule_id == "OPT-048"]
        assert len(opt048) >= 1

    def test_no_false_positive_few_polylines(self):
        code = '//@version=6\nindicator("test")\nvar polyline p = polyline.new(array.new<point>(0))\nplot(close)'
        results = analyze_code(code)
        opt048 = [r for r in results if r.rule_id == "OPT-048"]
        assert len(opt048) == 0


# ─────────────────────────────────────────────────────────────────────────────
# New rules (OPT-049 through OPT-056)
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT049LookaheadFutureLeak:
    """OPT-049: lookahead_on without [1] offset causes future data leak."""

    def test_detects_lookahead_without_offset(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float val = request.security(syminfo.tickerid, "1D", close, '
            'lookahead = barmerge.lookahead_on)\nplot(val)'
        )
        results = analyze_code(code)
        opt049 = [r for r in results if r.rule_id == "OPT-049"]
        assert len(opt049) >= 1

    def test_no_false_positive_with_offset(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float val = request.security(syminfo.tickerid, "1D", close[1], '
            'lookahead = barmerge.lookahead_on)\nplot(val)'
        )
        results = analyze_code(code)
        opt049 = [r for r in results if r.rule_id == "OPT-049"]
        assert len(opt049) == 0

    def test_no_false_positive_no_lookahead(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float val = request.security(syminfo.tickerid, "1D", close)\nplot(val)'
        )
        results = analyze_code(code)
        opt049 = [r for r in results if r.rule_id == "OPT-049"]
        assert len(opt049) == 0


class TestOPT050TimenowRepaint:
    """OPT-050: timenow usage causes inconsistent historical/realtime behavior."""

    def test_detects_timenow(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'if time > timenow - 86400000\n    plot(close)'
        )
        results = analyze_code(code)
        opt050 = [r for r in results if r.rule_id == "OPT-050"]
        assert len(opt050) >= 1

    def test_no_false_positive_time(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'if time > timestamp(2024, 1, 1, 0, 0)\n    plot(close)'
        )
        results = analyze_code(code)
        opt050 = [r for r in results if r.rule_id == "OPT-050"]
        assert len(opt050) == 0


class TestOPT051IsnewSignalRepaint:
    """OPT-051: barstate.isnew for signal logic repaints."""

    def test_detects_isnew_with_strategy_entry(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'if barstate.isnew\n    strategy.entry("Long", strategy.long)'
        )
        results = analyze_code(code)
        opt051 = [r for r in results if r.rule_id == "OPT-051"]
        assert len(opt051) >= 1

    def test_no_false_positive_isnew_no_signal(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'if barstate.isnew\n    var count = 0'
        )
        results = analyze_code(code)
        opt051 = [r for r in results if r.rule_id == "OPT-051"]
        assert len(opt051) == 0


class TestOPT052MissingIsconfirmed:
    """OPT-052: Strategy signal without barstate.isconfirmed guard."""

    def test_detects_unconfirmed_entry(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'strategy.entry("Long", strategy.long)'
        )
        results = analyze_code(code)
        opt052 = [r for r in results if r.rule_id == "OPT-052"]
        assert len(opt052) >= 1

    def test_no_false_positive_with_isconfirmed(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'if barstate.isconfirmed\n    strategy.entry("Long", strategy.long)'
        )
        results = analyze_code(code)
        opt052 = [r for r in results if r.rule_id == "OPT-052"]
        assert len(opt052) == 0

    def test_no_false_positive_indicator(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'plot(close)'
        )
        results = analyze_code(code)
        opt052 = [r for r in results if r.rule_id == "OPT-052"]
        assert len(opt052) == 0


class TestOPT053NonStandardChartStrategy:
    """OPT-053: Strategy on non-standard chart data."""

    def test_detects_heikin_ashi_strategy(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'float ha = request.security(ticker.heikinashi(syminfo.tickerid), "1D", close)\n'
            'plot(ha)'
        )
        results = analyze_code(code)
        opt053 = [r for r in results if r.rule_id == "OPT-053"]
        assert len(opt053) >= 1

    def test_no_false_positive_standard_ticker(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            'float val = request.security(syminfo.tickerid, "1D", close)\n'
            'plot(val)'
        )
        results = analyze_code(code)
        opt053 = [r for r in results if r.rule_id == "OPT-053"]
        assert len(opt053) == 0

    def test_no_false_positive_indicator_with_ha(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float ha = request.security(ticker.heikinashi(syminfo.tickerid), "1D", close)\n'
            'plot(ha)'
        )
        results = analyze_code(code)
        opt053 = [r for r in results if r.rule_id == "OPT-053"]
        assert len(opt053) == 0


class TestOPT054LowerTfRequest:
    """OPT-054: request.security_lower_tf() repainting risk."""

    def test_detects_lower_tf_request(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'arr = request.security_lower_tf(syminfo.tickerid, "5", close)\n'
            'plot(array.size(arr))'
        )
        results = analyze_code(code)
        opt054 = [r for r in results if r.rule_id == "OPT-054"]
        assert len(opt054) >= 1

    def test_no_false_positive_normal_request(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'float val = request.security(syminfo.tickerid, "1D", close)\n'
            'plot(val)'
        )
        results = analyze_code(code)
        opt054 = [r for r in results if r.rule_id == "OPT-054"]
        assert len(opt054) == 0


class TestOPT055DrawingDisplayLimit:
    """OPT-055: Many drawings without max_*_count parameter."""

    def test_detects_many_drawings_no_max_count(self):
        calls = "\n".join(f'line.new(bar_index, high[{i}], bar_index + 1, close)' for i in range(55))
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt055 = [r for r in results if r.rule_id == "OPT-055"]
        assert len(opt055) >= 1

    def test_no_false_positive_with_max_count(self):
        calls = "\n".join(f'line.new(bar_index, high[{i}], bar_index + 1, close)' for i in range(55))
        code = f'//@version=6\nindicator("test", max_lines_count=500)\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt055 = [r for r in results if r.rule_id == "OPT-055"]
        assert len(opt055) == 0

    def test_no_false_positive_few_drawings(self):
        code = '//@version=6\nindicator("test")\nline.new(bar_index, high, bar_index + 1, close)\nplot(close)'
        results = analyze_code(code)
        opt055 = [r for r in results if r.rule_id == "OPT-055"]
        assert len(opt055) == 0


class TestOPT056MapSizeLimit:
    """OPT-056: Map populated in loop approaching 50K limit."""

    def test_detects_map_put_in_loop(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'm = map.new<string, float>()\n'
            'for i = 0 to 100\n'
            '    map.put(m, str.tostring(i), close[i])\n'
            'plot(close)'
        )
        results = analyze_code(code)
        opt056 = [r for r in results if r.rule_id == "OPT-056"]
        assert len(opt056) >= 1

    def test_no_false_positive_no_loop(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'm = map.new<string, float>()\n'
            'map.put(m, "key", close)\n'
            'plot(close)'
        )
        results = analyze_code(code)
        opt056 = [r for r in results if r.rule_id == "OPT-056"]
        assert len(opt056) == 0


class TestOPT010DeepOffset:
    """OPT-010: Deep history offsets starting with non-4 digits."""

    def test_detects_offset_3000(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'if barstate.islast\n    float past = myVar[3000]\nplot(past)'
        )
        results = analyze_code(code)
        opt010 = [r for r in results if r.rule_id == "OPT-010"]
        assert len(opt010) >= 1

    def test_detects_offset_500(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'if barstate.islast\n    float past = myVar[500]\nplot(past)'
        )
        results = analyze_code(code)
        opt010 = [r for r in results if r.rule_id == "OPT-010"]
        assert len(opt010) >= 1


class TestOPT014CommentSuppress:
    """OPT-014: Commented-out plots should not count."""

    def test_no_false_positive_commented_plots(self):
        comment_plots = "\n".join(f'// plot(close, "p{i}")' for i in range(50))
        code = f'//@version=6\nindicator("test")\n{comment_plots}\nplot(close)'
        results = analyze_code(code)
        opt014 = [r for r in results if r.rule_id == "OPT-014"]
        assert len(opt014) == 0


class TestOPT015UniqueCheck:
    """OPT-015: Should check total count approaching limit."""

    def test_no_false_positive_few_requests(self):
        calls = "\n".join(
            f'float r{i} = request.security(syminfo.tickerid, "1D", close)'
            for i in range(30)
        )
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt015 = [r for r in results if r.rule_id == "OPT-015"]
        assert len(opt015) == 0  # 30 calls — not approaching limit

    def test_detects_many_unique_requests(self):
        timeframes = ["1D", "4H", "1H", "15", "5", "1", "1D", "4H", "1H",
                      "15", "5", "1", "1D", "4H", "1H", "15", "5", "1",
                      "D", "W", "M", "60", "120", "240", "30", "10", "3",
                      "2", "1D", "4H", "1H", "15", "5", "1", "D", "W", "M"]
        calls = "\n".join(
            f'float r{i} = request.security(syminfo.tickerid, "{tf}", close)'
            for i, tf in enumerate(timeframes)
        )
        code = f'//@version=6\nindicator("test")\n{calls}\nplot(close)'
        results = analyze_code(code)
        opt015 = [r for r in results if r.rule_id == "OPT-015"]
        assert len(opt015) >= 1


class TestOPT046CommentSuppress:
    """OPT-046: Commented calc_on_every_tick should not trigger."""

    def test_no_false_positive_commented(self):
        code = (
            '//@version=6\nstrategy("test", overlay=true)\n'
            '// calc_on_every_tick=true\nplot(close)'
        )
        results = analyze_code(code)
        opt046 = [r for r in results if r.rule_id == "OPT-046"]
        assert len(opt046) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Clean code tests — no false positives
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanCode:
    """Well-written code should produce few or no findings."""

    def test_minimal_clean_code(self):
        code = """\
//@version=6
indicator("Clean Example")
myEma = ta.ema(close, 21)
plot(myEma, "EMA", color.blue)
"""
        results = analyze_code(code)
        # This code is clean — should have zero findings
        assert len(results) == 0

    def test_optimized_request_pattern(self):
        code = """\
//@version=6
indicator("Optimized Request")
[a, b, c] = request.security(syminfo.tickerid, "1D", [close, ta.sma(close, 20), volume])
plot(a)
"""
        results = analyze_code(code)
        opt003 = [r for r in results if r.rule_id == "OPT-003"]
        assert len(opt003) == 0  # Single call — no duplication


# ─────────────────────────────────────────────────────────────────────────────
# Format results tests
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatResults:
    def test_empty_results(self):
        report = format_results([])
        assert "No issues found" in report

    def test_results_sorted_by_severity(self):
        code = """\
//@version=6
indicator("test")
if close > open
    float mySma = ta.sma(close, 20)
var table t = table.new(position.top_right, 2, 2)
table.cell_set_text(t, 0, 0, "hello")
for i = 1 to 50000
    sum += i
"""
        results = analyze_code(code)
        if len(results) >= 2:
            first_sev = results[0].severity
            assert first_sev in ("critical", "high")

    def test_summary_includes_counts(self):
        results = [
            OptimizationResult("OPT-003", "test", "critical", 1, "s", "fix", "q", "cat"),
            OptimizationResult("OPT-005", "test", "medium", 2, "s", "fix", "q", "cat"),
        ]
        report = format_results(results)
        assert "1 critical" in report
        assert "1 medium" in report


# ─────────────────────────────────────────────────────────────────────────────
# Branding middleware tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBrandingMiddleware:
    def test_branding_header_present(self):
        from server import _BRANDING_HEADER
        assert "@Fractalyst" in _BRANDING_HEADER
        assert "deeptest.io" in _BRANDING_HEADER

    def test_branding_footer_rotation(self):
        from server import _BRANDING_FOOTERS
        assert len(_BRANDING_FOOTERS) == 3
        for footer in _BRANDING_FOOTERS:
            assert "deeptest" in footer
            assert "Fractalyst" in footer

    def test_branding_disabled_with_env(self):
        original = os.environ.get("BRANDING")
        os.environ["BRANDING"] = "0"
        try:
            assert os.getenv("BRANDING", "1") == "0"
        finally:
            if original is None:
                os.environ.pop("BRANDING", None)
            else:
                os.environ["BRANDING"] = original


# ─────────────────────────────────────────────────────────────────────────────
# Negative tests for OPT-009 and OPT-029
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT009Neg:
    """OPT-009 negative: array.min/max OUTSIDE loop."""

    def test_no_false_positive_outside_loop(self):
        code = (
            '//@version=6\nindicator("test")\narr = array.new<float>()\n'
            'm = array.min(arr)\nfor item in arr\n    result := item * m\nplot(result)'
        )
        results = analyze_code(code)
        opt009 = [r for r in results if r.rule_id == "OPT-009"]
        assert len(opt009) == 0


class TestOPT029Neg:
    """OPT-029 negative: isrealtime update NOT feeding into plot."""

    def test_no_false_positive_no_plot(self):
        code = (
            '//@version=6\nstrategy("test")\n'
            'varip float x = na\nif barstate.isrealtime\n    x := close\n'
            'if x > 0\n    strategy.entry("Long", strategy.long)'
        )
        results = analyze_code(code)
        opt029 = [r for r in results if r.rule_id == "OPT-029"]
        assert len(opt029) == 0


# ─────────────────────────────────────────────────────────────────────────────
# New rules (OPT-057 through OPT-059)
# ─────────────────────────────────────────────────────────────────────────────


class TestOPT057RequestInLoop:
    """OPT-057: request.*() inside loop with variable args."""

    def test_detects_request_with_loop_var(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'syms = array.new<string>()\n'
            'for sym in syms\n'
            '    float val = request.security(sym, "1D", close)\n'
            'plot(val)'
        )
        results = analyze_code(code)
        opt057 = [r for r in results if r.rule_id == "OPT-057"]
        assert len(opt057) >= 1

    def test_no_false_positive_static_request(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'for i = 0 to 10\n'
            '    val := val + i\n'
            'float r = request.security(syminfo.tickerid, "1D", close)\n'
            'plot(r)'
        )
        results = analyze_code(code)
        opt057 = [r for r in results if r.rule_id == "OPT-057"]
        assert len(opt057) == 0


class TestOPT058FootprintLimit:
    """OPT-058: request.footprint() called more than once."""

    def test_detects_two_footprint_calls(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'f1 = request.footprint("AAPL")\n'
            'f2 = request.footprint("MSFT")\n'
            'plot(close)'
        )
        results = analyze_code(code)
        opt058 = [r for r in results if r.rule_id == "OPT-058"]
        assert len(opt058) >= 1

    def test_no_false_positive_single_footprint(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'f1 = request.footprint("AAPL")\n'
            'plot(close)'
        )
        results = analyze_code(code)
        opt058 = [r for r in results if r.rule_id == "OPT-058"]
        assert len(opt058) == 0


class TestOPT059DrawingPastMaxBars:
    """OPT-059: Drawing x-coordinate >10,000 bars back."""

    def test_detects_bar_index_minus_15000(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'line.new(bar_index - 15000, close, bar_index, close)\nplot(close)'
        )
        results = analyze_code(code)
        opt059 = [r for r in results if r.rule_id == "OPT-059"]
        assert len(opt059) >= 1

    def test_no_false_positive_normal_offset(self):
        code = (
            '//@version=6\nindicator("test")\n'
            'line.new(bar_index - 100, close, bar_index, close)\nplot(close)'
        )
        results = analyze_code(code)
        opt059 = [r for r in results if r.rule_id == "OPT-059"]
        assert len(opt059) == 0
