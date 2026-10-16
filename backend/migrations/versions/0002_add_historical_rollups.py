"""Add historical_rollups table for tiered memory and aged event compaction

Revision ID: 0002_add_historical_rollups
Revises: 0001_initial_schema
Create Date: 2026-10-17 01:03:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_add_historical_rollups"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    table_names = insp.get_table_names()

    if "historical_rollups" not in table_names:
        op.create_table(
            "historical_rollups",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("theme_id", sa.Integer(), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("epoch_key", sa.String(length=64), nullable=False),
            sa.Column("total_events", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("critical_events_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("max_importance_score", sa.SmallInteger(), server_default=sa.text("5"), nullable=False),
            sa.Column("avg_importance_score", sa.Float(), server_default=sa.text("5.0"), nullable=False),
            sa.Column("executive_summary", sa.Text(), nullable=False),
            sa.Column("storylines_json", sa.JSON(), nullable=False),
            sa.Column("entity_counts_json", sa.JSON(), nullable=False),
            sa.Column("location_counts_json", sa.JSON(), nullable=False),
            sa.Column("category_counts_json", sa.JSON(), nullable=False),
            sa.Column("source_stats_json", sa.JSON(), nullable=False),
            sa.Column("notable_events_json", sa.JSON(), nullable=False),
            sa.Column("embedding_vector", sa.JSON(), nullable=True),
            sa.Column("embedding_model_name", sa.String(length=100), server_default=sa.text("'gemini-embedding-2'"), nullable=False),
            sa.Column("is_compacted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["theme_id"], ["themes.id"], name="historical_rollups_theme_id_fkey", ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id", name="historical_rollups_pkey"),
            sa.UniqueConstraint("theme_id", "epoch_key", name="uq_historical_rollups_theme_epoch"),
        )
        op.create_index("ix_historical_rollups_theme_id", "historical_rollups", ["theme_id"], unique=False)
        op.create_index("ix_historical_rollups_period_start", "historical_rollups", ["period_start"], unique=False)
        op.create_index("ix_historical_rollups_period_end", "historical_rollups", ["period_end"], unique=False)
        op.create_index("ix_historical_rollups_epoch_key", "historical_rollups", ["epoch_key"], unique=False)
        op.create_index("idx_rollups_theme_period", "historical_rollups", ["theme_id", "period_start"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    table_names = insp.get_table_names()

    if "historical_rollups" in table_names:
        op.drop_index("idx_rollups_theme_period", table_name="historical_rollups")
        op.drop_index("ix_historical_rollups_epoch_key", table_name="historical_rollups")
        op.drop_index("ix_historical_rollups_period_end", table_name="historical_rollups")
        op.drop_index("ix_historical_rollups_period_start", table_name="historical_rollups")
        op.drop_index("ix_historical_rollups_theme_id", table_name="historical_rollups")
        op.drop_table("historical_rollups")
