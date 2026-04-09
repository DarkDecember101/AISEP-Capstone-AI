"""
SQLModel persistence models for the Recommendation module (Phase 2B).

Three tables:
  * ``recommendation_investors`` – investor preference documents
  * ``recommendation_startups``  – startup recommendation documents
  * ``recommendation_runs``      – scored-run history per investor

All rich nested data (structured preferences, AI profiles, embeddings,
source payloads, results) is stored as JSON columns.  This is a pragmatic
choice: the data is opaque to SQL queries, only ever loaded/written as a
whole document, and keeping it in JSON avoids an explosion of join tables
while remaining fully queryable via Python after load.

The tables carry indexed columns for the keys used in look-ups:
``entity_id``  (investor_id / startup_id), ``investor_id`` on runs, and
``generated_at`` for history ordering.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import Column as SAColumn
from sqlmodel import Column, Field, SQLModel, String, Text

try:
    from pgvector.sqlalchemy import Vector as PGVector
    _PGVECTOR_AVAILABLE = True
except ImportError:  # fallback: store as JSON text (SQLite dev)
    _PGVECTOR_AVAILABLE = False
    PGVector = None  # type: ignore

EMBEDDING_DIM = 64


class RecommendationInvestorRow(SQLModel, table=True):
    """One row per investor recommendation document (upsert by investor_id)."""

    __tablename__ = "recommendation_investors"

    id: Optional[int] = Field(default=None, primary_key=True)
    investor_id: str = Field(
        sa_column=Column(String(128), unique=True, nullable=False, index=True)
    )
    profile_version: str = Field(default="1.0")
    source_updated_at: datetime = Field(default_factory=datetime.utcnow)
    # Full InvestorRecommendationDocument serialised as JSON
    document_json: str = Field(sa_column=Column(Text, nullable=False))
    # Native pgvector column for semantic embedding (nullable during migration)
    investor_embedding: Optional[List[float]] = Field(
        default=None,
        sa_column=SAColumn(PGVector(EMBEDDING_DIM), nullable=True)
        if _PGVECTOR_AVAILABLE
        else SAColumn(Text, nullable=True),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RecommendationStartupRow(SQLModel, table=True):
    """One row per startup recommendation document (upsert by startup_id)."""

    __tablename__ = "recommendation_startups"

    id: Optional[int] = Field(default=None, primary_key=True)
    startup_id: str = Field(
        sa_column=Column(String(128), unique=True, nullable=False, index=True)
    )
    profile_version: str = Field(default="1.0")
    source_updated_at: datetime = Field(default_factory=datetime.utcnow)
    startup_name: str = Field(default="")
    primary_industry: str = Field(default="")
    stage: str = Field(default="")
    location: str = Field(default="")
    # Full StartupRecommendationDocument serialised as JSON
    document_json: str = Field(sa_column=Column(Text, nullable=False))
    # Native pgvector columns for profile and AI embeddings
    startup_profile_embedding: Optional[List[float]] = Field(
        default=None,
        sa_column=SAColumn(PGVector(EMBEDDING_DIM), nullable=True)
        if _PGVECTOR_AVAILABLE
        else SAColumn(Text, nullable=True),
    )
    startup_ai_embedding: Optional[List[float]] = Field(
        default=None,
        sa_column=SAColumn(PGVector(EMBEDDING_DIM), nullable=True)
        if _PGVECTOR_AVAILABLE
        else SAColumn(Text, nullable=True),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RecommendationRunRow(SQLModel, table=True):
    """One row per recommendation scoring run."""

    __tablename__ = "recommendation_runs"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(
        sa_column=Column(String(128), unique=True, nullable=False, index=True)
    )
    investor_id: str = Field(
        sa_column=Column(String(128), nullable=False, index=True)
    )
    investor_profile_version: str = Field(default="1.0")
    candidate_count: int = Field(default=0)
    candidate_set_size: int = Field(default=0)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    # Full RecommendationRunRecord serialised as JSON
    document_json: str = Field(sa_column=Column(Text, nullable=False))
