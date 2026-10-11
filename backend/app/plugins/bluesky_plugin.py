import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from backend.app.plugins.base import BaseSourcePlugin, NormalizedEvent

logger = logging.getLogger("osint.plugin.bluesky")

BSKY_PUBLIC_API = "https://public.api.bsky.app/xrpc"


def _detect_lang(text: str) -> str:
    if any(c in text for c in "پچگژ\u200c") or bool(re.search(r"[\u06A9\u06CC]", text)):
        return "fa"
    if any(c in text for c in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي"):
        return "ar"
    if any("\u0590" <= c <= "\u05EA" for c in text):
        return "he"
    return "en"


class BlueskySourcePlugin(BaseSourcePlugin):
    """
    Bluesky / ATProto unauthenticated open-web OSINT ingestion plugin.
    Connects to https://public.api.bsky.app/xrpc/ without requiring any API key,
    account, or phone number (3,000 requests / 5 min per IP).

    Supported modes:
    - 'author': app.bsky.feed.getAuthorFeed (actor handle or DID, e.g., elintnews.bsky.social)
    - 'feed': app.bsky.feed.getFeed (AT-URI or bsky.app feed URL)
    - 'starter_pack': app.bsky.graph.getStarterPack -> app.bsky.feed.getListFeed
    - 'search': app.bsky.feed.searchPosts (live keyword/phrase search)
    """

    def __init__(
        self,
        source_id: Any,
        theme_id: Any,
        name: str,
        config: Optional[Dict[str, Any]] = None,
        poll_interval: int = 90,
        is_enabled: bool = True,
    ):
        super().__init__(source_id, theme_id, name, config, poll_interval, is_enabled)
        self.target: str = str(
            self.config.get("target")
            or self.config.get("actor")
            or self.config.get("feed")
            or self.config.get("query")
            or "elintnews.bsky.social"
        ).strip()
        self.mode: str = str(self.config.get("mode") or self._infer_mode(self.target)).lower().strip()
        self.limit: int = min(int(self.config.get("limit", 25)), 50)
        self._http_client: Optional[httpx.AsyncClient] = None
        self._etag_cache: Dict[str, str] = {}
        self._resolved_list_uri: Optional[str] = None

    @staticmethod
    def _infer_mode(target: str) -> str:
        t = target.strip()
        if "app.bsky.graph.starterpack" in t or "/starter-pack/" in t:
            return "starter_pack"
        if "app.bsky.feed.generator" in t or "/feed/" in t:
            return "feed"
        if "app.bsky.graph.list" in t or "/lists/" in t:
            return "list"
        if " " in t or t.startswith("search:"):
            return "search"
        return "author"

    @staticmethod
    def _normalize_actor(raw: str) -> str:
        s = raw.strip().lstrip("@")
        s = re.sub(r"^https?://bsky\.app/profile/", "", s)
        s = s.split("/")[0].strip()
        if "." not in s and not s.startswith("did:"):
            s = f"{s}.bsky.social"
        return s

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "User-Agent": "OSINT-Monitor/2.0 (Academic Research; +https://localhost)",
                    "Accept": "application/json",
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
        """Poll Bluesky public XRPC API and normalize posts into NormalizedEvent instances."""
        events: List[NormalizedEvent] = []
        client = await self._get_client()

        try:
            posts = await self._fetch_posts(client)
            for post_obj in posts:
                event = self._parse_post(post_obj)
                if event and self.is_event_within_lookback(event.published_at):
                    events.append(event)

            self.last_polled_at = datetime.now(timezone.utc)
            self.events_count += len(events)
            self.status = "active"
            self.last_error = None
        except Exception as exc:
            self.status = "error"
            self.last_error = str(exc)
            self.logger.warning(f"Bluesky poll error for '{self.name}' ({self.target}): {exc}")

        return events

    async def _fetch_posts(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        if self.mode == "search":
            q = self.target[7:].strip() if self.target.lower().startswith("search:") else self.target
            url = f"{BSKY_PUBLIC_API}/app.bsky.feed.searchPosts"
            params = {"q": q, "limit": self.limit, "sort": "latest"}
            data = await self._get_json(client, url, params)
            return data.get("posts", []) if isinstance(data, dict) else []

        if self.mode == "starter_pack":
            list_uri = await self._resolve_starter_pack_list(client, self.target)
            if not list_uri:
                return []
            url = f"{BSKY_PUBLIC_API}/app.bsky.feed.getListFeed"
            params = {"list": list_uri, "limit": self.limit}
            data = await self._get_json(client, url, params)
            feed = data.get("feed", []) if isinstance(data, dict) else []
            return [item["post"] for item in feed if isinstance(item, dict) and "post" in item]

        if self.mode == "list":
            url = f"{BSKY_PUBLIC_API}/app.bsky.feed.getListFeed"
            params = {"list": self.target, "limit": self.limit}
            data = await self._get_json(client, url, params)
            feed = data.get("feed", []) if isinstance(data, dict) else []
            return [item["post"] for item in feed if isinstance(item, dict) and "post" in item]

        if self.mode == "feed":
            feed_uri = await self._resolve_feed_uri(client, self.target)
            url = f"{BSKY_PUBLIC_API}/app.bsky.feed.getFeed"
            params = {"feed": feed_uri, "limit": self.limit}
            data = await self._get_json(client, url, params)
            feed = data.get("feed", []) if isinstance(data, dict) else []
            return [item["post"] for item in feed if isinstance(item, dict) and "post" in item]

        # Default: author feed
        actor = self._normalize_actor(self.target)
        url = f"{BSKY_PUBLIC_API}/app.bsky.feed.getAuthorFeed"
        params = {"actor": actor, "limit": self.limit, "filter": "posts_no_replies"}
        data = await self._get_json(client, url, params)
        feed = data.get("feed", []) if isinstance(data, dict) else []
        return [item["post"] for item in feed if isinstance(item, dict) and "post" in item]

    async def _resolve_starter_pack_list(self, client: httpx.AsyncClient, target: str) -> Optional[str]:
        if self._resolved_list_uri:
            return self._resolved_list_uri
        sp_uri = target
        if target.startswith("http"):
            # Convert https://bsky.app/starter-pack/{actor}/{rkey} -> AT URI via getProfile
            m = re.search(r"bsky\.app/starter-pack/([^/]+)/([^/?#]+)", target)
            if m:
                actor, rkey = m.group(1), m.group(2)
                prof = await self._get_json(client, f"{BSKY_PUBLIC_API}/app.bsky.actor.getProfile", {"actor": actor})
                did = prof.get("did") if isinstance(prof, dict) else None
                if did:
                    sp_uri = f"at://{did}/app.bsky.graph.starterpack/{rkey}"
        data = await self._get_json(client, f"{BSKY_PUBLIC_API}/app.bsky.graph.getStarterPack", {"starterPack": sp_uri})
        if isinstance(data, dict):
            sp = data.get("starterPack", {})
            list_obj = sp.get("list", {}) if isinstance(sp, dict) else {}
            list_uri = list_obj.get("uri") if isinstance(list_obj, dict) else sp.get("listUri")
            if list_uri:
                self._resolved_list_uri = str(list_uri)
                return self._resolved_list_uri
        return None

    async def _resolve_feed_uri(self, client: httpx.AsyncClient, target: str) -> str:
        if target.startswith("at://"):
            return target
        m = re.search(r"bsky\.app/profile/([^/]+)/feed/([^/?#]+)", target)
        if m:
            actor, rkey = m.group(1), m.group(2)
            prof = await self._get_json(client, f"{BSKY_PUBLIC_API}/app.bsky.actor.getProfile", {"actor": actor})
            did = prof.get("did") if isinstance(prof, dict) else actor
            return f"at://{did}/app.bsky.feed.generator/{rkey}"
        return target

    async def _get_json(
        self, client: httpx.AsyncClient, url: str, params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        cache_key = f"{url}?{sorted(params.items())}"
        req_headers: Dict[str, str] = {}
        if cache_key in self._etag_cache:
            req_headers["If-None-Match"] = self._etag_cache[cache_key]

        response = await client.get(url, params=params, headers=req_headers)
        if response.status_code == 304:
            return {}
        if response.status_code != 200:
            self.logger.warning(f"Bluesky XRPC {url} returned status {response.status_code}")
            return {}
        resp_headers = getattr(response, "headers", None) or {}
        if resp_headers.get("etag"):
            self._etag_cache[cache_key] = resp_headers["etag"]
        return response.json()

    def _parse_post(self, post: Dict[str, Any]) -> Optional[NormalizedEvent]:
        if not isinstance(post, dict):
            return None
        record = post.get("record") or {}
        text = str(record.get("text") or "").strip()
        if not text:
            return None

        uri = str(post.get("uri") or "")
        cid = str(post.get("cid") or "")
        rkey = uri.split("/")[-1] if "/" in uri else cid[:16]

        author_obj = post.get("author") or {}
        handle = str(author_obj.get("handle") or "unknown.bsky.social")
        display_name = str(author_obj.get("displayName") or handle)

        created_str = str(record.get("createdAt") or post.get("indexedAt") or "")
        published_at = datetime.now(timezone.utc)
        if created_str:
            try:
                published_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except Exception:
                published_at = datetime.now(timezone.utc)

        external_id = f"bsky_{handle}_{rkey}"
        post_url = f"https://bsky.app/profile/{handle}/post/{rkey}"
        lang = _detect_lang(text)

        title = f"@{handle}: {text[:85]}".replace("\n", " ").strip()
        if len(text) > 85:
            title += "..."

        return NormalizedEvent(
            theme_id=self.theme_id,
            source_type="bluesky",
            source_name=self.name,
            source_url=post_url,
            external_id=external_id,
            title=title,
            raw_text=text,
            language=lang,
            author=f"@{handle}",
            published_at=published_at,
            metadata={
                "uri": uri,
                "cid": cid,
                "author_handle": handle,
                "author_display_name": display_name,
                "like_count": post.get("likeCount", 0),
                "repost_count": post.get("repostCount", 0),
                "reply_count": post.get("replyCount", 0),
                "mode": self.mode,
            },
        )

    async def test_connection(self) -> bool:
        try:
            client = await self._get_client()
            posts = await self._fetch_posts(client)
            return isinstance(posts, list)
        except Exception:
            return False
