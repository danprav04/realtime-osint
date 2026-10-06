import hashlib
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent


class NewsAPISourcePlugin(BaseSourcePlugin):
    """
    News API Ingestion Plugin.
    Integrates:
    1. GDELT 2.0 Doc API (100% Free, keyless, global multilingual OSINT coverage).
    2. NewsAPI.org (commercial REST API with api_key).
    3. Currents API (international news API with api_key).
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
        self.provider: str = (self.config.get("provider") or "gdelt").lower()
        self.api_key: Optional[str] = self.config.get("api_key")
        self.query: str = (
            self.config.get("query")
            or self.config.get("keywords")
            or self.config.get("target")
            or "geopolitics"
        )
        self.max_records: int = int(self.config.get("max_records") or 25)
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                headers={"User-Agent": "RealTimeOSINT-NewsAPIPlugin/1.0"},
                follow_redirects=True,
            )
        return self._http_client

    async def stop(self) -> None:
        await super().stop()
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def poll(self) -> List[NormalizedEvent]:
        """Poll chosen provider and return canonical NormalizedEvents."""
        provider = self.provider

        # Auto-route: if newsapi or currents is selected but no api_key is supplied, fallback to GDELT
        if provider in ("newsapi", "currents") and not self.api_key:
            self.logger.warning(
                f"Provider '{provider}' requires an api_key. Falling back to keyless GDELT 2.0 Doc API."
            )
            provider = "gdelt"

        if provider == "newsapi":
            return await self._poll_newsapi()
        elif provider == "currents":
            return await self._poll_currents()
        else:
            return await self._poll_gdelt()

    async def _poll_gdelt(self) -> List[NormalizedEvent]:
        """
        Query GDELT 2.0 Doc API (Free & Keyless).
        Endpoint: https://api.gdeltproject.org/api/v2/doc/doc?query={query}&mode=artlist&maxrecords={max_records}&format=json
        """
        encoded_query = urllib.parse.quote(self.query)
        url = (
            f"https://api.gdeltproject.org/api/v2/doc/doc?"
            f"query={encoded_query}&mode=artlist&maxrecords={self.max_records}&format=json"
        )

        try:
            client = await self._get_client()
            resp = await client.get(url)
            if resp.status_code != 200:
                self.logger.warning(f"GDELT API responded with HTTP {resp.status_code} for '{self.name}'.")
                return []
            data = resp.json()
        except Exception as exc:
            self.last_error = f"GDELT query failure: {exc}"
            self.logger.warning(f"GDELT fetch error for '{self.name}': {exc}")
            return []

        articles = data.get("articles") or []
        events: List[NormalizedEvent] = []

        for art in articles:
            try:
                title = (art.get("title") or "Untitled GDELT Intelligence").strip()
                article_url = art.get("url") or ""
                domain = art.get("domain") or "GDELT Intelligence"
                raw_seendate = art.get("seendate") or ""

                # GDELT date format: YYYYMMDDTHHMMSSZ or YYYYMMDDHHMMSS
                published_at: datetime
                if raw_seendate:
                    cleaned_date = raw_seendate.replace("T", "").replace("Z", "")
                    try:
                        published_at = datetime.strptime(cleaned_date[:14], "%Y%m%d%H%M%S").replace(
                            tzinfo=timezone.utc
                        )
                    except Exception:
                        published_at = datetime.now(timezone.utc)
                else:
                    published_at = datetime.now(timezone.utc)

                external_id = hashlib.sha256((article_url or f"{title}_{domain}").encode("utf-8")).hexdigest()[:24]

                event = NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="newsapi",
                    source_name=f"{domain} (GDELT)",
                    source_url=article_url,
                    external_id=external_id,
                    title=title,
                    raw_text=title,  # GDELT Doc artlist returns headlines and URLs
                    language=art.get("language")[:2].lower() if art.get("language") else "en",
                    author=domain,
                    published_at=published_at,
                    metadata={
                        "domain": domain,
                        "sourcecountry": art.get("sourcecountry"),
                        "socialimage": art.get("socialimage"),
                        "provider": "gdelt",
                    },
                )
                events.append(event)
            except Exception as e:
                self.logger.warning(f"Error parsing GDELT article item: {e}")
                continue

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(events)
        self.status = "active"
        self.last_error = None
        return events

    async def _poll_newsapi(self) -> List[NormalizedEvent]:
        """Query NewsAPI.org everything endpoint."""
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": self.query,
            "sortBy": "publishedAt",
            "pageSize": min(self.max_records, 100),
            "apiKey": self.api_key,
        }

        try:
            client = await self._get_client()
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                self.logger.warning(f"NewsAPI rate limit exceeded for '{self.name}'. Backing off.")
                return []
            if resp.status_code != 200:
                self.logger.warning(f"NewsAPI error response {resp.status_code}: {resp.text}")
                return []
            data = resp.json()
        except Exception as exc:
            self.last_error = f"NewsAPI fetch failure: {exc}"
            self.logger.warning(f"NewsAPI error for '{self.name}': {exc}")
            return []

        articles = data.get("articles") or []
        events: List[NormalizedEvent] = []

        for art in articles:
            try:
                title = art.get("title") or "Untitled"
                description = art.get("description") or art.get("content") or title
                article_url = art.get("url") or ""
                source_info = art.get("source") or {}
                source_name = source_info.get("name") or self.name
                author = art.get("author") or source_name

                raw_pub = art.get("publishedAt")
                if raw_pub:
                    try:
                        published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
                    except Exception:
                        published_at = datetime.now(timezone.utc)
                else:
                    published_at = datetime.now(timezone.utc)

                external_id = hashlib.sha256((article_url or f"{title}_{source_name}").encode("utf-8")).hexdigest()[:24]

                event = NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="newsapi",
                    source_name=source_name,
                    source_url=article_url,
                    external_id=external_id,
                    title=title,
                    raw_text=description,
                    language="en",
                    author=author,
                    published_at=published_at,
                    metadata={"provider": "newsapi", "source_id": source_info.get("id")},
                )
                events.append(event)
            except Exception as e:
                self.logger.warning(f"Error parsing NewsAPI article: {e}")
                continue

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(events)
        self.status = "active"
        self.last_error = None
        return events

    async def _poll_currents(self) -> List[NormalizedEvent]:
        """Query Currents API search endpoint."""
        url = "https://api.currentsapi.services/v1/search"
        params = {
            "keywords": self.query,
            "apiKey": self.api_key,
        }

        try:
            client = await self._get_client()
            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                self.logger.warning(f"Currents API error {resp.status_code}: {resp.text}")
                return []
            data = resp.json()
        except Exception as exc:
            self.last_error = f"Currents API fetch error: {exc}"
            self.logger.warning(f"Currents API error for '{self.name}': {exc}")
            return []

        news_items = data.get("news") or []
        events: List[NormalizedEvent] = []

        for item in news_items:
            try:
                title = item.get("title") or "Untitled"
                desc = item.get("description") or title
                item_url = item.get("url") or ""
                author = item.get("author") or self.name

                raw_pub = item.get("published")
                if raw_pub:
                    try:
                        published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
                    except Exception:
                        published_at = datetime.now(timezone.utc)
                else:
                    published_at = datetime.now(timezone.utc)

                external_id = hashlib.sha256((item_url or f"{title}_{author}").encode("utf-8")).hexdigest()[:24]

                event = NormalizedEvent(
                    theme_id=self.theme_id,
                    source_type="newsapi",
                    source_name=f"{author} (Currents)",
                    source_url=item_url,
                    external_id=external_id,
                    title=title,
                    raw_text=desc,
                    language=item.get("language", "en")[:2].lower(),
                    author=author,
                    published_at=published_at,
                    metadata={"provider": "currents", "category": item.get("category")},
                )
                events.append(event)
            except Exception as e:
                self.logger.warning(f"Error parsing Currents item: {e}")
                continue

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(events)
        self.status = "active"
        self.last_error = None
        return events

    async def test_connection(self) -> bool:
        """Validate connection to the configured news provider."""
        try:
            client = await self._get_client()
            if self.provider == "gdelt":
                url = "https://api.gdeltproject.org/api/v2/doc/doc?query=test&mode=artlist&maxrecords=1&format=json"
                r = await client.get(url)
                return r.status_code == 200
            elif self.provider == "newsapi" and self.api_key:
                url = f"https://newsapi.org/v2/top-headlines?pageSize=1&apiKey={self.api_key}"
                r = await client.get(url)
                return r.status_code == 200
            elif self.provider == "currents" and self.api_key:
                url = f"https://api.currentsapi.services/v1/latest-news?apiKey={self.api_key}"
                r = await client.get(url)
                return r.status_code == 200
            return True
        except Exception:
            return False
