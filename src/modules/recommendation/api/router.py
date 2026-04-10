from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from src.shared.config.settings import settings
from src.shared.auth import require_internal_auth
from src.shared.error_response import APIError
from src.shared.correlation import get_correlation_id
from src.shared.sanitize import is_safe_id
from src.shared.rate_limit.limiter import RateLimitDep
from src.modules.recommendation.application.dto.recommendation_schema import (
    RecommendationExplanationResponse,
    RecommendationListResponse,
    ReindexInvestorRequest,
    ReindexStartupRequest,
)
from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
from src.shared.observability.metrics import (
    RECO_REQUESTS_TOTAL,
    RECO_REQUEST_DURATION,
    RECO_REINDEX_TOTAL,
    RECO_REINDEX_DURATION,
)

import time as _time

router = APIRouter()
engine = RecommendationEngine()
logger = logging.getLogger("aisep.recommendation")

# Rate limiter for public recommendation read endpoints
_reco_rate_limit = RateLimitDep("reco", settings.RATE_LIMIT_RECO_RPM)


def _validate_entity_id(value: str, label: str) -> str:
    """Raise APIError(400) if the id is not safe for file/path use."""
    if not is_safe_id(value):
        raise APIError(
            status_code=400,
            code="INVALID_ID",
            message=f"{label} contains invalid characters or is too long.",
        )
    return value


@router.post("/internal/recommendations/reindex/startup/{startup_id}")
async def reindex_startup(
    startup_id: str,
    request: ReindexStartupRequest,
    _auth: None = Depends(require_internal_auth),
):
    _validate_entity_id(startup_id, "startup_id")
    logger.info("recommendation.reindex_startup startup_id=%s correlation_id=%s",
                startup_id, get_correlation_id())
    _start = _time.monotonic()
    try:
        document = engine.reindex_startup(startup_id, request)
        RECO_REINDEX_TOTAL.labels(entity_type="startup", outcome="success").inc()
    except Exception as exc:
        RECO_REINDEX_TOTAL.labels(entity_type="startup", outcome="error").inc()
        logger.error("recommendation.reindex_startup failed startup_id=%s error=%s correlation_id=%s",
                     startup_id, exc, get_correlation_id())
        raise APIError(
            status_code=500,
            code="REINDEX_STARTUP_FAILED",
            message=f"Failed to reindex startup {startup_id}.",
        )
    finally:
        RECO_REINDEX_DURATION.labels(entity_type="startup").observe(_time.monotonic() - _start)
    return {
        "success": True,
        "startup_id": startup_id,
        "profile_version": document.profile_version,
        "source_updated_at": document.source_updated_at,
        "message": "Startup recommendation document reindexed successfully",
    }


@router.post("/internal/recommendations/reindex/investor/{investor_id}")
async def reindex_investor(
    investor_id: str,
    request: ReindexInvestorRequest,
    _auth: None = Depends(require_internal_auth),
):
    _validate_entity_id(investor_id, "investor_id")
    logger.info("recommendation.reindex_investor investor_id=%s correlation_id=%s",
                investor_id, get_correlation_id())
    _start = _time.monotonic()
    try:
        document = engine.reindex_investor(investor_id, request)
        RECO_REINDEX_TOTAL.labels(entity_type="investor", outcome="success").inc()
    except Exception as exc:
        RECO_REINDEX_TOTAL.labels(entity_type="investor", outcome="error").inc()
        logger.error("recommendation.reindex_investor failed investor_id=%s error=%s correlation_id=%s",
                     investor_id, exc, get_correlation_id())
        raise APIError(
            status_code=500,
            code="REINDEX_INVESTOR_FAILED",
            message=f"Failed to reindex investor {investor_id}.",
        )
    finally:
        RECO_REINDEX_DURATION.labels(entity_type="investor").observe(_time.monotonic() - _start)
    return {
        "success": True,
        "investor_id": investor_id,
        "profile_version": document.profile_version,
        "source_updated_at": document.source_updated_at,
        "message": "Investor recommendation document reindexed successfully",
    }


@router.get("/api/v1/recommendations/startups", response_model=RecommendationListResponse)
async def get_startup_recommendations(
    investor_id: str = Query(..., description="Investor document id"),
    top_n: int = Query(10, ge=1, le=10),
    _rl=Depends(_reco_rate_limit),
):
    _validate_entity_id(investor_id, "investor_id")
    logger.info("recommendation.get investor_id=%s top_n=%d correlation_id=%s",
                investor_id, top_n, get_correlation_id())
    _start = _time.monotonic()
    try:
        result = engine.get_recommendations(investor_id=investor_id, top_n=top_n)
        RECO_REQUESTS_TOTAL.labels(endpoint="list").inc()
        return result
    except ValueError as exc:
        raise APIError(
            status_code=404,
            code="INVESTOR_NOT_FOUND",
            message=str(exc),
        )
    except Exception as exc:
        logger.error("recommendation.get failed investor_id=%s error=%s correlation_id=%s",
                     investor_id, exc, get_correlation_id())
        raise APIError(
            status_code=500,
            code="RECOMMENDATION_ERROR",
            message="Failed to generate recommendations.",
        )
    finally:
        RECO_REQUEST_DURATION.labels(endpoint="list").observe(_time.monotonic() - _start)


@router.get("/api/v1/recommendations/startups/{startup_id}/explanation", response_model=RecommendationExplanationResponse)
async def get_recommendation_explanation(
    startup_id: str,
    investor_id: str = Query(..., description="Investor document id"),
    _rl=Depends(_reco_rate_limit),
):
    _validate_entity_id(startup_id, "startup_id")
    _validate_entity_id(investor_id, "investor_id")
    logger.info("recommendation.explanation investor_id=%s startup_id=%s correlation_id=%s",
                investor_id, startup_id, get_correlation_id())
    _start = _time.monotonic()
    try:
        result = engine.get_explanation(investor_id=investor_id, startup_id=startup_id)
        RECO_REQUESTS_TOTAL.labels(endpoint="explanation").inc()
        return result
    except ValueError as exc:
        raise APIError(
            status_code=404,
            code="NOT_FOUND",
            message=str(exc),
        )
    except Exception as exc:
        logger.error("recommendation.explanation failed error=%s correlation_id=%s",
                     exc, get_correlation_id())
        raise APIError(
            status_code=500,
            code="EXPLANATION_ERROR",
            message="Failed to generate explanation.",
        )
    finally:
        RECO_REQUEST_DURATION.labels(endpoint="explanation").observe(_time.monotonic() - _start)
