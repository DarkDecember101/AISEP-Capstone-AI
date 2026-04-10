from fastapi import APIRouter, Depends
from typing import List, Dict, Any
import json
import logging
from sqlalchemy.orm import Session
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import EvaluationRun, EvaluationDocument, EvaluationCriteriaResult
from src.shared.error_response import APIError
from src.shared.correlation import get_correlation_id
from src.shared.rate_limit.limiter import RateLimitDep
from src.shared.config.settings import settings
from src.modules.evaluation.application.dto.evaluation_schema import (
    SubmitEvaluationRequest, SubmitEvaluationResponse
)
from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
from src.modules.evaluation.application.use_cases.submit_evaluation import submit_evaluation
from src.modules.evaluation.application.services.report_validity import validate_canonical_report
from src.shared.observability.metrics import EVAL_SUBMISSIONS_TOTAL

router = APIRouter()
logger = logging.getLogger("aisep.evaluation")

# Rate limiter dependency for evaluation submit
_eval_rate_limit = RateLimitDep("eval", settings.RATE_LIMIT_EVAL_RPM)

# Canonical statuses exposed in API contract
_TERMINAL_STATUSES = {"completed", "failed"}
_VALID_STATUSES = {"queued", "processing", "retry", "completed", "failed"}


@router.get("/history")
def get_evaluation_history(startup_id: str, session: Session = Depends(get_session)):
    logger.info("evaluation.history startup_id=%s correlation_id=%s",
                startup_id, get_correlation_id())
    runs = session.query(EvaluationRun).filter(
        EvaluationRun.startup_id == startup_id).order_by(EvaluationRun.submitted_at.desc()).all()
    return [{
        "id": r.id,
        "status": _normalise_status(r.status),
        "submitted_at": r.submitted_at,
        "overall_score": r.overall_score,
        "failure_reason": r.failure_reason,
    } for r in runs]


@router.post("/", response_model=SubmitEvaluationResponse)
def submit_evaluation_endpoint(
    request: SubmitEvaluationRequest,
    _rl=Depends(_eval_rate_limit),
):
    logger.info("evaluation.submit startup_id=%s documents=%d correlation_id=%s",
                request.startup_id, len(request.documents), get_correlation_id())
    try:
        result = submit_evaluation(request)
        EVAL_SUBMISSIONS_TOTAL.labels(status="accepted").inc()
        return result
    except Exception:
        EVAL_SUBMISSIONS_TOTAL.labels(status="error").inc()
        raise


@router.get("/{id}")
def get_evaluation(id: int, session: Session = Depends(get_session)):
    logger.info("evaluation.get id=%s correlation_id=%s",
                id, get_correlation_id())
    run = session.query(EvaluationRun).filter(EvaluationRun.id == id).first()
    if not run:
        raise APIError(
            status_code=404,
            code="EVALUATION_NOT_FOUND",
            message=f"Evaluation run {id} not found.",
        )

    docs = session.query(EvaluationDocument).filter(
        EvaluationDocument.evaluation_run_id == id).all()

    return {
        "id": run.id,
        "startup_id": run.startup_id,
        "status": _normalise_status(run.status),
        "submitted_at": run.submitted_at,
        "failure_reason": run.failure_reason,
        "overall_score": run.overall_score,
        "overall_confidence": run.overall_confidence,
        "documents": [
            {
                "id": doc.id,
                "document_type": doc.document_type,
                "status": doc.processing_status,
                "extraction_status": doc.extraction_status,
                "summary": doc.summary,
            } for doc in docs
        ]
    }


@router.get("/{id}/report", response_model=CanonicalEvaluationResult)
def get_evaluation_report(id: int, session: Session = Depends(get_session)):
    logger.info("evaluation.report id=%s correlation_id=%s",
                id, get_correlation_id())
    run = session.query(EvaluationRun).filter(EvaluationRun.id == id).first()
    if not run:
        raise APIError(
            status_code=404,
            code="EVALUATION_NOT_FOUND",
            message=f"Evaluation run {id} not found.",
        )

    status = _normalise_status(run.status)

    if status == "failed":
        raise APIError(
            status_code=409,
            code="EVALUATION_FAILED",
            message="Evaluation failed. Report is unavailable.",
            detail={
                "failure_reason": run.failure_reason,
                "next_step": "Check GET /api/v1/evaluations/{id} for document summaries and re-submit.",
            },
        )

    if status != "completed":
        raise APIError(
            status_code=202,
            code="EVALUATION_NOT_READY",
            message="Report is not ready yet. Please retry shortly.",
            retryable=True,
        )

    docs = session.query(EvaluationDocument).filter(
        EvaluationDocument.evaluation_run_id == id).all()
    # Deterministic selection: pick the completed doc with the highest id
    completed_docs = sorted(
        [d for d in docs if d.processing_status ==
            "completed" and d.artifact_metadata_json],
        key=lambda d: d.id,
        reverse=True,
    )
    primary_doc = completed_docs[0] if completed_docs else None

    if not primary_doc:
        raise APIError(
            status_code=409,
            code="NO_SUCCESSFUL_DOCUMENT",
            message="Evaluation completed but no successful document evaluations found.",
        )

    try:
        metadata = json.loads(primary_doc.artifact_metadata_json)
        canonical_dict = metadata.get("canonical_evaluation")
        if not canonical_dict:
            raise ValueError("No canonical evaluation extracted.")

        # Backfill startup_id from run context for legacy/stale artifacts.
        canonical_startup_id = str(
            canonical_dict.get("startup_id") or "").strip()
        if not canonical_startup_id and run.startup_id:
            canonical_dict["startup_id"] = run.startup_id

        validity = validate_canonical_report(canonical_dict)
        if not validity.is_valid:
            raise APIError(
                status_code=409,
                code="EVALUATION_INVALID_REPORT",
                message="Evaluation report is not a valid scored result.",
                detail={
                    "reason": validity.reason,
                    "next_step": "Check GET /api/v1/evaluations/{id} for failure_reason and resubmit with better source documents.",
                },
            )

        return CanonicalEvaluationResult(**canonical_dict)
    except Exception as e:
        if isinstance(e, APIError):
            raise
        logger.error("evaluation.report parse_error id=%s error=%s correlation_id=%s",
                     id, e, get_correlation_id())
        raise APIError(
            status_code=500,
            code="REPORT_PARSE_ERROR",
            message="Failed to parse evaluation report.",
        )


def _normalise_status(raw: str | None) -> str:
    """Map any legacy/ambiguous DB status to the canonical contract set."""
    if not raw:
        return "queued"
    lower = raw.lower().strip()
    # Map legacy partial_completed → completed
    if lower in ("partial_completed", "partial"):
        return "completed"
    if lower in _VALID_STATUSES:
        return lower
    # Unknown status — clamp to "processing" to avoid leaking internals
    logger.warning(
        "Unexpected evaluation status %r — clamping to 'processing'", raw)
    return "processing"
