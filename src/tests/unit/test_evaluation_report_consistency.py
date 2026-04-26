from src.modules.evaluation.application.dto.pipeline_schema import (
    ClassificationField,
    ClassificationResult,
    CriterionEvidence,
    EvidenceMappingResult,
    EvidenceUnit,
    RawCriterionJudgmentResult,
    RawJudgment,
)
from src.modules.evaluation.application.services.deterministic_scorer import (
    DeterministicScoringService,
)
from src.modules.evaluation.application.services.report_validity import (
    sanitize_canonical_report,
)


def _canonical_payload() -> dict:
    return {
        "startup_id": "startup-1",
        "document_type": "pitch_deck",
        "status": "completed",
        "classification": {
            "stage": {"value": "GROWTH", "confidence": "High", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "main_industry": {"value": None, "confidence": "High", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "subindustry": {"value": None, "confidence": "High", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "operational_notes": [],
        },
        "effective_weights": {
            "Problem_&_Customer_Pain": 20.0,
            "Market_Attractiveness_&_Timing": 15.0,
            "Solution_&_Differentiation": 15.0,
            "Business_Model_&_Go_to_Market": 20.0,
            "Team_&_Execution_Readiness": 10.0,
            "Validation_Traction_Evidence_Quality": 20.0,
        },
        "criteria_results": [
            {
                "criterion": "Business_Model_&_Go_to_Market",
                "status": "scored",
                "raw_score": 6.0,
                "final_score": None,
                "weighted_contribution": None,
                "confidence": "Medium",
                "cap_summary": {
                    "core_cap": 10.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 7.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "INDIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 0,
                "strengths": [],
                "concerns": [],
                "explanation": "Revenue streams present but GTM plan remains thin.",
            },
            {
                "criterion": "Validation_Traction_Evidence_Quality",
                "status": "contradictory",
                "raw_score": 8.0,
                "final_score": None,
                "weighted_contribution": None,
                "confidence": "High",
                "cap_summary": {
                    "core_cap": 10.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 8.0,
                    "contradiction_cap": 5.0,
                    "contradiction_penalty_points": 3.0,
                },
                "evidence_strength_summary": "INDIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 0,
                "strengths": [],
                "concerns": [],
                "explanation": "Leadership claims are not backed by quantified traction evidence.",
            },
        ],
        "overall_result": {
            "overall_score": None,
            "overall_confidence": "Medium",
            "evidence_coverage": "strong",
            "interpretation_band": "strong",
            "stage_context_note": "Evaluated against GROWTH expectations.",
        },
        "narrative": {
            "executive_summary": "summary",
            "top_strengths": [],
            "top_concerns": [],
            "top_risks": [],
            "missing_information": [],
            "overall_explanation": "overall",
            "recommendations": [],
            "key_questions": [],
            "operational_notes": [
                "main_industry was classified as OTHER because the deck does not map cleanly to a supported sector."
            ],
        },
        "processing_warnings": [],
    }


def test_deterministic_scorer_keeps_final_score_for_contradictory_criterion():
    scorer = DeterministicScoringService(total_pages=5)
    classification = ClassificationResult(
        stage=ClassificationField(value="GROWTH", confidence="High", resolution_source="inferred", supporting_evidence_locations=[]),
        main_industry=ClassificationField(value="MEDIA", confidence="Medium", resolution_source="inferred", supporting_evidence_locations=[]),
        subindustry=ClassificationField(value=None, confidence="Low", resolution_source="inferred", supporting_evidence_locations=[]),
        operational_notes=[],
    )
    evidence = EvidenceMappingResult(
        criteria_evidence=[
            CriterionEvidence(
                criterion="Validation_Traction_Evidence_Quality",
                strongest_evidence_level="INDIRECT",
                evidence_units=[
                    EvidenceUnit(
                        source_type="Pitch Deck",
                        source_id="doc-1",
                        slide_number_or_page_number=1,
                        excerpt_or_summary="Traction claims are largely qualitative.",
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
                criterion="Validation_Traction_Evidence_Quality",
                raw_score=8.0,
                criterion_confidence="High",
                suggested_core_cap=10.0,
                suggested_stage_cap=10.0,
                suggested_contradiction_severity="severe",
                reasoning="Strong claim but contradiction penalty applies.",
            )
        ]
    )

    result = scorer.score(classification, evidence, raw)
    traction = next(
        c for c in result.criteria_results
        if c.criterion == "Validation_Traction_Evidence_Quality"
    )

    assert traction.status == "contradictory"
    assert traction.final_score is not None
    assert traction.weighted_contribution is not None


def test_sanitize_canonical_report_repairs_scores_industry_and_risks():
    canonical = _canonical_payload()

    result = sanitize_canonical_report(canonical)
    criteria = {
        item["criterion"]: item
        for item in result["criteria_results"]
    }

    assert criteria["Validation_Traction_Evidence_Quality"]["final_score"] == 20.0
    assert criteria["Validation_Traction_Evidence_Quality"]["weighted_contribution"] == 4.0
    assert criteria["Business_Model_&_Go_to_Market"]["final_score"] == 60.0
    assert criteria["Business_Model_&_Go_to_Market"]["weighted_contribution"] == 12.0
    assert result["overall_result"]["overall_score"] == 16.0
    assert result["overall_result"]["evidence_coverage"] == "mixed"

    assert result["classification"]["main_industry"]["value"] == "OTHER"
    assert result["classification"]["subindustry"]["value"] is None
    assert result["classification"]["subindustry"]["confidence"] == "Low"

    risk_types = {risk["risk_type"] for risk in result["narrative"]["top_risks"]}
    assert "Evidence quality risk" in risk_types
    assert "GTM monetization risk" in risk_types
    assert "Market adoption risk" in risk_types


def test_sanitize_pitch_deck_top_risks_are_expanded_for_multi_gap_case():
    canonical = {
        "startup_id": "startup-disabled-jobs",
        "document_type": "pitch_deck",
        "status": "completed",
        "classification": {
            "stage": {"value": "SEED", "confidence": "High", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "main_industry": {"value": "HR_TECH", "confidence": "Medium", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "subindustry": {"value": None, "confidence": "Low", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "operational_notes": [],
        },
        "effective_weights": {
            "Problem_&_Customer_Pain": 16.0,
            "Market_Attractiveness_&_Timing": 16.0,
            "Solution_&_Differentiation": 17.0,
            "Business_Model_&_Go_to_Market": 17.0,
            "Team_&_Execution_Readiness": 16.0,
            "Validation_Traction_Evidence_Quality": 18.0,
        },
        "criteria_results": [
            {
                "criterion": "Solution_&_Differentiation",
                "status": "insufficient_evidence",
                "raw_score": 6.2,
                "final_score": 62.0,
                "weighted_contribution": 10.54,
                "confidence": "Medium",
                "cap_summary": {
                    "core_cap": 8.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 7.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "INDIRECT",
                "evidence_locations": [],
                "strengths": [],
                "concerns": [
                    "Claim khac biet voi Jobmetoo, LinkedIn va Indeed chua co benchmark doc lap hoac user feedback."
                ],
                "explanation": "Pitch deck de cap data mining, pattern recognition va matching algorithm nhung chua mo ta du lieu dau vao, do chinh xac hay cach kiem chung cong nghe.",
            },
            {
                "criterion": "Business_Model_&_Go_to_Market",
                "status": "insufficient_evidence",
                "raw_score": 5.8,
                "final_score": 58.0,
                "weighted_contribution": 9.86,
                "confidence": "Medium",
                "cap_summary": {
                    "core_cap": 8.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 7.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "INDIRECT",
                "evidence_locations": [],
                "strengths": [],
                "concerns": [
                    "Mo hinh doanh thu co referral fee, training, subscription va freemium nhung thieu pricing, CAC, conversion funnel va sales strategy."
                ],
                "explanation": "Go-to-market va monetization da duoc neu nhung kenh tiep can, sales motion va co che chuyen doi van con mo.",
            },
            {
                "criterion": "Team_&_Execution_Readiness",
                "status": "insufficient_evidence",
                "raw_score": 6.0,
                "final_score": 60.0,
                "weighted_contribution": 9.6,
                "confidence": "Medium",
                "cap_summary": {
                    "core_cap": 8.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 7.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "INDIRECT",
                "evidence_locations": [],
                "strengths": [],
                "concerns": [
                    "Deck chi neu ten va vai tro cua 6 thanh vien, thieu founder background, hoc van va track record."
                ],
                "explanation": "Thong tin doi ngu hien con mong va chua du de kiem chung nang luc execution.",
            },
            {
                "criterion": "Validation_Traction_Evidence_Quality",
                "status": "insufficient_evidence",
                "raw_score": 2.2,
                "final_score": 22.0,
                "weighted_contribution": 3.96,
                "confidence": "Low",
                "cap_summary": {
                    "core_cap": 4.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 4.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "ABSENT",
                "evidence_locations": [],
                "strengths": [],
                "concerns": [
                    "Chua co user feedback, pilot, signed partners, revenue hoac traction metrics cho nen tang tuyen dung cho nguoi khuyet tat."
                ],
                "explanation": "Deck chua chung minh nguoi khuyet tat, doanh nghiep tuyen dung va to chuc dao tao san sang su dung hoac tra tien cho nen tang.",
            },
        ],
        "overall_result": {
            "overall_score": 48.0,
            "overall_confidence": "Medium",
            "evidence_coverage": "weak",
            "interpretation_band": "below average",
            "stage_context_note": "Evaluated against SEED expectations.",
        },
        "narrative": {
            "executive_summary": "Nen tang tuyen dung cho nguoi khuyet tat con thieu traction va bang chung thi truong.",
            "top_strengths": [],
            "top_concerns": [
                "Thieu traction, user validation va partner da ky.",
                "Claim khac biet va cong nghe matching chua duoc kiem chung.",
            ],
            "top_risks": [],
            "missing_information": [
                "Thieu user feedback, pilot, signed partner, revenue metrics.",
                "Thieu pricing, CAC, conversion va sales strategy.",
                "Thieu founder background, hoc van va track record.",
            ],
            "overall_explanation": "Pitch deck trinh bay bai toan xa hoi ro nhung cac claim chinh van thieu bang chung va validation thuc te.",
            "recommendations": [],
            "key_questions": [],
            "operational_notes": [],
        },
        "processing_warnings": [],
    }

    result = sanitize_canonical_report(canonical)

    risk_types = [risk["risk_type"] for risk in result["narrative"]["top_risks"]]
    assert risk_types[:6] == [
        "Evidence quality risk",
        "Market adoption risk",
        "Execution risk",
        "GTM monetization risk",
        "Competitive differentiation risk",
        "Technology feasibility risk",
    ]

    severities = {risk["risk_type"]: risk["severity"] for risk in result["narrative"]["top_risks"]}
    assert severities["Evidence quality risk"] == "High"
    assert severities["Market adoption risk"] == "High"
    assert severities["Execution risk"] == "Medium"
