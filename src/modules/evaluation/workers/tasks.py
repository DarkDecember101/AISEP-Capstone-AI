"""
Celery tasks for the AI Evaluation module.

This module defines the Celery task that replaces the old polling-loop worker.
The DB (EvaluationRun.status) remains the source of truth; Celery is only
the broker / orchestration layer.

Task contract:
    process_evaluation_run_task(evaluation_run_id: int)

Idempotency:
    - If the run does not exist -> log + skip
    - If the run is not in a retryable state (queued / retry) -> log + skip
    - If the run is already processing / completed -> skip (no double-processing)

Retry:
    - Only on transient errors (e.g. DB connection hiccups)
    - Exponential backoff, max 3 retries
    - On retry the status is set to "retry" so the DB reflects truth
"""

from datetime import datetime
from src.celery_app import celery_app
from src.shared.logging.logger import setup_logger
from src.shared.providers.llm.gemini_client import is_transient_gemini_error

logger = setup_logger("celery_tasks")

# States that are eligible for processing
_PROCESSABLE_STATUSES = {"queued", "retry"}


def _fire_webhook(run) -> None:
    """
    Best-effort webhook delivery for terminal evaluation statuses.

    Never raises — failures are logged and persisted in webhook_deliveries.
    """
    try:
        from src.shared.webhook.delivery import build_webhook_payload, deliver_webhook
        from src.shared.correlation import get_correlation_id

        payload = build_webhook_payload(
            evaluation_run_id=run.id,
            startup_id=run.startup_id or "",
            terminal_status=run.status,
            overall_score=run.overall_score,
            failure_reason=run.failure_reason,
            correlation_id=get_correlation_id(),
        )
        deliver_webhook(payload)
    except Exception as exc:
        # Webhook failure must never break evaluation completion
        logger.error(
            "[task:webhook] Failed to deliver callback for run %s: %s",
            run.id, exc, exc_info=True,
        )


@celery_app.task(
    bind=True,
    name="evaluation.process_run",
    max_retries=3,
    default_retry_delay=30,
    retry_backoff=True,
    retry_backoff_max=300,
    acks_late=True,
)
def process_evaluation_run_task(self, evaluation_run_id: int):
    """
    Process a single EvaluationRun end-to-end.

    Steps:
        1. Open a fresh DB session (scoped to this invocation)
        2. Load the EvaluationRun by ID
        3. Validate status is eligible for processing
        4. Mark status = "processing"
        5. Process each document through the evaluation pipeline
        6. Aggregate results
        7. Mark status = "completed" (or "failed" on error)
        8. Close session
    """
    # ── Late imports to avoid circular deps at module load ──────────
    from src.shared.persistence.db import get_session
    from src.shared.persistence.models.evaluation_models import (
        EvaluationRun, EvaluationDocument, EvaluationLog,
    )
    from src.shared.tracing.setup import trace_span

    logger.info(
        "[task:start] evaluation_run_id=%s celery_task_id=%s attempt=%s",
        evaluation_run_id, self.request.id, self.request.retries,
    )

    session = next(get_session())

    try:
        # ── 1. Load run ────────────────────────────────────────────
        run = session.query(EvaluationRun).filter(
            EvaluationRun.id == evaluation_run_id
        ).first()

        if not run:
            logger.warning(
                "[task:skip] Run %s not found in DB – nothing to do.",
                evaluation_run_id,
            )
            return {"status": "skipped", "reason": "run_not_found"}

        # ── 2. Idempotency guard ───────────────────────────────────
        if run.status not in _PROCESSABLE_STATUSES:
            logger.info(
                "[task:skip] Run %s status is '%s' – not eligible for processing.",
                evaluation_run_id, run.status,
            )
            return {"status": "skipped", "reason": f"status_{run.status}"}

        # ── 3. Mark processing ─────────────────────────────────────
        run.status = "processing"
        run.started_at = datetime.utcnow()
        session.add(EvaluationLog(
            evaluation_run_id=run.id,
            step="celery_task",
            status="processing",
            message=f"Celery worker picked up task (attempt {self.request.retries}).",
        ))
        session.commit()

        logger.info(
            "[task:processing] Run %s marked as processing.", evaluation_run_id,
        )

        # ── 4. Process documents ───────────────────────────────────
        docs = session.query(EvaluationDocument).filter(
            EvaluationDocument.evaluation_run_id == evaluation_run_id
        ).all()

        from src.modules.evaluation.application.use_cases.process_document import process_document

        for doc in docs:
            logger.info(
                "[task:doc] Processing document %s (type=%s) for run %s",
                doc.id, doc.document_type, evaluation_run_id,
            )
            try:
                with trace_span("evaluation.process_document", attributes={
                    "document_id": doc.id,
                    "document_type": doc.document_type,
                    "evaluation_run_id": evaluation_run_id,
                }):
                    process_document(doc.id)
            except Exception as doc_err:
                logger.error(
                    "[task:doc:error] Document %s failed: %s",
                    doc.id, str(doc_err), exc_info=True,
                )
                if is_transient_gemini_error(doc_err):
                    logger.warning(
                        "[task:doc:retry] Document %s hit a transient Gemini error; retrying run %s.",
                        doc.id,
                        evaluation_run_id,
                    )
                    raise
                # Document-level failure is persisted inside process_document
                # itself; we continue to the next document.

        # ── 5. Aggregate results ───────────────────────────────────
        from src.modules.evaluation.application.use_cases.aggregate_evaluation import (
            aggregate_evaluation_run,
        )

        logger.info(
            "[task:aggregate] Aggregating results for run %s", evaluation_run_id,
        )
        with trace_span("evaluation.aggregate", attributes={
            "evaluation_run_id": evaluation_run_id,
        }):
            aggregate_evaluation_run(evaluation_run_id)

        # ── 6. Refresh status from DB (aggregate may set completed/failed)
        session.refresh(run)

        logger.info(
            "[task:completed] Run %s finished with status '%s'.",
            evaluation_run_id, run.status,
        )

        # ── 7. Fire webhook callback for terminal statuses ─────────
        if run.status in ("completed", "failed"):
            _fire_webhook(run)

        return {
            "status": run.status,
            "evaluation_run_id": evaluation_run_id,
        }

    except Exception as exc:
        # ── Transient error -> mark "failed" or "retry" ────────────
        logger.error(
            "[task:error] Run %s failed: %s (attempt %s/%s)",
            evaluation_run_id, str(exc),
            self.request.retries, self.max_retries,
            exc_info=True,
        )

        try:
            # Re-fetch run inside a fresh read in case session is dirty
            session.rollback()
            run = session.query(EvaluationRun).filter(
                EvaluationRun.id == evaluation_run_id
            ).first()

            if run:
                if self.request.retries < self.max_retries:
                    run.status = "retry"
                    run.failure_reason = (
                        f"Retry {self.request.retries + 1}/{self.max_retries}: {str(exc)}"
                    )
                else:
                    run.status = "failed"
                    run.failure_reason = str(exc)
                    run.completed_at = datetime.utcnow()

                session.add(EvaluationLog(
                    evaluation_run_id=evaluation_run_id,
                    step="celery_task",
                    status=run.status,
                    message=str(exc)[:500],
                ))
                session.commit()
        except Exception as inner_exc:
            logger.error(
                "[task:error:inner] Could not persist failure status for run %s: %s",
                evaluation_run_id, str(inner_exc), exc_info=True,
            )
            session.rollback()

        # Retry with exponential backoff if retries remain
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)

        # All retries exhausted — fire webhook for terminal failure
        try:
            run_for_hook = session.query(EvaluationRun).filter(
                EvaluationRun.id == evaluation_run_id
            ).first()
            if run_for_hook and run_for_hook.status == "failed":
                _fire_webhook(run_for_hook)
        except Exception as hook_exc:
            logger.error(
                "[task:webhook:failure] Could not deliver failure callback for run %s: %s",
                evaluation_run_id, hook_exc,
            )

        # All retries exhausted – don't re-raise (task is terminal)
        return {
            "status": "failed",
            "evaluation_run_id": evaluation_run_id,
            "error": str(exc),
        }

    finally:
        session.close()
        logger.info(
            "[task:session_closed] Session closed for run %s.", evaluation_run_id,
        )
