"""
Reusable internal-auth dependency for FastAPI.

Usage::

    from src.shared.auth import require_internal_auth

    @router.post("/internal/...")
    async def my_endpoint(
        ...,
        _auth: None = Depends(require_internal_auth),
    ):
        ...

Behaviour
---------
* If ``REQUIRE_INTERNAL_AUTH`` is ``False`` (default for local dev),
  the dependency is a no-op — all requests pass.
* If ``True``, the caller MUST send ``X-Internal-Token`` header whose
  value matches ``AISEP_INTERNAL_TOKEN``.  A mismatch returns 401 with
  the standard APIError envelope.
"""

from __future__ import annotations

import hmac

from fastapi import Header

from src.shared.config.settings import settings
from src.shared.error_response import APIError


async def require_internal_auth(
    x_internal_token: str | None = Header(
        default=None, alias="X-Internal-Token"
    ),
) -> None:
    """FastAPI dependency — raises APIError(401) on auth failure."""
    if not settings.REQUIRE_INTERNAL_AUTH:
        return  # auth disabled for local dev

    expected = settings.AISEP_INTERNAL_TOKEN
    if not expected:
        raise APIError(
            status_code=500,
            code="AUTH_NOT_CONFIGURED",
            message="AISEP_INTERNAL_TOKEN is required but not set on server.",
        )

    if not x_internal_token or not hmac.compare_digest(
        x_internal_token.encode(), expected.encode()
    ):
        raise APIError(
            status_code=401,
            code="AUTH_FAILED",
            message="Invalid or missing internal token.",
        )
