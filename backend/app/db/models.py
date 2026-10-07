import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    JSON,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Return timezone-aware current UTC datetime."""
    return datetime.now(timezone.utc)


def generate_uuid() -> str:
    """Generate string UUID."""
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Declarative base class for all SQLAlchemy models."""
    pass


class Theme(Base):
    """
    Theme model defining the intelligence monitoring focus.
    Enables zero-code dynamic switching between topics.
    """
    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[Optional[str]] = mapped_column(String(150), unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    keywords: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    entities: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    scoring_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    alert_threshold: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    # Relationships
    sources: Mapped[List["Source"]] = relationship(
        "Source", back_populates="theme", cascade="all, delete-orphan", lazy="selectin"
    )
    events: Mapped[List["Event"]] = relationship(
        "Event", back_populates="theme", cascade="all, delete-orphan", lazy="selectin"
    )

    def to_dict(self) -> Dict[str, Any]:
        slug_val = self.slug or (self.name.lower().replace(" ", "-") if self.name else None)
        return {
            "id": self.id,
            "slug": slug_val,
            "name": self.name,
            "description": self.description,
            "keywords": self.keywords or [],
            "entities": self.entities or [],
            "scoring_prompt": self.scoring_prompt,
            "is_active": self.is_active,
            "alert_threshold": self.alert_threshold,
            "importance_threshold": self.alert_threshold,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Source(Base):
    """
    Ingestion source configuration linked to a theme.
    Supports RSS, Telegram, NewsAPI, Reddit, and Maritime AIS plugins.
    """
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("themes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    config_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    polling_interval_sec: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    last_polled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    theme: Mapped["Theme"] = relationship("Theme", back_populates="sources", lazy="selectin")
    events: Mapped[List["Event"]] = relationship("Event", back_populates="source", lazy="selectin")

    def to_dict(self) -> Dict[str, Any]:
        target = None
        if isinstance(self.config_json, dict):
            target = self.config_json.get("target") or self.config_json.get("url")
        return {
            "id": self.id,
            "theme_id": self.theme_id,
            "name": self.name,
            "source_type": self.source_type,
            "target": target,
            "config_json": self.config_json or {},
            "enabled": self.is_enabled,
            "is_enabled": self.is_enabled,
            "interval_seconds": self.polling_interval_sec,
            "polling_interval_sec": self.polling_interval_sec,
            "status": "active" if self.is_enabled else "paused",
            "last_polled_at": self.last_polled_at.isoformat() if self.last_polled_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Event(Base):
    """
    Normalized intelligence event enriched by multi-stage AI processing.
    """
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    theme_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("themes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sources.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(150), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    
    # Text content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    author: Mapped[Optional[str]] = mapped_column(String(150), nullable=True)

    # Structured metadata
    location_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, default=None, nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    
    # AI Pipeline enrichment
    importance_score: Mapped[Optional[int]] = mapped_column(SmallInteger, nullable=True, index=True)
    score_rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    headline: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    entities_json: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    is_analyzed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Unique constraint per theme & external_id to prevent duplicate processing
    __table_args__ = (
        UniqueConstraint("theme_id", "external_id", name="uq_events_theme_external_id"),
        Index("idx_events_theme_score", "theme_id", "importance_score"),
        Index("idx_events_theme_published", "theme_id", "published_at"),
    )

    # Relationships
    theme: Mapped["Theme"] = relationship("Theme", back_populates="events", lazy="selectin")
    source: Mapped[Optional["Source"]] = relationship("Source", back_populates="events")
    alerts: Mapped[List["Alert"]] = relationship(
        "Alert", back_populates="event", cascade="all, delete-orphan", lazy="selectin"
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "theme_id": self.theme_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "source_url": getattr(self, "source_url", None),
            "external_id": self.external_id,
            "title": self.title,
            "raw_text": self.raw_text,
            "translated_text": self.translated_text,
            "language": self.language,
            "author": self.author,
            "location_json": self.location_json,
            "metadata_json": self.metadata_json or {},
            "importance_score": self.importance_score,
            "score_rationale": self.score_rationale,
            "summary": self.summary,
            "headline": self.headline,
            "entities_json": self.entities_json or [],
            "is_analyzed": getattr(self, "is_analyzed", self.importance_score is not None),
            "processed_at": self.processed_at.isoformat() if getattr(self, "processed_at", None) else None,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "ingested_at": self.ingested_at.isoformat() if self.ingested_at else None,
        }



class Alert(Base):
    """
    Alert notification dispatched for critical events exceeding threshold.
    """
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    importance_score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), default="telegram", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="sent", nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="alerts")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "event_id": self.event_id,
            "importance_score": self.importance_score,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "channel": self.channel,
            "status": self.status,
            "error_message": self.error_message,
        }


class SystemSettings(Base):
    """
    Global system settings table for AI pipeline model selection, credentials,
    and alert threshold parameters.
    """
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value_json": self.value_json,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
