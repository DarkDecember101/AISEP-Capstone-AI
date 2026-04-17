"""
Tests for combined-mode fallback observability in aggregate_evaluation_run.

Covers the 6 required scenarios:
  1. combined, pitch_deck completed, business_plan failed
     → merge_status = fallback_source_only, log step=aggregate status=combined_fallback
  2. combined, business_plan completed, pitch_deck failed
     → merge_status = fallback_source_only, log step=aggregate status=combined_fallback
  3. combined, both completed, merge succeeds
     → merge_status = merged, merged_artifact_json populated
  4. combined, both completed, merge throws exception
     → merge_status = merge_failed, log step=merge status=failed, merged_artifact_json null
  5. combined, one doc still processing (in-flight guard)
     → merge_status = waiting_for_sources, aggregate deferred (run.status unchanged)
  6. MERGE_EVAL_ENABLED = False, both docs completed
     → merge_status = merge_disabled, log step=merge status=skipped, merged_artifact_json null

Also covers:
  7. non-combined run → merge_status = not_applicable
  8. EvaluationStatusResponse.merge_status propagated from run
  9. ReportEnvelope.merge_status propagated from run
"""
import json
import pytest
from unittest.mock import patch, MagicMock, call

from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from src.shared.persistence.models.evaluation_models import (
    EvaluationRun, EvaluationDocument, EvaluationLog,
)
from src.modules.evaluation.application.use_cases.aggregate_evaluation import (
    aggregate_evaluation_run,
    _MS_NOT_APPLICABLE,
    _MS_WAITING,
    _MS_FALLBACK,
    _MS_MERGED,
    _MS_MERGE_FAILED,
    _MS_MERGE_DISABLED,
)


# ── In-memory SQLite fixture ─────────────────────────────────────────────────

def _mk_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _fake_session_factory(engine):
    """Return a function that yields a fresh Session from the given engine."""
    def _get():
        with Session(engine) as s:
            yield s
    return _get


# ── Shared canonical helpers ──────────────────────────────────────────────────

def _valid_canonical(startup_id="s1", doc_type="pitch_deck") -> dict:
    return {
        "startup_id": startup_id,
        "document_type": doc_type,
        "status": "completed",
        "classification": {
            "stage": {"value": "SEED", "confidence": "High",
                      "resolution_source": "provided", "supporting_evidence_locations": []},
            "main_industry": {"value": "FINTECH", "confidence": "High",
                              "resolution_source": "provided", "supporting_evidence_locations": []},
            "subindustry": {"value": None, "confidence": "Low",
                            "resolution_source": "inferred", "supporting_evidence_locations": []},
            "operational_notes": [],
        },
        "effective_weights": {
            "Problem_&_Customer_Pain": 16.66,
            "Market_Attractiveness_&_Timing": 16.66,
            "Solution_&_Differentiation": 16.66,
            "Business_Model_&_Go_to_Market": 16.66,
            "Team_&_Execution_Readiness": 16.66,
            "Validation_Traction_Evidence_Quality": 16.66,
        },
        "criteria_results": [
            {
                "criterion": "Problem_&_Customer_Pain",
                "status": "scored",
                "raw_score": 70.0,
                "final_score": 70.0,
                "weighted_contribution": 11.66,
                "confidence": "High",
                "cap_summary": {
                    "core_cap": None, "stage_cap": None,
                    "evidence_quality_cap": 80.0, "contradiction_cap": 100.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "DIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 3,
                "strengths": ["Good problem definition"],
                "concerns": [],
                "explanation": "Well defined problem.",
            },
        ],
        "overall_result": {
            "overall_score": 70.0,
            "overall_confidence": "High",
            "evidence_coverage": "moderate",
            "interpretation_band": "strong",
            "stage_context_note": "Seed stage",
        },
        "narrative": {
            "executive_summary": "Good startup.",
            "top_strengths": ["Strong problem"],
            "top_concerns": [],
            "missing_information": [],
            "overall_explanation": "Good startup.",
            "recommendations": [],
            "key_questions": [],
            "operational_notes": [],
        },
        "processing_warnings": [],
    }


def _make_run(session: Session, startup_id: str, mode: str) -> int:
    """Create a run and return its integer ID (safe to use after session closes)."""
    run = EvaluationRun(
        startup_id=startup_id,
        status="queued",
        evaluation_mode=mode,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run.id


def _make_doc(session: Session, run_id: int, doc_type: str,
              status: str = "completed", canonical: dict | None = None) -> EvaluationDocument:
    doc = EvaluationDocument(
        evaluation_run_id=run_id,
        document_id=f"doc-{doc_type}",
        document_type=doc_type,
        processing_status=status,
        extraction_status="done" if status == "completed" else "failed",
        source_file_url_or_path="dummy.pdf",
        artifact_metadata_json=(
            json.dumps({"canonical_evaluation": canonical}
                       ) if canonical else None
        ),
    )
    session.add(doc)
    session.commit()
    return doc


def _run_aggregate(monkeypatch, engine, run_id: int):
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.aggregate_evaluation.get_session",
        _fake_session_factory(engine),
    )
    aggregate_evaluation_run(run_id)


def _get_logs(engine, run_id: int) -> list[EvaluationLog]:
    with Session(engine) as s:
        return s.query(EvaluationLog).filter(
            EvaluationLog.evaluation_run_id == run_id
        ).all()


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 1: combined, PD completed, BP failed
# ══════════════════════════════════════════════════════════════════════════════

class TestCombinedFallbackPDOnly:
    def test_merge_status_is_fallback_source_only(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-1", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-1", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "failed", None)

        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            assert refreshed.merge_status == _MS_FALLBACK
            assert refreshed.status == "completed"
            assert refreshed.merged_artifact_json is None

    def test_combined_fallback_log_entry_references_business_plan_missing(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-1b", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-1b", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "failed", None)

        _run_aggregate(monkeypatch, engine, run_id)

        logs = _get_logs(engine, run_id)
        fallback_logs = [
            l for l in logs
            if l.step == "aggregate" and l.status == "combined_fallback"
        ]
        assert fallback_logs, "Expected an aggregate/combined_fallback log entry"
        assert "business_plan" in fallback_logs[0].message
        assert "pitch_deck" in fallback_logs[0].message


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 2: combined, BP completed, PD failed
# ══════════════════════════════════════════════════════════════════════════════

class TestCombinedFallbackBPOnly:
    def test_merge_status_is_fallback_source_only(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-2", "combined")
            _make_doc(s, run_id, "pitch_deck", "failed", None)
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-2", "business_plan"))

        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            assert refreshed.merge_status == _MS_FALLBACK
            assert refreshed.status == "completed"
            assert refreshed.merged_artifact_json is None

    def test_fallback_log_references_pitch_deck_missing(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-2b", "combined")
            _make_doc(s, run_id, "pitch_deck", "failed", None)
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-2b", "business_plan"))

        _run_aggregate(monkeypatch, engine, run_id)

        logs = _get_logs(engine, run_id)
        fallback_logs = [
            l for l in logs
            if l.step == "aggregate" and l.status == "combined_fallback"
        ]
        assert fallback_logs, "Expected an aggregate/combined_fallback log entry"
        assert "pitch_deck" in fallback_logs[0].message
        assert "business_plan" in fallback_logs[0].message


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 3: combined, both completed, merge succeeds
# ══════════════════════════════════════════════════════════════════════════════

class TestCombinedMergeSuccess:
    def test_merge_status_is_merged_and_artifact_populated(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-3", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-3", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-3", "business_plan"))

        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            assert refreshed.merge_status == _MS_MERGED
            assert refreshed.merged_artifact_json is not None
            artifact = json.loads(refreshed.merged_artifact_json)
            assert artifact.get("canonical_evaluation", {}).get(
                "document_type") == "merged"
            assert refreshed.status == "completed"

    def test_aggregate_log_indicates_merge(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-3b", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-3b", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-3b", "business_plan"))

        _run_aggregate(monkeypatch, engine, run_id)

        logs = _get_logs(engine, run_id)
        completed_logs = [
            l for l in logs
            if l.step == "aggregate" and l.status == "completed"
        ]
        assert completed_logs
        assert "Merged" in completed_logs[0].message or "merged" in completed_logs[0].message.lower(
        )


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 4: combined, both completed, merge throws exception
# ══════════════════════════════════════════════════════════════════════════════

class TestCombinedMergeFailed:
    def test_merge_status_is_merge_failed_and_no_artifact(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-4", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-4", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-4", "business_plan"))

        monkeypatch.setattr(
            "src.modules.evaluation.application.use_cases.aggregate_evaluation.merge_canonical_results",
            MagicMock(side_effect=RuntimeError("Intentional merge failure")),
        )
        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            assert refreshed.merge_status == _MS_MERGE_FAILED
            assert refreshed.merged_artifact_json is None
            # Fallback to PD still completes the run
            assert refreshed.status == "completed"

    def test_merge_failed_log_entry_captured(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-4b", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-4b", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-4b", "business_plan"))

        monkeypatch.setattr(
            "src.modules.evaluation.application.use_cases.aggregate_evaluation.merge_canonical_results",
            MagicMock(side_effect=RuntimeError("schema mismatch")),
        )
        _run_aggregate(monkeypatch, engine, run_id)

        logs = _get_logs(engine, run_id)
        merge_fail_logs = [l for l in logs if l.step ==
                           "merge" and l.status == "failed"]
        assert merge_fail_logs, "Expected a merge/failed log entry"
        assert "schema mismatch" in merge_fail_logs[0].message or \
               "falling back" in merge_fail_logs[0].message.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 5: combined, one doc still processing (in-flight guard)
# ══════════════════════════════════════════════════════════════════════════════

class TestCombinedWaitingForSources:
    def test_merge_status_set_to_waiting_when_in_flight(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-5", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-5", "pitch_deck"))
            # BP still processing
            _make_doc(s, run_id, "business_plan", "processing", None)

        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            # Aggregate deferred — run status NOT changed to completed
            assert refreshed.status == "queued"
            assert refreshed.merge_status == _MS_WAITING
            assert refreshed.merged_artifact_json is None


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 6: MERGE_EVAL_ENABLED = False, both docs completed
# ══════════════════════════════════════════════════════════════════════════════

class TestCombinedMergeDisabled:
    def test_merge_status_is_merge_disabled(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-6", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-6", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-6", "business_plan"))

        monkeypatch.setattr(
            "src.modules.evaluation.application.use_cases.aggregate_evaluation.settings",
            MagicMock(MERGE_EVAL_ENABLED=False),
        )
        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            assert refreshed.merge_status == _MS_MERGE_DISABLED
            assert refreshed.merged_artifact_json is None
            # Run still completes with single-source (PD) fallback
            assert refreshed.status == "completed"

    def test_merge_disabled_log_entry(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-6b", "combined")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-6b", "pitch_deck"))
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-6b", "business_plan"))

        monkeypatch.setattr(
            "src.modules.evaluation.application.use_cases.aggregate_evaluation.settings",
            MagicMock(MERGE_EVAL_ENABLED=False),
        )
        _run_aggregate(monkeypatch, engine, run_id)

        logs = _get_logs(engine, run_id)
        skip_logs = [l for l in logs if l.step ==
                     "merge" and l.status == "skipped"]
        assert skip_logs, "Expected a merge/skipped log entry"
        assert "MERGE_EVAL_ENABLED" in skip_logs[0].message


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 7: non-combined run → merge_status = not_applicable
# ══════════════════════════════════════════════════════════════════════════════

class TestNonCombinedMergeNotApplicable:
    def test_pitch_deck_only_run(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-7", "pitch_deck_only")
            _make_doc(s, run_id, "pitch_deck", "completed",
                      _valid_canonical("startup-7", "pitch_deck"))

        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            assert refreshed.merge_status == _MS_NOT_APPLICABLE
            assert refreshed.status == "completed"

    def test_business_plan_only_run(self, monkeypatch):
        engine = _mk_engine()
        with Session(engine) as s:
            run_id = _make_run(s, "startup-7b", "business_plan_only")
            _make_doc(s, run_id, "business_plan", "completed",
                      _valid_canonical("startup-7b", "business_plan"))

        _run_aggregate(monkeypatch, engine, run_id)

        with Session(engine) as s:
            refreshed = s.get(EvaluationRun, run_id)
            assert refreshed.merge_status == _MS_NOT_APPLICABLE


# ══════════════════════════════════════════════════════════════════════════════
# Scenario 8-9: merge_status propagated through DTOs
# ══════════════════════════════════════════════════════════════════════════════

class TestMergeStatusDTOPropagation:
    def test_evaluation_status_response_carries_merge_status(self):
        from src.modules.evaluation.application.dto.evaluation_schema import EvaluationStatusResponse
        resp = EvaluationStatusResponse(
            evaluation_run_id=1,
            startup_id="s1",
            status="completed",
            evaluation_mode="combined",
            documents=[],
            has_pitch_deck_result=True,
            has_business_plan_result=False,
            has_merged_result=False,
            merge_status="fallback_source_only",
        )
        assert resp.merge_status == "fallback_source_only"

    def test_evaluation_status_response_merge_status_defaults_none(self):
        from src.modules.evaluation.application.dto.evaluation_schema import EvaluationStatusResponse
        resp = EvaluationStatusResponse(
            evaluation_run_id=1,
            startup_id="s1",
            status="completed",
            evaluation_mode="pitch_deck_only",
            documents=[],
        )
        assert resp.merge_status is None

    def test_report_envelope_carries_merge_status(self):
        from src.modules.evaluation.application.dto.evaluation_schema import ReportEnvelope
        env = ReportEnvelope(
            report_mode="source",
            evaluation_mode="combined",
            has_merged_result=False,
            available_sources=["pitch_deck"],
            source_document_type="pitch_deck",
            merge_status="fallback_source_only",
            report={"startup_id": "s1"},
        )
        assert env.merge_status == "fallback_source_only"

    def test_report_envelope_merge_status_defaults_none(self):
        from src.modules.evaluation.application.dto.evaluation_schema import ReportEnvelope
        env = ReportEnvelope(
            report_mode="pitch_deck_only",
            evaluation_mode="pitch_deck_only",
            has_merged_result=False,
            available_sources=["pitch_deck"],
            report={"startup_id": "s1"},
        )
        assert env.merge_status is None

    def test_report_envelope_merged_state_signals(self):
        from src.modules.evaluation.application.dto.evaluation_schema import ReportEnvelope
        env = ReportEnvelope(
            report_mode="merged",
            evaluation_mode="combined",
            has_merged_result=True,
            available_sources=["business_plan", "pitch_deck"],
            merge_status="merged",
            report={"startup_id": "s1", "document_type": "merged"},
        )
        assert env.merge_status == "merged"
        assert env.has_merged_result is True
        assert env.report_mode == "merged"
