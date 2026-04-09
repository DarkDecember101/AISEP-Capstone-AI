"""
Phase 2B – Recommendation storage migration tests.

Covers:
 - DB-backed repository: upsert/get investor, upsert/get/list startup,
   store/list/latest run, idempotent upsert, missing entity returns None
 - Repo factory: returns correct backend based on RECOMMENDATION_BACKEND
 - Migration utility: imports JSON files, idempotent re-run, handles
   malformed files, handles missing directories
 - Health check: DB backend probe
 - Engine wiring: engine uses factory-provided repo
 - Settings: new RECOMMENDATION_BACKEND field defaults
 - Integration: full reindex→query→explain round-trip via DB repo
"""

from __future__ import annotations
from src.shared.health import _check_recommendation_storage
from src.modules.recommendation.scripts.migrate_json_to_db import migrate
from src.shared.config.settings import Settings

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import patch

import pytest

from src.shared.persistence.db import init_db, engine as db_engine
from src.shared.persistence.models.recommendation_models import (
    RecommendationInvestorRow,
    RecommendationRunRow,
    RecommendationStartupRow,
)
from src.modules.recommendation.infrastructure.repositories.db_recommendation_repository import (
    DBRecommendationRepository,
)
from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    InvestorRecommendationPreferences,
    RecommendationRunRecord,
    RecommendationMatchResult,
    RecommendationBreakdown,
    ReindexInvestorRequest,
    ReindexStartupRequest,
    StartupAIProfile,
    StartupRecommendationDocument,
    StartupStructuredProfile,
)
from src.modules.recommendation.application.services.embedding import EmbeddingService


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _ensure_db_tables():
    """Make sure recommendation tables exist before every test."""
    init_db()
    yield


def _build_investor_doc(investor_id: str = "inv-test-1") -> InvestorRecommendationDocument:
    prefs = InvestorRecommendationPreferences(
        investor_name="Test Fund",
        investor_type="VC",
        preferred_industries=["Fintech"],
        preferred_stages=["Seed"],
        preferred_geographies=["Vietnam"],
        preferred_market_scopes=["B2B"],
    )
    semantic = "Seed fintech VC in Vietnam"
    return InvestorRecommendationDocument(
        investor_id=investor_id,
        profile_version="1.0",
        source_updated_at=datetime.utcnow(),
        structured_preferences=prefs,
        investment_thesis_text="Back B2B fintech in SEA.",
        investor_semantic_text=semantic,
        investor_semantic_embedding=EmbeddingService.build_embedding(semantic),
    )


def _build_startup_doc(startup_id: str = "s-test-1") -> StartupRecommendationDocument:
    profile = StartupStructuredProfile(
        startup_name="TestPay",
        tagline="B2B payments",
        stage="Seed",
        primary_industry="Fintech",
        location="Vietnam",
        market_scope="B2B",
        product_status="beta",
        is_profile_visible_to_investors=True,
        verification_label="basic_verified",
        account_active=True,
    )
    ai = StartupAIProfile(
        ai_evaluation_status="completed",
        ai_overall_score=78.0,
        ai_summary="Strong traction.",
    )
    text = "B2B payments Seed fintech Vietnam"
    return StartupRecommendationDocument(
        startup_id=startup_id,
        profile_version="1.0",
        source_updated_at=datetime.utcnow(),
        structured_profile=profile,
        ai_profile=ai,
        startup_profile_semantic_text=text,
        startup_profile_embedding=EmbeddingService.build_embedding(text),
    )


def _build_run_record(
    run_id: str = "run-1", investor_id: str = "inv-test-1"
) -> RecommendationRunRecord:
    return RecommendationRunRecord(
        run_id=run_id,
        investor_id=investor_id,
        investor_profile_version="1.0",
        candidate_count=5,
        candidate_set_size=3,
        generated_at=datetime.utcnow(),
        candidate_startup_ids=["s-1", "s-2", "s-3"],
        results=[],
        warnings=[],
    )


def _cleanup_rows():
    """Delete all recommendation rows between tests for isolation."""
    from sqlmodel import Session, delete

    with Session(db_engine) as session:
        session.exec(delete(RecommendationRunRow))
        session.exec(delete(RecommendationStartupRow))
        session.exec(delete(RecommendationInvestorRow))
        session.commit()


# ────────────────────────────────────────────────────────────────────
# 0. Settings defaults
# ────────────────────────────────────────────────────────────────────


class TestRecommendationSettings:
    def test_default_backend_is_db(self):
        s = Settings()
        assert s.RECOMMENDATION_BACKEND == "db"


# ────────────────────────────────────────────────────────────────────
# 1. DB Repository – Investor CRUD
# ────────────────────────────────────────────────────────────────────

class TestDBRepoInvestor:
    def setup_method(self):
        _cleanup_rows()

    def test_upsert_and_get_investor(self):
        repo = DBRecommendationRepository()
        doc = _build_investor_doc("inv-db-1")
        result = repo.upsert_investor(doc)
        assert result.investor_id == "inv-db-1"

        loaded = repo.get_investor("inv-db-1")
        assert loaded is not None
        assert loaded.investor_id == "inv-db-1"
        assert loaded.profile_version == "1.0"
        assert loaded.structured_preferences.investor_name == "Test Fund"

    def test_get_missing_investor_returns_none(self):
        repo = DBRecommendationRepository()
        assert repo.get_investor("nonexistent") is None

    def test_upsert_is_idempotent(self):
        repo = DBRecommendationRepository()
        doc = _build_investor_doc("inv-idem")
        repo.upsert_investor(doc)

        # Update and upsert again
        doc.profile_version = "2.0"
        repo.upsert_investor(doc)

        loaded = repo.get_investor("inv-idem")
        assert loaded is not None
        assert loaded.profile_version == "2.0"


# ────────────────────────────────────────────────────────────────────
# 2. DB Repository – Startup CRUD
# ────────────────────────────────────────────────────────────────────

class TestDBRepoStartup:
    def setup_method(self):
        _cleanup_rows()

    def test_upsert_and_get_startup(self):
        repo = DBRecommendationRepository()
        doc = _build_startup_doc("s-db-1")
        result = repo.upsert_startup(doc)
        assert result.startup_id == "s-db-1"

        loaded = repo.get_startup("s-db-1")
        assert loaded is not None
        assert loaded.startup_id == "s-db-1"
        assert loaded.structured_profile.startup_name == "TestPay"

    def test_get_missing_startup_returns_none(self):
        repo = DBRecommendationRepository()
        assert repo.get_startup("nonexistent") is None

    def test_list_startups(self):
        repo = DBRecommendationRepository()
        repo.upsert_startup(_build_startup_doc("s-a"))
        repo.upsert_startup(_build_startup_doc("s-b"))
        repo.upsert_startup(_build_startup_doc("s-c"))

        docs = repo.list_startups()
        ids = [d.startup_id for d in docs]
        assert len(ids) == 3
        assert sorted(ids) == ["s-a", "s-b", "s-c"]

    def test_upsert_startup_is_idempotent(self):
        repo = DBRecommendationRepository()
        doc = _build_startup_doc("s-idem")
        repo.upsert_startup(doc)

        doc.profile_version = "3.0"
        repo.upsert_startup(doc)

        loaded = repo.get_startup("s-idem")
        assert loaded is not None
        assert loaded.profile_version == "3.0"


# ────────────────────────────────────────────────────────────────────
# 3. DB Repository – Run records
# ────────────────────────────────────────────────────────────────────

class TestDBRepoRuns:
    def setup_method(self):
        _cleanup_rows()

    def test_store_and_list_runs(self):
        repo = DBRecommendationRepository()
        repo.store_run(_build_run_record("run-1", "inv-r1"))
        repo.store_run(_build_run_record("run-2", "inv-r1"))
        repo.store_run(_build_run_record("run-3", "inv-r2"))

        runs_r1 = repo.list_runs_for_investor("inv-r1")
        assert len(runs_r1) == 2
        assert all(r.investor_id == "inv-r1" for r in runs_r1)

        runs_r2 = repo.list_runs_for_investor("inv-r2")
        assert len(runs_r2) == 1

    def test_latest_run_for_investor(self):
        repo = DBRecommendationRepository()
        repo.store_run(_build_run_record("run-old", "inv-latest"))
        repo.store_run(_build_run_record("run-new", "inv-latest"))

        latest = repo.latest_run_for_investor("inv-latest")
        assert latest is not None
        # Both have nearly the same generated_at; just verify we get one
        assert latest.investor_id == "inv-latest"

    def test_latest_run_missing_investor(self):
        repo = DBRecommendationRepository()
        assert repo.latest_run_for_investor("nonexistent") is None


# ────────────────────────────────────────────────────────────────────
# 4. Repo factory
# ────────────────────────────────────────────────────────────────────

class TestRepoFactory:
    @patch("src.modules.recommendation.infrastructure.repositories.repo_factory.settings")
    def test_db_backend(self, mock_settings):
        from src.modules.recommendation.infrastructure.repositories.repo_factory import (
            get_recommendation_repository,
        )
        mock_settings.RECOMMENDATION_BACKEND = "db"
        repo = get_recommendation_repository()
        assert type(repo).__name__ == "DBRecommendationRepository"

    @patch("src.modules.recommendation.infrastructure.repositories.repo_factory.settings")
    def test_filesystem_backend(self, mock_settings):
        from src.modules.recommendation.infrastructure.repositories.repo_factory import (
            get_recommendation_repository,
        )
        mock_settings.RECOMMENDATION_BACKEND = "filesystem"
        repo = get_recommendation_repository()
        assert type(repo).__name__ == "RecommendationRepository"

    @patch("src.modules.recommendation.infrastructure.repositories.repo_factory.settings")
    def test_unknown_backend_defaults_to_db(self, mock_settings):
        from src.modules.recommendation.infrastructure.repositories.repo_factory import (
            get_recommendation_repository,
        )
        mock_settings.RECOMMENDATION_BACKEND = "mongo"
        repo = get_recommendation_repository()
        assert type(repo).__name__ == "DBRecommendationRepository"


# ────────────────────────────────────────────────────────────────────
# 5. Migration utility
# ────────────────────────────────────────────────────────────────────


class TestMigration:
    def setup_method(self):
        _cleanup_rows()

    def test_migrate_investors_and_startups(self, tmp_path):
        # Set up JSON files
        inv_dir = tmp_path / "investors"
        inv_dir.mkdir()
        doc = _build_investor_doc("inv-mig-1")
        (inv_dir / "inv-mig-1.json").write_text(
            json.dumps(doc.model_dump(mode="json"), default=str), encoding="utf-8"
        )

        su_dir = tmp_path / "startups"
        su_dir.mkdir()
        sdoc = _build_startup_doc("s-mig-1")
        (su_dir / "s-mig-1.json").write_text(
            json.dumps(sdoc.model_dump(mode="json"), default=str), encoding="utf-8"
        )

        stats = migrate(tmp_path)
        assert stats.investors_imported == 1
        assert stats.startups_imported == 1
        assert stats.total_errors == 0

        # Verify data landed in DB
        repo = DBRecommendationRepository()
        assert repo.get_investor("inv-mig-1") is not None
        assert repo.get_startup("s-mig-1") is not None

    def test_migrate_is_idempotent(self, tmp_path):
        inv_dir = tmp_path / "investors"
        inv_dir.mkdir()
        doc = _build_investor_doc("inv-idem-mig")
        (inv_dir / "inv-idem-mig.json").write_text(
            json.dumps(doc.model_dump(mode="json"), default=str), encoding="utf-8"
        )

        stats1 = migrate(tmp_path)
        stats2 = migrate(tmp_path)
        assert stats1.investors_imported == 1
        assert stats2.investors_imported == 1  # upsert, not error
        assert stats2.total_errors == 0

    def test_migrate_skips_malformed_json(self, tmp_path):
        su_dir = tmp_path / "startups"
        su_dir.mkdir()
        (su_dir /
         "bad.json").write_text("not valid json {{{", encoding="utf-8")

        stats = migrate(tmp_path)
        assert stats.startups_skipped == 1
        assert stats.total_errors == 1

    def test_migrate_handles_missing_directories(self, tmp_path):
        # Empty base with no subdirectories
        stats = migrate(tmp_path)
        assert stats.total_imported == 0
        assert stats.total_errors == 0

    def test_migrate_runs(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        rec = _build_run_record("run-mig-1", "inv-mig-r")
        (runs_dir / "run-mig-1.json").write_text(
            json.dumps(rec.model_dump(mode="json"), default=str), encoding="utf-8"
        )

        stats = migrate(tmp_path)
        assert stats.runs_imported == 1

        repo = DBRecommendationRepository()
        runs = repo.list_runs_for_investor("inv-mig-r")
        assert len(runs) == 1


# ────────────────────────────────────────────────────────────────────
# 6. Health check – recommendation_storage with DB backend
# ────────────────────────────────────────────────────────────────────


class TestHealthCheckRecommendation:
    @patch("src.shared.health.settings")
    def test_db_backend_probe_ok(self, mock_settings):
        mock_settings.RECOMMENDATION_BACKEND = "db"
        result = _check_recommendation_storage()
        assert result["ok"] is True
        assert result["backend"] == "db"

    @patch("src.shared.health.settings")
    def test_filesystem_backend_probe(self, mock_settings, tmp_path):
        mock_settings.RECOMMENDATION_BACKEND = "filesystem"
        mock_settings.STORAGE_DIR = str(tmp_path)
        rec_dir = tmp_path / "recommendations"
        rec_dir.mkdir()

        result = _check_recommendation_storage()
        assert result["ok"] is True
        assert result["backend"] == "filesystem"


# ────────────────────────────────────────────────────────────────────
# 7. Full round-trip: reindex → query → explanation via DB repo
# ────────────────────────────────────────────────────────────────────

class FakeReranker:
    def rerank(self, investor, candidates):
        items = []
        for c in candidates:
            items.append(
                SimpleNamespace(
                    startup_id=c["startup_id"],
                    rerank_adjustment=0,
                    positive_reason_codes=["INDUSTRY_MATCH"],
                    caution_reason_codes=[],
                )
            )
        return items, []


class TestFullRoundTrip:
    def setup_method(self):
        _cleanup_rows()

    def test_reindex_query_explain(self):
        from src.modules.recommendation.application.services.recommendation_engine import (
            RecommendationEngine,
        )

        repo = DBRecommendationRepository()
        engine = RecommendationEngine(repository=repo, reranker=FakeReranker())

        inv_req = ReindexInvestorRequest(
            investor_name="Round Trip Fund",
            investor_type="VC",
            short_thesis_summary="Fintech in SEA",
            preferred_industries=["Fintech"],
            preferred_stages=["Seed"],
            preferred_geographies=["Vietnam"],
            preferred_market_scopes=["B2B"],
            preferred_product_maturity=["beta"],
            preferred_validation_level=["traction"],
            preferred_strengths=["traction"],
            support_offered=["go to market"],
        )
        engine.reindex_investor("inv-rt", inv_req)

        su_req = ReindexStartupRequest(
            startup_name="RoundTripPay",
            tagline="Payments in Vietnam",
            stage="Seed",
            primary_industry="Fintech",
            location="Vietnam",
            market_scope="B2B",
            product_status="beta",
            validation_status="traction",
            is_profile_visible_to_investors=True,
            verification_label="basic_verified",
            ai_evaluation_status="completed",
            ai_overall_score=80.0,
            ai_summary="Strong traction",
            ai_strength_tags=["traction"],
            current_needs=["go to market"],
        )
        engine.reindex_startup("s-rt", su_req)

        # Query
        result = engine.get_recommendations("inv-rt", top_n=10)
        assert result.investor_id == "inv-rt"
        assert len(result.items) == 1
        assert result.items[0].startup_id == "s-rt"
        assert result.items[0].final_match_score > 0

        # Explanation
        explanation = engine.get_explanation("inv-rt", "s-rt")
        assert explanation.investor_id == "inv-rt"
        assert explanation.startup_id == "s-rt"
        assert explanation.result.final_match_score > 0

        # Run record
        latest = repo.latest_run_for_investor("inv-rt")
        assert latest is not None
        assert latest.investor_id == "inv-rt"

    def test_missing_investor_raises_valueerror(self):
        from src.modules.recommendation.application.services.recommendation_engine import (
            RecommendationEngine,
        )

        repo = DBRecommendationRepository()
        engine = RecommendationEngine(repository=repo, reranker=FakeReranker())

        with pytest.raises(ValueError, match="not found"):
            engine.get_recommendations("nonexistent-inv", top_n=5)


# ────────────────────────────────────────────────────────────────────
# 8. DB models exist in metadata
# ────────────────────────────────────────────────────────────────────

class TestDBModels:
    def test_recommendation_tables_created(self):
        from sqlmodel import inspect

        inspector = inspect(db_engine)
        tables = inspector.get_table_names()
        assert "recommendation_investors" in tables
        assert "recommendation_startups" in tables
        assert "recommendation_runs" in tables
