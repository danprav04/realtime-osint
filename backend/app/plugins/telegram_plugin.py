import asyncio
import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent

logger = logging.getLogger("osint.plugin.telegram")


def is_farsi(text: str) -> bool:
    """Check for distinctive Persian characters (Gachpazh, Persian Kaf/Yeh, ZWNJ)."""
    return any(c in text for c in "پچگژ\u200c") or bool(re.search(r"[\u06A9\u06CC]", text))


def is_arabic(text: str) -> bool:
    """Check for Arabic characters."""
    return any(c in text for c in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي")


def is_hebrew(text: str) -> bool:
    """Check for Hebrew characters."""
    return any("\u0590" <= c <= "\u05EA" for c in text)


def clean_html(raw: str) -> str:
    """Strip tags from web preview snippets."""
    if not raw:
        return ""
    clean = re.sub(r"<br\s*/?>", "\n", raw)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    return clean.strip()


class TelegramSourcePlugin(BaseSourcePlugin):
    """
    Telegram public channel monitoring plugin.
    Supports real-time Telethon MTProto client when API credentials are provided.
    Features robust resilient degradation: if MTProto credentials are missing or invalid,
    logs a descriptive warning and gracefully falls back to public web preview scraping
    (https://t.me/s/{channel}) without crashing or impacting peer plugins.
    """

    def __init__(
        self,
        source_id: Any,
        theme_id: Any,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 60,
        is_enabled: bool = True,
    ):
        super().__init__(source_id, theme_id, name, config, poll_interval, is_enabled)
        # Channel can be specified as @channel_name, https://t.me/channel_name, or channel_name
        channels_list = self.config.get("channels")
        first_channel = channels_list[0] if isinstance(channels_list, list) and channels_list else ""
        self.raw_target: str = (
            self.config.get("target")
            or self.config.get("channel")
            or self.config.get("channel_id")
            or first_channel
            or ""
        )
        self.channel_name: str = self._sanitize_channel_name(self.raw_target)
        self.api_id: Optional[Any] = self.config.get("api_id")
        self.api_hash: Optional[str] = self.config.get("api_hash")
        self.session_string: Optional[str] = (
            self.config.get("session_string") or self.config.get("string_session")
        )
        self.filter_keywords: List[str] = [
            kw.strip().lower() for kw in (self.config.get("filter_keywords") or []) if kw.strip()
        ]
        self.is_alert_channel: bool = bool(
            self.config.get("is_alert_channel")
            or any(k in self.channel_name.lower() for k in ("rocketalert", "miklat", "tzeva", "siren"))
        )
        self._http_client: Optional[httpx.AsyncClient] = None
        self._telethon_client: Optional[Any] = None
        self._mtproto_available: bool = False
        self._last_etag: Optional[str] = None
        self._last_modified: Optional[str] = None

    @staticmethod
    def _sanitize_channel_name(raw: str) -> str:
        s = raw.strip()
        s = re.sub(r"^https?://t\.me/(s/)?", "", s)
        s = s.lstrip("@").strip("/").split("/")[0]
        return s

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=12.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8,ar;q=0.7,he;q=0.6",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                },
                follow_redirects=True,
            )
        return self._http_client

    async def start(self) -> None:
        await super().start()
        # Attempt MTProto initialization if credentials provided
        if self.api_id and self.api_hash:
            try:
                from telethon import TelegramClient
                from telethon.sessions import StringSession

                session = StringSession(self.session_string or "")
                self._telethon_client = TelegramClient(session, int(self.api_id), str(self.api_hash))
                # Note: connect without blocking for interactive phone prompt
                await self._telethon_client.connect()
                if await self._telethon_client.is_user_authorized():
                    self._mtproto_available = True
                    self.logger.info(f"Telethon MTProto client successfully authorized for {self.name}.")
                else:
                    self.logger.warning(
                        f"Telethon MTProto client not authorized for {self.name}. "
                        "Falling back to public web preview scraping."
                    )
                    self._mtproto_available = False
            except Exception as exc:
                self.logger.warning(
                    f"Telethon MTProto initialization failed for {self.name}: {exc}. "
                    "Gracefully falling back to public web preview scraping."
                )
                self._mtproto_available = False
        else:
            self.logger.info(
                f"No Telegram MTProto credentials provided for '{self.name}'. "
                "Operating in resilient public web preview scraping mode."
            )
            self._mtproto_available = False

    async def stop(self) -> None:
        await super().stop()
        if self._telethon_client:
            try:
                await self._telethon_client.disconnect()
            except Exception:
                pass
            self._telethon_client = None
            self._mtproto_available = False

        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def poll(self) -> List[NormalizedEvent]:
        """Poll channel messages via MTProto client or resilient web preview fallback."""
        if not self.channel_name:
            self.logger.warning(f"Telegram plugin '{self.name}' has no channel target configured.")
            return []

        # 1. Try MTProto if available and authorized
        if self._mtproto_available and self._telethon_client:
            try:
                events = await self._poll_telethon()
                if events:
                    self.last_polled_at = datetime.now(timezone.utc)
                    self.events_count += len(events)
                    self.status = "active"
                    self.last_error = None
                    return events
            except Exception as exc:
                self.logger.warning(
                    f"Telethon MTProto poll failed for '{self.name}': {exc}. "
                    "Falling back to web preview scraping."
                )

        # 2. Resilient web preview fallback (https://t.me/s/{channel_name})
        events = await self._poll_web_preview()
        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(events)
        self.status = "active"
        return events

    async def _poll_telethon(self) -> List[NormalizedEvent]:
        """Fetch messages via Telethon client."""
        events: List[NormalizedEvent] = []
        entity = await self._telethon_client.get_entity(self.channel_name)
        messages = await self._telethon_client.get_messages(entity, limit=20)

        for msg in messages:
            if not msg or not msg.text:
                continue

            raw_text = msg.text.strip()
            msg_id = msg.id
            external_id = f"tg_{self.channel_name}_{msg_id}"
            published_at = msg.date.replace(tzinfo=timezone.utc) if msg.date else datetime.now(timezone.utc)

            # Discard messages older than configured lookback window
            if not self.is_event_within_lookback(published_at):
                continue

            # Keyword filter (for high-volume discussion groups or filtered channels)
            if self.filter_keywords and not any(kw in raw_text.lower() for kw in self.filter_keywords):
                continue

            lang = "fa" if is_farsi(raw_text) else ("ar" if is_arabic(raw_text) else ("he" if is_hebrew(raw_text) else "en"))
            is_urgent = self.is_alert_channel or any(
                s in raw_text for s in ("🚨", "🔴", "صفارات الإنذار", "צבע אדום", "rocket alert", "red alert")
            )
            title = raw_text[:90].replace("\n", " ").strip()
            if len(raw_text) > 90:
                title += "..."

            event = NormalizedEvent(
                theme_id=self.theme_id,
                source_type="telegram",
                source_name=self.name,
                source_url=f"https://t.me/{self.channel_name}/{msg_id}",
                external_id=external_id,
                title=title,
                raw_text=raw_text,
                language=lang,
                author=f"@{self.channel_name}",
                published_at=published_at,
                metadata={
                    "channel_id": f"@{self.channel_name}",
                    "message_id": msg_id,
                    "views": getattr(msg, "views", None),
                    "forwards": getattr(msg, "forwards", None),
                    "is_urgent_alert": is_urgent,
                    "is_alert_channel": self.is_alert_channel,
                },
            )
            events.append(event)

        return events

    async def _poll_web_preview(self) -> List[NormalizedEvent]:
        """
        Public web preview scraper for public Telegram channels.
        Fetches https://t.me/s/{channel_name} and extracts message cards.
        Supports ETag / If-Modified-Since conditional caching.
        """
        url = f"https://t.me/s/{self.channel_name}"
        events: List[NormalizedEvent] = []

        try:
            client = await self._get_http_client()
            req_headers: Dict[str, str] = {}
            if self._last_etag:
                req_headers["If-None-Match"] = self._last_etag
            if self._last_modified:
                req_headers["If-Modified-Since"] = self._last_modified

            response = await client.get(url, headers=req_headers)
            if response.status_code == 304:
                return []
            if response.status_code != 200:
                self.logger.warning(
                    f"Telegram web preview for '@{self.channel_name}' returned status {response.status_code}"
                )
                return []
            resp_headers = getattr(response, "headers", None) or {}
            if resp_headers.get("etag"):
                self._last_etag = resp_headers.get("etag")
            if resp_headers.get("last-modified"):
                self._last_modified = resp_headers.get("last-modified")
            html = response.text
        except Exception as exc:
            self.last_error = f"Web preview HTTP error: {exc}"
            self.logger.warning(f"Failed to fetch Telegram web preview for '@{self.channel_name}': {exc}")
            return []

        # Find each message wrapper by data-post attribute
        matches = list(re.finditer(r'data-post="([^"/]+)/(\d+)"', html))
        for idx, match in enumerate(matches):
            post_chan = match.group(1)
            msg_id_str = match.group(2)
            start_pos = match.start()
            end_pos = matches[idx + 1].start() if idx + 1 < len(matches) else len(html)
            block = html[start_pos:end_pos]

            text_match = re.search(
                r'<div class="[^"]*tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                block,
                re.DOTALL,
            )
            raw_msg_html = text_match.group(1) if text_match else ""
            text = clean_html(raw_msg_html)
            if not text:
                continue

            time_match = re.search(r'<time[^>]*datetime="([^"]+)"', block)
            dt_str = time_match.group(1) if time_match else None

            msg_id = int(msg_id_str)
            external_id = f"tg_{self.channel_name}_{msg_id}"

            # Parse datetime
            published_at: datetime
            if dt_str:
                try:
                    published_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                except Exception:
                    published_at = datetime.now(timezone.utc)
            else:
                published_at = datetime.now(timezone.utc)

            # Discard messages older than configured lookback window
            if not self.is_event_within_lookback(published_at):
                continue

            # Keyword filter (for high-volume discussion groups or filtered channels)
            if self.filter_keywords and not any(kw in text.lower() for kw in self.filter_keywords):
                continue

            lang = "fa" if is_farsi(text) else ("ar" if is_arabic(text) else ("he" if is_hebrew(text) else "en"))
            is_urgent = self.is_alert_channel or any(
                s in text for s in ("🚨", "🔴", "صفارات الإنذار", "צבע אדوم", "rocket alert", "red alert")
            )
            title = text[:90].replace("\n", " ").strip()
            if len(text) > 90:
                title += "..."

            event = NormalizedEvent(
                theme_id=self.theme_id,
                source_type="telegram",
                source_name=self.name,
                source_url=f"https://t.me/{self.channel_name}/{msg_id}",
                external_id=external_id,
                title=title,
                raw_text=text,
                language=lang,
                author=f"@{self.channel_name}",
                published_at=published_at,
                metadata={
                    "channel_id": f"@{self.channel_name}",
                    "message_id": msg_id,
                    "channel": self.channel_name,
                    "is_urgent_alert": is_urgent,
                    "is_alert_channel": self.is_alert_channel,
                },
            )
            events.append(event)

        return events

    async def test_connection(self) -> bool:
        """Validate accessibility of the Telegram channel."""
        if not self.channel_name:
            return False

        if self._mtproto_available and self._telethon_client:
            try:
                entity = await self._telethon_client.get_entity(self.channel_name)
                return entity is not None
            except Exception:
                pass

        try:
            client = await self._get_http_client()
            res = await client.get(f"https://t.me/s/{self.channel_name}")
            return res.status_code == 200
        except Exception:
            return False
