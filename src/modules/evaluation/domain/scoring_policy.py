from typing import List, Dict, Optional
import re
from pydantic import BaseModel, Field


class CriterionDef(BaseModel):
    code: str
    name: str
    weight: float
    description: str


class DimensionDef(BaseModel):
    code: str
    name: str
    weight: float
    criteria: List[CriterionDef]


class ScoringPolicy(BaseModel):
    version: str = "1.0"
    dimensions: List[DimensionDef]

    def get_dimension_by_code(self, code: str) -> Optional[DimensionDef]:
        for dim in self.dimensions:
            if dim.code == code:
                return dim
        return None

    def get_criterion_by_code(self, code: str) -> Optional[CriterionDef]:
        for dim in self.dimensions:
            for crit in dim.criteria:
                if crit.code == code:
                    return crit
        return None


# The default phase 1 policy:
DEFAULT_POLICY = ScoringPolicy(
    dimensions=[
        DimensionDef(
            code="market",
            name="Market",
            weight=0.20,
            criteria=[
                CriterionDef(code="problem_clarity", name="Problem Clarity",
                             weight=0.40, description="Clear and painful problem"),
                CriterionDef(code="market_opportunity", name="Market Opportunity",
                             weight=0.60, description="Market size and attractiveness")
            ]
        ),
        DimensionDef(
            code="product",
            name="Product",
            weight=0.18,
            criteria=[
                CriterionDef(code="solution_strength", name="Solution Strength",
                             weight=1.00, description="Product viability and uniqueness")
            ]
        ),
        DimensionDef(
            code="team",
            name="Team",
            weight=0.15,
            criteria=[
                CriterionDef(code="team_quality", name="Team Quality",
                             weight=1.00, description="Founder-market fit and capability")
            ]
        ),
        DimensionDef(
            code="traction",
            name="Traction",
            weight=0.15,
            criteria=[
                CriterionDef(code="traction_evidence", name="Traction Evidence",
                             weight=1.00, description="Proof of adoption/revenue")
            ]
        ),
        DimensionDef(
            code="financial",
            name="Financial",
            weight=0.16,
            criteria=[
                CriterionDef(code="business_model", name="Business Model",
                             weight=0.50, description="Revenue and economics"),
                CriterionDef(code="financial_feasibility", name="Financial Feasibility",
                             weight=0.50, description="Projection realism")
            ]
        ),
        DimensionDef(
            code="execution",
            name="Execution",
            weight=0.16,
            criteria=[
                CriterionDef(code="execution_readiness", name="Execution Readiness",
                             weight=0.55, description="Plan quality and readiness"),
                CriterionDef(code="risk_awareness", name="Risk Awareness",
                             weight=0.45, description="Awareness of risks and mitigation")
            ]
        )
    ]
)

CANONICAL_CRITERIA = {
    "Problem_&_Customer_Pain",
    "Market_Attractiveness_&_Timing",
    "Solution_&_Differentiation",
    "Business_Model_&_Go_to_Market",
    "Team_&_Execution_Readiness",
    "Validation_Traction_Evidence_Quality"
}

_CANONICAL_BY_INTERNAL_CODE = {
    "problem_clarity": "Problem_&_Customer_Pain",
    "market_opportunity": "Market_Attractiveness_&_Timing",
    "solution_strength": "Solution_&_Differentiation",
    "business_model": "Business_Model_&_Go_to_Market",
    "financial_feasibility": "Business_Model_&_Go_to_Market",
    "team_quality": "Team_&_Execution_Readiness",
    "execution_readiness": "Team_&_Execution_Readiness",
    "risk_awareness": "Team_&_Execution_Readiness",
    "traction_evidence": "Validation_Traction_Evidence_Quality",
}


def normalize_criterion_code(code: str) -> Optional[str]:
    if not code:
        return None
    code_str = code.strip()
    code_lower = code_str.lower()

    # If the code already matches an internal criterion code, keep it.
    # This allows callers to pass the exact internal codes (e.g. 'problem_clarity').
    if DEFAULT_POLICY.get_criterion_by_code(code_str) is not None:
        return code_str

    # Normalize common alias forms or human-readable labels into internal codes
    # Market / problem
    if "problem" in code_lower or "customer" in code_lower:
        return "problem_clarity"
    if "market" in code_lower or "timing" in code_lower or "opportunity" in code_lower:
        return "market_opportunity"

    # Product / solution
    if "solution" in code_lower or "differentiation" in code_lower or "product" in code_lower or "pmf" in code_lower:
        return "solution_strength"

    # Business / financial
    if "business" in code_lower or "monetization" in code_lower or ("model" in code_lower and "business" in code_lower):
        return "business_model"
    if "financial" in code_lower or "feasibility" in code_lower or "financials" in code_lower:
        return "financial_feasibility"

    # Team / execution
    if "team" in code_lower:
        return "team_quality"
    if "execution" in code_lower or "readiness" in code_lower:
        return "execution_readiness"

    # Traction / validation
    if "traction" in code_lower or "validation" in code_lower or "adoption" in code_lower or "evidence" in code_lower:
        return "traction_evidence"

    # Legacy or noisy uppercase codes mapping
    upper = code_str.upper()
    alias_map = {
        "MARKET_OPPORTUNITY": "market_opportunity",
        "PROBLEM_CLARITY": "problem_clarity",
        "SOLUTION_STRENGTH": "solution_strength",
        "BUSINESS_MODEL": "business_model",
        "FINANCIAL_FEASIBILITY": "financial_feasibility",
        "TEAM_QUALITY": "team_quality",
        "TRACTION_EVIDENCE": "traction_evidence",
        "PRODUCT_MARKET_FIT": "solution_strength",
        "TEAM": "team_quality",
        "FINANCIALS": "financial_feasibility",
        "TRACTION": "traction_evidence",
    }
    if upper in alias_map:
        return alias_map[upper]

    return None


def normalize_to_canonical_criterion_name(code: str) -> Optional[str]:
    """
    Normalize free-form / legacy / internal criterion strings into
    CanonicalNarrative CriterionName literals.

    Examples:
    - market_opportunity -> Market_Attractiveness_&_Timing
    - financial_feasibility -> Business_Model_&_Go_to_Market
    - TEAM -> Team_&_Execution_Readiness
    """
    if not code:
        return None

    code_str = code.strip()
    if code_str in CANONICAL_CRITERIA:
        return code_str

    internal_code = normalize_criterion_code(code_str)
    if internal_code is None:
        return None

    return _CANONICAL_BY_INTERNAL_CODE.get(internal_code)


def calculate_overall_score(criterion_scores: Dict[str, float]) -> Dict:
    """
    Calculate the overall score based on the policy and criterion scores (0-100).
    It also returns dimension-level scores.
    """
    normalized_scores: Dict[str, float] = {}
    for key, value in criterion_scores.items():
        canonical = normalize_criterion_code(key)
        if canonical is None:
            continue
        normalized_scores.setdefault(canonical, []).append(value)

    collapsed_scores = {
        key: sum(values) / len(values)
        for key, values in normalized_scores.items()
    }

    dimension_scores = {}
    total_score = 0.0
    valid_dimensions_weight = 0.0

    for dim in DEFAULT_POLICY.dimensions:
        dim_score = 0.0
        valid_criteria_weight = 0.0
        for crit in dim.criteria:
            if crit.code in collapsed_scores:
                dim_score += collapsed_scores[crit.code] * crit.weight
                valid_criteria_weight += crit.weight

        # Normalize dimension score if some criteria are missing
        if valid_criteria_weight > 0:
            final_dim_score = dim_score / valid_criteria_weight
            dimension_scores[dim.code] = final_dim_score
            total_score += final_dim_score * dim.weight
            valid_dimensions_weight += dim.weight

    overall = (total_score /
               valid_dimensions_weight) if valid_dimensions_weight > 0 else 0.0

    return {
        "overall_score": overall,
        "dimension_scores": dimension_scores
    }
