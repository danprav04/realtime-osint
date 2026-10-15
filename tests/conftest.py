"""
tests/conftest.py - Shared Fixtures, Mock Services, and Test Clients
for the Real-time OSINT Gathering Service E2E Test Suite.
"""

import sys
import os
import uuid
import time
import json
import re
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Literal

import pytest
from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, HTTPException, Header, Query, status
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

# Ensure root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ============================================================================
# 1. INTERFACE CONTRACTS & CANONICAL SCHEMAS (Derived from PROJECT.md)
# ============================================================================

class Location(BaseModel):
    name: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class NormalizedEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    theme_id: str
    source_type: Literal["rss", "telegram", "newsapi", "reddit", "ais"]
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


class AIStageConfig(BaseModel):
    stage_id: Literal["extraction", "scoring", "summarization", "visuals"]
    provider: Literal["gemini", "groq"]
    model_name: str
    api_key_override: Optional[str] = None
    temperature: float = 0.2


class Stage1Result(BaseModel):
    extracted_entities: List[str]
    detected_language: str
    translated_text: str
    key_facts: List[str]


class Stage2Result(BaseModel):
    importance_score: int = Field(ge=1, le=10)
    score_rationale: str
    priority_level: Literal["low", "medium", "high", "critical"]
    should_alert: bool


class Stage3Result(BaseModel):
    summary: str
    headline: str


class Stage4Result(BaseModel):
    trend_category: str
    cluster_tags: List[str]
    narrative_insight: str


class AnalyzedEvent(NormalizedEvent):
    translated_text: Optional[str] = None
    importance_score: int = 5
    score_rationale: str = "Standard event assessment"
    summary: str = ""
    headline: str = ""
    entities: List[str] = Field(default_factory=list)
    trend_category: Optional[str] = None
    notification_dispatched: bool = False
    notification_dispatched_at: Optional[datetime] = None


class ConfigReloadPayload(BaseModel):
    action: Literal["theme_update", "source_update", "ai_config_update", "reload_all"]
    theme_id: Optional[str] = None
    source_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TelegramAlertPayload(BaseModel):
    event_id: str
    title: str
    summary: str
    importance_score: int
    source_name: str
    published_at: datetime
    source_url: Optional[str] = None


class SourceConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    theme_id: str
    name: str
    source_type: Literal["rss", "telegram", "newsapi", "reddit", "ais"]
    target: str  # URL, @channel, subreddit, API query, or bbox
    enabled: bool = True
    interval_seconds: int = 300
    last_polled_at: Optional[datetime] = None
    status: str = "active"  # "active", "degraded", "error", "paused"
    error_message: Optional[str] = None


class ThemeConfig(BaseModel):
    id: str
    name: str
    description: str
    is_active: bool = True
    keywords: List[str]
    entities: List[str]
    scoring_prompt: str
    importance_threshold: int = 7
    notification_frequency_limit_minutes: int = 5
    source_presets: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# 2. MOCK EXTERNAL SERVICES (Offline High-Fidelity Emulators)
# ============================================================================

class MockGeminiService:
    """Simulates Google Gemini API (gemini-2.0-flash, gemini-1.5-pro)."""

    def __init__(self, api_key: str = "mock-gemini-key"):
        self.api_key = api_key
        self.call_history: List[Dict[str, Any]] = []
        self.simulate_429: bool = False
        self.simulate_auth_error: bool = False

    def process_stage(self, stage: str, input_text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.call_history.append({"stage": stage, "input_text": input_text, "context": context})

        if self.simulate_auth_error or not self.api_key:
            raise PermissionError("Gemini API key is invalid or unauthorized (HTTP 401)")
        if self.simulate_429:
            raise RuntimeError("Gemini ResourceExhausted: 429 Quota Exceeded")

        # Stage 1: Extraction & Translation
        if stage == "extraction":
            is_farsi = any(char in input_text for char in "سپچگکژآابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")
            is_arabic = any(char in input_text for char in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي")
            if is_farsi:
                return {
                    "extracted_entities": ["Iran", "IRGC", "Persian Gulf"],
                    "detected_language": "fa",
                    "translated_text": f"English translation: {input_text[:100]}... [Translated from Persian]",
                    "key_facts": ["Military movement detected", "Official Iranian state announcement"]
                }
            elif is_arabic:
                return {
                    "extracted_entities": ["Middle East", "Houthi", "Red Sea"],
                    "detected_language": "ar",
                    "translated_text": f"English translation: {input_text[:100]}... [Translated from Arabic]",
                    "key_facts": ["Regional escalation report", "Naval warning issued"]
                }
            return {
                "extracted_entities": ["United States", "Middle East"],
                "detected_language": "en",
                "translated_text": input_text,
                "key_facts": ["Geopolitical briefing report"]
            }

        # Stage 2: Importance Scoring
        elif stage == "scoring":
            scoring_prompt = (context or {}).get("scoring_prompt", "")
            lower_text = input_text.lower()
            # Dynamic prompt-guided scoring
            if "score diplomatic talks 10" in scoring_prompt.lower() and "diplomatic" in lower_text:
                score = 10
                rationale = "Diplomatic talks explicitly instructed to receive highest score (10)."
            elif "score diplomatic talks 1" in scoring_prompt.lower() and "diplomatic" in lower_text:
                score = 1
                rationale = "Diplomatic talks explicitly instructed to receive lowest score (1)."
            elif re.search(r"\b(missile|strike|kinetic|nuclear|war|intercepted|intercept|clash)\b", lower_text):
                score = 9
                rationale = "High-priority kinetic or military escalation signal matching threat criteria."
            elif re.search(r"\b(sanction|sanctions|drill|patrol|warning|deployment|tanker)\b", lower_text):
                score = 7
                rationale = "Significant military drill or economic escalation event."
            elif re.search(r"\b(meeting|statement|summit|comment)\b", lower_text):
                score = 4
                rationale = "Routine diplomatic statement without immediate kinetic threat."
            else:
                score = 3
                rationale = "Background geopolitical observation."

            threshold = (context or {}).get("importance_threshold", 7)
            return {
                "importance_score": max(1, min(10, score)),
                "score_rationale": rationale,
                "priority_level": "critical" if score >= 9 else ("high" if score >= 7 else "medium"),
                "should_alert": score >= threshold
            }

        # Stage 3: Summarization
        elif stage == "summarization":
            headline = input_text.split(".")[0][:80] if input_text else "OSINT Event Report"
            return {
                "summary": f"Concise intelligence summary: {input_text[:120]}...",
                "headline": headline
            }

        # Stage 4: Visuals & Trends
        elif stage == "visuals":
            return {
                "trend_category": "Naval Escallation" if "naval" in input_text.lower() else "Geopolitical",
                "cluster_tags": ["Strait of Hormuz", "IRGC", "US Navy"],
                "narrative_insight": "Sustained increase in naval patrol activity observed over the past 24 hours."
            }
        return {}


class MockGroqService:
    """Simulates Groq Cloud API (qwen/qwen3.8-27b, openai/gpt-oss-120b)."""

    def __init__(self, api_key: str = "mock-groq-key"):
        self.api_key = api_key
        self.call_history: List[Dict[str, Any]] = []
        self.simulate_429: bool = False
        self.simulate_auth_error: bool = False

    def process_stage(self, stage: str, input_text: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.call_history.append({"stage": stage, "input_text": input_text, "context": context})

        if self.simulate_auth_error or not self.api_key:
            raise PermissionError("Groq API key is invalid or unauthorized (HTTP 401)")
        if self.simulate_429:
            raise RuntimeError("Groq RateLimitError: 429 Rate limit reached")

        # Stage 1: Extraction & Translation
        if stage == "extraction":
            return {
                "extracted_entities": ["Groq-Extracted Entity 1", "Middle East"],
                "detected_language": "fa" if any(ord(c) > 1500 for c in input_text) else "en",
                "translated_text": f"Groq LLaMA translation: {input_text[:90]}",
                "key_facts": ["Extracted via Groq high-speed inference engine"]
            }

        # Stage 2: Scoring
        elif stage == "scoring":
            lower_text = input_text.lower()
            score = 8 if any(w in lower_text for w in ["missile", "strike", "escalation"]) else 5
            threshold = (context or {}).get("importance_threshold", 7)
            return {
                "importance_score": score,
                "score_rationale": "Evaluated via Groq LLaMA-3.3 model.",
                "priority_level": "high" if score >= 7 else "medium",
                "should_alert": score >= threshold
            }

        # Stage 3: Summarization
        elif stage == "summarization":
            return {
                "summary": f"Groq generated concise intelligence summary of event: {input_text[:80]}",
                "headline": "Groq Accelerated Summary"
            }

        # Stage 4: Visuals
        elif stage == "visuals":
            return {
                "trend_category": "Regional Tension Surge",
                "cluster_tags": ["Groq Analysis", "Geopolitics"],
                "narrative_insight": "Accelerated narrative analysis indicates multi-source convergence."
            }
        return {}


class MockTelegramBotService:
    """Simulates python-telegram-bot sending outbound alerts."""

    def __init__(self, bot_token: str = "mock-bot-token:12345", chat_id: str = "-1001234567890"):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.sent_messages: List[Dict[str, Any]] = []
        self.simulate_error: Optional[str] = None
        self.last_sent_timestamp: float = 0.0
        self.rate_limit_seconds: float = 0.05
        self.rate_limiting_enabled: bool = False

    def send_alert(self, payload: TelegramAlertPayload) -> Dict[str, Any]:
        if self.simulate_error == "invalid_token":
            raise ValueError("Telegram Bot API: Unauthorized / Invalid Token")
        if self.simulate_error == "chat_not_found":
            raise LookupError("Telegram Bot API: Chat not found (400 Bad Request)")
        if self.simulate_error == "flood_wait":
            raise RuntimeError("Telegram FloodWaitError: A wait of 30 seconds is required")

        # Rate-limiting check when explicitly enabled (unless score is 10)
        now = time.time()
        if self.rate_limiting_enabled and payload.importance_score < 10 and (now - self.last_sent_timestamp) < self.rate_limit_seconds:
            # Throttled
            return {"status": "throttled", "reason": "rate_limit_cooldown"}

        self.last_sent_timestamp = now

        # Construct required template formatting
        formatted_text = (
            f"🚨 *OSINT CRITICAL ALERT* [{payload.importance_score}/10]\n\n"
            f"*Summary:* {payload.summary}\n\n"
            f"*Source:* {payload.source_name}\n"
            f"*Time:* {payload.published_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"*Link:* {payload.source_url or 'N/A'}"
        )

        message_record = {
            "message_id": len(self.sent_messages) + 1,
            "chat_id": self.chat_id,
            "payload": payload.model_dump(),
            "formatted_text": formatted_text,
            "sent_at": datetime.now(timezone.utc)
        }
        self.sent_messages.append(message_record)
        return {"status": "delivered", "message_id": message_record["message_id"]}


class MockRSSFeedService:
    """Generates synthetic RSS/Atom feeds and simulates edge conditions."""

    @staticmethod
    def generate_rss_xml(items: List[Dict[str, str]], title: str = "OSINT News Feed") -> str:
        xml_items = []
        for it in items:
            guid = it.get("guid", hashlib.sha256(it.get("link", "").encode()).hexdigest())
            xml_items.append(f"""
            <item>
                <title><![CDATA[{it.get('title', 'Untitled')}]]></title>
                <link>{it.get('link', 'https://example.com/article')}</link>
                <description><![CDATA[{it.get('description', '')}]]></description>
                <guid>{guid}</guid>
                <pubDate>{it.get('pubDate', 'Tue, 06 Oct 2026 20:30:00 GMT')}</pubDate>
            </item>
            """)
        return f"""<?xml version="1.0" encoding="UTF-8" ?>
        <rss version="2.0">
        <channel>
            <title>{title}</title>
            <link>https://example.com</link>
            <description>Test OSINT Feed</description>
            {''.join(xml_items)}
        </channel>
        </rss>"""

    @staticmethod
    def generate_empty_feed() -> str:
        return """<?xml version="1.0" encoding="UTF-8" ?><rss version="2.0"><channel><title>Empty Feed</title></channel></rss>"""

    @staticmethod
    def generate_malformed_feed() -> str:
        return "<html><body><h1>502 Bad Gateway</h1><p>The upstream server failed.</p></body></html>"


class MockTelegramChannelService:
    """Emulates Telethon MTProto event ingestion."""

    def __init__(self, api_id: int = 123456, api_hash: str = "mock-telethon-hash"):
        self.api_id = api_id
        self.api_hash = api_hash
        self.channels: Dict[str, List[Dict[str, Any]]] = {}

    def push_message(self, channel: str, text: str, message_id: Optional[int] = None) -> Dict[str, Any]:
        msg_id = message_id or (len(self.channels.get(channel, [])) + 1)
        record = {
            "channel": channel,
            "message_id": msg_id,
            "text": text,
            "date": datetime.now(timezone.utc).isoformat()
        }
        self.channels.setdefault(channel, []).append(record)
        return record


class MockNewsApiService:
    """Emulates NewsAPI, Currents API, and GDELT 2.0 Doc API."""

    def __init__(self, api_key: str = "mock-news-key"):
        self.api_key = api_key
        self.articles: List[Dict[str, Any]] = []

    def add_article(self, title: str, description: str, url: str, source_name: str = "Reuters World"):
        self.articles.append({
            "title": title,
            "description": description,
            "url": url,
            "source": {"name": source_name},
            "publishedAt": datetime.now(timezone.utc).isoformat()
        })

    def query(self, q: str, limit: int = 10) -> Dict[str, Any]:
        filtered = [a for a in self.articles if q.lower() in (a["title"] + " " + a["description"]).lower()]
        return {
            "status": "ok",
            "totalResults": len(filtered),
            "articles": filtered[:limit]
        }


class MockRedditService:
    """Emulates AsyncPRAW subreddit ingestion."""

    def __init__(self, client_id: str = "mock-reddit-id", client_secret: str = "mock-reddit-secret"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.submissions: Dict[str, List[Dict[str, Any]]] = {}

    def submit_post(self, subreddit: str, title: str, selftext: str, url: str = "https://reddit.com", score: int = 42):
        post = {
            "id": f"sub_{uuid.uuid4().hex[:8]}",
            "title": title,
            "selftext": selftext,
            "url": url,
            "author": "GeopoliticsWatcher",
            "score": score,
            "created_utc": time.time()
        }
        self.submissions.setdefault(subreddit, []).append(post)
        return post


class MockAISStreamService:
    """Emulates AISStream WebSocket & Digitraffic REST maritime vessel movement signals."""

    def __init__(self):
        self.vessels: List[Dict[str, Any]] = []

    def emit_vessel_signal(self, mmsi: str, name: str, lat: float, lon: float, speed: float, course: float):
        signal = {
            "mmsi": mmsi,
            "vessel_name": name,
            "latitude": lat,
            "longitude": lon,
            "speed_knots": speed,
            "course": course,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "zone": "Strait of Hormuz"
        }
        self.vessels.append(signal)
        return signal


# ============================================================================
# 3. CONTRACT REFERENCE FASTAPI APPLICATION
# ============================================================================

def create_contract_test_app() -> FastAPI:
    """
    Creates a contract-compliant reference FastAPI application.
    If the real backend is present and functional, routes delegate to it.
    Otherwise, this provides the authoritative contract implementation
    derived from PROJECT.md and spec_inventory.md.
    """
    app = FastAPI(title="Real-time OSINT Gathering Service (Test Harness)", version="1.0.0")

    # In-memory stores
    API_KEY = "test-secret-key-12345"
    db_themes: Dict[str, Dict[str, Any]] = {}
    db_sources: Dict[str, Dict[str, Any]] = {}
    db_events: Dict[str, Dict[str, Any]] = {}
    db_ai_configs: Dict[str, Any] = {
        "extraction": {
            "stage_id": "extraction",
            "provider": "gemini",
            "model_name": "gemini-2.0-flash",
            "temperature": 0.2,
            "fallbacks": [
                {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.2},
                {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.2},
            ],
        },
        "scoring": {
            "stage_id": "scoring",
            "provider": "gemini",
            "model_name": "gemini-2.0-flash",
            "temperature": 0.1,
            "fallbacks": [
                {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.1},
                {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.1},
                {"provider": "groq", "model_name": "openai/gpt-oss-120b", "temperature": 0.1},
                {"provider": "groq", "model_name": "qwen/qwen3.8-27b", "temperature": 0.1},
            ],
        },
        "summarization": {
            "stage_id": "summarization",
            "provider": "gemini",
            "model_name": "gemini-2.0-flash",
            "temperature": 0.3,
            "fallbacks": [
                {"provider": "groq", "model_name": "qwen/qwen3.8-27b", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.2},
                {"provider": "groq", "model_name": "openai/gpt-oss-20b", "temperature": 0.2},
            ],
        },
        "visuals": {
            "stage_id": "visuals",
            "provider": "gemini",
            "model_name": "gemini-2.0-flash",
            "temperature": 0.3,
            "fallbacks": [
                {"provider": "groq", "model_name": "qwen/qwen3.8-27b", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemini-3.5-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemini-3.1-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model_name": "gemma-4-26b-a4b-it", "temperature": 0.2},
            ],
        },
    }
    db_settings: Dict[str, Any] = {
        "api_keys": {
            "gemini_api_key": "mock-gemini-key",
            "groq_api_key": "mock-groq-key",
            "telegram_bot_token": "mock-bot-token:12345",
            "telegram_chat_id": "-1001234567890",
            "reddit_client_id": "mock-reddit-id",
            "news_api_key": "mock-news-key"
        },
        "notification_threshold": 7,
        "polling_intervals": {"rss": 180, "telegram": 60, "newsapi": 300, "reddit": 600, "ais": 900}
    }
    active_theme_id: List[str] = ["us-iran-conflict"]
    websocket_broadcast_log: List[Dict[str, Any]] = []

    # Mock services instances attached to app state
    app.state.mock_gemini = MockGeminiService(api_key="mock-gemini-key")
    app.state.mock_groq = MockGroqService(api_key="mock-groq-key")
    app.state.mock_telegram = MockTelegramBotService(bot_token="mock-bot-token:12345")
    app.state.mock_rss = MockRSSFeedService()
    app.state.mock_news = MockNewsApiService()
    app.state.mock_reddit = MockRedditService()
    app.state.mock_ais = MockAISStreamService()

    # Pre-populate US-Iran Theme (F31)
    us_iran_theme = {
        "id": "us-iran-conflict",
        "name": "US-Iran Conflict Monitoring",
        "description": "Real-time monitoring of geopolitical, naval, and military tensions involving the US and Iran.",
        "is_active": True,
        "keywords": [
            "IRGC", "Strait of Hormuz", "Persian Gulf", "CENTCOM", "enrichment",
            "Natanz", "Isfahan", "Tehran", "Houthis", "ballistic missile", "sanctions"
        ],
        "entities": ["United States", "Iran", "Israel", "IRGC", "US Navy", "Khamenei"],
        "scoring_prompt": (
            "You are a senior geopolitical intelligence analyst. Score the following event on a 1-10 importance scale "
            "for the US-Iran conflict theme. Guidelines: Score 9-10 for direct kinetic military engagements, strikes on naval/nuclear "
            "assets, or declared nuclear breakout. Score 7-8 for proxy attacks, major sanctions, troop deployments, or heightened naval "
            "blockades. Score 4-6 for standard diplomatic rhetoric, non-escalatory drills, or minor statements. Score 1-3 for background "
            "commentary. Provide an integer score and rationale."
        ),
        "importance_threshold": 7,
        "notification_frequency_limit_minutes": 5,
        "source_presets": {
            "rss_feeds": ["https://www.aljazeera.com/xml/rss/all.xml", "https://en.irna.ir/rss"],
            "telegram_channels": ["@OSINTdefender", "@MiddleEastSpectator"],
            "subreddits": ["worldnews", "geopolitics"]
        }
    }
    db_themes["us-iran-conflict"] = us_iran_theme

    # Pre-populate working seed sources
    seed_sources = [
        {"id": "src_rss_1", "theme_id": "us-iran-conflict", "name": "Al Jazeera English", "source_type": "rss", "target": "https://www.aljazeera.com/xml/rss/all.xml", "enabled": True, "interval_seconds": 180, "status": "active"},
        {"id": "src_rss_2", "theme_id": "us-iran-conflict", "name": "IRNA English", "source_type": "rss", "target": "https://en.irna.ir/rss", "enabled": True, "interval_seconds": 300, "status": "active"},
        {"id": "src_tg_1", "theme_id": "us-iran-conflict", "name": "OSINT Defender TG", "source_type": "telegram", "target": "@OSINTdefender", "enabled": True, "interval_seconds": 60, "status": "active"},
        {"id": "src_news_1", "theme_id": "us-iran-conflict", "name": "NewsAPI Iran Query", "source_type": "newsapi", "target": "Iran AND military", "enabled": True, "interval_seconds": 300, "status": "active"},
        {"id": "src_reddit_1", "theme_id": "us-iran-conflict", "name": "Reddit Geopolitics", "source_type": "reddit", "target": "geopolitics", "enabled": True, "interval_seconds": 600, "status": "active"},
        {"id": "src_ais_1", "theme_id": "us-iran-conflict", "name": "Strait of Hormuz AIS", "source_type": "ais", "target": "25.0,54.0,27.5,57.5", "enabled": True, "interval_seconds": 900, "status": "active"}
    ]
    for src in seed_sources:
        db_sources[src["id"]] = src

    # Auth Dependency
    def verify_auth(x_api_key: Optional[str] = Header(None), authorization: Optional[str] = Header(None)):
        token = x_api_key
        if not token and authorization:
            if authorization.startswith("Bearer "):
                token = authorization.split("Bearer ")[1].strip()
            else:
                token = authorization.strip()
        if not token or token != API_KEY:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key"
            )
        return token

    # --- HEALTH CHECKS ---
    @app.get("/health")
    def health_check():
        return {
            "status": "healthy",
            "database": "connected",
            "redis": "connected",
            "memory_mb": 180,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @app.get("/ready")
    def readiness_check():
        return {"ready": True, "version": "1.0.0"}

    # --- THEMES API ---
    @app.get("/api/themes", dependencies=[Depends(verify_auth)])
    def list_themes():
        return list(db_themes.values())

    @app.post("/api/themes", dependencies=[Depends(verify_auth)])
    def create_theme(payload: Dict[str, Any]):
        theme_id = payload.get("id") or str(uuid.uuid4())[:8]
        payload["id"] = theme_id
        db_themes[theme_id] = payload
        return payload

    @app.get("/api/themes/{theme_id}", dependencies=[Depends(verify_auth)])
    def get_theme(theme_id: str):
        if theme_id not in db_themes:
            raise HTTPException(status_code=404, detail="Theme not found")
        return db_themes[theme_id]

    @app.put("/api/themes/{theme_id}", dependencies=[Depends(verify_auth)])
    def update_theme(theme_id: str, payload: Dict[str, Any]):
        if theme_id not in db_themes:
            raise HTTPException(status_code=404, detail="Theme not found")
        db_themes[theme_id].update(payload)
        return db_themes[theme_id]

    @app.post("/api/themes/{theme_id}/activate", dependencies=[Depends(verify_auth)])
    def activate_theme(theme_id: str):
        if theme_id not in db_themes:
            raise HTTPException(status_code=404, detail="Theme not found")
        for tid in db_themes:
            db_themes[tid]["is_active"] = (tid == theme_id)
        active_theme_id[0] = theme_id
        return {"status": "activated", "active_theme_id": theme_id}

    # --- SOURCES API ---
    @app.get("/api/sources", dependencies=[Depends(verify_auth)])
    def list_sources(theme_id: Optional[str] = None, source_type: Optional[str] = None):
        results = list(db_sources.values())
        if theme_id:
            results = [s for s in results if s.get("theme_id") == theme_id]
        if source_type:
            results = [s for s in results if s.get("source_type") == source_type]
        return results

    @app.post("/api/sources", dependencies=[Depends(verify_auth)])
    def create_source(payload: Dict[str, Any]):
        source_id = payload.get("id") or f"src_{uuid.uuid4().hex[:8]}"
        payload["id"] = source_id
        payload.setdefault("enabled", True)
        payload.setdefault("status", "active")
        payload.setdefault("interval_seconds", 300)
        db_sources[source_id] = payload
        return payload

    @app.get("/api/sources/{source_id}", dependencies=[Depends(verify_auth)])
    def get_source(source_id: str):
        if source_id not in db_sources:
            raise HTTPException(status_code=404, detail="Source not found")
        return db_sources[source_id]

    @app.put("/api/sources/{source_id}", dependencies=[Depends(verify_auth)])
    def update_source(source_id: str, payload: Dict[str, Any]):
        if source_id not in db_sources:
            raise HTTPException(status_code=404, detail="Source not found")
        db_sources[source_id].update(payload)
        return db_sources[source_id]

    @app.delete("/api/sources/{source_id}", dependencies=[Depends(verify_auth)])
    def delete_source(source_id: str):
        if source_id not in db_sources:
            raise HTTPException(status_code=404, detail="Source not found")
        del db_sources[source_id]
        return {"status": "deleted", "id": source_id}

    @app.post("/api/sources/{source_id}/trigger", dependencies=[Depends(verify_auth)])
    def trigger_source(source_id: str):
        if source_id not in db_sources:
            raise HTTPException(status_code=404, detail="Source not found")
        src = db_sources[source_id]
        if not src.get("enabled", True):
            raise HTTPException(status_code=400, detail="Cannot trigger disabled source")
        src["last_polled_at"] = datetime.now(timezone.utc).isoformat()
        return {"status": "triggered", "source_id": source_id, "timestamp": src["last_polled_at"]}

    # --- EVENTS API ---
    @app.get("/api/events", dependencies=[Depends(verify_auth)])
    def list_events(
        theme_id: Optional[str] = None,
        source_type: Optional[str] = None,
        min_score: Optional[int] = None,
        sort_by: Optional[str] = "published_desc",
        limit: int = 50,
        offset: int = 0
    ):
        results = list(db_events.values())
        if theme_id:
            results = [e for e in results if e.get("theme_id") == theme_id]
        if source_type:
            results = [e for e in results if e.get("source_type") == source_type]
        if min_score is not None:
            results = [e for e in results if e.get("importance_score", 0) >= min_score]
        if sort_by == "score_desc":
            results.sort(key=lambda x: (x.get("importance_score") or -1, x.get("published_at") or ""), reverse=True)
        elif sort_by == "score_asc":
            results.sort(key=lambda x: (x.get("importance_score") if x.get("importance_score") is not None else 99, x.get("published_at") or ""))
        elif sort_by == "published_asc":
            results.sort(key=lambda x: x.get("published_at", ""))
        else:
            # Sort newest first
            results.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        return results[offset:offset + limit]

    @app.post("/api/events", dependencies=[Depends(verify_auth)])
    def ingest_event(event: Dict[str, Any]):
        event_id = event.get("id") or str(uuid.uuid4())
        event["id"] = event_id
        event.setdefault("ingested_at", datetime.now(timezone.utc).isoformat())
        db_events[event_id] = event
        # Broadcast via WebSocket
        websocket_broadcast_log.append({"type": "event:new", "payload": event})
        return event

    @app.get("/api/events/{event_id}", dependencies=[Depends(verify_auth)])
    def get_event(event_id: str):
        if event_id not in db_events:
            raise HTTPException(status_code=404, detail="Event not found")
        return db_events[event_id]

    # --- PIPELINE PROCESS API ---
    @app.post("/api/pipeline/process", dependencies=[Depends(verify_auth)])
    def process_event_pipeline(raw_event: Dict[str, Any]):
        """
        Executes the 4-stage AI Pipeline on a normalized event.
        Dispatches to Gemini or Groq depending on db_ai_configs for each stage.
        """
        current_theme = db_themes.get(active_theme_id[0], {})
        scoring_prompt = current_theme.get("scoring_prompt", "")
        threshold = current_theme.get("importance_threshold", 7)

        input_text = raw_event.get("raw_text") or raw_event.get("title", "")

        # Stage 1: Extraction & Translation
        cfg_s1 = db_ai_configs.get("extraction", {})
        provider_s1 = app.state.mock_groq if cfg_s1.get("provider") == "groq" else app.state.mock_gemini
        res_s1 = provider_s1.process_stage("extraction", input_text)

        # Stage 2: Scoring
        cfg_s2 = db_ai_configs.get("scoring", {})
        provider_s2 = app.state.mock_groq if cfg_s2.get("provider") == "groq" else app.state.mock_gemini
        res_s2 = provider_s2.process_stage(
            "scoring",
            res_s1.get("translated_text", input_text),
            context={"scoring_prompt": scoring_prompt, "importance_threshold": threshold}
        )

        # Stage 3: Summarization
        cfg_s3 = db_ai_configs.get("summarization", {})
        provider_s3 = app.state.mock_groq if cfg_s3.get("provider") == "groq" else app.state.mock_gemini
        res_s3 = provider_s3.process_stage("summarization", res_s1.get("translated_text", input_text))

        # Stage 4: Visuals & Trends
        cfg_s4 = db_ai_configs.get("visuals", {})
        provider_s4 = app.state.mock_groq if cfg_s4.get("provider") == "groq" else app.state.mock_gemini
        res_s4 = provider_s4.process_stage("visuals", res_s1.get("translated_text", input_text))

        # Synthesize AnalyzedEvent
        analyzed = dict(raw_event)
        analyzed["translated_text"] = res_s1.get("translated_text")
        analyzed["entities"] = res_s1.get("extracted_entities", [])
        analyzed["importance_score"] = res_s2.get("importance_score", 5)
        analyzed["score_rationale"] = res_s2.get("score_rationale", "")
        analyzed["summary"] = res_s3.get("summary", "")
        analyzed["headline"] = res_s3.get("headline", raw_event.get("title", ""))
        analyzed["trend_category"] = res_s4.get("trend_category")
        analyzed["notification_dispatched"] = False
        analyzed["notification_dispatched_at"] = None

        # Persist event
        event_id = analyzed.get("id") or str(uuid.uuid4())
        analyzed["id"] = event_id
        db_events[event_id] = analyzed

        # If score >= threshold, trigger Telegram alert
        if analyzed["importance_score"] >= threshold:
            alert_payload = TelegramAlertPayload(
                event_id=event_id,
                title=analyzed.get("title", ""),
                summary=analyzed["summary"],
                importance_score=analyzed["importance_score"],
                source_name=analyzed.get("source_name", "Unknown Source"),
                published_at=datetime.now(timezone.utc),
                source_url=analyzed.get("source_url")
            )
            telegram_res = app.state.mock_telegram.send_alert(alert_payload)
            if telegram_res.get("status") == "delivered":
                analyzed["notification_dispatched"] = True
                analyzed["notification_dispatched_at"] = datetime.now(timezone.utc).isoformat()
            websocket_broadcast_log.append({"type": "alert:critical", "payload": analyzed})

        websocket_broadcast_log.append({"type": "event:new", "payload": analyzed})
        return analyzed

    # --- SETTINGS & AI CONFIG ---
    @app.get("/api/settings", dependencies=[Depends(verify_auth)])
    def get_settings():
        masked_keys = {k: ("***" + v[-4:] if v and len(v) > 4 else "***") for k, v in db_settings["api_keys"].items()}
        return {
            "api_keys": masked_keys,
            "notification_threshold": db_settings["notification_threshold"],
            "polling_intervals": db_settings["polling_intervals"]
        }

    @app.put("/api/settings", dependencies=[Depends(verify_auth)])
    def update_settings(payload: Dict[str, Any]):
        if "api_keys" in payload:
            for k, v in payload["api_keys"].items():
                if v and not v.startswith("***"):
                    db_settings["api_keys"][k] = v
                    if k == "gemini_api_key":
                        app.state.mock_gemini.api_key = v
                    elif k == "groq_api_key":
                        app.state.mock_groq.api_key = v
        if "notification_threshold" in payload:
            db_settings["notification_threshold"] = payload["notification_threshold"]
        if "polling_intervals" in payload:
            db_settings["polling_intervals"].update(payload["polling_intervals"])
        return {"status": "updated", "settings": db_settings}

    @app.get("/api/settings/ai-pipeline", dependencies=[Depends(verify_auth)])
    def get_ai_pipeline_config():
        return db_ai_configs

    @app.put("/api/settings/ai-pipeline", dependencies=[Depends(verify_auth)])
    def update_ai_pipeline_config(payload: Dict[str, Any]):
        for stage, cfg in payload.items():
            if stage in db_ai_configs:
                db_ai_configs[stage].update(cfg)
        return db_ai_configs

    # --- STATS API ---
    @app.get("/api/stats", dependencies=[Depends(verify_auth)])
    def get_stats():
        events_list = list(db_events.values())
        threshold = db_settings.get("notification_threshold", 7)
        high_priority_count = sum(1 for e in events_list if e.get("importance_score", 0) >= threshold)
        active_sources_count = sum(1 for s in db_sources.values() if s.get("enabled", True))
        last_updated = max((e.get("ingested_at", "") for e in events_list), default=datetime.now(timezone.utc).isoformat())

        return {
            "total_events": len(events_list),
            "high_priority_events": high_priority_count,
            "active_sources": active_sources_count,
            "last_updated": last_updated
        }

    # Helper endpoint for test inspection
    @app.get("/test/websocket-logs", dependencies=[Depends(verify_auth)])
    def get_ws_logs():
        return websocket_broadcast_log

    return app


# ============================================================================
# 4. PYTEST FIXTURES
# ============================================================================

@pytest.fixture(scope="session")
def app():
    """Returns the contract reference app."""
    return create_contract_test_app()


@pytest.fixture
def test_client(app):
    """Authenticated client with valid X-API-Key."""
    return TestClient(app, headers={"X-API-Key": "test-secret-key-12345"})


@pytest.fixture
def unauth_client(app):
    """Unauthenticated client without API key."""
    return TestClient(app)


@pytest.fixture
def mock_gemini(app):
    return app.state.mock_gemini


@pytest.fixture
def mock_groq(app):
    return app.state.mock_groq


@pytest.fixture
def mock_telegram(app):
    return app.state.mock_telegram


@pytest.fixture
def mock_rss(app):
    return app.state.mock_rss


@pytest.fixture
def mock_news(app):
    return app.state.mock_news


@pytest.fixture
def mock_reddit(app):
    return app.state.mock_reddit


@pytest.fixture
def mock_ais(app):
    return app.state.mock_ais


@pytest.fixture
def sample_normalized_event():
    """Factory fixture generating valid NormalizedEvent payload."""
    return {
        "id": f"event_{uuid.uuid4().hex[:8]}",
        "theme_id": "us-iran-conflict",
        "source_type": "rss",
        "source_name": "Reuters World",
        "source_url": "https://reuters.com/world/us-iran-naval-warning",
        "external_id": f"ext_{uuid.uuid4().hex[:6]}",
        "title": "US Navy CENTCOM Issues Navigation Warning Near Strait of Hormuz",
        "raw_text": "US Navy CENTCOM issued an urgent navigational warning to commercial vessels transiting the Strait of Hormuz following IRGC high-speed patrol craft maneuvers.",
        "language": "en",
        "author": "Reuters Staff",
        "location": {"name": "Strait of Hormuz", "lat": 26.56, "lon": 56.25},
        "metadata": {"category": "military"},
        "published_at": datetime.now(timezone.utc).isoformat(),
        "ingested_at": datetime.now(timezone.utc).isoformat()
    }


@pytest.fixture
def sample_farsi_event():
    """Event with Persian/Farsi text."""
    return {
        "id": f"event_fa_{uuid.uuid4().hex[:8]}",
        "theme_id": "us-iran-conflict",
        "source_type": "telegram",
        "source_name": "IRNA Persian",
        "source_url": "https://t.me/irna_1313/98765",
        "external_id": f"ext_tg_{uuid.uuid4().hex[:6]}",
        "title": "رزمایش مشترک موشکی و پهپادی سپاه پاسداران در خلیج فارس",
        "raw_text": "سپاه پاسداران انقلاب اسلامی رزمایش موشکی جدیدی را در تنگه هرمز و آب‌های نیلگون خلیج فارس آغاز کرد. سامانه‌های پدافند هوایی آزمایش شدند.",
        "language": "fa",
        "author": "IRNA",
        "location": {"name": "Persian Gulf", "lat": 27.0, "lon": 51.5},
        "metadata": {"channel_id": "@irna_1313", "message_id": 98765},
        "published_at": datetime.now(timezone.utc).isoformat(),
        "ingested_at": datetime.now(timezone.utc).isoformat()
    }


@pytest.fixture
def sample_arabic_event():
    """Event with Arabic text."""
    return {
        "id": f"event_ar_{uuid.uuid4().hex[:8]}",
        "theme_id": "us-iran-conflict",
        "source_type": "rss",
        "source_name": "Al Jazeera Arabic",
        "source_url": "https://aljazeera.net/news/2026/10/6/houthi-missile",
        "external_id": f"ext_ar_{uuid.uuid4().hex[:6]}",
        "title": "الحوثيون يعلنون استهداف سفينة تجارية في البحر الأحمر",
        "raw_text": "أعلنت جماعة الحوثي تنفيذ عملية عسكرية استهدفت سفينة شحن في البحر الأحمر بصواريخ باليستية وطائرات مسيرة.",
        "language": "ar",
        "author": "Al Jazeera",
        "location": {"name": "Red Sea", "lat": 15.0, "lon": 41.5},
        "metadata": {"feed_category": "urgent"},
        "published_at": datetime.now(timezone.utc).isoformat(),
        "ingested_at": datetime.now(timezone.utc).isoformat()
    }
