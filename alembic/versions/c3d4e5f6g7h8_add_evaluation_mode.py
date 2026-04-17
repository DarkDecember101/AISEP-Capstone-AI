"""add evaluation_mode and merged_artifact_json to evaluation_runs

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-04-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3d4e5f6g7h8'
down_revision = 'b2c3d4e5f6g7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('evaluation_runs', sa.Column(
        'evaluation_mode', sa.String(), nullable=True))
    op.add_column('evaluation_runs', sa.Column(
        'merged_artifact_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('evaluation_runs', 'merged_artifact_json')
    op.drop_column('evaluation_runs', 'evaluation_mode')
