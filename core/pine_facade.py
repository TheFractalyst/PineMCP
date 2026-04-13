"""
core/pine_facade.py
──────────────────────────────────────────────────────────────────────────────
Pine-facade compiler integration.
- PineFacadeCircuitBreaker with exponential backoff + jitter
- Shared httpx.AsyncClient (keep-alive, connection pooling)
- Dual-tier validation: local linter → remote pine-facade
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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from pine_linter import LintResult

import httpx
from loguru import logger

from core.caches import get_cached_validation, set_cached_validation
from core.config import PINE_FACADE_TIMEOUT, PINE_FACADE_URL

# ─────────────────────────────────────────────────────────────────────────────
# Pine-facade circuit breaker
# ─────────────────────────────────────────────────────────────────────────────


class PineFacadeCircuitBreaker:
    """Circuit breaker for pine-facade API calls.

    Separates network failures (connection refused, timeout, DNS) from
    compiler responses (4xx/5xx HTTP with a valid body). Only network
    failures trip the breaker — compiler errors are expected and don't
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
        Uses exponential backoff with jitter: 60s, 120s, 240s... ±15% jitter.
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
            # ±15% jitter to avoid thundering herd
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
        # Compiler errors don't accumulate — reset network counter
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
            "network_failures": self.network_failures,
            "total_calls": self.total_calls,
            "total_network_errors": self.total_network_errors,
            "total_compiler_errors": self.total_compiler_errors,
            "total_successes": self.total_successes,
            "threshold": self.threshold,
            "cooldown": self.cooldown,
        }


pine_cb = PineFacadeCircuitBreaker()

# ─────────────────────────────────────────────────────────────────────────────
# Shared HTTP client
# ─────────────────────────────────────────────────────────────────────────────

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


def shutdown_http_client() -> None:
    global _facade_http_client
    if _facade_http_client and not _facade_http_client.is_closed:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop — create a fresh one to close the client
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

# ─────────────────────────────────────────────────────────────────────────────
# Response normalization
# ─────────────────────────────────────────────────────────────────────────────


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
        return {
            "line": e.get("line")
            or e.get("lineNumber")
            or e.get("start", {}).get("line", 0),
            "column": e.get("column")
            or e.get("col")
            or e.get("start", {}).get("column", 0),
            "text": text,
            "type": e.get("type") or "error",
        }

    all_normalized = [normalize_error(e) for e in raw_errors if isinstance(e, dict)]
    # Separate errors from warnings — warnings must NOT appear in errors list
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


# ─────────────────────────────────────────────────────────────────────────────
# Main facade call
# ─────────────────────────────────────────────────────────────────────────────


async def call_pine_facade(code: str, *, skip_lint: bool = False) -> dict:
    """POST code to pine-facade compiler. Returns normalized response dict.

    Checks content-hash cache FIRST (avoids linter + network on repeat calls).
    Then runs local Tier 1 linter, then attempts remote compile.
    If remote fails, returns local linter results as fallback.

    Args:
        code: PineScript source code to validate.
        skip_lint: If True, skip local linter (caller already ran it).

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
    from pine_linter import lint as _pine_lint

    # Guard: reject empty/whitespace-only code before any work
    if not code or not code.strip():
        return {
            "success": False,
            "errors": [{"line": 0, "column": 0, "text": "No code provided — empty source", "type": "error"}],
            "warnings": [],
            "meta": {},
            "raw_response": {},
        }

    # Fast path: check content-hash cache BEFORE running linter or network call.
    cached = get_cached_validation(code)
    if cached:
        return cached

    # Tier 1: Run local linter (instant, always available)
    local_result = _pine_lint(code) if not skip_lint else None

    # Lazy linter: ensures local_result is populated when needed for fallback paths.
    def _ensure_lint() -> LintResult:
        nonlocal local_result
        if local_result is None:
            local_result = _pine_lint(code)
        return local_result

    if pine_cb.is_open():
        # Remote unavailable — return local linter results as fallback
        logger.info("Circuit breaker open, returning local linter results")
        lint_dict = _ensure_lint().to_dict()
        lint_dict["meta"]["fallback"] = "local_linter_tier1"
        lint_dict["meta"]["note"] = "Remote compiler unavailable. Local linter catches ~50% of errors."
        return lint_dict

    code = sanitize_text(code)

    try:
        client = get_facade_client()
        resp = await client.post(
            PINE_FACADE_URL,
            files={"source": (None, code)},
        )

        if resp.status_code == 403:
            logger.warning(
                f"pine-facade 403 — headers: {dict(resp.headers)} | "
                f"body: {resp.text[:200]}"
            )
            pine_cb.record_network_failure()
            lint_dict = _ensure_lint().to_dict()
            lint_dict["meta"]["fallback"] = "local_linter_tier1"
            lint_dict["meta"]["note"] = (
                "Remote compiler returned HTTP 403 (access denied). "
                "Showing local linter results — catches ~50% of common errors. "
                "Validate in TradingView's Pine Editor for full compilation."
            )
            lint_dict["raw_response"] = {
                "http_status": resp.status_code,
                "body": resp.text[:200],
            }
            return lint_dict

        if resp.status_code in (502, 503, 504):
            pine_cb.record_network_failure()
            lint_dict = _ensure_lint().to_dict()
            lint_dict["meta"]["fallback"] = "local_linter_tier1"
            lint_dict["meta"]["note"] = (
                f"Remote compiler returned HTTP {resp.status_code} (service unavailable). "
                "Showing local linter results."
            )
            lint_dict["raw_response"] = {
                "http_status": resp.status_code,
                "body": resp.text[:200],
            }
            return lint_dict

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

        # HTTP 200 — parse the response
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
        lint_dict = _ensure_lint().to_dict()
        lint_dict["meta"]["fallback"] = "local_linter_tier1"
        lint_dict["meta"]["note"] = (
            f"Remote compiler unreachable ({type(e).__name__}). "
            "Showing local linter results — catches ~50% of common errors."
        )
        lint_dict["raw_response"] = {"exception": str(e)}
        return lint_dict
    except Exception as e:
        logger.error(f"[call_pine_facade] unexpected: {e}")
        lint_dict = _ensure_lint().to_dict()
        lint_dict["meta"]["fallback"] = "local_linter_tier1"
        lint_dict["meta"]["note"] = (
            f"Remote compiler error ({type(e).__name__}). "
            "Showing local linter results."
        )
        lint_dict["raw_response"] = {"exception": str(e)}
        return lint_dict
