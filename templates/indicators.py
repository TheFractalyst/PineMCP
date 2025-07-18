"""
templates/indicators.py
------------------------------------------------------------------------------
Known-correct indicator templates and keyword extraction.
- _INDICATOR_TEMPLATES: pre-verified calc stubs for 20+ indicator families
- extract_indicator_keywords(): detect indicator family from description
- map_input_to_param(): fuzzy variable-to-parameter name matching
"""

from __future__ import annotations

import re

# -----------------------------------------------------------------------------
# Indicator templates
# Keys: lowercased indicator-family names
# Values: (calc_stub, overlay_default)
# -----------------------------------------------------------------------------

_INDICATOR_TEMPLATES: dict[str, tuple[str, bool]] = {
    "rsi": (
        "rsiValue = ta.rsi(src, length)\nplot(rsiValue, \"RSI\", color.orange)\n"
        "hline(70, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(30, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "bollinger": (
        "[middle, upper, lower] = ta.bb(src, length, mult)\n"
        "plot(middle, \"Basis\", color.blue)\nplot(upper, \"Upper\", color.red)\n"
        "plot(lower, \"Lower\", color.green)\n"
        "p1 = plot(upper, display=display.none)\np2 = plot(lower, display=display.none)\n"
        "fill(p1, p2, color=color.new(color.blue, 90))",
        True,
    ),
    "ema": (
        "emaValue = ta.ema(src, length)\nplot(emaValue, \"EMA\", color.orange)",
        True,
    ),
    "sma": (
        "smaValue = ta.sma(src, length)\nplot(smaValue, \"SMA\", color.orange)",
        True,
    ),
    "atr": (
        "atrValue = ta.atr(length)\nplot(atrValue, \"ATR\", color.orange)",
        False,
    ),
    "stochastic": (
        "k = ta.sma(ta.stoch(src, high, low, length), kSmooth)\n"
        "d = ta.sma(k, dSmooth)\n"
        "plot(k, \"K\", color.blue)\nplot(d, \"D\", color.orange)\n"
        "hline(80, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(20, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "supertrend": (
        "[supertrendValue, direction] = ta.supertrend(factor, atrLength)\n"
        "upTrend = plot(direction < 0 ? supertrendValue : na, \"Up Trend\", color.green, linewidth=2)\n"
        "dnTrend = plot(direction < 0 ? na : supertrendValue, \"Down Trend\", color.red, linewidth=2)\n"
        "fill(upTrend, dnTrend, color=direction < 0 ? color.new(color.green, 90) : color.new(color.red, 90))",
        True,
    ),
    "vwap": (
        "vwapValue = ta.vwap(hlc3)\nplot(vwapValue, \"VWAP\", color.orange, linewidth=2)",
        True,
    ),
    "adl": (
        "adlValue = ta.accdist\nplot(adlValue, \"ADL\", color.orange)",
        False,
    ),
    "obv": (
        "obvValue = ta.obv\nplot(obvValue, \"OBV\", color.orange)",
        False,
    ),
    "cci": (
        "cciValue = ta.cci(src, length)\nplot(cciValue, \"CCI\", color.orange)\n"
        "hline(100, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(-100, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "mfi": (
        "mfiValue = ta.mfi(length)\nplot(mfiValue, \"MFI\", color.orange)\n"
        "hline(80, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(20, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "williams": (
        "wrValue = ta.wpr(length)\nplot(wrValue, \"Williams %R\", color.orange)\n"
        "hline(-20, \"Overbought\", color.red, hline.style_dashed)\n"
        "hline(-80, \"Oversold\", color.green, hline.style_dashed)",
        False,
    ),
    "macd": (
        "[macdLine, signalLine, histLine] = ta.macd(src, fastLength, slowLength, signalLength)\n"
        "plot(macdLine, \"MACD\", color.blue)\nplot(signalLine, \"Signal\", color.orange)\n"
        "plot(histLine, \"Histogram\", color.red, style=plot.style_histogram)",
        False,
    ),
    "dmi": (
        "[diPlus, diMinus, adxValue] = ta.dmi(diLength, adxSmoothing)\n"
        "plot(diPlus, \"+DI\", color.green)\nplot(diMinus, \"-DI\", color.red)\n"
        "plot(adxValue, \"ADX\", color.orange, linewidth=2)",
        False,
    ),
    "ichimoku": (
        "tenkan = math.avg(ta.highest(high, 9), ta.lowest(low, 9))\n"
        "kijun = math.avg(ta.highest(high, 26), ta.lowest(low, 26))\n"
        "senkouA = math.avg(tenkan, kijun)\n"
        "senkouB = math.avg(ta.highest(high, 52), ta.lowest(low, 52))\n"
        "plot(tenkan, \"Tenkan\", color.blue)\nplot(kijun, \"Kijun\", color.red)\n"
        "p1 = plot(senkouA, \"Senkou A\", display=display.none)\n"
        "p2 = plot(senkouB, \"Senkou B\", display=display.none)\n"
        "fill(p1, p2, color=senkouA > senkouB ? color.new(color.green, 90) : color.new(color.red, 90))",
        True,
    ),
    "sar": (
        "sarValue = ta.sar(start, increment, maximum)\n"
        "plot(sarValue, \"Parabolic SAR\", color.orange, style=plot.style_cross, linewidth=2)",
        True,
    ),
    "keltner": (
        "emaValue = ta.ema(src, length)\n"
        "atrValue = ta.atr(atrLength)\n"
        "upper = emaValue + mult * atrValue\nlower = emaValue - mult * atrValue\n"
        "plot(emaValue, \"EMA\", color.blue)\nplot(upper, \"Upper\", color.red)\n"
        "plot(lower, \"Lower\", color.green)\n"
        "p1 = plot(upper, display=display.none)\np2 = plot(lower, display=display.none)\n"
        "fill(p1, p2, color=color.new(color.blue, 90))",
        True,
    ),
    "donchian": (
        "upper = ta.highest(high, length)\nlower = ta.lowest(low, length)\n"
        "mid = math.avg(upper, lower)\n"
        "plot(upper, \"Upper\", color.red)\nplot(lower, \"Lower\", color.green)\n"
        "plot(mid, \"Middle\", color.orange, style=plot.style_circles)",
        True,
    ),
    "aroon": (
        "up = ta.aroon(length).up\ndn = ta.aroon(length).down\n"
        "osc = up - dn\n"
        "plot(up, \"Aroon Up\", color.green)\nplot(dn, \"Aroon Down\", color.red)\n"
        "plot(osc, \"Oscillator\", color.orange, style=plot.style_histogram)\n"
        "hline(0, \"Zero\", color.gray, hline.style_dotted)",
        False,
    ),
    "cmf": (
        "cmfValue = ta.cmf(length)\nplot(cmfValue, \"CMF\", color.orange)\n"
        "hline(0, \"Zero\", color.gray, hline.style_dotted)",
        False,
    ),
    "tema": (
        "e1 = ta.ema(src, length)\ne2 = ta.ema(e1, length)\ne3 = ta.ema(e2, length)\n"
        "temaValue = 3 * (e1 - e2) + e3\nplot(temaValue, \"TEMA\", color.orange)",
        True,
    ),
    "dema": (
        "e1 = ta.ema(src, length)\ne2 = ta.ema(e1, length)\n"
        "demaValue = 2 * e1 - e2\nplot(demaValue, \"DEMA\", color.orange)",
        True,
    ),
}


def extract_indicator_keywords(description: str) -> list[str]:
    """Extract indicator-family keywords from a natural language description.

    Returns a list of lowercase keyword tokens that map to keys in
    _INDICATOR_TEMPLATES. Order matters: longer/more-specific patterns
    are tested first so "Bollinger Bands" is not swallowed by "bb".
    """
    desc_lower = description.lower()
    # Order matters: longer/more specific patterns first
    patterns = [
        ("bollinger", r"\bbollinger\b|\bbb\b(?!\s*=)"),
        ("supertrend", r"\bsupertrend\b|\bsuper.?trend\b"),
        ("stochastic", r"\bstochastic\b|\bstoch\b"),
        ("rsi", r"\brelative\s+strength\b|\brsi\b"),
        ("ema", r"\bexponential\s+moving\s+average\b|\bema\b"),
        ("sma", r"\bsimple\s+moving\s+average\b|\bsma\b"),
        ("atr", r"\baverage\s+true\s+range\b|\batr\b"),
        ("vwap", r"\bvolume\s+weighted\s+average\b|\bvwap\b"),
        ("adl", r"\baccumulation\s+distribution\b|\badl\b|\baccdist\b"),
        ("obv", r"\bon\s+balance\s+volume\b|\bobv\b"),
        ("cci", r"\bcommodity\s+channel\b|\bcci\b"),
        ("mfi", r"\bmoney\s+flow\s+index\b|\bmfi\b"),
        ("williams", r"\bwilliams\s*%?\s*r\b|\bwpr\b"),
        ("macd", r"\bmacd\b|\bmoving\s+average\s+convergence\s+divergence\b"),
        ("dmi", r"\bdirectional\s+movement\b|\bdmi\b|\badx\b"),
        ("ichimoku", r"\bichimoku\b|\bcloud\b"),
        ("sar", r"\bparabolic\s+sar\b|\bstop\s+and\s+reverse\b|\bsar\b"),
        ("keltner", r"\bkeltner\b|\bkc\b"),
        ("donchian", r"\bdonchian\b|\bchannel\b(?!.*cci)"),
        ("aroon", r"\baroon\b"),
        ("cmf", r"\bchaikin\s+money\s+flow\b|\bcmf\b"),
        ("tema", r"\btriple\s+exponential\b|\btema\b"),
        ("dema", r"\bdouble\s+exponential\b|\bdema\b"),
    ]
    matches = []
    for family, pattern in patterns:
        if re.search(pattern, desc_lower):
            matches.append(family)
    return matches


def map_input_to_param(
    var_name: str, param_names: list[str]
) -> str | None:
    """Map a user input variable name to the best-matching function parameter.

    Matching strategy (in priority order):
    1. Exact match
    2. Param name is a suffix of the var name (e.g. rsiLength -> length)
    3. Param name is a prefix of the var name (e.g. src -> srcClose)
    4. Substring containment with minimum 3-char overlap
    """
    vl = var_name.lower()
    # 1. Exact
    for pn in param_names:
        if vl == pn.lower():
            return pn
    # 2. Param is suffix of var (rsiLength -> length)
    for pn in param_names:
        pnl = pn.lower()
        if len(pnl) >= 3 and vl.endswith(pnl):
            return pn
    # 3. Param is prefix of var (src -> srcClose)
    for pn in param_names:
        pnl = pn.lower()
        if len(pnl) >= 3 and vl.startswith(pnl):
            return pn
    # 4. Substring containment (min 3 chars)
    for pn in param_names:
        pnl = pn.lower()
        if len(pnl) >= 3 and (pnl in vl or vl in pnl):
            return pn
    return None
