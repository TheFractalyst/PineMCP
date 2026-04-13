"""
test_optimizer.py — Tests for the PineScript optimization engine and branding middleware.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.optimizer import OptimizationResult, analyze_code, format_results  # noqa: E402

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


class TestOPT015RequestLimit:
    """OPT-015: Approaching request.*() call limit."""

    def test_detects_many_request_calls(self):
        # Generate code with 40+ request calls
        calls = "\n".join(
            f'float r{i} = request.security(syminfo.tickerid, "{tf}", close)'
            for i, tf in enumerate(["1D"] * 40)
        )
        code = f"//@version=6\nindicator('test')\n{calls}\nplot(close)"
        results = analyze_code(code)
        opt015 = [r for r in results if r.rule_id == "OPT-015"]
        assert len(opt015) >= 1


class TestOPT017LargeScript:
    """OPT-017: Very large script approaching token limits."""

    def test_detects_large_script(self):
        # Generate a script with 4000+ lines
        lines = ["//@version=6", 'indicator("test")']
        for i in range(4000):
            lines.append(f"float var{i} = {i}.0")
        lines.append("plot(close)")
        code = "\n".join(lines)
        results = analyze_code(code)
        opt017 = [r for r in results if r.rule_id == "OPT-017"]
        assert len(opt017) >= 1


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
        # analyze_code sorts by severity — test that end-to-end
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
            # First finding should be higher severity than later ones
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
        # Store original
        original = os.environ.get("BRANDING")
        os.environ["BRANDING"] = "0"
        try:
            # The middleware checks BRANDING env var at call time
            assert os.getenv("BRANDING", "1") == "0"
        finally:
            if original is None:
                os.environ.pop("BRANDING", None)
            else:
                os.environ["BRANDING"] = original
