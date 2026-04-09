"""add pgvector embedding columns to recommendation tables

Revision ID: a1b2c3d4e5f6
Revises: 54bb95643a8c
Create Date: 2026-04-08 16:00:00.000000

Adds:
  - ``vector`` extension (CREATE EXTENSION IF NOT EXISTS vector)
  - ``investor_embedding``       vector(64) on recommendation_investors
  - ``startup_profile_embedding`` vector(64) on recommendation_startups
  - ``startup_ai_embedding``      vector(64) on recommendation_startups
  - ivfflat indexes on the three columns for fast ANN search
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "54bb95643a8c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 64


def upgrade() -> None:
    # 1. Enable pgvector extension (idempotent — requires superuser, run once manually
    #    or via: psql -U postgres -d aisep_ai -c "CREATE EXTENSION IF NOT EXISTS vector;")
    #    We skip it here so aisep_user (non-superuser) can run migrations freely.

    # 2. Add native vector columns
    op.add_column(
        "recommendation_investors",
        sa.Column("investor_embedding", sa.Text(), nullable=True),
    )
    op.add_column(
        "recommendation_startups",
        sa.Column("startup_profile_embedding", sa.Text(), nullable=True),
    )
    op.add_column(
        "recommendation_startups",
        sa.Column("startup_ai_embedding", sa.Text(), nullable=True),
    )

    # 3. Convert Text columns to vector(dim) using ALTER COLUMN
    #    (cannot use pgvector type class directly in op.add_column with alembic+psycopg3
    #    so we add as Text then ALTER to vector)
    op.execute(
        f"ALTER TABLE recommendation_investors "
        f"ALTER COLUMN investor_embedding TYPE vector({EMBEDDING_DIM}) "
        f"USING investor_embedding::vector({EMBEDDING_DIM});"
    )
    op.execute(
        f"ALTER TABLE recommendation_startups "
        f"ALTER COLUMN startup_profile_embedding TYPE vector({EMBEDDING_DIM}) "
        f"USING startup_profile_embedding::vector({EMBEDDING_DIM});"
    )
    op.execute(
        f"ALTER TABLE recommendation_startups "
        f"ALTER COLUMN startup_ai_embedding TYPE vector({EMBEDDING_DIM}) "
        f"USING startup_ai_embedding::vector({EMBEDDING_DIM});"
    )

    # 4. ivfflat indexes for approximate nearest-neighbor search
    #    (lists=50 suitable for up to ~50k rows; tune upward as dataset grows)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rec_investors_embedding "
        "ON recommendation_investors USING ivfflat (investor_embedding vector_cosine_ops) "
        "WITH (lists = 50);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rec_startups_profile_embedding "
        "ON recommendation_startups USING ivfflat (startup_profile_embedding vector_cosine_ops) "
        "WITH (lists = 50);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rec_startups_ai_embedding "
        "ON recommendation_startups USING ivfflat (startup_ai_embedding vector_cosine_ops) "
        "WITH (lists = 50);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rec_startups_ai_embedding;")
    op.execute("DROP INDEX IF EXISTS ix_rec_startups_profile_embedding;")
    op.execute("DROP INDEX IF EXISTS ix_rec_investors_embedding;")
    op.drop_column("recommendation_startups", "startup_ai_embedding")
    op.drop_column("recommendation_startups", "startup_profile_embedding")
    op.drop_column("recommendation_investors", "investor_embedding")
    # Note: we intentionally keep the vector extension installed on downgrade
