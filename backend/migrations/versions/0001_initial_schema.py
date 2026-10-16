"""Initial schema baseline with core intelligence entities, events, alerts, and settings

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-10-16 18:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Themes table
    op.create_table(
        "themes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(length=150), nullable=True),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("scoring_prompt", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("alert_threshold", sa.Integer(), server_default=sa.text("7"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="themes_pkey"),
    )
    op.create_index("ix_themes_name", "themes", ["name"], unique=True)
    op.create_index("ix_themes_slug", "themes", ["slug"], unique=True)
    op.create_index("ix_themes_is_active", "themes", ["is_active"], unique=False)

    # 2. Sources table
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("theme_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("polling_interval_sec", sa.Integer(), server_default=sa.text("300"), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["theme_id"], ["themes.id"], name="sources_theme_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="sources_pkey"),
    )
    op.create_index("ix_sources_theme_id", "sources", ["theme_id"], unique=False)
    op.create_index("ix_sources_source_type", "sources", ["source_type"], unique=False)
    op.create_index("ix_sources_is_enabled", "sources", ["is_enabled"], unique=False)

    # 3. Events table
    op.create_table(
        "events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("theme_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_name", sa.String(length=150), nullable=False),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=10), server_default=sa.text("'en'"), nullable=False),
        sa.Column("author", sa.String(length=150), nullable=True),
        sa.Column("location_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("importance_score", sa.SmallInteger(), nullable=True),
        sa.Column("score_rationale", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("headline", sa.String(length=500), nullable=True),
        sa.Column("entities_json", sa.JSON(), nullable=False),
        sa.Column("is_analyzed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], name="events_source_id_fkey", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["theme_id"], ["themes.id"], name="events_theme_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="events_pkey"),
        sa.UniqueConstraint("theme_id", "external_id", name="uq_events_theme_external_id"),
    )
    op.create_index("ix_events_theme_id", "events", ["theme_id"], unique=False)
    op.create_index("ix_events_source_id", "events", ["source_id"], unique=False)
    op.create_index("ix_events_source_type", "events", ["source_type"], unique=False)
    op.create_index("ix_events_external_id", "events", ["external_id"], unique=False)
    op.create_index("ix_events_importance_score", "events", ["importance_score"], unique=False)
    op.create_index("ix_events_is_analyzed", "events", ["is_analyzed"], unique=False)
    op.create_index("ix_events_published_at", "events", ["published_at"], unique=False)
    op.create_index("idx_events_theme_score", "events", ["theme_id", "importance_score"], unique=False)
    op.create_index("idx_events_theme_published", "events", ["theme_id", "published_at"], unique=False)

    # 4. Alerts table
    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("importance_score", sa.SmallInteger(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=50), server_default=sa.text("'telegram'"), nullable=False),
        sa.Column("status", sa.String(length=50), server_default=sa.text("'sent'"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="alerts_event_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="alerts_pkey"),
    )
    op.create_index("ix_alerts_event_id", "alerts", ["event_id"], unique=False)

    # 5. Event Embeddings table
    op.create_table(
        "event_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("embedding_vector", sa.JSON(), nullable=False),
        sa.Column("model_name", sa.String(length=100), server_default=sa.text("'gemini-embedding-2'"), nullable=False),
        sa.Column("x_coord", sa.Float(), nullable=True),
        sa.Column("y_coord", sa.Float(), nullable=True),
        sa.Column("trustworthiness_score", sa.SmallInteger(), nullable=True),
        sa.Column("corroboration_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=True),
        sa.Column("cluster_label", sa.String(length=150), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="event_embeddings_event_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="event_embeddings_pkey"),
    )
    op.create_index("ix_event_embeddings_event_id", "event_embeddings", ["event_id"], unique=True)
    op.create_index("ix_event_embeddings_trustworthiness_score", "event_embeddings", ["trustworthiness_score"], unique=False)
    op.create_index("ix_event_embeddings_cluster_id", "event_embeddings", ["cluster_id"], unique=False)

    # 6. System Settings table
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name="system_settings_pkey"),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_index("ix_event_embeddings_cluster_id", table_name="event_embeddings")
    op.drop_index("ix_event_embeddings_trustworthiness_score", table_name="event_embeddings")
    op.drop_index("ix_event_embeddings_event_id", table_name="event_embeddings")
    op.drop_table("event_embeddings")
    op.drop_index("ix_alerts_event_id", table_name="alerts")
    op.drop_table("alerts")
    op.drop_index("idx_events_theme_published", table_name="events")
    op.drop_index("idx_events_theme_score", table_name="events")
    op.drop_index("ix_events_published_at", table_name="events")
    op.drop_index("ix_events_is_analyzed", table_name="events")
    op.drop_index("ix_events_importance_score", table_name="events")
    op.drop_index("ix_events_external_id", table_name="events")
    op.drop_index("ix_events_source_type", table_name="events")
    op.drop_index("ix_events_source_id", table_name="events")
    op.drop_index("ix_events_theme_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_sources_is_enabled", table_name="sources")
    op.drop_index("ix_sources_source_type", table_name="sources")
    op.drop_index("ix_sources_theme_id", table_name="sources")
    op.drop_table("sources")
    op.drop_index("ix_themes_is_active", table_name="themes")
    op.drop_index("ix_themes_slug", table_name="themes")
    op.drop_index("ix_themes_name", table_name="themes")
    op.drop_table("themes")
