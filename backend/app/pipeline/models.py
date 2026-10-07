"""
backend/app/pipeline/models.py - Pydantic Data Models for Multi-Stage AI Pipeline
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


class AIStageConfig(BaseModel):
    """
    Configuration model for an individual pipeline stage.
    Supports per-stage model provider (Gemini / Groq), model selection,
    temperature, and API key override.
    """
    stage_id: Literal["extraction", "scoring", "summarization", "visuals"]
    provider: Literal["gemini", "groq"] = "gemini"
    model_name: str = "gemini-2.0-flash"
    api_key_override: Optional[str] = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    enabled: bool = True

    @field_validator("stage_id", mode="before")
    @classmethod
    def normalize_stage_id(cls, v: Any) -> str:
        s = str(v).lower().strip()
        if s in ("extraction_translation", "extraction"):
            return "extraction"
        if s in ("importance_scoring", "scoring"):
            return "scoring"
        if s in ("visual_generation", "visuals"):
            return "visuals"
        return s

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, v: Any) -> str:
        s = str(v).lower().strip()
        if s in ("gemini", "google"):
            return "gemini"
        if s in ("groq",):
            return "groq"
        return s

    @field_validator("model_name", mode="before")
    @classmethod
    def normalize_model_name(cls, v: Any) -> str:
        return str(v).strip() if v else "gemini-2.0-flash"


class Stage1Result(BaseModel):
    """
    Output contract for Stage 1: Extraction & Translation.
    Handles ISO language detection, Farsi/Arabic translation with acronym preservation,
    and structured entity extraction across 5 categories.
    """
    extracted_entities: List[str] = Field(
        default_factory=list,
        description="Flat deduplicated list of named entities across all categories.",
    )
    detected_language: str = Field(
        default="en",
        description="Detected ISO 639-1 language code ('fa', 'ar', 'en', etc.).",
    )
    translated_text: str = Field(
        description="Fluent English intelligence translation. Original text if already English.",
    )
    key_facts: List[str] = Field(
        default_factory=list,
        description="2 to 5 atomic factual statements extracted from the intelligence text.",
    )
    entity_breakdown: Optional[Dict[str, List[str]]] = Field(
        default_factory=lambda: {
            "people": [],
            "organizations": [],
            "locations": [],
            "military_assets": [],
            "geopolitical_events": [],
        },
        description="Categorized entity taxonomy.",
    )

    @field_validator("detected_language", mode="before")
    @classmethod
    def normalize_lang(cls, v: Any) -> str:
        s = str(v).lower().strip() if v else "en"
        if s in ("persian", "farsi", "fas", "per"):
            return "fa"
        if s in ("arabic", "ara"):
            return "ar"
        if s in ("english", "eng"):
            return "en"
        return s[:5] if s else "en"

    @field_validator("extracted_entities", mode="before")
    @classmethod
    def clean_entities(cls, v: Any) -> List[str]:
        if isinstance(v, list):
            seen = set()
            out = []
            for item in v:
                clean = str(item).strip()
                if clean and clean.lower() not in seen:
                    seen.add(clean.lower())
                    out.append(clean)
            return out
        return []


class Stage2Result(BaseModel):
    """
    Output contract for Stage 2: Importance Scoring.
    Evaluates tactical/strategic significance on a 1-10 clamped scale,
    driven by active Theme scoring prompt and alert threshold.
    """
    importance_score: int = Field(
        ge=1, le=10,
        description="Integer score from 1 to 10 evaluating tactical/strategic significance.",
    )
    score_rationale: str = Field(
        description="1-2 sentence concise justification for assigned score.",
    )
    priority_level: Literal["low", "medium", "high", "critical"] = Field(
        description="Classification: low (1-3), medium (4-6), high (7-8), critical (9-10).",
    )
    should_alert: bool = Field(
        description="True if importance_score >= theme alert threshold.",
    )
    matched_keywords: List[str] = Field(
        default_factory=list,
        description="Tracked theme keywords matched in the event text.",
    )
    matched_entities: List[str] = Field(
        default_factory=list,
        description="Tracked theme entities matched in the event text.",
    )

    @field_validator("importance_score", mode="before")
    @classmethod
    def clamp_score(cls, v: Any) -> int:
        try:
            val = round(float(v))
            return max(1, min(10, val))
        except (ValueError, TypeError):
            return 5

    @field_validator("priority_level", mode="before")
    @classmethod
    def derive_priority(cls, v: Any) -> str:
        val = str(v).lower().strip() if v else ""
        if val in ("low", "medium", "high", "critical"):
            return val
        return "medium"


class Stage3Result(BaseModel):
    """Output contract for Stage 3: Single Event Summarization."""
    summary: str = Field(
        description="Concise 2-3 sentence executive intelligence summary (BLUF).",
    )
    headline: str = Field(
        description="Punchy, informative headline under 80 characters.",
    )


class DigestEventItem(BaseModel):
    """Individual event item inside periodic intelligence digest."""
    event_id: str
    headline: str
    importance_score: int
    source_name: str
    bullet_summary: str


class PeriodicDigestResult(BaseModel):
    """Output contract for Stage 3: Periodic Intelligence Digest."""
    theme_name: str
    time_window_start: datetime
    time_window_end: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_events_analyzed: int
    critical_alerts_count: int
    threat_trajectory: Literal["Escalating", "Elevated", "Stable", "De-escalating", "Volatile"]
    executive_overview: str = Field(
        description="Executive paragraph summarizing overall tactical and geopolitical posture.",
    )
    key_developments: List[DigestEventItem] = Field(default_factory=list)
    watchlist_indicators: List[str] = Field(default_factory=list)


class Stage4Result(BaseModel):
    """Output contract for Stage 4: Single Event Visual & Trend Tagging."""
    trend_category: str = Field(
        default="Geopolitical Development",
        description="Operational category (e.g. 'Naval Escalation', 'Air & Missile Defense').",
    )
    cluster_tags: List[str] = Field(
        default_factory=list,
        description="2 to 5 thematic clustering tags for UI badges and search filters.",
    )
    narrative_insight: str = Field(
        default="",
        description="Analytical sentence contextualizing this event within wider trends.",
    )


class EntityTrendMetric(BaseModel):
    entity: str
    category: str
    count: int
    velocity: Literal["rising", "stable", "falling"]


class VisualChartMarker(BaseModel):
    label: str
    marker_type: Literal["peak_alert", "anomaly_spike", "shift_in_tempo", "milestone"]
    timestamp: Optional[datetime] = None
    event_id: Optional[str] = None
    description: str


class DashboardTrendSynthesis(BaseModel):
    """Output contract for Stage 4: Aggregate Dashboard Visuals & Trends."""
    theme_name: str
    period_analyzed: str
    trend_narrative: str = Field(
        description="Multi-paragraph narrative synthesis accompanying dashboard charts.",
    )
    key_trend_bullets: List[str] = Field(default_factory=list)
    top_categories: List[Dict[str, Any]] = Field(default_factory=list)
    trending_entities: List[EntityTrendMetric] = Field(default_factory=list)
    chart_markers: List[VisualChartMarker] = Field(default_factory=list)


class AnalyzedEvent(BaseModel):
    """
    Enriched canonical event model representing the complete output of the AI pipeline.
    Compatible with NormalizedEvent schemas and test assertions.
    """
    id: str
    theme_id: Union[str, int]
    source_type: str
    source_name: str
    source_url: Optional[str] = None
    external_id: str
    title: str
    raw_text: str
    language: str = "en"
    author: Optional[str] = None
    location: Optional[Dict[str, Any]] = None
    location_json: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    published_at: datetime
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Pipeline enrichments
    translated_text: Optional[str] = None
    importance_score: int = Field(default=5, ge=1, le=10)
    score_rationale: str = "Standard event assessment"
    summary: str = ""
    headline: str = ""
    entities: List[str] = Field(default_factory=list)
    trend_category: Optional[str] = None
    notification_dispatched: bool = False
    notification_dispatched_at: Optional[Union[datetime, str]] = None
    is_analyzed: bool = True
    processed_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context: Any) -> None:
        if self.location and not self.location_json:
            self.location_json = self.location
        elif self.location_json and not self.location:
            self.location = self.location_json

        if self.metadata and not self.metadata_json:
            self.metadata_json = self.metadata
        elif self.metadata_json and not self.metadata:
            self.metadata = self.metadata_json
