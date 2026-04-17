from typing import List, Dict, Any
import json
import logging
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import (
    EvaluationRun, EvaluationDocument, EvaluationCriteriaResult, EvaluationLog
)
from src.modules.evaluation.application.dto.evaluation_schema import (
    SubmitEvaluationRequest, SubmitEvaluationResponse, DocumentStatusSchema,
)

logger = logging.getLogger("submit_evaluation")


def submit_evaluation(request: SubmitEvaluationRequest) -> SubmitEvaluationResponse:
    session = next(get_session())

    try:
        # Cancel any active runs for this startup
        active_runs = session.query(EvaluationRun).filter(
            EvaluationRun.startup_id == request.startup_id,
            EvaluationRun.status.in_(
                ["queued", "processing", "partial_completed"])
        ).all()

        for active_run in active_runs:
            active_run.status = "failed"
            active_run.failure_reason = "Superseded by a new evaluation request."
        session.commit()

        # Derive evaluation mode from documents
        evaluation_mode = request.derived_evaluation_mode

        # Create run
        run = EvaluationRun(
            startup_id=request.startup_id,
            status="queued",
            provided_stage=request.provided_stage,
            provided_main_industry=request.provided_main_industry,
            provided_subindustry=request.provided_subindustry,
            evaluation_mode=evaluation_mode,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        session.add(EvaluationLog(
            evaluation_run_id=run.id, step="submit", status="ok",
            message=f"Created new evaluation run (mode={evaluation_mode})",
        ))

        # Add documents
        doc_statuses: list[DocumentStatusSchema] = []
        for doc in request.documents:
            eval_doc = EvaluationDocument(
                evaluation_run_id=run.id,
                document_id=doc.document_id,
                document_type=doc.document_type,
                source_file_url_or_path=doc.file_url_or_path
            )
            session.add(eval_doc)
            doc_statuses.append(DocumentStatusSchema(
                document_id=doc.document_id,
                document_type=doc.document_type,
                status="queued",
            ))

        session.commit()

        # Dispatch Celery task
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        task_result = process_evaluation_run_task.delay(run.id)

        logger.info(
            "[submit] Enqueued Celery task %s for evaluation_run_id=%s mode=%s",
            task_result.id, run.id, evaluation_mode,
        )

        session.add(EvaluationLog(
            evaluation_run_id=run.id, step="enqueue", status="ok",
            message=f"Celery task enqueued: {task_result.id}",
        ))
        session.commit()

        return SubmitEvaluationResponse(
            evaluation_run_id=run.id,
            startup_id=request.startup_id,
            status="queued",
            evaluation_mode=evaluation_mode,
            documents=doc_statuses,
        )

    finally:
        session.close()
