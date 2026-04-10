"""
FastAPI / Starlette middleware that records per-request Prometheus metrics.

Metrics recorded:
  - aisep_http_requests_total   (Counter)
  - aisep_http_request_duration_seconds  (Histogram)
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from src.shared.observability.metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION


class PrometheusHTTPMiddleware(BaseHTTPMiddleware):
    """
    Lightweight middleware that measures every HTTP request.

    The ``endpoint`` label is normalised to the FastAPI route template
    (e.g. ``/api/v1/evaluations/{id}``) so label cardinality stays bounded.
    Requests that don't match any route get the label ``"unmatched"``.
    The ``/metrics`` and ``/health*`` paths are excluded to avoid noise.
    """

    _EXCLUDED_PREFIXES = ("/metrics", "/health")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Skip instrumentation for /metrics and health checks
        path = request.url.path
        if any(path.startswith(prefix) for prefix in self._EXCLUDED_PREFIXES):
            return await call_next(request)

        endpoint = self._resolve_route_template(request)
        method = request.method

        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start

        status_code = str(response.status_code)

        HTTP_REQUESTS_TOTAL.labels(
            method=method, endpoint=endpoint, status_code=status_code
        ).inc()
        HTTP_REQUEST_DURATION.labels(
            method=method, endpoint=endpoint
        ).observe(elapsed)

        return response

    @staticmethod
    def _resolve_route_template(request: Request) -> str:
        """
        Walk the app's routes to find the matching route template string.

        Returns ``"unmatched"`` if nothing matches (keeps cardinality bounded).
        """
        app = request.app
        routes = getattr(app, "routes", [])
        for route in routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                return getattr(route, "path", "unmatched")
        return "unmatched"
