import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from backend.app.pipeline.stage0_gatekeeper import Stage0Gatekeeper
from backend.app.pipeline.stage2_score import classify_source_reliability
from backend.app.plugins.base import NormalizedEvent
from backend.app.plugins.oref_plugin import (
    OrefSourcePlugin,
    resolve_location_coordinates,
    DEFAULT_HISTORY_URL,
    DEFAULT_REALTIME_URL,
)
from backend.app.plugins.supervisor import create_plugin_instance


class TestOrefPluginInit:
    def test_default_initialization(self):
        plugin = OrefSourcePlugin(
            source_id="src_oref_1",
            theme_id="us-iran-conflict",
            name="Israel Home Front Command Alerts",
        )
        assert plugin.source_id == "src_oref_1"
        assert plugin.theme_id == "us-iran-conflict"
        assert plugin.name == "Israel Home Front Command Alerts"
        assert plugin.history_url == DEFAULT_HISTORY_URL
        assert plugin.realtime_url == DEFAULT_REALTIME_URL
        assert plugin.poll_interval == 30
        assert plugin.is_enabled is True
        assert plugin.poll_realtime is True

    def test_custom_config(self):
        plugin = OrefSourcePlugin(
            source_id="src_oref_custom",
            theme_id="us-iran-conflict",
            name="Custom Oref Proxy",
            config={
                "target": "http://127.0.0.1:8080/GetAlarmsHistory.aspx",
                "realtime_url": "http://127.0.0.1:8080/alerts.json",
                "proxy": "http://proxy.local:3128",
                "lang": "en",
                "poll_realtime": False,
            },
            poll_interval=15,
        )
        assert plugin.history_url == "http://127.0.0.1:8080/GetAlarmsHistory.aspx"
        assert plugin.realtime_url == "http://127.0.0.1:8080/alerts.json"
        assert plugin.proxy_url == "http://proxy.local:3128"
        assert plugin.preferred_lang == "en"
        assert plugin.poll_realtime is False
        assert plugin.poll_interval == 15


class TestOrefGeolocation:
    def test_hebrew_major_cities(self):
        loc_tlv = resolve_location_coordinates("תל אביב")
        assert loc_tlv is not None
        assert abs(loc_tlv.lat - 32.0853) < 0.05
        assert abs(loc_tlv.lon - 34.7818) < 0.05

        loc_haifa = resolve_location_coordinates("חיפה")
        assert loc_haifa is not None
        assert abs(loc_haifa.lat - 32.7940) < 0.05

        loc_jerusalem = resolve_location_coordinates("ירושלים")
        assert loc_jerusalem is not None
        assert abs(loc_jerusalem.lat - 31.7683) < 0.05

    def test_northern_and_southern_frontline(self):
        loc_aramshe = resolve_location_coordinates("ערב אל עראמשה")
        assert loc_aramshe is not None
        assert abs(loc_aramshe.lat - 33.0850) < 0.05

        loc_shmona = resolve_location_coordinates("קריית שמונה")
        assert loc_shmona is not None
        assert abs(loc_shmona.lat - 33.2075) < 0.05

        loc_sderot = resolve_location_coordinates("שדרות")
        assert loc_sderot is not None
        assert abs(loc_sderot.lat - 31.5215) < 0.05

    def test_english_transliterations(self):
        loc = resolve_location_coordinates("Tel Aviv")
        assert loc is not None
        assert abs(loc.lat - 32.0853) < 0.05

        loc_beer = resolve_location_coordinates("beersheba")
        assert loc_beer is not None
        assert abs(loc_beer.lat - 31.2518) < 0.05

    def test_empty_or_unknown_fallback(self):
        assert resolve_location_coordinates("") is None
        assert resolve_location_coordinates("   ") is None
        fallback_loc = resolve_location_coordinates("ישוב לא מוכר כלשהו")
        assert fallback_loc is not None
        assert fallback_loc.lat == 31.5
        assert fallback_loc.lon == 34.8


class TestOrefPolling:
    @pytest.mark.asyncio
    async def test_poll_history_records(self):
        plugin = OrefSourcePlugin("1", "theme1", "Pikud HaOref")

        sample_history = [
            {
                "data": "ערב אל עראמשה",
                "date": "16.10.2026",
                "time": "20:06:13",
                "alertDate": "2026-10-16T20:06:00",
                "category": 13,
                "category_desc": "האירוע הסתיים",
                "matrix_id": 10,
                "rid": 706606,
            },
            {
                "data": "קריית שמונה",
                "date": "16.10.2026",
                "time": "19:57:09",
                "alertDate": "2026-10-16T19:57:00",
                "category": 2,
                "category_desc": "חדירת כלי טיס עוין",
                "matrix_id": 6,
                "rid": 706605,
            },
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Include UTF-8 BOM
        mock_resp.content = ("\ufeff" + json.dumps(sample_history)).encode("utf-8")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(plugin, "_get_client", return_value=mock_client), patch.object(
            plugin, "_poll_realtime", new_callable=AsyncMock
        ) as mock_live:
            mock_live.return_value = []
            events = await plugin.poll()

        assert len(events) == 2
        ev1 = events[0]
        assert ev1.source_type == "oref"
        assert "האירוע הסתיים" in ev1.title
        assert ev1.location is not None
        assert abs(ev1.location.lat - 33.0850) < 0.05
        assert ev1.metadata["rid"] == "706606"
        assert ev1.metadata["is_all_clear"] is True

        ev2 = events[1]
        assert ev2.source_type == "oref"
        assert "חדירת כלי טיס עוין" in ev2.title
        assert ev2.location is not None
        assert abs(ev2.location.lat - 33.2075) < 0.05
        assert ev2.metadata["rid"] == "706605"
        assert ev2.metadata["is_urgent_alert"] is True

    @pytest.mark.asyncio
    async def test_poll_realtime_active_siren(self):
        plugin = OrefSourcePlugin("1", "theme1", "Pikud HaOref")

        sample_live = {
            "id": "1729001234",
            "cat": "1",
            "title": "ירי רקטות וטילים",
            "data": ["חיפה - כרמל ועיר תחתית", "נשר"],
            "desc": "היכנסו למרחב המוגן ושהו בו 10 דקות",
        }

        mock_live_resp = MagicMock()
        mock_live_resp.status_code = 200
        mock_live_resp.content = json.dumps(sample_live).encode("utf-8")

        mock_hist_resp = MagicMock()
        mock_hist_resp.status_code = 200
        mock_hist_resp.content = b"[]"

        mock_client = AsyncMock()

        async def fake_get(url, *args, **kwargs):
            if "alerts.json" in str(url):
                return mock_live_resp
            return mock_hist_resp

        mock_client.get = AsyncMock(side_effect=fake_get)

        with patch.object(plugin, "_get_client", return_value=mock_client):
            events = await plugin.poll()

        assert len(events) == 1
        live_ev = events[0]
        assert live_ev.source_type == "oref"
        assert "אזעקת צבע אדום בזמן אמת" in live_ev.title
        assert "ירי רקטות וטילים" in live_ev.title
        assert "חיפה" in live_ev.title
        assert live_ev.metadata["is_realtime_siren"] is True
        assert live_ev.metadata["is_urgent_alert"] is True
        assert live_ev.location is not None

    @pytest.mark.asyncio
    async def test_poll_deduplication(self):
        plugin = OrefSourcePlugin("1", "theme1", "Pikud HaOref")

        sample_history = [
            {
                "data": "שדרות",
                "date": "16.10.2026",
                "time": "12:00:00",
                "alertDate": "2026-10-16T12:00:00",
                "category": 1,
                "category_desc": "ירי רקטות וטילים",
                "rid": 800100,
            }
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = json.dumps(sample_history).encode("utf-8")

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(plugin, "_get_client", return_value=mock_client), patch.object(
            plugin, "_poll_realtime", new_callable=AsyncMock
        ) as mock_live:
            mock_live.return_value = []
            first_poll = await plugin.poll()
            assert len(first_poll) == 1

            # Second poll should return empty due to RID deduplication
            second_poll = await plugin.poll()
            assert len(second_poll) == 0

    @pytest.mark.asyncio
    async def test_empty_and_bom_responses(self):
        plugin = OrefSourcePlugin("1", "theme1", "Pikud HaOref")

        # BOM + empty newline
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"\xef\xbb\xbf\r\n"

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(plugin, "_get_client", return_value=mock_client):
            events = await plugin.poll()
            assert events == []

    @pytest.mark.asyncio
    async def test_connection_success_and_failure(self):
        plugin = OrefSourcePlugin("1", "theme1", "Pikud HaOref")

        mock_client = AsyncMock()
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_client.get = AsyncMock(return_value=mock_ok)

        with patch.object(plugin, "_get_client", return_value=mock_client):
            assert await plugin.test_connection() is True

        mock_err = MagicMock()
        mock_err.status_code = 500
        mock_client.get = AsyncMock(return_value=mock_err)

        with patch.object(plugin, "_get_client", return_value=mock_client):
            assert await plugin.test_connection() is False


class TestSupervisorAndPipelineIntegration:
    def test_supervisor_factory_creates_oref_plugin(self):
        plugin = create_plugin_instance("oref", "1", "theme_1", "Oref Source", {})
        assert isinstance(plugin, OrefSourcePlugin)

    def test_supervisor_factory_creates_from_aliases(self):
        for alias in ("pikud_haoref", "homefront", "hfc"):
            plugin = create_plugin_instance(alias, "2", "theme_1", "Oref Alias", {})
            assert isinstance(plugin, OrefSourcePlugin)

    def test_stage0_fast_pass_oref(self):
        gatekeeper = Stage0Gatekeeper()
        event = NormalizedEvent(
            theme_id="us-iran-conflict",
            source_type="oref",
            source_name="Israel Home Front Command Alerts",
            external_id="oref-test-1",
            title="צבע אדום: ירי רקטות וטילים - קריית שמונה",
            raw_text="אזעקת צבע אדום בקריית שמונה",
            published_at=datetime.now(timezone.utc),
        )
        decision = gatekeeper.triage_layer1(event)
        assert decision == "fast_pass"

    def test_stage2_tier1_source_reliability(self):
        tier, desc = classify_source_reliability("Israel Home Front Command Alerts (פיקוד העורף)", "oref")
        assert tier == "tier_1_wire_official"
        assert "Civil Defense Alert" in desc or "Direct Sensor" in desc
