"""
Health / readiness endpoints.

``/health/live``  — lightweight liveness probe (always 200).
``/health/ready`` — deep readiness check: DB, Redis, Celery, provider
                    config, recommendation storage path.
                    Returns **503** when one or more checks fail so
                    load-balancers can act on it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.shared.config.settings import settings

router = APIRouter(tags=["Health"])
logger = logging.getLogger("aisep.health")


@router.get("/health")
def health_check() -> dict:
    """Backwards-compatible simple health."""
    return {"status": "ok"}


@router.get("/health/live")
def liveness() -> dict:
    """Kubernetes-style liveness probe."""
    return {"status": "alive"}


@router.get("/health/ready")
def readiness():
    """
    Deep readiness probe.

    Returns **200** with ``ready: true`` when all subsystems are reachable,
    or **503** with ``ready: false`` + per-component status when something
    is down (so the caller / load-balancer can inspect which subsystem failed).
    """
    checks: Dict[str, Any] = {}

    # ── 1. Database ─────────────────────────────────────────────────
    checks["database"] = _check_database()

    # ── 2. Redis broker ─────────────────────────────────────────────
    checks["redis_broker"] = _check_redis(settings.CELERY_BROKER_URL, "broker")

    # ── 3. Redis result backend ─────────────────────────────────────
    checks["redis_backend"] = _check_redis(
        settings.CELERY_RESULT_BACKEND, "result backend")

    # ── 4. Celery worker (inspect active workers via broker) ────────
    checks["celery_workers"] = _check_celery_workers()

    # ── 5. Provider API keys configured ─────────────────────────────
    checks["providers"] = _check_providers()

    # ── 6. Recommendation storage directory ─────────────────────────
    checks["recommendation_storage"] = _check_recommendation_storage()

    all_ok = all(c.get("ok", False) for c in checks.values())
    body = {"ready": all_ok, "checks": checks}

    if all_ok:
        return body
    return JSONResponse(status_code=503, content=body)


# ── Individual probes ───────────────────────────────────────────────

def _check_database() -> dict:
    try:
        from sqlmodel import text
        from src.shared.persistence.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:
        logger.warning("DB readiness check failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _check_redis(url: str, label: str = "redis") -> dict:
    try:
        import redis as redis_lib

        client = redis_lib.Redis.from_url(url, socket_connect_timeout=2)
        client.ping()
        client.close()
        return {"ok": True}
    except Exception as exc:
        logger.warning("Redis readiness check (%s) failed: %s", label, exc)
        return {"ok": False, "error": str(exc)}


def _check_celery_workers() -> dict:
    try:
        from src.celery_app import celery_app

        inspector = celery_app.control.inspect(timeout=2)
        active = inspector.active_queues()
        if active:
            return {"ok": True, "workers": len(active)}
        return {"ok": False, "error": "No active Celery workers found."}
    except Exception as exc:
        logger.warning("Celery readiness check failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def _check_providers() -> dict:
    configured = []
    missing = []
    if settings.GEMINI_API_KEY:
        configured.append("gemini")
    else:
        missing.append("gemini")

    if settings.TAVILY_API_KEY:
        configured.append("tavily")
    else:
        missing.append("tavily")

    # At least Gemini must be present for core flows
    ok = "gemini" in configured
    result: dict = {"ok": ok, "configured": configured}
    if missing:
        result["missing"] = missing
    return result


def _check_recommendation_storage() -> dict:
    backend = (settings.RECOMMENDATION_BACKEND or "db").lower().strip()
    if backend == "db":
        return _check_recommendation_db()
    # Legacy filesystem backend
    storage = Path(settings.STORAGE_DIR) / "recommendations"
    try:
        if not storage.is_dir():
            return {"ok": False, "backend": "filesystem",
                    "error": f"Directory does not exist: {storage}"}
        probe = storage / ".health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"ok": True, "backend": "filesystem", "path": str(storage)}
    except Exception as exc:
        return {"ok": False, "backend": "filesystem", "error": str(exc)}


def _check_recommendation_db() -> dict:
    """Verify recommendation tables are reachable via the shared DB engine."""
    try:
        from sqlmodel import text
        from src.shared.persistence.db import engine as db_engine

        with db_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "backend": "db"}
    except Exception as exc:
        logger.warning("Recommendation DB readiness check failed: %s", exc)
        return {"ok": False, "backend": "db", "error": str(exc)}
