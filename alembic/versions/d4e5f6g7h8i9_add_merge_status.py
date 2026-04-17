"""add merge_status to evaluation_runs

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8
Create Date: 2026-04-17 14:00:00.000000

merge_status values:
  not_applicable     — run is pitch_deck_only or business_plan_only
  waiting_for_sources — in-flight docs detected; aggregate deferred
  fallback_source_only — combined run, only one source doc succeeded
  merged             — both sources merged; merged_artifact_json populated
  merge_failed       — merge attempted but threw exception; fallback used
  merge_disabled     — MERGE_EVAL_ENABLED=False; not attempted
  NULL               — legacy rows created before this migration
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4e5f6g7h8i9'
down_revision = 'c3d4e5f6g7h8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'evaluation_runs',
        sa.Column('merge_status', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('evaluation_runs', 'merge_status')
