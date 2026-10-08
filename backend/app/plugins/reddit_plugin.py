import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent


class RedditSourcePlugin(BaseSourcePlugin):
    """
    Reddit OSINT Ingestion Plugin.
    Monitors geopolitical and conflict subreddits (e.g., r/worldnews, r/geopolitics, r/military).
    Supports:
    1. Public Reddit JSON endpoint (https://www.reddit.com/r/{sub}/hot.json) with descriptive User-Agent.
    2. AsyncPRAW client when client_id and client_secret credentials are provided.
    Filters submissions against theme keywords and upvote thresholds.
    """

    def __init__(
        self,
        source_id: Any,
        theme_id: Any,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 600,
        is_enabled: bool = True,
    ):
        super().__init__(source_id, theme_id, name, config, poll_interval, is_enabled)
        # Subreddits can be a list or comma-separated string
        raw_subs = self.config.get("subreddits") or self.config.get("target") or ["worldnews", "geopolitics"]
        if isinstance(raw_subs, str):
            raw_subs = [s.strip() for s in raw_subs.split(",") if s.strip()]
        self.subreddits: List[str] = [self._sanitize_sub(s) for s in raw_subs if s]

        # Optional keyword filter list
        raw_kw = self.config.get("keywords") or []
        if isinstance(raw_kw, str):
            raw_kw = [k.strip() for k in raw_kw.split(",") if k.strip()]
        self.keywords: List[str] = [k.lower() for k in raw_kw if k]

        self.min_upvotes: int = int(self.config.get("min_upvotes") or 0)
        self.limit: int = min(int(self.config.get("limit") or 25), 100)

        # AsyncPRAW credentials
        self.client_id: Optional[str] = self.config.get("client_id")
        self.client_secret: Optional[str] = self.config.get("client_secret")
        self.user_agent: str = (
            self.config.get("user_agent")
            or "RealTimeOSINT-GatheringService/1.0 (Public Research Bot; contact@osint.local)"
        )
        self._http_client: Optional[httpx.AsyncClient] = None
        self._praw_reddit: Optional[Any] = None

    @staticmethod
    def _sanitize_sub(sub: str) -> str:
        s = sub.strip()
        s = re.sub(r"^/?(r/)?", "", s)
        return s.rstrip("/")

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )
        return self._http_client

    async def start(self) -> None:
        await super().start()
        if self.client_id and self.client_secret:
            try:
                import asyncpraw

                self._praw_reddit = asyncpraw.Reddit(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    user_agent=self.user_agent,
                )
                self.logger.info(f"Initialized AsyncPRAW for Reddit plugin '{self.name}'.")
            except Exception as exc:
                self.logger.warning(
                    f"AsyncPRAW setup failed for '{self.name}': {exc}. Using public JSON endpoint fallback."
                )
                self._praw_reddit = None

    async def stop(self) -> None:
        await super().stop()
        if self._praw_reddit:
            try:
                await self._praw_reddit.close()
            except Exception:
                pass
            self._praw_reddit = None
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    async def poll(self) -> List[NormalizedEvent]:
        """Poll monitored subreddits and yield filtered NormalizedEvents."""
        if not self.subreddits:
            self.logger.warning(f"Reddit plugin '{self.name}' has no subreddits configured.")
            return []

        # If AsyncPRAW is configured, use it
        if self._praw_reddit:
            try:
                return await self._poll_asyncpraw()
            except Exception as exc:
                self.logger.warning(
                    f"AsyncPRAW polling failed for '{self.name}': {exc}. Falling back to public JSON endpoint."
                )

        # Fallback to public JSON endpoint
        return await self._poll_public_json()

    async def _poll_public_json(self) -> List[NormalizedEvent]:
        """Fetch posts via public reddit JSON API (https://www.reddit.com/r/{sub}/hot.json)."""
        client = await self._get_http_client()
        all_events: List[NormalizedEvent] = []

        for sub in self.subreddits:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={self.limit}"
            try:
                resp = await client.get(url)
                if resp.status_code == 429:
                    self.logger.warning(f"Reddit rate limit (429) hit for r/{sub}. Backing off.")
                    continue
                if resp.status_code != 200:
                    self.logger.warning(f"Reddit error {resp.status_code} for r/{sub}: {resp.text[:100]}")
                    continue
                data = resp.json()
            except Exception as exc:
                self.last_error = f"Reddit fetch failure on r/{sub}: {exc}"
                self.logger.warning(f"Failed to fetch posts from r/{sub}: {exc}")
                continue

            children = data.get("data", {}).get("children", [])
            for child in children:
                p = child.get("data", {})
                event = self._normalize_post_dict(p, sub)
                if event:
                    all_events.append(event)

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(all_events)
        self.status = "active"
        self.last_error = None
        return all_events

    async def _poll_asyncpraw(self) -> List[NormalizedEvent]:
        """Fetch posts using AsyncPRAW."""
        all_events: List[NormalizedEvent] = []
        for sub_name in self.subreddits:
            try:
                sub = await self._praw_reddit.subreddit(sub_name)
                async for submission in sub.hot(limit=self.limit):
                    post_dict = {
                        "id": submission.id,
                        "title": submission.title,
                        "selftext": submission.selftext,
                        "score": submission.score,
                        "num_comments": submission.num_comments,
                        "upvote_ratio": submission.upvote_ratio,
                        "author": str(submission.author) if submission.author else "[deleted]",
                        "permalink": submission.permalink,
                        "created_utc": submission.created_utc,
                        "link_flair_text": submission.link_flair_text,
                    }
                    event = self._normalize_post_dict(post_dict, sub_name)
                    if event:
                        all_events.append(event)
            except Exception as sub_exc:
                self.logger.warning(f"AsyncPRAW error reading r/{sub_name}: {sub_exc}")
                continue

        self.last_polled_at = datetime.now(timezone.utc)
        self.events_count += len(all_events)
        self.status = "active"
        self.last_error = None
        return all_events

    def _normalize_post_dict(self, p: Dict[str, Any], sub: str) -> Optional[NormalizedEvent]:
        """Filter and normalize a Reddit post dictionary into NormalizedEvent."""
        title = (p.get("title") or "").strip()
        selftext = (p.get("selftext") or "").strip()
        score = int(p.get("score") or 0)

        # 1. Check minimum upvotes
        if score < self.min_upvotes:
            return None

        # 2. Check keyword filter if configured
        if self.keywords:
            content_lower = f"{title.lower()} {selftext.lower()}"
            if not any(k in content_lower for k in self.keywords):
                return None

        post_id = p.get("id") or ""
        external_id = f"reddit_{post_id}"
        permalink = p.get("permalink") or ""
        full_url = f"https://reddit.com{permalink}" if permalink.startswith("/") else permalink

        created_utc = p.get("created_utc")
        if created_utc:
            try:
                published_at = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
            except Exception:
                published_at = datetime.now(timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)

        # 3. Check lookback window
        if not self.is_event_within_lookback(published_at):
            return None

        raw_text = selftext if selftext else title

        return NormalizedEvent(
            theme_id=self.theme_id,
            source_type="reddit",
            source_name=f"r/{sub}",
            source_url=full_url,
            external_id=external_id,
            title=title,
            raw_text=raw_text,
            language="en",
            author=str(p.get("author") or "[deleted]"),
            published_at=published_at,
            metadata={
                "subreddit": sub,
                "score": score,
                "num_comments": p.get("num_comments", 0),
                "upvote_ratio": p.get("upvote_ratio", 1.0),
                "flair": p.get("link_flair_text"),
            },
        )

    async def test_connection(self) -> bool:
        """Validate accessibility of monitored subreddits."""
        if not self.subreddits:
            return False
        sub = self.subreddits[0]
        try:
            client = await self._get_http_client()
            res = await client.get(f"https://www.reddit.com/r/{sub}/hot.json?limit=1")
            return res.status_code == 200
        except Exception:
            return False
