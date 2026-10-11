import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
import httpx

from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent

logger = logging.getLogger("osint.plugin.mastodon")


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _detect_lang(text: str, hint: Optional[str] = None) -> str:
    if any(c in text for c in "پچگژ\u200c") or bool(re.search(r"[\u06A9\u06CC]", text)):
        return "fa"
    if any(c in text for c in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"):
        return "ar"
    if any("\u0590" <= c <= "\u05EA" for c in text):
        return "he"
    if hint and hint[:2].lower() in ("ar", "fa", "he", "en"):
        return hint[:2].lower()
    return "en"


class MastodonSourcePlugin(BaseSourcePlugin):
    """
    Mastodon / Fediverse unauthenticated OSINT ingestion plugin.
    Supports:
    - Public hashtag timeline JSON: https://{instance}/api/v1/timelines/tag/{tag}?limit=20
    - Public hashtag/account RSS: https://{instance}/tags/{tag}.rss or https://{instance}/@{user}.rss
    - Shorthand target: '#OSINT@mastodon.social', 'mastodon.social#OSINT', or '@user@infosec.exchange'
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
        self.raw_target: str = str(
            self.config.get("target")
            or self.config.get("url")
            or self.config.get("tag")
            or "https://mastodon.social/api/v1/timelines/tag/OSINT?limit=20"
        ).strip()
        self.instance: str = str(self.config.get("instance") or "mastodon.social").strip()
        self.limit: int = min(int(self.config.get("limit", 20)), 40)
        self._http_client: Optional[httpx.AsyncClient] = None
        self._last_etag: Optional[str] = None
        self._last_modified: Optional[str] = None

    def _resolve_endpoint(self) -> str:
        t = self.raw_target
        if t.startswith("http://") or t.startswith("https://"):
            return t
        # Handle #tag@instance or tag@instance
        if "@" in t and not t.startswith("@"):
            tag_part, inst_part = t.split("@", 1)
            tag_clean = tag_part.lstrip("#").strip()
            return f"https://{inst_part.strip()}/api/v1/timelines/tag/{tag_clean}?limit={self.limit}"
        # Handle @user@instance
        if t.startswith("@") and t.count("@") >= 2:
            parts = t.lstrip("@").split("@", 1)
            user_part, inst_part = parts[0], parts[1]
            return f"https://{inst_part.strip()}/@{user_part.strip()}.rss"
        # Handle instance#tag
        if "#" in t and not t.startswith("#"):
            inst_part, tag_part = t.split("#", 1)
            return f"https://{inst_part.strip().rstrip('/')}/api/v1/timelines/tag/{tag_part.strip()}?limit={self.limit}"
        # Handle bare hashtag #OSINT or OSINT
        tag_clean = t.lstrip("#").strip()
        return f"https://{self.instance}/api/v1/timelines/tag/{tag_clean}?limit={self.limit}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": "OSINT-Monitor/2.0 (Academic Research; +https://localhost)",
                    "Accept": "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.8",
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
        url = self._resolve_endpoint()
        events: List[NormalizedEvent] = []
        try:
            client = await self._get_client()
            req_headers: Dict[str, str] = {}
            if self._last_etag:
                req_headers["If-None-Match"] = self._last_etag
            if self._last_modified:
                req_headers["If-Modified-Since"] = self._last_modified

            response = await client.get(url, headers=req_headers)
            if response.status_code == 304:
                self.last_polled_at = datetime.now(timezone.utc)
                self.status = "active"
                return []

            if response.status_code != 200:
                self.logger.warning(f"Mastodon endpoint '{url}' returned status {response.status_code}")
                self.status = "error"
                self.last_error = f"HTTP {response.status_code}"
                return []

            resp_headers = getattr(response, "headers", None) or {}
            if resp_headers.get("etag"):
                self._last_etag = resp_headers.get("etag")
            if resp_headers.get("last-modified"):
                self._last_modified = resp_headers.get("last-modified")

            content_type = str(resp_headers.get("content-type", "")).lower()
            if url.endswith(".rss") or "xml" in content_type or "rss" in content_type:
                events = self._parse_rss(response.text)
            else:
                data = response.json()
                if isinstance(data, list):
                    for item in data:
                        ev = self._parse_status_json(item)
                        if ev and self.is_event_within_lookback(ev.published_at):
                            events.append(ev)

            self.last_polled_at = datetime.now(timezone.utc)
            self.events_count += len(events)
            self.status = "active"
            self.last_error = None
        except Exception as exc:
            self.status = "error"
            self.last_error = str(exc)
            self.logger.warning(f"Mastodon poll error for '{self.name}' ({url}): {exc}")

        return events

    def _parse_status_json(self, item: Dict[str, Any]) -> Optional[NormalizedEvent]:
        if not isinstance(item, dict):
            return None
        raw_html = str(item.get("content") or "")
        text = _strip_html(raw_html)
        if not text:
            return None

        status_id = str(item.get("id") or "")
        status_url = str(item.get("url") or item.get("uri") or "")
        account = item.get("account") or {}
        acct = str(account.get("acct") or account.get("username") or "fediverse")

        created_str = str(item.get("created_at") or "")
        published_at = datetime.now(timezone.utc)
        if created_str:
            try:
                published_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except Exception:
                published_at = datetime.now(timezone.utc)

        lang = _detect_lang(text, item.get("language"))
        title = f"@{acct}: {text[:85]}".replace("\n", " ").strip()
        if len(text) > 85:
            title += "..."

        tags = [t.get("name") for t in (item.get("tags") or []) if isinstance(t, dict) and t.get("name")]

        return NormalizedEvent(
            theme_id=self.theme_id,
            source_type="mastodon",
            source_name=self.name,
            source_url=status_url,
            external_id=f"masto_{acct}_{status_id}",
            title=title,
            raw_text=text,
            language=lang,
            author=f"@{acct}",
            published_at=published_at,
            metadata={
                "status_id": status_id,
                "acct": acct,
                "reblogs_count": item.get("reblogs_count", 0),
                "favourites_count": item.get("favourites_count", 0),
                "tags": tags,
            },
        )

    def _parse_rss(self, xml_text: str) -> List[NormalizedEvent]:
        events: List[NormalizedEvent] = []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return []

        for item in root.findall(".//item"):
            link = (item.findtext("link") or item.findtext("guid") or "").strip()
            desc = _strip_html(item.findtext("description") or "")
            if not desc:
                continue
            pub_str = (item.findtext("pubDate") or "").strip()
            published_at = datetime.now(timezone.utc)
            if pub_str:
                try:
                    dt = parsedate_to_datetime(pub_str)
                    published_at = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except Exception:
                    published_at = datetime.now(timezone.utc)

            if not self.is_event_within_lookback(published_at):
                continue

            ext_id = f"masto_rss_{ abs(hash(link or desc[:64])) }"
            lang = _detect_lang(desc)
            title = desc[:85].replace("\n", " ").strip()
            if len(desc) > 85:
                title += "..."

            events.append(
                NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="mastodon",
                    source_name=self.name,
                    source_url=link,
                    external_id=ext_id,
                    title=title,
                    raw_text=desc,
                    language=lang,
                    author=self.name,
                    published_at=published_at,
                    metadata={"rss_link": link},
                )
            )
        return events

    async def test_connection(self) -> bool:
        try:
            client = await self._get_client()
            res = await client.get(self._resolve_endpoint())
            return res.status_code in (200, 304)
        except Exception:
            return False
