"""
Checkpoint provider for LangGraph investor-agent.

Returns either an **AsyncRedisSaver** (production-grade, shared across
instances, survives restarts) or a **MemorySaver** (local dev fallback).

Selection is driven by ``settings.CHECKPOINT_BACKEND``:

* ``"redis"`` → ``AsyncRedisSaver`` connected to ``CHECKPOINT_REDIS_URL``
* ``"memory"`` (default) → ``MemorySaver`` (in-process, volatile)

Usage::

    from src.shared.checkpoint import get_checkpointer
    checkpointer = get_checkpointer()          # call once at startup
    graph = builder.compile(checkpointer=checkpointer)
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from src.shared.config.settings import settings

logger = logging.getLogger("aisep.checkpoint")

# Module-level singleton so the entire app shares one checkpointer.
_checkpointer: BaseCheckpointSaver | None = None


def _build_ttl_config() -> dict[str, Any] | None:
    """Return the TTL dict expected by AsyncRedisSaver, or None."""
    ttl_minutes = settings.CHECKPOINT_TTL_MINUTES
    if ttl_minutes and ttl_minutes > 0:
        return {"default_ttl": ttl_minutes, "refresh_on_read": True}
    return None


def _create_redis_checkpointer() -> BaseCheckpointSaver:
    """Create an AsyncRedisSaver connected to CHECKPOINT_REDIS_URL."""
    # Import lazily so the heavy redis dependency isn't loaded unless needed.
    # type: ignore[import-untyped]
    from langgraph.checkpoint.redis import AsyncRedisSaver

    url = settings.CHECKPOINT_REDIS_URL
    if not url:
        raise RuntimeError(
            "CHECKPOINT_BACKEND is 'redis' but CHECKPOINT_REDIS_URL is empty."
        )

    ttl = _build_ttl_config()
    saver = AsyncRedisSaver(
        redis_url=url,
        ttl=ttl,
        checkpoint_prefix="aisep_ckpt",
        checkpoint_write_prefix="aisep_ckpt_w",
    )
    logger.info(
        "Redis checkpoint initialised url=%s ttl_minutes=%s",
        _redact_url(url),
        settings.CHECKPOINT_TTL_MINUTES,
    )
    return saver


def _redact_url(url: str) -> str:
    """Hide password from logs: redis://:secret@host → redis://***@host."""
    if "@" in url:
        scheme_end = url.index("://") + 3 if "://" in url else 0
        at_pos = url.index("@")
        return url[:scheme_end] + "***" + url[at_pos:]
    return url


def get_checkpointer() -> BaseCheckpointSaver:
    """
    Return the application-wide LangGraph checkpointer singleton.

    First call creates the instance; subsequent calls return the same one.
    Thread-safe in practice because it's called once at module-import time
    in the router.
    """
    global _checkpointer  # noqa: PLW0603

    if _checkpointer is not None:
        return _checkpointer

    backend = (settings.CHECKPOINT_BACKEND or "memory").lower().strip()

    if backend == "redis":
        try:
            _checkpointer = _create_redis_checkpointer()
        except Exception as exc:
            logger.error(
                "Failed to create Redis checkpointer — falling back to "
                "MemorySaver (state will NOT survive restart). Error: %s",
                exc,
            )
            _checkpointer = MemorySaver()
    elif backend == "memory":
        logger.info("Using in-process MemorySaver checkpoint (local dev mode).")
        _checkpointer = MemorySaver()
    else:
        logger.warning(
            "Unknown CHECKPOINT_BACKEND=%r — falling back to MemorySaver.", backend
        )
        _checkpointer = MemorySaver()

    return _checkpointer


async def setup_checkpointer() -> None:
    """Call asetup() on the checkpointer if it supports it (e.g. AsyncRedisSaver).

    This creates the required RediSearch indexes. Must be called once
    during application startup (inside an async context).
    """
    ckpt = get_checkpointer()
    if hasattr(ckpt, "asetup"):
        await ckpt.asetup()
        logger.info("Checkpointer asetup() completed — RediSearch indexes created.")


def reset_checkpointer() -> None:
    """Reset the singleton — used only in tests."""
    global _checkpointer  # noqa: PLW0603
    _checkpointer = None
