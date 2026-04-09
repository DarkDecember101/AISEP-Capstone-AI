from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    RecommendationBreakdown,
    StartupRecommendationDocument,
)
from src.modules.recommendation.application.services.embedding import EmbeddingService

INDUSTRY_DOMAIN_MAP: Dict[str, str] = {
    "fintech": "finance",
    "financial services": "finance",
    "finance": "finance",
    "banking": "finance",
    "payments": "finance",
    "paytech": "finance",
    "healthtech": "healthcare",
    "medical": "healthcare",
    "medtech": "healthcare",
    "biotech": "healthcare",
    "edtech": "education",
    "education": "education",
    "saas": "technology",
    "software": "technology",
    "ai": "technology",
    "artificial intelligence": "technology",
    "commerce": "commerce",
    "ecommerce": "commerce",
    "marketplace": "commerce",
    "retail": "commerce",
    "climate": "climate",
    "energy": "climate",
    "agritech": "agriculture",
    "mobility": "mobility",
}

REGION_COUNTRY_MAP: Dict[str, List[str]] = {
    "south east asia": ["vietnam", "singapore", "thailand", "malaysia", "indonesia", "philippines", "brunei", "cambodia", "laos", "myanmar", "timor-leste"],
    "sea": ["vietnam", "singapore", "thailand", "malaysia", "indonesia", "philippines", "brunei", "cambodia", "laos", "myanmar", "timor-leste"],
    "apac": ["vietnam", "singapore", "thailand", "malaysia", "indonesia", "philippines", "china", "japan", "korea", "australia", "new zealand", "india"],
    "emea": ["united kingdom", "germany", "france", "spain", "italy", "netherlands", "uae", "saudi arabia", "south africa"],
    "latam": ["brazil", "mexico", "argentina", "colombia", "chile", "peru"],
    "global": [],
}

VALIDATION_LEVEL_ORDER = {
    "idea": 1,
    "prototype": 2,
    "mvp": 3,
    "early_validation": 4,
    "traction": 5,
    "revenue": 6,
    "scaling": 7,
}

PRODUCT_MATURITY_ORDER = {
    "idea": 1,
    "concept": 1,
    "prototype": 2,
    "mvp": 3,
    "beta": 4,
    "launched": 5,
    "growth": 6,
    "scaled": 7,
}

AI_SCORE_IMPORTANCE_WEIGHT = {
    "low": 0.85,
    "medium": 1.0,
    "high": 1.15,
}


@dataclass
class StructuredScoreResult:
    thesis_fit_score: float
    maturity_fit_score: float
    support_fit_score: float
    ai_preference_fit_score: float
    structured_score: float
    warnings: List[str]


@dataclass
class SemanticScoreResult:
    semantic_profile_score: float
    semantic_ai_score: float | None
    semantic_score: float
    warnings: List[str]


class RecommendationScoringService:
    @staticmethod
    def _normalize_items(items: Sequence[str] | None) -> List[str]:
        normalized: List[str] = []
        for item in items or []:
            if item is None:
                continue
            text = str(item).strip().lower()
            if text:
                normalized.append(text)
        return normalized

    @staticmethod
    def _split_text_values(raw_value: str | Sequence[str] | None) -> List[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, (list, tuple, set)):
            return RecommendationScoringService._normalize_items([str(item) for item in raw_value])
        separators = [",", ";", "|", "\n"]
        tokens = [str(raw_value)]
        for separator in separators:
            next_tokens: List[str] = []
            for token in tokens:
                next_tokens.extend(token.split(separator))
            tokens = next_tokens
        return RecommendationScoringService._normalize_items(tokens)

    @staticmethod
    def _domain_for_industry(industry: str) -> str:
        industry_normalized = industry.strip().lower()
        for key, value in INDUSTRY_DOMAIN_MAP.items():
            if key in industry_normalized:
                return value
        return industry_normalized or "unknown"

    @staticmethod
    def _resolve_geography_token(value: str) -> str:
        value_normalized = value.strip().lower()
        if not value_normalized:
            return ""
        # Direct region match
        for region, countries in REGION_COUNTRY_MAP.items():
            if value_normalized == region:
                return region
            if value_normalized in countries:
                return value_normalized
        # Startup locations often come as "City, Country" (e.g. "Bangkok, Thailand")
        # Try each comma-separated part so "Bangkok, Thailand" resolves to "thailand"
        parts = [p.strip().lower() for p in value_normalized.split(",")]
        if len(parts) > 1:
            for part in parts:
                for region, countries in REGION_COUNTRY_MAP.items():
                    if part == region:
                        return region
                    if part in countries:
                        return part
            # Return the last part (usually the country) as best guess
            return parts[-1]
        return value_normalized

    @staticmethod
    def passes_hard_filter(
        investor: InvestorRecommendationDocument,
        startup: StartupRecommendationDocument,
    ) -> Tuple[bool, List[str]]:
        warnings: List[str] = []
        prefs = investor.structured_preferences
        profile = startup.structured_profile

        if not prefs.require_verified_startups and not prefs.require_visible_profiles:
            warnings.append(
                "Investor has relaxed visibility and verification requirements.")

        if prefs.require_visible_profiles and not profile.is_profile_visible_to_investors:
            return False, ["STARTUP_NOT_VISIBLE"]

        if prefs.require_verified_startups and profile.verification_label.strip().lower() in {"failed", "verification_failed", "rejected"}:
            return False, ["STARTUP_VERIFICATION_FAILED"]

        if not profile.account_active:
            return False, ["STARTUP_INACTIVE"]

        investor_state = investor.source_payload.get(
            "accepting_connections_status", "active")
        if str(investor_state).strip().lower() not in {"active", "accepting", "open"}:
            return False, ["INVESTOR_NOT_OPEN_FOR_RECOMMENDATION"]

        if not investor.source_payload.get("account_active", True):
            return False, ["INVESTOR_INACTIVE"]

        if not prefs.preferred_stages:
            return False, ["STAGE_PREFERENCE_EMPTY"]

        startup_stage = profile.stage.strip().lower()
        stage_options = {item.strip().lower()
                         for item in prefs.preferred_stages}
        if startup_stage not in stage_options:
            return False, ["STAGE_MISMATCH"]

        if prefs.preferred_industries:
            startup_industry = profile.primary_industry.strip().lower()
            startup_domain = RecommendationScoringService._domain_for_industry(
                startup_industry)
            industry_pass = False
            for preferred in prefs.preferred_industries:
                preferred_norm = preferred.strip().lower()
                preferred_domain = RecommendationScoringService._domain_for_industry(
                    preferred_norm)
                if preferred_norm == startup_industry:
                    industry_pass = True
                    break
                if preferred_domain == startup_domain and preferred_domain != "unknown":
                    industry_pass = True
                    break
            if not industry_pass:
                return False, ["INDUSTRY_MISMATCH"]

        if prefs.preferred_geographies:
            startup_location = RecommendationScoringService._resolve_geography_token(
                profile.location)
            geography_pass = False
            for preferred in prefs.preferred_geographies:
                preferred_norm = RecommendationScoringService._resolve_geography_token(
                    preferred)
                if preferred_norm == "global" or startup_location == preferred_norm:
                    geography_pass = True
                    break
                if preferred_norm in REGION_COUNTRY_MAP and startup_location in REGION_COUNTRY_MAP.get(preferred_norm, []):
                    geography_pass = True
                    break
                for region, countries in REGION_COUNTRY_MAP.items():
                    if preferred_norm in countries and startup_location in countries:
                        geography_pass = True
                        break
                if geography_pass:
                    break
            if not geography_pass:
                return False, ["GEOGRAPHY_MISMATCH"]

        if prefs.preferred_market_scopes:
            market_scope = profile.market_scope.strip().lower()
            preferred_scopes = {item.strip().lower()
                                for item in prefs.preferred_market_scopes}
            if "no_strong_preference" not in preferred_scopes and market_scope not in preferred_scopes:
                return False, ["MARKET_SCOPE_MISMATCH"]

        return True, warnings

    @staticmethod
    def score_structured(
        investor: InvestorRecommendationDocument,
        startup: StartupRecommendationDocument,
    ) -> StructuredScoreResult:
        prefs = investor.structured_preferences
        profile = startup.structured_profile
        ai_profile = startup.ai_profile

        warnings: List[str] = []

        thesis_industry = RecommendationScoringService._industry_thesis_score(
            prefs.preferred_industries, profile.primary_industry)
        thesis_stage = RecommendationScoringService._exact_match_score(
            prefs.preferred_stages, profile.stage, 12.0)
        thesis_geo = RecommendationScoringService._geography_match_score(
            prefs.preferred_geographies, profile.location)
        thesis_market = RecommendationScoringService._market_scope_score(
            prefs.preferred_market_scopes, profile.market_scope)
        thesis_fit_score = thesis_industry + thesis_stage + thesis_geo + thesis_market

        maturity_fit_score = RecommendationScoringService._product_maturity_score(
            prefs.preferred_product_maturity, profile.product_status)
        maturity_fit_score += RecommendationScoringService._validation_level_score(
            prefs.preferred_validation_level, profile.validation_status)
        maturity_fit_score = min(25.0, maturity_fit_score)

        support_fit_score = RecommendationScoringService._support_fit_score(
            prefs.support_offered, profile.current_needs)

        ai_preference_fit_score = RecommendationScoringService._ai_preference_score(
            prefs, ai_profile)

        structured_score = min(100.0, thesis_fit_score + maturity_fit_score +
                               support_fit_score + ai_preference_fit_score)
        if not ai_profile.ai_summary.strip() and ai_profile.ai_evaluation_status == "missing":
            warnings.append("AI evaluation is not available yet.")

        return StructuredScoreResult(
            thesis_fit_score=round(thesis_fit_score, 2),
            maturity_fit_score=round(maturity_fit_score, 2),
            support_fit_score=round(support_fit_score, 2),
            ai_preference_fit_score=round(ai_preference_fit_score, 2),
            structured_score=round(structured_score, 2),
            warnings=warnings,
        )

    @staticmethod
    def score_semantic(
        investor: InvestorRecommendationDocument,
        startup: StartupRecommendationDocument,
    ) -> SemanticScoreResult:
        profile_similarity = EmbeddingService.cosine_similarity(
            investor.investor_semantic_embedding,
            startup.startup_profile_embedding,
        )
        semantic_profile_score = EmbeddingService.normalize_similarity(
            profile_similarity)

        if startup.startup_ai_embedding is not None:
            ai_similarity = EmbeddingService.cosine_similarity(
                investor.investor_semantic_embedding,
                startup.startup_ai_embedding,
            )
            semantic_ai_score = EmbeddingService.normalize_similarity(
                ai_similarity)
            combined = 0.6 * semantic_profile_score + 0.4 * semantic_ai_score
        else:
            semantic_ai_score = None
            combined = semantic_profile_score

        return SemanticScoreResult(
            semantic_profile_score=round(semantic_profile_score, 2),
            semantic_ai_score=round(
                semantic_ai_score, 2) if semantic_ai_score is not None else None,
            semantic_score=round(combined, 2),
            warnings=[] if semantic_ai_score is not None else [
                "startup_ai_embedding_missing"],
        )

    @staticmethod
    def compute_final_score(structured_score: float, semantic_score: float, rerank_adjustment: float) -> float:
        combined_pre_llm_score = 0.7 * structured_score + 0.3 * semantic_score
        final_score = combined_pre_llm_score + rerank_adjustment
        return round(max(0.0, min(100.0, final_score)), 1)

    @staticmethod
    def compute_combined_pre_llm_score(structured_score: float, semantic_score: float) -> float:
        return round(0.7 * structured_score + 0.3 * semantic_score, 2)

    @staticmethod
    def band_for_score(score: float, breakdown: RecommendationBreakdown | None = None) -> Tuple[str, str]:
        zero_count = 0
        if breakdown:
            if breakdown.maturity_fit_score <= 0.1:
                zero_count += 1
            if breakdown.support_fit_score <= 0.1:
                zero_count += 1
            if breakdown.thesis_fit_score <= 10.0:
                zero_count += 1

        band = "LOW"
        if score >= 85:
            band = "VERY_HIGH"
        elif score >= 70:
            band = "HIGH"
        elif score >= 50:
            band = "MEDIUM"

        if zero_count >= 2 and band in ["HIGH", "VERY_HIGH"]:
            band = "MEDIUM"

        label = ""
        if zero_count == 0 and band in ["HIGH", "VERY_HIGH"]:
            label = "Excellent overall match"
        elif zero_count == 1:
            if breakdown and breakdown.maturity_fit_score <= 0.1:
                label = "Strong strategic fit, weak maturity alignment"
            elif breakdown and breakdown.support_fit_score <= 0.1:
                label = "Good thesis fit, support mismatch"
            else:
                label = "Qualified but incomplete fit"
        elif zero_count >= 2:
            label = "Narrow fit with multiple execution caveats"
        else:
            label = "Reasonable strategic and execution fit"

        return band, label

    @staticmethod
    def top_structured_factors(breakdown: RecommendationBreakdown) -> List[str]:
        factors = [
            ("Thesis fit", breakdown.thesis_fit_score),
            ("Maturity fit", breakdown.maturity_fit_score),
            ("Support fit", breakdown.support_fit_score),
            ("AI preference fit", breakdown.ai_preference_fit_score),
            ("Semantic profile fit", breakdown.semantic_profile_score),
        ]
        ranked = sorted(factors, key=lambda item: item[1], reverse=True)
        return [name for name, score in ranked if score > 0][:3]

    @staticmethod
    def _industry_thesis_score(preferred_industries: Sequence[str], primary_industry: str) -> float:
        if not preferred_industries or not primary_industry:
            return 0.0

        startup_industry = primary_industry.strip().lower()
        startup_domain = RecommendationScoringService._domain_for_industry(
            startup_industry)
        preferred_domains = {RecommendationScoringService._domain_for_industry(
            item) for item in preferred_industries}
        preferred_values = {item.strip().lower()
                            for item in preferred_industries}

        if startup_industry in preferred_values:
            return 20.0
        if startup_domain in preferred_domains and startup_domain != "unknown":
            return 16.0
        return 0.0

    @staticmethod
    def _exact_match_score(preferred_values: Sequence[str], actual_value: str, max_score: float) -> float:
        if not preferred_values or not actual_value:
            return 0.0
        actual = actual_value.strip().lower()
        preferred = {item.strip().lower() for item in preferred_values}
        return max_score if actual in preferred else 0.0

    @staticmethod
    def _geography_match_score(preferred_geographies: Sequence[str], location: str) -> float:
        if not preferred_geographies or not location:
            return 0.0
        location_norm = RecommendationScoringService._resolve_geography_token(
            location)
        preferred_norms = [RecommendationScoringService._resolve_geography_token(
            item) for item in preferred_geographies]

        if location_norm in preferred_norms:
            return 8.0

        for preferred in preferred_norms:
            if preferred in REGION_COUNTRY_MAP and location_norm in REGION_COUNTRY_MAP[preferred]:
                return 8.0

        return 0.0

    @staticmethod
    def _market_scope_score(preferred_market_scopes: Sequence[str], market_scope: str) -> float:
        if not preferred_market_scopes:
            return 0.0
        preferred = {item.strip().lower() for item in preferred_market_scopes}
        if "no_strong_preference" in preferred:
            return 5.0
        market_scope_norm = market_scope.strip().lower()
        return 5.0 if market_scope_norm in preferred else 0.0

    @staticmethod
    def _product_maturity_score(preferred_product_maturity: Sequence[str], product_status: str) -> float:
        if not preferred_product_maturity or not product_status:
            return 0.0
        actual_rank = PRODUCT_MATURITY_ORDER.get(
            product_status.strip().lower(), 0)
        preferred_ranks = [PRODUCT_MATURITY_ORDER.get(
            item.strip().lower(), 0) for item in preferred_product_maturity]
        if not preferred_ranks or actual_rank == 0:
            return 0.0
        best_distance = min(abs(actual_rank - pref_rank) for pref_rank in preferred_ranks if pref_rank >
                            0) if any(pref_rank > 0 for pref_rank in preferred_ranks) else 99
        if best_distance == 0:
            return 10.0
        if best_distance == 1:
            return 8.0
        if best_distance == 2:
            return 5.0
        return 0.0

    @staticmethod
    def _validation_level_score(preferred_validation_level: Sequence[str], validation_status: str) -> float:
        if not preferred_validation_level or not validation_status:
            return 0.0
        actual_rank = VALIDATION_LEVEL_ORDER.get(
            validation_status.strip().lower(), 0)
        preferred_ranks = [VALIDATION_LEVEL_ORDER.get(
            item.strip().lower(), 0) for item in preferred_validation_level]
        if not preferred_ranks or actual_rank == 0:
            return 0.0
        best_distance = min(abs(actual_rank - pref_rank) for pref_rank in preferred_ranks if pref_rank >
                            0) if any(pref_rank > 0 for pref_rank in preferred_ranks) else 99
        if best_distance == 0:
            return 15.0
        if best_distance == 1:
            return 12.0
        if best_distance == 2:
            return 8.0
        if best_distance == 3:
            return 4.0
        return 0.0

    @staticmethod
    def _support_fit_score(support_offered: Sequence[str], current_needs: Sequence[str]) -> float:
        support_tokens = {item.strip().lower()
                          for item in support_offered if item}
        need_tokens = {item.strip().lower() for item in current_needs if item}
        if not support_tokens or not need_tokens:
            return 0.0

        overlap = support_tokens & need_tokens
        if not overlap:
            return 0.0

        coverage = len(overlap) / max(1, len(need_tokens))
        if coverage >= 0.85:
            return 10.0
        if coverage >= 0.55:
            return 7.0
        if coverage >= 0.25:
            return 5.0
        return 2.0

    @staticmethod
    def _ai_preference_score(
        prefs,
        ai_profile,
    ) -> float:
        score = 0.0
        importance_modifier = AI_SCORE_IMPORTANCE_WEIGHT.get(
            str(prefs.ai_score_importance).strip().lower(), 1.0)

        if prefs.preferred_ai_score_range and ai_profile.ai_overall_score is not None:
            minimum = float(prefs.preferred_ai_score_range.get("min", 0.0))
            maximum = float(prefs.preferred_ai_score_range.get("max", 100.0))
            if minimum <= ai_profile.ai_overall_score <= maximum:
                score += 8.0
            else:
                distance = min(abs(ai_profile.ai_overall_score - minimum),
                               abs(ai_profile.ai_overall_score - maximum))
                if distance <= 5:
                    score += 5.0
                elif distance <= 10:
                    score += 2.5
        elif ai_profile.ai_overall_score is None:
            score += 0.0

        preferred_strengths = {item.strip().lower()
                               for item in prefs.preferred_strengths if item}
        ai_strengths = {item.strip().lower()
                        for item in ai_profile.ai_strength_tags if item}
        dimension_tags = {item.replace("_", " ").strip().lower(
        ) for item in ai_profile.ai_dimension_scores.keys()}
        overlap = preferred_strengths & (ai_strengths | dimension_tags)
        if overlap:
            coverage = len(overlap) / max(1, len(preferred_strengths)
                                          ) if preferred_strengths else 1.0
            if coverage >= 0.85:
                score += 12.0
            elif coverage >= 0.55:
                score += 9.0
            elif coverage >= 0.25:
                score += 6.0
            else:
                score += 3.0

        return min(20.0, round(score * importance_modifier, 2))
