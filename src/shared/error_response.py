"""
Unified API error response for all AISEP modules.

Provides:
- APIError: exception class to raise from any endpoint/service
- APIErrorResponse: Pydantic model for the JSON body
- register_error_handlers(): wires into FastAPI app
"""

from __future__ import annotations

import logging
import traceback
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.shared.correlation import get_correlation_id

logger = logging.getLogger("aisep.errors")

# ── Human-friendly messages for common HTTP status codes ────────────
_HTTP_STATUS_MESSAGES: dict[int, str] = {
    400: "Bad request.",
    401: "Authentication required.",
    403: "Forbidden.",
    404: "Resource not found.",
    405: "Method not allowed.",
    409: "Conflict.",
    422: "Validation error.",
    429: "Too many requests.",
    500: "Internal server error.",
    502: "Bad gateway.",
    503: "Service unavailable.",
    504: "Gateway timeout.",
}


# ── Response model ──────────────────────────────────────────────────
class APIErrorResponse(BaseModel):
    """Stable error envelope consumed by .NET client."""

    code: str = Field(
        ..., description="Machine-readable error code, e.g. EVALUATION_NOT_FOUND"
    )
    message: str = Field(..., description="Human-readable summary")
    detail: Optional[Any] = Field(
        default=None, description="Optional structured detail payload"
    )
    retryable: bool = Field(
        default=False, description="Hint: can the caller retry this request?"
    )
    correlation_id: Optional[str] = Field(
        default=None, description="Request correlation id for tracing"
    )


# ── Exception class ─────────────────────────────────────────────────
class APIError(Exception):
    """Raise from any endpoint or service layer to produce a uniform error."""

    def __init__(
        self,
        status_code: int = 500,
        code: str = "INTERNAL_ERROR",
        message: str = "An unexpected error occurred.",
        detail: Any = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail
        self.retryable = retryable


# ── FastAPI exception handlers ──────────────────────────────────────
def _build_body(
    code: str,
    message: str,
    detail: Any = None,
    retryable: bool = False,
) -> dict:
    return APIErrorResponse(
        code=code,
        message=message,
        detail=detail,
        retryable=retryable,
        correlation_id=get_correlation_id(),
    ).model_dump(exclude_none=True)


def register_error_handlers(app: FastAPI) -> None:
    """Call once during app startup to install global exception handlers."""

    @app.exception_handler(APIError)
    async def _api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        logger.warning(
            "APIError code=%s status=%s message=%s correlation_id=%s",
            exc.code,
            exc.status_code,
            exc.message,
            get_correlation_id(),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=_build_body(
                code=exc.code,
                message=exc.message,
                detail=exc.detail,
                retryable=exc.retryable,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Return 422 with structured validation errors (field locations)."""
        errors = []
        for err in exc.errors():
            errors.append({
                "loc": list(err.get("loc", [])),
                "msg": err.get("msg", ""),
                "type": err.get("type", ""),
            })
        return JSONResponse(
            status_code=422,
            content=_build_body(
                code="VALIDATION_ERROR",
                message="Request validation failed.",
                detail={"errors": errors},
                retryable=False,
            ),
        )

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        # Normalise FastAPI's default HTTPException into our envelope.
        # Preserve structured dict detail; never leak raw strings to caller.
        detail_raw = exc.detail
        if isinstance(detail_raw, dict):
            message = detail_raw.get("message", _HTTP_STATUS_MESSAGES.get(
                exc.status_code, "An error occurred."))
            detail = detail_raw
        else:
            # Don't echo arbitrary exception text back to client
            message = _HTTP_STATUS_MESSAGES.get(
                exc.status_code, "An error occurred.")
            detail = None

        return JSONResponse(
            status_code=exc.status_code,
            content=_build_body(
                code="HTTP_ERROR",
                message=message,
                detail=detail,
                retryable=exc.status_code in (429, 502, 503, 504),
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.error(
            "Unhandled exception correlation_id=%s: %s",
            get_correlation_id(),
            traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content=_build_body(
                code="INTERNAL_ERROR",
                message="An unexpected internal error occurred.",
                retryable=False,
            ),
        )
