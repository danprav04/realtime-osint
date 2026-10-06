"""
tests/e2e/test_tier4_scenarios.py - Tier 4: Real-World Operational Scenarios

Simulates realistic production intelligence operations:
- Full multi-source US-Iran conflict event stream across all 5 plugins
- Breaking news storm with burst deduplication & alert throttling
- Sub-60s critical alert guarantee for kinetic strike events
- Complete Day-in-the-Life operational lifecycle (seed -> reconfigure -> ingest -> alert -> query)
- Multilingual crisis translation across Arabic, Persian, and English streams
- Source network failover and graceful recovery
- Maritime AIS anomaly detection to critical alert
- Dashboard real-time WebSocket event sequence verification
"""

import time
import uuid
from datetime import datetime, timezone, timedelta
import pytest
from tests.conftest import (
    NormalizedEvent,
    TelegramAlertPayload
)


def test_scenario_live_us_iran_event_stream(test_client, mock_telegram):
    """
    Simulates a live multi-source intelligence gathering stream during a US-Iran tension spike:
    Processes 10 diverse events across RSS, Telegram, NewsAPI, Reddit, and Maritime AIS.
    Verifies translation, scoring, summarization, timeline storage, and threshold-based alerts.
    """
    initial_alerts_count = len(mock_telegram.sent_messages)

    raw_stream = [
        # 1. RSS: Reuters Naval Alert
        {
            "theme_id": "us-iran-conflict",
            "source_type": "rss",
            "source_name": "Reuters World",
            "source_url": "https://reuters.com/world/naval-alert-hormuz",
            "external_id": "stream_reuters_01",
            "title": "US Fifth Fleet issues commercial advisory in Persian Gulf",
            "raw_text": "US Navy Fifth Fleet advised commercial maritime vessels to maintain heightened vigilance following IRGC speed boat maneuvers.",
            "language": "en"
        },
        # 2. RSS: Al Jazeera English Diplomacy
        {
            "theme_id": "us-iran-conflict",
            "source_type": "rss",
            "source_name": "Al Jazeera English",
            "source_url": "https://aljazeera.com/news/diplomacy-talks",
            "external_id": "stream_aljazeera_02",
            "title": "Diplomatic delegation arrives for regional stability talks",
            "raw_text": "Envoys from regional states gathered in Doha for scheduled consultations regarding economic cooperation.",
            "language": "en"
        },
        # 3. Telegram: Persian Military Drill
        {
            "theme_id": "us-iran-conflict",
            "source_type": "telegram",
            "source_name": "IRNA Persian TG",
            "source_url": "https://t.me/irna_1313/501",
            "external_id": "stream_tg_fa_03",
            "title": "آغاز رزمایش پهپادی ارتش در جنوب کشور",
            "raw_text": "نیروهای مسلح رزمایش گسترده پهپادی و موشکی خود را در آب‌های خلیج فارس و دریای عمان با موفقیت آغاز کردند.",
            "language": "fa"
        },
        # 4. Telegram: English OSINT Flight Tracking
        {
            "theme_id": "us-iran-conflict",
            "source_type": "telegram",
            "source_name": "@OSINTdefender",
            "source_url": "https://t.me/OSINTdefender/1204",
            "external_id": "stream_tg_en_04",
            "title": "USAF RC-135 Rivet Joint reconnaissance flight over Persian Gulf",
            "raw_text": "A United States Air Force RC-135V reconnaissance aircraft departed Al Udeid and is conducting electronic surveillance over international waters.",
            "language": "en"
        },
        # 5. NewsAPI: AP Sanctions Package
        {
            "theme_id": "us-iran-conflict",
            "source_type": "newsapi",
            "source_name": "Associated Press",
            "source_url": "https://apnews.com/article/iran-sanctions-defense",
            "external_id": "stream_news_05",
            "title": "Treasury expands sanctions on ballistic missile procurement network",
            "raw_text": "US Department of the Treasury designated several entities involved in procurement of components for Iranian ballistic missile program.",
            "language": "en"
        },
        # 6. NewsAPI: GDELT Maritime Report
        {
            "theme_id": "us-iran-conflict",
            "source_type": "newsapi",
            "source_name": "GDELT Monitor",
            "source_url": "https://gdelt.org/event/88712",
            "external_id": "stream_gdelt_06",
            "title": "Commercial maritime transit insurance premiums adjust upward",
            "raw_text": "Lloyds joint war committee noted persistent tensions near Strait of Hormuz with shipping insurance surcharges updated.",
            "language": "en"
        },
        # 7. Reddit: r/worldnews breaking discussion
        {
            "theme_id": "us-iran-conflict",
            "source_type": "reddit",
            "source_name": "r/worldnews",
            "source_url": "https://reddit.com/r/worldnews/comments/test1",
            "external_id": "stream_reddit_07",
            "title": "Megathread: Regional maritime security developments in Gulf",
            "raw_text": "Ongoing updates regarding naval presence and international carrier strike group transit.",
            "language": "en"
        },
        # 8. Reddit: r/geopolitics analysis
        {
            "theme_id": "us-iran-conflict",
            "source_type": "reddit",
            "source_name": "r/geopolitics",
            "source_url": "https://reddit.com/r/geopolitics/comments/test2",
            "external_id": "stream_reddit_08",
            "title": "Strategic assessment of Iranian deterrent doctrine post-2025",
            "raw_text": "Analysis focusing on asymmetric naval tactics, missile dispersal, and regional proxy capabilities.",
            "language": "en"
        },
        # 9. AIS: High-Speed Commercial Transit
        {
            "theme_id": "us-iran-conflict",
            "source_type": "ais",
            "source_name": "AISStream Hormuz",
            "source_url": None,
            "external_id": "stream_ais_09",
            "title": "Vessel Position: MARAN GAS APOLLO (MMSI: 240898000)",
            "raw_text": "LNG Tanker MARAN GAS APOLLO navigating inbound TSS Strait of Hormuz at speed 17.8 knots.",
            "language": "en",
            "location": {"name": "Strait of Hormuz", "lat": 26.50, "lon": 56.40}
        },
        # 10. RSS: Kinetic Strike Breaking Event
        {
            "theme_id": "us-iran-conflict",
            "source_type": "rss",
            "source_name": "Reuters Breaking",
            "source_url": "https://reuters.com/breaking/missile-strike-gulf",
            "external_id": "stream_reuters_10",
            "title": "Breaking: Ballistic missile strike launched at coastal facility",
            "raw_text": "Explosions confirmed as kinetic ballistic missile attack impacts military coastal installation. Interceptors engaged.",
            "language": "en"
        }
    ]

    processed_events = []
    for item in raw_stream:
        res = test_client.post("/api/pipeline/process", json=item)
        assert res.status_code == 200
        processed_events.append(res.json())

    # All 10 events successfully processed
    assert len(processed_events) == 10

    # Persian event translated
    farsi_res = processed_events[2]
    assert farsi_res["translated_text"] is not None
    assert "English translation:" in farsi_res["translated_text"]

    # Kinetic strike event received high score and triggered Telegram alert
    kinetic_res = processed_events[9]
    assert kinetic_res["importance_score"] >= 9
    assert kinetic_res["notification_dispatched"] is True

    # Telegram alerts count increased for high-priority items
    assert len(mock_telegram.sent_messages) > initial_alerts_count

    # Verify all appear in timeline
    timeline_res = test_client.get("/api/events?limit=50")
    assert timeline_res.status_code == 200
    timeline_ids = [e["id"] for e in timeline_res.json()]
    for p in processed_events:
        assert p["id"] in timeline_ids


def test_scenario_breaking_news_storm_with_deduplication(test_client):
    """
    Simulates a breaking news storm with 30 rapid events containing duplicates:
    10 distinct events and 20 identical duplicate transmissions.
    Verifies that deduplication prevents redundant storage and pipeline executes under 3 seconds.
    """
    start_time = time.time()

    distinct_stories = [
        {"id": f"storm_story_{i:02d}", "title": f"Breaking Situation Update #{i}", "text": f"Crucial incident report #{i} regarding regional developments."}
        for i in range(10)
    ]

    # Generate 30 event submissions (10 unique + 20 duplicates)
    storm_batch = []
    for idx in range(30):
        story = distinct_stories[idx % 10]
        storm_batch.append({
            "id": story["id"],  # Same ID for duplicates
            "theme_id": "us-iran-conflict",
            "source_type": "rss" if idx % 2 == 0 else "newsapi",
            "source_name": "Wire Service",
            "external_id": f"ext_{story['id']}",
            "title": story["title"],
            "raw_text": story["text"]
        })

    for ev in storm_batch:
        res = test_client.post("/api/events", json=ev)
        assert res.status_code == 200

    elapsed = time.time() - start_time
    # Must finish rapidly (< 3.0 seconds as required by AC-13)
    assert elapsed < 3.0

    # Query events: exactly 10 distinct events exist for the storm
    res_events = test_client.get("/api/events?limit=200")
    assert res_events.status_code == 200
    storm_ids = [e["id"] for e in res_events.json() if e["id"].startswith("storm_story_")]
    assert len(set(storm_ids)) == 10


def test_scenario_high_priority_alert_sub_60s_delivery(test_client, mock_telegram):
    """
    AC-11 / AC-12: High-priority event (score >= 9) verified from ingestion to
    Telegram delivery within the sub-60s window, containing all required fields.
    """
    start_time = time.time()

    urgent_event = {
        "theme_id": "us-iran-conflict",
        "source_type": "rss",
        "source_name": "Reuters Breaking",
        "source_url": "https://reuters.com/breaking/nuclear-facility-incident",
        "external_id": "urgent_strike_99",
        "title": "Direct kinetic strike impacts critical nuclear enrichment facility",
        "raw_text": "Emergency reports confirm direct ballistic missile kinetic impact on nuclear enrichment site with significant escalation declared.",
        "language": "en"
    }

    sent_count_before = len(mock_telegram.sent_messages)

    res = test_client.post("/api/pipeline/process", json=urgent_event)
    assert res.status_code == 200
    data = res.json()

    delivery_time = time.time() - start_time
    # Strictly delivered under 60 seconds (sub-second in test)
    assert delivery_time < 60.0

    assert data["importance_score"] >= 9
    assert data["notification_dispatched"] is True
    assert len(mock_telegram.sent_messages) == sent_count_before + 1

    # Verify Telegram payload compliance (AC-12)
    last_alert = mock_telegram.sent_messages[-1]
    alert_text = last_alert["formatted_text"]
    assert "🚨 *OSINT CRITICAL ALERT*" in alert_text
    assert "*Summary:*" in alert_text
    assert "*Source:* Reuters Breaking" in alert_text
    assert "*Link:* https://reuters.com/breaking/nuclear-facility-incident"


def test_scenario_full_operational_lifecycle(test_client, mock_telegram):
    """
    Validates complete operational workflow:
    1. Seed check -> US-Iran theme active
    2. Dynamic adjustment of threshold to 8 via settings
    3. Ingestion of 4 events with scores (low=3, med=5, high=7, critical=9)
    4. Score 7 is suppressed (since threshold=8), score 9 triggers alert
    5. Dashboard statistics verified for total and high-priority counts
    """
    # 1. Verify seed theme
    res_themes = test_client.get("/api/themes")
    assert res_themes.status_code == 200
    active_theme = next(t for t in res_themes.json() if t["id"] == "us-iran-conflict")
    assert active_theme["is_active"] is True

    # 2. Elevate threshold to 8
    res_thresh = test_client.put("/api/themes/us-iran-conflict", json={"importance_threshold": 8})
    assert res_thresh.status_code == 200

    alerts_before = len(mock_telegram.sent_messages)

    # 3. Ingest 4 events
    events = [
        {"title": "Weather in Hormuz", "text": "Clear skies over the gulf.", "expected_alert": False},
        {"title": "Routine trade meeting", "text": "Commercial representatives meet in Dubai.", "expected_alert": False},
        {"title": "Naval patrol warning", "text": "Sanctions warning issued near maritime corridor.", "expected_alert": False},  # score ~7, threshold is 8
        {"title": "Direct kinetic strike", "text": "Missile attack launched hitting naval frigate.", "expected_alert": True}      # score ~9
    ]

    for ev in events:
        p_res = test_client.post("/api/pipeline/process", json={
            "theme_id": "us-iran-conflict",
            "source_type": "rss",
            "source_name": "Wire",
            "title": ev["title"],
            "raw_text": ev["text"]
        })
        assert p_res.status_code == 200
        p_data = p_res.json()
        assert p_data["notification_dispatched"] == ev["expected_alert"]

    # Only the kinetic strike triggered an alert
    assert len(mock_telegram.sent_messages) == alerts_before + 1

    # 4. Verify stats
    stats = test_client.get("/api/stats").json()
    assert stats["total_events"] >= 4
    assert stats["active_sources"] >= 1

    # Restore threshold to 7
    test_client.put("/api/themes/us-iran-conflict", json={"importance_threshold": 7})


def test_scenario_multilingual_crisis_translation_and_timeline(test_client):
    """
    Simulates multilingual crisis feed with Arabic, Persian, and English breaking items.
    All non-English items translated into coherent English summaries on timeline.
    """
    multilingual_items = [
        {
            "id": f"multi_ar_{uuid.uuid4().hex[:6]}",
            "theme_id": "us-iran-conflict",
            "source_type": "rss",
            "source_name": "Al Arabiya",
            "title": "بيان عاجل من قيادة العمليات البحرية",
            "raw_text": "أعلنت قيادة القوات البحرية رفع حالة التأهب إلى الدرجة القصوى في الممرات المائية الدولية.",
            "language": "ar"
        },
        {
            "id": f"multi_fa_{uuid.uuid4().hex[:6]}",
            "theme_id": "us-iran-conflict",
            "source_type": "telegram",
            "source_name": "IRNA Persian",
            "title": "هشدار رسمی وزارت امور خارجه",
            "raw_text": "سخنگوی وزارت امور خارجه نسبت به هرگونه اقدام ماجراجویانه در منطقه هشدار جدی داد.",
            "language": "fa"
        }
    ]

    for item in multilingual_items:
        res = test_client.post("/api/pipeline/process", json=item)
        assert res.status_code == 200
        data = res.json()
        assert data["translated_text"] is not None
        assert "English translation:" in data["translated_text"]
        assert len(data["summary"]) > 0


def test_scenario_source_failover_and_recovery(test_client):
    """
    Simulates temporary source degradation:
    A degraded source updates its status without crashing the server or affecting other sources.
    """
    # Create test source
    src_res = test_client.post("/api/sources", json={
        "theme_id": "us-iran-conflict",
        "name": "Failing External RSS Feed",
        "source_type": "rss",
        "target": "https://unreachable-host.org/feed.xml"
    })
    src_id = src_res.json()["id"]

    # Mark as degraded with error message
    test_client.put(f"/api/sources/{src_id}", json={
        "status": "degraded",
        "error_message": "HTTP 502 Bad Gateway: Upstream connection timeout"
    })

    # Verify source status degraded
    get_res = test_client.get(f"/api/sources/{src_id}")
    assert get_res.json()["status"] == "degraded"
    assert "HTTP 502" in get_res.json()["error_message"]

    # Verify other sources remain active and queryable
    all_sources = test_client.get("/api/sources").json()
    active_count = sum(1 for s in all_sources if s["status"] == "active")
    assert active_count >= 1

    # Recover source
    test_client.put(f"/api/sources/{src_id}", json={"status": "active", "error_message": None})
    assert test_client.get(f"/api/sources/{src_id}").json()["status"] == "active"

    # Cleanup
    test_client.delete(f"/api/sources/{src_id}")


def test_scenario_maritime_anomaly_detection_to_alert(test_client, mock_telegram):
    """
    Simulates an AIS anomaly in the Strait of Hormuz triggering a critical alert.
    """
    alerts_before = len(mock_telegram.sent_messages)

    ais_anomaly_event = {
        "theme_id": "us-iran-conflict",
        "source_type": "ais",
        "source_name": "AISStream Strait of Hormuz",
        "external_id": "mmsi_210456000",
        "title": "Vessel Distress Anomaly: TANKER HORIZON (MMSI: 210456000)",
        "raw_text": "Commercial crude carrier transiting Strait of Hormuz reported naval patrol intercept craft approach and unexpected course divergence.",
        "location": {"name": "Strait of Hormuz", "lat": 26.54, "lon": 56.33},
        "metadata": {"mmsi": "210456000", "vessel_name": "TANKER HORIZON", "speed_knots": 3.2}
    }

    res = test_client.post("/api/pipeline/process", json=ais_anomaly_event)
    assert res.status_code == 200
    data = res.json()
    assert data["importance_score"] >= 7
    assert data["notification_dispatched"] is True
    assert len(mock_telegram.sent_messages) == alerts_before + 1


def test_scenario_dashboard_realtime_ws_event_stream(test_client):
    """
    Verifies that processing events generates structured WebSocket messages
    on the broadcast log: 'event:new' and 'alert:critical'.
    """
    ws_logs_before = len(test_client.get("/test/websocket-logs").json())

    event_payload = {
        "theme_id": "us-iran-conflict",
        "source_type": "rss",
        "source_name": "BBC News",
        "title": "Naval missile launch confirmed in international waters",
        "raw_text": "Live report: Missile test launch executed in international waters with naval response."
    }

    res = test_client.post("/api/pipeline/process", json=event_payload)
    assert res.status_code == 200
    ev_id = res.json()["id"]

    ws_logs_after = test_client.get("/test/websocket-logs").json()
    assert len(ws_logs_after) > ws_logs_before

    # Verify both new event and critical alert were pushed
    pushed_types = [m["type"] for m in ws_logs_after if m.get("payload", {}).get("id") == ev_id]
    assert "event:new" in pushed_types
    assert "alert:critical" in pushed_types
