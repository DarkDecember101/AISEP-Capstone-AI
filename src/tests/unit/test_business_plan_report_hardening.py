from src.modules.evaluation.application.services.report_validity import sanitize_canonical_report


def _business_plan_payload() -> dict:
    return {
        "startup_id": "bp-1",
        "document_type": "business_plan",
        "status": "completed",
        "classification": {
            "stage": {"value": "PRE_SEED", "confidence": "High", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "main_industry": {"value": "SAAS_ENTERPRISE_SOFTWARE", "confidence": "High", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "subindustry": {"value": None, "confidence": "Low", "resolution_source": "inferred", "supporting_evidence_locations": []},
            "operational_notes": [],
        },
        "effective_weights": {
            "Problem_&_Customer_Pain": 16.0,
            "Market_Attractiveness_&_Timing": 14.0,
            "Solution_&_Differentiation": 18.0,
            "Business_Model_&_Go_to_Market": 18.0,
            "Team_&_Execution_Readiness": 14.0,
            "Validation_Traction_Evidence_Quality": 20.0,
        },
        "criteria_results": [
            {
                "criterion": "Problem_&_Customer_Pain",
                "status": "scored",
                "raw_score": 8.5,
                "final_score": 85.0,
                "weighted_contribution": 13.6,
                "confidence": "High",
                "cap_summary": {
                    "core_cap": 10.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 10.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "STRONG_DIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 1,
                "strengths": [],
                "concerns": [],
                "explanation": "Pain point ro rang.",
            },
            {
                "criterion": "Market_Attractiveness_&_Timing",
                "status": "scored",
                "raw_score": 7.0,
                "final_score": 70.0,
                "weighted_contribution": 9.8,
                "confidence": "Medium",
                "cap_summary": {
                    "core_cap": 10.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 10.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "STRONG_DIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 1,
                "strengths": [],
                "concerns": [],
                "explanation": "Thi truong ro nhung why now con mong.",
            },
            {
                "criterion": "Solution_&_Differentiation",
                "status": "scored",
                "raw_score": 7.5,
                "final_score": 75.0,
                "weighted_contribution": 13.5,
                "confidence": "Medium",
                "cap_summary": {
                    "core_cap": 10.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 8.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "DIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 1,
                "strengths": [
                    "Khong doi thu nao giai quyet scope creep nhu startup."
                ],
                "concerns": [],
                "explanation": "Khong doi thu nao giai quyet scope creep nhu startup.",
            },
            {
                "criterion": "Business_Model_&_Go_to_Market",
                "status": "scored",
                "raw_score": 7.5,
                "final_score": 75.0,
                "weighted_contribution": 13.5,
                "confidence": "Medium",
                "cap_summary": {
                    "core_cap": 10.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 8.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "DIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 1,
                "strengths": [],
                "concerns": [],
                "explanation": "GTM co huong di nhung chua du kiem chung.",
            },
            {
                "criterion": "Team_&_Execution_Readiness",
                "status": "scored",
                "raw_score": 9.0,
                "final_score": 90.0,
                "weighted_contribution": 12.6,
                "confidence": "High",
                "cap_summary": {
                    "core_cap": 10.0,
                    "stage_cap": 10.0,
                    "evidence_quality_cap": 10.0,
                    "contradiction_cap": 10.0,
                    "contradiction_penalty_points": 0.0,
                },
                "evidence_strength_summary": "STRONG_DIRECT",
                "evidence_locations": [],
                "supporting_pages_count": 1,
                "strengths": [],
                "concerns": [],
                "explanation": "Doi ngu rat manh theo business plan.",
            },
            {
                "criterion": "Validation_Traction_Evidence_Quality",
                "status": "insufficient_evidence",
                "raw_score": 1.0,
                "final_score": 10.0,
                "weighted_contribution": 2.0,
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
                "supporting_pages_count": 0,
                "strengths": [],
                "concerns": [],
                "explanation": "Do bang chung ABSENT, diem khong the vuot qua 4.0.",
            },
        ],
        "overall_result": {
            "overall_score": 65.0,
            "overall_confidence": "Medium",
            "evidence_coverage": "mixed",
            "interpretation_band": "promising but incomplete",
            "stage_context_note": "PRE_SEED",
        },
        "narrative": {
            "executive_summary": "Khong doi thu nao giai quyet scope creep nhu startup.",
            "top_strengths": [
                "Khong doi thu nao giai quyet scope creep nhu startup."
            ],
            "top_concerns": [],
            "top_risks": [],
            "missing_information": [],
            "overall_explanation": "Khong doi thu nao giai quyet scope creep nhu startup.",
            "recommendations": [],
            "key_questions": [],
            "operational_notes": [],
        },
        "processing_warnings": [],
    }


def test_sanitize_business_plan_report_adds_concerns_and_risks_and_softens_claims():
    result = sanitize_canonical_report(_business_plan_payload())

    assert result["narrative"]["top_concerns"]
    assert result["narrative"]["top_risks"]
    assert any(
        risk["risk_type"] == "Market adoption risk"
        for risk in result["narrative"]["top_risks"]
    )
    assert "Theo tài liệu cung cấp, startup cho rằng" in result["narrative"]["executive_summary"]
    assert "kiểm chứng" in result["criteria_results"][2]["explanation"]


def test_sanitize_business_plan_report_normalizes_team_and_validation_language():
    result = sanitize_canonical_report(_business_plan_payload())
    criteria = {c["criterion"]: c for c in result["criteria_results"]}

    assert criteria["Team_&_Execution_Readiness"]["final_score"] == 85.0
    assert criteria["Team_&_Execution_Readiness"]["confidence"] == "Medium"
    assert "4.0" not in criteria["Validation_Traction_Evidence_Quality"]["explanation"]
    assert result["overall_result"]["overall_score"] < 65.0
