"""
backend/app/pipeline/models.py - Pydantic Data Models for Multi-Stage AI Pipeline
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator


class AIFallbackConfig(BaseModel):
    """
    Configuration model for a fallback AI model and provider in a stage's fallback chain.
    """
    provider: Literal["gemini", "groq"] = "gemini"
    model_name: str = "gemini-3.1-flash-lite"
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    api_key_override: Optional[str] = None

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
        return str(v).strip() if v else "gemini-3.1-flash-lite"


class AIStageConfig(BaseModel):
    """
    Configuration model for an individual pipeline stage.
    Supports per-stage model provider (Gemini / Groq), model selection,
    temperature, API key override, and an ordered chain of fallbacks.
    """
    stage_id: Literal["extraction", "scoring", "summarization", "visuals"]
    provider: Literal["gemini", "groq"] = "gemini"
    model_name: str = "gemini-3.5-flash-lite"
    api_key_override: Optional[str] = None
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    enabled: bool = True
    fallbacks: List[AIFallbackConfig] = Field(default_factory=list)

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
        return str(v).strip() if v else "gemini-3.5-flash-lite"

    @field_validator("fallbacks", mode="before")
    @classmethod
    def normalize_fallbacks(cls, v: Any) -> List[Any]:
        if not v:
            return []
        if isinstance(v, list):
            norm = []
            for item in v:
                if isinstance(item, dict):
                    c = dict(item)
                    if "model" in c and "model_name" not in c:
                        c["model_name"] = c["model"]
                    norm.append(c)
                else:
                    norm.append(item)
            return norm
        return []


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
    translated_title: Optional[str] = Field(
        default=None,
        description="Fluent English translation of the event title if original is non-English.",
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

    @model_validator(mode="before")
    @classmethod
    def remap_flexible_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Map translated_text from aliases
            if not data.get("translated_text"):
                for candidate in ("translated_content", "translation", "english_translation", "content", "translated_body"):
                    if data.get(candidate):
                        data["translated_text"] = data[candidate]
                        break
            # Map detected_language from aliases
            if not data.get("detected_language"):
                for candidate in ("source_language", "language", "lang", "detected_lang"):
                    if data.get(candidate):
                        data["detected_language"] = data[candidate]
                        break
            # Map translated_title from aliases
            if not data.get("translated_title"):
                for candidate in ("title_translation", "english_title", "translated_heading", "headline"):
                    if data.get(candidate):
                        data["translated_title"] = data[candidate]
                        break
        return data

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


class RelatedReportContext(BaseModel):
    """Summary of a semantically similar recent report retrieved from EventEmbedding space."""
    event_id: str
    headline: str
    source_name: str
    source_type: str = "rss"
    importance_score: int = 5
    similarity: float = 0.0
    published_at: Optional[str] = None


class ScoringContext(BaseModel):
    """
    Pre-Stage 2 Situational & Semantic Embedding Context snapshot.
    Provides Stage 2 with vector neighborhood matches, cross-source corroboration,
    same-source echo detection, frontline signal triangulation, source reliability tier,
    and general 6h/24h theater reporting velocity.
    """
    related_reports: List[RelatedReportContext] = Field(default_factory=list)
    corroboration_count: int = 0
    corroborating_sources: List[str] = Field(default_factory=list)
    corroborating_event_ids: List[str] = Field(default_factory=list)
    same_source_echo_count: int = 0
    is_same_source_echo: bool = False
    max_similarity: float = 0.0
    corroboration_status: str = "First Report / Unverified"
    source_reliability_tier: str = "tier_2_regional_osint"
    source_reliability_label: str = "Standard OSINT Feed"
    kinetic_signal: Dict[str, Any] = Field(default_factory=dict)
    velocity_status: str = "Normal"
    events_last_6h: int = 0
    critical_alerts_last_6h: int = 0
    avg_score_24h: float = 5.0
    active_storylines: List[str] = Field(default_factory=list)
    pizza_meter_status: Optional[str] = None
    embedding_vector: Optional[List[float]] = None
    embedding_model_name: Optional[str] = None


class Stage2Result(BaseModel):
    """
    Output contract for Stage 2: Importance Scoring.
    Evaluates tactical/strategic significance on a 1-10 clamped scale,
    driven by active Theme scoring prompt, general theater context,
    multi-dimensional sub-scores, and vector embedding corroboration.
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
    # Multi-dimensional sub-scores & embedding calibration metadata
    severity_score: Optional[int] = Field(
        default=None, ge=1, le=10,
        description="Intrinsic tactical/strategic severity of the event (1-10).",
    )
    novelty_score: Optional[int] = Field(
        default=None, ge=1, le=10,
        description="Escalation novelty vs. repetitive echo or status quo (1-10).",
    )
    verification_confidence: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Confidence classification based on source reliability, signal type, and corroboration.",
    )
    confidence_score: Optional[int] = Field(
        default=None, ge=1, le=10,
        description="Numeric verification confidence score (1-10).",
    )
    corroboration_status: Optional[str] = Field(
        default=None,
        description="Embedding corroboration classification label.",
    )
    corroborating_sources: List[str] = Field(
        default_factory=list,
        description="Independent sources confirming this event in embedding space.",
    )
    related_event_ids: List[str] = Field(
        default_factory=list,
        description="Event IDs of semantically matched recent reports.",
    )
    calibration_notes: List[str] = Field(
        default_factory=list,
        description="Deterministic calibration adjustments applied to the score.",
    )

    @field_validator("importance_score", mode="before")
    @classmethod
    def clamp_score(cls, v: Any) -> int:
        try:
            val = round(float(v))
            return max(1, min(10, val))
        except (ValueError, TypeError):
            return 5

    @field_validator("severity_score", "novelty_score", "confidence_score", mode="before")
    @classmethod
    def clamp_optional_subscore(cls, v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            val = round(float(v))
            return max(1, min(10, val))
        except (ValueError, TypeError):
            return None

    @field_validator("priority_level", mode="before")
    @classmethod
    def derive_priority(cls, v: Any) -> str:
        val = str(v).lower().strip() if v else ""
        if val in ("low", "medium", "high", "critical"):
            return val
        return "medium"

    @field_validator("verification_confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, v: Any) -> str:
        val = str(v).lower().strip() if v else "medium"
        if val in ("low", "medium", "high"):
            return val
        if "high" in val or "verif" in val or "confirm" in val:
            return "high"
        if "low" in val or "unverif" in val or "rumor" in val:
            return "low"
        return "medium"

    def to_breakdown_dict(self, scoring_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Serialize structured multi-dimensional scoring breakdown for DB persistence and UI."""
        ctx = scoring_context or {}
        kin = ctx.get("kinetic_signal") or {}
        sev = self.severity_score if self.severity_score is not None else self.importance_score
        nov = self.novelty_score if self.novelty_score is not None else (
            3 if ctx.get("is_same_source_echo") else 7
        )
        conf_num = self.confidence_score if self.confidence_score is not None else (
            9 if self.verification_confidence == "high"
            else (4 if self.verification_confidence == "low" else 7)
        )
        return {
            "final_score": self.importance_score,
            "priority_level": self.priority_level,
            "severity_score": sev,
            "novelty_score": nov,
            "verification_confidence": self.verification_confidence,
            "confidence_score": conf_num,
            "corroboration_status": self.corroboration_status or ctx.get("corroboration_status", "First Report / Unverified"),
            "corroboration_count": ctx.get("corroboration_count", len(self.corroborating_sources)),
            "corroborating_sources": self.corroborating_sources or ctx.get("corroborating_sources", []),
            "same_source_echo_count": ctx.get("same_source_echo_count", 0),
            "is_same_source_echo": bool(ctx.get("is_same_source_echo", False)),
            "max_similarity": round(float(ctx.get("max_similarity", 0.0)), 3),
            "source_reliability_tier": ctx.get("source_reliability_tier", "tier_2_regional_osint"),
            "source_reliability_label": ctx.get("source_reliability_label", "Standard OSINT Feed"),
            "signal_type": kin.get("signal_type", "general_intelligence"),
            "signal_verification": kin.get("verification_status", "contextual_update"),
            "velocity_status": ctx.get("velocity_status", "Normal"),
            "calibration_notes": list(self.calibration_notes),
            "related_reports": ctx.get("related_reports", []),
        }



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


class ClusterSynthesisLLMItem(BaseModel):
    """Per-cluster narrative assessment generated by the AI situational summary engine."""
    cluster_id: int = 0
    title: str = Field(
        default="Thematic Intelligence Cluster",
        description="Concise operational title for this semantic storyline cluster.",
    )
    executive_summary: str = Field(
        default="",
        description="2-3 sentence synthesis of the reports in this cluster, noting source consensus.",
    )
    operational_implication: str = Field(
        default="",
        description="1 sentence tactical or strategic takeaway.",
    )
    threat_level: Literal["low", "medium", "high", "critical"] = "medium"

    @field_validator("threat_level", mode="before")
    @classmethod
    def normalize_threat_level(cls, v: Any) -> str:
        s = str(v).lower().strip() if v else "medium"
        if s in ("low", "medium", "high", "critical"):
            return s
        return "medium"


class SituationBriefingLLMOutput(BaseModel):
    """Structured LLM output contract for the full-spectrum AI Situational Summary."""
    threat_trajectory: Literal["Escalating", "Elevated", "Stable", "De-escalating", "Volatile"] = "Elevated"
    threat_level_score: int = Field(default=5, ge=1, le=10)
    executive_bluf: str = Field(
        default="",
        description="Bottom Line Up Front 2-3 paragraph comprehensive situational synthesis.",
    )
    strategic_assessment: str = Field(
        default="",
        description="Analysis of cross-cluster dynamics, reporting velocity, and anomalous indicators.",
    )
    cluster_assessments: List[ClusterSynthesisLLMItem] = Field(default_factory=list)
    key_developments: List[str] = Field(default_factory=list)
    unverified_outlier_assessment: str = Field(
        default="",
        description="Assessment of single-source or isolated outlier claims flagged by embedding distance.",
    )
    watchlist_indicators: List[str] = Field(default_factory=list)

    @field_validator("threat_trajectory", mode="before")
    @classmethod
    def normalize_trajectory(cls, v: Any) -> str:
        s = str(v).strip().capitalize() if v else "Elevated"
        valid = {"Escalating", "Elevated", "Stable", "De-escalating", "Volatile"}
        if s in valid:
            return s
        s_low = s.lower()
        if "escalat" in s_low and "de" not in s_low:
            return "Escalating"
        if "de-escalat" in s_low or "deescalat" in s_low:
            return "De-escalating"
        if "volat" in s_low:
            return "Volatile"
        if "stab" in s_low:
            return "Stable"
        return "Elevated"

    @field_validator("threat_level_score", mode="before")
    @classmethod
    def clamp_threat_score(cls, v: Any) -> int:
        try:
            return max(1, min(10, round(float(v))))
        except (ValueError, TypeError):
            return 5


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
    entities_json: List[str] = Field(default_factory=list)
    trend_category: Optional[str] = None
    notification_dispatched: bool = False
    notification_dispatched_at: Optional[Union[datetime, str]] = None
    is_analyzed: bool = True
    processed_at: Optional[datetime] = Field(default_factory=lambda: datetime.now(timezone.utc))

    def model_post_init(self, __context: Any) -> None:
        if self.entities and not self.entities_json:
            self.entities_json = list(self.entities)
        elif self.entities_json and not self.entities:
            self.entities = list(self.entities_json)

        if self.location and not self.location_json:
            self.location_json = self.location
        elif self.location_json and not self.location:
            self.location = self.location_json

        if self.metadata and not self.metadata_json:
            self.metadata_json = self.metadata
        elif self.metadata_json and not self.metadata:
            self.metadata = self.metadata_json
