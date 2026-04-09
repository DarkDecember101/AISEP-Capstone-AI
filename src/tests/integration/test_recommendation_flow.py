from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from src.modules.recommendation.application.dto.recommendation_schema import (
    ReindexInvestorRequest,
    ReindexStartupRequest,
)
from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
from src.modules.recommendation.infrastructure.repositories.recommendation_repository import RecommendationRepository


class FakeReranker:
    def __init__(self, adjustments: dict[str, int] | None = None):
        self.adjustments = adjustments or {}

    def rerank(self, investor, candidates):
        items = []
        for candidate in candidates:
            startup_id = candidate["startup_id"]
            items.append(
                SimpleNamespace(
                    startup_id=startup_id,
                    rerank_adjustment=self.adjustments.get(startup_id, 0),
                    positive_reason_codes=[
                        "INDUSTRY_MATCH", "STAGE_MATCH", "STRENGTHS_ALIGN"],
                    caution_reason_codes=["AI_SCORE_MISSING"] if candidate.get(
                        "ai_overall_score") is None else [],
                )
            )
        return items, []


def build_engine(tmp_path, adjustments: dict[str, int] | None = None):
    repo = RecommendationRepository(base_dir=tmp_path / "recommendations")
    engine = RecommendationEngine(
        repository=repo, reranker=FakeReranker(adjustments))
    return engine, repo


def build_investor_request() -> ReindexInvestorRequest:
    return ReindexInvestorRequest(
        profile_version="1.0",
        source_updated_at=datetime.utcnow(),
        investor_name="Alpha Fund",
        investor_type="VC",
        organization="Alpha Capital",
        role_title="Partner",
        location="Singapore",
        website="https://alpha.example.com",
        verification_label="verified_fund",
        logo_url="https://alpha.example.com/logo.png",
        short_thesis_summary="We back B2B fintech startups in SEA with strong traction and clear GTM.",
        preferred_industries=["Fintech"],
        preferred_stages=["Seed"],
        preferred_geographies=["Singapore", "Vietnam"],
        preferred_market_scopes=["B2B"],
        preferred_product_maturity=["beta"],
        preferred_validation_level=["traction"],
        preferred_ai_score_range={"min": 60, "max": 90},
        ai_score_importance="high",
        preferred_strengths=["traction", "market"],
        support_offered=["go to market", "hiring"],
        accepting_connections_status="active",
        recently_active_badge=True,
        require_verified_startups=True,
        require_visible_profiles=True,
        avoid_text="consumer-only",
        tags=["fintech", "sea"],
    )


def build_startup_request(
    *,
    startup_id: str,
    name: str,
    ai_score: float | None,
    ai_missing: bool = False,
    visible: bool = True,
    verified_label: str = "basic_verified",
    stage: str = "Seed",
    location: str = "Vietnam",
) -> ReindexStartupRequest:
    return ReindexStartupRequest(
        profile_version="1.0",
        source_updated_at=datetime.utcnow(),
        startup_name=name,
        tagline=f"{name} improves B2B payments",
        stage=stage,
        primary_industry="Fintech",
        location=location,
        website=f"https://{startup_id}.example.com",
        product_link=f"https://{startup_id}.example.com/product",
        demo_link=f"https://{startup_id}.example.com/demo",
        logo_url=f"https://{startup_id}.example.com/logo.png",
        problem_statement="Cross-border payment friction",
        solution_summary="Unified payment rails for SMEs",
        market_scope="B2B",
        product_status="beta",
        current_needs=["go to market", "hiring"],
        founder_names=["Founder A"],
        founder_roles=["CEO"],
        team_size="6",
        validation_status="traction",
        optional_short_metric_summary="Monthly recurring revenue growing",
        is_profile_visible_to_investors=visible,
        verification_label=verified_label,
        account_active=True,
        ai_evaluation_status="missing" if ai_missing or ai_score is None else "completed",
        ai_overall_score=ai_score,
        ai_summary="Strong traction and clear go-to-market" if ai_score is not None and not ai_missing else "",
        ai_strength_tags=[
            "traction", "market"] if ai_score is not None and not ai_missing else [],
        ai_weakness_tags=["fundraising"],
        ai_dimension_scores={
            "traction": 8.0, "market": 7.5} if ai_score is not None and not ai_missing else {},
        tags=["fintech", "b2b"],
    )


def test_retrieve_limits_candidate_set_and_is_stable(tmp_path):
    engine, repo = build_engine(tmp_path)
    investor = build_investor_request()
    engine.reindex_investor("inv-1", investor)

    for index in range(12):
        ai_score = 60 + index
        engine.reindex_startup(
            f"s-{index}",
            build_startup_request(
                startup_id=f"s-{index}", name=f"Startup {index}", ai_score=ai_score),
        )

    first = engine.get_recommendations("inv-1", top_n=10)
    second = engine.get_recommendations("inv-1", top_n=10)

    assert len(first.items) == 10
    assert len(second.items) == 10
    assert [item.startup_id for item in first.items] == [
        item.startup_id for item in second.items]

    latest_run = repo.latest_run_for_investor("inv-1")
    assert latest_run is not None
    assert latest_run.candidate_set_size == 10
    assert latest_run.candidate_count == 12


def test_hard_filter_excludes_non_matching_startups(tmp_path):
    engine, _ = build_engine(tmp_path)
    engine.reindex_investor("inv-1", build_investor_request())

    engine.reindex_startup("visible-ok", build_startup_request(
        startup_id="visible-ok", name="Visible OK", ai_score=80))
    engine.reindex_startup("hidden", build_startup_request(
        startup_id="hidden", name="Hidden Startup", ai_score=80, visible=False))
    engine.reindex_startup("failed", build_startup_request(
        startup_id="failed", name="Failed Startup", ai_score=80, verified_label="failed"))
    engine.reindex_startup("wrong-stage", build_startup_request(
        startup_id="wrong-stage", name="Wrong Stage", ai_score=80, stage="Series A"))

    result = engine.get_recommendations("inv-1", top_n=10)
    startup_ids = [item.startup_id for item in result.items]

    assert startup_ids == ["visible-ok"]
    assert all(
        "hard_filter_applied" in item.warning_flags for item in result.items)


def test_missing_ai_adds_warning_and_explanation(tmp_path):
    engine, _ = build_engine(tmp_path)
    engine.reindex_investor("inv-1", build_investor_request())
    engine.reindex_startup("no-ai", build_startup_request(startup_id="no-ai",
                           name="No AI", ai_score=None, ai_missing=True))

    result = engine.get_recommendations("inv-1", top_n=10)
    assert len(result.items) == 1
    assert any(
        warning in result.items[0].warning_flags
        for warning in ["AI evaluation is not available yet.", "startup_ai_embedding_missing"]
    )

    explanation = engine.get_explanation("inv-1", "no-ai")
    assert explanation.result.startup_id == "no-ai"
    assert explanation.result.breakdown.semantic_ai_score is None


def test_rerank_adjustment_is_clamped(tmp_path):
    engine, _ = build_engine(tmp_path, adjustments={"clamped": 25})
    engine.reindex_investor("inv-1", build_investor_request())
    engine.reindex_startup("clamped", build_startup_request(
        startup_id="clamped", name="Clamped Startup", ai_score=82))

    result = engine.get_recommendations("inv-1", top_n=10)
    assert len(result.items) == 1
    # Since there's only 1 startup, the rule caps rerank at 2.0
    assert result.items[0].rerank_adjustment == 2.0
    assert any("rerank_capped" in w for w in result.items[0].warning_flags)
    assert 0.0 <= result.items[0].final_match_score <= 100.0
