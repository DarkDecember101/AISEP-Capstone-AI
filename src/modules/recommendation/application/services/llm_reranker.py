from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from src.shared.config.settings import settings
from src.shared.providers.llm.gemini_client import GeminiClient
from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    LLMRerankItem,
    LLMRerankResult,
    StartupRecommendationDocument,
)


class LLMRerankResponse(BaseModel):
    items: List[LLMRerankItem] = Field(default_factory=list)


class RecommendationLLMReranker:
    def __init__(self) -> None:
        self._client = GeminiClient() if settings.GOOGLE_CLOUD_PROJECT else None

    def rerank(
        self,
        investor: InvestorRecommendationDocument,
        candidates: List[dict],
    ) -> tuple[List[LLMRerankItem], List[str]]:
        if self._client is None:
            return [], ["LLM rerank skipped because GOOGLE_CLOUD_PROJECT is not configured"]

        investor_context = {
            "investor_name": investor.structured_preferences.investor_name,
            "investor_type": investor.structured_preferences.investor_type,
            "investment_thesis": investor.investment_thesis_text,
            "preferred_industries": investor.structured_preferences.preferred_industries,
            "preferred_stages": investor.structured_preferences.preferred_stages,
            "preferred_geographies": investor.structured_preferences.preferred_geographies,
            "preferred_market_scopes": investor.structured_preferences.preferred_market_scopes,
            "preferred_product_maturity": investor.structured_preferences.preferred_product_maturity,
            "preferred_validation_level": investor.structured_preferences.preferred_validation_level,
            "support_offered": investor.structured_preferences.support_offered,
            "preferred_strengths": investor.structured_preferences.preferred_strengths,
            "ai_score_importance": investor.structured_preferences.ai_score_importance,
        }

        prompt = f"""
You are ranking startup candidates for an investor.
Use only the provided facts.
Do not invent facts.
Do not search the web.
Only adjust ranking lightly.
Return JSON only.

Investor context:
{investor_context}

Candidate startup cards:
{candidates}

Rules:
- compare candidates relative to each other
- adjustment range must stay within -10..+10
- positive_reason_codes and caution_reason_codes must only use the allowed code list
- if AI evaluation is missing, you may use AI_SCORE_MISSING

Allowed reason codes:
INDUSTRY_MATCH, STAGE_MATCH, GEOGRAPHY_MATCH, MARKET_SCOPE_MATCH, VALIDATION_MATCH, SUPPORT_OVERLAP, STRENGTHS_ALIGN, AI_SCORE_RANGE_MATCH, VALIDATION_EARLY, AI_SCORE_MISSING, WEAK_VERIFICATION
"""

        result = self._client.generate_structured(
            prompt=prompt,
            response_schema=LLMRerankResponse,
            model_name="gemini-2.5-flash",
        )
        return result.items, []
