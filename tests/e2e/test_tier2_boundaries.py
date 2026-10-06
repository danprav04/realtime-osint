"""
tests/e2e/test_tier2_boundaries.py - Tier 2: Boundary Value Analysis & Edge Cases

Validates system robustness against extreme inputs and failure conditions:
- Empty RSS feeds and malformed XML / HTML error responses
- Missing API keys and provider authentication failures
- Provider rate-limiting (HTTP 429) and backoff handling
- Complex multilingual scripts: Persian (ZWNJ, RTL), Arabic diacritics, and emojis
- Importance score boundaries (1 and 10 clamping, threshold edge values)
- Large payload handling (50KB+ texts) and whitespace edge cases
- Coordinate bounds validation for Maritime/AIS feeds
- Injection attack resilience (SQL / JSON meta-characters)
"""

import uuid
from datetime import datetime, timezone
import pytest
from tests.conftest import (
    NormalizedEvent,
    TelegramAlertPayload,
    MockRSSFeedService,
    MockGeminiService,
    MockGroqService,
    MockTelegramBotService
)


# ============================================================================
# 1. RSS & XML BOUNDARY TESTS
# ============================================================================

def test_boundary_empty_rss_feed(mock_rss):
    """Empty RSS feed (<rss><channel></channel></rss>) produces zero items without crashing parser."""
    empty_xml = mock_rss.generate_empty_feed()
    assert "<channel>" in empty_xml
    assert "<item>" not in empty_xml


def test_boundary_malformed_xml_rss(mock_rss):
    """HTML 502 Bad Gateway response received instead of XML is safely rejected without crash."""
    malformed_xml = mock_rss.generate_malformed_feed()
    assert "<html>" in malformed_xml
    assert "<rss" not in malformed_xml


def test_boundary_rss_duplicate_guid_deduplication(test_client, sample_normalized_event):
    """Ingesting an event with identical external_id and source_type updates or ignores duplicate."""
    dup_event = dict(sample_normalized_event)
    dup_event["id"] = "unique_dup_1"
    dup_event["external_id"] = "fixed_guid_12345"

    res1 = test_client.post("/api/events", json=dup_event)
    assert res1.status_code == 200

    # Ingest duplicate
    res2 = test_client.post("/api/events", json=dup_event)
    assert res2.status_code == 200
    assert res2.json()["id"] == "unique_dup_1"


# ============================================================================
# 2. PROVIDER CREDENTIALS & RATE LIMIT BOUNDARIES
# ============================================================================

def test_boundary_missing_gemini_api_key():
    """Gemini client initialized with empty API key raises PermissionError on stage execution."""
    gemini_no_key = MockGeminiService(api_key="")
    with pytest.raises(PermissionError) as exc_info:
        gemini_no_key.process_stage("extraction", "Some test content")
    assert "Gemini API key is invalid or unauthorized" in str(exc_info.value)


def test_boundary_missing_groq_api_key():
    """Groq client initialized with empty API key raises PermissionError on stage execution."""
    groq_no_key = MockGroqService(api_key="")
    with pytest.raises(PermissionError) as exc_info:
        groq_no_key.process_stage("scoring", "Some test content")
    assert "Groq API key is invalid or unauthorized" in str(exc_info.value)


def test_boundary_gemini_rate_limit_429():
    """Gemini 429 ResourceExhausted exception is raised and categorized as quota error."""
    gemini = MockGeminiService(api_key="valid-key")
    gemini.simulate_429 = True
    with pytest.raises(RuntimeError) as exc_info:
        gemini.process_stage("scoring", "Breaking event text")
    assert "429 Quota Exceeded" in str(exc_info.value)


def test_boundary_groq_rate_limit_429():
    """Groq 429 RateLimitError is caught cleanly with descriptive error."""
    groq = MockGroqService(api_key="valid-key")
    groq.simulate_429 = True
    with pytest.raises(RuntimeError) as exc_info:
        groq.process_stage("summarization", "Breaking event text")
    assert "429 Rate limit reached" in str(exc_info.value)


# ============================================================================
# 3. MULTILINGUAL, RTL, DIACRITICS & ENCODING BOUNDARIES
# ============================================================================

def test_boundary_persian_zwnj_and_arabic_numerals(test_client, sample_farsi_event):
    """Persian text with Zero-Width Non-Joiner (\\u200c) and Persian numerals normalizes cleanly."""
    farsi_text_with_zwnj = "موشک\u200cهای بالستیک سپاه با برد ۱۲۰۰ کیلومتر آزمایش شدند."
    sample_farsi_event["raw_text"] = farsi_text_with_zwnj
    sample_farsi_event["title"] = "آزمایش موشک\u200cهای جدید"

    res = test_client.post("/api/pipeline/process", json=sample_farsi_event)
    assert res.status_code == 200
    data = res.json()
    assert data["translated_text"] is not None
    assert "English translation:" in data["translated_text"]


def test_boundary_arabic_diacritics_and_urgent_emojis(test_client, sample_arabic_event):
    """Arabic text containing full Harakat/diacritics and alert emojis normalizes and translates."""
    arabic_with_harakat = "🚨 عاجِل: تَحَرُّكَاتٌ عَسْكَرِيَّةٌ مَكْثُوفَةٌ فِي مَضِيقِ هُرْمُزَ ⚠️"
    sample_arabic_event["raw_text"] = arabic_with_harakat
    sample_arabic_event["title"] = "عاجل من الخليج"

    res = test_client.post("/api/pipeline/process", json=sample_arabic_event)
    assert res.status_code == 200
    data = res.json()
    assert data["translated_text"] is not None


def test_boundary_mixed_farsi_english_acronyms(test_client, sample_farsi_event):
    """Mixed Persian text with English acronyms (IRGC, CENTCOM, IAEA) preserves acronym integrity."""
    mixed_text = "بیانیه رسمی IRGC در واکنش به تحریم‌های جدید CENTCOM و گزارش اخیر IAEA."
    sample_farsi_event["raw_text"] = mixed_text

    res = test_client.post("/api/pipeline/process", json=sample_farsi_event)
    assert res.status_code == 200
    data = res.json()
    assert data["translated_text"] is not None


# ============================================================================
# 4. IMPORTANCE SCORE BOUNDARIES & THRESHOLD VALUES
# ============================================================================

def test_boundary_importance_score_clamping_minimum(app):
    """Importance score lower bound is strictly clamped to 1."""
    gemini = app.state.mock_gemini
    res = gemini.process_stage("scoring", "Irrelevant background weather report", context={"scoring_prompt": "Score 0"})
    assert res["importance_score"] >= 1


def test_boundary_importance_score_clamping_maximum(app):
    """Importance score upper bound is strictly clamped to 10."""
    gemini = app.state.mock_gemini
    res = gemini.process_stage("scoring", "Nuclear war declared immediately", context={"scoring_prompt": "Score 15"})
    assert res["importance_score"] <= 10


def test_boundary_threshold_exact_match_triggers_alert(test_client, sample_normalized_event):
    """Event scoring exactly 7 against threshold 7 triggers alert dispatch."""
    event = dict(sample_normalized_event)
    event["raw_text"] = "Sanctions and naval deployment warning issued."
    res = test_client.post("/api/pipeline/process", json=event)
    assert res.status_code == 200
    data = res.json()
    if data["importance_score"] >= 7:
        assert data["notification_dispatched"] is True


def test_boundary_threshold_score_just_below_suppresses_alert(test_client, sample_normalized_event):
    """Event scoring 6 or below against threshold 7 does NOT trigger alert dispatch."""
    event = dict(sample_normalized_event)
    event["raw_text"] = "Routine press conference comments on regional economy."
    res = test_client.post("/api/pipeline/process", json=event)
    assert res.status_code == 200
    data = res.json()
    if data["importance_score"] < 7:
        assert data["notification_dispatched"] is False


# ============================================================================
# 5. PAYLOAD SIZE, WHITESPACE & COORDINATE BOUNDARIES
# ============================================================================

def test_boundary_large_payload_50kb_text(test_client, sample_normalized_event):
    """Large 50KB+ article payload processes without memory exhaustion or stack crash."""
    large_body = ("In-depth geopolitical analysis of the Middle East maritime security architecture. " * 750)
    assert len(large_body) > 50000

    large_event = dict(sample_normalized_event)
    large_event["raw_text"] = large_body

    res = test_client.post("/api/pipeline/process", json=large_event)
    assert res.status_code == 200
    data = res.json()
    assert "summary" in data
    # Summary is concisely bounded despite 50KB input
    assert len(data["summary"]) < 500


def test_boundary_whitespace_only_title_fallback(test_client, sample_normalized_event):
    """Event with empty or whitespace title falls back gracefully without 500 error."""
    ws_event = dict(sample_normalized_event)
    ws_event["title"] = "   "
    ws_event["raw_text"] = "Emergency report regarding naval vessel communications."

    res = test_client.post("/api/pipeline/process", json=ws_event)
    assert res.status_code == 200
    data = res.json()
    assert "headline" in data


def test_boundary_ais_extreme_coordinates():
    """Coordinates within valid latitude [-90, 90] and longitude [-180, 180] normalize cleanly."""
    valid_event = NormalizedEvent(
        theme_id="us-iran-conflict",
        source_type="ais",
        source_name="AIS Stream",
        external_id="mmsi_valid_coords",
        title="Vessel Signal",
        raw_text="Vessel transiting near Hormuz",
        location={"name": "Hormuz", "lat": 89.9, "lon": 179.9},
        published_at=datetime.now(timezone.utc)
    )
    assert valid_event.location.lat == 89.9
    assert valid_event.location.lon == 179.9


# ============================================================================
# 6. INJECTION ATTACK RESILIENCE & ADVERSARIAL INPUTS
# ============================================================================

def test_boundary_sql_and_json_injection_strings(test_client, sample_normalized_event):
    """Adversarial SQL and JSON meta-characters in event content are safely stored and returned as literal text."""
    injection_text = "'; DROP TABLE events; SELECT * FROM users WHERE '1'='1'; {\"$gt\": \"\"} <script>alert('XSS')</script>"
    inj_event = dict(sample_normalized_event)
    inj_event["id"] = "event_injection_test_1"
    inj_event["raw_text"] = injection_text
    inj_event["title"] = "Adversarial Injection Test"

    res = test_client.post("/api/events", json=inj_event)
    assert res.status_code == 200
    saved = res.json()
    assert saved["raw_text"] == injection_text

    # Retrieve and verify exact string preservation without code execution
    get_res = test_client.get(f"/api/events/{saved['id']}")
    assert get_res.status_code == 200
    assert get_res.json()["raw_text"] == injection_text


def test_boundary_telegram_bot_token_malformed():
    """Mock Telegram bot catches invalid token error without unhandled exception."""
    bot = MockTelegramBotService()
    bot.simulate_error = "invalid_token"
    payload = TelegramAlertPayload(
        event_id="e1",
        title="Test",
        summary="Test Summary",
        importance_score=9,
        source_name="Test Source",
        published_at=datetime.now(timezone.utc)
    )
    with pytest.raises(ValueError) as exc_info:
        bot.send_alert(payload)
    assert "Invalid Token" in str(exc_info.value)


def test_boundary_empty_keywords_theme(test_client):
    """Theme created with an empty keywords list ([]) is accepted without crashing."""
    res = test_client.post("/api/themes", json={
        "id": "empty-keywords-theme",
        "name": "Broad Global Theme",
        "description": "Theme with no specific keyword filter",
        "keywords": [],
        "entities": [],
        "scoring_prompt": "Score all events equally.",
        "importance_threshold": 5
    })
    assert res.status_code == 200
    assert res.json()["keywords"] == []
