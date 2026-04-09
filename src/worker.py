"""
⚠️  DEPRECATED – dev-only fallback polling worker.

This polling loop has been replaced by Celery + Redis in production.
Use the Celery worker instead:

    celery -A src.celery_app:celery_app worker -l INFO

This file is kept ONLY for local development without Redis.
It will be removed in a future release.
"""

import time
import warnings
import logging
from src.shared.persistence.db import get_session
from src.shared.persistence.models.evaluation_models import EvaluationRun

logger = logging.getLogger("worker_daemon")


def start_worker():
    """
    DEPRECATED: Simple polling loop for dev-only fallback.
    In production, use Celery worker instead.
    """
    warnings.warn(
        "Polling worker is deprecated. Use Celery worker: "
        "celery -A src.celery_app:celery_app worker -l INFO",
        DeprecationWarning,
        stacklevel=2,
    )

    logger.warning(
        "⚠️  Starting DEPRECATED polling worker. "
        "Use 'celery -A src.celery_app:celery_app worker -l INFO' in production."
    )

    # Import the task function (not the Celery task – call it directly)
    from src.modules.evaluation.application.use_cases.process_document import process_document
    from src.modules.evaluation.application.use_cases.aggregate_evaluation import aggregate_evaluation_run

    while True:
        session = next(get_session())
        try:
            queued_run = session.query(EvaluationRun).filter(
                EvaluationRun.status == "queued"
            ).first()

            if queued_run:
                logger.info(
                    "Found queued run %s. Starting processing.", queued_run.id)
                queued_run.status = "processing"
                session.commit()

                from src.shared.persistence.models.evaluation_models import EvaluationDocument
                docs = session.query(EvaluationDocument).filter(
                    EvaluationDocument.evaluation_run_id == queued_run.id
                ).all()

                for doc in docs:
                    try:
                        process_document(doc.id)
                    except Exception as e:
                        logger.error(
                            "Error processing doc %s: %s", doc.id, str(e))

                aggregate_evaluation_run(queued_run.id)
                logger.info("Finished processing run %s.", queued_run.id)
        finally:
            session.close()

        time.sleep(5)


if __name__ == "__main__":
    start_worker()
