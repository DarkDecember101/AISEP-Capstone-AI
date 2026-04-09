"""
Outbound webhook / callback delivery for evaluation completion events.

Design goals
------------
* **Idempotent-safe** — Each logical callback gets a stable ``delivery_id``
  (derived from ``evaluation_run_id`` + terminal status).  The receiver can
  de-duplicate on this id.
* **Retry with backoff** — Up to ``WEBHOOK_MAX_RETRIES`` attempts with
  exponential back-off (1 s, 2 s, 4 s …).
* **Signed payload** — If ``WEBHOOK_SIGNING_SECRET`` is configured, every
  request carries an ``X-Webhook-Signature`` header (HMAC-SHA256 hex digest
  of the raw JSON body).
* **Audit trail** — Every attempt is persisted to ``webhook_deliveries``.
* **Fire-and-forget safe** — Callback failure never raises into the caller;
  errors are logged and persisted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from src.shared.config.settings import settings
from src.shared.persistence.db import get_session
from src.shared.persistence.models.webhook_models import WebhookDeliveryRow

logger = logging.getLogger("aisep.webhook")

# ── Defaults ────────────────────────────────────────────────────────
_DEFAULT_TIMEOUT = 10  # seconds per HTTP attempt


def _compute_signature(body_bytes: bytes, secret: str) -> str:
    """HMAC-SHA256 hex digest of *body_bytes* using *secret*."""
    return hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()


def _stable_delivery_id(evaluation_run_id: int, terminal_status: str) -> str:
    """Deterministic id so the same logical event always produces the same id."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"aisep:evaluation:{evaluation_run_id}:{terminal_status}",
    ).hex


def build_webhook_payload(
    *,
    evaluation_run_id: int,
    startup_id: str,
    terminal_status: str,
    overall_score: Optional[float] = None,
    failure_reason: Optional[str] = None,
    correlation_id: str = "",
) -> Dict[str, Any]:
    """Construct the stable webhook payload dict."""
    delivery_id = _stable_delivery_id(evaluation_run_id, terminal_status)
    payload: Dict[str, Any] = {
        "delivery_id": delivery_id,
        "evaluation_run_id": evaluation_run_id,
        "startup_id": startup_id,
        "terminal_status": terminal_status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    if overall_score is not None:
        payload["overall_score"] = overall_score
    if failure_reason:
        payload["failure_reason"] = failure_reason
    if correlation_id:
        payload["correlation_id"] = correlation_id
    return payload


def deliver_webhook(
    payload: Dict[str, Any],
    callback_url: str | None = None,
) -> bool:
    """
    POST *payload* to *callback_url* (or the global ``WEBHOOK_CALLBACK_URL``).

    Returns ``True`` if delivery succeeded (2xx), ``False`` otherwise.
    Never raises — all errors are logged + persisted.
    """
    url = callback_url or getattr(settings, "WEBHOOK_CALLBACK_URL", "")
    if not url:
        logger.debug("No webhook callback URL configured — skipping delivery.")
        return False

    secret: str = getattr(settings, "WEBHOOK_SIGNING_SECRET", "")
    max_retries: int = int(getattr(settings, "WEBHOOK_MAX_RETRIES", 3))
    verify_ssl: bool = getattr(settings, "WEBHOOK_VERIFY_SSL", True)
    delivery_id: str = payload.get("delivery_id", uuid.uuid4().hex)
    body_bytes = json.dumps(payload, default=str).encode("utf-8")

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "X-Webhook-Delivery-Id": delivery_id,
    }
    if secret:
        headers["X-Webhook-Signature"] = _compute_signature(body_bytes, secret)

    attempt = 0
    success = False

    while attempt < max_retries:
        attempt += 1
        response_status = 0
        outcome = "pending"
        response_body: str | None = None

        try:
            with httpx.Client(timeout=_DEFAULT_TIMEOUT, follow_redirects=True, verify=verify_ssl) as client:
                resp = client.post(url, content=body_bytes, headers=headers)
                response_status = resp.status_code
                response_body = resp.text[:2000]  # truncate for storage
                if 200 <= response_status < 300:
                    outcome = "success"
                    success = True
                else:
                    outcome = "failed"
                    location = resp.headers.get("location", "")
                    if location:
                        logger.warning(
                            "Webhook delivery attempt %d/%d returned %d with Location: %s "
                            "(follow_redirects is True — check .NET route config)",
                            attempt, max_retries, response_status, location,
                        )
        except Exception as exc:
            outcome = "error"
            response_body = str(exc)[:2000]
            logger.warning(
                "Webhook delivery attempt %d/%d failed for delivery_id=%s: %s",
                attempt, max_retries, delivery_id, exc,
            )

        # ── Persist attempt ─────────────────────────────────────────
        _persist_attempt(
            delivery_id=delivery_id,
            evaluation_run_id=payload.get("evaluation_run_id", 0),
            startup_id=payload.get("startup_id", ""),
            callback_url=url,
            attempt=attempt,
            response_status=response_status,
            outcome=outcome,
            response_body=response_body,
            payload_json=body_bytes.decode("utf-8"),
        )

        if success:
            logger.info(
                "Webhook delivered successfully delivery_id=%s attempt=%d/%d",
                delivery_id, attempt, max_retries,
            )
            return True

        # Exponential back-off: 1s, 2s, 4s …
        if attempt < max_retries:
            backoff = min(2 ** (attempt - 1), 30)
            time.sleep(backoff)

    logger.error(
        "Webhook delivery exhausted all %d retries for delivery_id=%s",
        max_retries, delivery_id,
    )
    return False


def _persist_attempt(
    *,
    delivery_id: str,
    evaluation_run_id: int,
    startup_id: str,
    callback_url: str,
    attempt: int,
    response_status: int,
    outcome: str,
    response_body: str | None,
    payload_json: str,
) -> None:
    """Best-effort persistence — never raises."""
    try:
        session = next(get_session())
        row = WebhookDeliveryRow(
            delivery_id=delivery_id,
            evaluation_run_id=evaluation_run_id,
            startup_id=startup_id,
            callback_url=callback_url,
            attempt=attempt,
            response_status=response_status,
            outcome=outcome,
            response_body=response_body,
            payload_json=payload_json,
        )
        session.add(row)
        session.commit()
    except Exception as exc:
        logger.error("Failed to persist webhook delivery attempt: %s", exc)
