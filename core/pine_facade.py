"""
core/pine_facade.py
------------------------------------------------------------------------------
Pine-facade compiler integration.
- PineFacadeCircuitBreaker with exponential backoff + jitter
- Shared httpx.AsyncClient (keep-alive, connection pooling)
- Remote pine-facade compilation (TradingView's official compiler)
- Response normalization + placeholder resolution
"""

from __future__ import annotations

import asyncio
import atexit
import json
import random
import re
import threading
import time
from typing import Optional

import httpx
from loguru import logger

from core.caches import get_cached_validation, set_cached_validation
from core.config import PINE_FACADE_FALLBACK_ENABLED, PINE_FACADE_TIMEOUT, PINE_FACADE_URL

# -----------------------------------------------------------------------------
# Pine-facade circuit breaker
# -----------------------------------------------------------------------------


class PineFacadeCircuitBreaker:
    """Circuit breaker for pine-facade API calls.

    Separates network failures (connection refused, timeout, DNS) from
    compiler responses (4xx/5xx HTTP with a valid body). Only network
    failures trip the breaker - compiler errors are expected and don't
    indicate the service is down.
    """

    def __init__(self, threshold: int = 10, cooldown: int = 60):
        self.network_failures: int = 0
        self.threshold = threshold
        self.cooldown = cooldown
        self.open_until: float = 0.0
        self.total_calls: int = 0
        self.total_network_errors: int = 0
        self.total_compiler_errors: int = 0
        self.total_successes: int = 0

    def is_open(self) -> bool:
        return time.time() < self.open_until

    def record_network_failure(self) -> None:
        """Record a network-level failure (timeout, connection refused, DNS).
        These indicate the service is unreachable and SHOULD trip the breaker.
        Uses exponential backoff with jitter: 60s, 120s, 240s... +/-15% jitter.
        """
        self.network_failures += 1
        self.total_network_errors += 1
        self.total_calls += 1
        if self.network_failures >= self.threshold:
            # Exponential backoff: base * 2^(failure_count - threshold)
            backoff_power = min(self.network_failures - self.threshold, 5)
            base_cooldown = self.cooldown * (2 ** backoff_power)
            # Cap at 10 minutes
            base_cooldown = min(base_cooldown, 600)
            # +/-15% jitter to avoid thundering herd
            jitter = base_cooldown * 0.15 * (random.random() * 2 - 1)
            actual_cooldown = max(30, base_cooldown + jitter)
            self.open_until = time.time() + actual_cooldown
            logger.warning(
                f"Pine-facade circuit OPEN for {actual_cooldown:.0f}s "
                f"({self.network_failures} consecutive network failures, "
                f"backoff power={backoff_power})"
            )

    def record_compiler_error(self) -> None:
        """Record a compiler response (HTTP 200 with errors in JSON body).
        These are EXPECTED and should NOT trip the breaker.
        """
        self.total_compiler_errors += 1
        self.total_calls += 1
        # Compiler errors don't accumulate - reset network counter
        self.network_failures = 0

    def record_success(self) -> None:
        """Record a successful compilation (HTTP 200, no errors)."""
        self.total_successes += 1
        self.total_calls += 1
        self.network_failures = 0
        self.open_until = 0.0

    def stats(self) -> dict:
        return {
            "circuit_open": self.is_open(),
            "open_until": self.open_until,
            "network_failures": self.network_failures,
            "total_calls": self.total_calls,
            "total_network_errors": self.total_network_errors,
            "total_compiler_errors": self.total_compiler_errors,
            "total_successes": self.total_successes,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
        }


pine_cb = PineFacadeCircuitBreaker()

# -----------------------------------------------------------------------------
# Shared HTTP client
# -----------------------------------------------------------------------------

_facade_http_client: Optional[httpx.AsyncClient] = None
_facade_client_lock = threading.Lock()


def get_facade_client() -> httpx.AsyncClient:
    """Lazy-init a shared httpx.AsyncClient for pine-facade calls.
    Thread-safe: uses _facade_client_lock to prevent concurrent initialization.
    """
    global _facade_http_client
    # Fast path: already initialized
    if _facade_http_client is not None and not _facade_http_client.is_closed:
        return _facade_http_client
    # Slow path: initialize under lock
    with _facade_client_lock:
        # Double-check after acquiring lock
        if _facade_http_client is not None and not _facade_http_client.is_closed:
            return _facade_http_client
        _facade_http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(float(PINE_FACADE_TIMEOUT), connect=5.0),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            headers={
                "Origin": "https://www.tradingview.com",
                "Referer": "https://www.tradingview.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "DNT": "1",
            },
        )
    return _facade_http_client


async def shutdown_http_client_async() -> None:
    """Properly close the httpx.AsyncClient from an async context."""
    global _facade_http_client
    if _facade_http_client and not _facade_http_client.is_closed:
        try:
            await _facade_http_client.aclose()
        except Exception as e:
            logger.debug(f"HTTP async shutdown error: {e}")
        finally:
            _facade_http_client = None


def shutdown_http_client() -> None:
    """Close the httpx.AsyncClient. Best-effort sync fallback for atexit."""
    global _facade_http_client
    if _facade_http_client and not _facade_http_client.is_closed:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(_facade_http_client.aclose())
            except Exception as e:
                logger.debug(f"HTTP shutdown (new loop) error: {e}")
            finally:
                _facade_http_client = None
            return
        try:
            if loop.is_running():
                loop.create_task(_facade_http_client.aclose())
            else:
                loop.run_until_complete(_facade_http_client.aclose())
        except Exception as e:
            logger.debug(f"HTTP shutdown error: {e}")
        finally:
            _facade_http_client = None


atexit.register(shutdown_http_client)

# -----------------------------------------------------------------------------
# Response normalization
# -----------------------------------------------------------------------------


def normalize_facade_response(raw: dict) -> dict:
    """Normalize /compile API response."""
    success = raw.get("success", False)

    result_obj = raw.get("result") or {}
    raw_errors = result_obj.get("errors", []) if isinstance(result_obj, dict) else []

    # /compile may also put errors at top level
    if not raw_errors and "errors" in raw:
        raw_errors = raw.get("errors", [])

    # Handle rejection shape (success=false with reason, result=null)
    if not success and not raw_errors:
        reason = raw.get("reason", "Unknown compilation failure")
        return {
            "success": False,
            "errors": [{"line": 0, "column": 0, "text": reason, "type": "error"}],
            "warnings": [],
            "meta": {},
            "raw_response": raw,
        }

    def normalize_error(e: dict) -> dict:
        text = e.get("text") or e.get("message") or e.get("msg") or str(e)
        # TradingView pine-facade returns template variables like {kind}, {fullName}, etc.
        # Step 1: Resolve from error object fields
        for key, val in e.items():
            if isinstance(val, (str, int, float)) and key not in ("line", "column", "col",
                "lineNumber", "type", "severity", "start", "end"):
                placeholder = f"{{{key}}}"
                if placeholder in text:
                    text = text.replace(placeholder, str(val))
        start = e.get("start") or {}
        return {
            "line": e.get("line")
            or e.get("lineNumber")
            or (start.get("line", 0) if isinstance(start, dict) else 0),
            "column": e.get("column")
            or e.get("col")
            or (start.get("column", 0) if isinstance(start, dict) else 0),
            "text": text,
            "type": e.get("type") or "error",
        }

    all_normalized = [
        normalize_error(e) if isinstance(e, dict) else {"line": 0, "column": 0, "text": str(e), "type": "error"}
        for e in raw_errors
    ]
    # Separate errors from warnings - warnings must NOT appear in errors list
    errors = [e for e in all_normalized if e.get("type") != "warning"]
    warnings = [e for e in all_normalized if e.get("type") == "warning"]

    # Meta from result object - extract useful fields
    meta = {}
    if isinstance(result_obj, dict):
        for key in ("variables", "functions", "types", "enums", "scopes"):
            if key in result_obj:
                meta[key] = result_obj[key]

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "meta": meta,
        "raw_response": raw,
    }


def enrich_error_with_code(errors: list[dict], code: str) -> list[dict]:
    """Resolve remaining {placeholder} vars in error text using source code context."""
    if not code:
        return errors
    code_lines = code.splitlines()
    placeholder_re = re.compile(r"\{(\w+)\}")

    for err in errors:
        text = err.get("text", "")
        if not placeholder_re.search(text):
            continue

        # Extract identifier/expression at error position from source code
        line_num = err.get("line", 0)
        col_num = err.get("column", 0)
        ident = ""
        if isinstance(line_num, int) and 0 < line_num <= len(code_lines):
            line_text = code_lines[line_num - 1]
            if isinstance(col_num, int) and 0 < col_num <= len(line_text):
                i = col_num - 1
                while i < len(line_text) and (line_text[i].isalnum() or line_text[i] in "_."):
                    ident += line_text[i]
                    i += 1

        # Resolve known placeholders using extracted context
        replacements = {
            "identifier": ident or "value",
            "name": ident or "value",
            "kind": "identifier",
            "fullName": ident or "value",
            "funId": ident or "function",
            "funName": ident or "function",
            "argDisplayName": ident or "argument",
            "argUserFriendlyRepresentation": ident or "value",
            "argumentType": "type",
            "currentTypeDocStr": "expected type",
            "typePostfix": "",
            "scope": "scope",
        }
        for ph_key, ph_val in replacements.items():
            text = text.replace(f"{{{ph_key}}}", ph_val)

        # Catch any remaining unknown {placeholders}
        text = placeholder_re.sub(lambda m: m.group(1), text)
        err["text"] = text
    return errors


# -----------------------------------------------------------------------------
# Local syntax validation (fallback when facade is unavailable)
# -----------------------------------------------------------------------------

_V4_COLORS = frozenset({
    'red', 'green', 'blue', 'white', 'black', 'yellow', 'orange',
    'purple', 'gray', 'silver', 'lime', 'maroon', 'navy', 'teal',
    'olive', 'aqua', 'fuchsia',
})

_V5_TA_FUNCTIONS = frozenset({
    'ema', 'sma', 'rsi', 'macd', 'atr', 'bb', 'stoch', 'wma', 'hma',
    'vwap', 'crossover', 'crossunder', 'highest', 'lowest', 'barssince',
    'valuewhen', 'linreg', 'mom', 'cum', 'change', 'pivothigh', 'pivotlow',
    'supertrend', 'correlation', 'percentrank', 'dmi', 'stdev', 'variance',
    'rising', 'falling', 'alma', 'kama', 'swma',
})


def _clean_code(code: str) -> str:
    """Strip string literals and comments for syntax analysis."""
    from formatters.errors import strip_string_literals
    stripped = strip_string_literals(code)
    lines = []
    for line in stripped.splitlines():
        idx = line.find('//')
        if idx >= 0:
            line = line[:idx]
        lines.append(line)
    return '\n'.join(lines)


def local_syntax_check(code: str) -> dict:
    """Local PineScript v6 syntax validator (fallback when facade is down).

    Catches common PineScript-specific errors without remote compilation.
    Returns the same dict format as call_pine_facade().
    """
    errors: list[dict] = []
    warnings: list[dict] = []
    raw_lines = code.splitlines()
    cleaned = _clean_code(code)
    clean_lines = cleaned.splitlines()

    # 1. Missing //@version=6 header
    has_v6 = any('//@version=6' in line for line in raw_lines[:10])
    other_v: tuple[int, str] | None = None
    for i, line in enumerate(raw_lines[:10], 1):
        m = re.match(r'//@version=(\d)', line.strip())
        if m:
            if m.group(1) != '6':
                other_v = (i, m.group(1))
            break
    if not has_v6:
        if other_v:
            errors.append({
                "line": other_v[0], "column": 0,
                "text": f"Version {other_v[1]} detected. This server requires //@version=6",
                "type": "error",
            })
        else:
            errors.append({
                "line": 1, "column": 0,
                "text": "Missing //@version=6 header. First line must be //@version=6",
                "type": "error",
            })

    # 2. v5 syntax: study() instead of indicator()
    for i, line in enumerate(clean_lines, 1):
        if re.search(r'\bstudy\s*\(', line):
            errors.append({
                "line": i, "column": 0,
                "text": "study() is v5 syntax. Use indicator() in v6",
                "type": "error",
            })
            break

    # 3. Bare v4 color constants (red instead of color.red)
    for i, line in enumerate(clean_lines, 1):
        m = re.search(r'\bcolor\s*=\s*(\w+)', line)
        if m and m.group(1) in _V4_COLORS:
            errors.append({
                "line": i, "column": 0,
                "text": f"Bare color '{m.group(1)}' is v4 syntax. Use color.{m.group(1)} in v6",
                "type": "error",
            })
        m2 = re.search(r'\bbgcolor\s*\(\s*(\w+)', line)
        if m2 and m2.group(1) in _V4_COLORS:
            errors.append({
                "line": i, "column": 0,
                "text": f"Bare color '{m2.group(1)}' is v4 syntax. Use color.{m2.group(1)} in v6",
                "type": "error",
            })

    # 4. Unclosed parentheses / brackets (overall balance)
    paren_depth = 0
    bracket_depth = 0
    for i, line in enumerate(clean_lines, 1):
        for char in line:
            if char == '(':
                paren_depth += 1
            elif char == ')':
                paren_depth -= 1
            elif char == '[':
                bracket_depth += 1
            elif char == ']':
                bracket_depth -= 1
            if paren_depth < 0:
                errors.append({
                    "line": i, "column": 0,
                    "text": "Unmatched closing parenthesis ')'",
                    "type": "error",
                })
                paren_depth = 0
            if bracket_depth < 0:
                errors.append({
                    "line": i, "column": 0,
                    "text": "Unmatched closing bracket ']'",
                    "type": "error",
                })
                bracket_depth = 0
    if paren_depth > 0:
        errors.append({
            "line": len(raw_lines), "column": 0,
            "text": f"Unclosed parenthesis - {paren_depth} '(' without matching ')'",
            "type": "error",
        })
    if bracket_depth > 0:
        errors.append({
            "line": len(raw_lines), "column": 0,
            "text": f"Unclosed bracket - {bracket_depth} '[' without matching ']'",
            "type": "error",
        })

    # 5. := assignment on first declaration (should use =)
    declared: set[str] = set()
    for i, line in enumerate(clean_lines, 1):
        decl = re.match(r'\s*(?:var\s+|varip\s+)?(?:\w+\s+)?(\w+)\s*=(?![=>])', line)
        if decl:
            declared.add(decl.group(1))
        reassign = re.match(r'\s*(\w+)\s*:=', line)
        if reassign and reassign.group(1) not in declared:
            errors.append({
                "line": i, "column": 0,
                "text": f"'{reassign.group(1)}' uses := before being declared with =. Use = for first declaration",
                "type": "error",
            })

    # 6. na comparison with == instead of na() function
    for i, line in enumerate(clean_lines, 1):
        if re.search(r'==\s*na\b', line) or re.search(r'\bna\s*==', line):
            errors.append({
                "line": i, "column": 0,
                "text": "Use na(x) instead of x == na for na comparison",
                "type": "error",
            })
        if re.search(r'!=\s*na\b', line) or re.search(r'\bna\s*!=', line):
            errors.append({
                "line": i, "column": 0,
                "text": "Use not na(x) instead of x != na for na comparison",
                "type": "error",
            })

    # 7. array.get without bounds check (warning)
    for i, line in enumerate(clean_lines, 1):
        if re.search(r'array\.get\s*\(', line):
            warnings.append({
                "line": i, "column": 0,
                "text": "array.get() without bounds check may cause runtime error. Check index < array.size() first",
                "type": "warning",
            })

    # 8. varip misuse (warning) - varip is for input() values
    for i, line in enumerate(clean_lines, 1):
        m = re.match(r'\s*varip\s+(\w+)\s*=\s*(.+)', line)
        if m and 'input(' not in m.group(2):
            warnings.append({
                "line": i, "column": 0,
                "text": "varip is designed for input() values. Complex expressions may not work correctly",
                "type": "warning",
            })

    # 9. strategy.* functions without strategy() declaration
    has_strategy_decl = bool(re.search(r'\bstrategy\s*\(', cleaned))
    has_strategy_call = bool(re.search(r'\bstrategy\.(entry|exit|close|order|cancel)', cleaned))
    if has_strategy_call and not has_strategy_decl:
        errors.append({
            "line": 0, "column": 0,
            "text": "strategy.* functions used without strategy() declaration. Use strategy() instead of indicator()",
            "type": "error",
        })

    # 10. transp= parameter (v5, removed in v6)
    for i, line in enumerate(clean_lines, 1):
        if re.search(r',\s*transp\s*=', line):
            warnings.append({
                "line": i, "column": 0,
                "text": "transp= parameter removed in v6. Use color.new(color, transparency) instead",
                "type": "warning",
            })

    # 11. Bare TA functions without ta. namespace (v5 syntax)
    for i, line in enumerate(clean_lines, 1):
        for fn in _V5_TA_FUNCTIONS:
            if re.search(rf'(?<![\w.])\b{fn}\s*\(', line):
                errors.append({
                    "line": i, "column": 0,
                    "text": f"Bare '{fn}()' is v5 syntax. Use ta.{fn}() in v6",
                    "type": "error",
                })
                break

    return {
        "success": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "meta": {"fallback": "syntax_check"},
        "raw_response": {},
    }


async def check_facade_health() -> dict:
    """Check if the pine-facade compiler is reachable.

    Returns:
        {"available": bool, "url": str, "latency_ms": float, "error": str | None}
    """
    start = time.monotonic()
    try:
        client = get_facade_client()
        resp = await client.get(PINE_FACADE_URL, timeout=httpx.Timeout(3.0, connect=2.0))
        latency = (time.monotonic() - start) * 1000
        return {
            "available": True,
            "url": PINE_FACADE_URL,
            "latency_ms": round(latency, 1),
            "status_code": resp.status_code,
            "circuit_open": pine_cb.is_open(),
            "error": None,
        }
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return {
            "available": False,
            "url": PINE_FACADE_URL,
            "latency_ms": round(latency, 1),
            "status_code": None,
            "circuit_open": pine_cb.is_open(),
            "error": f"{type(e).__name__}: {e}",
        }


# -----------------------------------------------------------------------------
# Main facade call
# -----------------------------------------------------------------------------


async def call_pine_facade(code: str) -> dict:
    """POST code to pine-facade compiler. Returns normalized response dict.

    Checks content-hash cache FIRST (avoids network on repeat calls).
    Then compiles via TradingView's remote pine-facade compiler.

    Args:
        code: PineScript source code to validate.

    Returns:
        {
            "success": bool,
            "errors": [{"line", "column", "text", "type"}, ...],
            "warnings": [{"line", "column", "text"}, ...],
            "meta": dict,
            "raw_response": dict
        }
    """
    from formatters.errors import sanitize_text

    # Guard: reject empty/whitespace-only code before any work
    if not code or not code.strip():
        return {
            "success": False,
            "errors": [{"line": 0, "column": 0, "text": "No code provided - empty source", "type": "error"}],
            "warnings": [],
            "meta": {},
            "raw_response": {},
        }

    # Fast path: check content-hash cache BEFORE network call.
    # Use sanitized code as cache key to match store key (avoids mismatch from whitespace/control chars)
    code_key = sanitize_text(code)
    cached = get_cached_validation(code_key)
    if cached:
        return cached

    if pine_cb.is_open():
        logger.info("Circuit breaker open, falling back to local syntax check")
        if PINE_FACADE_FALLBACK_ENABLED:
            result = local_syntax_check(code_key)
            set_cached_validation(code_key, json.dumps(result))
            return result
        return {
            "success": False,
            "errors": [
                {
                    "line": 0,
                    "column": 0,
                    "text": (
                        "Remote compiler temporarily unavailable (circuit breaker open). "
                        "The service will retry automatically."
                    ),
                    "type": "error",
                }
            ],
            "warnings": [],
            "meta": {"fallback": "circuit_breaker_open"},
            "raw_response": {},
        }

    code = sanitize_text(code)

    try:
        client = get_facade_client()
        resp = await client.post(
            PINE_FACADE_URL,
            files={"source": (None, code)},
        )

        if resp.status_code == 403:
            logger.warning(
                f"pine-facade 403 - headers: {dict(resp.headers)} | "
                f"body: {resp.text[:200]}"
            )
            if PINE_FACADE_FALLBACK_ENABLED:
                result = local_syntax_check(code)
                set_cached_validation(code, json.dumps(result))
                return result
            return {
                "success": False,
                "errors": [
                    {
                        "line": 0,
                        "column": 0,
                        "text": (
                            "Remote compiler returned HTTP 403 (access denied). "
                            "Validate in TradingView's Pine Editor for full compilation."
                        ),
                        "type": "http",
                    }
                ],
                "warnings": [],
                "meta": {},
                "raw_response": {
                    "http_status": resp.status_code,
                    "body": resp.text[:200],
                },
            }

        if resp.status_code in (502, 503, 504):
            pine_cb.record_network_failure()
            if PINE_FACADE_FALLBACK_ENABLED:
                result = local_syntax_check(code)
                set_cached_validation(code, json.dumps(result))
                return result
            return {
                "success": False,
                "errors": [
                    {
                        "line": 0,
                        "column": 0,
                        "text": (
                            f"Remote compiler returned HTTP {resp.status_code} "
                            "(service unavailable). Try again shortly."
                        ),
                        "type": "http",
                    }
                ],
                "warnings": [],
                "meta": {},
                "raw_response": {
                    "http_status": resp.status_code,
                    "body": resp.text[:200],
                },
            }

        if resp.status_code != 200:
            if resp.status_code in (400, 429):
                try:
                    data = resp.json()
                    normalized = normalize_facade_response(data)
                    set_cached_validation(code, json.dumps(normalized))
                    return normalized
                except Exception as e:
                    logger.debug(f"Cache write failed: {e}")
            else:
                pine_cb.record_network_failure()

            return {
                "success": False,
                "errors": [
                    {
                        "line": 0,
                        "column": 0,
                        "text": f"HTTP {resp.status_code}: {resp.text[:200]}",
                        "type": "http",
                    }
                ],
                "warnings": [],
                "meta": {},
                "raw_response": {
                    "http_status": resp.status_code,
                    "body": resp.text[:500],
                },
            }

        # HTTP 200 - parse the response
        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.error(
                f"pine-facade returned non-JSON (HTTP {resp.status_code}): "
                f"{resp.text[:200]}"
            )
            return {
                "success": False,
                "errors": [
                    {
                        "line": 0,
                        "column": 0,
                        "text": "Compiler returned non-JSON response",
                        "type": "error",
                    }
                ],
                "warnings": [],
                "meta": {},
                "raw_response": {"raw_text": resp.text[:500]},
            }
        normalized = normalize_facade_response(data)

        if normalized["success"]:
            pine_cb.record_success()
        else:
            pine_cb.record_compiler_error()

        set_cached_validation(code, json.dumps(normalized))
        return normalized

    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.PoolTimeout,
        httpx.WriteTimeout,
        OSError,
    ) as e:
        pine_cb.record_network_failure()
        logger.error(f"[call_pine_facade] network error: {e}")
        if PINE_FACADE_FALLBACK_ENABLED:
            result = local_syntax_check(code)
            set_cached_validation(code, json.dumps(result))
            return result
        return {
            "success": False,
            "errors": [
                {
                    "line": 0,
                    "column": 0,
                    "text": f"Remote compiler unreachable ({type(e).__name__}). Check your network connection.",
                    "type": "http",
                }
            ],
            "warnings": [],
            "meta": {},
            "raw_response": {"exception": str(e)},
        }
    except Exception as e:
        logger.error(f"[call_pine_facade] unexpected: {e}")
        return {
            "success": False,
            "errors": [
                {
                    "line": 0,
                    "column": 0,
                    "text": f"Remote compiler error ({type(e).__name__}): {e}",
                    "type": "error",
                }
            ],
            "warnings": [],
            "meta": {},
            "raw_response": {"exception": str(e)},
        }
