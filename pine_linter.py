"""
Tier 1 heuristic linter for Pine Script v6.

Catches ~50% of common errors using regex/heuristic rules.
Runs in <10ms, no external dependencies, fully offline.

Architecture:
    Tier 1: This module (regex/heuristics, ~50% coverage, <10ms)
    Tier 2: pynescript AST + semantic checks (future, ~70% coverage)
    Tier 3: Remote pine-facade compiler (100% coverage, external)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class LintIssue:
    line: int
    column: int
    text: str
    severity: str = "error"  # error, warning, info
    rule: str = ""
    fix_hint: str = ""


@dataclass
class LintResult:
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[LintIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0

    def to_dict(self) -> dict:
        return {
            "success": not self.has_errors,
            "errors": [
                {"line": e.line, "column": e.column, "text": e.text, "type": "error"}
                for e in self.errors
            ],
            "warnings": [
                {"line": w.line, "column": w.column, "text": w.text, "type": "warning"}
                for w in self.warnings
            ],
            "meta": {"source": "pine_linter_tier1", "rule_count": len(self.issues)},
        }


# ── Namespace maps ───────────────────────────────────────────────────

# Functions that require ta. prefix in v6 (commonly used without it in v4/v5)
_TA_FUNCTIONS = frozenset({
    "ema", "sma", "wma", "vwma", "rma", "swma", "alma",
    "rsi", "macd", "atr", "cci", "stoch", "bb", "bbw",
    "donchian", "kc", "kcw", "dmi", "adx", "supertrend",
    "sar", "aroon", "obv", "mfi", "roc", "mom", "tsi",
    "ppo", "apo", "cog", "corr", "variance", "stdev",
    "dev", "percentrank", "percentile_nearest_rank",
    "percentile_linear_interpolation",
    "linreg", "median", "mode", "cum", "tr",
    "change", "mom", "highest", "lowest", "highestbars",
    "lowestbars", "cross", "crossover", "crossunder",
    "valuewhen", "barssince", "rising", "falling",
    "pivotshigh", "pivotslow", "iff",
})

# Functions that require math. prefix in v6
_MATH_FUNCTIONS = frozenset({
    "abs", "round", "floor", "ceil", "max", "min", "pow",
    "sqrt", "log", "log10", "exp", "sign", "avg", "sum",
    "random", "todegrees", "toradians", "sin", "cos", "tan",
    "asin", "acos", "atan",
})

# Functions that require str. prefix in v6
_STR_FUNCTIONS = frozenset({
    "tostring", "format", "match", "pos", "replace",
    "length", "lower", "upper", "trim", "split",
    "tonumber", "contains", "substring",
})

# Functions that require array. prefix in v6
_ARRAY_FUNCTIONS = frozenset({
    "new", "push", "pop", "shift", "unshift", "insert",
    "remove", "set", "sort", "reverse", "index", "includes",
    "size", "clear", "copy", "concat", "fill", "join",
    "sum", "avg", "median", "mode", "stdev", "variance",
    "min", "max", "abs", "binsearch", "binary_search",
    "every", "some", "map", "filter", "reduce", "last",
    "first", "from",
})

# v5 functions/keywords that don't exist in v6
_V5_DEPRECATED = {
    "study(": "Use indicator() instead of study() in v6.",
    "security(": "Use request.security() instead of security() in v6.",
    "tickerid": "Use syminfo.tickerid instead of tickerid in v6.",
    "period": "Use timeframe.period instead of period in v6.",
    "interval": "Use timeframe.period instead of interval in v6.",
    "multiplier": "Use timeframe.multiplier instead of multiplier in v6.",
    "isdaily": "Use timeframe.isdaily instead of isdaily in v6.",
    "isintraday": "Use timeframe.isintraday instead of isintraday in v6.",
    "isweekly": "Use timeframe.isweekly instead of isweekly in v6.",
    "ismonthly": "Use timeframe.ismonthly instead of ismonthly in v6.",
    "tostring(": "Use str.tostring() instead of tostring() in v6.",
    "tonumber(": "Use str.tonumber() instead of tonumber() in v6.",
    "round(": "Use math.round() instead of round() in v6.",
    "abs(": "Use math.abs() instead of abs() in v6.",
    "pow(": "Use math.pow() instead of pow() in v6.",
    "sqrt(": "Use math.sqrt() instead of sqrt() in v6.",
    "log(": "Use math.log() instead of log() in v6.",
    "max(": "Use math.max() instead of max() in v6.",
    "min(": "Use math.min() instead of min() in v6.",
}

# Reserved words that cannot be used as variable names
_RESERVED_WORDS = frozenset({
    "import", "export", "switch", "for", "while", "if", "else",
    "var", "varip", "true", "false", "na", "and", "or", "not",
    "type", "enum", "method", "global",
})


# ── Helper functions ─────────────────────────────────────────────────


def _strip_comments(code: str) -> str:
    """Remove single-line comments (// ...) from code."""
    return re.sub(r'//[^\n]*', '', code)


def _strip_strings(code: str) -> str:
    """Remove string literals to avoid false matches inside strings."""
    return re.sub(r'"(?:[^"\\]|\\.)*"', '""', code)


def _find_outside_strings(code: str, pattern: str) -> list[tuple[int, int]]:
    """Find pattern positions that are NOT inside string literals.
    Returns list of (line, column) positions.
    """
    clean = _strip_strings(code)
    positions = []
    for i, line in enumerate(clean.split('\n'), 1):
        for m in re.finditer(pattern, line):
            positions.append((i, m.start() + 1))
    return positions


# ── Check functions ──────────────────────────────────────────────────


def _check_version_directive(lines: list[str], result: LintResult) -> None:
    """Rule: Script must start with //@version=6."""
    if not lines:
        return

    # Check first 5 non-empty lines for version directive
    for i, line in enumerate(lines[:5]):
        stripped = line.strip()
        if stripped.startswith("//@version="):
            if "6" not in stripped:
                result.issues.append(LintIssue(
                    line=i + 1, column=1,
                    text=f"Wrong Pine Script version: '{stripped}'. This tool supports v6 only.",
                    severity="error", rule="wrong_version",
                    fix_hint="Change to //@version=6",
                ))
            return  # version directive found, stop checking

    # No version directive found in first 5 lines
    result.issues.append(LintIssue(
        line=1, column=1,
        text="Missing //@version=6 directive. First line must be //@version=6.",
        severity="error", rule="missing_version",
        fix_hint="Add //@version=6 as the first line of your script.",
    ))


def _check_script_declaration(lines: list[str], result: LintResult) -> None:
    """Rule: Script must declare indicator(), strategy(), or library()."""
    clean_code = _strip_strings(_strip_comments('\n'.join(lines)))

    has_indicator = bool(re.search(r'\bindicator\s*\(', clean_code))
    has_strategy = bool(re.search(r'\bstrategy\s*\(\s*["\']', clean_code))
    has_library = bool(re.search(r'\blibrary\s*\(\s*["\']', clean_code))

    if not (has_indicator or has_strategy or has_library):
        result.issues.append(LintIssue(
            line=0, column=0,
            text="Missing script declaration. Add indicator(), strategy(), or library() after //@version=6.",
            severity="error", rule="missing_declaration",
            fix_hint='Add: indicator("My Script") or strategy("My Strategy", overlay=true) after the version directive.',
        ))


def _check_v5_syntax(code: str, lines: list[str], result: LintResult) -> None:
    """Rule: Detect v5 syntax that breaks in v6."""
    clean = _strip_strings(_strip_comments(code))

    for pattern, hint in _V5_DEPRECATED.items():
        # Match function calls (pattern includes the opening paren)
        if '(' in pattern:
            # Ensure it's a function call, not a namespaced one
            func_name = pattern.rstrip('(')
            # Match bare call: not preceded by a dot or namespace
            regex = r'(?<![.\w])' + re.escape(func_name) + r'\s*\('
            if re.search(regex, clean):
                for i, line in enumerate(lines, 1):
                    clean_line = _strip_strings(_strip_comments(line))
                    if re.search(regex, clean_line):
                        result.issues.append(LintIssue(
                            line=i, column=1,
                            text=f"v5 syntax detected: '{func_name}()'. {hint}",
                            severity="error", rule="v5_syntax",
                            fix_hint=hint,
                        ))
                        break  # one report per pattern
        else:
            # Bare keyword/variable
            regex = r'(?<![.\w])' + re.escape(pattern.rstrip('(')) + r'(?!\w)'
            if re.search(regex, clean):
                for i, line in enumerate(lines, 1):
                    clean_line = _strip_strings(_strip_comments(line))
                    if re.search(regex, clean_line):
                        result.issues.append(LintIssue(
                            line=i, column=1,
                            text=f"v5 syntax detected: '{pattern.rstrip('(')}'. {hint}",
                            severity="warning", rule="v5_syntax",
                            fix_hint=hint,
                        ))
                        break


def _check_namespace_errors(code: str, lines: list[str], result: LintResult) -> None:
    """Rule: Detect bare calls that need namespace prefix."""
    clean = _strip_strings(_strip_comments(code))

    namespace_checks = [
        ("ta.", _TA_FUNCTIONS),
        ("math.", _MATH_FUNCTIONS),
        ("str.", _STR_FUNCTIONS),
        ("array.", _ARRAY_FUNCTIONS),
    ]

    for prefix, funcs in namespace_checks:
        for func in funcs:
            # Match bare function call (not preceded by dot or word char)
            pattern = r'(?<![.\w])' + re.escape(func) + r'\s*\('
            matches = list(re.finditer(pattern, clean))
            for m in matches:
                # Find which line this is on
                line_num = clean[:m.start()].count('\n') + 1
                result.issues.append(LintIssue(
                    line=line_num, column=m.start() + 1,
                    text=f"Function '{func}()' requires '{prefix}' prefix in v6. Use {prefix}{func}().",
                    severity="error", rule="missing_namespace",
                    fix_hint=f"Change {func}() to {prefix}{func}()",
                ))


def _check_duplicate_declarations(lines: list[str], result: LintResult) -> None:
    """Rule: Only one script declaration allowed."""
    clean_lines = [_strip_strings(_strip_comments(l)) for l in lines]

    declarations = []
    for i, line in enumerate(clean_lines):
        if re.search(r'\bindicator\s*\(', line):
            declarations.append(("indicator", i + 1))
        if re.search(r'\bstrategy\s*\(', line):
            declarations.append(("strategy", i + 1))
        if re.search(r'\blibrary\s*\(', line):
            declarations.append(("library", i + 1))

    if len(declarations) > 1:
        for dtype, line_num in declarations[1:]:
            result.issues.append(LintIssue(
                line=line_num, column=1,
                text=f"Duplicate script declaration: {dtype}(). Only one indicator/strategy/library declaration is allowed.",
                severity="error", rule="duplicate_declaration",
                fix_hint=f"Remove the duplicate {dtype}() declaration.",
            ))

    # Check for conflicting declarations (both indicator and strategy)
    types = set(d[0] for d in declarations)
    if "indicator" in types and "strategy" in types:
        result.issues.append(LintIssue(
            line=0, column=0,
            text="Cannot use both indicator() and strategy() in the same script.",
            severity="error", rule="conflicting_declarations",
            fix_hint="Choose either indicator() or strategy(), not both.",
        ))


def _check_bracket_balance(code: str, result: LintResult) -> None:
    """Rule: Parentheses, brackets, and braces must be balanced."""
    clean = _strip_strings(_strip_comments(code))

    pairs = {'(': ')', '[': ']', '{': '}'}
    openers = set(pairs.keys())
    closers = set(pairs.values())

    stack: list[tuple[str, int, int]] = []  # (char, line, col)

    for line_idx, line in enumerate(clean.split('\n'), 1):
        for col, ch in enumerate(line, 1):
            if ch in openers:
                stack.append((ch, line_idx, col))
            elif ch in closers:
                if not stack:
                    result.issues.append(LintIssue(
                        line=line_idx, column=col,
                        text=f"Unexpected closing '{ch}' with no matching opener.",
                        severity="error", rule="unbalanced_brackets",
                        fix_hint=f"Remove the extra '{ch}' or add a matching opener.",
                    ))
                    return  # stop checking after first imbalance
                last_open, _, _ = stack[-1]
                if pairs.get(last_open) != ch:
                    result.issues.append(LintIssue(
                        line=line_idx, column=col,
                        text=f"Mismatched '{ch}': expected '{pairs[last_open]}' to close '{last_open}'.",
                        severity="error", rule="unbalanced_brackets",
                        fix_hint=f"Check bracket matching near this location.",
                    ))
                    return
                stack.pop()

    # Check for unclosed brackets
    for ch, line_num, col in stack:
        expected_close = pairs[ch]
        result.issues.append(LintIssue(
            line=line_num, column=col,
            text=f"Unclosed '{ch}' — missing '{expected_close}'.",
            severity="error", rule="unbalanced_brackets",
            fix_hint=f"Add '{expected_close}' to close the '{ch}' opened on line {line_num}.",
        ))


def _check_assignment_before_declaration(code: str, lines: list[str], result: LintResult) -> None:
    """Rule: := reassignment requires prior declaration with =."""
    clean = _strip_strings(_strip_comments(code))

    # Find all declarations (x = ...) and reassignments (x := ...)
    declared: dict[str, int] = {}  # name -> line number of first declaration
    reassigned: list[tuple[str, int]] = []  # (name, line) of := usage

    for i, line in enumerate(clean.split('\n'), 1):
        stripped = line.strip()
        if stripped.startswith('//'):
            continue

        # Match declarations: var/varip prefix or direct name = (not ==, !=, <=, >=)
        decl_match = re.finditer(
            r'(?:var|varip)\s+(?:\w+\s+)?(\w+)\s*=',
            stripped,
        )
        for m in decl_match:
            name = m.group(1)
            if name not in declared:
                declared[name] = i

        # Match direct declarations: name = (but not ==, !=, <=, >=, :=)
        direct_decl = re.finditer(
            r'(?<![:<>=!])\b(\w+)\s*(?<![<>!=])=(?!=)',
            stripped,
        )
        for m in direct_decl:
            name = m.group(1)
            # Skip if it's inside a function call or declaration keyword
            if name in ('if', 'else', 'for', 'while', 'switch', 'var', 'varip',
                        'import', 'export', 'type', 'enum', 'method'):
                continue
            if name not in declared:
                declared[name] = i

        # Match reassignments: name :=
        reassign_match = re.finditer(r'\b(\w+)\s*:=', stripped)
        for m in reassign_match:
            name = m.group(1)
            reassigned.append((name, i))

    # Check if any reassigned variable was not declared first
    for name, line_num in reassigned:
        if name not in declared:
            result.issues.append(LintIssue(
                line=line_num, column=1,
                text=f"Variable '{name}' reassigned with := but never declared with =.",
                severity="error", rule="undeclared_reassignment",
                fix_hint=f"Declare '{name}' first with '{name} = ...' or 'var {name} = na' before using ':='.",
            ))


def _check_reserved_words(code: str, lines: list[str], result: LintResult) -> None:
    """Rule: Reserved words cannot be used as variable names."""
    clean = _strip_strings(_strip_comments(code))

    for i, line in enumerate(clean.split('\n'), 1):
        # Match word = assignment (variable declaration)
        for m in re.finditer(r'\b(\w+)\s*(?<![<>!=])=(?!=)', line):
            name = m.group(1)
            if name in _RESERVED_WORDS and name not in ('var', 'varip'):
                # Skip if it's a legitimate keyword usage (var x =, type Foo =)
                if name in ('type', 'enum', 'method', 'import', 'export', 'global'):
                    continue
                result.issues.append(LintIssue(
                    line=i, column=m.start() + 1,
                    text=f"'{name}' is a reserved keyword and cannot be used as a variable name.",
                    severity="warning", rule="reserved_word",
                    fix_hint=f"Rename the variable to something other than '{name}'.",
                ))


def _check_empty_script(lines: list[str], result: LintResult) -> None:
    """Rule: Script with only comments/whitespace after version+declaration."""
    clean = _strip_comments('\n'.join(lines))
    # Remove version directive and declaration
    clean = re.sub(r'//@version=\d+', '', clean)
    clean = re.sub(r'\b(indicator|strategy|library)\s*\([^)]*\)', '', clean)
    clean = clean.strip()

    if not clean:
        result.issues.append(LintIssue(
            line=0, column=0,
            text="Script has no logic after declaration. Add plot(), strategy.entry(), or other statements.",
            severity="warning", rule="empty_script",
            fix_hint="Add some logic to your script, like: plot(close)",
        ))


def _check_plot_in_strategy(code: str, lines: list[str], result: LintResult) -> None:
    """Rule: Using plot() without indicator() is only valid in strategy() for debug."""
    clean = _strip_strings(_strip_comments(code))
    has_strategy = bool(re.search(r'\bstrategy\s*\(', clean))
    has_indicator = bool(re.search(r'\bindicator\s*\(', clean))

    if not has_indicator and not has_strategy:
        return  # caught by _check_script_declaration

    # strategy scripts can use plot() for debug, so this is just a warning
    if has_strategy and not has_indicator:
        plot_count = len(re.findall(r'\bplot\s*\(', clean))
        if plot_count > 5:
            result.issues.append(LintIssue(
                line=0, column=0,
                text=f"Strategy script has {plot_count} plot() calls. Consider using indicator() for visual-only scripts.",
                severity="info", rule="many_plots_in_strategy",
            ))


def _check_color_syntax(code: str, lines: list[str], result: LintResult) -> None:
    """Rule: color() is not a function — use color.new() or color.rgb()."""
    clean = _strip_strings(_strip_comments(code))

    # Match bare color() call (not color.new, color.rgb, etc.)
    for i, line in enumerate(clean.split('\n'), 1):
        if re.search(r'(?<!\.)\bcolor\s*\(', line):
            # Check it's not a known namespaced call
            if not re.search(r'color\.(new|rgb)\s*\(', line):
                result.issues.append(LintIssue(
                    line=i, column=1,
                    text="'color()' is not a valid function. Use color.new(base, transparency) or a named color like color.red.",
                    severity="error", rule="invalid_color_call",
                    fix_hint="Use color.new(color.red, 50) or just color.red.",
                ))


def _check_input_types(code: str, lines: list[str], result: LintResult) -> None:
    """Rule: input() without type specifier defaults to input float — use explicit types."""
    clean = _strip_strings(_strip_comments(code))

    # Match bare input() without type-specific variant
    for i, line in enumerate(clean.split('\n'), 1):
        stripped = line.strip()
        # Match input( but NOT input.int, input.float, input.string, etc.
        if re.search(r'(?<!\.)\binput\s*\(', stripped):
            if not re.search(r'input\.(int|float|string|bool|source|color|session|symbol|resolution|timeframe)', stripped):
                result.issues.append(LintIssue(
                    line=i, column=1,
                    text="Using bare input() — prefer explicit type: input.int(), input.float(), input.string(), etc.",
                    severity="warning", rule="bare_input",
                    fix_hint="Use input.int() or input.float() for clarity and type safety in v6.",
                ))


# ── Main entry point ─────────────────────────────────────────────────


def lint(code: str) -> LintResult:
    """Run all Tier 1 heuristic checks on Pine Script v6 code.

    Args:
        code: Complete Pine Script source code.

    Returns:
        LintResult with any issues found. Check result.has_errors for pass/fail.
    """
    result = LintResult()

    if not code or not code.strip():
        result.issues.append(LintIssue(
            line=0, column=0,
            text="Empty source code.",
            severity="error", rule="empty_source",
        ))
        return result

    lines = code.split('\n')

    # Core structural checks (highest priority)
    _check_version_directive(lines, result)
    _check_script_declaration(lines, result)
    _check_duplicate_declarations(lines, result)

    # If basic structure is broken, skip deeper checks
    if result.has_errors and any(
        i.rule in ("missing_version", "wrong_version") for i in result.errors
    ):
        return result

    # Syntax and namespace checks
    _check_v5_syntax(code, lines, result)
    _check_namespace_errors(code, lines, result)
    _check_bracket_balance(code, result)
    _check_assignment_before_declaration(code, lines, result)
    _check_reserved_words(code, lines, result)

    # Best practice checks (warnings/info only)
    _check_empty_script(lines, result)
    _check_plot_in_strategy(code, lines, result)
    _check_color_syntax(code, lines, result)
    _check_input_types(code, lines, result)

    return result
