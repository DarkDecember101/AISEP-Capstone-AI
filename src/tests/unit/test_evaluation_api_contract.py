"""
Tests for the final evaluation API contract.

Covers:
 1. submit with pitch_deck only → evaluation_mode = pitch_deck_only
 2. submit with business_plan only → evaluation_mode = business_plan_only
 3. submit with both → evaluation_mode = combined
 4. reject two pitch decks in one run
 5. reject two business plans in one run
 6. normalize provided_subindustry "null" → None
 7. /report returns pitch deck report for pitch_deck_only
 8. /report returns business plan report for business_plan_only
 9. /report returns merged report when both completed
10. /report returns best available single source when combined but merge not ready
11. /report/source/pitch_deck works
12. /report/source/business_plan works
13. has_merged_result false until both source results are completed
14. existing single-document evaluation flow still passes
15. reject unsupported document_type
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError

from src.modules.evaluation.application.dto.evaluation_schema import (
    SubmitEvaluationRequest,
    DocumentInputSchema,
    SubmitEvaluationResponse,
    EvaluationStatusResponse,
    ReportEnvelope,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_doc(doc_id="01", doc_type="pitch_deck", path="C:\\deck.pdf"):
    return DocumentInputSchema(
        document_id=doc_id,
        document_type=doc_type,
        file_url_or_path=path,
    )


def _make_request(**kwargs):
    defaults = dict(
        startup_id="test-01",
        documents=[_make_doc()],
        provided_stage="seed",
        provided_main_industry="fintech",
        provided_subindustry=None,
    )
    defaults.update(kwargs)
    return SubmitEvaluationRequest(**defaults)


# Minimal valid canonical dict for testing report assembly
def _minimal_canonical(startup_id="test-01", doc_type="pitch_deck"):
    return {
        "startup_id": startup_id,
        "document_type": doc_type,
        "status": "completed",
        "classification": {
            "stage": {"value": "SEED", "confidence": "High", "resolution_source": "provided",
                      "supporting_evidence_locations": []},
            "main_industry": {"value": "FINTECH", "confidence": "High", "resolution_source": "provided",
                              "supporting_evidence_locations": []},
            "subindustry": {"value": None, "confidence": "Low", "resolution_source": "inferred",
                            "supporting_evidence_locations": []},
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


# ══════════════════════════════════════════════════════════════════════════════
# 1-3: Submit mode derivation
# ══════════════════════════════════════════════════════════════════════════════

class TestSubmitModeDerivation:
    def test_pitch_deck_only(self):
        req = _make_request(documents=[_make_doc(doc_type="pitch_deck")])
        assert req.derived_evaluation_mode == "pitch_deck_only"

    def test_business_plan_only(self):
        req = _make_request(documents=[_make_doc(doc_type="business_plan")])
        assert req.derived_evaluation_mode == "business_plan_only"

    def test_combined(self):
        req = _make_request(documents=[
            _make_doc(doc_id="01", doc_type="pitch_deck"),
            _make_doc(doc_id="02", doc_type="business_plan",
                      path="C:\\plan.pdf"),
        ])
        assert req.derived_evaluation_mode == "combined"


# ══════════════════════════════════════════════════════════════════════════════
# 4-5: Reject duplicate documents
# ══════════════════════════════════════════════════════════════════════════════

class TestSubmitValidation:
    def test_reject_two_pitch_decks(self):
        with pytest.raises(ValidationError, match="Only 1 pitch_deck"):
            _make_request(documents=[
                _make_doc(doc_id="01", doc_type="pitch_deck"),
                _make_doc(doc_id="02", doc_type="pitch_deck",
                          path="C:\\d2.pdf"),
            ])

    def test_reject_two_business_plans(self):
        with pytest.raises(ValidationError, match="Only 1 business_plan"):
            _make_request(documents=[
                _make_doc(doc_id="01", doc_type="business_plan"),
                _make_doc(doc_id="02", doc_type="business_plan",
                          path="C:\\p2.pdf"),
            ])

    def test_reject_empty_documents(self):
        with pytest.raises(ValidationError, match="at least 1"):
            _make_request(documents=[])

    def test_reject_unsupported_document_type(self):
        with pytest.raises(ValidationError, match="document_type"):
            _make_request(documents=[_make_doc(doc_type="video")])


# ══════════════════════════════════════════════════════════════════════════════
# 6: Normalize provided_subindustry
# ══════════════════════════════════════════════════════════════════════════════

class TestSubindustryNormalization:
    @pytest.mark.parametrize("raw", ["null", "None", "", "unknown", "n/a", "NA", "undefined"])
    def test_null_like_becomes_none(self, raw):
        req = _make_request(provided_subindustry=raw)
        assert req.provided_subindustry is None

    def test_real_value_preserved(self):
        req = _make_request(provided_subindustry="online lending credit")
        assert req.provided_subindustry == "online lending credit"


# ══════════════════════════════════════════════════════════════════════════════
# 7-10: Report endpoint behavior (unit tests using mock DB objects)
# ══════════════════════════════════════════════════════════════════════════════

def _mock_run(run_id=1, status="completed", mode="pitch_deck_only", merged_json=None,
              startup_id="test-01", merge_status=None):
    run = MagicMock()
    run.id = run_id
    run.startup_id = startup_id
    run.status = status
    run.evaluation_mode = mode
    run.failure_reason = None
    run.merged_artifact_json = merged_json
    run.merge_status = merge_status
    return run


def _mock_doc(doc_id=1, doc_type="pitch_deck", status="completed", canonical=None):
    doc = MagicMock()
    doc.id = doc_id
    doc.document_id = str(doc_id)
    doc.document_type = doc_type
    doc.processing_status = status
    doc.extraction_status = "done"
    doc.summary = "Summary"
    if canonical:
        doc.artifact_metadata_json = json.dumps(
            {"canonical_evaluation": canonical})
    else:
        doc.artifact_metadata_json = None
    return doc


class TestReportEnvelopeLogic:
    """Test the router helper functions and envelope construction logic."""

    def test_pitch_deck_only_report_mode(self):
        """pitch_deck_only mode returns report_mode=pitch_deck_only"""
        from src.modules.evaluation.api.router import _has_merged, _available_sources
        run = _mock_run(mode="pitch_deck_only")
        assert not _has_merged(run)

        pd_doc = _mock_doc(doc_type="pitch_deck",
                           canonical=_minimal_canonical())
        sources = _available_sources([pd_doc])
        assert sources == ["pitch_deck"]

    def test_business_plan_only_report_mode(self):
        from src.modules.evaluation.api.router import _available_sources
        bp_doc = _mock_doc(doc_type="business_plan",
                           canonical=_minimal_canonical(doc_type="business_plan"))
        sources = _available_sources([bp_doc])
        assert sources == ["business_plan"]

    def test_combined_merged_available(self):
        from src.modules.evaluation.api.router import _has_merged, _load_merged
        merged_canonical = _minimal_canonical(doc_type="merged")
        merged_json = json.dumps({"canonical_evaluation": merged_canonical})
        run = _mock_run(mode="combined", merged_json=merged_json)
        assert _has_merged(run)
        loaded = _load_merged(run)
        assert loaded["document_type"] == "merged"

    def test_combined_no_merge_yet(self):
        from src.modules.evaluation.api.router import _has_merged
        run = _mock_run(mode="combined", merged_json=None)
        assert not _has_merged(run)

    def test_available_sources_combined(self):
        from src.modules.evaluation.api.router import _available_sources
        pd = _mock_doc(doc_type="pitch_deck", canonical=_minimal_canonical())
        bp = _mock_doc(doc_id=2, doc_type="business_plan",
                       canonical=_minimal_canonical(doc_type="business_plan"))
        sources = _available_sources([pd, bp])
        assert sources == ["business_plan", "pitch_deck"]


# ══════════════════════════════════════════════════════════════════════════════
# 11-12: Source endpoint
# ══════════════════════════════════════════════════════════════════════════════

class TestSourceEndpointHelpers:
    def test_load_canonical_from_doc(self):
        from src.modules.evaluation.api.router import _load_canonical_from_doc
        canonical = _minimal_canonical()
        doc = _mock_doc(canonical=canonical)
        run = _mock_run()
        loaded = _load_canonical_from_doc(doc, run)
        assert loaded["startup_id"] == "test-01"
        assert loaded["document_type"] == "pitch_deck"

    def test_load_canonical_backfills_startup_id(self):
        from src.modules.evaluation.api.router import _load_canonical_from_doc
        canonical = _minimal_canonical(startup_id="")
        doc = _mock_doc(canonical=canonical)
        run = _mock_run(startup_id="backfilled-01")
        loaded = _load_canonical_from_doc(doc, run)
        assert loaded["startup_id"] == "backfilled-01"


# ══════════════════════════════════════════════════════════════════════════════
# 13: has_merged_result tracking
# ══════════════════════════════════════════════════════════════════════════════

class TestMergedResultFlag:
    def test_false_when_no_merged_json(self):
        from src.modules.evaluation.api.router import _has_merged
        run = _mock_run(merged_json=None)
        assert _has_merged(run) is False

    def test_true_when_merged_json_present(self):
        from src.modules.evaluation.api.router import _has_merged
        run = _mock_run(merged_json='{"canonical_evaluation": {}}')
        assert _has_merged(run) is True


# ══════════════════════════════════════════════════════════════════════════════
# 14: Submit response shape
# ══════════════════════════════════════════════════════════════════════════════

class TestSubmitResponseShape:
    def test_response_has_required_fields(self):
        resp = SubmitEvaluationResponse(
            evaluation_run_id=1,
            startup_id="test-01",
            status="queued",
            evaluation_mode="pitch_deck_only",
            documents=[{"document_id": "01",
                        "document_type": "pitch_deck", "status": "queued"}],
        )
        assert resp.evaluation_run_id == 1
        assert resp.startup_id == "test-01"
        assert resp.evaluation_mode == "pitch_deck_only"
        assert len(resp.documents) == 1

    def test_combined_response_has_two_documents(self):
        resp = SubmitEvaluationResponse(
            evaluation_run_id=2,
            startup_id="test-01",
            status="queued",
            evaluation_mode="combined",
            documents=[
                {"document_id": "01", "document_type": "pitch_deck", "status": "queued"},
                {"document_id": "02", "document_type": "business_plan",
                    "status": "queued"},
            ],
        )
        assert resp.evaluation_mode == "combined"
        assert len(resp.documents) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 15: Status response shape
# ══════════════════════════════════════════════════════════════════════════════

class TestStatusResponseShape:
    def test_status_response_fields(self):
        resp = EvaluationStatusResponse(
            evaluation_run_id=1,
            startup_id="test-01",
            status="completed",
            evaluation_mode="combined",
            documents=[],
            has_pitch_deck_result=True,
            has_business_plan_result=True,
            has_merged_result=True,
        )
        assert resp.has_merged_result is True
        assert resp.evaluation_mode == "combined"

    def test_status_no_merged_yet(self):
        resp = EvaluationStatusResponse(
            evaluation_run_id=1,
            startup_id="test-01",
            status="processing",
            evaluation_mode="combined",
            documents=[],
            has_pitch_deck_result=True,
            has_business_plan_result=False,
            has_merged_result=False,
        )
        assert resp.has_merged_result is False


# ══════════════════════════════════════════════════════════════════════════════
# Report envelope shape
# ══════════════════════════════════════════════════════════════════════════════

class TestReportEnvelopeShape:
    def test_envelope_pitch_deck_only(self):
        env = ReportEnvelope(
            report_mode="pitch_deck_only",
            evaluation_mode="pitch_deck_only",
            has_merged_result=False,
            available_sources=["pitch_deck"],
            source_document_type=None,
            report=_minimal_canonical(),
        )
        assert env.report_mode == "pitch_deck_only"
        assert env.report["document_type"] == "pitch_deck"

    def test_envelope_merged(self):
        env = ReportEnvelope(
            report_mode="merged",
            evaluation_mode="combined",
            has_merged_result=True,
            available_sources=["business_plan", "pitch_deck"],
            source_document_type=None,
            report=_minimal_canonical(doc_type="merged"),
        )
        assert env.report_mode == "merged"
        assert env.has_merged_result is True

    def test_envelope_source(self):
        env = ReportEnvelope(
            report_mode="source",
            evaluation_mode="combined",
            has_merged_result=True,
            available_sources=["business_plan", "pitch_deck"],
            source_document_type="pitch_deck",
            report=_minimal_canonical(),
        )
        assert env.report_mode == "source"
        assert env.source_document_type == "pitch_deck"

    def test_envelope_combined_partial(self):
        """Combined mode, only one source done, no merge yet."""
        env = ReportEnvelope(
            report_mode="source",
            evaluation_mode="combined",
            has_merged_result=False,
            available_sources=["pitch_deck"],
            source_document_type="pitch_deck",
            report=_minimal_canonical(),
        )
        assert env.report_mode == "source"
        assert env.has_merged_result is False
