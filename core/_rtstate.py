"""HTTP/SSE transport runtime state - request ID binding only.

No code logging. No script storage. No telemetry.
"""

from __future__ import annotations

import contextvars
import hashlib
import re

_current_rid: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_rid", default="local"
)


_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def bind_rid(identifier: str) -> str:
    """Bind a request ID for the current async context (SSE auth only)."""
    if not identifier:
        rid = "anonymous"
    elif len(identifier) > 32 or _SAFE_NAME_RE.search(identifier):
        rid = "r_" + hashlib.sha256(identifier.encode()).hexdigest()[:12]
    else:
        rid = _SAFE_NAME_RE.sub("_", identifier).strip("_") or "anonymous"
    _current_rid.set(rid)
    return rid


def get_rid() -> str:
    return _current_rid.get()
