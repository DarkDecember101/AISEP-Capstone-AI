"""
Correlation / Request-ID middleware.

Assigns a unique correlation_id to every inbound request and makes it
available via ``get_correlation_id()`` throughout the call stack (using
a contextvars.ContextVar).

The correlation_id is also returned in the ``X-Correlation-Id`` response
header so .NET callers can log it for end-to-end tracing.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

_correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")

HEADER_NAME = "X-Correlation-Id"

# Allowed characters for a caller-supplied correlation id:
# alphanumeric, hyphens, underscores, dots, colons (common in .NET trace ids).
_CID_ALLOWED = re.compile(r"^[a-zA-Z0-9\-_.:]{1,128}$")


def get_correlation_id() -> str:
    """Return the correlation id for the current request (empty outside request)."""
    return _correlation_id_ctx.get()


def _sanitize_correlation_id(raw: str) -> str | None:
    """Return the value if it matches the safe pattern, else ``None``."""
    stripped = raw.strip()
    if stripped and _CID_ALLOWED.match(stripped):
        return stripped
    return None


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Inject / propagate a correlation id on every request."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Prefer caller-supplied header (if it passes sanity check);
        # fall back to a fresh uuid.
        incoming = request.headers.get(HEADER_NAME, "")
        cid = _sanitize_correlation_id(incoming) or uuid.uuid4().hex

        # Use token-based reset so ContextVar is always cleaned up,
        # even if the downstream handler raises.
        token = _correlation_id_ctx.set(cid)
        try:
            response: Response = await call_next(request)
            response.headers[HEADER_NAME] = cid
            return response
        finally:
            _correlation_id_ctx.reset(token)
