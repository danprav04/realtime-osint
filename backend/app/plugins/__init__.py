"""
backend/app/plugins - Data Source Ingestion Plugins & Supervisor

Provides:
- BaseSourcePlugin: Abstract base class for all ingestion plugins
- NormalizedEvent: Canonical event schema for real-time intelligence
- Location: Geospatial location schema
- RSSSourcePlugin: RSS/Atom feeds ingestion
- TelegramSourcePlugin: Telegram channel MTProto and web preview scraper
- NewsAPISourcePlugin: GDELT 2.0, NewsAPI, and Currents API ingestion
- RedditSourcePlugin: Geopolitical subreddit monitoring (AsyncPRAW & public JSON)
- AISSourcePlugin: Maritime and naval movement telemetry ingestion
- IngestionSupervisor: Asynchronous worker lifecycle and zero-code hot-reload manager
"""

from backend.app.plugins.base import BaseSourcePlugin, Location, NormalizedEvent
from backend.app.plugins.rss_plugin import RSSSourcePlugin
from backend.app.plugins.telegram_plugin import TelegramSourcePlugin
from backend.app.plugins.newsapi_plugin import NewsAPISourcePlugin
from backend.app.plugins.reddit_plugin import RedditSourcePlugin
from backend.app.plugins.ais_plugin import AISSourcePlugin
from backend.app.plugins.supervisor import (
    IngestionSupervisor,
    create_plugin_instance,
    get_ingestion_supervisor,
)

__all__ = [
    "BaseSourcePlugin",
    "Location",
    "NormalizedEvent",
    "RSSSourcePlugin",
    "TelegramSourcePlugin",
    "NewsAPISourcePlugin",
    "RedditSourcePlugin",
    "AISSourcePlugin",
    "IngestionSupervisor",
    "create_plugin_instance",
    "get_ingestion_supervisor",
]
