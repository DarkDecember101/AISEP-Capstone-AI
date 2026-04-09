"""
Tests for the Celery-based evaluation task and submit flow.

Uses CELERY_ALWAYS_EAGER so tasks execute in-process without a Redis broker.

Validates:
    - Task skips when run not found
    - Task skips when status is not eligible (idempotency)
    - Task marks processing -> delegates to pipeline -> aggregate
    - Task marks failed when pipeline raises exception
    - Submit flow creates run + enqueues Celery task (not threading)
    - Retry-eligible status 'retry' is re-processable
    - Individual document failure does not block remaining docs
"""

import pytest
from unittest.mock import patch, MagicMock

# ── Enable eager mode BEFORE any task import ────────────────────────
from src.celery_app import celery_app

celery_app.conf.update(
    task_always_eager=True,
    task_eager_propagates=False,  # don't propagate – let task handle errors
)


# ─── Helpers ─────────────────────────────────────────────────────────

def _make_fake_run(run_id=1, status="queued", startup_id="test-startup"):
    run = MagicMock()
    run.id = run_id
    run.status = status
    run.startup_id = startup_id
    run.started_at = None
    run.completed_at = None
    run.failure_reason = None
    return run


def _make_fake_doc(doc_id=10, run_id=1, doc_type="pitch_deck"):
    doc = MagicMock()
    doc.id = doc_id
    doc.evaluation_run_id = run_id
    doc.document_type = doc_type
    return doc


class FakeSession:
    def __init__(self, run=None, docs=None):
        self._run = run
        self._docs = docs or []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False
        self.added = []

    def query(self, model):
        return _FakeQuery(self._run, self._docs)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def refresh(self, obj):
        pass

    def close(self):
        self.closed = True


class _FakeQuery:
    def __init__(self, run, docs):
        self._run = run
        self._docs = docs

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._run

    def all(self):
        return self._docs


# ─── Unit Tests: Task Behavior ───────────────────────────────────────
# All use .apply(args=[...]) with eager mode.


class TestProcessEvaluationRunTask:

    @patch("src.shared.persistence.db.get_session")
    def test_skip_when_run_not_found(self, mock_gs):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        session = FakeSession(run=None)
        mock_gs.return_value = iter([session])

        res = process_evaluation_run_task.apply(args=[999])

        assert res.result["status"] == "skipped"
        assert res.result["reason"] == "run_not_found"
        assert session.closed

    @patch("src.shared.persistence.db.get_session")
    def test_skip_when_already_completed(self, mock_gs):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        session = FakeSession(run=_make_fake_run(status="completed"))
        mock_gs.return_value = iter([session])

        res = process_evaluation_run_task.apply(args=[1])

        assert res.result["status"] == "skipped"
        assert "completed" in res.result["reason"]
        assert session.closed

    @patch("src.shared.persistence.db.get_session")
    def test_skip_when_already_processing(self, mock_gs):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        session = FakeSession(run=_make_fake_run(status="processing"))
        mock_gs.return_value = iter([session])

        res = process_evaluation_run_task.apply(args=[1])

        assert res.result["status"] == "skipped"
        assert "processing" in res.result["reason"]

    @patch("src.shared.persistence.db.get_session")
    def test_skip_when_already_failed(self, mock_gs):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        session = FakeSession(run=_make_fake_run(status="failed"))
        mock_gs.return_value = iter([session])

        res = process_evaluation_run_task.apply(args=[1])

        assert res.result["status"] == "skipped"
        assert "failed" in res.result["reason"]

    @patch("src.modules.evaluation.application.use_cases.aggregate_evaluation.aggregate_evaluation_run")
    @patch("src.modules.evaluation.application.use_cases.process_document.process_document")
    @patch("src.shared.persistence.db.get_session")
    def test_processing_to_completed(self, mock_gs, mock_proc, mock_agg):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        run = _make_fake_run(status="queued")
        doc = _make_fake_doc()
        session = FakeSession(run=run, docs=[doc])
        mock_gs.return_value = iter([session])

        res = process_evaluation_run_task.apply(args=[1])

        assert run.status == "processing"
        assert run.started_at is not None
        mock_proc.assert_called_once_with(doc.id)
        mock_agg.assert_called_once_with(1)
        assert session.closed
        assert session.committed >= 1

    @patch("src.modules.evaluation.application.use_cases.aggregate_evaluation.aggregate_evaluation_run")
    @patch("src.modules.evaluation.application.use_cases.process_document.process_document")
    @patch("src.shared.persistence.db.get_session")
    def test_marks_failed_on_aggregate_exception(self, mock_gs, mock_proc, mock_agg):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        run = _make_fake_run(status="queued")
        session = FakeSession(run=run, docs=[_make_fake_doc()])
        mock_gs.return_value = iter([session])

        mock_agg.side_effect = RuntimeError("Aggregate crashed")

        # Disable retries for this test so it goes straight to failed
        with patch.object(process_evaluation_run_task, "max_retries", 0):
            res = process_evaluation_run_task.apply(args=[1])

        assert res.result["status"] == "failed"
        assert run.status == "failed"
        assert session.closed

    @patch("src.modules.evaluation.application.use_cases.aggregate_evaluation.aggregate_evaluation_run")
    @patch("src.shared.persistence.db.get_session")
    def test_retry_eligible_status_includes_retry(self, mock_gs, mock_agg):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        run = _make_fake_run(status="retry")
        session = FakeSession(run=run, docs=[])
        mock_gs.return_value = iter([session])

        res = process_evaluation_run_task.apply(args=[1])

        # Should proceed (not skip) – status goes to "processing"
        assert run.status == "processing"

    @patch("src.modules.evaluation.application.use_cases.aggregate_evaluation.aggregate_evaluation_run")
    @patch("src.modules.evaluation.application.use_cases.process_document.process_document")
    @patch("src.shared.persistence.db.get_session")
    def test_document_failure_does_not_stop_others(self, mock_gs, mock_proc, mock_agg):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        run = _make_fake_run(status="queued")
        doc1 = _make_fake_doc(doc_id=10)
        doc2 = _make_fake_doc(doc_id=11)
        session = FakeSession(run=run, docs=[doc1, doc2])
        mock_gs.return_value = iter([session])

        # First doc raises, second succeeds
        mock_proc.side_effect = [RuntimeError("doc1 failed"), None]

        res = process_evaluation_run_task.apply(args=[1])

        assert mock_proc.call_count == 2
        mock_agg.assert_called_once_with(1)


# ─── Integration Test: Submit Flow ───────────────────────────────────


class TestSubmitEvaluationFlow:

    @patch("src.modules.evaluation.workers.tasks.process_evaluation_run_task")
    @patch("src.modules.evaluation.application.use_cases.submit_evaluation.get_session")
    def test_submit_enqueues_celery_task(self, mock_gs, mock_task_obj):
        from src.modules.evaluation.application.use_cases.submit_evaluation import submit_evaluation
        from src.modules.evaluation.application.dto.evaluation_schema import (
            SubmitEvaluationRequest, DocumentInputSchema,
        )

        session = FakeSession(run=None, docs=[])
        session.query = lambda model: _FakeQuery(run=None, docs=[])
        session.refresh = lambda obj: setattr(obj, "id", 42)
        mock_gs.return_value = iter([session])

        mock_async_result = MagicMock()
        mock_async_result.id = "celery-task-id-123"
        mock_task_obj.delay.return_value = mock_async_result

        request = SubmitEvaluationRequest(
            startup_id="startup-1",
            documents=[
                DocumentInputSchema(
                    document_id="doc-1",
                    document_type="pitch_deck",
                    file_url_or_path="/tmp/deck.pdf",
                )
            ],
        )

        response = submit_evaluation(request)

        mock_task_obj.delay.assert_called_once()
        assert response.status == "queued"
        assert response.message == "Evaluation run initialized."
        assert session.closed

    def test_no_threading_import_in_submit(self):
        import src.modules.evaluation.application.use_cases.submit_evaluation as mod

        with open(mod.__file__, "r", encoding="utf-8") as f:
            source = f.read()

        assert "import threading" not in source
        assert "threading.Thread" not in source


# ─── Integration Test: DB Status Transitions ─────────────────────────


class TestDBStatusTransitions:

    @patch("src.modules.evaluation.application.use_cases.aggregate_evaluation.aggregate_evaluation_run")
    @patch("src.modules.evaluation.application.use_cases.process_document.process_document")
    @patch("src.shared.persistence.db.get_session")
    def test_full_lifecycle_queued_to_processing(self, mock_gs, mock_proc, mock_agg):
        from src.modules.evaluation.workers.tasks import process_evaluation_run_task

        status_log = []
        run = _make_fake_run(status="queued")

        original_setattr = type(run).__setattr__

        def tracking_setattr(self_obj, name, value):
            if name == "status":
                status_log.append(value)
            original_setattr(self_obj, name, value)

        type(run).__setattr__ = tracking_setattr

        try:
            session = FakeSession(run=run, docs=[_make_fake_doc()])
            mock_gs.return_value = iter([session])

            process_evaluation_run_task.apply(args=[1])

            assert "processing" in status_log
        finally:
            type(run).__setattr__ = original_setattr
