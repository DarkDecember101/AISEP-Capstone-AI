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
    SubmitEvaluationRequest, SubmitEvaluationResponse,
    EvaluationStatusResponse, ReportEnvelope,
)
from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
from src.modules.evaluation.application.use_cases.submit_evaluation import submit_evaluation
from src.modules.evaluation.application.use_cases.merge_evaluation import merge_canonical_results
from src.modules.evaluation.application.services.report_validity import (
    sanitize_canonical_report,
    validate_canonical_report,
)

router = APIRouter()
logger = logging.getLogger("aisep.evaluation")

_eval_rate_limit = RateLimitDep("eval", settings.RATE_LIMIT_EVAL_RPM)
_TERMINAL_STATUSES = {"completed", "failed"}
_VALID_STATUSES = {"queued", "processing", "retry", "completed", "failed"}


def _normalise_status(raw: str | None) -> str:
    if not raw:
        return "queued"
    lower = raw.lower().strip()
    if lower in ("partial_completed", "partial"):
        return "completed"
    if lower in _VALID_STATUSES:
        return lower
    logger.warning(
        "Unexpected evaluation status %r — clamping to 'processing'", raw)
    return "processing"


def _load_canonical_from_doc(doc: EvaluationDocument, run: EvaluationRun) -> dict:
    meta = json.loads(doc.artifact_metadata_json)
    c = meta.get("canonical_evaluation")
    if not c:
        raise ValueError(f"No canonical evaluation in doc {doc.id}")
    if not str(c.get("startup_id") or "").strip() and run.startup_id:
        c["startup_id"] = run.startup_id
    return c


def _available_sources(docs: list) -> list[str]:
    return sorted(set(
        d.document_type for d in docs
        if d.processing_status == "completed" and d.artifact_metadata_json
    ))


def _has_merged(run: EvaluationRun) -> bool:
    return bool(run.merged_artifact_json)


def _load_merged(run: EvaluationRun) -> dict | None:
    if not run.merged_artifact_json:
        return None
    try:
        meta = json.loads(run.merged_artifact_json)
        c = meta.get("canonical_evaluation")
        if c and not str(c.get("startup_id") or "").strip() and run.startup_id:
            c["startup_id"] = run.startup_id
        return c
    except Exception:
        return None


# ── History ──────────────────────────────────────────────────────────────────

@router.get("/history")
def get_evaluation_history(startup_id: str, session: Session = Depends(get_session)):
    logger.info("evaluation.history startup_id=%s correlation_id=%s",
                startup_id, get_correlation_id())
    runs = session.query(EvaluationRun).filter(
        EvaluationRun.startup_id == startup_id).order_by(EvaluationRun.submitted_at.desc()).all()
    return [{
        "id": r.id,
        "status": _normalise_status(r.status),
        "evaluation_mode": r.evaluation_mode,
        "submitted_at": r.submitted_at,
        "overall_score": r.overall_score,
        "failure_reason": r.failure_reason,
    } for r in runs]


# ── Submit ───────────────────────────────────────────────────────────────────

@router.post("/", response_model=SubmitEvaluationResponse)
def submit_evaluation_endpoint(
    request: SubmitEvaluationRequest,
    _rl=Depends(_eval_rate_limit),
):
    logger.info("evaluation.submit startup_id=%s documents=%d mode=%s correlation_id=%s",
                request.startup_id, len(request.documents),
                request.derived_evaluation_mode, get_correlation_id())
    return submit_evaluation(request)


# ── Status ───────────────────────────────────────────────────────────────────

@router.get("/{id}", response_model=EvaluationStatusResponse)
def get_evaluation(id: int, session: Session = Depends(get_session)):
    logger.info("evaluation.get id=%s correlation_id=%s",
                id, get_correlation_id())
    run = session.query(EvaluationRun).filter(EvaluationRun.id == id).first()
    if not run:
        raise APIError(status_code=404, code="EVALUATION_NOT_FOUND",
                       message=f"Evaluation run {id} not found.")

    docs = session.query(EvaluationDocument).filter(
        EvaluationDocument.evaluation_run_id == id).all()

    completed_types = {
        d.document_type for d in docs
        if d.processing_status == "completed" and d.artifact_metadata_json
    }

    return EvaluationStatusResponse(
        id=run.id,
        evaluation_run_id=run.id,
        startup_id=run.startup_id,
        status=_normalise_status(run.status),
        submitted_at=run.submitted_at,
        failure_reason=run.failure_reason,
        overall_score=run.overall_score,
        overall_confidence=run.overall_confidence,
        evaluation_mode=run.evaluation_mode,
        documents=[
            {
                "id": doc.id,
                "document_id": doc.document_id,
                "document_type": doc.document_type,
                "status": doc.processing_status,
                "extraction_status": doc.extraction_status,
                "summary": doc.summary,
            } for doc in docs
        ],
        has_pitch_deck_result="pitch_deck" in completed_types,
        has_business_plan_result="business_plan" in completed_types,
        has_merged_result=_has_merged(run),
        merge_status=run.merge_status,
    )


# ── Main report ──────────────────────────────────────────────────────────────

@router.get("/{id}/report", response_model=ReportEnvelope)
def get_evaluation_report(id: int, session: Session = Depends(get_session)):
    logger.info("evaluation.report id=%s correlation_id=%s",
                id, get_correlation_id())
    run = session.query(EvaluationRun).filter(EvaluationRun.id == id).first()
    if not run:
        raise APIError(status_code=404, code="EVALUATION_NOT_FOUND",
                       message=f"Evaluation run {id} not found.")

    status = _normalise_status(run.status)
    if status == "failed":
        raise APIError(status_code=409, code="EVALUATION_FAILED",
                       message="Evaluation failed. Report is unavailable.",
                       detail={"failure_reason": run.failure_reason})
    if status != "completed":
        raise APIError(status_code=202, code="EVALUATION_NOT_READY",
                       message="Report is not ready yet. Please retry shortly.",
                       retryable=True)

    docs = session.query(EvaluationDocument).filter(
        EvaluationDocument.evaluation_run_id == id).all()
    completed_docs = [d for d in docs
                      if d.processing_status == "completed" and d.artifact_metadata_json]
    if not completed_docs:
        raise APIError(status_code=409, code="NO_SUCCESSFUL_DOCUMENT",
                       message="Evaluation completed but no successful document evaluations found.")

    eval_mode = run.evaluation_mode or "pitch_deck_only"
    sources = _available_sources(completed_docs)
    merged = _has_merged(run)

    try:
        if eval_mode == "combined" and merged:
            report_mode = "merged"
            canonical_dict = _load_merged(run)
            if not canonical_dict:
                raise APIError(status_code=500, code="MERGE_PARSE_ERROR",
                               message="Failed to load merged report.")
        elif eval_mode == "combined" and not merged:
            # Best available single source — combined but merge not yet done
            report_mode = "source"
            pd_docs = [
                d for d in completed_docs if d.document_type == "pitch_deck"]
            bp_docs = [
                d for d in completed_docs if d.document_type == "business_plan"]
            if pd_docs:
                canonical_dict = _load_canonical_from_doc(pd_docs[0], run)
            elif bp_docs:
                canonical_dict = _load_canonical_from_doc(bp_docs[0], run)
            else:
                canonical_dict = _load_canonical_from_doc(
                    completed_docs[0], run)
        elif eval_mode == "pitch_deck_only":
            report_mode = "pitch_deck_only"
            pd_docs = [
                d for d in completed_docs if d.document_type == "pitch_deck"]
            if not pd_docs:
                raise APIError(status_code=409, code="NO_SUCCESSFUL_DOCUMENT",
                               message="No completed pitch_deck document found.")
            canonical_dict = _load_canonical_from_doc(pd_docs[0], run)
        else:  # business_plan_only
            report_mode = "business_plan_only"
            bp_docs = [
                d for d in completed_docs if d.document_type == "business_plan"]
            if not bp_docs:
                raise APIError(status_code=409, code="NO_SUCCESSFUL_DOCUMENT",
                               message="No completed business_plan document found.")
            canonical_dict = _load_canonical_from_doc(bp_docs[0], run)

        canonical_dict = sanitize_canonical_report(canonical_dict)
        validity = validate_canonical_report(canonical_dict)
        if not validity.is_valid:
            raise APIError(status_code=409, code="EVALUATION_INVALID_REPORT",
                           message="Evaluation report is not a valid scored result.",
                           detail={"reason": validity.reason})

        return ReportEnvelope(
            report_mode=report_mode,
            evaluation_mode=eval_mode,
            has_merged_result=merged,
            available_sources=sources,
            source_document_type=canonical_dict.get(
                "document_type") if report_mode == "source" else None,
            merge_status=run.merge_status,
            report=canonical_dict,
        )

    except APIError:
        raise
    except Exception as e:
        logger.error("evaluation.report parse_error id=%s error=%s", id, e)
        raise APIError(status_code=500, code="REPORT_PARSE_ERROR",
                       message="Failed to parse evaluation report.")


# ── Source-specific report ───────────────────────────────────────────────────

@router.get("/{id}/report/source/{document_type}", response_model=ReportEnvelope)
def get_evaluation_report_by_source(id: int, document_type: str,
                                    session: Session = Depends(get_session)):
    logger.info("evaluation.report.source id=%s doc_type=%s correlation_id=%s",
                id, document_type, get_correlation_id())
    if document_type not in ("pitch_deck", "business_plan"):
        raise APIError(status_code=400, code="INVALID_DOCUMENT_TYPE",
                       message=f"document_type must be 'pitch_deck' or 'business_plan', got '{document_type}'.")

    run = session.query(EvaluationRun).filter(EvaluationRun.id == id).first()
    if not run:
        raise APIError(status_code=404, code="EVALUATION_NOT_FOUND",
                       message=f"Evaluation run {id} not found.")

    status = _normalise_status(run.status)
    if status != "completed":
        raise APIError(status_code=202, code="EVALUATION_NOT_READY",
                       message="Report is not ready yet.", retryable=True)

    doc = (session.query(EvaluationDocument)
           .filter(EvaluationDocument.evaluation_run_id == id,
                   EvaluationDocument.document_type == document_type,
                   EvaluationDocument.processing_status == "completed").first())
    if not doc or not doc.artifact_metadata_json:
        raise APIError(status_code=404, code="DOCUMENT_NOT_FOUND",
                       message=f"No completed {document_type} document found for run {id}.")

    all_docs = session.query(EvaluationDocument).filter(
        EvaluationDocument.evaluation_run_id == id).all()
    eval_mode = run.evaluation_mode or "pitch_deck_only"

    try:
        canonical_dict = _load_canonical_from_doc(doc, run)
        return ReportEnvelope(
            report_mode="source",
            evaluation_mode=eval_mode,
            has_merged_result=_has_merged(run),
            available_sources=_available_sources(all_docs),
            source_document_type=document_type,
            merge_status=run.merge_status,
            report=canonical_dict,
        )
    except APIError:
        raise
    except Exception as e:
        logger.error(
            "evaluation.report.source parse_error id=%s error=%s", id, e)
        raise APIError(status_code=500, code="REPORT_PARSE_ERROR",
                       message="Failed to parse evaluation report.")
