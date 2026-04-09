from typing import List, Dict, Any
import json
import logging
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import (
    EvaluationRun, EvaluationDocument, EvaluationCriteriaResult, EvaluationLog
)
from src.modules.evaluation.application.dto.evaluation_schema import (
    SubmitEvaluationRequest, SubmitEvaluationResponse, DocumentInputSchema, LLMDutchEvaluationResult
)

logger = logging.getLogger("submit_evaluation")


def submit_evaluation(request: SubmitEvaluationRequest) -> SubmitEvaluationResponse:
    # 1. Start session
    session = next(get_session())

    try:
        # 2. Check if there's an active run for this startup and cancel them
        active_runs = session.query(EvaluationRun).filter(
            EvaluationRun.startup_id == request.startup_id,
            EvaluationRun.status.in_(
                ["queued", "processing", "partial_completed"])
        ).all()

        for active_run in active_runs:
            active_run.status = "failed"
            active_run.failure_reason = "Superseded by a new evaluation request."
        session.commit()

        # 3. Create run
        run = EvaluationRun(
            startup_id=request.startup_id,
            status="queued"
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        # Add initial log
        session.add(EvaluationLog(
            evaluation_run_id=run.id, step="submit", status="ok",
            message="Created new evaluation run",
        ))

        # 4. Add documents
        for doc in request.documents:
            eval_doc = EvaluationDocument(
                evaluation_run_id=run.id,
                document_id=doc.document_id,
                document_type=doc.document_type,
                source_file_url_or_path=doc.file_url_or_path
            )
            session.add(eval_doc)

        session.commit()

        # 5. Dispatch Celery task (async – API returns immediately)
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        task_result = process_evaluation_run_task.delay(run.id)

        logger.info(
            "[submit] Enqueued Celery task %s for evaluation_run_id=%s",
            task_result.id, run.id,
        )

        # Persist the Celery task ID for observability
        session.add(EvaluationLog(
            evaluation_run_id=run.id, step="enqueue", status="ok",
            message=f"Celery task enqueued: {task_result.id}",
        ))
        session.commit()

        return SubmitEvaluationResponse(
            evaluation_run_id=run.id,
            status="queued",
            message="Evaluation run initialized."
        )

    finally:
        session.close()
