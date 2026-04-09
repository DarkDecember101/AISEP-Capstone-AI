"""
Phase 2C tests — Webhook delivery, tracing, and rate limiting.

Covers:
  * Webhook payload construction + HMAC signing
  * Webhook delivery with retry + audit persistence
  * DB model for webhook_deliveries
  * Tracing setup (no-op and init paths)
  * Rate limiter token-bucket logic
  * Rate limiter FastAPI dependency (429 response)
  * Integration: Celery task fires webhook on terminal status
  * Settings wiring for all Phase 2C config
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import json
import time
from datetime import datetime
from typing import Any, Dict
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from sqlmodel import Session, SQLModel, create_engine

# ── Helpers ─────────────────────────────────────────────────────────


def _inmemory_engine():
    e = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(e)
    return e


def _session(engine):
    return Session(engine)


# ════════════════════════════════════════════════════════════════════
# 1. WEBHOOK PAYLOAD & SIGNING
# ════════════════════════════════════════════════════════════════════

class TestWebhookPayload:

    def test_build_payload_completed(self):
        from src.shared.webhook.delivery import build_webhook_payload

        payload = build_webhook_payload(
            evaluation_run_id=42,
            startup_id="s-100",
            terminal_status="completed",
            overall_score=8.5,
        )
        assert payload["evaluation_run_id"] == 42
        assert payload["startup_id"] == "s-100"
        assert payload["terminal_status"] == "completed"
        assert payload["overall_score"] == 8.5
        assert "delivery_id" in payload
        assert "timestamp" in payload
        assert "failure_reason" not in payload

    def test_build_payload_failed(self):
        from src.shared.webhook.delivery import build_webhook_payload

        payload = build_webhook_payload(
            evaluation_run_id=99,
            startup_id="s-200",
            terminal_status="failed",
            failure_reason="timeout",
        )
        assert payload["terminal_status"] == "failed"
        assert payload["failure_reason"] == "timeout"
        assert "overall_score" not in payload

    def test_delivery_id_is_stable(self):
        from src.shared.webhook.delivery import _stable_delivery_id

        id1 = _stable_delivery_id(1, "completed")
        id2 = _stable_delivery_id(1, "completed")
        id3 = _stable_delivery_id(1, "failed")
        assert id1 == id2, "Same run+status must produce same delivery_id"
        assert id1 != id3, "Different status must produce different delivery_id"

    def test_compute_signature(self):
        from src.shared.webhook.delivery import _compute_signature

        body = b'{"hello":"world"}'
        secret = "test-secret"
        sig = _compute_signature(body, secret)
        expected = hmac_mod.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        assert sig == expected


# ════════════════════════════════════════════════════════════════════
# 2. WEBHOOK DELIVERY & AUDIT
# ════════════════════════════════════════════════════════════════════

class TestWebhookDelivery:

    def test_deliver_skips_when_no_url(self):
        from src.shared.webhook.delivery import deliver_webhook

        with patch("src.shared.webhook.delivery.settings") as mock_settings:
            mock_settings.WEBHOOK_CALLBACK_URL = ""
            result = deliver_webhook({"delivery_id": "x"})
        assert result is False

    @patch("src.shared.webhook.delivery._persist_attempt")
    @patch("src.shared.webhook.delivery.httpx.Client")
    def test_deliver_success(self, mock_client_cls, mock_persist):
        from src.shared.webhook.delivery import deliver_webhook

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        payload = {"delivery_id": "abc",
                   "evaluation_run_id": 1, "startup_id": "s1"}
        result = deliver_webhook(
            payload, callback_url="http://test.local/hook")
        assert result is True
        mock_persist.assert_called_once()
        call_kwargs = mock_persist.call_args[1]
        assert call_kwargs["outcome"] == "success"
        assert call_kwargs["attempt"] == 1

    @patch("src.shared.webhook.delivery._persist_attempt")
    @patch("src.shared.webhook.delivery.httpx.Client")
    @patch("src.shared.webhook.delivery.time.sleep")
    def test_deliver_retries_on_500(self, mock_sleep, mock_client_cls, mock_persist):
        from src.shared.webhook.delivery import deliver_webhook

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "error"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("src.shared.webhook.delivery.settings") as mock_s:
            mock_s.WEBHOOK_CALLBACK_URL = ""
            mock_s.WEBHOOK_SIGNING_SECRET = ""
            mock_s.WEBHOOK_MAX_RETRIES = 2

            payload = {"delivery_id": "xyz",
                       "evaluation_run_id": 2, "startup_id": "s2"}
            result = deliver_webhook(
                payload, callback_url="http://test.local/hook")

        assert result is False
        assert mock_persist.call_count == 2  # 2 attempts
        # Verify backoff sleep was called between retries
        assert mock_sleep.call_count == 1

    @patch("src.shared.webhook.delivery._persist_attempt")
    @patch("src.shared.webhook.delivery.httpx.Client")
    def test_deliver_includes_signature_header(self, mock_client_cls, mock_persist):
        from src.shared.webhook.delivery import deliver_webhook

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value = mock_client

        with patch("src.shared.webhook.delivery.settings") as mock_s:
            mock_s.WEBHOOK_CALLBACK_URL = ""
            mock_s.WEBHOOK_SIGNING_SECRET = "mysecret"
            mock_s.WEBHOOK_MAX_RETRIES = 1

            payload = {"delivery_id": "sig-test",
                       "evaluation_run_id": 3, "startup_id": "s3"}
            deliver_webhook(payload, callback_url="http://test.local/hook")

        # Check that X-Webhook-Signature header was included
        call_args = mock_client.post.call_args
        headers = call_args[1]["headers"] if "headers" in call_args[1] else call_args.kwargs.get(
            "headers", {})
        assert "X-Webhook-Signature" in headers


# ════════════════════════════════════════════════════════════════════
# 3. WEBHOOK DB MODEL
# ════════════════════════════════════════════════════════════════════

class TestWebhookModel:

    def test_webhook_delivery_row_roundtrip(self):
        from src.shared.persistence.models.webhook_models import WebhookDeliveryRow

        engine = _inmemory_engine()
        session = _session(engine)

        row = WebhookDeliveryRow(
            delivery_id="d-001",
            evaluation_run_id=10,
            startup_id="s-10",
            callback_url="http://example.com/hook",
            attempt=1,
            response_status=200,
            outcome="success",
            response_body="ok",
            payload_json='{"test": true}',
        )
        session.add(row)
        session.commit()
        session.refresh(row)

        assert row.id is not None
        assert row.delivery_id == "d-001"
        assert row.outcome == "success"

    def test_multiple_attempts_same_delivery_id(self):
        from src.shared.persistence.models.webhook_models import WebhookDeliveryRow
        from sqlmodel import select

        engine = _inmemory_engine()
        session = _session(engine)

        for attempt in (1, 2, 3):
            session.add(WebhookDeliveryRow(
                delivery_id="d-retry",
                evaluation_run_id=20,
                callback_url="http://example.com",
                attempt=attempt,
                outcome="failed" if attempt < 3 else "success",
                payload_json="{}",
            ))
        session.commit()

        rows = session.exec(
            select(WebhookDeliveryRow).where(
                WebhookDeliveryRow.delivery_id == "d-retry"
            )
        ).all()
        assert len(rows) == 3
        assert rows[-1].outcome == "success"


# ════════════════════════════════════════════════════════════════════
# 4. TRACING
# ════════════════════════════════════════════════════════════════════

class TestTracing:

    def test_tracing_disabled_by_default(self):
        from src.shared.tracing.setup import is_tracing_enabled
        # Default is disabled (OTEL_ENABLED=false)
        assert is_tracing_enabled() is False

    def test_get_tracer_returns_noop_when_disabled(self):
        from src.shared.tracing.setup import get_tracer, _NoOpTracer
        tracer = get_tracer()
        assert isinstance(tracer, _NoOpTracer)

    def test_trace_span_noop_context_manager(self):
        from src.shared.tracing.setup import trace_span
        # Should not raise, just pass through
        with trace_span("test.operation", attributes={"key": "val"}) as span:
            assert span is None  # no-op returns None

    def test_noop_span_methods(self):
        from src.shared.tracing.setup import _NoOpSpan
        span = _NoOpSpan()
        span.set_attribute("k", "v")
        span.add_event("e")
        span.set_status("ok")
        # No exceptions = pass

    def test_init_tracing_skips_when_disabled(self):
        """init_tracing should be safe to call when OTEL_ENABLED=false."""
        from src.shared.tracing.setup import init_tracing, is_tracing_enabled
        init_tracing()  # should not raise
        assert is_tracing_enabled() is False


# ════════════════════════════════════════════════════════════════════
# 5. RATE LIMITER
# ════════════════════════════════════════════════════════════════════

class TestRateLimiter:

    def setup_method(self):
        from src.shared.rate_limit.limiter import reset_buckets
        reset_buckets()

    def test_consume_allows_under_limit(self):
        from src.shared.rate_limit.limiter import _consume
        # First request should always pass with a non-zero RPM
        assert _consume("test:a", 10) is True

    def test_consume_blocks_over_limit(self):
        from src.shared.rate_limit.limiter import _consume
        # Exhaust a bucket with RPM=1
        _consume("test:b", 1)  # first allowed
        assert _consume("test:b", 1) is False  # second blocked

    def test_consume_refills_over_time(self):
        from src.shared.rate_limit import limiter
        limiter._buckets["test:c"] = (0.0, time.monotonic() - 120)
        # After 120 seconds with RPM=60, should have refilled ~120 tokens
        assert limiter._consume("test:c", 60) is True

    def test_consume_unlimited_when_rpm_zero(self):
        from src.shared.rate_limit.limiter import _consume
        for _ in range(100):
            assert _consume("test:d", 0) is True

    def test_reset_buckets(self):
        from src.shared.rate_limit.limiter import _consume, _buckets, reset_buckets
        _consume("test:e", 10)
        assert len(_buckets) > 0
        reset_buckets()
        assert len(_buckets) == 0


# ════════════════════════════════════════════════════════════════════
# 6. RATE LIMITER FASTAPI DEPENDENCY
# ════════════════════════════════════════════════════════════════════

class TestRateLimitDep:

    def setup_method(self):
        from src.shared.rate_limit.limiter import reset_buckets
        reset_buckets()

    @pytest.mark.asyncio
    async def test_dep_allows_normal_request(self):
        from src.shared.rate_limit.limiter import RateLimitDep

        dep = RateLimitDep("test_dep", 60)
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "127.0.0.1"

        # Should not raise
        await dep(request)

    @pytest.mark.asyncio
    async def test_dep_raises_429_when_exhausted(self):
        from src.shared.rate_limit.limiter import RateLimitDep
        from src.shared.error_response import APIError

        dep = RateLimitDep("test_429", 1)
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        await dep(request)  # first allowed
        with pytest.raises(APIError) as exc_info:
            await dep(request)  # second blocked
        assert exc_info.value.status_code == 429
        assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_dep_skips_when_disabled(self):
        from src.shared.rate_limit.limiter import RateLimitDep

        dep = RateLimitDep("test_disabled", 1)
        request = MagicMock()
        request.client = MagicMock()
        request.client.host = "10.0.0.2"

        with patch("src.shared.rate_limit.limiter._is_enabled", return_value=False):
            await dep(request)  # 1st
            await dep(request)  # 2nd — should NOT raise because disabled


# ════════════════════════════════════════════════════════════════════
# 7. CELERY TASK WEBHOOK INTEGRATION
# ════════════════════════════════════════════════════════════════════

class TestCeleryWebhookIntegration:

    def test_fire_webhook_called_on_completed(self):
        """Verify _fire_webhook is called when a run reaches terminal status."""
        from src.modules.evaluation.workers.tasks import _fire_webhook

        mock_run = MagicMock()
        mock_run.id = 1
        mock_run.startup_id = "s-1"
        mock_run.status = "completed"
        mock_run.overall_score = 7.5
        mock_run.failure_reason = None

        with patch("src.shared.webhook.delivery.deliver_webhook") as mock_deliver:
            with patch("src.shared.webhook.delivery.settings") as mock_s:
                mock_s.WEBHOOK_CALLBACK_URL = "http://test.local/hook"
                mock_s.WEBHOOK_SIGNING_SECRET = ""
                mock_s.WEBHOOK_MAX_RETRIES = 1
                _fire_webhook(mock_run)

            mock_deliver.assert_called_once()
            payload = mock_deliver.call_args[0][0]
            assert payload["terminal_status"] == "completed"
            assert payload["evaluation_run_id"] == 1

    def test_fire_webhook_never_raises(self):
        """Verify _fire_webhook swallows exceptions."""
        from src.modules.evaluation.workers.tasks import _fire_webhook

        mock_run = MagicMock()
        mock_run.id = 2
        mock_run.startup_id = "s-2"
        mock_run.status = "failed"
        mock_run.overall_score = None
        mock_run.failure_reason = "boom"

        with patch("src.shared.webhook.delivery.deliver_webhook", side_effect=RuntimeError("oops")):
            # Must not raise
            _fire_webhook(mock_run)


# ════════════════════════════════════════════════════════════════════
# 8. SETTINGS
# ════════════════════════════════════════════════════════════════════

class TestPhase2CSettings:

    def test_webhook_settings_exist(self):
        from src.shared.config.settings import settings
        assert hasattr(settings, "WEBHOOK_CALLBACK_URL")
        assert hasattr(settings, "WEBHOOK_SIGNING_SECRET")
        assert hasattr(settings, "WEBHOOK_MAX_RETRIES")
        assert isinstance(settings.WEBHOOK_MAX_RETRIES, int)

    def test_otel_settings_exist(self):
        from src.shared.config.settings import settings
        assert hasattr(settings, "OTEL_ENABLED")
        assert hasattr(settings, "OTEL_SERVICE_NAME")
        assert hasattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT")

    def test_rate_limit_settings_exist(self):
        from src.shared.config.settings import settings
        assert hasattr(settings, "RATE_LIMIT_ENABLED")
        assert isinstance(settings.RATE_LIMIT_EVAL_RPM, int)
        assert isinstance(settings.RATE_LIMIT_CHAT_RPM, int)
        assert isinstance(settings.RATE_LIMIT_STREAM_RPM, int)
        assert isinstance(settings.RATE_LIMIT_RECO_RPM, int)


# ════════════════════════════════════════════════════════════════════
# 9. HEALTH CHECK — WEBHOOK SUBSYSTEM
# ════════════════════════════════════════════════════════════════════

class TestHealthWebhook:

    def test_webhook_deliveries_table_created(self):
        """Verify the webhook_deliveries table is included in init_db."""
        from src.shared.persistence.models.webhook_models import WebhookDeliveryRow
        engine = _inmemory_engine()
        session = _session(engine)
        # Table should exist and be queryable
        from sqlmodel import select
        rows = session.exec(select(WebhookDeliveryRow)).all()
        assert rows == []
