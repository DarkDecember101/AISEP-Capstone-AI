"""
Database-backed recommendation repository (Phase 2B).

Drop-in replacement for the filesystem ``RecommendationRepository``.
Same public interface — every method that the engine calls exists here
with the same signature and return types.

Uses SQLModel sessions against the shared ``engine`` from ``db.py``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text
from sqlmodel import Session, select

from src.shared.persistence.db import engine as db_engine
from src.shared.persistence.models.recommendation_models import (
    RecommendationInvestorRow,
    RecommendationRunRow,
    RecommendationStartupRow,
)
from src.modules.recommendation.application.dto.recommendation_schema import (
    InvestorRecommendationDocument,
    RecommendationRunRecord,
    StartupRecommendationDocument,
)

logger = logging.getLogger("aisep.recommendation.db_repository")

# pgvector operators available only on Postgres
try:
    from pgvector.sqlalchemy import Vector  # noqa: F401
    _PGVECTOR = True
except ImportError:
    _PGVECTOR = False


class DBRecommendationRepository:
    """Postgres/SQLite-backed recommendation repository."""

    # ── Investor CRUD ───────────────────────────────────────────────

    def upsert_investor(
        self, document: InvestorRecommendationDocument
    ) -> InvestorRecommendationDocument:
        doc_json = json.dumps(
            document.model_dump(mode="json"), ensure_ascii=False, default=str
        )
        with Session(db_engine) as session:
            stmt = select(RecommendationInvestorRow).where(
                RecommendationInvestorRow.investor_id == document.investor_id
            )
            existing = session.exec(stmt).first()
            now = datetime.utcnow()
            if existing:
                existing.profile_version = document.profile_version
                existing.source_updated_at = document.source_updated_at
                existing.document_json = doc_json
                existing.updated_at = now
                existing.investor_embedding = document.investor_semantic_embedding
                session.add(existing)
            else:
                row = RecommendationInvestorRow(
                    investor_id=document.investor_id,
                    profile_version=document.profile_version,
                    source_updated_at=document.source_updated_at,
                    document_json=doc_json,
                    investor_embedding=document.investor_semantic_embedding,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            session.commit()
        return document

    def get_investor(
        self, investor_id: str
    ) -> Optional[InvestorRecommendationDocument]:
        with Session(db_engine) as session:
            stmt = select(RecommendationInvestorRow).where(
                RecommendationInvestorRow.investor_id == investor_id
            )
            row = session.exec(stmt).first()
            if row is None:
                return None
            return self._parse_investor(row)

    # ── Startup CRUD ────────────────────────────────────────────────

    def upsert_startup(
        self, document: StartupRecommendationDocument
    ) -> StartupRecommendationDocument:
        doc_json = json.dumps(
            document.model_dump(mode="json"), ensure_ascii=False, default=str
        )
        with Session(db_engine) as session:
            stmt = select(RecommendationStartupRow).where(
                RecommendationStartupRow.startup_id == document.startup_id
            )
            existing = session.exec(stmt).first()
            now = datetime.utcnow()
            if existing:
                existing.profile_version = document.profile_version
                existing.source_updated_at = document.source_updated_at
                existing.startup_name = document.structured_profile.startup_name
                existing.primary_industry = document.structured_profile.primary_industry
                existing.stage = document.structured_profile.stage
                existing.location = document.structured_profile.location
                existing.document_json = doc_json
                existing.startup_profile_embedding = document.startup_profile_embedding
                existing.startup_ai_embedding = document.startup_ai_embedding
                existing.updated_at = now
                session.add(existing)
            else:
                row = RecommendationStartupRow(
                    startup_id=document.startup_id,
                    profile_version=document.profile_version,
                    source_updated_at=document.source_updated_at,
                    startup_name=document.structured_profile.startup_name,
                    primary_industry=document.structured_profile.primary_industry,
                    stage=document.structured_profile.stage,
                    location=document.structured_profile.location,
                    document_json=doc_json,
                    startup_profile_embedding=document.startup_profile_embedding,
                    startup_ai_embedding=document.startup_ai_embedding,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            session.commit()
        return document

    def get_startup(
        self, startup_id: str
    ) -> Optional[StartupRecommendationDocument]:
        with Session(db_engine) as session:
            stmt = select(RecommendationStartupRow).where(
                RecommendationStartupRow.startup_id == startup_id
            )
            row = session.exec(stmt).first()
            if row is None:
                return None
            return self._parse_startup(row)

    def list_startups(self) -> List[StartupRecommendationDocument]:
        with Session(db_engine) as session:
            stmt = select(RecommendationStartupRow).order_by(
                RecommendationStartupRow.startup_id
            )
            rows = session.exec(stmt).all()
            documents: List[StartupRecommendationDocument] = []
            for row in rows:
                doc = self._parse_startup(row)
                if doc is not None:
                    documents.append(doc)
            return documents

    def list_startups_nearest(
        self,
        investor_embedding: List[float],
        top_n: int = 50,
    ) -> List[StartupRecommendationDocument]:
        """Return up to *top_n* startups ordered by cosine distance to
        *investor_embedding* using pgvector's ``<=>`` operator.

        Falls back to ``list_startups()`` when pgvector is not available
        (e.g. SQLite in unit tests).
        """
        if not _PGVECTOR or not investor_embedding:
            return self.list_startups()

        vec_literal = "[" + ",".join(str(v) for v in investor_embedding) + "]"
        sql = text(
            "SELECT startup_id FROM recommendation_startups "
            "WHERE startup_profile_embedding IS NOT NULL "
            f"ORDER BY startup_profile_embedding <=> :vec \n"
            "LIMIT :top_n"
        )
        with Session(db_engine) as session:
            result = session.execute(sql, {"vec": vec_literal, "top_n": top_n})
            ordered_ids = [row.startup_id for row in result]

        if not ordered_ids:
            return []

        with Session(db_engine) as session:
            stmt = select(RecommendationStartupRow).where(
                RecommendationStartupRow.startup_id.in_(
                    ordered_ids)  # type: ignore[union-attr]
            )
            rows = {r.startup_id: r for r in session.exec(stmt).all()}

        docs: List[StartupRecommendationDocument] = []
        for sid in ordered_ids:  # preserve distance order
            row = rows.get(sid)
            if row:
                doc = self._parse_startup(row)
                if doc:
                    docs.append(doc)
        return docs

    # ── Run records ─────────────────────────────────────────────────

    def store_run(
        self, record: RecommendationRunRecord
    ) -> RecommendationRunRecord:
        doc_json = json.dumps(
            record.model_dump(mode="json"), ensure_ascii=False, default=str
        )
        with Session(db_engine) as session:
            row = RecommendationRunRow(
                run_id=record.run_id,
                investor_id=record.investor_id,
                investor_profile_version=record.investor_profile_version,
                candidate_count=record.candidate_count,
                candidate_set_size=record.candidate_set_size,
                generated_at=record.generated_at,
                document_json=doc_json,
            )
            session.add(row)
            session.commit()
        return record

    def list_runs_for_investor(
        self, investor_id: str
    ) -> List[RecommendationRunRecord]:
        with Session(db_engine) as session:
            stmt = (
                select(RecommendationRunRow)
                .where(RecommendationRunRow.investor_id == investor_id)
                .order_by(RecommendationRunRow.generated_at)
            )
            rows = session.exec(stmt).all()
            records: List[RecommendationRunRecord] = []
            for row in rows:
                rec = self._parse_run(row)
                if rec is not None:
                    records.append(rec)
            return records

    def latest_run_for_investor(
        self, investor_id: str
    ) -> Optional[RecommendationRunRecord]:
        with Session(db_engine) as session:
            stmt = (
                select(RecommendationRunRow)
                .where(RecommendationRunRow.investor_id == investor_id)
                # type: ignore[union-attr]
                .order_by(RecommendationRunRow.generated_at.desc())
                .limit(1)
            )
            row = session.exec(stmt).first()
            if row is None:
                return None
            return self._parse_run(row)

    # ── Parsing helpers ─────────────────────────────────────────────

    @staticmethod
    def _parse_investor(
        row: RecommendationInvestorRow,
    ) -> Optional[InvestorRecommendationDocument]:
        try:
            return InvestorRecommendationDocument.model_validate_json(
                row.document_json
            )
        except Exception as exc:
            logger.warning(
                "Failed to parse investor doc investor_id=%s: %s",
                row.investor_id,
                exc,
            )
            return None

    @staticmethod
    def _parse_startup(
        row: RecommendationStartupRow,
    ) -> Optional[StartupRecommendationDocument]:
        try:
            return StartupRecommendationDocument.model_validate_json(
                row.document_json
            )
        except Exception as exc:
            logger.warning(
                "Failed to parse startup doc startup_id=%s: %s",
                row.startup_id,
                exc,
            )
            return None

    @staticmethod
    def _parse_run(
        row: RecommendationRunRow,
    ) -> Optional[RecommendationRunRecord]:
        try:
            return RecommendationRunRecord.model_validate_json(
                row.document_json
            )
        except Exception as exc:
            logger.warning(
                "Failed to parse run record run_id=%s: %s", row.run_id, exc
            )
            return None
