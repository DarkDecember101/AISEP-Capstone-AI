"""
Celery application configuration for AISEP AI.

Broker: Redis
Result backend: Redis (optional – DB is the real source of truth for EvaluationRun status)

Env vars (defined in Settings / .env):
    CELERY_BROKER_URL      – default redis://localhost:6379/0
    CELERY_RESULT_BACKEND  – default redis://localhost:6379/1 (optional, can be disabled)
"""

import os
from celery import Celery
from src.shared.config.settings import settings
from src.shared.logging.logger import configure_root_logger

# Worker writes to a separate file to avoid multi-process rotation conflicts
# with the API container (both share the same /app/storage volume).
configure_root_logger("aisep-worker.log")

celery_app = Celery(
    "aisep_ai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

_is_windows = os.name == "nt"

# ── Auto-discover task modules ──────────────────────────────────────
celery_app.autodiscover_tasks(
    ["src.modules.evaluation.workers"],
    force=True,
)

# ── Celery configuration ────────────────────────────────────────────
celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Timezone
    timezone="UTC",
    enable_utc=True,

    # Reliability
    task_track_started=True,
    broker_connection_retry_on_startup=True,

    # Result expiry – 24 h (DB is the real source of truth)
    result_expires=86400,

    # Default retry policy for broker connection
    broker_transport_options={
        "visibility_timeout": 3600,  # 1 hour
    },

    # Late ack: only acknowledge after task body finishes
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Keep default pool behavior on non-Windows platforms.
    worker_pool="prefork",
)

if _is_windows:
    # Windows compatibility:
    # Avoid billiard multiprocessing pool PermissionError ([WinError 5])
    # by forcing single-process execution.
    celery_app.conf.update(
        worker_pool="solo",
        worker_concurrency=1,
    )
