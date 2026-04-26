"""
Tests for Phase B implementation:
  A. Classification context flow (tests 1-7)
  B. Document result modes (tests 8-13)
  C. Merge behavior (tests 14-17)
  D. Long document handling (tests 18-20)
  E. Regression (tests 21-22)
"""

import pytest
import json
from unittest.mock import patch, MagicMock, PropertyMock
from pydantic import BaseModel


# ── Test 1-3: ClassificationContextInput ─────────────────────────────

class TestClassificationContextInput:
    """Tests 1-3: ClassificationContextInput DTO."""

    def test_1_all_fields_provided(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(
            provided_stage="SEED",
            provided_main_industry="FINTECH",
            provided_subindustry="INSURTECH",
        )
        block = ctx.to_prompt_block()
        assert "Provided stage: SEED" in block
        assert "Provided main_industry: FINTECH" in block
        assert "INSURTECH" in block
        assert "VERIFY" in block

    def test_2_partial_fields(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(provided_stage="MVP")
        block = ctx.to_prompt_block()
        assert "Provided stage: MVP" in block
        assert "main_industry" not in block
        assert "VERIFY" in block

    def test_3_no_fields(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput()
        block = ctx.to_prompt_block()
        assert "No classification context was provided" in block


# ── Test 4-5: SubmitEvaluationRequest accepts context fields ─────────

class TestSubmitEvaluationRequestContext:
    def test_4_request_with_context(self):
        from src.modules.evaluation.application.dto.evaluation_schema import SubmitEvaluationRequest
        req = SubmitEvaluationRequest(
            startup_id="s1",
            documents=[
                {"document_id": "d1", "document_type": "pitch_deck", "file_url_or_path": "/a.pdf"}],
            provided_stage="SEED",
            provided_main_industry="FINTECH",
        )
        assert req.provided_stage == "SEED"
        assert req.provided_main_industry == "FINTECH"
        assert req.provided_subindustry is None

    def test_5_request_without_context_backward_compat(self):
        from src.modules.evaluation.application.dto.evaluation_schema import SubmitEvaluationRequest
        req = SubmitEvaluationRequest(
            startup_id="s1",
            documents=[
                {"document_id": "d1", "document_type": "pitch_deck", "file_url_or_path": "/a.pdf"}],
        )
        assert req.provided_stage is None
        assert req.provided_main_industry is None
        assert req.provided_subindustry is None


# ── Test 6-7: PipelineLLMServices.classify_startup accepts context ───

class TestPipelineLLMServicesContext:
    @patch("src.modules.evaluation.application.services.pipeline_llm_services.GeminiClient")
    def test_6_context_injected_into_prompt(self, MockGemini):
        from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput

        mock_llm = MockGemini.return_value
        mock_llm.generate_structured.return_value = MagicMock()

        svc = PipelineLLMServices(pack_name="pitch_deck")
        ctx = ClassificationContextInput(provided_stage="GROWTH")
        svc.classify_startup("some text", classification_context=ctx)

        call_args = mock_llm.generate_structured.call_args
        prompt = call_args[0][0]
        assert "Provided stage: GROWTH" in prompt

    @patch("src.modules.evaluation.application.services.pipeline_llm_services.GeminiClient")
    def test_7_no_context_default_message(self, MockGemini):
        from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices

        mock_llm = MockGemini.return_value
        mock_llm.generate_structured.return_value = MagicMock()

        svc = PipelineLLMServices(pack_name="pitch_deck")
        svc.classify_startup("some text")

        call_args = mock_llm.generate_structured.call_args
        prompt = call_args[0][0]
        assert "No classification context was provided" in prompt


# ── Test 8-10: canonical_schema additions ────────────────────────────

class TestCanonicalSchemaAdditions:
    def test_8_evidence_location_section_name(self):
        from src.modules.evaluation.application.dto.canonical_schema import EvidenceLocation
        loc = EvidenceLocation(
            source_type="Business Plan",
            source_id="d1",
            slide_number_or_page_number=3,
            excerpt_or_summary="exec summary",
            section_name="Executive Summary",
        )
        assert loc.section_name == "Executive Summary"

    def test_9_evidence_location_section_name_optional(self):
        from src.modules.evaluation.application.dto.canonical_schema import EvidenceLocation
        loc = EvidenceLocation(
            source_type="Pitch Deck",
            source_id="d1",
            slide_number_or_page_number=1,
            excerpt_or_summary="slide 1",
        )
        assert loc.section_name is None

    def test_10_canonical_result_document_type(self):
        from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
        data = _make_canonical_dict(doc_type="pitch_deck")
        result = CanonicalEvaluationResult(**data)
        assert result.document_type == "pitch_deck"

    def test_11_canonical_result_document_type_optional(self):
        from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
        data = _make_canonical_dict()
        del data["document_type"]
        result = CanonicalEvaluationResult(**data)
        assert result.document_type is None


# ── Test 12-13: Feature flags ────────────────────────────────────────

class TestFeatureFlags:
    def test_12_bp_eval_enabled_default(self):
        from src.shared.config.settings import settings
        assert isinstance(settings.BUSINESS_PLAN_EVAL_ENABLED, bool)

    def test_13_merge_eval_enabled_default(self):
        from src.shared.config.settings import settings
        assert isinstance(settings.MERGE_EVAL_ENABLED, bool)


# ── Test 14-17: Merge behavior ───────────────────────────────────────

def _make_canonical_dict(startup_id="s1", doc_type="pitch_deck", stage="SEED",
                         industry="FINTECH", overall_score=6.0, criteria=None):
    """Helper to build a minimal valid canonical dict."""
    if criteria is None:
        criteria = [
            {
                "criterion": "Problem_&_Customer_Pain",
                "status": "scored",
                "raw_score": 6.0,
                "final_score": 6.0,
                "confidence": "High",
                "cap_summary": {"core_cap": None, "stage_cap": None,
                                "evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                "contradiction_penalty_points": 0.0},
                "evidence_strength_summary": "DIRECT",
                "explanation": "Good problem statement",
                "evidence_locations": [
                    {"source_type": "Pitch Deck", "source_id": "d1",
                     "slide_number_or_page_number": 1, "excerpt_or_summary": "test"}
                ],
            }
        ]
    return {
        "startup_id": startup_id,
        "document_type": doc_type,
        "status": "completed",
        "classification": {
            "stage": {"value": stage, "confidence": "High", "resolution_source": "inferred",
                      "supporting_evidence_locations": []},
            "main_industry": {"value": industry, "confidence": "High", "resolution_source": "inferred",
                              "supporting_evidence_locations": []},
            "subindustry": {"value": "Unknown", "confidence": "Low", "resolution_source": "inferred",
                            "supporting_evidence_locations": []},
        },
        "effective_weights": {"Problem_&_Customer_Pain": 1.0},
        "criteria_results": criteria,
        "overall_result": {"overall_score": overall_score, "overall_confidence": "Medium",
                           "evidence_coverage": "moderate", "interpretation_band": "promising but incomplete",
                           "stage_context_note": "test context"},
        "narrative": {
            "executive_summary": f"Summary from {doc_type}",
            "top_strengths": [f"strength from {doc_type}"],
            "top_concerns": [f"concern from {doc_type}"],
            "missing_information": [],
            "overall_explanation": "explained",
            "recommendations": [],
            "key_questions": [],
            "operational_notes": [],
        },
        "processing_warnings": [],
    }


class TestMergeEvaluation:
    def test_14_merge_produces_merged_doc_type(self):
        from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
        from src.modules.evaluation.application.use_cases.merge_evaluation import merge_canonical_results

        pd = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="pitch_deck", overall_score=6.0))
        bp = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="business_plan", overall_score=7.0))
        merged = merge_canonical_results(pd, bp)
        assert merged.document_type == "merged"

    def test_15_merge_takes_higher_criterion_score(self):
        """
        When PD=DIRECT (score 5) and BP=STRONG_DIRECT (score 8),
        BP's stronger evidence wins (STRONG_DIRECT > DIRECT).
        When both are DIRECT, scores are averaged.
        """
        from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
        from src.modules.evaluation.application.use_cases.merge_evaluation import merge_canonical_results

        # PD=DIRECT/5, BP=STRONG_DIRECT/8 → BP wins (stronger evidence)
        pd_criteria = [{"criterion": "Problem_&_Customer_Pain", "status": "scored",
                        "raw_score": 5.0, "final_score": 5.0, "confidence": "Medium",
                        "cap_summary": {"core_cap": None, "stage_cap": None,
                                        "evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                        "contradiction_penalty_points": 0.0},
                        "evidence_strength_summary": "DIRECT",
                        "explanation": "from PD", "evidence_locations": []}]
        bp_criteria = [{"criterion": "Problem_&_Customer_Pain", "status": "scored",
                        "raw_score": 7.0, "final_score": 8.0, "confidence": "High",
                        "cap_summary": {"core_cap": None, "stage_cap": None,
                                        "evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                        "contradiction_penalty_points": 0.0},
                        "evidence_strength_summary": "STRONG_DIRECT",
                        "explanation": "from BP", "evidence_locations": []}]

        pd = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="pitch_deck", criteria=pd_criteria))
        bp = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="business_plan", criteria=bp_criteria))
        merged = merge_canonical_results(pd, bp)

        merged_c = {c.criterion: c for c in merged.criteria_results}
        # Business Plan preferred (STRONG_DIRECT > DIRECT), score = 8.0
        assert merged_c["Problem_&_Customer_Pain"].final_score == 8.0
        assert "ưu tiên Business Plan" in merged_c["Problem_&_Customer_Pain"].explanation

    def test_15b_merge_averages_when_both_direct(self):
        """When both are DIRECT, final_score is averaged."""
        from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
        from src.modules.evaluation.application.use_cases.merge_evaluation import merge_canonical_results

        pd_criteria = [{"criterion": "Problem_&_Customer_Pain", "status": "scored",
                        "raw_score": 6.0, "final_score": 6.0, "confidence": "High",
                        "cap_summary": {"core_cap": None, "stage_cap": None,
                                        "evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                        "contradiction_penalty_points": 0.0},
                        "evidence_strength_summary": "DIRECT",
                        "explanation": "from PD", "evidence_locations": []}]
        bp_criteria = [{"criterion": "Problem_&_Customer_Pain", "status": "scored",
                        "raw_score": 8.0, "final_score": 8.0, "confidence": "High",
                        "cap_summary": {"core_cap": None, "stage_cap": None,
                                        "evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                        "contradiction_penalty_points": 0.0},
                        "evidence_strength_summary": "DIRECT",
                        "explanation": "from BP", "evidence_locations": []}]

        pd = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="pitch_deck", criteria=pd_criteria))
        bp = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="business_plan", criteria=bp_criteria))
        merged = merge_canonical_results(pd, bp)

        merged_c = {c.criterion: c for c in merged.criteria_results}
        # (6+8)/2
        assert merged_c["Problem_&_Customer_Pain"].final_score == 7.0

    def test_16_merge_notes_classification_conflict(self):
        from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
        from src.modules.evaluation.application.use_cases.merge_evaluation import merge_canonical_results

        pd = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="pitch_deck", stage="SEED"))
        bp = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="business_plan", stage="MVP"))
        merged = merge_canonical_results(pd, bp)

        notes = merged.narrative.operational_notes
        assert any("XUNG_ĐỘT_PHÂN_LOẠI" in n for n in notes)

    def test_17_merge_combines_strengths(self):
        from src.modules.evaluation.application.dto.canonical_schema import CanonicalEvaluationResult
        from src.modules.evaluation.application.use_cases.merge_evaluation import merge_canonical_results

        pd = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="pitch_deck"))
        bp = CanonicalEvaluationResult(
            **_make_canonical_dict(doc_type="business_plan"))
        merged = merge_canonical_results(pd, bp)
        assert len(merged.narrative.top_strengths) >= 2


# ── Test 18-20: BP text reduction ────────────────────────────────────

class TestBPTextReduction:
    def test_18_no_reduction_under_threshold(self):
        from src.modules.evaluation.application.services.reduce_bp_text import reduce_business_plan_text
        pages = [{"text": "word " * 100, "page_number": 1}]
        warnings = []
        text, meta = reduce_business_plan_text(pages, warnings)
        assert meta["reduction_applied"] is False
        assert len(warnings) == 0

    def test_19_reduction_over_threshold(self):
        from src.modules.evaluation.application.services.reduce_bp_text import reduce_business_plan_text
        # Create pages that exceed threshold
        pages = [{"text": f"executive summary word " * 500, "page_number": i}
                 for i in range(1, 30)]
        warnings = []
        text, meta = reduce_business_plan_text(
            pages, warnings, word_threshold=5000)
        assert meta["reduction_applied"] is True
        assert meta["reduced_word_count"] <= 5500  # Allow some slack
        assert len(warnings) == 1
        assert "BP_TEXT_REDUCED" in warnings[0]

    def test_20_reduction_preserves_band_structure(self):
        from src.modules.evaluation.application.services.reduce_bp_text import reduce_business_plan_text
        pages = [
            {"text": "executive summary overview " * 200, "page_number": 1},
            {"text": "market tam sam som " * 200, "page_number": 2},
            {"text": "team founder experience " * 200, "page_number": 3},
            {"text": "financial revenue cost " * 200, "page_number": 4},
            {"text": "problem customer pain point " * 2000, "page_number": 5},
        ]
        warnings = []
        text, meta = reduce_business_plan_text(
            pages, warnings, word_threshold=2000)
        assert meta["reduction_applied"] is True
        assert "band_stats" in meta
        assert "executive_summary" in meta["band_stats"]


# ── Test 21-22: Regression ───────────────────────────────────────────

class TestRegression:
    def test_21_deterministic_scorer_unchanged(self):
        """Verify DeterministicScoringService signature is unchanged."""
        from src.modules.evaluation.application.services.deterministic_scorer import DeterministicScoringService
        svc = DeterministicScoringService(total_pages=10)
        assert hasattr(svc, "score")

    def test_22_stage_taxonomy_no_series_a(self):
        """Verify SERIES_A is not in the stage taxonomy."""
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        # Read shared_rule.txt to confirm no SERIES_A
        import os
        prompts_dir = os.path.join(
            os.path.dirname(
                __file__), "..", "..", "modules", "evaluation", "prompts"
        )
        for pack in ["pitch_deck", "business_plan"]:
            shared_rule = os.path.join(prompts_dir, pack, "shared_rule.txt")
            if os.path.exists(shared_rule):
                with open(shared_rule) as f:
                    content = f.read()
                assert "SERIES_A" not in content, f"SERIES_A found in {pack}/shared_rule.txt"
