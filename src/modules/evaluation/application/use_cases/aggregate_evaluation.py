import json
from typing import Dict, Any
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import (
    EvaluationRun, EvaluationDocument, EvaluationCriteriaResult, EvaluationLog
)
from src.modules.evaluation.application.services.report_validity import validate_canonical_report


def aggregate_evaluation_run(run_id: int):
    """
    Summarize document level criteria results into run level.
    """
    session = next(get_session())
    run = session.query(EvaluationRun).filter(
        EvaluationRun.id == run_id).first()

    if not run:
        return

    docs = session.query(EvaluationDocument).filter(
        EvaluationDocument.evaluation_run_id == run_id).all()

    if any(d.processing_status in ["queued", "processing", "pending", "extracting"] for d in docs):
        return

    # Check for canonical data
    success_docs = [d for d in docs if d.processing_status ==
                    "completed" and d.artifact_metadata_json]

    if not success_docs:
        run.status = "failed"
        run.failure_reason = "No canonical evaluation data produced. Check document path/url, parser, and pipeline configuration."
        session.add(EvaluationLog(evaluation_run_id=run_id,
                    step="aggregate", status="failed", message=run.failure_reason))
        session.commit()
        return

    # Using the first successful document's canonical metadata as the source of truth for the run
    primary_doc = success_docs[0]

    try:
        metadata = json.loads(primary_doc.artifact_metadata_json)
        canonical = metadata.get("canonical_evaluation")

        if canonical:
            # Backfill startup_id from run context when missing/blank.
            # This protects against legacy artifacts produced before the fix
            # and prevents false invalidation when scoring data is otherwise valid.
            canonical_startup_id = str(
                canonical.get("startup_id") or "").strip()
            if not canonical_startup_id and run.startup_id:
                canonical["startup_id"] = run.startup_id
                metadata["canonical_evaluation"] = canonical
                primary_doc.artifact_metadata_json = json.dumps(
                    metadata, ensure_ascii=False)

            validity = validate_canonical_report(canonical)
            if not validity.is_valid:
                run.status = "failed"
                run.overall_score = None
                run.overall_confidence = None
                run.failure_reason = f"Invalid canonical evaluation report: {validity.reason}"
                session.add(EvaluationLog(
                    evaluation_run_id=run_id,
                    step="aggregate",
                    status="failed",
                    message=run.failure_reason,
                ))
                session.commit()
                return

            run.overall_score = canonical.get(
                "overall_result", {}).get("overall_score")
            cf = canonical.get("overall_result", {}).get(
                "overall_confidence", "Medium")
            run.overall_confidence = {"High": 0.9,
                                      "Medium": 0.5, "Low": 0.2}.get(cf, 0.5)
            run.executive_summary = canonical.get(
                "narrative", {}).get("executive_summary")
            run.status = "completed"
            run.failure_reason = None
            session.add(EvaluationLog(
                evaluation_run_id=run_id,
                step="aggregate",
                status="completed",
                message="Canonical aggregation finished.",
            ))
        else:
            run.status = "failed"
            run.failure_reason = "Failed to parse canonical evaluation object from document."
            session.add(EvaluationLog(
                evaluation_run_id=run_id,
                step="aggregate",
                status="failed",
                message=run.failure_reason,
            ))
    except Exception as e:
        run.status = "failed"
        run.failure_reason = str(e)
        session.add(EvaluationLog(evaluation_run_id=run_id,
                    step="aggregate", status="failed", message=str(e)))

    session.commit()
