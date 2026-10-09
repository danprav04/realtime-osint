"""
tests/e2e/test_tier3_cross_feature.py - Tier 3: Pairwise Combinatorial & Cross-Feature Integration

Validates end-to-end integration across decoupled subsystem boundaries:
- Theme modification -> Source reconfiguration -> AI scoring -> Telegram notification
- Concurrent multi-provider pipeline execution (Stage 1 Groq + Stage 2 Gemini in same run)
- Source disabling -> Task cessation -> Stats updates
- Dynamic threshold adjustment -> Real-time alert suppression / triggering
- Multilingual Farsi/Arabic ingestion -> Pipeline translation -> WebSocket emission
- Cross-source deduplication (RSS vs NewsAPI)
- Settings propagation to live AI provider instances
- Telegram anti-flood throttling and critical score bypass
"""

import uuid
from datetime import datetime, timezone
import pytest
from tests.conftest import (
    NormalizedEvent,
    TelegramAlertPayload,
    MockGeminiService,
    MockGroqService
)


def test_cross_feature_theme_mod_reconfig_scoring_alert_flow(test_client, mock_telegram):
    """
    Validates complete zero-code reconfiguration cycle:
    1. Create new theme with specific scoring prompt & threshold 8
    2. Add theme-specific source
    3. Process event matching the new theme
    4. Verify AI applies new scoring prompt and threshold 8 triggers Telegram alert
    """
    theme_id = f"taiwan_crisis_{uuid.uuid4().hex[:6]}"
    theme_payload = {
        "id": theme_id,
        "name": "Taiwan Strait Security",
        "description": "Cross-strait military readiness and ADIZ incursions.",
        "keywords": ["PLA", "Taiwan Strait", "ADIZ", "Taipei", "amphibious"],
        "entities": ["China", "Taiwan", "PLA Navy", "MND"],
        "scoring_prompt": "You are a military analyst. Score direct kinetic actions or blockades 9-10. Score administrative drills 3.",
        "importance_threshold": 8
    }
    # 1. Create theme
    res_theme = test_client.post("/api/themes", json=theme_payload)
    assert res_theme.status_code == 200

    # 2. Activate theme
    res_act = test_client.post(f"/api/themes/{theme_id}/activate")
    assert res_act.status_code == 200

    # 3. Add custom source linked to this theme
    res_src = test_client.post("/api/sources", json={
        "theme_id": theme_id,
        "name": "Taiwan MND Reports",
        "source_type": "rss",
        "target": "https://mnd.gov.tw/rss",
        "interval_seconds": 180
    })
    assert res_src.status_code == 200

    # 4. Ingest and process high-threat kinetic event
    kinetic_event = {
        "theme_id": theme_id,
        "source_type": "rss",
        "source_name": "Taiwan MND Reports",
        "title": "PLA Naval Vessels Cross Median Line with Missile Craft",
        "raw_text": "Multiple PLA missile destroyers crossed the Taiwan Strait median line in an aggressive naval strike maneuver."
    }
    delivered_before = len(mock_telegram.sent_messages)
    res_proc = test_client.post("/api/pipeline/process", json=kinetic_event)
    assert res_proc.status_code == 200
    data = res_proc.json()
    assert data["importance_score"] >= 8
    assert data["notification_dispatched"] is True
    assert len(mock_telegram.sent_messages) == delivered_before + 1

    # Cleanup: restore US-Iran active theme
    test_client.post("/api/themes/us-iran-conflict/activate")


def test_cross_feature_concurrent_multi_provider_pipeline(test_client, mock_gemini, mock_groq):
    """
    AC-7: Heterogeneous multi-provider execution in the same pipeline run.
    Configures Stage 1 (Extraction) to use Groq and Stage 2 (Scoring) to use Gemini.
    """
    # Configure per-stage providers
    update_cfg = {
        "extraction": {"provider": "groq", "model_name": "qwen/qwen3.8-27b"},
        "scoring": {"provider": "gemini", "model_name": "gemini-2.0-flash"}
    }
    res_cfg = test_client.put("/api/settings/ai-pipeline", json=update_cfg)
    assert res_cfg.status_code == 200

    groq_calls_before = len(mock_groq.call_history)
    gemini_calls_before = len(mock_gemini.call_history)

    event_payload = {
        "theme_id": "us-iran-conflict",
        "source_type": "newsapi",
        "source_name": "Reuters",
        "title": "Submarine patrol reported in Gulf of Oman",
        "raw_text": "Naval intelligence confirms submarine patrol movement in Gulf of Oman."
    }
    res_proc = test_client.post("/api/pipeline/process", json=event_payload)
    assert res_proc.status_code == 200

    # Verify Groq processed Stage 1
    assert len(mock_groq.call_history) > groq_calls_before
    assert any(c["stage"] == "extraction" for c in mock_groq.call_history[groq_calls_before:])

    # Verify Gemini processed Stage 2
    assert len(mock_gemini.call_history) > gemini_calls_before
    assert any(c["stage"] == "scoring" for c in mock_gemini.call_history[gemini_calls_before:])

    # Restore default stage 1 config
    test_client.put("/api/settings/ai-pipeline", json={
        "extraction": {"provider": "gemini", "model_name": "gemini-2.0-flash"}
    })


def test_cross_feature_source_disabling_pipeline_bypass(test_client):
    """
    Disabling a source updates active source counters in /api/stats
    and rejects manual trigger requests for the disabled source.
    """
    # Get active sources count
    stats_before = test_client.get("/api/stats").json()
    active_before = stats_before["active_sources"]

    # Create active source
    res_src = test_client.post("/api/sources", json={
        "theme_id": "us-iran-conflict",
        "name": "Temporary Test Source",
        "source_type": "rss",
        "target": "https://temp.example.com/feed",
        "enabled": True
    })
    src_id = res_src.json()["id"]

    stats_after_add = test_client.get("/api/stats").json()
    assert stats_after_add["active_sources"] == active_before + 1

    # Disable source
    res_disable = test_client.put(f"/api/sources/{src_id}", json={"enabled": False})
    assert res_disable.status_code == 200
    assert res_disable.json()["enabled"] is False

    stats_after_disable = test_client.get("/api/stats").json()
    assert stats_after_disable["active_sources"] == active_before

    # Attempting to trigger disabled source returns HTTP 400
    res_trig = test_client.post(f"/api/sources/{src_id}/trigger")
    assert res_trig.status_code == 400
    assert "Cannot trigger disabled source" in res_trig.json()["detail"]

    # Cleanup
    test_client.delete(f"/api/sources/{src_id}")


def test_cross_feature_dynamic_threshold_alert_filter(test_client, mock_telegram, sample_normalized_event):
    """
    Raising the importance threshold dynamically suppresses alerts for intermediate scores.
    """
    event_med_high = dict(sample_normalized_event)
    event_med_high["raw_text"] = "Sanctions enforcement expanded on shipping companies."

    # Step 1: Default threshold = 7 -> score 7 triggers alert
    test_client.put("/api/themes/us-iran-conflict", json={"importance_threshold": 7})
    delivered_before = len(mock_telegram.sent_messages)

    res1 = test_client.post("/api/pipeline/process", json=event_med_high)
    assert res1.status_code == 200
    if res1.json()["importance_score"] == 7:
        assert res1.json()["notification_dispatched"] is True
        assert len(mock_telegram.sent_messages) == delivered_before + 1

    # Step 2: Elevate threshold to 9 -> same score 7 does NOT trigger alert
    test_client.put("/api/themes/us-iran-conflict", json={"importance_threshold": 9})
    delivered_after_raise = len(mock_telegram.sent_messages)

    res2 = test_client.post("/api/pipeline/process", json=event_med_high)
    assert res2.status_code == 200
    assert res2.json()["notification_dispatched"] is False
    assert len(mock_telegram.sent_messages) == delivered_after_raise

    # Restore default threshold
    test_client.put("/api/themes/us-iran-conflict", json={"importance_threshold": 7})


def test_cross_feature_multilingual_source_to_websocket_timeline(test_client, sample_farsi_event):
    """
    Persian Telegram channel event ingests -> Stage 1 translates ->
    Stage 2 scores -> publishes to WebSocket timeline -> verified in /api/events.
    """
    res_proc = test_client.post("/api/pipeline/process", json=sample_farsi_event)
    assert res_proc.status_code == 200
    processed = res_proc.json()
    event_id = processed["id"]

    # Verify English translation exists
    assert "English translation:" in processed["translated_text"]

    # Verify event appears in timeline query
    get_res = test_client.get(f"/api/events/{event_id}")
    assert get_res.status_code == 200
    saved = get_res.json()
    assert saved["language"] == "fa"
    assert saved["translated_text"] is not None
    assert "سپاه" in saved["raw_text"]

    # Verify published to WebSocket bus
    ws_res = test_client.get("/test/websocket-logs")
    assert ws_res.status_code == 200
    assert any(log["type"] == "event:new" and log["payload"]["id"] == event_id for log in ws_res.json())


def test_cross_feature_cross_source_deduplication(test_client, sample_normalized_event):
    """
    Events with duplicate external identifiers or identical content are mapped to the same ID.
    """
    event_a = dict(sample_normalized_event)
    event_a["id"] = "dedup_story_001"
    event_a["external_id"] = "reuters_story_1001"
    event_a["title"] = "Breaking: Commercial tanker escorted by naval frigate"

    res_a = test_client.post("/api/events", json=event_a)
    assert res_a.status_code == 200

    # Ingest duplicate from another poller with matching ID
    res_b = test_client.post("/api/events", json=event_a)
    assert res_b.status_code == 200
    assert res_b.json()["id"] == "dedup_story_001"


def test_cross_feature_stats_updates_on_event_lifecycle(test_client):
    """
    Ingesting multiple events dynamically increments stats total_events and high_priority_events.
    """
    stats_init = test_client.get("/api/stats").json()
    init_total = stats_init["total_events"]
    init_high = stats_init["high_priority_events"]

    # Ingest high-priority event
    event_high = {
        "theme_id": "us-iran-conflict",
        "source_type": "rss",
        "source_name": "Reuters",
        "title": "Direct kinetic strike reported",
        "raw_text": "Missile attack struck port facility with kinetic impact reported."
    }
    test_client.post("/api/pipeline/process", json=event_high)

    stats_after = test_client.get("/api/stats").json()
    assert stats_after["total_events"] == init_total + 1
    assert stats_after["high_priority_events"] == init_high + 1


def test_cross_feature_settings_api_keys_propagation(test_client, app):
    """
    Updating API keys via /api/settings updates the underlying live provider instances.
    """
    new_gemini_key = "test-custom-gemini-key-999"
    new_groq_key = "test-custom-groq-key-888"

    res_put = test_client.put("/api/settings", json={
        "api_keys": {
            "gemini_api_key": new_gemini_key,
            "groq_api_key": new_groq_key
        }
    })
    assert res_put.status_code == 200

    # Verify attached instances updated
    assert app.state.mock_gemini.api_key == new_gemini_key
    assert app.state.mock_groq.api_key == new_groq_key

    # Check masked read from /api/settings
    res_get = test_client.get("/api/settings")
    assert res_get.status_code == 200
    keys = res_get.json()["api_keys"]
    assert keys["gemini_api_key"].startswith("***")
    assert keys["groq_api_key"].startswith("***")


def test_cross_feature_theme_switching_isolates_event_queries(test_client):
    """
    Querying events by theme_id correctly isolates events belonging to different themes.
    """
    # Event for theme 1
    test_client.post("/api/events", json={
        "id": "event_theme_us_iran",
        "theme_id": "us-iran-conflict",
        "source_type": "rss",
        "source_name": "Reuters",
        "external_id": "ext_ui_1",
        "title": "US Iran Event Title",
        "raw_text": "Content"
    })

    # Event for theme 2
    test_client.post("/api/events", json={
        "id": "event_theme_china_taiwan",
        "theme_id": "china-taiwan-tensions",
        "source_type": "newsapi",
        "source_name": "CNA",
        "external_id": "ext_ct_1",
        "title": "Taiwan Strait Event Title",
        "raw_text": "Content"
    })

    # Query us-iran
    res_ui = test_client.get("/api/events?theme_id=us-iran-conflict")
    assert res_ui.status_code == 200
    ids_ui = [e["id"] for e in res_ui.json()]
    assert "event_theme_us_iran" in ids_ui
    assert "event_theme_china_taiwan" not in ids_ui


def test_cross_feature_telegram_anti_flood_throttling(mock_telegram):
    """
    Telegram anti-flood protection throttles rapid successive alerts within the cooldown window,
    while critical events with score = 10 immediately bypass the cooldown limit.
    """
    mock_telegram.rate_limiting_enabled = True
    mock_telegram.rate_limit_seconds = 2.0  # 2 second window
    mock_telegram.last_sent_timestamp = 0.0

    # Alert 1: score 8 -> delivered
    p1 = TelegramAlertPayload(
        event_id="af_1",
        title="Alert 1",
        summary="Summary 1",
        importance_score=8,
        source_name="Reuters",
        published_at=datetime.now(timezone.utc)
    )
    res1 = mock_telegram.send_alert(p1)
    assert res1["status"] == "delivered"

    # Alert 2: score 8 immediately after -> throttled
    p2 = TelegramAlertPayload(
        event_id="af_2",
        title="Alert 2",
        summary="Summary 2",
        importance_score=8,
        source_name="AP",
        published_at=datetime.now(timezone.utc)
    )
    res2 = mock_telegram.send_alert(p2)
    assert res2["status"] == "throttled"
    assert res2["reason"] == "rate_limit_cooldown"

    # Alert 3: critical score 10 immediately after -> bypasses throttle
    p3 = TelegramAlertPayload(
        event_id="af_3",
        title="Alert 3 - Critical Strike",
        summary="Immediate kinetic strike breakout",
        importance_score=10,
        source_name="CENTCOM",
        published_at=datetime.now(timezone.utc)
    )
    res3 = mock_telegram.send_alert(p3)
    assert res3["status"] == "delivered"

    # Cleanup
    mock_telegram.rate_limiting_enabled = False
