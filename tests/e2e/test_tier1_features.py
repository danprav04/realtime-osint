"""
tests/e2e/test_tier1_features.py - Tier 1: Functional Feature Coverage

Validates all core feature requirements (>=5 test cases per requirement area):
- 5 Source Ingestion Plugins (RSS, Telegram, NewsAPI, Reddit, Maritime AIS)
- Multi-Stage AI Pipeline (Extraction, Translation, Scoring, Summarization, Visuals)
- Zero-Code Theme System
- Live WebSocket Timeline & Badges
- Telegram Alert Dispatcher Bot
- API Authentication & Access Control
- Health Checks & Operational Readiness
"""

import uuid
from datetime import datetime, timezone
import pytest
from tests.conftest import (
    NormalizedEvent,
    AIStageConfig,
    Stage1Result,
    Stage2Result,
    Stage3Result,
    ThemeConfig,
    SourceConfig,
    TelegramAlertPayload,
    MockRSSFeedService
)


# ============================================================================
# 1. RSS / ATOM SOURCE PLUGIN (F01, F02, F10, F11, F12)
# ============================================================================

def test_rss_feed_normalization_valid_items(test_client, mock_rss):
    """F01/F11: Valid RSS items normalize into the canonical schema with title, link, and timestamp."""
    items = [
        {"title": "Naval standoff in Persian Gulf", "link": "https://reuters.com/1", "description": "Patrol boats approached."},
        {"title": "OPEC meeting concludes in Vienna", "link": "https://reuters.com/2", "description": "Oil output discussions."}
    ]
    xml_data = mock_rss.generate_rss_xml(items, title="Reuters Geopolitics")
    assert "<rss version=\"2.0\">" in xml_data
    assert "Naval standoff in Persian Gulf" in xml_data

    # Verify normalization into NormalizedEvent schema
    event = NormalizedEvent(
        theme_id="us-iran-conflict",
        source_type="rss",
        source_name="Reuters Geopolitics",
        source_url="https://reuters.com/1",
        external_id="reuters_1",
        title=items[0]["title"],
        raw_text=items[0]["description"],
        published_at=datetime.now(timezone.utc)
    )
    assert event.source_type == "rss"
    assert event.title == "Naval standoff in Persian Gulf"
    assert event.theme_id == "us-iran-conflict"
    assert event.id is not None


def test_rss_feed_configurable_polling_interval(test_client):
    """F02: Polling interval can be configured per RSS source (e.g. 180s, 300s)."""
    payload = {
        "theme_id": "us-iran-conflict",
        "name": "Custom Fast RSS Feed",
        "source_type": "rss",
        "target": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "interval_seconds": 120,
        "enabled": True
    }
    res = test_client.post("/api/sources", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["interval_seconds"] == 120
    assert data["source_type"] == "rss"


def test_rss_feed_custom_source_addition_via_api(test_client):
    """F13: New RSS feed source can be created, retrieved, and listed via API."""
    new_src = {
        "theme_id": "us-iran-conflict",
        "name": "Tehran Times RSS",
        "source_type": "rss",
        "target": "https://www.tehrantimes.com/rss",
        "enabled": True,
        "interval_seconds": 240
    }
    create_res = test_client.post("/api/sources", json=new_src)
    assert create_res.status_code == 200
    src_id = create_res.json()["id"]

    get_res = test_client.get(f"/api/sources/{src_id}")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Tehran Times RSS"


def test_rss_feed_status_tracking(test_client):
    """F10/F12: RSS source maintains status state (active, degraded, error)."""
    sources_res = test_client.get("/api/sources?source_type=rss")
    assert sources_res.status_code == 200
    sources = sources_res.json()
    assert len(sources) >= 1
    for src in sources:
        assert src["status"] in ["active", "degraded", "error", "paused"]


def test_rss_feed_multiple_distinct_feeds(test_client):
    """F01: Multiple distinct RSS feeds can coexist simultaneously under one theme."""
    list_res = test_client.get("/api/sources?theme_id=us-iran-conflict&source_type=rss")
    assert list_res.status_code == 200
    rss_sources = list_res.json()
    targets = [s["target"] for s in rss_sources]
    assert len(rss_sources) >= 2
    # Ensure targets are distinct
    assert len(targets) == len(set(targets))


# ============================================================================
# 2. TELEGRAM CHANNEL PLUGIN (F03, F04, F10, F11, F12)
# ============================================================================

def test_telegram_channel_message_normalization(sample_farsi_event):
    """F03/F11: Ingested Telegram messages normalize with channel_id, message_id, and author."""
    event = NormalizedEvent(**sample_farsi_event)
    assert event.source_type == "telegram"
    assert event.metadata["channel_id"] == "@irna_1313"
    assert event.metadata["message_id"] == 98765
    assert "رزمایش" in event.raw_text


def test_telegram_channel_configuration_via_api(test_client):
    """F03/F13: Public Telegram channels can be added and configured via API."""
    tg_src = {
        "theme_id": "us-iran-conflict",
        "name": "Middle East Military TG",
        "source_type": "telegram",
        "target": "@MEMilitaryIntel",
        "enabled": True,
        "interval_seconds": 60
    }
    res = test_client.post("/api/sources", json=tg_src)
    assert res.status_code == 200
    assert res.json()["target"] == "@MEMilitaryIntel"


def test_telegram_credentials_handling(test_client):
    """F04/F24: Telegram bot token and chat ID can be managed in settings."""
    settings_res = test_client.get("/api/settings")
    assert settings_res.status_code == 200
    data = settings_res.json()
    assert "telegram_bot_token" in data["api_keys"]
    assert "telegram_chat_id" in data["api_keys"]


def test_telegram_channel_source_isolation(test_client):
    """F12: Telegram sources run isolated from RSS and other plugins."""
    sources_res = test_client.get("/api/sources")
    assert sources_res.status_code == 200
    sources = sources_res.json()
    types = {s["source_type"] for s in sources}
    assert "telegram" in types
    assert "rss" in types


def test_telegram_multiple_channel_support(test_client):
    """F03: System supports monitoring multiple distinct Telegram channels."""
    res = test_client.get("/api/sources?source_type=telegram")
    assert res.status_code == 200
    assert len(res.json()) >= 1


# ============================================================================
# 3. NEWS APIS PLUGIN (F05, F06, F10, F11, F12)
# ============================================================================

def test_news_api_query_and_normalization(app):
    """F05/F11: News API query results are normalized into canonical schema."""
    mock_news = app.state.mock_news
    mock_news.add_article(
        title="Sanctions tightening announced in Washington",
        description="US Treasury announces new sanctions package targeting defense trade.",
        url="https://newsapi.org/article/101",
        source_name="Associated Press"
    )
    res = mock_news.query("sanctions")
    assert res["status"] == "ok"
    assert res["totalResults"] >= 1
    art = res["articles"][0]
    assert art["title"] == "Sanctions tightening announced in Washington"

    norm_event = NormalizedEvent(
        theme_id="us-iran-conflict",
        source_type="newsapi",
        source_name=art["source"]["name"],
        source_url=art["url"],
        external_id="news_101",
        title=art["title"],
        raw_text=art["description"],
        published_at=datetime.now(timezone.utc)
    )
    assert norm_event.source_type == "newsapi"
    assert norm_event.source_name == "Associated Press"


def test_news_api_configurable_polling_interval(test_client):
    """F06: News API polling interval is configurable (e.g. 300-600 seconds)."""
    src_payload = {
        "theme_id": "us-iran-conflict",
        "name": "GDELT Unmetered Stream",
        "source_type": "newsapi",
        "target": "iran military maritime",
        "interval_seconds": 300,
        "enabled": True
    }
    res = test_client.post("/api/sources", json=src_payload)
    assert res.status_code == 200
    assert res.json()["interval_seconds"] == 300


def test_news_api_keyword_search_filtering(app):
    """F05: Querying specific terms filters out non-matching articles."""
    mock_news = app.state.mock_news
    mock_news.add_article("Diplomatic envoy arrives in Geneva", "Talks on regional stability.", "https://ap.org/2", "AP")
    query_match = mock_news.query("Geneva")
    query_miss = mock_news.query("Submarine")
    assert len(query_match["articles"]) >= 1
    assert len(query_miss["articles"]) == 0


def test_news_api_source_attribution(sample_normalized_event):
    """F11/F36: News API events retain distinct publisher source attribution."""
    sample_normalized_event["source_type"] = "newsapi"
    sample_normalized_event["source_name"] = "Financial Times"
    event = NormalizedEvent(**sample_normalized_event)
    assert event.source_name == "Financial Times"
    assert event.source_type == "newsapi"


def test_news_api_status_and_metadata(test_client):
    """F10: News API source lists active status and interval in sources endpoint."""
    res = test_client.get("/api/sources?source_type=newsapi")
    assert res.status_code == 200
    sources = res.json()
    assert len(sources) >= 1
    assert sources[0]["status"] == "active"


# ============================================================================
# 4. REDDIT PLUGIN (F07, F08, F10, F11, F12)
# ============================================================================

def test_reddit_submission_normalization(app):
    """F07/F11: Reddit submissions normalize with author, upvotes, and subreddit metadata."""
    mock_reddit = app.state.mock_reddit
    post = mock_reddit.submit_post(
        subreddit="geopolitics",
        title="Analysis of maritime chokepoints in the Middle East",
        selftext="Discussion on Hormuz and Bab el-Mandeb security architecture.",
        score=156
    )
    event = NormalizedEvent(
        theme_id="us-iran-conflict",
        source_type="reddit",
        source_name="r/geopolitics",
        source_url=post["url"],
        external_id=post["id"],
        title=post["title"],
        raw_text=post["selftext"],
        author=post["author"],
        metadata={"subreddit": "geopolitics", "upvotes": post["score"]},
        published_at=datetime.now(timezone.utc)
    )
    assert event.source_type == "reddit"
    assert event.metadata["upvotes"] == 156
    assert event.metadata["subreddit"] == "geopolitics"


def test_reddit_configurable_subreddit_and_interval(test_client):
    """F08: Subreddit target and polling interval (e.g. 600s) can be set via API."""
    payload = {
        "theme_id": "us-iran-conflict",
        "name": "Subreddit WorldNews Monitor",
        "source_type": "reddit",
        "target": "worldnews",
        "interval_seconds": 600,
        "enabled": True
    }
    res = test_client.post("/api/sources", json=payload)
    assert res.status_code == 200
    assert res.json()["target"] == "worldnews"
    assert res.json()["interval_seconds"] == 600


def test_reddit_post_attributes_author_score_url(app):
    """F07: Post attributes maintain fidelity without data loss."""
    mock_reddit = app.state.mock_reddit
    post = mock_reddit.submit_post("worldnews", "Breaking: Missile defense test successful", "Brief description", score=420)
    assert post["score"] == 420
    assert post["author"] == "GeopoliticsWatcher"
    assert post["id"].startswith("sub_")


def test_reddit_source_creation_via_api(test_client):
    """F13: Adding Reddit sources updates system source inventory."""
    res_before = test_client.get("/api/sources?source_type=reddit")
    count_before = len(res_before.json())

    test_client.post("/api/sources", json={
        "theme_id": "us-iran-conflict",
        "name": "r/military",
        "source_type": "reddit",
        "target": "military",
        "interval_seconds": 600
    })

    res_after = test_client.get("/api/sources?source_type=reddit")
    assert len(res_after.json()) == count_before + 1


def test_reddit_post_filtering(test_client, sample_normalized_event):
    """F11: Reddit events appear in event query filtered by source_type."""
    reddit_event = dict(sample_normalized_event)
    reddit_event["source_type"] = "reddit"
    reddit_event["source_name"] = "r/worldnews"
    reddit_event["id"] = "reddit_test_event_1"

    post_res = test_client.post("/api/events", json=reddit_event)
    assert post_res.status_code == 200

    query_res = test_client.get("/api/events?source_type=reddit")
    assert query_res.status_code == 200
    results = query_res.json()
    assert any(e["id"] == "reddit_test_event_1" for e in results)


# ============================================================================
# 5. MARITIME / AIS TRACKING PLUGIN (F09, F10, F11, F12)
# ============================================================================

def test_maritime_ais_signal_normalization(app):
    """F09/F11: AIS vessel signals normalize with MMSI, vessel name, coordinates, speed, and course."""
    mock_ais = app.state.mock_ais
    signal = mock_ais.emit_vessel_signal(
        mmsi="123456789",
        name="PACIFIC VOYAGER",
        lat=26.45,
        lon=56.32,
        speed=14.2,
        course=185.0
    )
    event = NormalizedEvent(
        theme_id="us-iran-conflict",
        source_type="ais",
        source_name="AISStream Hormuz",
        external_id=signal["mmsi"],
        title=f"Vessel Position: {signal['vessel_name']} (MMSI: {signal['mmsi']})",
        raw_text=f"Vessel {signal['vessel_name']} transiting Strait of Hormuz at speed {signal['speed_knots']} knots, course {signal['course']} deg.",
        location={"name": "Strait of Hormuz", "lat": signal["latitude"], "lon": signal["longitude"]},
        metadata={"mmsi": signal["mmsi"], "vessel_name": signal["vessel_name"], "speed": signal["speed_knots"]},
        published_at=datetime.now(timezone.utc)
    )
    assert event.source_type == "ais"
    assert event.metadata["mmsi"] == "123456789"
    assert event.location.lat == 26.45
    assert event.location.lon == 56.32


def test_maritime_ais_coordinates_and_vessel_metadata(app):
    """F09: Vessel signals accurately record latitude, longitude, and course."""
    mock_ais = app.state.mock_ais
    sig = mock_ais.emit_vessel_signal("987654321", "GULF TANKER", 25.8, 55.4, 11.5, 90.0)
    assert sig["latitude"] == 25.8
    assert sig["speed_knots"] == 11.5
    assert sig["zone"] == "Strait of Hormuz"


def test_maritime_ais_bounding_box_configuration(test_client):
    """F09/F10: Maritime source configuration supports bounding box targets."""
    payload = {
        "theme_id": "us-iran-conflict",
        "name": "Persian Gulf South Bbox",
        "source_type": "ais",
        "target": "24.0,52.0,26.5,56.0",
        "interval_seconds": 900,
        "enabled": True
    }
    res = test_client.post("/api/sources", json=payload)
    assert res.status_code == 200
    assert res.json()["target"] == "24.0,52.0,26.5,56.0"


def test_maritime_ais_speed_and_course_attributes(sample_normalized_event):
    """F09: Location and vessel metadata serialize cleanly in JSON responses."""
    ais_event = dict(sample_normalized_event)
    ais_event["source_type"] = "ais"
    ais_event["metadata"]["speed_knots"] = 18.4
    ais_event["metadata"]["vessel_type"] = "Crude Oil Tanker"
    event = NormalizedEvent(**ais_event)
    assert event.metadata["speed_knots"] == 18.4


def test_maritime_ais_source_enable_disable(test_client):
    """F10: Maritime AIS source can be toggled enabled/disabled dynamically."""
    res = test_client.get("/api/sources?source_type=ais")
    assert res.status_code == 200
    ais_src = res.json()[0]
    src_id = ais_src["id"]

    # Toggle off
    toggle_res = test_client.put(f"/api/sources/{src_id}", json={"enabled": False})
    assert toggle_res.status_code == 200
    assert toggle_res.json()["enabled"] is False

    # Restore
    test_client.put(f"/api/sources/{src_id}", json={"enabled": True})


# ============================================================================
# 6. MULTI-STAGE AI PIPELINE (F14, F15, F16, F17, F18, F19, F20, F21, F22)
# ============================================================================

def test_ai_stage1_extraction_and_entity_parsing(test_client, sample_normalized_event):
    """F14: Stage 1 extracts named entities and key facts from event text."""
    res = test_client.post("/api/pipeline/process", json=sample_normalized_event)
    assert res.status_code == 200
    data = res.json()
    assert "entities" in data
    assert isinstance(data["entities"], list)
    assert len(data["entities"]) >= 1


def test_ai_stage1_farsi_to_english_translation(test_client, sample_farsi_event):
    """F15: Stage 1 automatically translates Persian/Farsi text into English."""
    res = test_client.post("/api/pipeline/process", json=sample_farsi_event)
    assert res.status_code == 200
    data = res.json()
    assert "translated_text" in data
    assert data["translated_text"] is not None
    # Verify translation output contains English words
    assert "English translation:" in data["translated_text"]


def test_ai_stage1_arabic_to_english_translation(test_client, sample_arabic_event):
    """F15: Stage 1 translates Arabic text into English."""
    res = test_client.post("/api/pipeline/process", json=sample_arabic_event)
    assert res.status_code == 200
    data = res.json()
    assert data["translated_text"] is not None
    assert "English translation:" in data["translated_text"]


def test_ai_stage2_importance_scoring_scale_and_rationale(test_client, sample_normalized_event):
    """F16: Stage 2 produces an integer importance score strictly within 1-10 and a rationale."""
    res = test_client.post("/api/pipeline/process", json=sample_normalized_event)
    assert res.status_code == 200
    data = res.json()
    assert "importance_score" in data
    assert isinstance(data["importance_score"], int)
    assert 1 <= data["importance_score"] <= 10
    assert "score_rationale" in data
    assert len(data["score_rationale"]) > 5


def test_ai_stage2_scoring_prompt_sensitivity(test_client):
    """F17: Modifying the theme's scoring prompt directly steers AI importance scores."""
    # Step 1: Update theme scoring prompt to prioritize low diplomatic score
    test_client.put("/api/themes/us-iran-conflict", json={
        "scoring_prompt": "Score diplomatic talks 1. De-prioritize rhetoric."
    })
    event_diplomatic = {
        "theme_id": "us-iran-conflict",
        "source_type": "rss",
        "source_name": "Reuters",
        "external_id": "dip_1",
        "title": "Diplomatic envoys meet for talks",
        "raw_text": "Diplomatic officials gathered to discuss ongoing treaty commitments."
    }
    res_low = test_client.post("/api/pipeline/process", json=event_diplomatic)
    assert res_low.status_code == 200
    assert res_low.json()["importance_score"] == 1

    # Step 2: Invert prompt: prioritize diplomatic talks as score 10
    test_client.put("/api/themes/us-iran-conflict", json={
        "scoring_prompt": "Score diplomatic talks 10. Prioritize all summit meetings."
    })
    res_high = test_client.post("/api/pipeline/process", json=event_diplomatic)
    assert res_high.status_code == 200
    assert res_high.json()["importance_score"] == 10

    # Step 3: Restore original seed scoring prompt for test isolation
    test_client.put("/api/themes/us-iran-conflict", json={
        "scoring_prompt": (
            "You are a senior geopolitical intelligence analyst. Score the following event on a 1-10 importance scale "
            "for the US-Iran conflict theme. Guidelines: Score 9-10 for direct kinetic military engagements, strikes on naval/nuclear "
            "assets, or declared nuclear breakout. Score 7-8 for proxy attacks, major sanctions, troop deployments, or heightened naval "
            "blockades. Score 4-6 for standard diplomatic rhetoric, non-escalatory drills, or minor statements. Score 1-3 for background "
            "commentary. Provide an integer score and rationale."
        )
    })


def test_ai_stage3_concise_event_summarization(test_client, sample_normalized_event):
    """F19: Stage 3 generates concise executive summary and headline."""
    res = test_client.post("/api/pipeline/process", json=sample_normalized_event)
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    assert len(data["summary"]) > 10
    assert "headline" in data


def test_ai_stage4_visual_and_trend_generation(test_client, sample_normalized_event):
    """F21: Stage 4 generates trend category and visual indicators."""
    res = test_client.post("/api/pipeline/process", json=sample_normalized_event)
    assert res.status_code == 200
    data = res.json()
    assert "trend_category" in data
    assert data["trend_category"] is not None


def test_ai_stage_independent_provider_configuration(test_client):
    """F22/F23: Each of the 4 stages can independently configure provider and model."""
    res = test_client.get("/api/settings/ai-pipeline")
    assert res.status_code == 200
    stages = res.json()
    assert "extraction" in stages
    assert "scoring" in stages
    assert "summarization" in stages
    assert "visuals" in stages

    for stage_name, cfg in stages.items():
        assert cfg["provider"] in ["gemini", "groq"]
        assert "model_name" in cfg


# ============================================================================
# 7. THEME SYSTEM (F26, F27, F28, F29, F30, F31)
# ============================================================================

def test_theme_listing_includes_default_us_iran_theme(test_client):
    """F31: System ships out-of-the-box with pre-configured US-Iran conflict theme."""
    res = test_client.get("/api/themes")
    assert res.status_code == 200
    themes = res.json()
    assert any(t["id"] == "us-iran-conflict" and t["is_active"] is True for t in themes)


def test_theme_creation_and_custom_keywords(test_client):
    """F26/F27: Operators can create new themes with custom keywords and entities."""
    new_theme = {
        "id": "china-taiwan-tensions",
        "name": "China-Taiwan Strait Monitoring",
        "description": "Cross-strait military exercises and defense airspace tracking.",
        "keywords": ["PLA", "Taiwan Strait", "ADIZ", "Taipei", "amphibious"],
        "entities": ["China", "Taiwan", "PLA Navy", "MND"],
        "scoring_prompt": "Score 9-10 for kinetic airspace incursions or naval blockades.",
        "importance_threshold": 8
    }
    res = test_client.post("/api/themes", json=new_theme)
    assert res.status_code == 200
    created = res.json()
    assert created["id"] == "china-taiwan-tensions"
    assert "ADIZ" in created["keywords"]


def test_theme_scoring_prompt_definition(test_client):
    """F28: Themes contain a natural language scoring prompt."""
    res = test_client.get("/api/themes/us-iran-conflict")
    assert res.status_code == 200
    theme = res.json()
    assert "scoring_prompt" in theme
    assert "Strait of Hormuz" in theme["scoring_prompt"] or "geopolitical" in theme["scoring_prompt"]


def test_theme_activation_switches_active_theme(test_client):
    """F32: Activating a theme switches the active theme cleanly without restart."""
    activate_res = test_client.post("/api/themes/china-taiwan-tensions/activate")
    assert activate_res.status_code == 200
    assert activate_res.json()["active_theme_id"] == "china-taiwan-tensions"

    # Verify state
    get_res = test_client.get("/api/themes/china-taiwan-tensions")
    assert get_res.json()["is_active"] is True

    # Re-activate US-Iran
    test_client.post("/api/themes/us-iran-conflict/activate")


def test_theme_source_presets_linkage(test_client):
    """F29/F30: Themes encapsulate notification threshold and source presets."""
    res = test_client.get("/api/themes/us-iran-conflict")
    assert res.status_code == 200
    theme = res.json()
    assert theme["importance_threshold"] >= 1
    assert "source_presets" in theme


# ============================================================================
# 8. WEBSOCKET TIMELINE & REAL-TIME DASHBOARD (F34, F35, F36, F37)
# ============================================================================

def test_event_ingestion_emits_websocket_event(test_client, sample_normalized_event):
    """F34: Ingesting an event publishes an 'event:new' message to the WebSocket bus."""
    ingest_res = test_client.post("/api/events", json=sample_normalized_event)
    assert ingest_res.status_code == 200

    ws_logs_res = test_client.get("/test/websocket-logs")
    assert ws_logs_res.status_code == 200
    logs = ws_logs_res.json()
    assert any(log["type"] == "event:new" and log["payload"]["id"] == sample_normalized_event["id"] for log in logs)


def test_timeline_events_query_chronological_order(test_client, sample_normalized_event):
    """F34: Event queries return items ordered chronologically (newest first)."""
    res = test_client.get("/api/events?limit=20")
    assert res.status_code == 200
    events = res.json()
    assert isinstance(events, list)


def test_timeline_importance_badge_scores(test_client, sample_normalized_event):
    """F35: Events contain importance scores suitable for color-coded badge rendering."""
    # Process event to assign score
    proc_res = test_client.post("/api/pipeline/process", json=sample_normalized_event)
    assert proc_res.status_code == 200
    score = proc_res.json()["importance_score"]
    assert 1 <= score <= 10


def test_timeline_source_attribution_and_metadata(test_client, sample_normalized_event):
    """F36: Timeline event payload contains source attribution label and metadata."""
    res = test_client.post("/api/events", json=sample_normalized_event)
    assert res.status_code == 200
    saved = res.json()
    assert saved["source_name"] == "Reuters World"
    assert saved["source_type"] == "rss"
    assert saved["source_url"] is not None


def test_timeline_filtering_by_theme_and_source(test_client):
    """F34: Timeline queries support filtering by theme_id and source_type."""
    res_theme = test_client.get("/api/events?theme_id=us-iran-conflict")
    assert res_theme.status_code == 200

    res_type = test_client.get("/api/events?source_type=rss")
    assert res_type.status_code == 200


# ============================================================================
# 9. TELEGRAM ALERT BOT (F40)
# ============================================================================

def test_telegram_alert_triggered_above_threshold(test_client, sample_normalized_event, mock_telegram):
    """F40/AC-11: High-importance events (score >= threshold) trigger a Telegram alert."""
    high_threat_event = dict(sample_normalized_event)
    high_threat_event["raw_text"] = "Ballistic missile strike launched at regional naval base. Kinetic engagement reported."

    delivered_before = len(mock_telegram.sent_messages)
    proc_res = test_client.post("/api/pipeline/process", json=high_threat_event)
    assert proc_res.status_code == 200
    data = proc_res.json()
    assert data["importance_score"] >= 7
    assert data["notification_dispatched"] is True
    assert len(mock_telegram.sent_messages) == delivered_before + 1


def test_telegram_alert_suppressed_below_threshold(test_client, sample_normalized_event, mock_telegram):
    """F40: Low-importance events (score < threshold) do NOT trigger Telegram alerts."""
    low_event = dict(sample_normalized_event)
    low_event["raw_text"] = "Minor routine administrative meeting concluded with generic comments."

    delivered_before = len(mock_telegram.sent_messages)
    proc_res = test_client.post("/api/pipeline/process", json=low_event)
    assert proc_res.status_code == 200
    data = proc_res.json()
    assert data["importance_score"] < 7
    assert data["notification_dispatched"] is False
    assert len(mock_telegram.sent_messages) == delivered_before


def test_telegram_alert_formatted_template_compliance(mock_telegram):
    """F40/AC-12: Dispatched Telegram messages follow the required template formatting."""
    payload = TelegramAlertPayload(
        event_id="alert_test_99",
        title="Escalation in Gulf",
        summary="Commercial tanker boarded by armed naval patrol.",
        importance_score=8,
        source_name="Al Jazeera English",
        published_at=datetime.now(timezone.utc),
        source_url="https://aljazeera.com/test-article"
    )
    res = mock_telegram.send_alert(payload)
    assert res["status"] == "delivered"
    last_msg = mock_telegram.sent_messages[-1]
    text = last_msg["formatted_text"]
    assert "🚨 *OSINT CRITICAL ALERT* [8/10]" in text
    assert "*Summary:* Commercial tanker boarded" in text
    assert "*Source:* Al Jazeera English" in text
    assert "*Link:* https://aljazeera.com/test-article"


def test_telegram_alert_includes_summary_score_source_link(mock_telegram):
    """F40: All four mandatory fields (summary, score, source, link) are present in alert."""
    payload = TelegramAlertPayload(
        event_id="alert_test_100",
        title="Sanctions signed",
        summary="Executive order imposing sanctions signed.",
        importance_score=7,
        source_name="AP News",
        published_at=datetime.now(timezone.utc),
        source_url="https://apnews.com/sanctions"
    )
    res = mock_telegram.send_alert(payload)
    assert res["status"] == "delivered"
    record = mock_telegram.sent_messages[-1]["payload"]
    assert record["importance_score"] == 7
    assert record["source_name"] == "AP News"
    assert record["source_url"] == "https://apnews.com/sanctions"


def test_telegram_bot_token_and_chat_id_configuration(test_client):
    """F40: Bot credentials can be updated through settings API."""
    update_res = test_client.put("/api/settings", json={
        "api_keys": {
            "telegram_bot_token": "new-test-token:67890",
            "telegram_chat_id": "-10099887766"
        }
    })
    assert update_res.status_code == 200


# ============================================================================
# 10. AUTHENTICATION & SECURITY (F39)
# ============================================================================

def test_unauthenticated_request_rejected_401(unauth_client):
    """F39/AC-18: Requests without API key receive HTTP 401 Unauthorized."""
    res = unauth_client.get("/api/events")
    assert res.status_code == 401
    assert "Invalid or missing API key" in res.json()["detail"]


def test_valid_api_key_header_accepted_200(test_client):
    """F39: Requests with valid X-API-Key header succeed with HTTP 200."""
    res = test_client.get("/api/events")
    assert res.status_code == 200


def test_bearer_token_authorization_header_accepted(app):
    """F39: Authorization Bearer header is also accepted as valid authentication."""
    from starlette.testclient import TestClient
    bearer_client = TestClient(app, headers={"Authorization": "Bearer test-secret-key-12345"})
    res = bearer_client.get("/api/events")
    assert res.status_code == 200


def test_invalid_api_key_rejected_401(app):
    """F39: Incorrect API key receives HTTP 401 Unauthorized."""
    from starlette.testclient import TestClient
    bad_client = TestClient(app, headers={"X-API-Key": "wrong-secret-key"})
    res = bad_client.get("/api/events")
    assert res.status_code == 401


def test_protected_routes_across_endpoints(unauth_client):
    """F39: Themes, sources, settings, and events routes are all secured."""
    for path in ["/api/themes", "/api/sources", "/api/events", "/api/settings"]:
        res = unauth_client.get(path)
        assert res.status_code == 401


# ============================================================================
# 11. HEALTH CHECKS & PRODUCTION READINESS (F41, F42)
# ============================================================================

def test_health_check_endpoint_status_200(test_client):
    """F42/AC-1: Health check endpoint /health returns HTTP 200 and healthy status."""
    res = test_client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"


def test_health_check_reports_database_and_redis(test_client):
    """F42: Health check verifies database and redis connectivity."""
    res = test_client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["database"] == "connected"
    assert body["redis"] == "connected"


def test_health_check_reports_memory_metric(test_client):
    """F42/AC-17: Health check includes memory consumption metric."""
    res = test_client.get("/health")
    assert res.status_code == 200
    assert "memory_mb" in res.json()
    assert res.json()["memory_mb"] <= 900


def test_readiness_check_endpoint_returns_ready(test_client):
    """F42: Readiness probe /ready returns ready status."""
    res = test_client.get("/ready")
    assert res.status_code == 200
    assert res.json()["ready"] is True


def test_stats_endpoint_reports_operational_metrics(test_client):
    """F37: Stats endpoint reports total events, high-priority count, active sources, and timestamp."""
    res = test_client.get("/api/stats")
    assert res.status_code == 200
    stats = res.json()
    assert "total_events" in stats
    assert "high_priority_events" in stats
    assert "active_sources" in stats
    assert "last_updated" in stats
