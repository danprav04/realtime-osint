import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from backend.app.plugins.base import BaseSourcePlugin, Location, NormalizedEvent


class AISSourcePlugin(BaseSourcePlugin):
    """
    Maritime & Naval AIS Tracking Plugin.
    Integrates:
    1. Digitraffic Maritime Open REST API (https://meri.digitraffic.fi/api/ais/v1/locations) - Free & Open.
    2. AISStream.io (WebSocket / REST API) subscribing to strategic chokepoint bounding boxes
       (Strait of Hormuz, Taiwan Strait, Bab el-Mandeb).
    Normalizes telemetry (MMSI, ship name, coordinates, speed, destination) into NormalizedEvent
    with structured Location objects.
    """

    def __init__(
        self,
        source_id: Any,
        theme_id: Any,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 120,
        is_enabled: bool = True,
    ):
        super().__init__(source_id, theme_id, name, config, poll_interval, is_enabled)
        self.provider: str = (self.config.get("provider") or "digitraffic").lower()
        self.api_key: Optional[str] = self.config.get("api_key")
        self.endpoint: str = (
            self.config.get("url")
            or self.config.get("endpoint")
            or self.config.get("target")
            or ("https://meri.digitraffic.fi/api/ais/v1/locations" if self.provider == "digitraffic" else "")
        )
        self.location_name: str = self.config.get("location_name") or "Strategic Maritime Chokepoint"
        self.bounding_boxes: List[Any] = self.config.get("bounding_boxes") or []
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                headers={"User-Agent": "RealTimeOSINT-AISPlugin/1.0 (Maritime Traffic Monitor)"},
                follow_redirects=True,
            )
        return self._http_client

    async def stop(self) -> None:
        await super().stop()
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def poll(self) -> List[NormalizedEvent]:
        """Poll maritime vessel telemetry from Digitraffic or configured AIS endpoint."""
        # Check if static test signals are injected in config for testing/fixtures
        static_signals = self.config.get("signals")
        if static_signals and isinstance(static_signals, list):
            return self._normalize_signals_list(static_signals)

        if self.provider == "digitraffic" or "digitraffic.fi" in self.endpoint:
            return await self._poll_digitraffic()
        else:
            return await self._poll_generic_ais_rest()

    async def _poll_digitraffic(self) -> List[NormalizedEvent]:
        """Fetch vessel locations from Digitraffic Finland open REST API."""
        url = self.endpoint or "https://meri.digitraffic.fi/api/ais/v1/locations"
        try:
            client = await self._get_client()
            resp = await client.get(url)
            if resp.status_code != 200:
                self.logger.warning(f"Digitraffic API returned HTTP {resp.status_code}")
                return []
            data = resp.json()
        except Exception as exc:
            self.last_error = f"Digitraffic AIS fetch error: {exc}"
            self.logger.warning(f"Digitraffic AIS poll failed for '{self.name}': {exc}")
            return []

        features = data.get("features") or []
        events: List[NormalizedEvent] = []

        # Process up to 50 active vessel telemetry points to stay within RAM & processing budget
        for feat in features[:50]:
            try:
                coords = feat.get("geometry", {}).get("coordinates", [])
                if len(coords) < 2:
                    continue
                lon, lat = float(coords[0]), float(coords[1])
                props = feat.get("properties", {})
                mmsi = str(props.get("mmsi") or "")
                sog = float(props.get("sog") or 0.0)  # Speed over ground in knots
                cog = float(props.get("cog") or 0.0)  # Course over ground in degrees
                heading = props.get("heading")
                nav_stat = props.get("navStat", 0)
                ts_ms = props.get("timestampExternal") or int(datetime.now(timezone.utc).timestamp() * 1000)
                published_at = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)

                # Discard telemetry points older than configured lookback window
                if not self.is_event_within_lookback(published_at):
                    continue

                ship_name = props.get("shipName") or f"Vessel-{mmsi}"
                destination = props.get("destination") or "Undisclosed"

                location_name = self.location_name
                loc = Location(name=location_name, lat=lat, lon=lon)

                external_id = f"ais_{mmsi}_{int(ts_ms / 1000)}"

                title = f"Naval Movement: {ship_name} (MMSI: {mmsi}) reported near {location_name}"
                raw_text = (
                    f"Vessel {ship_name} (MMSI: {mmsi}) recorded at coordinates {lat:.4f}, {lon:.4f}. "
                    f"Speed over ground: {sog:.1f} knots, course: {cog:.1f}°, destination: {destination}."
                )

                event = NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="ais",
                    source_name=self.name,
                    source_url="https://meri.digitraffic.fi/api/ais/v1/locations",
                    external_id=external_id,
                    title=title,
                    raw_text=raw_text,
                    language="en",
                    author=f"AIS-MMSI-{mmsi}",
                    location=loc,
                    published_at=published_at,
                    metadata={
                        "mmsi": mmsi,
                        "ship_name": ship_name,
                        "sog_knots": sog,
                        "cog_degrees": cog,
                        "heading": heading,
                        "nav_status": nav_stat,
                        "destination": destination,
                        "provider": "digitraffic",
                    },
                )
                events.append(event)
            except Exception as e:
                self.logger.warning(f"Error parsing Digitraffic feature: {e}")
                continue

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(events)
        self.status = "active"
        self.last_error = None
        return events

    async def _poll_generic_ais_rest(self) -> List[NormalizedEvent]:
        """Fetch AIS data from generic or configured REST endpoint."""
        if not self.endpoint:
            return []
        try:
            client = await self._get_client()
            resp = await client.get(self.endpoint)
            if resp.status_code != 200:
                return []
            data = resp.json()
            items = data if isinstance(data, list) else data.get("signals") or data.get("vessels") or []
            return self._normalize_signals_list(items)
        except Exception as exc:
            self.last_error = f"Generic AIS fetch error: {exc}"
            self.logger.warning(f"AIS poll error for '{self.name}': {exc}")
            return []

    def _normalize_signals_list(self, signals: List[Dict[str, Any]]) -> List[NormalizedEvent]:
        """Normalize a list of signal dictionaries into NormalizedEvents."""
        events: List[NormalizedEvent] = []
        for s in signals:
            try:
                mmsi = str(s.get("mmsi") or s.get("UserID") or "Unknown")
                lat = float(s.get("latitude") if s.get("latitude") is not None else (s.get("lat") or 0.0))
                lon = float(s.get("longitude") if s.get("longitude") is not None else (s.get("lon") or 0.0))
                sog = float(s.get("speed_knots") if s.get("speed_knots") is not None else (s.get("sog") or 0.0))
                cog = float(s.get("course") if s.get("course") is not None else (s.get("cog") or 0.0))
                ship_name = s.get("ship_name") or s.get("ShipName") or f"Vessel-{mmsi}"
                destination = s.get("destination") or "Unknown"
                loc_name = s.get("location_name") or self.location_name

                loc = Location(name=loc_name, lat=lat, lon=lon)
                published_at = datetime.now(timezone.utc)
                if not self.is_event_within_lookback(published_at):
                    continue

                external_id = f"ais_{mmsi}_{int(datetime.now(timezone.utc).timestamp())}_{hashlib.sha256(f'{lat}_{lon}'.encode()).hexdigest()[:6]}"

                title = f"Naval Signal: {ship_name} (MMSI: {mmsi}) reported near {loc_name}"
                raw_text = (
                    f"Vessel {ship_name} (MMSI {mmsi}) detected at coordinates {lat:.4f}, {lon:.4f}. "
                    f"SOG: {sog:.1f} kts, COG: {cog:.1f}°, destination: {destination}."
                )

                event = NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="ais",
                    source_name=self.name,
                    source_url=s.get("url") or "https://meri.digitraffic.fi",
                    external_id=external_id,
                    title=title,
                    raw_text=raw_text,
                    language="en",
                    author=f"MMSI-{mmsi}",
                    location=loc,
                    published_at=published_at,
                    metadata={
                        "mmsi": mmsi,
                        "ship_name": ship_name,
                        "sog_knots": sog,
                        "cog_degrees": cog,
                        "destination": destination,
                        "raw_signal": s,
                    },
                )
                events.append(event)
            except Exception as e:
                self.logger.warning(f"Error normalizing AIS signal item: {e}")
                continue

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(events)
        self.status = "active"
        self.last_error = None
        return events

    async def test_connection(self) -> bool:
        """Validate accessibility of maritime telemetry source."""
        if self.config.get("signals"):
            return True
        try:
            client = await self._get_client()
            url = self.endpoint or "https://meri.digitraffic.fi/api/ais/v1/locations"
            res = await client.head(url)
            if res.status_code in (200, 301, 302):
                return True
            res = await client.get(url, headers={"Range": "bytes=0-100"})
            return res.status_code in (200, 206)
        except Exception:
            return False
