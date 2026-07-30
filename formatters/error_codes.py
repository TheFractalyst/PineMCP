"""
formatters/error_codes.py
------------------------------------------------------------------------------
TradingView Pine compiler error code classifications.

Pine-facade returns error codes (CE10015, CE10123, etc.) but never
explains what they mean. This map gives the AI agent enough context
to understand the error category and take action.

Codes are harvested from live pine-facade probes (2026-07-28).
CE = Compile Error, CW = Compile Warning.
"""

# Error code -> (category, description, common_cause)
ERROR_CODES: dict[str, tuple[str, str, str]] = {
    # --- Syntax errors (CE100xx) ---
    "CE10015": ("syntax", "Missing closing parenthesis", "Unclosed '(' — check for missing ')'"),
    "CE10016": ("syntax", "Extra closing parenthesis", "Unmatched ')' — check for extra ')'"),
    "CE10095": ("syntax", "Variable redeclaration", "Variable already declared — use ':=' to reassign, not '='"),
    # --- Statement errors (CE101xx) ---
    "CE10115": ("args", "Too many arguments", "Function called with more args than expected — check signature with pine_lookup"),
    "CE10123": ("type", "Type mismatch", "Wrong argument type — check parameter types with pine_lookup"),
    "CE10156": ("syntax", "Syntax error at input", "Unexpected token — check for incomplete expressions or operators"),
    "CE10161": ("syntax", "Invalid for statement", "Missing 'to <expression>' in for loop"),
    "CE10165": ("args", "Missing required parameter", "No value assigned to required parameter — check function signature"),
    # --- Reference errors (CE102xx) ---
    "CE10271": ("reference", "Unknown function or variable", "Name not found — check spelling, namespace, or use pine_search"),
    "CE10272": ("reference", "Undeclared identifier", "Variable used before declaration — declare with '=' first"),
}

# Warning codes
WARNING_CODES: dict[str, tuple[str, str]] = {
    "CW10002": ("conditional", "Function call in conditional may skip historical bars — assign to a global variable first"),
}


def describe_code(code: str) -> str:
    """Return a human-readable description for an error/warning code.

    Returns the code itself if unknown (so AI still sees the raw code).
    """
    if code in ERROR_CODES:
        category, desc, cause = ERROR_CODES[code]
        return f"{category}: {desc} — {cause}"
    if code in WARNING_CODES:
        category, desc = WARNING_CODES[code]
        return f"{category}: {desc}"
    return ""
