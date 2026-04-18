from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    RecommendationBreakdown,
    RecommendationExplanationResponse,
    RecommendationListResponse,
    RecommendationMatchResult,
    RecommendationRunRecord,
    ReindexInvestorRequest,
    ReindexStartupRequest,
    StartupAIProfile,
    StartupRecommendationDocument,
    StartupStructuredProfile,
    InvestorRecommendationPreferences,
)
from src.modules.recommendation.application.services.embedding import EmbeddingService
from src.modules.recommendation.application.services.llm_reranker import RecommendationLLMReranker
from src.modules.recommendation.application.services.reason_renderer import RecommendationReasonRenderer
from src.modules.recommendation.application.services.scoring import RecommendationScoringService
from src.modules.recommendation.infrastructure.repositories.repo_factory import get_recommendation_repository


class RecommendationEngine:
    def __init__(
        self,
        repository=None,
        reranker: RecommendationLLMReranker | None = None,
    ) -> None:
        self.repository = repository or get_recommendation_repository()
        self.reranker = reranker or RecommendationLLMReranker()

    def reindex_investor(self, investor_id: str, request: ReindexInvestorRequest) -> InvestorRecommendationDocument:
        semantic_text = self._build_investor_semantic_text(request)
        document = InvestorRecommendationDocument(
            investor_id=investor_id,
            profile_version=request.profile_version,
            source_updated_at=request.source_updated_at,
            structured_preferences=InvestorRecommendationPreferences(
                investor_name=request.investor_name,
                investor_type=request.investor_type,
                preferred_industries=request.preferred_industries,
                preferred_stages=request.preferred_stages,
                preferred_geographies=request.preferred_geographies,
                preferred_market_scopes=request.preferred_market_scopes,
                preferred_product_maturity=request.preferred_product_maturity,
                preferred_validation_level=request.preferred_validation_level,
                preferred_ai_score_range=request.preferred_ai_score_range,
                ai_score_importance=request.ai_score_importance,
                preferred_strengths=request.preferred_strengths,
                support_offered=request.support_offered,
                require_verified_startups=request.require_verified_startups,
                require_visible_profiles=request.require_visible_profiles,
            ),
            investment_thesis_text=request.short_thesis_summary,
            avoid_text=request.avoid_text,
            support_offered_text=self._join_lines(request.support_offered),
            investor_semantic_text=semantic_text,
            investor_semantic_embedding=EmbeddingService.build_embedding(
                semantic_text),
            weights={
                "thesis_fit": 45.0,
                "maturity_fit": 25.0,
                "support_fit": 10.0,
                "ai_preference_fit": 20.0,
            },
            tags=request.tags,
            source_payload=request.model_dump(mode="json"),
        )
        return self.repository.upsert_investor(document)

    def reindex_startup(self, startup_id: str, request: ReindexStartupRequest) -> StartupRecommendationDocument:
        profile_text = self._build_startup_profile_semantic_text(request)
        ai_text = self._build_startup_ai_semantic_text(request)
        document = StartupRecommendationDocument(
            startup_id=startup_id,
            profile_version=request.profile_version,
            source_updated_at=request.source_updated_at,
            structured_profile=StartupStructuredProfile(
                startup_name=request.startup_name,
                tagline=request.tagline,
                stage=request.stage,
                primary_industry=request.primary_industry,
                location=request.location,
                market_scope=request.market_scope,
                product_status=request.product_status,
                current_needs=request.current_needs,
                founder_names=request.founder_names,
                founder_roles=request.founder_roles,
                team_size=request.team_size,
                validation_status=request.validation_status,
                optional_short_metric_summary=request.optional_short_metric_summary,
                is_profile_visible_to_investors=request.is_profile_visible_to_investors,
                verification_label=request.verification_label,
                account_active=request.account_active,
            ),
            ai_profile=StartupAIProfile(
                ai_evaluation_status=request.ai_evaluation_status,
                ai_overall_score=request.ai_overall_score,
                ai_summary=request.ai_summary,
                ai_strength_tags=request.ai_strength_tags,
                ai_weakness_tags=request.ai_weakness_tags,
                ai_dimension_scores=request.ai_dimension_scores,
            ),
            startup_profile_semantic_text=profile_text,
            startup_profile_embedding=EmbeddingService.build_embedding(
                profile_text),
            startup_ai_semantic_text=ai_text,
            startup_ai_embedding=EmbeddingService.build_embedding(ai_text)
            if ai_text.strip() and request.ai_evaluation_status != "missing"
            else None,
            tags=request.tags,
            source_payload=request.model_dump(mode="json"),
        )
        return self.repository.upsert_startup(document)

    def get_recommendations(self, investor_id: str, top_n: int = 10) -> RecommendationListResponse:
        investor = self.repository.get_investor(investor_id)
        if investor is None:
            raise ValueError(
                f"Investor recommendation document not found for {investor_id}")

        startup_docs = self.repository.list_startups()
        candidate_pool: List[StartupRecommendationDocument] = []
        candidate_warnings: List[str] = []
        hard_filter_rejections: List[str] = []

        for startup in startup_docs:
            passed, reasons = RecommendationScoringService.passes_hard_filter(
                investor, startup)
            if passed:
                candidate_pool.append(startup)
            else:
                hard_filter_rejections.extend(
                    [f"{startup.startup_id}:{reason}" for reason in reasons])

        scored_candidates: List[Dict[str, Any]] = []
        for startup in candidate_pool:
            structured = RecommendationScoringService.score_structured(
                investor, startup)
            semantic = RecommendationScoringService.score_semantic(
                investor, startup)
            combined = RecommendationScoringService.compute_combined_pre_llm_score(
                structured.structured_score,
                semantic.semantic_score,
            )
            scored_candidates.append(
                {
                    "startup": startup,
                    "structured": structured,
                    "semantic": semantic,
                    "combined_pre_llm_score": combined,
                    "warnings": structured.warnings + semantic.warnings,
                }
            )

        scored_candidates.sort(
            key=lambda item: item["combined_pre_llm_score"], reverse=True)
        candidate_set = scored_candidates[: min(max(top_n, 8), 10)]
        candidate_count = len(candidate_set)

        rerank_cards = [self._candidate_card_for_llm(
            item) for item in candidate_set]
        rerank_items, rerank_warnings = self.reranker.rerank(
            investor, rerank_cards)
        rerank_map = {item.startup_id: item for item in rerank_items}

        results: List[RecommendationMatchResult] = []
        for item in candidate_set:
            result = self._assemble_match_result(item, investor, rerank_map.get(
                item["startup"].startup_id), candidate_count)
            results.append(result)

        results.sort(key=lambda item: item.final_match_score, reverse=True)

        public_warnings = []
        if len(candidate_pool) == 0:
            public_warnings.append("No candidates remain after your filters.")
        elif candidate_count == 1:
            public_warnings.append(
                "Recommendation list is narrow due to strict stage/geography constraints.")

        run_level_flags: List[str] = []
        if hard_filter_rejections:
            run_level_flags.append("hard_filter_applied")
        run_level_flags.extend(rerank_warnings)
        internal_warnings = self._dedupe(
            candidate_warnings + hard_filter_rejections + run_level_flags)

        run = RecommendationRunRecord(
            run_id=str(uuid.uuid4()),
            investor_id=investor_id,
            investor_profile_version=investor.profile_version,
            candidate_count=len(candidate_pool),
            candidate_set_size=len(candidate_set),
            candidate_startup_ids=[item.startup_id for item in candidate_pool],
            results=results,
            warnings=internal_warnings,
        )
        self.repository.store_run(run)

        return RecommendationListResponse(
            investor_id=investor_id,
            matches=results[:top_n],
            warnings=public_warnings,
            internal_warnings=internal_warnings,
            generated_at=datetime.utcnow(),
        )

    def get_explanation(self, investor_id: str, startup_id: str) -> RecommendationExplanationResponse:
        investor = self.repository.get_investor(investor_id)
        startup = self.repository.get_startup(startup_id)
        if investor is None:
            raise ValueError(
                f"Investor recommendation document not found for {investor_id}")
        if startup is None:
            raise ValueError(
                f"Startup recommendation document not found for {startup_id}")

        # Compute real candidate count so rerank policy uses realistic caps
        all_startups = self.repository.list_startups()
        real_candidate_count = sum(
            1 for s in all_startups
            if RecommendationScoringService.passes_hard_filter(investor, s)[0]
        )
        # Ensure at least 1 (the startup being explained passed the filter implicitly)
        real_candidate_count = max(real_candidate_count, 1)

        structured = RecommendationScoringService.score_structured(
            investor, startup)
        semantic = RecommendationScoringService.score_semantic(
            investor, startup)
        combined = RecommendationScoringService.compute_combined_pre_llm_score(
            structured.structured_score,
            semantic.semantic_score,
        )
        fake_pool_item = {
            "startup": startup,
            "structured": structured,
            "semantic": semantic,
            "combined_pre_llm_score": combined,
            "warnings": structured.warnings + semantic.warnings,
        }
        rerank_items, rerank_warnings = self.reranker.rerank(
            investor, [self._candidate_card_for_single(startup, structured, semantic, combined)])
        rerank_item = rerank_items[0] if rerank_items else None

        result = self._assemble_match_result(
            fake_pool_item, investor, rerank_item, real_candidate_count)

        return RecommendationExplanationResponse(
            investor_id=investor_id,
            startup_id=startup_id,
            explanation=result,
            generated_at=datetime.utcnow(),
        )

    def _assemble_match_result(self, item: Dict[str, Any], investor: InvestorRecommendationDocument, rerank_item: Any, candidate_count: int) -> RecommendationMatchResult:
        startup = item["startup"]
        structured = item["structured"]
        semantic = item["semantic"]
        combined = item["combined_pre_llm_score"]

        raw_adjustment = float(
            rerank_item.rerank_adjustment if rerank_item else 0)

        if candidate_count < 2:
            max_adj = 2.0
            policy = "small_pool"
        elif candidate_count <= 4:
            max_adj = 5.0
            policy = "medium_pool"
        else:
            max_adj = 10.0
            policy = "default_cap_10"

        if structured.maturity_fit_score <= 0.1 or structured.support_fit_score <= 0.1:
            max_adj = min(max_adj, 4.0)
            policy = "weak_fit"

        adjustment = max(-max_adj, min(max_adj, raw_adjustment))
        capped = (adjustment != raw_adjustment)

        final_score = RecommendationScoringService.compute_final_score(
            structured.structured_score,
            semantic.semantic_score,
            adjustment,
        )

        breakdown = RecommendationBreakdown(
            thesis_fit_score=structured.thesis_fit_score,
            maturity_fit_score=structured.maturity_fit_score,
            support_fit_score=structured.support_fit_score,
            ai_preference_fit_score=structured.ai_preference_fit_score,
            semantic_profile_score=semantic.semantic_profile_score,
            semantic_ai_score=semantic.semantic_ai_score,
            combined_pre_llm_score=combined,
            rerank_adjustment=adjustment,
            final_match_score=final_score,
            breakdown_has_missing_ai=(semantic.semantic_ai_score is None),
            candidate_count=candidate_count,
            rerank_policy_applied=policy,
            rerank_capped=capped
        )

        band, label = RecommendationScoringService.band_for_score(
            final_score, breakdown)

        positive_coded, caution_coded = RecommendationReasonRenderer.render(
            breakdown,
            rerank_item,
            item["warnings"],
        )

        from src.modules.recommendation.application.dto.recommendation_schema import RecommendationReasonItem
        pos_items = [RecommendationReasonItem(
            type="positive", code=coded[0], text=coded[1]) for coded in positive_coded]
        caution_items = [RecommendationReasonItem(
            type="caution", code=coded[0], text=coded[1]) for coded in caution_coded]
        # match_reasons: concise positive-only summary (never cautions)
        match_reasons = [coded[1] for coded in positive_coded][:3]

        # ── Item-level warning_flags (machine-readable keys only) ──
        warnings = list(item["warnings"])
        warnings.extend(self._warnings_from_item(startup, investor, semantic))
        # Diagnostic flags: distinguish zero-from-missing vs zero-from-mismatch
        prefs = investor.structured_preferences
        profile = startup.structured_profile
        if structured.maturity_fit_score <= 0.1:
            if not prefs.preferred_product_maturity and not prefs.preferred_validation_level:
                warnings.append(
                    "maturity_score_zero:reason=investor_prefs_empty")
            elif not profile.product_status and not profile.validation_status:
                warnings.append(
                    "maturity_score_zero:reason=startup_data_missing")
            else:
                warnings.append("maturity_score_zero:reason=mismatch")
        if structured.support_fit_score <= 0.1:
            if not prefs.support_offered:
                warnings.append(
                    "support_score_zero:reason=investor_support_empty")
            elif not profile.current_needs:
                warnings.append(
                    "support_score_zero:reason=startup_needs_empty")
            else:
                warnings.append("support_score_zero:reason=mismatch")
        if structured.ai_preference_fit_score <= 0.1:
            if not prefs.preferred_strengths and not prefs.preferred_ai_score_range:
                warnings.append(
                    "ai_pref_score_zero:reason=investor_prefs_empty")
            elif startup.ai_profile.ai_evaluation_status == "missing":
                warnings.append("ai_pref_score_zero:reason=ai_eval_missing")
            else:
                warnings.append("ai_pref_score_zero:reason=mismatch")
        if adjustment != 0:
            warnings.append("llm_rerank_applied")
        if capped:
            warnings.append(f"rerank_capped:policy={policy}")
        # NOTE: hard_filter_applied is a run-level flag, not item-level.
        # It belongs in RecommendationListResponse.internal_warnings only.

        result = RecommendationMatchResult(
            investor_id=investor.investor_id,
            startup_id=startup.startup_id,
            startup_name=startup.structured_profile.startup_name,
            final_match_score=final_score,
            match_band=band,
            fit_summary_label=label,
            structured_score=structured.structured_score,
            semantic_score=semantic.semantic_score,
            combined_pre_llm_score=combined,
            rerank_adjustment=adjustment,
            breakdown=breakdown,
            match_reasons=match_reasons,
            positive_reasons=pos_items,
            caution_reasons=caution_items,
            warning_flags=self._normalize_warning_flags(warnings),
            generated_at=datetime.utcnow(),
        )

        return result

    def _build_investor_semantic_text(self, request: ReindexInvestorRequest) -> str:
        parts = [
            request.short_thesis_summary,
            f"Preferred industries: {self._join_lines(request.preferred_industries)}",
            f"Preferred stages: {self._join_lines(request.preferred_stages)}",
            f"Preferred geographies: {self._join_lines(request.preferred_geographies)}",
            f"Preferred strengths: {self._join_lines(request.preferred_strengths)}",
        ]
        return "\n".join(part for part in parts if part)

    def _build_startup_profile_semantic_text(self, request: ReindexStartupRequest) -> str:
        parts = [
            request.tagline,
            request.problem_statement,
            request.solution_summary,
            request.stage,
            request.primary_industry,
            request.location,
            request.market_scope,
        ]
        return "\n".join(part for part in parts if part)

    def _build_startup_ai_semantic_text(self, request: ReindexStartupRequest) -> str:
        parts = [
            request.ai_summary,
            f"Strengths: {self._join_lines(request.ai_strength_tags)}",
            f"Weaknesses: {self._join_lines(request.ai_weakness_tags)}",
        ]
        return "\n".join(part for part in parts if part)

    def _candidate_card_for_llm(self, item: Dict[str, Any]) -> Dict[str, Any]:
        startup = item["startup"]
        structured = item["structured"]
        semantic = item["semantic"]
        return {
            "startup_id": startup.startup_id,
            "startup_name": startup.structured_profile.startup_name,
            "stage": startup.structured_profile.stage,
            "primary_industry": startup.structured_profile.primary_industry,
            "location": startup.structured_profile.location,
            "market_scope": startup.structured_profile.market_scope,
            "product_status": startup.structured_profile.product_status,
            "validation_status": startup.structured_profile.validation_status,
            "current_needs": startup.structured_profile.current_needs,
            "tagline": startup.structured_profile.tagline,
            "problem_statement": startup.source_payload.get("problem_statement", ""),
            "solution_summary": startup.source_payload.get("solution_summary", ""),
            "ai_overall_score": startup.ai_profile.ai_overall_score,
            "ai_summary": startup.ai_profile.ai_summary,
            "ai_strength_tags": startup.ai_profile.ai_strength_tags,
            "structured_score": structured.structured_score,
            "semantic_score": semantic.semantic_score,
        }

    def _candidate_card_for_single(
        self,
        startup: StartupRecommendationDocument,
        structured,
        semantic,
        combined: float,
    ) -> Dict[str, Any]:
        return {
            "startup_id": startup.startup_id,
            "startup_name": startup.structured_profile.startup_name,
            "stage": startup.structured_profile.stage,
            "primary_industry": startup.structured_profile.primary_industry,
            "location": startup.structured_profile.location,
            "market_scope": startup.structured_profile.market_scope,
            "product_status": startup.structured_profile.product_status,
            "validation_status": startup.structured_profile.validation_status,
            "current_needs": startup.structured_profile.current_needs,
            "tagline": startup.structured_profile.tagline,
            "problem_statement": startup.source_payload.get("problem_statement", ""),
            "solution_summary": startup.source_payload.get("solution_summary", ""),
            "ai_overall_score": startup.ai_profile.ai_overall_score,
            "ai_summary": startup.ai_profile.ai_summary,
            "ai_strength_tags": startup.ai_profile.ai_strength_tags,
            "structured_score": structured.structured_score,
            "semantic_score": semantic.semantic_score,
        }

    def _warnings_from_item(
        self,
        startup: StartupRecommendationDocument,
        investor: InvestorRecommendationDocument,
        semantic,
    ) -> List[str]:
        """Return item-level diagnostic flags as machine-readable keys."""
        warnings: List[str] = []
        if startup.ai_profile.ai_evaluation_status == "missing":
            warnings.append("ai_evaluation_missing")
        if startup.structured_profile.verification_label.strip().lower() in {"pending_more_info", "basic_verified"}:
            warnings.append("verification_weak")
        if semantic.semantic_ai_score is None:
            warnings.append("startup_ai_embedding_missing")
        if investor.structured_preferences.ai_score_importance == "high" and startup.ai_profile.ai_overall_score is None:
            warnings.append("ai_score_high_importance_missing")
        return warnings

    @staticmethod
    def _join_lines(values: List[str]) -> str:
        return ", ".join(value.strip() for value in values if value and value.strip())

    @staticmethod
    def _normalize_warning_flags(values: List[str]) -> List[str]:
        """Deduplicate, strip, and sort warning flags for deterministic output."""
        seen: set[str] = set()
        unique: list[str] = []
        for value in values:
            key = value.strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(key)
        unique.sort()
        return unique

    @staticmethod
    def _dedupe(values: List[str]) -> List[str]:
        seen = set()
        ordered: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered
