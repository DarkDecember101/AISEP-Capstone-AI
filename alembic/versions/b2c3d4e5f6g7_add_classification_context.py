"""add provided classification context columns

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6g7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('evaluation_runs', sa.Column(
        'provided_stage', sa.String(), nullable=True))
    op.add_column('evaluation_runs', sa.Column(
        'provided_main_industry', sa.String(), nullable=True))
    op.add_column('evaluation_runs', sa.Column(
        'provided_subindustry', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('evaluation_runs', 'provided_subindustry')
    op.drop_column('evaluation_runs', 'provided_main_industry')
    op.drop_column('evaluation_runs', 'provided_stage')
