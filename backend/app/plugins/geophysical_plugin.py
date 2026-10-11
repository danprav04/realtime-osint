import hashlib
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import httpx

from backend.app.plugins.base import BaseSourcePlugin, Location, NormalizedEvent

logger = logging.getLogger("osint.plugin.geophysical")

DEFAULT_EMSC_URL = (
    "https://www.seismicportal.eu/fdsnws/event/1/query"
    "?format=json&minlat=12&maxlat=40&minlon=32&maxlon=64&limit=15"
)
DEFAULT_UKMTO_URL = "https://www.ukmto.org/indian-ocean/recent-incidents"


def _parse_dms_coords(text: str) -> Optional[Tuple[float, float]]:
    """Extract DMS or decimal coordinates from UKMTO advisory text."""
    # Pattern like 14°50'N 042°15'E or 14 50N 042 15E
    m = re.search(
        r"(\d{1,2})[°\s]+(\d{1,2})(?:['′]\s*(\d{1,2}))?\s*([NS])[,\s/]+(\d{2,3})[°\s]+(\d{1,2})(?:['′]\s*(\d{1,2}))?\s*([EW])",
        text,
        re.IGNORECASE,
    )
    if m:
        lat_d, lat_m, lat_s, lat_dir = float(m.group(1)), float(m.group(2)), float(m.group(3) or 0), m.group(4).upper()
        lon_d, lon_m, lon_s, lon_dir = float(m.group(5)), float(m.group(6)), float(m.group(7) or 0), m.group(8).upper()
        lat = lat_d + lat_m / 60.0 + lat_s / 3600.0
        lon = lon_d + lon_m / 60.0 + lon_s / 3600.0
        if lat_dir == "S":
            lat = -lat
        if lon_dir == "W":
            lon = -lon
        return round(lat, 4), round(lon, 4)
    return None


class GeophysicalSourcePlugin(BaseSourcePlugin):
    """
    Physical & Geophysical Proxy Sensor Plugin.
    Supports two unauthenticated open-web sensor feeds:
    1. 'seismic' / 'emsc': EMSC Seismic Portal & LastQuake Felt Reports API
       (seismicportal.eu). Flags shallow/zero-depth (<= 2.0 km) events in the Middle East
       bounding box (Lat 12..40, Lon 32..64) as potential large kinetic surface/bunker
       detonations or acoustic shockwave triggers.
    2. 'ukmto': UK Maritime Trade Operations (ukmto.org) Red Sea, Gulf of Aden,
       Bab el-Mandeb, and Persian Gulf maritime security incident advisories.
    """

    def __init__(
        self,
        source_id: Any,
        theme_id: Any,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 120,
        is_enabled: bool = True,
        sensor_type: str = "seismic",
    ):
        super().__init__(source_id, theme_id, name, config, poll_interval, is_enabled)
        raw_target = str(self.config.get("target") or self.config.get("url") or "").strip()
        inferred_type = str(self.config.get("sensor_type") or sensor_type).lower().strip()
        if "ukmto" in raw_target.lower() or inferred_type == "ukmto":
            self.sensor_type = "ukmto"
            self.url = raw_target if raw_target.startswith("http") else DEFAULT_UKMTO_URL
        else:
            self.sensor_type = "seismic"
            self.url = raw_target if raw_target.startswith("http") else DEFAULT_EMSC_URL

        self._http_client: Optional[httpx.AsyncClient] = None
        self._last_etag: Optional[str] = None
        self._last_modified: Optional[str] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": "OSINT-Monitor/2.0 (Academic Research; +https://localhost)",
                    "Accept": "application/json, text/html, application/xhtml+xml, */*;q=0.8",
                },
                follow_redirects=True,
            )
        return self._http_client

    async def stop(self) -> None:
        await super().stop()
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def poll(self) -> List[NormalizedEvent]:
        try:
            if self.sensor_type == "ukmto":
                events = await self._poll_ukmto()
            else:
                events = await self._poll_emsc()

            self.last_polled_at = datetime.now(timezone.utc)
            self.events_count += len(events)
            self.status = "active"
            self.last_error = None
            return events
        except Exception as exc:
            self.status = "error"
            self.last_error = str(exc)
            self.logger.warning(f"Geophysical plugin '{self.name}' ({self.sensor_type}) poll failed: {exc}")
            return []

    async def _poll_emsc(self) -> List[NormalizedEvent]:
        client = await self._get_client()
        req_headers: Dict[str, str] = {}
        if self._last_etag:
            req_headers["If-None-Match"] = self._last_etag
        if self._last_modified:
            req_headers["If-Modified-Since"] = self._last_modified

        response = await client.get(self.url, headers=req_headers)
        if response.status_code == 304:
            return []
        if response.status_code != 200:
            self.logger.warning(f"EMSC API returned status {response.status_code}")
            return []

        resp_headers = getattr(response, "headers", None) or {}
        if resp_headers.get("etag"):
            self._last_etag = resp_headers["etag"]
        if resp_headers.get("last-modified"):
            self._last_modified = resp_headers["last-modified"]

        data = response.json()
        features = data.get("features", []) if isinstance(data, dict) else []
        events: List[NormalizedEvent] = []

        for feat in features:
            if not isinstance(feat, dict):
                continue
            props = feat.get("properties") or {}
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []

            unid = str(props.get("unid") or feat.get("id") or "")
            region = str(props.get("flynn_region") or "MIDDLE EAST").strip()
            mag = float(props.get("mag") or 0.0)
            mag_type = str(props.get("magtype") or "M").strip()
            depth = float(
                props.get("depth")
                if props.get("depth") is not None
                else ( abs(float(coords[2])) if len(coords) >= 3 and coords[2] is not None else 10.0 )
            )
            depth = abs(depth)

            lon = float(coords[0]) if len(coords) >= 2 else float(props.get("lon") or 0.0)
            lat = float(coords[1]) if len(coords) >= 2 else float(props.get("lat") or 0.0)

            time_str = str(props.get("time") or "")
            published_at = datetime.now(timezone.utc)
            if time_str:
                try:
                    published_at = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                except Exception:
                    published_at = datetime.now(timezone.utc)

            if not self.is_event_within_lookback(published_at):
                continue

            is_shallow = depth <= 2.0
            surface_note = (
                " [SHALLOW/SURFACE ANOMALY <=2km: Potential kinetic detonation, munitions depot blast, or LastQuake shockwave trigger]"
                if is_shallow
                else ""
            )

            title = f"EMSC {mag_type}{mag:.1f} ({depth:.1f}km depth) — {region}"
            if is_shallow:
                title = f"[SURFACE/SHALLOW {depth:.1f}km] {title}"

            raw_text = (
                f"EMSC Geophysical / LastQuake Event in {region}: Magnitude {mag:.1f} ({mag_type}), "
                f"Depth {depth:.1f} km at coordinates ({lat:.4f}, {lon:.4f}).{surface_note}"
            )

            events.append(
                NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="seismic",
                    source_name=self.name,
                    source_url=f"https://www.emsc-csem.org/Earthquake/earthquake.php?id={unid}",
                    external_id=f"emsc_{unid}",
                    title=title,
                    raw_text=raw_text,
                    language="en",
                    author="EMSC-CSEM",
                    published_at=published_at,
                    location=Location(
                        lat=lat,
                        lon=lon,
                        name=region,
                    ),
                    metadata={
                        "unid": unid,
                        "magnitude": mag,
                        "mag_type": mag_type,
                        "depth_km": depth,
                        "flynn_region": region,
                        "is_shallow_surface_event": is_shallow,
                        "source_catalog": props.get("source_catalog", "EMSC-RTS"),
                    },
                )
            )

        return events

    async def _poll_ukmto(self) -> List[NormalizedEvent]:
        client = await self._get_client()
        req_headers: Dict[str, str] = {}
        if self._last_etag:
            req_headers["If-None-Match"] = self._last_etag
        if self._last_modified:
            req_headers["If-Modified-Since"] = self._last_modified

        response = await client.get(self.url, headers=req_headers)
        if response.status_code == 304:
            return []
        if response.status_code != 200:
            self.logger.warning(f"UKMTO endpoint returned status {response.status_code}")
            return []

        resp_headers = getattr(response, "headers", None) or {}
        if resp_headers.get("etag"):
            self._last_etag = resp_headers["etag"]
        if resp_headers.get("last-modified"):
            self._last_modified = resp_headers["last-modified"]

        raw_html = response.text
        events: List[NormalizedEvent] = []

        # Extract incident blocks or table rows mentioning UKMTO Warning / Advisory / Incident
        blocks = re.findall(
            r"(?:<(?:article|tr|div|li)[^>]*>)(.*?)(?:</(?:article|tr|div|li)>)",
            raw_html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        seen_hashes = set()
        for blk in blocks:
            clean = re.sub(r"<[^>]+>", " ", blk)
            clean = html.unescape(re.sub(r"\s+", " ", clean)).strip()
            if len(clean) < 40 or len(clean) > 1500:
                continue
            if not any(
                k in clean.lower()
                for k in ("ukmto", "advisory", "warning", "incident", "nautical miles", "vessel", "red sea", "aden", "hormuz")
            ):
                continue

            digest = hashlib.sha1(clean[:200].encode("utf-8")).hexdigest()[:14]
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            coords = _parse_dms_coords(clean)
            loc = (
                Location(
                    lat=coords[0],
                    lon=coords[1],
                    name="UKMTO Maritime Reporting Area",
                )
                if coords
                else Location(
                    lat=14.5,
                    lon=42.5,
                    name="Red Sea / Gulf of Aden (UKMTO)",
                )
            )

            title = f"UKMTO Maritime Security Notice: {clean[:80]}"
            if len(clean) > 80:
                title += "..."

            events.append(
                NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="ukmto",
                    source_name=self.name,
                    source_url=self.url,
                    external_id=f"ukmto_{digest}",
                    title=title,
                    raw_text=clean,
                    language="en",
                    author="UKMTO",
                    published_at=datetime.now(timezone.utc),
                    location=loc,
                    metadata={"sensor": "ukmto", "has_exact_dms": coords is not None},
                )
            )
            if len(events) >= 15:
                break

        return events

    async def test_connection(self) -> bool:
        try:
            client = await self._get_client()
            res = await client.get(self.url)
            return res.status_code in (200, 304)
        except Exception:
            return False
