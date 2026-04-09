"""
Configurable per-endpoint rate limiter for AISEP AI Service.

Uses an in-memory **token-bucket** algorithm — simple, predictable, and
sufficient for a single-process FastAPI deployment.  For multi-process /
multi-node deployments a Redis-backed counter would replace the inner store;
the FastAPI dependency interface stays identical.

Configuration (via env / settings)
----------------------------------
``RATE_LIMIT_ENABLED``    – master switch (default "true")
``RATE_LIMIT_EVAL_RPM``   – max requests/min for evaluation submit (default 20)
``RATE_LIMIT_CHAT_RPM``   – max requests/min for investor-agent chat (default 30)
``RATE_LIMIT_STREAM_RPM`` – max requests/min for investor-agent stream (default 30)
``RATE_LIMIT_RECO_RPM``   – max requests/min for recommendation reads (default 60)

Usage in a router
-----------------
::

    from src.shared.rate_limit.limiter import RateLimitDep

    @router.post("/")
    async def submit(
        ...,
        _rl=Depends(RateLimitDep("eval", settings.RATE_LIMIT_EVAL_RPM)),
    ):
        ...

The dependency raises ``APIError(429)`` with the standard error envelope
when the bucket is empty.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, Tuple

from fastapi import Request

from src.shared.config.settings import settings
from src.shared.error_response import APIError

logger = logging.getLogger("aisep.rate_limit")

# ── Token-bucket store ──────────────────────────────────────────────
# key -> (tokens_remaining, last_refill_timestamp)
_buckets: Dict[str, Tuple[float, float]] = {}


def _is_enabled() -> bool:
    val = getattr(settings, "RATE_LIMIT_ENABLED", "true")
    return str(val).lower().strip() in ("true", "1", "yes")


def _consume(bucket_key: str, rpm: int) -> bool:
    """
    Try to consume one token from *bucket_key*.

    Returns ``True`` if the request is allowed, ``False`` if rate-limited.
    """
    if rpm <= 0:
        return True  # 0 = unlimited

    now = time.monotonic()

    if bucket_key not in _buckets:
        # First request ever for this bucket — start full (minus the one we consume)
        _buckets[bucket_key] = (float(rpm) - 1.0, now)
        return True

    tokens, last_refill = _buckets[bucket_key]

    # Refill: add tokens proportional to elapsed time
    elapsed = now - last_refill
    refill = elapsed * (rpm / 60.0)
    tokens = min(rpm, tokens + refill)  # cap at bucket size

    if tokens < 1.0:
        return False

    _buckets[bucket_key] = (tokens - 1.0, now)
    return True


def reset_buckets() -> None:
    """Clear all buckets — useful in tests."""
    _buckets.clear()


class RateLimitDep:
    """
    FastAPI dependency that enforces a per-client-IP token-bucket rate limit.

    Instantiate once per endpoint with a logical *name* and *rpm* limit,
    then use as ``Depends(instance)``.
    """

    def __init__(self, name: str, rpm: int | str = 60) -> None:
        self.name = name
        self.rpm = int(rpm) if rpm else 60

    async def __call__(self, request: Request) -> None:  # noqa: D401
        if not _is_enabled():
            return

        client_ip = request.client.host if request.client else "unknown"
        bucket_key = f"{self.name}:{client_ip}"

        if not _consume(bucket_key, self.rpm):
            logger.warning(
                "Rate limit exceeded bucket=%s client=%s rpm=%d",
                self.name, client_ip, self.rpm,
            )
            raise APIError(
                status_code=429,
                code="RATE_LIMIT_EXCEEDED",
                message="Too many requests. Please slow down and retry shortly.",
                retryable=True,
            )
