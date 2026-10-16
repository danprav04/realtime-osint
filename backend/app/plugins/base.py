import abc
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


class Location(BaseModel):
    """Geospatial coordinates and locality description."""
    name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "lat": self.lat, "lon": self.lon}


class NormalizedEvent(BaseModel):
    """
    Canonical normalized intelligence event schema produced by all data source plugins.
    Ensures a single uniform contract for the AI reasoning pipeline, storage engine,
    and real-time WebSocket distribution bus.
    """
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    theme_id: str
    source_type: Literal[
        "rss",
        "telegram",
        "newsapi",
        "reddit",
        "ais",
        "bluesky",
        "bsky",
        "mastodon",
        "fediverse",
        "seismic",
        "emsc",
        "geophysical",
        "ukmto",
        "oref",
        "pikud_haoref",
        "homefront",
        "hfc",
    ]
    source_name: str
    source_url: Optional[str] = None
    external_id: str
    title: str
    raw_text: str
    language: Optional[str] = "en"
    author: Optional[str] = None
    location: Optional[Location] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    published_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("source_type", mode="before")
    @classmethod
    def normalize_source_type(cls, v: Any) -> str:
        s = str(v).lower().strip()
        if s in ("news_api", "newsapi"):
            return "newsapi"
        if s in ("maritime_ais", "ais"):
            return "ais"
        if s in ("bsky", "bluesky"):
            return "bluesky"
        if s in ("fediverse", "mastodon"):
            return "mastodon"
        if s in ("oref", "pikud_haoref", "homefront", "hfc"):
            return "oref"
        return s

    @field_validator("theme_id", mode="before")
    @classmethod
    def coerce_theme_id(cls, v: Any) -> str:
        return str(v)

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(cls, v: Any) -> Optional[Location]:
        if v is None:
            return None
        if isinstance(v, Location):
            return v
        if isinstance(v, dict):
            return Location(
                name=v.get("name") or v.get("location_name"),
                lat=v.get("lat") if v.get("lat") is not None else v.get("latitude"),
                lon=v.get("lon") if v.get("lon") is not None else v.get("longitude"),
            )
        return None

    @field_validator("published_at", "ingested_at", mode="before")
    @classmethod
    def ensure_timezone_aware(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            if v.tzinfo is None:
                return v.replace(tzinfo=timezone.utc)
            return v
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                return datetime.now(timezone.utc)
        return datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to a plain dictionary."""
        data = self.model_dump()
        if self.location:
            data["location"] = self.location.to_dict()
        return data


class BaseSourcePlugin(abc.ABC):
    """
    Abstract base class for all real-time OSINT data source ingestion plugins.
    Ensures uniform lifecycle management, isolated error boundaries, and health tracking.
    """

    def __init__(
        self,
        source_id: Union[int, str],
        theme_id: Union[int, str],
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 300,
        is_enabled: bool = True,
    ):
        self.source_id = str(source_id)
        self.theme_id = str(theme_id)
        self.name = name
        self.config: Dict[str, Any] = config or {}
        self.poll_interval = max(1, int(poll_interval))
        self._is_enabled = is_enabled
        self.is_running: bool = False
        self.status: str = "active" if is_enabled else "paused"
        self.last_error: Optional[str] = None
        self.last_polled_at: Optional[datetime] = None
        self.events_count: int = 0
        raw_lookback = self.config.get("max_lookback_hours")
        self.max_lookback_hours: Optional[float] = float(raw_lookback) if raw_lookback is not None else None
        self.logger = logging.getLogger(f"osint.plugin.{self.__class__.__name__}.{self.source_id}")

    def is_event_within_lookback(self, published_at: datetime) -> bool:
        """
        Verify if an event's publication timestamp falls within the configured lookback window.
        Returns True if within lookback or if no lookback is configured (None or <= 0).
        """
        if self.max_lookback_hours is None or self.max_lookback_hours <= 0:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.max_lookback_hours)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        return published_at >= cutoff

    @property
    def is_enabled(self) -> bool:
        """Indicates whether this plugin instance is actively permitted to poll."""
        return self._is_enabled

    @is_enabled.setter
    def is_enabled(self, value: bool) -> None:
        self._is_enabled = bool(value)
        self.status = "active" if self._is_enabled else "paused"

    @abc.abstractmethod
    async def poll(self) -> List[NormalizedEvent]:
        """
        Poll the upstream source, parse raw content, and return normalized events.
        Must handle source-specific parsing errors internally and return a list of NormalizedEvent.
        """
        pass

    async def start(self) -> None:
        """Lifecycle hook called when the plugin is started by the supervisor."""
        self.is_running = True
        if self._is_enabled:
            self.status = "active"
        self.logger.info(f"Plugin '{self.name}' (id={self.source_id}) started.")

    async def stop(self) -> None:
        """Lifecycle hook called when the plugin is stopped by the supervisor."""
        self.is_running = False
        if not self._is_enabled:
            self.status = "paused"
        self.logger.info(f"Plugin '{self.name}' (id={self.source_id}) stopped.")

    @abc.abstractmethod
    async def test_connection(self) -> bool:
        """
        Validate connectivity, API credentials, or remote feed availability.
        Returns True if the source is accessible, False otherwise.
        """
        pass
