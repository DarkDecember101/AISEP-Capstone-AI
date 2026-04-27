from __future__ import annotations

import json
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from src.modules.evaluation.application.services.report_validity import validate_canonical_report
from src.modules.evaluation.application.use_cases.aggregate_evaluation import aggregate_evaluation_run
from src.modules.evaluation.api.router import router as evaluation_router
from src.modules.evaluation.api.router import get_session as router_get_session
from src.modules.evaluation.application.dto.evaluation_schema import SubmitEvaluationRequest
from src.modules.evaluation.application.services.deterministic_scorer import DeterministicScoringService
from src.modules.evaluation.application.dto.pipeline_schema import (
    ClassificationResult,
    ClassificationField,
    EvidenceMappingResult,
    CriterionEvidence,
    EvidenceUnit,
    RawCriterionJudgmentResult,
    RawJudgment,
)
from src.shared.error_response import register_error_handlers
from src.shared.persistence.models.evaluation_models import EvaluationDocument, EvaluationRun
from src.modules.evaluation.application.dto.canonical_schema import (
    CanonicalCriterionResult,
    CanonicalOverallResult,
    CapSummary,
    DeterministicScoringResult,
)


def _mk_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _invalid_canonical(startup_id: str = "") -> dict:
    return {
        "startup_id": startup_id,
        "status": "completed",
        "classification": {
            "stage": {"value": "Seed", "confidence": "Low", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "main_industry": {"value": "AI", "confidence": "Low", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "subindustry": {"value": "SaaS", "confidence": "Low", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "operational_notes": [],
        },
        "effective_weights": {},
        "criteria_results": [
            {
                "criterion": "Problem_&_Customer_Pain",
                "status": "not_applicable",
                "raw_score": None,
                "final_score": None,
                "weighted_contribution": None,
                "confidence": "Low",
                "cap_summary": {
                    "core_cap": None,
                    "stage_cap": None,
                    "evidence_quality_cap": 0.0,
                    "contradiction_cap": 0.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "ABSENT",
                "evidence_locations": [],
                "supporting_pages_count": 0,
                "strengths": [],
                "concerns": [],
                "explanation": "missing",
            }
        ],
        "overall_result": {
            "overall_score": None,
            "overall_confidence": "Low",
            "evidence_coverage": "weak",
            "interpretation_band": "weak",
            "stage_context_note": "note",
        },
        "narrative": {
            "executive_summary": "Narrative exists but scoring missing",
            "top_strengths": ["story"],
            "top_concerns": ["data"],
            "missing_information": ["evidence"],
            "overall_explanation": "Rich text",
            "recommendations": [],
            "key_questions": [],
            "operational_notes": [],
        },
        "processing_warnings": ["No active criterion weights could be evaluated."],
    }


def _valid_canonical(startup_id: str = "startup-1") -> dict:
    payload = _invalid_canonical(startup_id=startup_id)
    payload["criteria_results"][0]["status"] = "scored"
    payload["criteria_results"][0]["final_score"] = 62.0
    payload["criteria_results"][0]["raw_score"] = 70.0
    payload["criteria_results"][0]["weighted_contribution"] = 12.4
    payload["overall_result"]["overall_score"] = 62.0
    payload["overall_result"]["overall_confidence"] = "Medium"
    return payload


def test_validate_canonical_report_rejects_empty_startup_id():
    validity = validate_canonical_report(_invalid_canonical(startup_id=""))
    assert validity.is_valid is False
    assert "startup_id" in validity.reason


def test_submit_request_rejects_blank_startup_id():
    with pytest.raises(ValueError):
        SubmitEvaluationRequest(
            startup_id="   ",
            documents=[
                {
                    "document_id": "doc-1",
                    "document_type": "pitch_deck",
                    "file_url_or_path": "dummy.pdf",
                }
            ],
        )


def test_deterministic_scorer_maps_canonical_criteria_to_non_null_overall_score():
    scorer = DeterministicScoringService(total_pages=10)

    classification = ClassificationResult(
        stage=ClassificationField(
            value="Seed",
            confidence="Medium",
            resolution_source="inferred",
            supporting_evidence_locations=[],
        ),
        main_industry=ClassificationField(
            value="AI",
            confidence="Medium",
            resolution_source="inferred",
            supporting_evidence_locations=[],
        ),
        subindustry=ClassificationField(
            value="SaaS",
            confidence="Low",
            resolution_source="inferred",
            supporting_evidence_locations=[],
        ),
        operational_notes=[],
    )

    evidence = EvidenceMappingResult(
        criteria_evidence=[
            CriterionEvidence(
                criterion="Problem_&_Customer_Pain",
                strongest_evidence_level="DIRECT",
                evidence_units=[
                    EvidenceUnit(
                        source_type="Pitch Deck",
                        source_id="doc-1",
                        slide_number_or_page_number=2,
                        excerpt_or_summary="Customer pain validated",
                    )
                ],
                weakening_evidence_units=[],
                possible_contradictions=[],
                gaps=[],
            )
        ]
    )

    raw = RawCriterionJudgmentResult(
        raw_judgments=[
            RawJudgment(
                criterion="Problem_&_Customer_Pain",
                raw_score=7.0,
                criterion_confidence="Medium",
                suggested_core_cap=10.0,
                suggested_stage_cap=10.0,
                suggested_contradiction_severity="none",
                reasoning="Clear problem evidence",
            )
        ]
    )

    result = scorer.score(classification=classification,
                          evidence=evidence, raw_judgments=raw)

    assert result.overall_result.overall_score is not None
    assert any(c.final_score is not None for c in result.criteria_results)


def test_validate_canonical_report_rejects_no_usable_scores():
    validity = validate_canonical_report(
        _invalid_canonical(startup_id="startup-x"))
    assert validity.is_valid is False
    assert "No usable scoring data" in validity.reason


def test_validate_canonical_report_accepts_overall_score_or_final_score():
    validity = validate_canonical_report(_valid_canonical())
    assert validity.is_valid is True


def test_aggregate_marks_run_failed_when_canonical_is_invalid(monkeypatch):
    engine = _mk_engine()

    with Session(engine) as session:
        run = EvaluationRun(startup_id="startup-100", status="queued")
        session.add(run)
        session.commit()
        session.refresh(run)

        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-1",
            document_type="pitch_deck",
            processing_status="completed",
            extraction_status="done",
            source_file_url_or_path="dummy.pdf",
            artifact_metadata_json=json.dumps(
                {"canonical_evaluation": _invalid_canonical(startup_id="")}),
        )
        session.add(doc)
        session.commit()

        def _fake_get_session():
            with Session(engine) as fake:
                yield fake

        monkeypatch.setattr(
            "src.modules.evaluation.application.use_cases.aggregate_evaluation.get_session",
            _fake_get_session,
        )

        aggregate_evaluation_run(run.id)

    with Session(engine) as verify:
        refreshed = verify.get(EvaluationRun, run.id)
        assert refreshed.status == "failed"
        assert refreshed.overall_score is None
        assert "Invalid canonical evaluation report" in (
            refreshed.failure_reason or "")


def test_aggregate_marks_run_completed_when_canonical_is_valid(monkeypatch):
    engine = _mk_engine()

    with Session(engine) as session:
        run = EvaluationRun(startup_id="startup-200", status="queued")
        session.add(run)
        session.commit()
        session.refresh(run)

        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-2",
            document_type="pitch_deck",
            processing_status="completed",
            extraction_status="done",
            source_file_url_or_path="dummy.pdf",
            artifact_metadata_json=json.dumps(
                {"canonical_evaluation": _valid_canonical(startup_id="startup-200")}),
        )
        session.add(doc)
        session.commit()

        def _fake_get_session():
            with Session(engine) as fake:
                yield fake

        monkeypatch.setattr(
            "src.modules.evaluation.application.use_cases.aggregate_evaluation.get_session",
            _fake_get_session,
        )

        aggregate_evaluation_run(run.id)

    with Session(engine) as verify:
        refreshed = verify.get(EvaluationRun, run.id)
        assert refreshed.status == "completed"
        assert refreshed.overall_score == 62.0
        assert refreshed.failure_reason is None


def test_report_endpoint_returns_409_for_invalid_canonical_completed_run():
    engine = _mk_engine()

    with Session(engine) as session:
        run = EvaluationRun(startup_id="startup-300", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)

        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-3",
            document_type="pitch_deck",
            processing_status="completed",
            extraction_status="done",
            source_file_url_or_path="dummy.pdf",
            artifact_metadata_json=json.dumps(
                {"canonical_evaluation": _invalid_canonical(startup_id="")}),
        )
        session.add(doc)
        session.commit()
        run_id = run.id

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(evaluation_router, prefix="/api/v1/evaluations")

    def _override_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[router_get_session] = _override_session
    client = TestClient(app)

    resp = client.get(f"/api/v1/evaluations/{run_id}/report")
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "EVALUATION_INVALID_REPORT"
    assert "not a valid scored result" in body["message"]


def test_report_endpoint_backfills_startup_id_when_scores_are_valid():
    engine = _mk_engine()

    with Session(engine) as session:
        run = EvaluationRun(startup_id="st03", status="completed")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

        canonical = _valid_canonical(startup_id="")
        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-4",
            document_type="pitch_deck",
            processing_status="completed",
            extraction_status="done",
            source_file_url_or_path="dummy.pdf",
            artifact_metadata_json=json.dumps(
                {"canonical_evaluation": canonical}),
        )
        session.add(doc)
        session.commit()

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(evaluation_router, prefix="/api/v1/evaluations")

    def _override_session():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[router_get_session] = _override_session
    client = TestClient(app)

    resp = client.get(f"/api/v1/evaluations/{run_id}/report")
    assert resp.status_code == 200
    body = resp.json()
    report = body.get("report", body)  # support envelope or raw
    assert report["startup_id"] == "st03"
    assert report["overall_result"]["overall_score"] == 62.0


def test_process_document_propagates_startup_id_from_run(monkeypatch, tmp_path):
    from src.modules.evaluation.application.use_cases.process_document import process_document

    engine = _mk_engine()
    pdf_path = tmp_path / "dummy.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    with Session(engine) as session:
        run = EvaluationRun(startup_id="startup-999", status="processing")
        session.add(run)
        session.commit()
        session.refresh(run)

        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-startup",
            document_type="pitch_deck",
            processing_status="queued",
            extraction_status="pending",
            source_file_url_or_path=str(pdf_path),
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        doc_id = doc.id

    def _fake_get_session():
        with Session(engine) as s:
            yield s

    class _ClsItem:
        def __init__(self, value: str):
            self.value = value
            self.confidence = "Medium"
            self.resolution_source = "inferred"
            self.supporting_evidence_locations = []

    class _FakeClassification:
        stage = _ClsItem("Seed")
        main_industry = _ClsItem("AI")
        subindustry = _ClsItem("SaaS")
        operational_notes = []

        def model_dump_json(self, indent=2):
            return "{}"

        def model_copy(self, update=None):
            return self

    class _FakeEvidenceCriterion:
        gaps = []

    class _FakeEvidence:
        criteria_evidence = [_FakeEvidenceCriterion()]

        def model_dump_json(self, indent=2):
            return "{}"

    class _FakeRaw:
        pass

    class _FakeNarrative:
        overall_explanation = "ok"
        top_strengths = ["s"]
        top_concerns = ["c"]

    class _FakeReport:
        overall_result_narrative = _FakeNarrative()
        recommendations = []
        key_questions = []
        operational_notes = []

    class _FakePipeline:
        def __init__(self, pack_name: str):
            self.pack_name = pack_name

        def classify_startup(self, full_text, images, classification_context=None):
            return _FakeClassification()

        def map_evidence(self, full_text, images):
            return _FakeEvidence()

        def judge_raw_criteria(self, evidence_result_json, full_text, images):
            return _FakeRaw()

        def write_report(self, scoring_result_json, document_type="pitch_deck", classification_json="{}"):
            return _FakeReport()

    class _FakeScorer:
        def __init__(self, total_pages=1):
            self.total_pages = total_pages

        def score(self, classification, evidence, raw_judgments):
            return DeterministicScoringResult(
                effective_weights={"Problem_&_Customer_Pain": 1.0},
                criteria_results=[
                    CanonicalCriterionResult(
                        criterion="Problem_&_Customer_Pain",
                        status="scored",
                        raw_score=70.0,
                        final_score=70.0,
                        weighted_contribution=70.0,
                        confidence="Medium",
                        cap_summary=CapSummary(
                            core_cap=10.0,
                            stage_cap=10.0,
                            evidence_quality_cap=10.0,
                            contradiction_cap=10.0,
                            contradiction_penalty_points=0.0,
                        ),
                        evidence_strength_summary="DIRECT",
                        evidence_locations=[],
                        supporting_pages_count=1,
                        strengths=["x"],
                        concerns=[],
                        explanation="ok",
                    )
                ],
                overall_result=CanonicalOverallResult(
                    overall_score=70.0,
                    overall_confidence="Medium",
                    evidence_coverage="moderate",
                    interpretation_band="strong",
                    stage_context_note="Seed",
                ),
                processing_warnings=[],
            )

    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.get_session",
        _fake_get_session,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.PipelineLLMServices",
        _FakePipeline,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.DeterministicScoringService",
        _FakeScorer,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.PDFParser.extract_text_and_images",
        lambda local_file_path, extract_images=True, **kwargs: [
            {"text": "hello", "image_path": None}],
    )

    process_document(doc_id)

    with Session(engine) as verify:
        refreshed_doc = verify.get(EvaluationDocument, doc_id)
        metadata = json.loads(refreshed_doc.artifact_metadata_json)
        canonical = metadata.get("canonical_evaluation")
        assert canonical["startup_id"] == "startup-999"


def test_aggregate_surfaces_document_failure_reason_when_no_canonical(monkeypatch):
    engine = _mk_engine()

    with Session(engine) as session:
        run = EvaluationRun(startup_id="startup-failed", status="queued")
        session.add(run)
        session.commit()
        session.refresh(run)

        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-bp-failed",
            document_type="business_plan",
            processing_status="failed",
            extraction_status="failed",
            source_file_url_or_path="dummy.pdf",
            summary="1 validation error for CanonicalNarrative: FINANCIAL_IMPROVEMENT",
        )
        session.add(doc)
        session.commit()
        run_id = run.id

    def _fake_get_session():
        with Session(engine) as s:
            yield s

    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.aggregate_evaluation.get_session",
        _fake_get_session,
    )

    aggregate_evaluation_run(run_id)

    with Session(engine) as verify:
        refreshed = verify.get(EvaluationRun, run_id)
        assert refreshed.status == "failed"
        assert "Document failures:" in (refreshed.failure_reason or "")
        assert "FINANCIAL_IMPROVEMENT" in (refreshed.failure_reason or "")


def test_process_document_accepts_financial_improvement_recommendation(monkeypatch, tmp_path):
    from src.modules.evaluation.application.use_cases.process_document import process_document

    engine = _mk_engine()
    pdf_path = tmp_path / "business-plan.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    with Session(engine) as session:
        run = EvaluationRun(startup_id="startup-bp", status="processing")
        session.add(run)
        session.commit()
        session.refresh(run)

        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-bp",
            document_type="business_plan",
            processing_status="queued",
            extraction_status="pending",
            source_file_url_or_path=str(pdf_path),
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        doc_id = doc.id

    def _fake_get_session():
        with Session(engine) as s:
            yield s

    class _ClsItem:
        def __init__(self, value: str):
            self.value = value
            self.confidence = "Medium"
            self.resolution_source = "inferred"
            self.supporting_evidence_locations = []

    class _FakeClassification:
        stage = _ClsItem("Seed")
        main_industry = _ClsItem("AI")
        subindustry = _ClsItem("SaaS")
        operational_notes = []

        def model_dump_json(self, indent=2):
            return "{}"

        def model_copy(self, update=None):
            return self

    class _FakeEvidenceCriterion:
        gaps = []

    class _FakeEvidence:
        criteria_evidence = [_FakeEvidenceCriterion()]

        def model_dump_json(self, indent=2):
            return "{}"

    class _FakeRaw:
        pass

    class _FakeNarrative:
        overall_explanation = "ok"
        top_strengths = ["s"]
        top_concerns = ["c"]

    class _FakeRecommendation:
        def model_dump(self):
            return {
                "category": "FINANCIAL_IMPROVEMENT",
                "priority": 1,
                "recommendation": "Bo sung mo hinh tai chinh chi tiet.",
                "rationale": "Nha dau tu can thay dong tien va gia dinh ro rang.",
                "expected_impact": "Business_Model_&_Go_to_Market",
            }

    class _FakeReport:
        overall_result_narrative = _FakeNarrative()
        recommendations = [_FakeRecommendation()]
        key_questions = []
        operational_notes = []
        top_risks = []

    class _FakePipeline:
        def __init__(self, pack_name: str):
            self.pack_name = pack_name

        def classify_startup(self, full_text, images, classification_context=None):
            return _FakeClassification()

        def map_evidence(self, full_text, images):
            return _FakeEvidence()

        def judge_raw_criteria(self, evidence_result_json, full_text, images):
            return _FakeRaw()

        def write_report(self, scoring_result_json, document_type="pitch_deck", classification_json="{}"):
            return _FakeReport()

    class _FakeScorer:
        def __init__(self, total_pages=1):
            self.total_pages = total_pages

        def score(self, classification, evidence, raw_judgments):
            return DeterministicScoringResult(
                effective_weights={"Problem_&_Customer_Pain": 1.0},
                criteria_results=[
                    CanonicalCriterionResult(
                        criterion="Problem_&_Customer_Pain",
                        status="scored",
                        raw_score=70.0,
                        final_score=70.0,
                        weighted_contribution=70.0,
                        confidence="Medium",
                        cap_summary=CapSummary(
                            core_cap=10.0,
                            stage_cap=10.0,
                            evidence_quality_cap=10.0,
                            contradiction_cap=10.0,
                            contradiction_penalty_points=0.0,
                        ),
                        evidence_strength_summary="DIRECT",
                        evidence_locations=[],
                        supporting_pages_count=1,
                        strengths=["x"],
                        concerns=[],
                        explanation="ok",
                    )
                ],
                overall_result=CanonicalOverallResult(
                    overall_score=70.0,
                    overall_confidence="Medium",
                    evidence_coverage="moderate",
                    interpretation_band="strong",
                    stage_context_note="Seed",
                ),
                processing_warnings=[],
            )

    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.get_session",
        _fake_get_session,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.PipelineLLMServices",
        _FakePipeline,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.DeterministicScoringService",
        _FakeScorer,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.PDFParser.extract_text_and_images",
        lambda local_file_path, extract_images=True, **kwargs: [
            {"text": "hello", "image_path": None}],
    )

    process_document(doc_id)

    with Session(engine) as verify:
        refreshed_doc = verify.get(EvaluationDocument, doc_id)
        assert refreshed_doc.processing_status == "completed"
        metadata = json.loads(refreshed_doc.artifact_metadata_json)
        report = metadata["canonical_evaluation"]["narrative"]
        assert report["recommendations"][0]["category"] == "FINANCIAL_IMPROVEMENT"


def test_process_document_normalizes_single_source_evidence_ids(monkeypatch, tmp_path):
    from src.modules.evaluation.application.use_cases.process_document import process_document
    from src.modules.evaluation.application.dto.canonical_schema import EvidenceLocation

    engine = _mk_engine()
    pdf_path = tmp_path / "single-source.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    with Session(engine) as session:
        run = EvaluationRun(startup_id="startup-docid", status="processing")
        session.add(run)
        session.commit()
        session.refresh(run)

        doc = EvaluationDocument(
            evaluation_run_id=run.id,
            document_id="doc-public-id",
            document_type="business_plan",
            processing_status="queued",
            extraction_status="pending",
            source_file_url_or_path=str(pdf_path),
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        doc_id = doc.id

    def _fake_get_session():
        with Session(engine) as s:
            yield s

    class _ClsItem:
        def __init__(self, value: str):
            self.value = value
            self.confidence = "Medium"
            self.resolution_source = "inferred"
            self.supporting_evidence_locations = [
                {
                    "source_id": "stale-id",
                    "slide_number_or_page_number": 2,
                    "excerpt_or_summary": "evidence",
                    "section_name": "Team",
                }
            ]

    class _FakeClassification:
        stage = _ClsItem("Seed")
        main_industry = _ClsItem("AI")
        subindustry = _ClsItem("SaaS")
        operational_notes = []

        def model_dump_json(self, indent=2):
            return "{}"

        def model_copy(self, update=None):
            return self

    class _FakeEvidenceCriterion:
        gaps = []

    class _FakeEvidence:
        criteria_evidence = [_FakeEvidenceCriterion()]

        def model_dump_json(self, indent=2):
            return "{}"

    class _FakeRaw:
        pass

    class _FakeNarrative:
        overall_explanation = "ok"
        top_strengths = ["s"]
        top_concerns = ["c"]

    class _FakeReport:
        overall_result_narrative = _FakeNarrative()
        recommendations = []
        key_questions = []
        operational_notes = []
        top_risks = []

    class _FakePipeline:
        def __init__(self, pack_name: str):
            self.pack_name = pack_name

        def classify_startup(self, full_text, images, classification_context=None):
            return _FakeClassification()

        def map_evidence(self, full_text, images):
            return _FakeEvidence()

        def judge_raw_criteria(self, evidence_result_json, full_text, images):
            return _FakeRaw()

        def write_report(self, scoring_result_json, document_type="pitch_deck", classification_json="{}"):
            return _FakeReport()

    class _FakeScorer:
        def __init__(self, total_pages=1):
            self.total_pages = total_pages

        def score(self, classification, evidence, raw_judgments):
            return DeterministicScoringResult(
                effective_weights={"Problem_&_Customer_Pain": 1.0},
                criteria_results=[
                    CanonicalCriterionResult(
                        criterion="Problem_&_Customer_Pain",
                        status="scored",
                        raw_score=70.0,
                        final_score=70.0,
                        weighted_contribution=70.0,
                        confidence="Medium",
                        cap_summary=CapSummary(
                            core_cap=10.0,
                            stage_cap=10.0,
                            evidence_quality_cap=10.0,
                            contradiction_cap=10.0,
                            contradiction_penalty_points=0.0,
                        ),
                        evidence_strength_summary="DIRECT",
                        evidence_locations=[
                            EvidenceLocation(
                                source_type="Business Plan",
                                source_id="wrong-id",
                                slide_number_or_page_number=3,
                                excerpt_or_summary="proof",
                            )
                        ],
                        supporting_pages_count=1,
                        strengths=["x"],
                        concerns=[],
                        explanation="ok",
                    )
                ],
                overall_result=CanonicalOverallResult(
                    overall_score=70.0,
                    overall_confidence="Medium",
                    evidence_coverage="moderate",
                    interpretation_band="strong",
                    stage_context_note="Seed",
                ),
                processing_warnings=[],
            )

    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.get_session",
        _fake_get_session,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.PipelineLLMServices",
        _FakePipeline,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.DeterministicScoringService",
        _FakeScorer,
    )
    monkeypatch.setattr(
        "src.modules.evaluation.application.use_cases.process_document.PDFParser.extract_text_and_images",
        lambda local_file_path, extract_images=True, **kwargs: [
            {"text": "hello", "image_path": None}],
    )

    process_document(doc_id)

    with Session(engine) as verify:
        refreshed_doc = verify.get(EvaluationDocument, doc_id)
        metadata = json.loads(refreshed_doc.artifact_metadata_json)
        canonical = metadata["canonical_evaluation"]
        assert canonical["classification"]["stage"]["supporting_evidence_locations"][0]["source_id"] == "doc-public-id"
        assert canonical["criteria_results"][0]["evidence_locations"][0]["source_id"] == "doc-public-id"
