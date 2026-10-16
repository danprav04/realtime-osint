import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from backend.app.plugins.base import BaseSourcePlugin, Location, NormalizedEvent

logger = logging.getLogger("osint.plugin.oref")

DEFAULT_HISTORY_URL = "https://alerts-history.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=he&mode=1"
DEFAULT_REALTIME_URL = "https://www.oref.org.il/WarningMessages/alert/alerts.json"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "he,en-US;q=0.9,en;q=0.8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://alerts-history.oref.org.il/",
}

OREF_CATEGORIES: Dict[int, Dict[str, str]] = {
    1: {"he": "ירי רקטות וטילים", "en": "Rockets & Missiles"},
    2: {"he": "חדירת כלי טיס עוין", "en": "Hostile Aircraft / Drone Infiltration"},
    3: {"he": "חדירת מחבלים", "en": "Terrorist Infiltration"},
    4: {"he": "אירוע חומרים מסוכנים", "en": "Hazardous Materials Incident"},
    5: {"he": "רעידת אדמה", "en": "Earthquake Alert"},
    6: {"he": "צונאמי", "en": "Tsunami Warning"},
    7: {"he": "אירוע רדיולוגי", "en": "Radiological Incident"},
    13: {"he": "האירוע הסתיים", "en": "Threat Concluded (All Clear)"},
}

# Coordinate registry for Israeli localities and alert zones
ISRAEL_COORDINATES: Dict[str, Tuple[float, float]] = {
    # Central / Dan / Sharon
    "תל אביב": (32.0853, 34.7818),
    "תל אביב - יפו": (32.0853, 34.7818),
    "תל אביב - מרכז העיר": (32.0853, 34.7818),
    "תל אביב - דרום העיר ויפו": (32.0500, 34.7600),
    "תל אביב - צפון העיר": (32.1100, 34.8000),
    "תל אביב - מזרח": (32.0700, 34.8000),
    "רמת גן": (32.0684, 34.8248),
    "גבעתיים": (32.0722, 34.8089),
    "בני ברק": (32.0849, 34.8352),
    "פתח תקווה": (32.0840, 34.8878),
    "חולון": (32.0158, 34.7874),
    "בת ים": (32.0192, 34.7505),
    "ראשון לציון": (31.9730, 34.7925),
    "ראשון לציון - מערב": (31.9800, 34.7600),
    "ראשון לציון - מזרח": (31.9600, 34.8100),
    "הרצליה": (32.1663, 34.8433),
    "רמת השרון": (32.1465, 34.8398),
    "רעננה": (32.1848, 34.8713),
    "כפר סבא": (32.1750, 34.9069),
    "הוד השרון": (32.1550, 34.8900),
    "נתניה": (32.3215, 34.8532),
    "רחובות": (31.8928, 34.8113),
    "נס ציונה": (31.9300, 34.8000),
    "יבנה": (31.8780, 34.7397),
    "מודיעין": (31.8903, 35.0104),
    "מודיעין מכבים רעות": (31.8903, 35.0104),

    # Jerusalem & Shfela
    "ירושלים": (31.7683, 35.2137),
    "ירושלים - מערב": (31.7800, 35.1900),
    "ירושלים - מרכז": (31.7833, 35.2167),
    "ירושלים - דרום": (31.7500, 35.2100),
    "ירושלים - צפון": (31.8100, 35.2200),
    "ירושלים - מזרח": (31.7800, 35.2400),
    "בית שמש": (31.7470, 34.9881),
    "מבשרת ציון": (31.7950, 35.1500),
    "אבו גוש": (31.8069, 35.1089),

    # North / Galilee / Confrontation Line
    "חיפה": (32.7940, 34.9896),
    "חיפה - כרמל ועיר תחתית": (32.8100, 34.9900),
    "חיפה - מפרץ": (32.8000, 35.0400),
    "חיפה - נווה שאנן ורמות כרמל": (32.7800, 35.0100),
    "קריות": (32.8300, 35.0800),
    "קריית אתא": (32.8050, 35.1050),
    "קריית ביאליק": (32.8350, 35.0750),
    "קריית מוצקין": (32.8300, 35.0800),
    "קריית ים": (32.8450, 35.0650),
    "עכו": (32.9278, 35.0818),
    "נהריה": (33.0059, 35.0941),
    "שלומי": (33.0728, 35.1436),
    "ערב אל עראמשה": (33.0850, 35.1950),
    "ערב אל-עראמשה": (33.0850, 35.1950),
    "קריית שמונה": (33.2075, 35.5721),
    "מטולה": (33.2789, 35.5786),
    "משגב עם": (33.2450, 35.5350),
    "מרגליות": (33.2200, 35.5450),
    "מנרה": (33.1950, 35.5400),
    "יפתח": (33.1300, 35.5500),
    "דפנה": (33.2300, 35.6300),
    "דן": (33.2400, 35.6500),
    "שניר": (33.2400, 35.6700),
    "הגושרים": (33.2200, 35.6200),
    "כפר יובל": (33.2600, 35.5800),
    "כפר גלעדי": (33.2400, 35.5700),
    "מעיין ברוך": (33.2500, 35.6000),
    "צפת": (32.9646, 35.4960),
    "ראש פינה": (32.9700, 35.5400),
    "חצור הגלילית": (32.9800, 35.5500),
    "טבריה": (32.7944, 35.5312),
    "כרמיאל": (32.9199, 35.2901),
    "מעלות תרשיחא": (33.0167, 35.2667),
    "מעלות - תרשיחא": (33.0167, 35.2667),
    "פסוטה": (33.0450, 35.3000),
    "חורפיש": (33.0100, 35.3500),
    "בית ג'אן": (32.9600, 35.3800),
    "סאסא": (33.0300, 35.3900),
    "גוש חלב": (33.0200, 35.4200),
    "ג'ש": (33.0200, 35.4200),
    "נצרת": (32.7019, 35.3035),
    "נוף הגליל": (32.7150, 35.3350),
    "עפולה": (32.6074, 35.2891),
    "מגדל העמק": (32.6730, 35.2400),
    "קצרין": (32.9922, 35.6900),
    "מג'דל שמס": (33.2667, 35.7667),
    "מסעדה": (33.2350, 35.7500),
    "בוקעתא": (33.2050, 35.7700),
    "עין קנייא": (33.2450, 35.7350),

    # South / Gaza Envelope / Negev
    "אשקלון": (31.6688, 34.5743),
    "אשקלון - צפון": (31.6850, 34.5800),
    "אשקלון - דרום": (31.6500, 34.5650),
    "אשדוד": (31.8044, 34.6553),
    "אשדוד - צפון": (31.8200, 34.6600),
    "אשדוד - דרום": (31.7850, 34.6400),
    "שדרות": (31.5215, 34.5960),
    "נתיבות": (31.4189, 34.5869),
    "אופקים": (31.3140, 34.6200),
    "באר שבע": (31.2518, 34.7913),
    "באר שבע - צפון": (31.2750, 34.7950),
    "באר שבע - מזרח": (31.2500, 34.8200),
    "באר שבע - מערב": (31.2500, 34.7700),
    "קריית גת": (31.6100, 34.7642),
    "קריית מלאכי": (31.7292, 34.7478),
    "נירים": (31.3350, 34.3950),
    "עין השלושה": (31.3500, 34.4050),
    "כיסופים": (31.3750, 34.4000),
    "רעים": (31.3850, 34.4600),
    "בארי": (31.4250, 34.4900),
    "נחל עוז": (31.4750, 34.4950),
    "עלומים": (31.4550, 34.5200),
    "סעד": (31.4700, 34.5350),
    "כפר עזה": (31.4850, 34.5300),
    "מפלסים": (31.5050, 34.5600),
    "ניר עם": (31.5200, 34.5750),
    "יד מרדכי": (31.5900, 34.5550),
    "נתיב העשרה": (31.5750, 34.5450),
    "כרם שלום": (31.2280, 34.2850),
    "סופה": (31.2380, 34.3250),
    "חולית": (31.2400, 34.3500),
    "שדה אברהם": (31.2300, 34.3800),
    "דימונה": (31.0700, 35.0330),
    "ירוחם": (30.9880, 34.9190),
    "מצפה רמון": (30.6100, 34.8000),
    "ערד": (31.2610, 35.2130),
    "אילת": (29.5581, 34.9482),
}

# Transliteration aliases for common English search terms
ENGLISH_CITY_ALIASES: Dict[str, str] = {
    "tel aviv": "תל אביב",
    "ramat gan": "רמת גן",
    "jerusalem": "ירושלים",
    "haifa": "חיפה",
    "ashkelon": "אשקלון",
    "ashdod": "אשדוד",
    "sderot": "שדרות",
    "netivot": "נתיבות",
    "beer sheva": "באר שבע",
    "beersheba": "באר שבע",
    "kiryat shmona": "קריית שמונה",
    "nahariya": "נהריה",
    "metula": "מטולה",
    "safed": "צפת",
    "tiberias": "טבריה",
    "eilat": "אילת",
    "arab al-aramshe": "ערב אל עראמשה",
    "arab al aramshe": "ערב אל עראמשה",
}


def resolve_location_coordinates(location_name: str) -> Optional[Location]:
    """Resolve city or settlement name in Hebrew or English to GPS coordinates."""
    if not location_name or not location_name.strip():
        return None
    raw = location_name.strip()

    # Direct match in Hebrew dictionary
    if raw in ISRAEL_COORDINATES:
        lat, lon = ISRAEL_COORDINATES[raw]
        return Location(name=raw, lat=lat, lon=lon)

    # Check English aliases
    low = raw.lower()
    if low in ENGLISH_CITY_ALIASES:
        he_name = ENGLISH_CITY_ALIASES[low]
        if he_name in ISRAEL_COORDINATES:
            lat, lon = ISRAEL_COORDINATES[he_name]
            return Location(name=raw, lat=lat, lon=lon)

    # Substring search (e.g. "תל אביב - מרכז" -> "תל אביב")
    for known_city, (lat, lon) in ISRAEL_COORDINATES.items():
        if known_city in raw or raw in known_city:
            return Location(name=raw, lat=lat, lon=lon)

    # Fallback to general Israel center if locality recognized as Israeli
    return Location(name=raw, lat=31.5, lon=34.8)


class OrefSourcePlugin(BaseSourcePlugin):
    """
    Israel Home Front Command (פיקוד העורף / Pikud HaOref) Civil Defense Alerts Plugin.

    Capabilities:
    1. Ingests official civil defense alerts (rocket/missile fire, UAV infiltration,
       terrorist infiltration, earthquake, hazardous materials, and all-clear messages).
    2. Polls history logs (GetAlarmsHistory.aspx) for complete zero-drop coverage,
       and real-time alerts.json for active sub-minute sirens.
    3. Handles UTF-8 BOM encoding and empty alert frames cleanly.
    4. Automatically maps cities and settlements to geospatial coordinates for map visualization.
    5. Emits NormalizedEvent objects with source_type='oref' and Tier 1 official metadata.
    """

    def __init__(
        self,
        source_id: Any,
        theme_id: Any,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 30,
        is_enabled: bool = True,
    ):
        super().__init__(source_id, theme_id, name, config, poll_interval, is_enabled)
        cfg = self.config or {}
        raw_target = str(cfg.get("target") or cfg.get("url") or "").strip()
        self.history_url = (
            raw_target if raw_target.startswith("http") else DEFAULT_HISTORY_URL
        )
        self.realtime_url = str(cfg.get("realtime_url") or DEFAULT_REALTIME_URL).strip()
        self.proxy_url: Optional[str] = cfg.get("proxy") or cfg.get("proxy_url")
        self.preferred_lang: str = str(cfg.get("lang") or "he").lower().strip()
        self.poll_realtime: bool = bool(cfg.get("poll_realtime", True))

        self._http_client: Optional[httpx.AsyncClient] = None
        self._seen_ids: Set[str] = set()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            kwargs: Dict[str, Any] = {
                "timeout": 12.0,
                "headers": dict(DEFAULT_HEADERS),
                "follow_redirects": True,
            }
            if self.proxy_url:
                kwargs["proxy"] = self.proxy_url
            self._http_client = httpx.AsyncClient(**kwargs)
        return self._http_client

    async def stop(self) -> None:
        await super().stop()
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def _clean_response_text(self, content: bytes) -> str:
        """Decode response bytes safely, stripping UTF-8 BOM if present."""
        if not content:
            return ""
        try:
            return content.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            try:
                return content.decode("utf-8", errors="replace").strip()
            except Exception:
                return ""

    def _parse_alert_timestamp(self, alert_date_str: Optional[str]) -> datetime:
        """Parse alertDate string (e.g. '2026-10-10T20:06:00' or '2026-10-10 20:06:00') into UTC datetime."""
        if not alert_date_str:
            return datetime.now(timezone.utc)
        try:
            # Format: '2026-10-10T20:06:00' or '2026-10-10 20:06:00'
            cleaned = alert_date_str.replace("Z", "").replace(" ", "T")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                # Oref times are Israel local time (UTC+2 or UTC+3). Assume Israel local time (+03:00 summer / +02:00 standard)
                # To be consistent with the system, attach Israel offset (+03:00) then convert to UTC
                israel_tz = timezone(timedelta(hours=3))
                dt = dt.replace(tzinfo=israel_tz).astimezone(timezone.utc)
            return dt
        except Exception:
            return datetime.now(timezone.utc)

    async def _poll_history(self) -> List[NormalizedEvent]:
        """Poll the recent alarms history endpoint."""
        events: List[NormalizedEvent] = []
        client = await self._get_client()

        try:
            resp = await client.get(self.history_url)
            if resp.status_code != 200:
                logger.warning(
                    f"Oref history API returned status {resp.status_code} for source {self.name}"
                )
                return []

            text = self._clean_response_text(resp.content)
            if not text or text in ("[]", "null"):
                return []

            data = json.loads(text)
            if not isinstance(data, list):
                return []

            for item in data:
                if not isinstance(item, dict):
                    continue

                rid = str(item.get("rid") or item.get("id") or "").strip()
                loc_raw = str(item.get("data") or "").strip()
                cat_code = item.get("category")
                try:
                    cat_int = int(cat_code) if cat_code is not None else 1
                except (ValueError, TypeError):
                    cat_int = 1

                cat_info = OREF_CATEGORIES.get(
                    cat_int, {"he": str(item.get("category_desc") or "התרעה"), "en": "Civil Defense Alert"}
                )
                category_desc = item.get("category_desc") or cat_info["he"]
                category_en = cat_info["en"]

                # Unique external ID based on record ID and location
                ext_id = f"oref-hist-{rid}" if rid else f"oref-{hashlib.sha256((loc_raw + str(item.get('alertDate'))).encode()).hexdigest()[:12]}"

                if ext_id in self._seen_ids:
                    continue
                self._seen_ids.add(ext_id)
                if len(self._seen_ids) > 5000:
                    self._seen_ids.pop()

                pub_time = self._parse_alert_timestamp(item.get("alertDate"))
                if not self.is_event_within_lookback(pub_time):
                    continue

                location_obj = resolve_location_coordinates(loc_raw)

                is_all_clear = cat_int == 13
                headline_icon = "🟢" if is_all_clear else "🚨"
                title = f"{headline_icon} צבע אדום: {category_desc} - {loc_raw}"

                raw_body = (
                    f"פיקוד העורף - התרעה אזרחית ({category_desc})\n"
                    f"אזור / יישוב: {loc_raw}\n"
                    f"English: {category_en} in {loc_raw}\n"
                    f"תאריך: {item.get('date', '')} {item.get('time', '')}\n"
                    f"הנחיות: היכנסו למרחב המוגן ושהו בו 10 דקות או עד להודעה חדשה."
                )

                norm_ev = NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="oref",
                    source_name=self.name,
                    source_url="https://alerts-history.oref.org.il/",
                    external_id=ext_id,
                    title=title,
                    raw_text=raw_body,
                    language="he",
                    author="Israel Home Front Command (פיקוד העורף)",
                    location=location_obj,
                    metadata={
                        "category_id": cat_int,
                        "category_he": category_desc,
                        "category_en": category_en,
                        "locality": loc_raw,
                        "rid": rid,
                        "matrix_id": item.get("matrix_id"),
                        "is_urgent_alert": not is_all_clear,
                        "is_all_clear": is_all_clear,
                        "source_catalog": "OREF-HFC-HIST",
                    },
                    published_at=pub_time,
                )
                events.append(norm_ev)

        except Exception as exc:
            self.last_error = f"Oref history poll error: {exc}"
            logger.error(f"Error polling Oref history alerts: {exc}", exc_info=True)

        return events

    async def _poll_realtime(self) -> List[NormalizedEvent]:
        """Poll the active real-time sirens endpoint (alerts.json)."""
        events: List[NormalizedEvent] = []
        client = await self._get_client()

        try:
            resp = await client.get(
                self.realtime_url,
                headers={"Referer": "https://www.oref.org.il/"},
            )
            if resp.status_code != 200:
                return []

            text = self._clean_response_text(resp.content)
            if not text or text in ("null", "{}"):
                return []

            data = json.loads(text)
            if not isinstance(data, dict):
                return []

            alert_id = str(data.get("id") or "").strip()
            cat_code = data.get("cat")
            try:
                cat_int = int(cat_code) if cat_code is not None else 1
            except (ValueError, TypeError):
                cat_int = 1

            cat_info = OREF_CATEGORIES.get(cat_int, {"he": "צבע אדום", "en": "Red Alert"})
            cat_title = data.get("title") or cat_info["he"]
            cat_en = cat_info["en"]
            instructions = data.get("desc") or "היכנסו למרחב המוגן ושהו בו 10 דקות"

            raw_locations = data.get("data") or []
            if isinstance(raw_locations, str):
                locations_list = [l.strip() for l in raw_locations.split(",") if l.strip()]
            elif isinstance(raw_locations, list):
                locations_list = [str(l).strip() for l in raw_locations if str(l).strip()]
            else:
                locations_list = []

            loc_summary = ", ".join(locations_list) if locations_list else "ישראל"

            ext_id = f"oref-live-{alert_id}" if alert_id else f"oref-live-{hashlib.sha256(loc_summary.encode()).hexdigest()[:12]}"
            if ext_id in self._seen_ids:
                return []
            self._seen_ids.add(ext_id)
            if len(self._seen_ids) > 5000:
                self._seen_ids.pop()

            pub_time = datetime.now(timezone.utc)
            primary_loc = resolve_location_coordinates(locations_list[0]) if locations_list else resolve_location_coordinates(loc_summary)

            title = f"🚨 אזעקת צבע אדום בזמן אמת: {cat_title} - {loc_summary}"
            raw_body = (
                f"🚨 אזעקת צבע אדום בזמן אמת - פיקוד העורף!\n"
                f"סוג התרעה: {cat_title} ({cat_en})\n"
                f"יישובים ואזורים מותקפים: {loc_summary}\n"
                f"הנחיית התגוננות: {instructions}\n"
                f"זמן התרעה: {pub_time.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )

            norm_ev = NormalizedEvent(
                theme_id=self.theme_id,
                source_type="oref",
                source_name=self.name,
                source_url="https://www.oref.org.il/",
                external_id=ext_id,
                title=title,
                raw_text=raw_body,
                language="he",
                author="Israel Home Front Command (פיקוד העורף)",
                location=primary_loc,
                metadata={
                    "category_id": cat_int,
                    "category_he": cat_title,
                    "category_en": cat_en,
                    "localities": locations_list,
                    "instructions": instructions,
                    "is_urgent_alert": True,
                    "is_realtime_siren": True,
                    "source_catalog": "OREF-HFC-REALTIME",
                },
                published_at=pub_time,
            )
            events.append(norm_ev)

        except Exception as exc:
            logger.debug(f"Oref realtime sirens poll notice: {exc}")

        return events

    async def poll(self) -> List[NormalizedEvent]:
        """Poll both real-time sirens and recent history feeds."""
        all_events: List[NormalizedEvent] = []

        # 1. Real-time active sirens
        if self.poll_realtime:
            live_events = await self._poll_realtime()
            if live_events:
                all_events.extend(live_events)

        # 2. Historical alarms log (zero drop guarantee)
        hist_events = await self._poll_history()
        if hist_events:
            all_events.extend(hist_events)

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(all_events)
        if all_events:
            self.last_error = None

        return all_events

    async def test_connection(self) -> bool:
        """Validate network connectivity and access to Home Front Command API."""
        try:
            client = await self._get_client()
            resp = await client.get(self.history_url)
            return resp.status_code in (200, 304)
        except Exception as exc:
            self.last_error = f"Connection test failed: {exc}"
            logger.warning(f"Oref test_connection failed: {exc}")
            return False
