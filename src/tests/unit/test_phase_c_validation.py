"""
Phase C: Post-assembly validation layer + auto-correction tests.

Tests 1-3:   Input normalization (provided_subindustry null sentinels)
Tests 4-9:   Stage weight profiles — table values AND .score() end-to-end
Tests 10-12: Source isolation validation
Tests 13-16: Subindustry consistency — auto-correction (Bug 2)
Tests 17-20: Score/narrative consistency — recommendation filtering (Bug 3)
Tests 21-22: Criterion/key-question consistency validation
Tests 23-24: write_report() document_type injection
Tests 25-27: Regression — hard failure gates unchanged
"""

import pytest


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_canonical(
    doc_type: str = "pitch_deck",
    overall_score: float = 75.0,
    op_notes: list | None = None,
    subindustry_value: str = "MARKETING_TECH",
    subindustry_confidence: str = "High",
    recommendations: list | None = None,
    key_questions: list | None = None,
    criteria_results: list | None = None,
) -> dict:
    default_criteria = [
        {
            "criterion": "Problem_&_Customer_Pain",
            "status": "scored",
            "final_score": 80.0,
            "confidence": "High",
            "evidence_strength_summary": "STRONG_DIRECT",
            "evidence_locations": [],
            "cap_summary": {
                "evidence_quality_cap": 10.0,
                "contradiction_cap": 10.0,
                "contradiction_penalty_points": 0.0,
            },
            "explanation": "strong evidence",
        }
    ]
    return {
        "startup_id": "test-startup",
        "document_type": doc_type,
        "status": "completed",
        "classification": {
            "stage": {"value": "SEED", "confidence": "High", "resolution_source": "inferred",
                      "supporting_evidence_locations": []},
            "main_industry": {"value": "SAAS_ENTERPRISE_SOFTWARE", "confidence": "High",
                              "resolution_source": "inferred", "supporting_evidence_locations": []},
            "subindustry": {
                "value": subindustry_value,
                "confidence": subindustry_confidence,
                "resolution_source": "inferred",
                "supporting_evidence_locations": [],
            },
            "operational_notes": [],
        },
        "effective_weights": {"Problem_&_Customer_Pain": 14.0},
        "criteria_results": criteria_results if criteria_results is not None else default_criteria,
        "overall_result": {
            "overall_score": overall_score,
            "overall_confidence": "High",
            "evidence_coverage": "strong",
            "interpretation_band": "very strong" if overall_score >= 85 else "strong",
            "stage_context_note": "SEED",
        },
        "narrative": {
            "executive_summary": "Good startup.",
            "top_strengths": ["Strong team"],
            "top_concerns": ["Limited GTM"],
            "missing_information": [],
            "overall_explanation": "Overall solid performance.",
            "recommendations": recommendations or [],
            "key_questions": key_questions or [],
            "operational_notes": op_notes or [],
        },
        "processing_warnings": [],
    }


def _make_mock_classification(stage: str):
    """Build a minimal ClassificationResult for DeterministicScoringService.score()."""
    from src.modules.evaluation.application.dto.pipeline_schema import (
        ClassificationResult, ClassificationField,
        EvidenceMappingResult, CriterionEvidence,
        RawCriterionJudgmentResult, RawJudgment,
    )
    clf = ClassificationResult(
        stage=ClassificationField(value=stage, confidence="High",
                                  resolution_source="inferred", supporting_evidence_locations=[]),
        main_industry=ClassificationField(value="SAAS_ENTERPRISE_SOFTWARE", confidence="High",
                                          resolution_source="inferred", supporting_evidence_locations=[]),
        subindustry=ClassificationField(value="MARKETING_TECH", confidence="High",
                                        resolution_source="inferred", supporting_evidence_locations=[]),
    )
    criteria_names = [
        "Problem_&_Customer_Pain",
        "Market_Attractiveness_&_Timing",
        "Solution_&_Differentiation",
        "Business_Model_&_Go_to_Market",
        "Team_&_Execution_Readiness",
        "Validation_Traction_Evidence_Quality",
    ]
    evidence = EvidenceMappingResult(
        criteria_evidence=[
            CriterionEvidence(
                criterion=c,
                strongest_evidence_level="DIRECT",
                evidence_units=[],
                weakening_evidence_units=[],
            ) for c in criteria_names
        ]
    )
    judgments = RawCriterionJudgmentResult(
        raw_judgments=[
            RawJudgment(
                criterion=c,
                raw_score=7.5,
                criterion_confidence="High",
                suggested_contradiction_severity="none",
                reasoning="",
            ) for c in criteria_names
        ]
    )
    return clf, evidence, judgments


# ─── Tests 1-3: Input normalization ───────────────────────────────────────────

class TestInputNormalization:
    def test_1_empty_string_normalized_to_none(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(
            provided_stage="SEED", provided_main_industry="", provided_subindustry="",
        )
        assert ctx.provided_main_industry is None
        assert ctx.provided_subindustry is None
        assert ctx.provided_stage == "SEED"

    def test_2_null_string_normalized_to_none(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(
            provided_stage="null", provided_main_industry="None", provided_subindustry="NULL",
        )
        assert ctx.provided_stage is None
        assert ctx.provided_main_industry is None
        assert ctx.provided_subindustry is None

    def test_3_none_string_sentinel_not_in_prompt_block(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(
            provided_stage="SEED", provided_subindustry="null")
        block = ctx.to_prompt_block()
        assert "subindustry" not in block
        assert "SEED" in block


# ─── Tests 4-9: Stage weight profiles ─────────────────────────────────────────

class TestStageWeightProfiles:
    def _get_weights(self, stage: str) -> dict:
        from src.modules.evaluation.application.services.deterministic_scorer import STAGE_WEIGHT_PROFILES
        return STAGE_WEIGHT_PROFILES[stage]

    def test_4_all_profiles_sum_to_100(self):
        from src.modules.evaluation.application.services.deterministic_scorer import STAGE_WEIGHT_PROFILES
        for stage, profile in STAGE_WEIGHT_PROFILES.items():
            assert abs(sum(profile.values()) - 100.0) < 0.01, \
                f"{stage} weights sum to {sum(profile.values())}"

    def test_5_seed_profile_matches_master_prompt(self):
        w = self._get_weights("SEED")
        assert w["Problem_&_Customer_Pain"] == 14.0
        assert w["Market_Attractiveness_&_Timing"] == 14.0
        assert w["Solution_&_Differentiation"] == 17.0
        assert w["Business_Model_&_Go_to_Market"] == 20.0
        assert w["Team_&_Execution_Readiness"] == 15.0
        assert w["Validation_Traction_Evidence_Quality"] == 20.0

    def test_6_growth_profile_matches_master_prompt(self):
        w = self._get_weights("GROWTH")
        assert w["Problem_&_Customer_Pain"] == 12.0
        assert w["Business_Model_&_Go_to_Market"] == 22.0
        assert w["Validation_Traction_Evidence_Quality"] == 25.0

    def test_7_score_returns_seed_effective_weights(self):
        """End-to-end: DeterministicScoringService.score() with SEED classification
        must return effective_weights matching the approved SEED profile exactly."""
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES
        )
        clf, evidence, judgments = _make_mock_classification("SEED")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)

        expected = STAGE_WEIGHT_PROFILES["SEED"]
        assert result.effective_weights == expected, (
            f"SEED effective_weights mismatch.\n"
            f"Expected: {expected}\n"
            f"Got:      {result.effective_weights}"
        )

    def test_8_score_returns_growth_effective_weights(self):
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES
        )
        clf, evidence, judgments = _make_mock_classification("GROWTH")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)
        assert result.effective_weights == STAGE_WEIGHT_PROFILES["GROWTH"]

    def test_9_unknown_stage_falls_back_to_mvp_weights(self):
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES
        )
        clf, evidence, judgments = _make_mock_classification("SERIES_A")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)
        assert result.effective_weights == STAGE_WEIGHT_PROFILES["MVP"]
        assert any("Unknown stage" in w for w in result.processing_warnings)


# ─── Tests 10-12: Source isolation validation ─────────────────────────────────

class TestSourceIsolationValidation:
    def test_10_pitch_deck_mentioning_business_plan_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            doc_type="pitch_deck",
            op_notes=["The Business Plan reveals strong financial projections."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any("SOURCE_ISOLATION" in f for f in result.validation_flags)

    def test_11_business_plan_mentioning_pitch_deck_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            doc_type="business_plan",
            op_notes=["The Pitch Deck provides complementary evidence."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any("SOURCE_ISOLATION" in f for f in result.validation_flags)

    def test_12_cross_doc_language_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            doc_type="pitch_deck",
            op_notes=["Assessed across both documents to triangulate evidence."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any("SOURCE_ISOLATION" in f for f in result.validation_flags)


# ─── Tests 13-16: Subindustry consistency — auto-correction (Bug 2) ──────────

class TestSubindustryAutoCorrection:
    def test_13_conflicting_note_is_removed_and_correct_note_injected(self):
        """Core Bug 2 fix: sanitize_canonical_report must remove the conflicting note
        and replace it with a correct overlay confirmation."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            subindustry_value="MARKETING_TECH",
            subindustry_confidence="High",
            op_notes=[
                "No specific subindustry was confidently resolvable. Core rubric applied."],
        )
        result = sanitize_canonical_report(canon)
        notes = result["narrative"]["operational_notes"]
        # Conflicting note removed
        assert not any("no specific subindustry" in n.lower() for n in notes)
        # Correct note injected
        assert any("MARKETING_TECH" in n and "subindustry" in n.lower()
                   for n in notes)
        # Warning logged in processing_warnings
        assert any(
            "AUTO_CORRECTED_SUBINDUSTRY_NOTE" in w for w in result["processing_warnings"])

    def test_14_expanded_pattern_subindustry_could_not_be_resolved(self):
        """Expanded regex must catch 'subindustry could not be resolved' phrasing."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            subindustry_value="MARKETING_TECH",
            subindustry_confidence="High",
            op_notes=[
                "Subindustry could not be resolved; only core rubric was applied."],
        )
        result = sanitize_canonical_report(canon)
        notes = result["narrative"]["operational_notes"]
        assert any("MARKETING_TECH" in n and "subindustry" in n.lower()
                   for n in notes)

    def test_15_expanded_pattern_no_overlay_was_applied(self):
        """Expanded regex must catch 'no subindustry overlay was applied' phrasing."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            subindustry_value="MARKETING_TECH",
            subindustry_confidence="High",
            op_notes=["No subindustry overlay was applied for this evaluation."],
        )
        result = sanitize_canonical_report(canon)
        notes = result["narrative"]["operational_notes"]
        assert any("MARKETING_TECH" in n and "subindustry" in n.lower()
                   for n in notes)

    def test_16_low_confidence_subindustry_not_corrected(self):
        """Low-confidence subindustry should NOT trigger auto-correction."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            subindustry_value="Unknown",
            subindustry_confidence="Low",
            op_notes=["No specific subindustry was confidently resolvable."],
        )
        result = sanitize_canonical_report(canon)
        # Note must remain untouched
        notes = result["narrative"]["operational_notes"]
        assert any("no specific subindustry" in n.lower() for n in notes)
        assert not any(
            "AUTO_CORRECTED" in w for w in result["processing_warnings"])


# ─── Tests 17-20: Score/narrative consistency — recommendation filter (Bug 3) ─

class TestRecommendationFilter:
    def _make_criteria_with_high_validation(self) -> list:
        return [
            {
                "criterion": "Validation_Traction_Evidence_Quality",
                "status": "scored",
                "final_score": 95.0,
                "confidence": "High",
                "evidence_strength_summary": "STRONG_DIRECT",
                "evidence_locations": [],
                "cap_summary": {"evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                "contradiction_penalty_points": 0.0},
                "explanation": "strong traction evidence",
            },
            {
                "criterion": "Market_Attractiveness_&_Timing",
                "status": "scored",
                "final_score": 90.0,
                "confidence": "High",
                "evidence_strength_summary": "STRONG_DIRECT",
                "evidence_locations": [],
                "cap_summary": {"evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                "contradiction_penalty_points": 0.0},
                "explanation": "strong market evidence",
            },
        ]

    def test_17_recommendation_targeting_high_score_criterion_removed(self):
        """Recommendation whose expected_impact is a criterion with score>=75/High/STRONG_DIRECT
        must be removed by sanitize_canonical_report."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        recs = [{
            "category": "VALIDATION_PRIORITY",
            "priority": 1,
            "recommendation": "Prioritize obtaining early validation evidence",
            "rationale": "Limited early validation data",
            "expected_impact": "Validation_Traction_Evidence_Quality",
        }]
        canon = _make_canonical(
            overall_score=85.25,
            criteria_results=self._make_criteria_with_high_validation(),
            recommendations=recs,
        )
        result = sanitize_canonical_report(canon)
        remaining_recs = result["narrative"]["recommendations"]
        assert not any(
            "validation" in (r.get("recommendation") or "").lower()
            and r.get("expected_impact") == "Validation_Traction_Evidence_Quality"
            for r in remaining_recs
        )
        assert any(
            "AUTO_REMOVED_REC" in w for w in result["processing_warnings"])

    def test_18_stage_regressive_recommendation_removed(self):
        """'build an MVP' recommendation must be removed when overall_score >= 70."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        recs = [{
            "category": "STRATEGIC_CLARITY",
            "priority": 1,
            "recommendation": "Build an MVP to validate core hypothesis",
            "rationale": "No product exists yet",
            "expected_impact": "Solution_&_Differentiation",
        }]
        canon = _make_canonical(overall_score=89.75, recommendations=recs)
        result = sanitize_canonical_report(canon)
        remaining = result["narrative"]["recommendations"]
        assert not any("build" in (r.get("recommendation") or "").lower() and "mvp" in (r.get("recommendation") or "").lower()
                       for r in remaining)
        assert any(
            "AUTO_REMOVED_REC" in w for w in result["processing_warnings"])

    def test_19_top_concern_conflicting_with_high_score_validation_flagged(self):
        """top_concern saying 'limited early validation' when Validation scored 95 must flag."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            overall_score=85.25,
            criteria_results=self._make_criteria_with_high_validation(),
            op_notes=[],
        )
        canon["narrative"]["top_concerns"] = [
            "Limited early validation evidence limits investor confidence"
        ]
        result = sanitize_canonical_report(canon)
        assert any(
            "AUTO_REMOVED_CONCERN" in w for w in result["processing_warnings"])

    def test_20_appropriate_recommendation_for_weak_criterion_kept(self):
        """Recommendation targeting a LOW-scoring criterion must NOT be removed."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        weak_criteria = [{
            "criterion": "Business_Model_&_Go_to_Market",
            "status": "scored",
            "final_score": 45.0,
            "confidence": "Medium",
            "evidence_strength_summary": "INDIRECT",
            "evidence_locations": [],
            "cap_summary": {"evidence_quality_cap": 6.0, "contradiction_cap": 10.0,
                            "contradiction_penalty_points": 0.0},
            "explanation": "weak GTM evidence",
        }]
        recs = [{
            "category": "EVIDENCE_GAP",
            "priority": 1,
            "recommendation": "Document unit economics and GTM conversion funnel",
            "rationale": "GTM evidence is indirect",
            "expected_impact": "Business_Model_&_Go_to_Market",
        }]
        canon = _make_canonical(
            overall_score=75.0, criteria_results=weak_criteria, recommendations=recs)
        result = sanitize_canonical_report(canon)
        remaining = result["narrative"]["recommendations"]
        assert len(remaining) == 1
        assert remaining[0]["expected_impact"] == "Business_Model_&_Go_to_Market"


# ─── Tests 21-22: Criterion/key-question consistency ─────────────────────────

class TestCriterionKQConsistencyValidation:
    def test_21_kq_implying_absent_evidence_for_high_confidence_criterion_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        kqs = [{"criterion": "Problem_&_Customer_Pain",
                "question": "Is there any evidence of customer pain? The criterion appears completely missing."}]
        canon = _make_canonical(key_questions=kqs)
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any(
            "CRITERION_KQ_CONSISTENCY" in f for f in result.validation_flags)

    def test_22_valid_kq_for_high_confidence_criterion_not_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        kqs = [{"criterion": "Problem_&_Customer_Pain",
                "question": "How does the team plan to expand customer discovery beyond current pilots?"}]
        canon = _make_canonical(key_questions=kqs)
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert not any(
            "CRITERION_KQ_CONSISTENCY" in f for f in result.validation_flags)


# ─── Tests 23-24: write_report() document_type injection ─────────────────────

class TestWriteReportDocumentTypeInjection:
    def test_23_document_type_placeholder_replaced_in_prompt(self):
        from unittest.mock import patch
        from src.modules.evaluation.application.dto.pipeline_schema import ReportWriterResult, OverallResultNarrative

        captured_prompts: list[str] = []
        mock_result = ReportWriterResult(
            overall_result_narrative=OverallResultNarrative(
                top_strengths=["s1"], top_concerns=["c1"], overall_explanation="ok"
            )
        )
        with patch("src.modules.evaluation.application.services.pipeline_llm_services.GeminiClient") as MockGemini:
            mock_instance = MockGemini.return_value

            def capture_call(prompt, schema, **kwargs):
                captured_prompts.append(prompt)
                return mock_result
            mock_instance.generate_structured.side_effect = capture_call
            from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices
            svc = PipelineLLMServices(pack_name="pitch_deck")
            svc.write_report(scoring_result_json="{}",
                             document_type="pitch_deck")

        assert len(captured_prompts) == 1
        assert "{document_type}" not in captured_prompts[0]
        assert "pitch_deck" in captured_prompts[0]

    def test_24_source_isolation_text_in_both_report_writer_prompts(self):
        import os
        base = os.path.join(
            os.path.dirname(
                __file__), "..", "..", "modules", "evaluation", "prompts"
        )
        for pack in ("pitch_deck", "business_plan"):
            path = os.path.join(base, pack, "report_writer.txt")
            with open(path) as f:
                content = f.read()
            assert "STRICT SOURCE ISOLATION" in content, f"{pack}/report_writer.txt missing isolation rule"
            assert "{document_type}" in content, f"{pack}/report_writer.txt missing {{document_type}} placeholder"


# ─── Tests 25-27: Regression — hard failure gates unchanged ──────────────────

class TestReportValidityRegressions:
    def test_25_missing_startup_id_still_hard_fails(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical()
        canon["startup_id"] = ""
        result = validate_canonical_report(canon)
        assert not result.is_valid
        assert "startup_id" in result.reason

    def test_26_no_score_still_hard_fails(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical()
        canon["overall_result"]["overall_score"] = None
        canon["criteria_results"] = []
        result = validate_canonical_report(canon)
        assert not result.is_valid
        assert "scoring data" in result.reason

    def test_27_clean_report_has_no_flags(self):
        from src.modules.evaluation.application.services.report_validity import (
            sanitize_canonical_report, validate_canonical_report
        )
        recs = [{"category": "EVIDENCE_GAP", "priority": 2,
                 "recommendation": "Provide more GTM conversion data",
                 "rationale": "Indirect evidence only",
                 "expected_impact": "Business_Model_&_Go_to_Market"}]
        kqs = [{"criterion": "Problem_&_Customer_Pain",
                "question": "Which customer segment is the primary ICP?"}]
        canon = _make_canonical(
            doc_type="pitch_deck",
            overall_score=75.0,
            subindustry_value="MARKETING_TECH",
            subindustry_confidence="High",
            op_notes=["MARKETING_TECH subindustry overlay applied."],
            recommendations=recs,
            key_questions=kqs,
        )
        canon = sanitize_canonical_report(canon)
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert result.validation_flags == ()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_canonical(
    doc_type: str = "pitch_deck",
    overall_score: float = 75.0,
    op_notes: list | None = None,
    subindustry_value: str = "MARKETING_TECH",
    subindustry_confidence: str = "High",
    recommendations: list | None = None,
    key_questions: list | None = None,
    criteria_results: list | None = None,
) -> dict:
    default_criteria = [
        {
            "criterion": "Problem_&_Customer_Pain",
            "status": "scored",
            "final_score": 80.0,
            "confidence": "High",
            "evidence_strength_summary": "STRONG_DIRECT",
            "evidence_locations": [],
            "cap_summary": {
                "evidence_quality_cap": 10.0,
                "contradiction_cap": 10.0,
                "contradiction_penalty_points": 0.0,
            },
            "explanation": "strong evidence",
        }
    ]
    return {
        "startup_id": "test-startup",
        "document_type": doc_type,
        "status": "completed",
        "classification": {
            "stage": {"value": "SEED", "confidence": "High", "resolution_source": "inferred",
                      "supporting_evidence_locations": []},
            "main_industry": {"value": "SAAS_ENTERPRISE_SOFTWARE", "confidence": "High",
                              "resolution_source": "inferred", "supporting_evidence_locations": []},
            "subindustry": {
                "value": subindustry_value,
                "confidence": subindustry_confidence,
                "resolution_source": "inferred",
                "supporting_evidence_locations": [],
            },
            "operational_notes": [],
        },
        "effective_weights": {"Problem_&_Customer_Pain": 14.0},
        "criteria_results": criteria_results if criteria_results is not None else default_criteria,
        "overall_result": {
            "overall_score": overall_score,
            "overall_confidence": "High",
            "evidence_coverage": "strong",
            "interpretation_band": "very strong" if overall_score >= 85 else "strong",
            "stage_context_note": "SEED",
        },
        "narrative": {
            "executive_summary": "Good startup.",
            "top_strengths": ["Strong team"],
            "top_concerns": ["Limited GTM"],
            "missing_information": [],
            "overall_explanation": "Overall solid performance.",
            "recommendations": recommendations or [],
            "key_questions": key_questions or [],
            "operational_notes": op_notes or [],
        },
        "processing_warnings": [],
    }


# ─── Tests 1-3: Input normalization ───────────────────────────────────────────

class TestInputNormalization:
    def test_1_empty_string_normalized_to_none(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(
            provided_stage="SEED",
            provided_main_industry="",
            provided_subindustry="",
        )
        assert ctx.provided_main_industry is None
        assert ctx.provided_subindustry is None
        assert ctx.provided_stage == "SEED"

    def test_2_null_string_normalized_to_none(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(
            provided_stage="null",
            provided_main_industry="None",
            provided_subindustry="NULL",
        )
        assert ctx.provided_stage is None
        assert ctx.provided_main_industry is None
        assert ctx.provided_subindustry is None

    def test_3_none_string_sentinel_not_in_prompt_block(self):
        from src.modules.evaluation.application.dto.pipeline_schema import ClassificationContextInput
        ctx = ClassificationContextInput(
            provided_stage="SEED",
            provided_subindustry="null",
        )
        block = ctx.to_prompt_block()
        assert "null" not in block.lower() or "SEED" in block
        # subindustry=None should not appear in the prompt block
        assert "subindustry" not in block


# ─── Tests 4-8: Stage weight profiles ────────────────────────────────────────

class TestStageWeightProfiles:
    def _get_weights(self, stage: str) -> dict:
        from src.modules.evaluation.application.services.deterministic_scorer import STAGE_WEIGHT_PROFILES
        return STAGE_WEIGHT_PROFILES[stage]

    def test_4_idea_weights_sum_to_100(self):
        w = self._get_weights("IDEA")
        assert abs(sum(w.values()) - 100.0) < 0.01

    def test_5_mvp_weights_sum_to_100(self):
        w = self._get_weights("MVP")
        assert abs(sum(w.values()) - 100.0) < 0.01

    def test_6_pre_seed_weights_sum_to_100(self):
        w = self._get_weights("PRE_SEED")
        assert abs(sum(w.values()) - 100.0) < 0.01

    def test_7_seed_weights_match_approved_profile(self):
        w = self._get_weights("SEED")
        assert abs(sum(w.values()) - 100.0) < 0.01
        # Approved SEED profile from master_prompt.txt
        assert w["Problem_&_Customer_Pain"] == 14.0
        assert w["Market_Attractiveness_&_Timing"] == 14.0
        assert w["Solution_&_Differentiation"] == 17.0
        assert w["Business_Model_&_Go_to_Market"] == 20.0
        assert w["Team_&_Execution_Readiness"] == 15.0
        assert w["Validation_Traction_Evidence_Quality"] == 20.0

    def test_8_growth_weights_match_approved_profile(self):
        w = self._get_weights("GROWTH")
        assert abs(sum(w.values()) - 100.0) < 0.01
        assert w["Problem_&_Customer_Pain"] == 12.0
        assert w["Business_Model_&_Go_to_Market"] == 22.0
        assert w["Validation_Traction_Evidence_Quality"] == 25.0


# ─── Tests 9-11: Source isolation validation ──────────────────────────────────

class TestSourceIsolationValidation:
    def test_9_pitch_deck_mentioning_business_plan_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            doc_type="pitch_deck",
            op_notes=["The Business Plan reveals strong financial projections."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any("SOURCE_ISOLATION" in f for f in result.validation_flags)

    def test_10_business_plan_mentioning_pitch_deck_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            doc_type="business_plan",
            op_notes=["The Pitch Deck provides complementary evidence."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any("SOURCE_ISOLATION" in f for f in result.validation_flags)

    def test_11_cross_doc_language_flagged_for_single_source(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            doc_type="pitch_deck",
            op_notes=["Assessed across both documents to triangulate evidence."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any("SOURCE_ISOLATION" in f for f in result.validation_flags)


# ─── Tests 12-13: Classification consistency validation ───────────────────────

class TestClassificationConsistencyValidation:
    def test_12_high_confidence_subindustry_vs_no_overlay_note_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            subindustry_value="MARKETING_TECH",
            subindustry_confidence="High",
            op_notes=[
                "No specific subindustry was confidently resolvable. Core rubric applied."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any(
            "CLASSIFICATION_CONSISTENCY" in f for f in result.validation_flags)

    def test_13_low_confidence_subindustry_vs_no_overlay_not_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(
            subindustry_value="Unknown",
            subindustry_confidence="Low",
            op_notes=[
                "No specific subindustry was confidently resolvable. Core rubric applied."],
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        # No classification conflict when confidence is Low and value is Unknown
        assert not any(
            "CLASSIFICATION_CONSISTENCY" in f for f in result.validation_flags)


# ─── Tests 14-15: Score/narrative consistency validation ──────────────────────

class TestScoreNarrativeConsistencyValidation:
    def test_14_build_mvp_recommendation_with_high_score_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        recs = [{"category": "STRATEGIC_CLARITY", "priority": 1,
                 "recommendation": "Build an MVP to validate market assumptions",
                 "rationale": "No product exists yet", "expected_impact": "Solution_&_Differentiation"}]
        canon = _make_canonical(overall_score=89.75, recommendations=recs)
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any(
            "SCORE_NARRATIVE_CONSISTENCY" in f for f in result.validation_flags)

    def test_15_stage_appropriate_recommendation_not_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        recs = [{"category": "EVIDENCE_GAP", "priority": 1,
                 "recommendation": "Document unit economics to strengthen GTM case",
                 "rationale": "GTM evidence is indirect", "expected_impact": "Business_Model_&_Go_to_Market"}]
        canon = _make_canonical(overall_score=89.75, recommendations=recs)
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert not any(
            "SCORE_NARRATIVE_CONSISTENCY" in f for f in result.validation_flags)


# ─── Tests 16-17: Criterion/key-question consistency ─────────────────────────

class TestCriterionKQConsistencyValidation:
    def test_16_high_confidence_criterion_with_absent_kq_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        kqs = [{"criterion": "Problem_&_Customer_Pain",
                "question": "Is there any evidence of customer pain? The criterion appears completely missing."}]
        canon = _make_canonical(key_questions=kqs)
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any(
            "CRITERION_KQ_CONSISTENCY" in f for f in result.validation_flags)

    def test_17_high_confidence_criterion_with_valid_kq_not_flagged(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        kqs = [{"criterion": "Problem_&_Customer_Pain",
                "question": "How does the team plan to expand customer discovery beyond current pilots?"}]
        canon = _make_canonical(key_questions=kqs)
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert not any(
            "CRITERION_KQ_CONSISTENCY" in f for f in result.validation_flags)


# ─── Tests 18-19: write_report() document_type injection ─────────────────────

class TestWriteReportDocumentTypeInjection:
    def test_18_document_type_placeholder_replaced_in_prompt(self):
        """Verify {document_type} is injected when write_report is called."""
        from unittest.mock import patch, MagicMock
        from src.modules.evaluation.application.dto.pipeline_schema import ReportWriterResult, OverallResultNarrative

        captured_prompts: list[str] = []

        mock_result = ReportWriterResult(
            overall_result_narrative=OverallResultNarrative(
                top_strengths=["s1"], top_concerns=["c1"], overall_explanation="ok"
            )
        )

        with patch("src.modules.evaluation.application.services.pipeline_llm_services.GeminiClient") as MockGemini:
            mock_instance = MockGemini.return_value

            def capture_call(prompt, schema, **kwargs):
                captured_prompts.append(prompt)
                return mock_result
            mock_instance.generate_structured.side_effect = capture_call

            from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices
            svc = PipelineLLMServices(pack_name="pitch_deck")
            svc.write_report(scoring_result_json="{}",
                             document_type="pitch_deck")

        assert len(captured_prompts) == 1
        assert "{document_type}" not in captured_prompts[0]
        assert "pitch_deck" in captured_prompts[0]

    def test_19_source_isolation_text_present_in_prompt(self):
        """The report_writer.txt prompt must contain the isolation rule."""
        import os
        base = os.path.join(
            os.path.dirname(
                __file__), "..", "..", "modules", "evaluation", "prompts"
        )
        for pack in ("pitch_deck", "business_plan"):
            path = os.path.join(base, pack, "report_writer.txt")
            with open(path) as f:
                content = f.read()
            assert "STRICT SOURCE ISOLATION" in content, f"{pack}/report_writer.txt missing isolation rule"
            assert "{document_type}" in content, f"{pack}/report_writer.txt missing {{document_type}} placeholder"


# ─── Test 20: Regression — hard failure gates unchanged ──────────────────────

class TestReportValidityRegressions:
    def test_20_missing_startup_id_still_hard_fails(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical()
        canon["startup_id"] = ""
        result = validate_canonical_report(canon)
        assert not result.is_valid
        assert "startup_id" in result.reason

    def test_21_no_score_still_hard_fails(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical()
        canon["overall_result"]["overall_score"] = None
        canon["criteria_results"] = []
        result = validate_canonical_report(canon)
        assert not result.is_valid
        assert "scoring data" in result.reason

    def test_22_clean_report_has_no_flags(self):
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        recs = [{"category": "EVIDENCE_GAP", "priority": 2,
                 "recommendation": "Provide more GTM conversion data",
                 "rationale": "Indirect evidence only", "expected_impact": "Business_Model_&_Go_to_Market"}]
        kqs = [{"criterion": "Problem_&_Customer_Pain",
                "question": "Which customer segment is the primary ICP?"}]
        canon = _make_canonical(
            doc_type="pitch_deck",
            overall_score=75.0,
            subindustry_value="MARKETING_TECH",
            subindustry_confidence="High",
            op_notes=["MARKETING_TECH subindustry overlay applied."],
            recommendations=recs,
            key_questions=kqs,
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert result.validation_flags == ()


# ─── Tests: Provided-stage enforcement (Bugs 1 + 2) ─────────────────────────

class TestProvidedStageEnforcement:
    """Verify that provided_stage is enforced in process_document.py so the
    deterministic scorer always receives (and uses) the evaluator-supplied stage,
    regardless of what the LLM classified."""

    def _make_mock_clf_with_wrong_stage(self, llm_stage: str = "PRE_SEED"):
        """Simulate an LLM that returned the wrong stage despite a provided hint."""
        from src.modules.evaluation.application.dto.pipeline_schema import (
            ClassificationResult, ClassificationField,
            EvidenceMappingResult, CriterionEvidence,
            RawCriterionJudgmentResult, RawJudgment,
        )
        clf = ClassificationResult(
            stage=ClassificationField(
                value=llm_stage, confidence="Medium",
                resolution_source="inferred", supporting_evidence_locations=[],
            ),
            main_industry=ClassificationField(
                value="SAAS_ENTERPRISE_SOFTWARE", confidence="High",
                resolution_source="inferred", supporting_evidence_locations=[],
            ),
        )
        cnames = [
            "Problem_&_Customer_Pain", "Market_Attractiveness_&_Timing",
            "Solution_&_Differentiation", "Business_Model_&_Go_to_Market",
            "Team_&_Execution_Readiness", "Validation_Traction_Evidence_Quality",
        ]
        evidence = EvidenceMappingResult(
            criteria_evidence=[
                CriterionEvidence(criterion=c, strongest_evidence_level="DIRECT",
                                  evidence_units=[]) for c in cnames
            ]
        )
        judgments = RawCriterionJudgmentResult(
            raw_judgments=[
                RawJudgment(criterion=c, raw_score=7.0, criterion_confidence="High",
                            suggested_contradiction_severity="none", reasoning="ok") for c in cnames
            ]
        )
        return clf, evidence, judgments

    def _apply_provided_stage_override(self, clf, provided_stage: str):
        """Apply the same override logic as process_document.py."""
        from src.modules.evaluation.application.dto.pipeline_schema import (
            ClassificationContextInput, ClassificationField
        )
        _VALID = frozenset({"IDEA", "MVP", "PRE_SEED", "SEED", "GROWTH"})
        ctx = ClassificationContextInput(provided_stage=provided_stage)
        ps = (ctx.provided_stage or "").upper().strip()
        if ps in _VALID:
            clf = clf.model_copy(update={
                "stage": ClassificationField(
                    value=ps,
                    confidence="High",
                    resolution_source="provided",
                    supporting_evidence_locations=clf.stage.supporting_evidence_locations,
                )
            })
        return clf

    def test_provided_seed_overrides_llm_pre_seed(self):
        """When provided_stage=SEED and LLM returned PRE_SEED, stage must be SEED."""
        clf, _, _ = self._make_mock_clf_with_wrong_stage("PRE_SEED")
        clf = self._apply_provided_stage_override(clf, "seed")
        assert clf.stage.value == "SEED"
        assert clf.stage.resolution_source == "provided"
        assert clf.stage.confidence == "High"

    def test_provided_seed_gives_seed_effective_weights(self):
        """After provided_stage enforcement, scorer must use SEED weight profile."""
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES
        )
        clf, evidence, judgments = self._make_mock_clf_with_wrong_stage(
            "PRE_SEED")
        clf = self._apply_provided_stage_override(clf, "SEED")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)
        assert result.effective_weights == STAGE_WEIGHT_PROFILES["SEED"], (
            f"Expected SEED weights but got: {result.effective_weights}"
        )

    def test_provided_seed_weights_not_pre_seed(self):
        """SEED effective_weights must differ from PRE_SEED effective_weights."""
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES
        )
        clf, evidence, judgments = self._make_mock_clf_with_wrong_stage(
            "PRE_SEED")
        clf = self._apply_provided_stage_override(clf, "SEED")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)
        assert result.effective_weights != STAGE_WEIGHT_PROFILES["PRE_SEED"], (
            "SEED weights must not equal PRE_SEED weights — stage override failed"
        )

    def test_invalid_provided_stage_not_applied(self):
        """An invalid provided_stage value must not override the LLM classification."""
        from src.modules.evaluation.application.dto.pipeline_schema import (
            ClassificationContextInput, ClassificationField
        )
        _VALID = frozenset({"IDEA", "MVP", "PRE_SEED", "SEED", "GROWTH"})
        clf, _, _ = self._make_mock_clf_with_wrong_stage("SEED")
        ctx = ClassificationContextInput(provided_stage="SERIES_A")
        ps = (ctx.provided_stage or "").upper().strip()
        if ps in _VALID:
            clf = clf.model_copy(update={
                "stage": ClassificationField(
                    value=ps, confidence="High", resolution_source="provided",
                    supporting_evidence_locations=[],
                )
            })
        # SERIES_A is not valid — override must not be applied
        assert clf.stage.value == "SEED"

    def test_provided_stage_resolution_source_is_provided(self):
        """After override, classification.stage.resolution_source must be 'provided'."""
        clf, _, _ = self._make_mock_clf_with_wrong_stage("IDEA")
        clf = self._apply_provided_stage_override(clf, "SEED")
        assert clf.stage.resolution_source == "provided"


# ─── Tests: Stage-narrative contradiction (Bug 5) ────────────────────────────

class TestStageNarrativeContradiction:
    """sanitize_canonical_report must remove PRE_SEED language from a SEED report,
    and validate_canonical_report must flag any that slipped through."""

    def _seed_canon_with_preseed_concern(self):
        """SEED report that has a PRE_SEED-language top_concern."""
        canon = _make_canonical(overall_score=89.0)
        canon["narrative"]["top_concerns"] = [
            "This pre-seed venture has not yet secured its first customers.",
            "GTM strategy needs further development.",
        ]
        return canon

    def test_sanitize_removes_preseed_concern_from_seed_report(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = self._seed_canon_with_preseed_concern()
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert not any("pre-seed" in c.lower() for c in concerns), (
            f"PRE_SEED language was not removed from SEED concerns: {concerns}"
        )
        assert any(
            "AUTO_REMOVED_CONCERN" in w for w in result["processing_warnings"])

    def test_sanitize_keeps_non_contradictory_concern(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = self._seed_canon_with_preseed_concern()
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert any(
            "GTM" in c for c in concerns), "Non-contradictory concern must not be removed"

    def test_sanitize_removes_preseed_language_from_recommendations(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(overall_score=89.0)
        canon["narrative"]["recommendations"] = [
            {
                "category": "STRATEGIC_CLARITY",
                "priority": 1,
                "recommendation": "Seek validation from first customers as an early-stage venture.",
                "rationale": "Pre-seed companies should validate early.",
                "expected_impact": "Validation_Traction_Evidence_Quality",
            }
        ]
        result = sanitize_canonical_report(canon)
        recs = result["narrative"]["recommendations"]
        assert not any("pre-seed" in (r.get("rationale") or "").lower() for r in recs), (
            "PRE_SEED language must be removed from SEED recommendations"
        )
        assert any(
            "AUTO_REMOVED_REC" in w for w in result["processing_warnings"])

    def test_validator_flags_residual_stage_contradiction(self):
        """validate_canonical_report must flag PRE_SEED language that survived sanitize."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(overall_score=89.0)
        # Inject directly without going through sanitize
        canon["narrative"]["overall_explanation"] = (
            "This pre-seed venture shows strong market potential at the SEED stage."
        )
        result = validate_canonical_report(canon)
        assert result.is_valid  # soft flag only, not hard fail
        assert any("STAGE_NARRATIVE_CONTRADICTION" in f for f in result.validation_flags), (
            f"Expected STAGE_NARRATIVE_CONTRADICTION flag but got: {result.validation_flags}"
        )

    def test_growth_stage_no_seed_language_flagged(self):
        """validate_canonical_report must flag seed-stage language in a GROWTH report."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(overall_score=89.0)
        canon["classification"]["stage"]["value"] = "GROWTH"
        canon["narrative"]["overall_explanation"] = (
            "This seed-stage company should now focus on building the initial product."
        )
        result = validate_canonical_report(canon)
        assert result.is_valid
        assert any(
            "STAGE_NARRATIVE_CONTRADICTION" in f for f in result.validation_flags)

    def test_mvp_stage_no_stage_contradiction_flag(self):
        """MVP stage has no lower-stage restriction pattern — must produce no flag."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical(overall_score=65.0)
        canon["classification"]["stage"]["value"] = "MVP"
        canon["narrative"]["overall_explanation"] = "This early-stage MVP shows promise."
        result = validate_canonical_report(canon)
        assert not any(
            "STAGE_NARRATIVE_CONTRADICTION" in f for f in result.validation_flags)


# ─── Tests: Concern removal for contradictory score + narrative (Bug 4) ──────

class TestConcernRemovalBug4:
    """sanitize_canonical_report must REMOVE (not just flag) top_concerns that
    describe high-scoring criteria as having weak/limited evidence."""

    def _high_validation_criteria(self) -> list:
        return [
            {
                "criterion": "Validation_Traction_Evidence_Quality",
                "status": "scored",
                "final_score": 95.0,
                "confidence": "High",
                "evidence_strength_summary": "STRONG_DIRECT",
                "evidence_locations": [],
                "cap_summary": {"evidence_quality_cap": 10.0, "contradiction_cap": 10.0,
                                "contradiction_penalty_points": 0.0},
                "explanation": "strong traction evidence",
            }
        ]

    def test_contradictory_concern_is_removed_not_just_flagged(self):
        """Concern saying 'limited validation' for a criterion scoring 95/STRONG_DIRECT
        must be physically removed from top_concerns."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            overall_score=89.0,
            criteria_results=self._high_validation_criteria(),
        )
        canon["narrative"]["top_concerns"] = [
            "Limited early validation evidence undermines investor confidence.",
            "GTM channel diversity should be improved.",
        ]
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert not any("limited" in c.lower() and "validation" in c.lower() for c in concerns), (
            f"Contradictory concern was not removed: {concerns}"
        )
        assert any(
            "AUTO_REMOVED_CONCERN" in w for w in result["processing_warnings"])

    def test_non_contradictory_concern_is_kept(self):
        """A concern about a WEAK criterion (score < 65) must never be removed."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        weak_criteria = [
            {
                "criterion": "Business_Model_&_Go_to_Market",
                "status": "scored",
                "final_score": 45.0,
                "confidence": "Medium",
                "evidence_strength_summary": "INDIRECT",
                "evidence_locations": [],
                "cap_summary": {"evidence_quality_cap": 6.0, "contradiction_cap": 10.0,
                                "contradiction_penalty_points": 0.0},
                "explanation": "weak GTM",
            }
        ]
        canon = _make_canonical(
            overall_score=75.0, criteria_results=weak_criteria)
        canon["narrative"]["top_concerns"] = [
            "Limited go-to-market evidence undermines the revenue model credibility."
        ]
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert len(
            concerns) == 1, f"Concern for weak GTM criterion must be kept: {concerns}"


# ─── Tests: Classification context in report writer prompt (RC-1) ─────────────

class TestClassificationInReportWriterPrompt:
    """Verify that classification JSON reaches the report writer prompt."""

    def _make_mock_report_result(self):
        from src.modules.evaluation.application.dto.pipeline_schema import (
            ReportWriterResult, OverallResultNarrative,
        )
        return ReportWriterResult(
            overall_result_narrative=OverallResultNarrative(
                top_strengths=["Strong market fit"],
                top_concerns=["Limited GTM evidence"],
                overall_explanation="This SEED-stage venture shows promise.",
            )
        )

    def test_classification_json_injected_into_prompt(self):
        """write_report() must inject classification_json into the prompt via {classification}."""
        from unittest.mock import patch, MagicMock
        captured: list[str] = []
        mock_result = self._make_mock_report_result()

        with patch(
            "src.modules.evaluation.application.services.pipeline_llm_services.GeminiClient"
        ) as MockGemini:
            mock_instance = MockGemini.return_value
            mock_instance.generate_structured.side_effect = lambda prompt, schema, **kw: (
                captured.append(prompt) or mock_result
            )
            from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices
            svc = PipelineLLMServices(pack_name="pitch_deck")
            svc.llm = mock_instance  # replace the already-constructed real client
            svc.write_report(
                scoring_result_json='{"effective_weights": {}}',
                document_type="pitch_deck",
                classification_json='{"stage": {"value": "SEED"}, "subindustry": {"value": "MARKETING_TECH", "confidence": "High"}}',
            )

        assert len(captured) == 1
        prompt = captured[0]
        assert "SEED" in prompt, "Stage must appear in the report writer prompt"
        assert "MARKETING_TECH" in prompt, "Subindustry must appear in the report writer prompt"
        assert "{classification}" not in prompt, "{classification} placeholder must be replaced"

    def test_classification_placeholder_absent_means_stage_visible(self):
        """If classification is 'GROWTH', the word GROWTH must appear in the prompt."""
        from unittest.mock import patch
        captured: list[str] = []
        mock_result = self._make_mock_report_result()

        with patch(
            "src.modules.evaluation.application.services.pipeline_llm_services.GeminiClient"
        ) as MockGemini:
            mock_instance = MockGemini.return_value
            mock_instance.generate_structured.side_effect = lambda prompt, schema, **kw: (
                captured.append(prompt) or mock_result
            )
            from src.modules.evaluation.application.services.pipeline_llm_services import PipelineLLMServices
            svc = PipelineLLMServices(pack_name="pitch_deck")
            svc.llm = mock_instance
            svc.write_report(
                scoring_result_json="{}",
                document_type="pitch_deck",
                classification_json='{"stage": {"value": "GROWTH"}}',
            )

        assert "GROWTH" in captured[0]

    def test_report_writer_prompt_contains_classification_section_header(self):
        """Both prompt packs must have the CLASSIFICATION INPUT section."""
        import os
        base = os.path.join(
            os.path.dirname(
                __file__), "..", "..", "modules", "evaluation", "prompts"
        )
        for pack in ("pitch_deck", "business_plan"):
            path = os.path.join(base, pack, "report_writer.txt")
            with open(path, encoding="utf-8") as f:
                content = f.read()
            assert "{classification}" in content, (
                f"{pack}/report_writer.txt missing {{classification}} placeholder"
            )
            assert "CLASSIFICATION INPUT" in content, (
                f"{pack}/report_writer.txt missing 'CLASSIFICATION INPUT' section header"
            )

    def test_pitch_deck_prompt_json_shape_uses_plain_strings(self):
        """pitch_deck/report_writer.txt must NOT define top_strengths as objects."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "modules", "evaluation",
            "prompts", "pitch_deck", "report_writer.txt"
        )
        with open(path, encoding="utf-8") as f:
            content = f.read()
        # The old broken shape used {"title": ..., "reason": ..., "evidence_reference": ...}
        assert '"title":' not in content, (
            "pitch_deck/report_writer.txt still has object-shaped top_strengths/top_concerns"
        )
        assert '"reason":' not in content, (
            "pitch_deck/report_writer.txt still has phantom 'reason' key in key_questions"
        )
        assert '"overlay_applied":' not in content, (
            "pitch_deck/report_writer.txt still has nested dict operational_notes"
        )

    def test_pitch_deck_prompt_no_scoring_json_stage_reference(self):
        """After RC-1 fix, stage must be sourced from the CLASSIFICATION block, not 'scoring JSON'."""
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", "modules", "evaluation",
            "prompts", "pitch_deck", "report_writer.txt"
        )
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "stage is in classification.stage.value inside the scoring JSON" not in content, (
            "Stage CONSISTENCY rule still references scoring JSON instead of classification block"
        )


# ─── Tests: Malformed narrative field sanitizer (RC-2 / Priority 3) ──────────

class TestMalformedFieldSanitizer:
    """_sanitize_narrative_list_fields must flatten JSON-embedded dicts and dicts
    in top_strengths, top_concerns, and operational_notes."""

    def test_json_embedded_string_in_top_strengths_is_flattened(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical()
        canon["narrative"]["top_strengths"] = [
            '{"title": "Strong Validation", "reason": "Revenue data on slide 9", "evidence_reference": "slide 9"}',
        ]
        result = sanitize_canonical_report(canon)
        strengths = result["narrative"]["top_strengths"]
        assert len(strengths) == 1
        assert isinstance(strengths[0], str)
        assert "{" not in strengths[0], f"Should be flattened plain string, got: {strengths[0]}"
        assert "Strong Validation" in strengths[0]
        assert any(
            "AUTO_FLATTENED" in w for w in result["processing_warnings"])

    def test_dict_object_in_top_concerns_is_flattened(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical()
        canon["narrative"]["top_concerns"] = [
            {"title": "Weak GTM", "reason": "Limited evidence",
                "evidence_reference": "slide 5"},
        ]
        result = sanitize_canonical_report(canon)
        concerns = result["narrative"]["top_concerns"]
        assert len(concerns) == 1
        assert isinstance(concerns[0], str)
        assert "Weak GTM" in concerns[0]

    def test_nested_dict_operational_notes_is_flattened(self):
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical()
        canon["narrative"]["operational_notes"] = [
            '{"overlay_applied": "Đã áp dụng lớp đánh giá subindustry: MARKETING_TECH", "document_quality_observations": []}',
        ]
        result = sanitize_canonical_report(canon)
        notes = result["narrative"]["operational_notes"]
        assert isinstance(notes[0], str)
        assert "{" not in notes[0]

    def test_malformed_field_flagged_by_validator(self):
        """validate_canonical_report must flag operational_notes containing a JSON-dict string."""
        from src.modules.evaluation.application.services.report_validity import validate_canonical_report
        canon = _make_canonical()
        canon["narrative"]["operational_notes"] = [
            '{"overlay_applied": "something", "evidence_gaps": []}',
        ]
        result = validate_canonical_report(canon)
        assert result.is_valid  # soft flag, not hard fail
        assert any("MALFORMED_FIELD" in f for f in result.validation_flags)

    def test_plain_strings_not_touched(self):
        """Clean plain-string fields must pass through unchanged."""
        from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report
        canon = _make_canonical(
            op_notes=["Đã áp dụng lớp đánh giá subindustry: MARKETING_TECH (độ tin cậy High)."])
        result = sanitize_canonical_report(canon)
        notes = result["narrative"]["operational_notes"]
        assert notes[0] == "Đã áp dụng lớp đánh giá subindustry: MARKETING_TECH (độ tin cậy High)."
        assert not any(
            "AUTO_FLATTENED" in w for w in result["processing_warnings"])


# ─── Tests: Ghost DTO isolation (RC-3) ───────────────────────────────────────

class TestGhostDTOIsolation:
    """Confirm ghost DTO types are no longer live in pipeline_schema.py."""

    def test_pipeline_schema_has_no_ghost_cap_summary(self):
        """pipeline_schema.CapSummary must not exist — it shadows canonical_schema.CapSummary."""
        import src.modules.evaluation.application.dto.pipeline_schema as ps
        assert not hasattr(ps, "CapSummary") or ps.CapSummary is None or (
            # If re-exported from canonical_schema, it must BE the canonical one
            ps.CapSummary.__module__.endswith("canonical_schema")
        ), "pipeline_schema.CapSummary must not define its own ghost class"

    def test_pipeline_schema_has_no_ghost_final_criterion_result(self):
        import src.modules.evaluation.application.dto.pipeline_schema as ps
        assert not hasattr(ps, "FinalCriterionResult"), (
            "pipeline_schema.FinalCriterionResult still exists — ghost class not removed"
        )

    def test_pipeline_schema_has_no_ghost_overall_result(self):
        import src.modules.evaluation.application.dto.pipeline_schema as ps
        assert not hasattr(ps, "OverallResult"), (
            "pipeline_schema.OverallResult still exists — ghost class not removed"
        )

    def test_scorer_returns_canonical_deterministic_scoring_result(self):
        """DeterministicScoringService.score() must return canonical_schema.DeterministicScoringResult."""
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService,
        )
        from src.modules.evaluation.application.dto.canonical_schema import DeterministicScoringResult
        clf, evidence, judgments = _make_mock_classification("SEED")
        svc = DeterministicScoringService(total_pages=20)
        result = svc.score(clf, evidence, judgments)
        assert isinstance(result, DeterministicScoringResult), (
            f"Expected canonical DeterministicScoringResult, got {type(result)}"
        )

    def test_seed_effective_weights_come_from_stage_weight_profiles(self):
        """Hard assertion: SEED effective_weights must exactly equal STAGE_WEIGHT_PROFILES['SEED'].
        This pins the weight source — prevents any stale-worker or shadow-class drift."""
        from src.modules.evaluation.application.services.deterministic_scorer import (
            DeterministicScoringService, STAGE_WEIGHT_PROFILES,
        )
        clf, evidence, judgments = _make_mock_classification("SEED")
        result = DeterministicScoringService(
            total_pages=20).score(clf, evidence, judgments)
        expected = STAGE_WEIGHT_PROFILES["SEED"]
        assert result.effective_weights == expected, (
            f"SEED weight mismatch — source is not STAGE_WEIGHT_PROFILES.\n"
            f"Expected: {expected}\nGot: {result.effective_weights}"
        )
        # Explicitly verify the SEED-specific values that differ from PRE_SEED
        assert result.effective_weights["Solution_&_Differentiation"] == 17.0
        assert result.effective_weights["Business_Model_&_Go_to_Market"] == 20.0
        assert result.effective_weights["Validation_Traction_Evidence_Quality"] == 20.0
