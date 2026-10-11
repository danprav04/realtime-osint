import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import httpx

from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent
from backend.app.plugins.telegram_plugin import is_arabic, is_farsi, is_hebrew


class RedditSourcePlugin(BaseSourcePlugin):
    """
    Reddit OSINT Ingestion Plugin.
    Monitors geopolitical, regional, and conflict subreddits (e.g., r/lebanon, r/israel, r/iran, r/NewIran, r/OSINT).
    Supports:
    1. Public Reddit JSON endpoints (/comments.json, /new.json, /hot.json) with catalog-compliant User-Agent
       and conditional HTTP caching (ETag / If-Modified-Since).
    2. AsyncPRAW client when client_id and client_secret credentials are provided.
    Filters submissions and live comments against theme keywords and upvote thresholds.
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
        # Subreddits can be a list, comma-separated string, or full Reddit URL
        raw_subs = self.config.get("subreddits") or self.config.get("target") or ["worldnews", "geopolitics"]
        if isinstance(raw_subs, str):
            raw_subs = [s.strip() for s in raw_subs.split(",") if s.strip()]

        # Determine default feed_mode ("hot", "new", "comments", or "all")
        self.feed_mode: str = str(self.config.get("feed_mode") or "").lower().strip()
        parsed_subs: List[str] = []
        inferred_modes: Dict[str, str] = {}
        for raw_s in raw_subs:
            if not raw_s:
                continue
            sub_clean, detected_mode = self._parse_sub_and_mode(str(raw_s))
            if sub_clean:
                parsed_subs.append(sub_clean)
                if detected_mode:
                    inferred_modes[sub_clean] = detected_mode

        self.subreddits: List[str] = parsed_subs
        self._sub_modes: Dict[str, str] = inferred_modes
        if not self.feed_mode:
            if inferred_modes:
                self.feed_mode = next(iter(inferred_modes.values()))
            else:
                self.feed_mode = "hot"

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
            or "linux:realtime-osint.collector:v1.1 (by /u/osint_research_node)"
        )
        self._http_client: Optional[httpx.AsyncClient] = None
        self._praw_reddit: Optional[Any] = None
        # Conditional HTTP cache headers per URL: {url: {"etag": ..., "last_modified": ...}}
        self._http_cache_meta: Dict[str, Dict[str, str]] = {}

    @staticmethod
    def _parse_sub_and_mode(raw: str) -> Tuple[str, Optional[str]]:
        s = raw.strip()
        s = re.sub(r"^https?://(www\.|old\.)?reddit\.com", "", s, flags=re.IGNORECASE)
        s = re.sub(r"^/?(r/)?", "", s)
        s = s.split("?")[0].rstrip("/")
        mode: Optional[str] = None
        for suffix, m_val in (
            ("/comments.json", "comments"),
            ("/comments", "comments"),
            ("/new.json", "new"),
            ("/new", "new"),
            ("/hot.json", "hot"),
            ("/hot", "hot"),
            ("/.rss", "new"),
            (".rss", "new"),
            (".json", "hot"),
        ):
            if s.lower().endswith(suffix):
                s = s[: -len(suffix)].rstrip("/")
                mode = m_val
                break
        return s, mode

    @staticmethod
    def _sanitize_sub(sub: str) -> str:
        clean, _ = RedditSourcePlugin._parse_sub_and_mode(sub)
        return clean

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8,fa;q=0.7,he;q=0.6",
                },
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

        # If AsyncPRAW is configured and mode is not comments-only, use it
        if self._praw_reddit and self.feed_mode not in ("comments",):
            try:
                return await self._poll_asyncpraw()
            except Exception as exc:
                self.logger.warning(
                    f"AsyncPRAW polling failed for '{self.name}': {exc}. Falling back to public JSON endpoint."
                )

        # Fallback to public JSON endpoint
        return await self._poll_public_json()

    def _get_endpoints_for_sub(self, sub: str) -> List[Tuple[str, str]]:
        mode = self._sub_modes.get(sub) or self.feed_mode or "hot"
        if mode == "comments":
            return [(f"https://www.reddit.com/r/{sub}/comments.json?limit={self.limit}", "comments")]
        elif mode == "new":
            return [(f"https://www.reddit.com/r/{sub}/new.json?limit={self.limit}", "new")]
        elif mode in ("all", "both", "comments_and_new"):
            return [
                (f"https://www.reddit.com/r/{sub}/comments.json?limit={self.limit}", "comments"),
                (f"https://www.reddit.com/r/{sub}/new.json?limit={self.limit}", "new"),
            ]
        else:
            return [(f"https://www.reddit.com/r/{sub}/hot.json?limit={self.limit}", "hot")]

    async def _poll_public_json(self) -> List[NormalizedEvent]:
        """Fetch posts or live comments via public Reddit JSON API (/comments.json, /new.json, /hot.json)."""
        client = await self._get_http_client()
        all_events: List[NormalizedEvent] = []

        for sub in self.subreddits:
            for url, endpoint_mode in self._get_endpoints_for_sub(sub):
                req_headers: Dict[str, str] = {}
                cache_entry = self._http_cache_meta.get(url)
                if cache_entry:
                    if cache_entry.get("etag"):
                        req_headers["If-None-Match"] = cache_entry["etag"]
                    if cache_entry.get("last_modified"):
                        req_headers["If-Modified-Since"] = cache_entry["last_modified"]

                try:
                    resp = await client.get(url, headers=req_headers) if req_headers else await client.get(url)
                    if resp.status_code == 304:
                        self.logger.debug(f"Reddit 304 Not Modified for r/{sub} ({endpoint_mode}).")
                        continue
                    if resp.status_code == 429:
                        self.logger.warning(f"Reddit rate limit (429) hit for r/{sub} ({endpoint_mode}). Backing off.")
                        continue
                    if resp.status_code != 200:
                        self.logger.warning(f"Reddit error {resp.status_code} for r/{sub}: {resp.text[:100]}")
                        continue

                    # Save ETag / Last-Modified for conditional polling
                    etag = resp.headers.get("etag") or resp.headers.get("ETag")
                    last_mod = resp.headers.get("last-modified") or resp.headers.get("Last-Modified")
                    if etag or last_mod:
                        self._http_cache_meta[url] = {
                            k: v for k, v in (("etag", etag), ("last_modified", last_mod)) if v
                        }

                    data = resp.json()
                except Exception as exc:
                    self.last_error = f"Reddit fetch failure on r/{sub}: {exc}"
                    self.logger.warning(f"Failed to fetch from r/{sub} ({endpoint_mode}): {exc}")
                    continue

                children = data.get("data", {}).get("children", [])
                for child in children:
                    kind = child.get("kind", "")
                    p = child.get("data", {})
                    if kind == "t1" or endpoint_mode == "comments" or ("body" in p and "title" not in p):
                        event = self._normalize_comment_dict(p, sub)
                    else:
                        event = self._normalize_post_dict(p, sub, feed_mode=endpoint_mode)
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
                stream_iter = sub.new(limit=self.limit) if self.feed_mode == "new" else sub.hot(limit=self.limit)
                async for submission in stream_iter:
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
                    event = self._normalize_post_dict(post_dict, sub_name, feed_mode=self.feed_mode)
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

    @staticmethod
    def _detect_lang(text: str) -> str:
        if is_farsi(text):
            return "fa"
        if is_arabic(text):
            return "ar"
        if is_hebrew(text):
            return "he"
        return "en"

    def _normalize_comment_dict(self, c: Dict[str, Any], sub: str) -> Optional[NormalizedEvent]:
        """Normalize a Reddit comment dictionary (from /comments.json) into NormalizedEvent."""
        body = (c.get("body") or "").strip()
        if not body or body.lower() in ("[deleted]", "[removed]"):
            return None

        author = str(c.get("author") or "[deleted]")
        if author.lower() == "automoderator":
            return None

        score = int(c.get("score") or 1)
        if self.min_upvotes > 1 and score < self.min_upvotes:
            return None

        link_title = (c.get("link_title") or f"r/{sub} Live Discussion").strip()
        if self.keywords:
            content_lower = f"{link_title.lower()} {body.lower()}"
            if not any(k in content_lower for k in self.keywords):
                return None

        comment_id = c.get("id") or ""
        external_id = f"reddit_cmt_{comment_id}"
        permalink = c.get("permalink") or c.get("link_permalink") or ""
        full_url = f"https://reddit.com{permalink}" if permalink.startswith("/") else permalink

        created_utc = c.get("created_utc")
        if created_utc:
            try:
                published_at = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
            except Exception:
                published_at = datetime.now(timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)

        if not self.is_event_within_lookback(published_at):
            return None

        snippet = body.replace("\n", " ").strip()
        title = f"[r/{sub} Comment] {snippet[:85]}{'...' if len(snippet) > 85 else ''}"
        raw_text = f"Thread: {link_title}\nEyewitness/User Comment: {body}"
        lang = self._detect_lang(body)

        return NormalizedEvent(
            theme_id=self.theme_id,
            source_type="reddit",
            source_name=f"r/{sub}",
            source_url=full_url,
            external_id=external_id,
            title=title,
            raw_text=raw_text,
            language=lang,
            author=author,
            published_at=published_at,
            metadata={
                "subreddit": sub,
                "feed_mode": "comments",
                "is_comment": True,
                "link_title": link_title,
                "score": score,
            },
        )

    def _normalize_post_dict(self, p: Dict[str, Any], sub: str, feed_mode: str = "hot") -> Optional[NormalizedEvent]:
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
        lang = self._detect_lang(f"{title} {raw_text}")

        return NormalizedEvent(
            theme_id=self.theme_id,
            source_type="reddit",
            source_name=f"r/{sub}",
            source_url=full_url,
            external_id=external_id,
            title=title,
            raw_text=raw_text,
            language=lang,
            author=str(p.get("author") or "[deleted]"),
            published_at=published_at,
            metadata={
                "subreddit": sub,
                "feed_mode": feed_mode,
                "is_comment": False,
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

