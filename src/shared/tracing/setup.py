"""
OpenTelemetry tracing bootstrap for AISEP AI Service.

Usage — call ``init_tracing()`` once during app startup.  Thereafter
use ``get_tracer()`` to create spans in any module.

Environment variables
---------------------
``OTEL_ENABLED``       – "true" to activate (default "false" for local dev)
``OTEL_SERVICE_NAME``  – logical service name (default "aisep-ai")
``OTEL_EXPORTER_OTLP_ENDPOINT`` – Collector/Jaeger endpoint
                          (default "http://localhost:4317")

When tracing is **disabled** the helper functions return no-op objects so
call-sites never need to guard with ``if tracing_enabled``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator, Optional

from src.shared.config.settings import settings

logger = logging.getLogger("aisep.tracing")

_tracer: Any = None
_tracing_enabled: bool = False


def _otel_available() -> bool:
    """Check if opentelemetry SDK packages are installed."""
    try:
        import opentelemetry  # noqa: F401
        return True
    except ImportError:
        return False


def init_tracing() -> None:
    """Initialise OpenTelemetry SDK if enabled and packages are available."""
    global _tracer, _tracing_enabled

    enabled = getattr(settings, "OTEL_ENABLED", "false")
    if str(enabled).lower().strip() not in ("true", "1", "yes"):
        logger.info(
            "OpenTelemetry tracing is disabled (OTEL_ENABLED=%s).", enabled)
        return

    if not _otel_available():
        logger.warning(
            "OTEL_ENABLED=true but opentelemetry packages are not installed. "
            "Install with: pip install opentelemetry-api opentelemetry-sdk "
            "opentelemetry-exporter-otlp-proto-grpc opentelemetry-instrumentation-fastapi"
        )
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        service_name = getattr(settings, "OTEL_SERVICE_NAME", "aisep-ai")
        endpoint = getattr(
            settings,
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://localhost:4317",
        )

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except ImportError:
            logger.warning(
                "OTLP gRPC exporter not available — using console exporter.")
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("aisep-ai")
        _tracing_enabled = True

        logger.info(
            "OpenTelemetry tracing initialised. service=%s endpoint=%s",
            service_name, endpoint,
        )

        # Auto-instrument FastAPI if package available
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument()
            logger.info("FastAPI auto-instrumented for tracing.")
        except ImportError:
            pass

    except Exception as exc:
        logger.error("Failed to initialise OpenTelemetry: %s",
                     exc, exc_info=True)


def get_tracer():
    """Return the application tracer (or a no-op proxy)."""
    if _tracer is not None:
        return _tracer
    # Return a no-op tracer
    return _NoOpTracer()


def is_tracing_enabled() -> bool:
    return _tracing_enabled


# ── Convenience context-manager span ────────────────────────────────

@contextmanager
def trace_span(
    name: str,
    attributes: Optional[dict] = None,
) -> Generator:
    """
    Context manager that wraps a block in a trace span.

    Works regardless of whether OTel is enabled — when disabled the block
    executes with zero overhead.
    """
    tracer = get_tracer()
    if _tracing_enabled and hasattr(tracer, "start_as_current_span"):
        with tracer.start_as_current_span(name) as span:
            if attributes:
                for k, v in attributes.items():
                    span.set_attribute(k, str(v))
            yield span
    else:
        yield None


# ── No-op fallback ──────────────────────────────────────────────────

class _NoOpSpan:
    """Minimal no-op span that silently ignores all attribute / event calls."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[dict] = None) -> None:
        pass

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args: Any):
        pass


class _NoOpTracer:
    """Minimal no-op tracer returned when OTel is not configured."""

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any):
        yield _NoOpSpan()
