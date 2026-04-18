from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

MatchBand = Literal["LOW", "MEDIUM", "HIGH", "VERY_HIGH"]
RecommendationReasonCode = Literal[
    "INDUSTRY_MATCH",
    "STAGE_MATCH",
    "GEOGRAPHY_MATCH",
    "MARKET_SCOPE_MATCH",
    "VALIDATION_MATCH",
    "SUPPORT_OVERLAP",
    "STRENGTHS_ALIGN",
    "AI_SCORE_RANGE_MATCH",
    "VALIDATION_EARLY",
    "AI_SCORE_MISSING",
    "WEAK_VERIFICATION",
    "SUPPORT_MISMATCH",
    # Fallback codes (renderer-generated, not from LLM rerank)
    "THESIS_FIT",
    "MATURITY_FIT",
    "SUPPORT_FIT",
    "AI_PREF_FIT",
    "SEMANTIC_FIT",
    "GENERAL_FIT",
    "AI_PREF_LOW",
]


class InvestorRecommendationPreferences(BaseModel):
    investor_name: str
    investor_type: str
    preferred_industries: List[str] = Field(default_factory=list)
    preferred_stages: List[str] = Field(default_factory=list)
    preferred_geographies: List[str] = Field(default_factory=list)
    preferred_market_scopes: List[str] = Field(default_factory=list)
    preferred_product_maturity: List[str] = Field(default_factory=list)
    preferred_validation_level: List[str] = Field(default_factory=list)
    preferred_ai_score_range: Optional[Dict[str, float]] = None
    ai_score_importance: str = "medium"
    preferred_strengths: List[str] = Field(default_factory=list)
    support_offered: List[str] = Field(default_factory=list)
    require_verified_startups: bool = True
    require_visible_profiles: bool = True


class InvestorRecommendationDocument(BaseModel):
    investor_id: str
    profile_version: str
    source_updated_at: datetime
    structured_preferences: InvestorRecommendationPreferences
    investment_thesis_text: str
    avoid_text: str = ""
    support_offered_text: str = ""
    investor_semantic_text: str
    investor_semantic_embedding: Optional[List[float]] = None
    weights: Dict[str, float] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    source_payload: Dict[str, Any] = Field(default_factory=dict)


class StartupStructuredProfile(BaseModel):
    startup_name: str
    tagline: str = ""
    stage: str = ""
    primary_industry: str = ""
    location: str = ""
    market_scope: str = ""
    product_status: str = ""
    current_needs: List[str] = Field(default_factory=list)
    founder_names: List[str] = Field(default_factory=list)
    founder_roles: List[str] = Field(default_factory=list)
    team_size: Optional[str] = None
    validation_status: str = ""
    optional_short_metric_summary: str = ""
    is_profile_visible_to_investors: bool = False
    verification_label: str = ""
    account_active: bool = True


class StartupAIProfile(BaseModel):
    ai_evaluation_status: str = "missing"
    ai_overall_score: Optional[float] = None
    ai_summary: str = ""
    ai_strength_tags: List[str] = Field(default_factory=list)
    ai_weakness_tags: List[str] = Field(default_factory=list)
    ai_dimension_scores: Dict[str, float] = Field(default_factory=dict)


class StartupRecommendationDocument(BaseModel):
    startup_id: str
    profile_version: str
    source_updated_at: datetime
    structured_profile: StartupStructuredProfile
    ai_profile: StartupAIProfile
    startup_profile_semantic_text: str
    startup_profile_embedding: Optional[List[float]] = None
    startup_ai_semantic_text: str = ""
    startup_ai_embedding: Optional[List[float]] = None
    tags: List[str] = Field(default_factory=list)
    source_payload: Dict[str, Any] = Field(default_factory=dict)


class RecommendationBreakdown(BaseModel):
    thesis_fit_score: float
    maturity_fit_score: float
    support_fit_score: float
    ai_preference_fit_score: float
    semantic_profile_score: float
    semantic_ai_score: Optional[float] = None
    combined_pre_llm_score: float
    rerank_adjustment: float
    final_match_score: float
    breakdown_has_missing_ai: bool = False
    candidate_count: int = 1
    rerank_policy_applied: str = "default_cap_10"
    rerank_capped: bool = False


class RecommendationReasonItem(BaseModel):
    type: Literal["positive", "caution"]
    code: str
    text: str


class LLMRerankItem(BaseModel):
    startup_id: str
    rerank_adjustment: int = Field(ge=-10, le=10)
    positive_reason_codes: List[RecommendationReasonCode] = Field(
        default_factory=list)
    caution_reason_codes: List[RecommendationReasonCode] = Field(
        default_factory=list)


class LLMRerankResult(BaseModel):
    items: List[LLMRerankItem] = Field(default_factory=list)


class RecommendationMatchResult(BaseModel):
    investor_id: str
    startup_id: str
    startup_name: str
    final_match_score: float
    match_band: MatchBand
    fit_summary_label: str = ""
    structured_score: float
    semantic_score: float
    combined_pre_llm_score: float
    rerank_adjustment: float
    breakdown: RecommendationBreakdown
    match_reasons: List[str] = Field(default_factory=list)
    positive_reasons: List[RecommendationReasonItem] = Field(
        default_factory=list)
    caution_reasons: List[RecommendationReasonItem] = Field(
        default_factory=list)
    warning_flags: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class ReindexInvestorRequest(BaseModel):
    profile_version: str = "1.0"
    source_updated_at: datetime = Field(default_factory=datetime.utcnow)
    investor_name: str
    investor_type: str
    organization: str = ""
    role_title: str = ""
    location: str = ""
    website: str = ""
    verification_label: str = "basic_verified"
    logo_url: str = ""
    short_thesis_summary: str = ""
    preferred_industries: List[str] = Field(default_factory=list)
    preferred_stages: List[str] = Field(default_factory=list)
    preferred_geographies: List[str] = Field(default_factory=list)
    preferred_market_scopes: List[str] = Field(default_factory=list)
    preferred_product_maturity: List[str] = Field(default_factory=list)
    preferred_validation_level: List[str] = Field(default_factory=list)
    preferred_ai_score_range: Optional[Dict[str, float]] = None
    ai_score_importance: str = "medium"
    preferred_strengths: List[str] = Field(default_factory=list)
    support_offered: List[str] = Field(default_factory=list)
    accepting_connections_status: str = "active"
    recently_active_badge: bool = False
    require_verified_startups: bool = True
    require_visible_profiles: bool = True
    avoid_text: str = ""
    tags: List[str] = Field(default_factory=list)


class ReindexStartupRequest(BaseModel):
    profile_version: str = "1.0"
    source_updated_at: datetime = Field(default_factory=datetime.utcnow)
    startup_name: str
    tagline: str = ""
    stage: str = ""
    primary_industry: str = ""
    location: str = ""
    website: str = ""
    product_link: str = ""
    demo_link: str = ""
    logo_url: str = ""
    problem_statement: str = ""
    solution_summary: str = ""
    market_scope: str = ""
    product_status: str = ""
    current_needs: List[str] = Field(default_factory=list)
    founder_names: List[str] = Field(default_factory=list)
    founder_roles: List[str] = Field(default_factory=list)
    team_size: Optional[str] = None
    validation_status: str = ""
    optional_short_metric_summary: str = ""
    is_profile_visible_to_investors: bool = True
    verification_label: str = "basic_verified"
    account_active: bool = True
    ai_evaluation_status: str = "missing"
    ai_overall_score: Optional[float] = None
    ai_summary: str = ""
    ai_strength_tags: List[str] = Field(default_factory=list)
    ai_weakness_tags: List[str] = Field(default_factory=list)
    ai_dimension_scores: Dict[str, float] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)


class RecommendationRunRecord(BaseModel):
    run_id: str
    investor_id: str
    investor_profile_version: str
    candidate_count: int
    candidate_set_size: int
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    candidate_startup_ids: List[str] = Field(default_factory=list)
    results: List[RecommendationMatchResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class RecommendationListResponse(BaseModel):
    investor_id: str
    matches: List[RecommendationMatchResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    internal_warnings: List[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class RecommendationExplanationResponse(BaseModel):
    investor_id: str
    startup_id: str
    explanation: RecommendationMatchResult
    generated_at: datetime = Field(default_factory=datetime.utcnow)
