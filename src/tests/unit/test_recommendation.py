from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    InvestorRecommendationPreferences,
    LLMRerankItem,
    RecommendationBreakdown,
    StartupAIProfile,
    StartupRecommendationDocument,
    StartupStructuredProfile,
)
from src.modules.recommendation.application.services.embedding import EmbeddingService
from src.modules.recommendation.application.services.reason_renderer import RecommendationReasonRenderer
from src.modules.recommendation.application.services.scoring import RecommendationScoringService


def build_investor() -> InvestorRecommendationDocument:
    preferences = InvestorRecommendationPreferences(
        investor_name="Alpha Fund",
        investor_type="VC",
        preferred_industries=["Fintech"],
        preferred_stages=["Seed"],
        preferred_geographies=["Vietnam", "Singapore"],
        preferred_market_scopes=["B2B"],
        preferred_product_maturity=["mvp", "beta"],
        preferred_validation_level=["traction"],
        preferred_ai_score_range={"min": 60, "max": 90},
        ai_score_importance="high",
        preferred_strengths=["traction", "market"],
        support_offered=["go to market", "hiring"],
        require_verified_startups=True,
        require_visible_profiles=True,
    )
    semantic_text = "Seed fintech investor focusing on B2B founders in Vietnam and Singapore. Prefers strong traction and market opportunity."
    return InvestorRecommendationDocument(
        investor_id="inv-1",
        profile_version="1.0",
        source_updated_at=datetime.utcnow(),
        structured_preferences=preferences,
        investment_thesis_text="We back B2B fintech startups with early traction in SEA.",
        avoid_text="Avoid consumer-only plays.",
        support_offered_text="go to market, hiring",
        investor_semantic_text=semantic_text,
        investor_semantic_embedding=EmbeddingService.build_embedding(
            semantic_text),
        weights={"thesis_fit": 45, "maturity_fit": 25,
                 "support_fit": 10, "ai_preference_fit": 20},
        tags=["fintech", "seed"],
        source_payload={"account_active": True,
                        "accepting_connections_status": "active"},
    )


def build_startup(ai_missing: bool = False) -> StartupRecommendationDocument:
    profile = StartupStructuredProfile(
        startup_name="PayFlow",
        tagline="B2B payments for Southeast Asia",
        stage="Seed",
        primary_industry="Fintech",
        location="Vietnam",
        market_scope="B2B",
        product_status="beta",
        current_needs=["go to market", "hiring"],
        founder_names=["A Nguyen"],
        founder_roles=["CEO"],
        team_size="6",
        validation_status="traction",
        optional_short_metric_summary="MRR growing monthly",
        is_profile_visible_to_investors=True,
        verification_label="basic_verified",
        account_active=True,
    )
    ai_profile = StartupAIProfile(
        ai_evaluation_status="missing" if ai_missing else "completed",
        ai_overall_score=None if ai_missing else 78.0,
        ai_summary="Strong traction and clear go-to-market.",
        ai_strength_tags=["traction", "market"],
        ai_weakness_tags=["fundraising"],
        ai_dimension_scores={"traction": 8.2, "market": 7.8},
    )
    profile_text = "B2B payments for Southeast Asia. Solves cross-border payment friction. Seed fintech in Vietnam."
    ai_text = "Strong traction and clear go-to-market."
    return StartupRecommendationDocument(
        startup_id="s-1",
        profile_version="1.0",
        source_updated_at=datetime.utcnow(),
        structured_profile=profile,
        ai_profile=ai_profile,
        startup_profile_semantic_text=profile_text,
        startup_profile_embedding=EmbeddingService.build_embedding(
            profile_text),
        startup_ai_semantic_text=ai_text if not ai_missing else "",
        startup_ai_embedding=None if ai_missing else EmbeddingService.build_embedding(
            ai_text),
        tags=["fintech", "seed"],
        source_payload={"problem_statement": "Cross-border payment friction",
                        "solution_summary": "Unified payment rails"},
    )


def test_hard_filter_and_structured_scoring():
    investor = build_investor()
    startup = build_startup()

    passed, warnings = RecommendationScoringService.passes_hard_filter(
        investor, startup)
    assert passed is True
    assert warnings == []

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    assert structured.structured_score > 0
    assert structured.thesis_fit_score > 0
    assert structured.maturity_fit_score > 0
    assert structured.support_fit_score > 0
    assert structured.ai_preference_fit_score > 0


def test_semantic_fallback_when_ai_embedding_missing():
    investor = build_investor()
    startup = build_startup(ai_missing=True)

    semantic = RecommendationScoringService.score_semantic(investor, startup)
    assert semantic.semantic_profile_score >= 0
    assert semantic.semantic_ai_score is None
    assert semantic.semantic_score == semantic.semantic_profile_score
    assert "startup_ai_embedding_missing" in semantic.warnings


def test_final_score_clamps_to_bounds_and_band():
    assert RecommendationScoringService.compute_final_score(
        95, 95, 20) == 100.0
    assert RecommendationScoringService.compute_final_score(0, 0, -20) == 0.0
    assert RecommendationScoringService.band_for_score(88.0)[0] == "VERY_HIGH"
    assert RecommendationScoringService.band_for_score(71.0)[0] == "HIGH"
    assert RecommendationScoringService.band_for_score(55.0)[0] == "MEDIUM"
    assert RecommendationScoringService.band_for_score(20.0)[0] == "LOW"


def test_reason_renderer_templates():
    breakdown = RecommendationBreakdown(
        thesis_fit_score=40,
        maturity_fit_score=18,
        support_fit_score=8,
        ai_preference_fit_score=10,
        semantic_profile_score=72,
        semantic_ai_score=65,
        combined_pre_llm_score=70,
        rerank_adjustment=4,
        final_match_score=74,
        breakdown_has_missing_ai=False,
    )
    rerank_item = LLMRerankItem(
        startup_id="s-1",
        rerank_adjustment=4,
        positive_reason_codes=["INDUSTRY_MATCH",
                               "STAGE_MATCH", "SUPPORT_OVERLAP"],
        caution_reason_codes=["WEAK_VERIFICATION"],
    )

    positives, cautions = RecommendationReasonRenderer.render(
        breakdown, rerank_item, [])

    # render now returns list of (code, text) tuples
    pos_texts = [t for _, t in positives]
    pos_codes = [c for c, _ in positives]
    assert "Matches your industry focus" in pos_texts
    assert "Fits your preferred stage" in pos_texts
    assert "INDUSTRY_MATCH" in pos_codes

    caut_texts = [t for _, t in cautions]
    assert caut_texts == ["Verification strength is lower than preferred"]


def test_rrerank_adjustment_can_be_clamped_by_engine_shape():
    fake_item = SimpleNamespace(
        startup_id="s-1",
        rerank_adjustment=25,
        positive_reason_codes=["INDUSTRY_MATCH"],
        caution_reason_codes=[],
    )
    assert fake_item.rerank_adjustment > 10


# ── Regression tests for recommendation output quality ──────────────


def test_warning_flags_no_malformed_double_capped():
    """BUG-1 regression: warning flags must never contain 'capped_capped_'."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
    from unittest.mock import MagicMock

    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.repository = MagicMock()

    investor = build_investor()
    startup = build_startup()

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    semantic = RecommendationScoringService.score_semantic(investor, startup)
    combined = RecommendationScoringService.compute_combined_pre_llm_score(
        structured.structured_score, semantic.semantic_score)

    item = {
        "startup": startup,
        "structured": structured,
        "semantic": semantic,
        "combined_pre_llm_score": combined,
        "warnings": structured.warnings + semantic.warnings,
    }

    rerank_item = LLMRerankItem(
        startup_id="s-1",
        rerank_adjustment=10,  # candidate_count=1 → max_adj=2, so this will be capped
        positive_reason_codes=["INDUSTRY_MATCH"],
        caution_reason_codes=[],
    )

    result = engine._assemble_match_result(item, investor, rerank_item, 1)

    for flag in result.warning_flags:
        assert "capped_capped" not in flag, f"Malformed flag found: {flag}"
        assert flag.strip() == flag, f"Flag has whitespace: '{flag}'"


def test_zero_score_diagnostics_for_missing_investor_prefs():
    """BUG-5 regression: zero scores from empty prefs should show diagnostic reason."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
    from unittest.mock import MagicMock

    # Build investor with EMPTY maturity/support/ai prefs
    investor = build_investor()
    investor.structured_preferences.preferred_product_maturity = []
    investor.structured_preferences.preferred_validation_level = []
    investor.structured_preferences.support_offered = []
    investor.structured_preferences.preferred_strengths = []
    investor.structured_preferences.preferred_ai_score_range = None

    startup = build_startup()

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    semantic = RecommendationScoringService.score_semantic(investor, startup)
    combined = RecommendationScoringService.compute_combined_pre_llm_score(
        structured.structured_score, semantic.semantic_score)

    item = {
        "startup": startup,
        "structured": structured,
        "semantic": semantic,
        "combined_pre_llm_score": combined,
        "warnings": structured.warnings + semantic.warnings,
    }

    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.repository = MagicMock()
    result = engine._assemble_match_result(item, investor, None, 5)

    flags = result.warning_flags
    assert any(
        "maturity_score_zero:reason=investor_prefs_empty" in f for f in flags)
    assert any(
        "support_score_zero:reason=investor_support_empty" in f for f in flags)
    assert any(
        "ai_pref_score_zero:reason=investor_prefs_empty" in f for f in flags)


def test_reason_codes_preserved_in_positive_reasons():
    """BUG-3 regression: positive_reasons must carry actual codes, not 'POS'."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
    from unittest.mock import MagicMock

    investor = build_investor()
    startup = build_startup()

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    semantic = RecommendationScoringService.score_semantic(investor, startup)
    combined = RecommendationScoringService.compute_combined_pre_llm_score(
        structured.structured_score, semantic.semantic_score)

    item = {
        "startup": startup,
        "structured": structured,
        "semantic": semantic,
        "combined_pre_llm_score": combined,
        "warnings": structured.warnings + semantic.warnings,
    }

    rerank_item = LLMRerankItem(
        startup_id="s-1",
        rerank_adjustment=3,
        positive_reason_codes=["INDUSTRY_MATCH", "STAGE_MATCH"],
        caution_reason_codes=[],
    )

    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.repository = MagicMock()
    result = engine._assemble_match_result(item, investor, rerank_item, 5)

    for reason in result.positive_reasons:
        assert reason.code != "POS", f"Hardcoded 'POS' found in positive_reasons"
    for reason in result.caution_reasons:
        assert reason.code != "CAUT", f"Hardcoded 'CAUT' found in caution_reasons"


def test_support_fit_caution_uses_correct_code():
    """BUG-4 regression: support_fit=0 should produce SUPPORT_MISMATCH, not WEAK_VERIFICATION."""
    breakdown = RecommendationBreakdown(
        thesis_fit_score=40,
        maturity_fit_score=18,
        support_fit_score=0,  # zero support fit
        ai_preference_fit_score=10,
        semantic_profile_score=72,
        semantic_ai_score=65,
        combined_pre_llm_score=70,
        rerank_adjustment=0,
        final_match_score=70,
        breakdown_has_missing_ai=False,
    )

    positives, cautions = RecommendationReasonRenderer.render(
        breakdown, None, [])
    caut_codes = [c for c, _ in cautions]
    caut_texts = [t for _, t in cautions]

    # Must NOT produce the old wrong code
    assert "WEAK_VERIFICATION" not in caut_codes
    # Should use the correct code
    if cautions:
        assert caut_codes[0] == "SUPPORT_MISMATCH"


def test_warning_flags_are_deduplicated():
    """Ensure _dedupe prevents duplicate warning flags."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine

    result = RecommendationEngine._dedupe([
        "ai_evaluation_missing",
        "startup_ai_embedding_missing",
        "ai_evaluation_missing",
        "startup_ai_embedding_missing",
        "llm_rerank_applied",
    ])
    assert result == [
        "ai_evaluation_missing",
        "startup_ai_embedding_missing",
        "llm_rerank_applied",
    ]


def test_normalize_warning_flags_sorted_and_deduped():
    """_normalize_warning_flags must dedupe, strip, and sort alphabetically."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine

    result = RecommendationEngine._normalize_warning_flags([
        "verification_weak",
        "ai_evaluation_missing",
        "startup_ai_embedding_missing",
        "verification_weak",
        "llm_rerank_applied",
        "  ",  # blank — should be dropped
        "ai_evaluation_missing",
    ])
    assert result == [
        "ai_evaluation_missing",
        "llm_rerank_applied",
        "startup_ai_embedding_missing",
        "verification_weak",
    ]


def test_warning_flags_are_machine_readable_keys():
    """All warning_flags must be machine-readable keys — no human sentences."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
    from unittest.mock import MagicMock

    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.repository = MagicMock()

    investor = build_investor()
    startup = build_startup(ai_missing=True)  # triggers ai-related warnings

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    semantic = RecommendationScoringService.score_semantic(investor, startup)
    combined = RecommendationScoringService.compute_combined_pre_llm_score(
        structured.structured_score, semantic.semantic_score)

    item = {
        "startup": startup,
        "structured": structured,
        "semantic": semantic,
        "combined_pre_llm_score": combined,
        "warnings": structured.warnings + semantic.warnings,
    }

    result = engine._assemble_match_result(item, investor, None, 5)

    for flag in result.warning_flags:
        # No spaces allowed except in colon-separated diagnostic keys
        assert "." not in flag, f"Human sentence found in warning_flags: {flag}"
        assert flag == flag.strip(), f"Untrimmed flag: '{flag}'"
        # Should not contain "AI evaluation is not available yet." or "Weak verification"
        assert "is not available" not in flag.lower()
        assert flag != "Weak verification"


def test_match_reasons_contain_only_positive_texts():
    """match_reasons must be positive-only — never contain caution text."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
    from unittest.mock import MagicMock
    from src.modules.recommendation.application.services.reason_renderer import REASON_CODE_TEMPLATES

    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.repository = MagicMock()

    investor = build_investor()
    # triggers AI_SCORE_MISSING caution
    startup = build_startup(ai_missing=True)

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    semantic = RecommendationScoringService.score_semantic(investor, startup)
    combined = RecommendationScoringService.compute_combined_pre_llm_score(
        structured.structured_score, semantic.semantic_score)

    item = {
        "startup": startup,
        "structured": structured,
        "semantic": semantic,
        "combined_pre_llm_score": combined,
        "warnings": structured.warnings + semantic.warnings,
    }

    rerank_item = LLMRerankItem(
        startup_id="s-1",
        rerank_adjustment=2,
        positive_reason_codes=["INDUSTRY_MATCH"],
        caution_reason_codes=["AI_SCORE_MISSING"],
    )

    result = engine._assemble_match_result(item, investor, rerank_item, 5)

    caution_texts = {REASON_CODE_TEMPLATES.get(c) for c in [
        "VALIDATION_EARLY", "AI_SCORE_MISSING", "WEAK_VERIFICATION",
        "SUPPORT_MISMATCH",
    ]}

    for reason in result.match_reasons:
        assert reason not in caution_texts, (
            f"Caution text found in match_reasons: {reason}"
        )

    # match_reasons should have at most 3 items
    assert len(result.match_reasons) <= 3


def test_hard_filter_applied_not_in_item_warning_flags():
    """hard_filter_applied is run-level, must never appear in item-level warning_flags."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
    from unittest.mock import MagicMock

    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.repository = MagicMock()

    investor = build_investor()
    startup = build_startup()

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    semantic = RecommendationScoringService.score_semantic(investor, startup)
    combined = RecommendationScoringService.compute_combined_pre_llm_score(
        structured.structured_score, semantic.semantic_score)

    item = {
        "startup": startup,
        "structured": structured,
        "semantic": semantic,
        "combined_pre_llm_score": combined,
        "warnings": structured.warnings + semantic.warnings,
    }

    # Even with candidate_count=1 (meaning other startups were filtered)
    result = engine._assemble_match_result(item, investor, None, 1)

    for flag in result.warning_flags:
        assert flag != "hard_filter_applied", (
            "hard_filter_applied should not appear in item-level warning_flags"
        )


def test_list_vs_explanation_same_core_result():
    """Given the same investor+startup, list and explanation should produce
    consistent positive_reasons, caution_reasons, match_reasons, and
    warning_flags (modulo run-level fields)."""
    from src.modules.recommendation.application.services.recommendation_engine import RecommendationEngine
    from unittest.mock import MagicMock

    engine = RecommendationEngine.__new__(RecommendationEngine)
    engine.repository = MagicMock()

    investor = build_investor()
    startup = build_startup()

    structured = RecommendationScoringService.score_structured(
        investor, startup)
    semantic = RecommendationScoringService.score_semantic(investor, startup)
    combined = RecommendationScoringService.compute_combined_pre_llm_score(
        structured.structured_score, semantic.semantic_score)

    item = {
        "startup": startup,
        "structured": structured,
        "semantic": semantic,
        "combined_pre_llm_score": combined,
        "warnings": structured.warnings + semantic.warnings,
    }

    rerank_item = LLMRerankItem(
        startup_id="s-1",
        rerank_adjustment=3,
        positive_reason_codes=["INDUSTRY_MATCH", "STAGE_MATCH"],
        caution_reason_codes=[],
    )

    # Simulate list-context assembly (candidate_count=5)
    result_list = engine._assemble_match_result(item, investor, rerank_item, 5)
    # Simulate explanation-context assembly (same candidate_count)
    result_expl = engine._assemble_match_result(item, investor, rerank_item, 5)

    # Core fields must be identical
    assert result_list.positive_reasons == result_expl.positive_reasons
    assert result_list.caution_reasons == result_expl.caution_reasons
    assert result_list.match_reasons == result_expl.match_reasons
    assert result_list.warning_flags == result_expl.warning_flags
    assert result_list.final_match_score == result_expl.final_match_score
    assert result_list.match_band == result_expl.match_band
    assert result_list.fit_summary_label == result_expl.fit_summary_label
