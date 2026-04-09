import pytest
from src.modules.evaluation.domain.scoring_policy import (
    calculate_overall_score,
    normalize_to_canonical_criterion_name,
)


def test_calculate_overall_score():
    # Giả lập kết quả các tiêu chí trả về từ LLM
    criterion_scores = {
        "problem_clarity": 80.0,
        "market_opportunity": 70.0,

        "solution_strength": 90.0,

        "business_model": 85.0,
        "traction_evidence": 50.0,

        "team_quality": 95.0,
        "financial_feasibility": 70.0,
        "execution_readiness": 75.0,
        "risk_awareness": 80.0
    }

    result = calculate_overall_score(criterion_scores)

    # Assert
    assert "overall_score" in result
    assert "dimension_scores" in result

    # Kiểm tra dimension market
    # dim "market" weight 0.20
    # problem_clarity weight 0.4 * 80.0 = 32
    # market weight 0.6 * 70.0 = 42
    # -> market_dim_score = (32 + 42) / (0.4 + 0.6) = 74.0

    assert abs(result["dimension_scores"]["market"] - 74.0) < 0.01

    # Kiểm tra partial dimension (nếu format thiếu dữ liệu)
    partial_scores = {
        "problem_clarity": 80.0
        # Thiếu market_opportunity
    }

    partial_result = calculate_overall_score(partial_scores)
    # score của market dimension = 80.0 do tự normalize lên theo weight có sẵn
    assert abs(partial_result["dimension_scores"]["market"] - 80.0) < 0.01


def test_calculate_empty_score():
    result = calculate_overall_score({})
    assert result["overall_score"] == 0.0


def test_alias_code_normalization():
    criterion_scores = {
        "MARKET_OPPORTUNITY": 76.0,
        "TEAM": 88.0,
        "PRODUCT_MARKET_FIT": 74.0,
        "FINANCIALS": 68.0,
        "TRACTION": 72.0,
    }
    result = calculate_overall_score(criterion_scores)
    assert result["overall_score"] > 0.0
    assert "market" in result["dimension_scores"]


def test_normalize_to_canonical_criterion_name_internal_codes():
    assert normalize_to_canonical_criterion_name(
        "market_opportunity") == "Market_Attractiveness_&_Timing"
    assert normalize_to_canonical_criterion_name(
        "financial_feasibility") == "Business_Model_&_Go_to_Market"
    assert normalize_to_canonical_criterion_name(
        "execution_readiness") == "Team_&_Execution_Readiness"


def test_normalize_to_canonical_criterion_name_aliases_and_passthrough():
    assert normalize_to_canonical_criterion_name(
        "TEAM") == "Team_&_Execution_Readiness"
    assert normalize_to_canonical_criterion_name(
        "FINANCIALS") == "Business_Model_&_Go_to_Market"
    assert normalize_to_canonical_criterion_name(
        "Market_Attractiveness_&_Timing") == "Market_Attractiveness_&_Timing"
