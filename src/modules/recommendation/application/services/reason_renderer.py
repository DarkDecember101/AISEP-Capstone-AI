from __future__ import annotations

from typing import Dict, List

from src.modules.recommendation.application.dto.recommendation_schema import (
    LLMRerankItem,
    RecommendationBreakdown,
    RecommendationReasonCode,
)
from src.modules.recommendation.application.services.scoring import RecommendationScoringService

REASON_CODE_TEMPLATES: Dict[str, str] = {
    "INDUSTRY_MATCH": "Matches your industry focus",
    "STAGE_MATCH": "Fits your preferred stage",
    "GEOGRAPHY_MATCH": "Aligned with your geography preference",
    "MARKET_SCOPE_MATCH": "Matches your preferred market scope",
    "VALIDATION_MATCH": "Meets your preferred validation level",
    "SUPPORT_OVERLAP": "Matches the type of support you typically offer",
    "STRENGTHS_ALIGN": "Strong in the startup strengths you prioritize",
    "AI_SCORE_RANGE_MATCH": "Falls within your preferred AI score range",
    "VALIDATION_EARLY": "Validation is earlier than your usual preference",
    "AI_SCORE_MISSING": "AI evaluation is not available yet",
    "WEAK_VERIFICATION": "Verification strength is lower than preferred",
    "SUPPORT_MISMATCH": "Support offered does not match startup's current needs",
}


class RecommendationReasonRenderer:
    @staticmethod
    def render(
        breakdown: RecommendationBreakdown,
        rerank_item: LLMRerankItem | None,
        startup_warnings: List[str] | None = None,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Return (positive_list, caution_list) where each item is (code, text)."""
        startup_warnings = list(startup_warnings or [])
        rerank_item = rerank_item or LLMRerankItem(
            startup_id="",
            rerank_adjustment=0,
            positive_reason_codes=[],
            caution_reason_codes=[],
        )

        positive_codes = RecommendationReasonRenderer._ordered_reason_codes(
            breakdown,
            rerank_item.positive_reason_codes,
        )
        caution_codes: List[str] = []
        if breakdown.breakdown_has_missing_ai:
            caution_codes.append("AI_SCORE_MISSING")
        if breakdown.maturity_fit_score <= 0.1:
            caution_codes.append("VALIDATION_EARLY")
        if breakdown.support_fit_score <= 0.1:
            caution_codes.append("SUPPORT_MISMATCH")

        for code in rerank_item.caution_reason_codes:
            if code not in caution_codes:
                caution_codes.append(code)

        reasons: list[tuple[str, str]] = []
        seen_texts: set[str] = set()
        for code in positive_codes[:3]:
            template = REASON_CODE_TEMPLATES.get(code)
            if template and template not in seen_texts:
                reasons.append((code, template))
                seen_texts.add(template)

        cautions: list[tuple[str, str]] = []
        for code in caution_codes[:1]:
            template = REASON_CODE_TEMPLATES.get(code)
            if template:
                cautions.append((code, template))

        if not reasons:
            reasons = RecommendationReasonRenderer._fallback_positive_reasons(
                breakdown)

        if not cautions:
            cautions = RecommendationReasonRenderer._fallback_cautions(
                breakdown, startup_warnings)

        return reasons[:3], cautions[:1]

    @staticmethod
    def _ordered_reason_codes(
        breakdown: RecommendationBreakdown,
        codes: List[RecommendationReasonCode],
    ) -> List[str]:
        order: List[str] = []
        if breakdown.thesis_fit_score >= 18:
            order.append("INDUSTRY_MATCH")
        if breakdown.thesis_fit_score >= 28:
            order.append("STAGE_MATCH")
        if breakdown.thesis_fit_score >= 35:
            order.append("GEOGRAPHY_MATCH")
        if breakdown.thesis_fit_score >= 40:
            order.append("MARKET_SCOPE_MATCH")
        if breakdown.maturity_fit_score >= 18:
            order.append("VALIDATION_MATCH")
        if breakdown.support_fit_score >= 6:
            order.append("SUPPORT_OVERLAP")
        if breakdown.ai_preference_fit_score >= 10:
            order.append("STRENGTHS_ALIGN")
        if breakdown.breakdown_has_missing_ai:
            order.append("AI_SCORE_MISSING")

        for code in codes:
            if code not in order:
                order.append(code)
        return order

    @staticmethod
    def _fallback_positive_reasons(breakdown: RecommendationBreakdown) -> list[tuple[str, str]]:
        factors = RecommendationScoringService.top_structured_factors(
            breakdown)
        results: list[tuple[str, str]] = []
        mapping = {
            "Thesis fit": ("THESIS_FIT", "Strong thesis fit"),
            "Maturity fit": ("MATURITY_FIT", "Good product and validation alignment"),
            "Support fit": ("SUPPORT_FIT", "Support needs align with your offer"),
            "AI preference fit": ("AI_PREF_FIT", "Fits your AI preference profile"),
            "Semantic profile fit": ("SEMANTIC_FIT", "Semantically close to your investment thesis"),
        }
        for factor in factors:
            if factor in mapping:
                results.append(mapping[factor])
        return results[:3] or [("GENERAL_FIT", "Reasonable fit based on your stated preferences")]

    @staticmethod
    def _fallback_cautions(breakdown: RecommendationBreakdown, warnings: List[str]) -> list[tuple[str, str]]:
        if "ai_evaluation_missing" in warnings or "AI evaluation is not available yet." in warnings:
            return [("AI_SCORE_MISSING", REASON_CODE_TEMPLATES["AI_SCORE_MISSING"])]
        if breakdown.ai_preference_fit_score < 4:
            return [("AI_PREF_LOW", "AI preference score is low or missing data")]
        return []
