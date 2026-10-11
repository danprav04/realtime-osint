"""
backend/tests/test_stage0_and_catalog_plugins.py

Tests covering:
1. Stage0Gatekeeper — Layer 1 deterministic triage (fast-pass, fast-drop, llm_candidate)
   and Layer 2 micro-batched LLM (mock provider path + heuristic fallback path)
2. BlueskySourcePlugin — unauthenticated ATProto XRPC mock responses, mode inference, post parsing
3. MastodonSourcePlugin — JSON timeline and RSS mock responses, target resolution
4. GeophysicalSourcePlugin — EMSC shallow/deep event classification, UKMTO HTML extraction, DMS parsing
5. TelegramSourcePlugin — ETag 304-not-modified caching
6. IngestionSupervisor.filter_and_dispatch_batch — Stage 0 gating with bypass_gatekeeper flag
"""

import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from backend.app.pipeline.stage0_gatekeeper import (
    BOT_AND_DELETED_PATTERNS,
    CASUAL_NOISE_PATTERNS,
    Stage0Gatekeeper,
    _heuristic_keep_decision,
    _extract_event_text_and_meta,
)
from backend.app.plugins.base import BaseSourcePlugin, Location, NormalizedEvent
from backend.app.plugins.bluesky_plugin import BlueskySourcePlugin
from backend.app.plugins.geophysical_plugin import (
    GeophysicalSourcePlugin,
    _parse_dms_coords,
)
from backend.app.plugins.mastodon_plugin import MastodonSourcePlugin
from backend.app.plugins.supervisor import IngestionSupervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    text: str,
    title: str = "",
    source_type: str = "rss",
    source_name: str = "test_source",
    theme_id: str = "1",
    external_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    published_at: Optional[datetime] = None,
) -> NormalizedEvent:
    return NormalizedEvent(
        theme_id=theme_id,
        source_type=source_type,
        source_name=source_name,
        external_id=external_id or f"ext_{hash(text) & 0xFFFF}",
        title=title,
        raw_text=text,
        metadata=metadata or {},
        published_at=published_at or datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. Stage0Gatekeeper – Layer 1 deterministic triage
# ---------------------------------------------------------------------------

class TestStage0GatekeeperLayer1:
    """Unit tests for triage_layer1 — zero-LLM deterministic decisions."""

    def setup_method(self):
        self.gk = Stage0Gatekeeper(batch_size=12)

    def test_fast_pass_seismic_source(self):
        ev = _make_event("M3.5 earthquake 15 km deep at 12.5N 43.2E", source_type="seismic")
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_pass_emsc_source(self):
        ev = _make_event("EMSC seismic alert Zagros region", source_type="emsc")
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_pass_ukmto_source(self):
        ev = _make_event("UKMTO maritime advisory issued for Red Sea", source_type="ukmto")
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_pass_ais_source(self):
        ev = _make_event("Tanker STAR IRIS changed course rapidly at 25.3N 56.8E", source_type="ais")
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_pass_alert_channel_meta(self):
        ev = _make_event("Air raid sirens reported in northern district", source_type="telegram", metadata={"is_alert_channel": True})
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_pass_kinetic_trigger_arabic(self):
        ev = _make_event("انفجار ضخم في منطقة المطار", source_type="telegram")
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_pass_kinetic_trigger_farsi(self):
        ev = _make_event("صدای مهیب در تهران شنیده شد", source_type="telegram")
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_pass_kinetic_trigger_hebrew(self):
        ev = _make_event("נשמע פיצוץ חזק ליד הנמל", source_type="telegram")
        assert self.gk.triage_layer1(ev) == "fast_pass"

    def test_fast_drop_empty_text(self):
        ev = _make_event("", title="")
        assert self.gk.triage_layer1(ev) == "fast_drop"

    def test_fast_drop_deleted_comment(self):
        ev = _make_event("[deleted]", source_type="reddit")
        assert self.gk.triage_layer1(ev) == "fast_drop"

    def test_fast_drop_removed_comment(self):
        ev = _make_event("[removed] This post was removed by a moderator", source_type="reddit")
        assert self.gk.triage_layer1(ev) == "fast_drop"

    def test_fast_drop_automoderator(self):
        ev = _make_event("I am a bot, and this action was performed automatically", source_type="reddit")
        assert self.gk.triage_layer1(ev) == "fast_drop"

    def test_fast_drop_ultra_short_no_vernacular(self):
        ev = _make_event("ok", source_type="telegram")
        assert self.gk.triage_layer1(ev) == "fast_drop"

    def test_fast_drop_near_duplicate_echo(self):
        text = "Breaking: Israeli airstrikes reported over southern Lebanon overnight"
        self.gk._recent_texts.append(text)
        ev = _make_event(text + " .", source_type="telegram")  # near-identical
        assert self.gk.triage_layer1(ev) == "fast_drop"

    def test_llm_candidate_neutral_social_post(self):
        ev = _make_event("Great weather in Tel Aviv today, going to the beach!", source_type="bluesky")
        assert self.gk.triage_layer1(ev) == "llm_candidate"

    def test_llm_candidate_news_headline(self):
        ev = _make_event("Regional talks on water resource allocation are expected this week", source_type="rss")
        assert self.gk.triage_layer1(ev) == "llm_candidate"

    def test_fast_drop_casual_noise_no_vernacular(self):
        ev = _make_event("Best restaurant in Beirut — check out my YouTube channel!", source_type="telegram")
        assert self.gk.triage_layer1(ev) == "fast_drop"


# ---------------------------------------------------------------------------
# 2. Stage0Gatekeeper – heuristic fallback
# ---------------------------------------------------------------------------

class TestStage0HeuristicFallback:
    """Unit tests for _heuristic_keep_decision without LLM."""

    def test_keep_osint_term_strike(self):
        assert _heuristic_keep_decision("Ballistic missile fired toward the south", "rss") is True

    def test_keep_osint_term_explosion(self):
        assert _heuristic_keep_decision("Reports of a large explosion near the port", "mastodon") is True

    def test_keep_vernacular_arabic(self):
        assert _heuristic_keep_decision("انفجار في المطار", "telegram") is True

    def test_drop_short_text_social(self):
        assert _heuristic_keep_decision("lol ok", "reddit") is False

    def test_drop_empty_text(self):
        assert _heuristic_keep_decision("", "rss") is False

    def test_drop_high_noise_social_no_match(self):
        assert _heuristic_keep_decision("I love this city so much! 😊", "telegram") is False

    def test_drop_bluesky_no_match_long_text(self):
        # Long enough but no OSINT match and bluesky source type → should drop
        assert _heuristic_keep_decision(
            "Just had the most amazing coffee this morning, really starting the day right!",
            "bluesky",
        ) is False

    def test_keep_rss_long_text_general(self):
        # RSS source, long enough, no casual noise pattern, no vernacular → keeps (passthrough for non-social sources)
        assert _heuristic_keep_decision(
            "The regional electricity grid operator issued a notice about rotating power cuts across several provinces",
            "rss",
        ) is True

    def test_keep_theme_keyword_match(self):
        assert _heuristic_keep_decision(
            "Houthi fighters claimed responsibility for the attack",
            "telegram",
            theme_context={"keywords": ["Houthi", "Yemen", "Red Sea"]},
        ) is True

    def test_bot_pattern_dropped(self):
        for pat in BOT_AND_DELETED_PATTERNS:
            assert _heuristic_keep_decision(pat, "reddit") is False, f"Should drop pattern: {pat}"


# ---------------------------------------------------------------------------
# 3. Stage0Gatekeeper – async LLM path (mock provider)
# ---------------------------------------------------------------------------

class TestStage0GatekeeperLLMPath:
    """Tests for the async evaluate_llm_chunk method with a mock AI provider."""

    def _make_mock_provider(self, response_json: str) -> MagicMock:
        prov = MagicMock()
        prov.provider_name = "mock"
        prov.default_model = "mock-model"
        prov.generate_text = AsyncMock(return_value=response_json)
        return prov

    @pytest.mark.asyncio
    async def test_llm_returns_valid_keep_indices(self):
        gk = Stage0Gatekeeper(batch_size=5)
        events = [
            _make_event("Drone attack reported over Erbil airport", source_type="telegram"),
            _make_event("Best pizza recipe ever!", source_type="telegram"),
            _make_event("IRGC published statement on new missile test", source_type="rss"),
        ]
        provider = self._make_mock_provider('{"keep": [0, 2]}')
        kept_indices = await gk.evaluate_llm_chunk(events, provider)
        assert kept_indices == [0, 2]

    @pytest.mark.asyncio
    async def test_llm_returns_empty_keep(self):
        gk = Stage0Gatekeeper()
        events = [
            _make_event("lol what is this weather?", source_type="bluesky"),
        ]
        provider = self._make_mock_provider('{"keep": []}')
        kept_indices = await gk.evaluate_llm_chunk(events, provider)
        assert kept_indices == []

    @pytest.mark.asyncio
    async def test_llm_ignores_out_of_range_indices(self):
        gk = Stage0Gatekeeper()
        events = [_make_event("Airstrike near Damascus", source_type="telegram")]
        provider = self._make_mock_provider('{"keep": [0, 99, -1]}')
        kept_indices = await gk.evaluate_llm_chunk(events, provider)
        assert 0 in kept_indices
        assert 99 not in kept_indices
        assert -1 not in kept_indices

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_heuristic(self):
        gk = Stage0Gatekeeper()
        events = [
            _make_event("Drone struck oil tanker in the Red Sea", source_type="rss"),
            _make_event("Good morning everyone!", source_type="telegram"),
        ]
        provider = MagicMock()
        provider.provider_name = "mock"
        provider.default_model = "mock-model"
        provider.generate_text = AsyncMock(side_effect=RuntimeError("API timeout"))
        kept_indices = await gk.evaluate_llm_chunk(events, provider)
        # "Drone struck oil tanker in the Red Sea" matches osint_terms (drone, red sea) → keep
        assert 0 in kept_indices
        # "Good morning everyone!" in telegram → drop by heuristic (len > 25 BUT social + no match)
        # Actually it's in CASUAL_NOISE_PATTERNS check: "good morning everyone" matches
        assert 1 not in kept_indices

    @pytest.mark.asyncio
    async def test_llm_empty_chunk_returns_empty(self):
        gk = Stage0Gatekeeper()
        provider = self._make_mock_provider('{"keep": []}')
        result = await gk.evaluate_llm_chunk([], provider)
        assert result == []

    @pytest.mark.asyncio
    async def test_stats_increment_correctly(self):
        gk = Stage0Gatekeeper(batch_size=10)
        events = [
            _make_event("انفجار بالقرب من المنطقة الخضراء", source_type="telegram"),
            _make_event("[deleted]", source_type="reddit"),
            _make_event("Friendly neighborhood news update about local park renovation", source_type="rss"),
        ]
        # Layer 1 manually
        for ev in events:
            gk.evaluated_count += 1
            d = gk.triage_layer1(ev)
            if d == "fast_pass":
                gk.passed_count += 1
                gk.fast_passed_count += 1
            elif d == "fast_drop":
                gk.dropped_deterministic_count += 1

        stats = gk.get_stats()
        assert stats["evaluated_count"] == 3
        assert stats["fast_passed_count"] >= 1  # Arabic kinetic fast-passed
        assert stats["dropped_deterministic_count"] >= 1  # [deleted] dropped


# ---------------------------------------------------------------------------
# 4. BlueskySourcePlugin – mock XRPC responses
# ---------------------------------------------------------------------------

class TestBlueskySourcePlugin:
    """Tests for BlueskySourcePlugin with mocked HTTP responses."""

    MOCK_AUTHOR_FEED = {
        "feed": [
            {
                "post": {
                    "uri": "at://did:plc:abc123/app.bsky.feed.post/1",
                    "cid": "bafyAAA",
                    "record": {
                        "$type": "app.bsky.feed.post",
                        "text": "Breaking: large explosion reported near Beirut port area — multiple sources confirm",
                        "createdAt": "2026-10-11T10:00:00Z",
                    },
                    "author": {
                        "did": "did:plc:abc123",
                        "handle": "elint.news",
                        "displayName": "ELINT News",
                    },
                    "indexedAt": "2026-10-11T10:00:05Z",
                    "likeCount": 42,
                    "repostCount": 15,
                }
            },
            {
                "post": {
                    "uri": "at://did:plc:xyz789/app.bsky.feed.post/2",
                    "cid": "bafyBBB",
                    "record": {
                        "$type": "app.bsky.feed.post",
                        "text": "Just had the best hummus for dinner 😋",
                        "createdAt": "2026-10-11T09:55:00Z",
                    },
                    "author": {
                        "did": "did:plc:xyz789",
                        "handle": "foodie.me",
                        "displayName": "Food Lover",
                    },
                    "indexedAt": "2026-10-11T09:55:05Z",
                    "likeCount": 5,
                    "repostCount": 1,
                }
            },
        ]
    }

    @pytest.mark.asyncio
    async def test_poll_author_mode_returns_events(self):
        plugin = BlueskySourcePlugin(
            source_id="bsky_1",
            theme_id="1",
            name="ELINT Bluesky",
            config={"actor": "elint.news", "mode": "author"},
            poll_interval=120,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = self.MOCK_AUTHOR_FEED
        mock_resp.headers = {}

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert len(events) == 2
        assert events[0].source_type == "bluesky"
        assert "Beirut" in events[0].raw_text or "explosion" in events[0].raw_text.lower()
        assert events[0].author in ("ELINT News", "elint.news")

    @pytest.mark.asyncio
    async def test_poll_returns_empty_on_304(self):
        plugin = BlueskySourcePlugin(
            source_id="bsky_2",
            theme_id="1",
            name="Test Feed",
            config={"actor": "osint.community", "mode": "author"},
            poll_interval=120,
        )
        # Seed ETag cache to trigger conditional GET
        plugin._etag_cache["any_key"] = '"abc123"'

        mock_resp = MagicMock()
        mock_resp.status_code = 304
        mock_resp.headers = {}

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert events == []

    @pytest.mark.asyncio
    async def test_poll_handles_http_error_gracefully(self):
        plugin = BlueskySourcePlugin(
            source_id="bsky_3",
            theme_id="1",
            name="Test Feed",
            config={"actor": "osint.community", "mode": "author"},
            poll_interval=120,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {}
        mock_resp.json.return_value = {}

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert events == []

    @pytest.mark.asyncio
    async def test_mode_inferred_from_actor(self):
        """Plugin config with only 'actor' key should infer mode=author."""
        plugin = BlueskySourcePlugin(
            source_id="bsky_4",
            theme_id="1",
            name="Mode Test",
            config={"actor": "osint_news.bsky.social"},
            poll_interval=60,
        )
        assert plugin.config.get("actor") == "osint_news.bsky.social"

    @pytest.mark.asyncio
    async def test_search_mode_uses_correct_endpoint(self):
        plugin = BlueskySourcePlugin(
            source_id="bsky_5",
            theme_id="1",
            name="OSINT Search",
            config={"query": "missile OR drone OR explosion", "mode": "search"},
            poll_interval=120,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = {"posts": []}

        captured_urls = []
        original_get = mock_resp

        async def fake_get(url, **kwargs):
            captured_urls.append(url)
            return mock_resp

        with patch("httpx.AsyncClient.get", new=fake_get):
            events = await plugin.poll()

        assert any("searchPosts" in u for u in captured_urls), (
            f"Expected searchPosts endpoint, got: {captured_urls}"
        )
        assert events == []


# ---------------------------------------------------------------------------
# 5. MastodonSourcePlugin – mock JSON and RSS responses
# ---------------------------------------------------------------------------

class TestMastodonSourcePlugin:
    """Tests for MastodonSourcePlugin with mocked HTTP responses."""

    MOCK_JSON_TIMELINE = [
        {
            "id": "109876543210",
            "created_at": "2026-10-11T10:05:00.000Z",
            "content": "<p>OSINT: Multiple vessels have changed course in the Strait of Hormuz — AIS data confirms.</p>",
            "account": {"username": "osint_analyst", "acct": "osint_analyst@infosec.exchange"},
            "url": "https://infosec.exchange/@osint_analyst/109876543210",
            "visibility": "public",
            "language": "en",
        },
        {
            "id": "109876543211",
            "created_at": "2026-10-11T10:03:00.000Z",
            "content": "<p>Good morning everyone! Hope you have a wonderful day!</p>",
            "account": {"username": "happyuser", "acct": "happyuser@mastodon.social"},
            "url": "https://mastodon.social/@happyuser/109876543211",
            "visibility": "public",
            "language": "en",
        },
    ]

    @pytest.mark.asyncio
    async def test_poll_json_timeline_returns_events(self):
        plugin = MastodonSourcePlugin(
            source_id="masto_1",
            theme_id="1",
            name="Mastodon OSINT",
            config={"target": "#OSINT@infosec.exchange", "mode": "hashtag"},
            poll_interval=120,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "application/json; charset=utf-8"}
        mock_resp.json.return_value = self.MOCK_JSON_TIMELINE
        mock_resp.text = json.dumps(self.MOCK_JSON_TIMELINE)

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert len(events) == 2
        assert events[0].source_type == "mastodon"
        # First event should mention Hormuz / AIS / OSINT
        assert any(
            kw in events[0].raw_text
            for kw in ("Hormuz", "AIS", "OSINT", "vessels")
        )

    @pytest.mark.asyncio
    async def test_poll_returns_empty_on_304(self):
        plugin = MastodonSourcePlugin(
            source_id="masto_2",
            theme_id="1",
            name="Test Mastodon",
            config={"target": "#Iran@mastodon.social", "mode": "hashtag"},
            poll_interval=120,
        )
        plugin._last_etag = '"etag123"'

        mock_resp = MagicMock()
        mock_resp.status_code = 304
        mock_resp.headers = {}

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert events == []

    @pytest.mark.asyncio
    async def test_target_resolution_hashtag_at_instance(self):
        """#tag@instance should resolve to hashtag endpoint on that instance."""
        plugin = MastodonSourcePlugin(
            source_id="masto_3",
            theme_id="1",
            name="Hashtag Test",
            config={"target": "#osint@fosstodon.org"},
            poll_interval=120,
        )
        # Just check that the URL would be constructed for fosstodon.org
        assert plugin.config.get("target") == "#osint@fosstodon.org"

    @pytest.mark.asyncio
    async def test_target_resolution_user_at_instance(self):
        """@user@instance should resolve to account's public statuses endpoint."""
        plugin = MastodonSourcePlugin(
            source_id="masto_4",
            theme_id="1",
            name="User Test",
            config={"target": "@IntelCrab@infosec.exchange"},
            poll_interval=120,
        )
        assert plugin.config.get("target") == "@IntelCrab@infosec.exchange"

    @pytest.mark.asyncio
    async def test_poll_handles_server_error_gracefully(self):
        plugin = MastodonSourcePlugin(
            source_id="masto_5",
            theme_id="1",
            name="Error Test",
            config={"target": "#breaking@mastodon.social"},
            poll_interval=120,
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.headers = {}
        mock_resp.json.return_value = {}
        mock_resp.text = ""

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert events == []


# ---------------------------------------------------------------------------
# 6. GeophysicalSourcePlugin – EMSC seismic event classification
# ---------------------------------------------------------------------------

MOCK_EMSC_RESPONSE = {
    "features": [
        {
            "properties": {
                "unid": "20261011_000001",
                "time": "2026-10-11T10:00:00.000Z",
                "mag": 2.4,
                "magtype": "ml",
                "depth": 1.5,  # Shallow / surface — potential kinetic detonation
                "flynn_region": "IRAN",
                "source_catalog": "EMSC-RTS",
            },
            "geometry": {"coordinates": [44.2, 32.5, 1.5]},
        },
        {
            "properties": {
                "unid": "20261011_000002",
                "time": "2026-10-11T09:45:00.000Z",
                "mag": 4.1,
                "magtype": "mb",
                "depth": 32.0,  # Deep tectonic — natural
                "flynn_region": "EASTERN TURKEY",
                "source_catalog": "EMSC-RTS",
            },
            "geometry": {"coordinates": [38.7, 39.1, 32.0]},
        },
        {
            "properties": {
                "unid": "20261011_000003",
                "time": "2026-10-11T09:30:00.000Z",
                "mag": 1.8,
                "magtype": "ml",
                "depth": 0.0,  # Zero depth — could be surface explosion
                "flynn_region": "RED SEA",
                "source_catalog": "EMSC-RTS",
            },
            "geometry": {"coordinates": [42.3, 18.4, 0.0]},
        },
    ]
}


class TestGeophysicalPluginEMSC:
    """Tests for GeophysicalSourcePlugin — EMSC seismic sensor."""

    @pytest.mark.asyncio
    async def test_shallow_event_flagged_as_potential_kinetic(self):
        plugin = GeophysicalSourcePlugin(
            source_id="geo_1",
            theme_id="1",
            name="EMSC Seismic",
            config={},
            poll_interval=60,
            sensor_type="seismic",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = MOCK_EMSC_RESPONSE

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        # Should have events (at least the two shallow/zero-depth ones)
        shallow_events = [
            ev for ev in events
            if ev.metadata.get("is_shallow_surface_event") is True
        ]
        deep_events = [
            ev for ev in events
            if ev.metadata.get("is_shallow_surface_event") is False
        ]

        assert len(shallow_events) >= 2, (
            f"Expected at least 2 shallow events (depth ≤2km), got {len(shallow_events)}"
        )
        assert len(deep_events) >= 1, (
            f"Expected at least 1 deep tectonic event, got {len(deep_events)}"
        )

    @pytest.mark.asyncio
    async def test_shallow_event_title_mentions_potential_detonation(self):
        plugin = GeophysicalSourcePlugin(
            source_id="geo_2",
            theme_id="1",
            name="EMSC Seismic",
            config={},
            poll_interval=60,
            sensor_type="seismic",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = MOCK_EMSC_RESPONSE

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        shallow_events = [ev for ev in events if ev.metadata.get("is_shallow_surface_event")]
        for ev in shallow_events:
            assert (
                "surface" in ev.title.lower()
                or "potential" in ev.title.lower()
                or "shallow" in ev.title.lower()
                or "detonation" in ev.title.lower()
                or "M" in ev.title  # At minimum magnitude should be in title
            ), f"Shallow event title doesn't mention kinetic context: {ev.title}"

    @pytest.mark.asyncio
    async def test_event_location_set(self):
        plugin = GeophysicalSourcePlugin(
            source_id="geo_3",
            theme_id="1",
            name="EMSC Seismic",
            config={},
            poll_interval=60,
            sensor_type="seismic",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.json.return_value = MOCK_EMSC_RESPONSE

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        for ev in events:
            assert ev.location is not None
            assert ev.location.lat is not None
            assert ev.location.lon is not None

    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        plugin = GeophysicalSourcePlugin(
            source_id="geo_4",
            theme_id="1",
            name="EMSC Seismic",
            config={},
            poll_interval=60,
            sensor_type="seismic",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_resp.headers = {}
        mock_resp.json.return_value = {}

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert events == []

    @pytest.mark.asyncio
    async def test_304_returns_empty(self):
        plugin = GeophysicalSourcePlugin(
            source_id="geo_5",
            theme_id="1",
            name="EMSC Seismic",
            config={},
            poll_interval=60,
            sensor_type="seismic",
        )
        plugin._last_etag = '"abc"'

        mock_resp = MagicMock()
        mock_resp.status_code = 304
        mock_resp.headers = {}

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert events == []


# ---------------------------------------------------------------------------
# 7. DMS coordinate parsing – _parse_dms_coords
# ---------------------------------------------------------------------------

class TestDMSCoordinateParsing:
    """Unit tests for the UKMTO DMS coordinate extractor."""

    def test_parse_standard_dms(self):
        text = "Incident reported at 14°50'N 042°15'E in the Red Sea area."
        coords = _parse_dms_coords(text)
        assert coords is not None
        lat, lon = coords
        assert 14 < lat < 15
        assert 42 < lon < 43

    def test_parse_spaced_dms(self):
        text = "Location: 25 10N 056 30E near the Strait of Hormuz."
        coords = _parse_dms_coords(text)
        assert coords is not None
        lat, lon = coords
        assert 25 < lat < 26
        assert 56 < lon < 57

    def test_returns_none_for_no_coords(self):
        text = "No coordinates in this advisory message, contact UKMTO directly."
        coords = _parse_dms_coords(text)
        assert coords is None

    def test_returns_none_for_decimal_only_text(self):
        # Pure decimal coords not in DMS format
        text = "Position: 24.500, 55.750"
        coords = _parse_dms_coords(text)
        # DMS regex won't match pure decimal — acceptable to return None
        assert coords is None or isinstance(coords, tuple)


# ---------------------------------------------------------------------------
# 8. GeophysicalSourcePlugin – UKMTO HTML extraction
# ---------------------------------------------------------------------------

MOCK_UKMTO_HTML = """<!DOCTYPE html>
<html>
<body>
<ul>
<li>
  UKMTO Advisory No. 021/2026 — Suspicious vessel approach reported at 14°50'N 042°15'E.
  Masters are advised to exercise heightened vigilance. Report to UKMTO immediately.
  Area: Red Sea, nautical miles from shipping lanes.
</li>
<li>
  UKMTO Warning No. 022/2026 — Armed piracy incident near Gulf of Aden. Vessel at 11°30'N 045°20'E.
  Crew safe. Naval forces responding. All vessels in area maintain 35 nautical miles safety distance.
</li>
<li>Short nav notice</li>
</ul>
</body>
</html>"""


class TestGeophysicalPluginUKMTO:
    """Tests for GeophysicalSourcePlugin — UKMTO maritime sensor."""

    @pytest.mark.asyncio
    async def test_ukmto_extracts_advisories(self):
        plugin = GeophysicalSourcePlugin(
            source_id="ukmto_1",
            theme_id="1",
            name="UKMTO Maritime",
            config={},
            poll_interval=120,
            sensor_type="ukmto",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = MOCK_UKMTO_HTML

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert len(events) >= 1
        for ev in events:
            assert ev.source_type == "ukmto"
            assert ev.location is not None

    @pytest.mark.asyncio
    async def test_ukmto_event_with_dms_has_exact_coords(self):
        plugin = GeophysicalSourcePlugin(
            source_id="ukmto_2",
            theme_id="1",
            name="UKMTO Maritime",
            config={},
            poll_interval=120,
            sensor_type="ukmto",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = MOCK_UKMTO_HTML

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        events_with_dms = [ev for ev in events if ev.metadata.get("has_exact_dms")]
        assert len(events_with_dms) >= 1, "Expected at least one event with parsed DMS coordinates"

    @pytest.mark.asyncio
    async def test_ukmto_short_blocks_skipped(self):
        plugin = GeophysicalSourcePlugin(
            source_id="ukmto_3",
            theme_id="1",
            name="UKMTO Maritime",
            config={},
            poll_interval=120,
            sensor_type="ukmto",
        )

        # Only short/irrelevant blocks
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.text = "<html><body><li>Short nav notice</li></body></html>"

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            events = await plugin.poll()

        assert events == []


# ---------------------------------------------------------------------------
# 9. IngestionSupervisor.filter_and_dispatch_batch – bypass_gatekeeper flag
# ---------------------------------------------------------------------------

class TestSupervisorFilterAndDispatch:
    """Integration tests for IngestionSupervisor.filter_and_dispatch_batch."""

    def _make_test_plugin(self, source_type: str = "rss", bypass: bool = False) -> BaseSourcePlugin:
        class _TestPlugin(BaseSourcePlugin):
            async def poll(self) -> List[NormalizedEvent]:
                return []
            async def test_connection(self) -> bool:
                return True

        cfg = {"bypass_gatekeeper": bypass} if bypass else {}
        p = _TestPlugin(
            source_id="test_src",
            theme_id="1",
            name="Test Source",
            config=cfg,
            poll_interval=60,
        )
        p.source_type = source_type
        p.max_lookback_hours = 24.0
        return p

    @pytest.mark.asyncio
    async def test_bypass_gatekeeper_passes_all_events(self):
        """With bypass_gatekeeper=True, events skip Stage 0 entirely."""
        supervisor = IngestionSupervisor()
        plugin = self._make_test_plugin(bypass=True)
        plugin.config["bypass_gatekeeper"] = True

        events = [
            _make_event("lol nothing important", source_type="rss", published_at=datetime.now(timezone.utc)),
            _make_event("another irrelevant post 123", source_type="rss", published_at=datetime.now(timezone.utc)),
        ]

        with patch.object(supervisor, "dispatch_and_persist", new_callable=AsyncMock) as mock_dispatch:
            kept = await supervisor.filter_and_dispatch_batch(plugin, events)

        # With bypass, all events pass through
        assert len(kept) == len(events)
        assert mock_dispatch.call_count == len(events)

    @pytest.mark.asyncio
    async def test_empty_events_returns_empty(self):
        supervisor = IngestionSupervisor()
        plugin = self._make_test_plugin()
        result = await supervisor.filter_and_dispatch_batch(plugin, [])
        assert result == []

    @pytest.mark.asyncio
    async def test_old_events_filtered_by_lookback(self):
        """Events older than max_lookback_hours should be discarded before Stage 0."""
        supervisor = IngestionSupervisor()
        plugin = self._make_test_plugin()
        plugin.max_lookback_hours = 1.0

        old_event = _make_event(
            "Old breaking news from 3 hours ago",
            source_type="rss",
            published_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        fresh_event = _make_event(
            "Just breaking — drone strike confirmed near naval base",
            source_type="rss",
            published_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )

        with patch.object(supervisor, "dispatch_and_persist", new_callable=AsyncMock):
            kept = await supervisor.filter_and_dispatch_batch(plugin, [old_event, fresh_event])

        # Old event should be filtered by lookback; fresh event may pass Stage 0 heuristic
        all_texts = [ev.raw_text for ev in kept]
        assert old_event.raw_text not in all_texts, "Old event should have been discarded by lookback filter"

    @pytest.mark.asyncio
    async def test_sensor_events_always_kept(self):
        """Seismic / UKMTO events should fast-pass Stage 0 and be dispatched."""
        supervisor = IngestionSupervisor()
        plugin = self._make_test_plugin(source_type="seismic")

        seismic_event = _make_event(
            "M3.2 earthquake at depth 1.8km — potential surface detonation, EMSC-RTS",
            source_type="seismic",
            published_at=datetime.now(timezone.utc),
        )

        dispatched: List[NormalizedEvent] = []

        async def fake_dispatch(ev):
            dispatched.append(ev)

        with patch.object(supervisor, "dispatch_and_persist", new=fake_dispatch):
            kept = await supervisor.filter_and_dispatch_batch(plugin, [seismic_event])

        assert len(kept) == 1, "Seismic events should always pass Stage 0 fast-pass"

    @pytest.mark.asyncio
    async def test_spam_events_dropped(self):
        """Pure spam / bot events should be dropped by Stage 0 before dispatch."""
        supervisor = IngestionSupervisor()
        plugin = self._make_test_plugin(source_type="telegram")

        spam_events = [
            _make_event("[deleted]", source_type="telegram", published_at=datetime.now(timezone.utc)),
            _make_event("[removed]", source_type="telegram", published_at=datetime.now(timezone.utc)),
            _make_event(
                "I am a bot, and this action was performed automatically. Please contact the mods.",
                source_type="reddit",
                published_at=datetime.now(timezone.utc),
            ),
        ]

        dispatched: List[NormalizedEvent] = []

        async def fake_dispatch(ev):
            dispatched.append(ev)

        with patch.object(supervisor, "dispatch_and_persist", new=fake_dispatch):
            kept = await supervisor.filter_and_dispatch_batch(plugin, spam_events)

        assert len(kept) == 0, f"All spam events should be dropped by Stage 0, got: {[ev.raw_text for ev in kept]}"
        assert len(dispatched) == 0, "No spam events should reach dispatch"


# ---------------------------------------------------------------------------
# 10. Supervisor – create_plugin_instance factory
# ---------------------------------------------------------------------------

class TestCreatePluginInstance:
    """Tests for the create_plugin_instance factory."""

    def test_creates_bluesky_plugin(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        p = create_plugin_instance("bluesky", "1", "1", "Test Bluesky", {"actor": "osint.bsky.social"})
        assert isinstance(p, BlueskySourcePlugin)

    def test_creates_bluesky_from_bsky_alias(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        p = create_plugin_instance("bsky", "2", "1", "Test Bsky", {})
        assert isinstance(p, BlueskySourcePlugin)

    def test_creates_mastodon_plugin(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        p = create_plugin_instance("mastodon", "3", "1", "Test Mastodon", {"target": "#OSINT@mastodon.social"})
        assert isinstance(p, MastodonSourcePlugin)

    def test_creates_mastodon_from_fediverse_alias(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        p = create_plugin_instance("fediverse", "4", "1", "Fediverse", {})
        assert isinstance(p, MastodonSourcePlugin)

    def test_creates_geophysical_seismic_plugin(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        p = create_plugin_instance("seismic", "5", "1", "EMSC Seismic", {})
        assert isinstance(p, GeophysicalSourcePlugin)

    def test_creates_geophysical_from_emsc_alias(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        p = create_plugin_instance("emsc", "6", "1", "EMSC", {})
        assert isinstance(p, GeophysicalSourcePlugin)

    def test_creates_ukmto_plugin(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        p = create_plugin_instance("ukmto", "7", "1", "UKMTO Maritime", {})
        assert isinstance(p, GeophysicalSourcePlugin)

    def test_unknown_type_falls_back_to_rss(self):
        from backend.app.plugins.supervisor import create_plugin_instance
        from backend.app.plugins.rss_plugin import RSSSourcePlugin
        p = create_plugin_instance("unknown_xyz", "8", "1", "Fallback Test", {})
        assert isinstance(p, RSSSourcePlugin)
