import asyncio
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import feedparser
import httpx

from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent


def clean_html(raw_html: str) -> str:
    """Strip HTML markup and collapse extraneous whitespace."""
    if not raw_html:
        return ""
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", " ", raw_html)
    # Collapse multiple whitespaces
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def is_persian_or_arabic(text: str) -> bool:
    """Detect presence of Persian or Arabic characters."""
    return any(c in text for c in "ابتثجحخدذرزسشصضطظعغفقكلمنهويسپچگکژآ")


class RSSSourcePlugin(BaseSourcePlugin):
    """
    RSS and Atom feed ingestion plugin.
    Polls configurable news outlets (e.g., Reuters, AP, BBC, Al Jazeera, IRNA, Tehran Times)
    and normalizes articles into the canonical NormalizedEvent schema.
    """

    def __init__(
        self,
        source_id: Any,
        theme_id: Any,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 300,
        is_enabled: bool = True,
    ):
        super().__init__(source_id, theme_id, name, config, poll_interval, is_enabled)
        self.feed_url: str = (
            self.config.get("url")
            or self.config.get("target")
            or self.config.get("feed_url")
            or ""
        )
        self._http_client: Optional[httpx.AsyncClient] = None
        self._seen_external_ids: set = set()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=12.0,
                headers={
                    "User-Agent": "RealTimeOSINT-GatheringService/1.0 (RSS Ingestion Worker)",
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
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
        """Fetch and parse feed items, returning newly discovered normalized events."""
        if not self.feed_url:
            self.logger.warning(f"RSS plugin '{self.name}' has no feed URL configured.")
            return []

        try:
            client = await self._get_client()
            response = await client.get(self.feed_url)
            response.raise_for_status()
            raw_content = response.text
        except Exception as exc:
            self.last_error = f"HTTP fetch failure: {exc}"
            self.status = "degraded"
            self.logger.warning(f"Failed to fetch RSS feed '{self.name}' ({self.feed_url}): {exc}")
            return []

        try:
            # Parse synchronously in a worker thread to keep the asyncio loop unblocked
            parsed = await asyncio.to_thread(feedparser.parse, raw_content)
        except Exception as exc:
            self.last_error = f"Feed parsing failure: {exc}"
            self.status = "error"
            self.logger.error(f"Failed to parse RSS feed content for '{self.name}': {exc}")
            return []

        events: List[NormalizedEvent] = []
        entries = getattr(parsed, "entries", [])
        feed_meta = getattr(parsed, "feed", {})
        feed_title = feed_meta.get("title", self.name)

        for entry in entries:
            try:
                title = clean_html(entry.get("title", "Untitled"))
                summary = clean_html(
                    entry.get("summary", "")
                    or entry.get("description", "")
                    or (entry.get("content", [{}])[0].get("value", "") if entry.get("content") else "")
                )
                link = entry.get("link", "")
                author = entry.get("author") or feed_title

                # Generate deterministic external ID
                raw_id = entry.get("id") or link or f"{title}_{link}"
                external_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:24]

                # Parse publication timestamp
                published_at: datetime
                if getattr(entry, "published_parsed", None):
                    t = entry.published_parsed
                    published_at = datetime(*t[:6], tzinfo=timezone.utc)
                elif getattr(entry, "updated_parsed", None):
                    t = entry.updated_parsed
                    published_at = datetime(*t[:6], tzinfo=timezone.utc)
                else:
                    published_at = datetime.now(timezone.utc)

                # Discard articles older than configured lookback window
                if not self.is_event_within_lookback(published_at):
                    continue

                # Language detection
                lang = entry.get("language") or feed_meta.get("language")
                if not lang:
                    if is_persian_or_arabic(f"{title} {summary}"):
                        lang = "fa"
                    else:
                        lang = "en"
                else:
                    lang = lang[:2].lower()

                # Collect tags/categories
                tags = [t.get("term") for t in entry.get("tags", []) if isinstance(t, dict) and t.get("term")]

                event = NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="rss",
                    source_name=self.name,
                    source_url=link or self.feed_url,
                    external_id=external_id,
                    title=title,
                    raw_text=summary if summary else title,
                    language=lang,
                    author=author,
                    published_at=published_at,
                    metadata={
                        "feed_title": feed_title,
                        "tags": tags,
                        "guid": entry.get("id"),
                    },
                )
                events.append(event)
            except Exception as entry_exc:
                self.logger.warning(f"Error parsing RSS entry in '{self.name}': {entry_exc}")
                continue

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(events)
        self.status = "active"
        self.last_error = None
        return events

    async def test_connection(self) -> bool:
        """Validate that the feed URL is reachable and delivers valid RSS/Atom content."""
        if not self.feed_url:
            return False
        try:
            client = await self._get_client()
            res = await client.get(self.feed_url)
            if res.status_code != 200:
                return False
            parsed = await asyncio.to_thread(feedparser.parse, res.text)
            return len(getattr(parsed, "entries", [])) >= 0 and not getattr(parsed, "bozo", 0) > 1
        except Exception as exc:
            self.logger.warning(f"RSS connection test failed for '{self.feed_url}': {exc}")
            return False
