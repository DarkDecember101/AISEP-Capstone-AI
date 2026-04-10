"""
Context manager & async context manager for tracking external provider calls.

Usage (sync):
    with track_provider("gemini"):
        result = client.generate_structured(...)

Usage (async):
    async with track_provider_async("tavily_search"):
        result = await tavily_client.search(...)
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager, contextmanager

from src.shared.observability.metrics import (
    PROVIDER_CALLS_TOTAL,
    PROVIDER_CALL_DURATION,
)


@contextmanager
def track_provider(provider: str):
    """Synchronous context manager for provider call instrumentation."""
    start = time.monotonic()
    try:
        yield
        PROVIDER_CALLS_TOTAL.labels(provider=provider, outcome="success").inc()
    except Exception as exc:
        _label = _classify_outcome(exc)
        PROVIDER_CALLS_TOTAL.labels(provider=provider, outcome=_label).inc()
        raise
    finally:
        PROVIDER_CALL_DURATION.labels(provider=provider).observe(
            time.monotonic() - start
        )


@asynccontextmanager
async def track_provider_async(provider: str):
    """Async context manager for provider call instrumentation."""
    start = time.monotonic()
    try:
        yield
        PROVIDER_CALLS_TOTAL.labels(provider=provider, outcome="success").inc()
    except Exception as exc:
        _label = _classify_outcome(exc)
        PROVIDER_CALLS_TOTAL.labels(provider=provider, outcome=_label).inc()
        raise
    finally:
        PROVIDER_CALL_DURATION.labels(provider=provider).observe(
            time.monotonic() - start
        )


def _classify_outcome(exc: Exception) -> str:
    """Map exception type to a bounded label value."""
    name = type(exc).__name__.lower()
    if "quota" in name:
        return "quota_exceeded"
    if "timeout" in name:
        return "timeout"
    return "error"
