"""
Telegram Notification Bot & Alert Dispatcher.
Dispatches high-importance alerts via Telegram Bot API with token-bucket cooldown,
importance score 10 bypass, 24-hour URL deduplication, and database alert tracking.
"""

import html
import re
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import get_settings
from backend.app.db.models import Alert, Event, SystemSettings, Theme, utc_now
from backend.app.db.session import get_session_factory
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_ALERT,
    RedisBus,
    get_redis_bus,
)

logger = logging.getLogger("osint.telegram_bot")


class TelegramAlertPayload(BaseModel):
    event_id: str
    title: str
    summary: str
    importance_score: int
    source_name: str
    published_at: datetime
    source_url: Optional[str] = None
    headline: Optional[str] = None
    trend_category: Optional[str] = None
    entities: List[str] = Field(default_factory=list)
    corroboration_status: Optional[str] = None
    corroboration_count: Optional[int] = 0
    score_rationale: Optional[str] = None


class TelegramBotService:
    """
    Production-grade Telegram Bot Alert Dispatcher with anti-flood protection,
    score=10 bypass, 24h deduplication, and resilient error recovery.
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        redis_bus: Optional[RedisBus] = None,
        rate_limit_seconds: float = 30.0,
        rate_limiting_enabled: bool = True,
    ):
        settings = get_settings()
        is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
        if is_pytest:
            self.bot_token = bot_token
            self.chat_id = chat_id
        else:
            self.bot_token = bot_token or settings.TELEGRAM_BOT_TOKEN
            self.chat_id = chat_id or settings.TELEGRAM_CHAT_ID
        self.redis_bus = redis_bus or get_redis_bus()
        self.rate_limit_seconds = rate_limit_seconds
        self.rate_limiting_enabled = rate_limiting_enabled

        self.last_sent_timestamp: float = 0.0
        self.sent_messages: List[Dict[str, Any]] = []
        self._dedup_cache: Dict[str, float] = {}  # In-memory fallback dedup store (key -> expiry_time)
        self._is_running: bool = False
        self._http_client: Optional[httpx.AsyncClient] = None
        self._retry_lock: asyncio.Lock = asyncio.Lock()
        self.simulate_error: Optional[str] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._last_update_id: int = 0

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=10.0)
        return self._http_client

    def format_message(self, payload: TelegramAlertPayload) -> str:
        """
        Format alert into Telegram Markdown message template.
        """
        if isinstance(payload.published_at, datetime):
            time_str = payload.published_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            time_str = str(payload.published_at)

        # Truncate summary if oversized to avoid exceeding 4096 character Telegram ceiling
        summary = payload.summary[:3000] if payload.summary else "N/A"
        link = payload.source_url if payload.source_url else "N/A"

        return (
            f"🚨 *OSINT CRITICAL ALERT* [{payload.importance_score}/10]\n\n"
            f"*Summary:* {summary}\n\n"
            f"*Source:* {payload.source_name}\n\n"
            f"*Time:* {time_str}\n\n"
            f"*Link:* {link}"
        )

    def format_html_message(self, payload: TelegramAlertPayload) -> str:
        """
        Format alert into Rich Intelligence HTML template with colored badges,
        punchy headline, verification confidence, key entities, and telemetry.
        """
        score = payload.importance_score
        if score >= 10:
            badge = "🔴 <b>CRITICAL FLASH ALERT</b> [10/10]"
        elif score == 9:
            badge = "🔴 <b>CRITICAL ALERT</b> [9/10]"
        elif score == 8:
            badge = "🟠 <b>HIGH IMPORTANCE ALERT</b> [8/10]"
        elif score == 7:
            badge = "🟡 <b>ELEVATED ALERT</b> [7/10]"
        else:
            badge = f"⚪ <b>OSINT ALERT</b> [{score}/10]"

        lines = [badge, ""]

        # 1. Headline / Title
        headline = (payload.headline or payload.title or "").strip()
        if headline:
            lines.append(f"<b>{html.escape(headline)}</b>")
            lines.append("")

        # 2. Executive Summary
        summary = (payload.summary or "").strip()
        if summary:
            lines.append(html.escape(summary[:3000]))
            lines.append("")

        # 3. Intelligence Breakdown & Metadata
        intel_lines = []
        if payload.trend_category:
            intel_lines.append(f"🎯 <b>Category:</b> {html.escape(payload.trend_category)}")

        if payload.corroboration_status:
            cnt = payload.corroboration_count or 0
            icon = "🛡️"
            st_lower = payload.corroboration_status.lower()
            if cnt >= 2 or "highly" in st_lower:
                icon = "✅"
            elif "unverified" in st_lower or "single" in st_lower:
                icon = "⚠️"
            corrob_desc = html.escape(payload.corroboration_status)
            if cnt > 0:
                corrob_desc += f" ({cnt} source{'s' if cnt > 1 else ''})"
            intel_lines.append(f"{icon} <b>Verification:</b> {corrob_desc}")

        if payload.entities:
            tags = []
            for ent in payload.entities[:5]:
                if isinstance(ent, str) and ent.strip():
                    clean_tag = re.sub(r"[^\w]", "", ent.strip().replace(" ", ""))
                    if clean_tag:
                        tags.append(f"#{clean_tag}")
            if tags:
                intel_lines.append(f"🏷️ <b>Entities:</b> {' '.join(tags)}")

        if isinstance(payload.published_at, datetime):
            time_str = payload.published_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        else:
            time_str = str(payload.published_at)

        source_name = html.escape(payload.source_name or "OSINT Stream")
        intel_lines.append(f"📡 <b>Source:</b> {source_name} | ⏱️ {time_str}")

        if payload.source_url:
            intel_lines.append(f"🔗 <b>Link:</b> <a href=\"{html.escape(payload.source_url)}\">{source_name}</a>")

        lines.extend(intel_lines)
        return "\n".join(lines)

    def build_inline_keyboard(self, payload: TelegramAlertPayload) -> Optional[Dict[str, Any]]:
        """Construct Telegram inline keyboard action buttons for alert messages."""
        from urllib.parse import urlparse
        row = []
        if payload.source_url and payload.source_url.startswith(("http://", "https://")):
            try:
                parsed = urlparse(payload.source_url)
                if parsed.hostname and "." in parsed.hostname:
                    row.append({"text": "🌐 Open Source", "url": payload.source_url})
            except Exception:
                pass

        frontend_url = os.environ.get("FRONTEND_URL") or os.environ.get("PUBLIC_URL")
        tailscale_ip = os.environ.get("DEPLOY_TAILSCALE_IP") or os.environ.get("TAILSCALE_IP")
        if not frontend_url and tailscale_ip:
            frontend_url = f"http://{tailscale_ip}:26816"

        if frontend_url and frontend_url.startswith(("http://", "https://")):
            try:
                parsed = urlparse(frontend_url)
                if parsed.hostname and "." in parsed.hostname:
                    row.append({"text": "⚡ Live Feed", "url": frontend_url})
            except Exception:
                pass

        if not row:
            return None
        return {"inline_keyboard": [row]}

    def _get_dedup_hash(self, payload: TelegramAlertPayload) -> str:
        """Compute SHA-256 hash for 24h deduplication."""
        identifier = (payload.source_url or "").strip() or payload.event_id or payload.title
        return hashlib.sha256(identifier.strip().encode("utf-8")).hexdigest()

    async def check_and_mark_dedup(self, payload: TelegramAlertPayload) -> bool:
        """
        Check 24-hour URL deduplication window.
        Returns True if alert is new and marked; False if duplicate.
        """
        h = self._get_dedup_hash(payload)
        key = f"alert:dedup:{h}"
        now = time.time()

        # Clean stale in-memory entries
        expired_keys = [k for k, exp in self._dedup_cache.items() if exp < now]
        for k in expired_keys:
            self._dedup_cache.pop(k, None)

        # Check Redis if connected
        if self.redis_bus and self.redis_bus.is_connected and self.redis_bus._client:
            try:
                # SET key 1 EX 86400 NX -> Returns True only if key was set (i.e. did not exist)
                set_res = await self.redis_bus._client.set(key, "1", ex=86400, nx=True)
                if not set_res:
                    return False
                return True
            except Exception as exc:
                logger.warning(f"Redis dedup check error: {exc}. Falling back to in-memory store.")

        # In-memory fallback
        if key in self._dedup_cache and self._dedup_cache[key] > now:
            return False

        self._dedup_cache[key] = now + 86400.0  # 24 hours TTL
        return True

    async def _unmark_dedup(self, payload: TelegramAlertPayload) -> None:
        """Remove dedup mark when an alert was only throttled by rate-limit cooldown so it can be retried."""
        h = self._get_dedup_hash(payload)
        key = f"alert:dedup:{h}"
        self._dedup_cache.pop(key, None)
        if self.redis_bus and self.redis_bus.is_connected and self.redis_bus._client:
            try:
                await self.redis_bus._client.delete(key)
            except Exception:
                pass

    def is_rate_limited(self, payload: TelegramAlertPayload) -> bool:
        """
        Evaluate token-bucket cooldown rate limiter.
        Importance score == 10 immediately bypasses rate limit!
        """
        if not self.rate_limiting_enabled:
            return False

        # CRITICAL BYPASS: Score 10 events ignore cooldown completely!
        if payload.importance_score >= 10:
            return False

        now = time.time()
        return (now - self.last_sent_timestamp) < self.rate_limit_seconds

    async def _record_alert_in_db(
        self,
        event_id: str,
        importance_score: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Persist or update alert record in PostgreSQL database."""
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                # Check if alert already exists for this event
                stmt = select(Alert).where(Alert.event_id == event_id).limit(1)
                existing = (await session.execute(stmt)).scalars().first()
                if existing:
                    existing.status = status
                    existing.error_message = error_message
                    existing.sent_at = utc_now()
                else:
                    alert = Alert(
                        event_id=event_id,
                        importance_score=importance_score,
                        channel="telegram",
                        status=status,
                        error_message=error_message,
                        sent_at=utc_now(),
                    )
                    session.add(alert)
                await session.commit()
        except Exception as exc:
            logger.debug(f"Failed to record alert in DB: {exc}")

    async def auto_discover_chat_id(self) -> Optional[str]:
        """
        Polls Telegram getUpdates API to automatically discover the user's chat_id
        when they message the bot (e.g. sending /start).
        """
        if not self.bot_token:
            return None
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
            resp = await self.http_client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("result", [])
                for update in reversed(results):
                    msg = update.get("message") or update.get("channel_post")
                    if msg and "chat" in msg:
                        discovered_id = str(msg["chat"]["id"])
                        logger.info(f"Auto-discovered Telegram chat_id: {discovered_id}")
                        self.chat_id = discovered_id
                        os.environ["TELEGRAM_CHAT_ID"] = discovered_id
                        try:
                            from sqlalchemy.orm.attributes import flag_modified
                            session_factory = get_session_factory()
                            async with session_factory() as session:
                                res = await session.execute(
                                    select(SystemSettings).where(SystemSettings.key == "api_keys")
                                )
                                existing = res.scalars().first()
                                if existing and isinstance(existing.value_json, dict):
                                    updated = dict(existing.value_json)
                                    updated["telegram_chat_id"] = discovered_id
                                    existing.value_json = updated
                                    flag_modified(existing, "value_json")
                                else:
                                    session.add(
                                        SystemSettings(
                                            key="api_keys",
                                            value_json={"telegram_chat_id": discovered_id},
                                        )
                                    )
                                await session.commit()
                        except Exception as db_exc:
                            logger.debug(f"Could not persist discovered chat_id to DB: {db_exc}")
                        return discovered_id
        except Exception as exc:
            logger.debug(f"Auto-discover Telegram chat_id attempt: {exc}")
        return None

    async def send_alert(self, payload: TelegramAlertPayload) -> Dict[str, Any]:
        """
        Process and dispatch a high-importance alert via Telegram.
        Handles deduplication, cooldown throttling, score 10 bypass,
        credential checks, and error resilience.
        """
        # 1. Error simulation support for boundary testing
        if self.simulate_error == "invalid_token":
            raise ValueError("Telegram Bot API: Unauthorized / Invalid Token")
        elif self.simulate_error == "chat_not_found":
            raise LookupError("Telegram Bot API: Chat not found (400 Bad Request)")
        elif self.simulate_error == "flood_wait":
            raise RuntimeError("Telegram FloodWaitError: A wait of 30 seconds is required")

        # 2. Check 24h deduplication
        is_unique = await self.check_and_mark_dedup(payload)
        if not is_unique:
            logger.info(f"Suppressed duplicate alert for event {payload.event_id}")
            await self._record_alert_in_db(
                payload.event_id, payload.importance_score, status="duplicate"
            )
            return {"status": "duplicate", "reason": "dedup_window_active"}

        # 3. Check rate limiting (with score=10 bypass)
        if self.is_rate_limited(payload):
            await self._unmark_dedup(payload)
            logger.info(
                f"Throttled alert for event {payload.event_id} (cooldown: {self.rate_limit_seconds}s)"
            )
            await self._record_alert_in_db(
                payload.event_id, payload.importance_score, status="throttled"
            )
            return {"status": "throttled", "reason": "rate_limit_cooldown"}

        # 4. Format message text (HTML and plain fallback)
        formatted_html = self.format_html_message(payload)
        formatted_text = self.format_message(payload)
        disable_audio = payload.importance_score < 9
        keyboard = self.build_inline_keyboard(payload)

        # 5. Check credentials (attempt auto-discovery if token is present but chat_id is missing outside pytest)
        if self.bot_token and not self.chat_id and not os.environ.get("PYTEST_CURRENT_TEST"):
            await self.auto_discover_chat_id()

        if not self.bot_token or not self.chat_id:
            logger.warning(
                "Telegram bot operating in simulated local alert mode (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)."
            )
            # In testing or development without live credentials, deliver to in-memory log
            self.last_sent_timestamp = time.time()
            message_record = {
                "message_id": len(self.sent_messages) + 1,
                "text": formatted_text,
                "html_text": formatted_html,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload.model_dump(),
                "disable_notification": disable_audio,
                "reply_markup": keyboard,
            }
            self.sent_messages.append(message_record)
            await self._record_alert_in_db(
                payload.event_id, payload.importance_score, status="delivered"
            )
            return {
                "status": "delivered",
                "message_id": message_record["message_id"],
                "simulated": True,
            }

        # 6. Live dispatch to Telegram Bot API
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        body = {
            "chat_id": self.chat_id,
            "text": formatted_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            "disable_notification": disable_audio,
        }
        if keyboard:
            body["reply_markup"] = keyboard

        try:
            response = await self.http_client.post(url, json=body)
            self.last_sent_timestamp = time.time()

            # Automatic fallback: if Telegram rejected HTML (e.g. unclosed entities, tag error), retry with stripped text
            if response.status_code == 400 and ("parse" in response.text.lower() or "entity" in response.text.lower() or "tag" in response.text.lower()):
                logger.warning(
                    f"Telegram HTML parse failed: {response.text}. Retrying with clean plain text..."
                )
                plain_text = re.sub(r"<[^>]+>", "", formatted_html)
                plain_body = {
                    "chat_id": self.chat_id,
                    "text": plain_text,
                    "disable_web_page_preview": False,
                    "disable_notification": disable_audio,
                }
                if keyboard:
                    plain_body["reply_markup"] = keyboard
                response = await self.http_client.post(url, json=plain_body)
                self.last_sent_timestamp = time.time()

            # Automatic fallback 2: if Telegram rejected inline keyboard button URL, retry without reply_markup
            if response.status_code == 400 and keyboard and ("button" in response.text.lower() or "url" in response.text.lower()):
                logger.warning(
                    f"Telegram keyboard URL rejected: {response.text}. Retrying without reply_markup..."
                )
                body_no_kb = dict(body)
                body_no_kb.pop("reply_markup", None)
                response = await self.http_client.post(url, json=body_no_kb)
                self.last_sent_timestamp = time.time()

            if response.status_code == 200:
                resp_json = response.json()
                msg_id = resp_json.get("result", {}).get("message_id", 0)
                message_record = {
                    "message_id": msg_id,
                    "text": formatted_text,
                    "html_text": formatted_html,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": payload.model_dump(),
                    "disable_notification": disable_audio,
                    "reply_markup": keyboard,
                }
                self.sent_messages.append(message_record)
                await self._record_alert_in_db(
                    payload.event_id, payload.importance_score, status="delivered"
                )
                return {"status": "delivered", "message_id": msg_id}

            elif response.status_code == 401:
                err_msg = "Telegram Bot API: Unauthorized / Invalid Token"
                logger.error(err_msg)
                await self._record_alert_in_db(
                    payload.event_id, payload.importance_score, status="failed", error_message=err_msg
                )
                return {"status": "failed", "error": err_msg}

            elif response.status_code == 400:
                err_msg = f"Telegram Bot API: Bad Request: {response.text}"
                logger.error(err_msg)
                await self._record_alert_in_db(
                    payload.event_id, payload.importance_score, status="failed", error_message=err_msg
                )
                return {"status": "failed", "error": err_msg}

            elif response.status_code == 429:
                err_msg = "Telegram FloodWaitError: 429 Rate Limit"
                logger.warning(err_msg)
                # Parse retry_after
                try:
                    retry_after = response.json().get("parameters", {}).get("retry_after", 30)
                except Exception:
                    retry_after = 30
                self.last_sent_timestamp = time.time() + retry_after
                await self._record_alert_in_db(
                    payload.event_id, payload.importance_score, status="throttled", error_message=err_msg
                )
                return {"status": "throttled", "reason": "telegram_flood_wait"}

            else:
                err_msg = f"Telegram Bot API returned HTTP {response.status_code}: {response.text}"
                logger.error(err_msg)
                await self._record_alert_in_db(
                    payload.event_id, payload.importance_score, status="failed", error_message=err_msg
                )
                return {"status": "failed", "error": err_msg}

        except Exception as exc:
            logger.error(f"Telegram dispatch error: {exc}")
            await self._record_alert_in_db(
                payload.event_id, payload.importance_score, status="failed", error_message=str(exc)
            )
            return {"status": "failed", "error": str(exc)}

    def _chunk_text(self, text: str, max_chars: int = 4000) -> List[str]:
        """Split text into chunks smaller than max_chars, preferably along newline boundaries."""
        if len(text) <= max_chars:
            return [text]

        chunks: List[str] = []
        lines = text.split("\n")
        current_chunk: List[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1
            if current_len + line_len > max_chars:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = []
                    current_len = 0
                while len(line) > max_chars:
                    chunks.append(line[:max_chars])
                    line = line[max_chars:]
                    line_len = len(line) + 1
            current_chunk.append(line)
            current_len += line_len

        if current_chunk:
            chunks.append("\n".join(current_chunk))

        return chunks or [text]

    async def send_chat_action(self, action: str = "typing", chat_id: Optional[str] = None) -> bool:
        """Send chat action (e.g. 'typing') to display user activity indicator."""
        target_chat_id = str(chat_id or self.chat_id or "")
        if not self.bot_token or not target_chat_id:
            return True
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendChatAction"
            body = {"chat_id": target_chat_id, "action": action}
            await self.http_client.post(url, json=body, timeout=5.0)
            return True
        except Exception as exc:
            logger.debug(f"Failed to send Telegram chat action: {exc}")
            return False

    async def send_text_message(
        self,
        text: str,
        chat_id: Optional[str] = None,
        parse_mode: str = "Markdown",
    ) -> Dict[str, Any]:
        """
        Deliver a text message to Telegram with automatic message chunking
        (4096 character ceiling) and Markdown parse fallback resilience.
        """
        target_chat_id = str(chat_id or self.chat_id or "")
        if not target_chat_id:
            logger.warning("send_text_message called without destination chat_id")
            return {"status": "failed", "error": "missing_chat_id"}

        chunks = self._chunk_text(text, max_chars=4000)
        last_res: Dict[str, Any] = {"status": "delivered"}

        for chunk in chunks:
            if self.bot_token and not self.chat_id and not os.environ.get("PYTEST_CURRENT_TEST"):
                await self.auto_discover_chat_id()

            if not self.bot_token or not self.chat_id:
                message_record = {
                    "message_id": len(self.sent_messages) + 1,
                    "chat_id": target_chat_id,
                    "text": chunk,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "parse_mode": parse_mode,
                    "simulated": True,
                }
                self.sent_messages.append(message_record)
                last_res = {
                    "status": "delivered",
                    "message_id": message_record["message_id"],
                    "simulated": True,
                }
                continue

            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            body = {
                "chat_id": target_chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": False,
            }

            try:
                response = await self.http_client.post(url, json=body)
                if response.status_code == 400 and ("parse" in response.text.lower() or "entity" in response.text.lower() or "tag" in response.text.lower()):
                    logger.warning(
                        f"Telegram message parse failed: {response.text}. Retrying with clean plain text..."
                    )
                    if parse_mode.lower() == "html":
                        plain_text = re.sub(r"<[^>]+>", "", chunk)
                    else:
                        plain_text = chunk.replace("*", "").replace("`", "").replace("_", "")
                    plain_body = {
                        "chat_id": target_chat_id,
                        "text": plain_text,
                        "disable_web_page_preview": False,
                    }
                    response = await self.http_client.post(url, json=plain_body)

                if response.status_code == 200:
                    resp_json = response.json()
                    msg_id = resp_json.get("result", {}).get("message_id", 0)
                    message_record = {
                        "message_id": msg_id,
                        "chat_id": target_chat_id,
                        "text": chunk,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "parse_mode": parse_mode,
                    }
                    self.sent_messages.append(message_record)
                    last_res = {"status": "delivered", "message_id": msg_id}
                else:
                    err_msg = f"Telegram Bot API returned HTTP {response.status_code}: {response.text}"
                    logger.error(err_msg)
                    last_res = {"status": "failed", "error": err_msg}
            except Exception as exc:
                logger.error(f"Telegram text dispatch error: {exc}")
                last_res = {"status": "failed", "error": str(exc)}

        return last_res

    def format_summary_message(self, summary_data: Dict[str, Any]) -> str:
        """
        Format full-spectrum AI Situational Summary into Telegram Markdown SitRep.
        """
        theme_name = summary_data.get("theme_name") or "All Monitored Themes"
        time_range = str(summary_data.get("time_range") or "24h").upper()
        trajectory = str(summary_data.get("threat_trajectory") or "STABLE").upper()
        threat_score = summary_data.get("threat_level_score", 5)

        telemetry = summary_data.get("telemetry") or {}
        velocity = telemetry.get("velocity_status", "Normal")
        total_events = telemetry.get("total_events", 0)
        corrob_rate = telemetry.get("corroboration_rate_pct", 0)
        critical_count = telemetry.get("critical_alerts_count", 0)

        lines = [
            "📊 *OSINT SITUATIONAL SUMMARY (SitRep)*",
            f"🎯 *Theme:* {theme_name} | ⏱️ *Window:* {time_range}",
            f"⚠️ *Threat Posture:* {trajectory} [{threat_score}/10]",
            f"📡 *Telemetry:* Velocity: {velocity} | Events: {total_events} | Corroboration: {corrob_rate}% | Critical: {critical_count}",
            "",
        ]

        bluf = (summary_data.get("executive_bluf") or "").strip()
        if bluf:
            lines.extend(["*BLUF (Bottom Line Up Front):*", bluf, ""])

        # Key developments
        key_devs = summary_data.get("key_developments") or []
        dev_bullets = []
        for d in key_devs[:5]:
            if isinstance(d, dict):
                bullet_text = d.get("bullet") or d.get("headline") or ""
                score = d.get("importance_score")
                prefix = f"[{score}/10] " if score is not None else ""
                if bullet_text:
                    dev_bullets.append(f"• {prefix}{bullet_text}".strip())
            elif isinstance(d, str) and d.strip():
                dev_bullets.append(f"• {d.strip()}")

        if dev_bullets:
            lines.extend(["*Key Operational Developments:*"] + dev_bullets + [""])

        # Strategic assessment
        strat = (summary_data.get("strategic_assessment") or "").strip()
        if strat:
            lines.extend(["*Strategic Assessment:*", strat, ""])

        # Watchlist indicators
        indicators = summary_data.get("watchlist_indicators") or []
        indicator_bullets = []
        for ind in indicators[:4]:
            if isinstance(ind, str) and ind.strip():
                indicator_bullets.append(f"• {ind.strip()}")
            elif isinstance(ind, dict) and ind.get("indicator"):
                indicator_bullets.append(f"• {ind['indicator'].strip()}")

        if indicator_bullets:
            lines.extend(["*Watchlist Indicators (12-24h):*"] + indicator_bullets + [""])

        # Footer
        active_model = summary_data.get("active_ai_model") or "OSINT Synthesis Engine"
        gen_at = summary_data.get("generated_at")
        if gen_at:
            try:
                dt = datetime.fromisoformat(str(gen_at).replace("Z", "+00:00"))
                time_str = dt.strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                time_str = str(gen_at)
        else:
            time_str = utc_now().strftime("%Y-%m-%d %H:%M UTC")

        lines.append(f"🤖 _Synthesized by {active_model} at {time_str}_")

        return "\n".join(lines)

    async def handle_command(
        self,
        command_text: str,
        chat_id: str,
        message_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Parse and execute incoming Telegram bot commands (/summary, /status, /help, etc.).
        Enforces chat_id authorization and auto-discovers if not configured.
        """
        chat_id_str = str(chat_id)

        # 1. Chat Authorization & Auto-Discovery
        if not self.chat_id:
            logger.info(f"Auto-discovering and binding Telegram chat_id: {chat_id_str} via command '{command_text}'")
            self.chat_id = chat_id_str
            os.environ["TELEGRAM_CHAT_ID"] = chat_id_str
            try:
                from sqlalchemy.orm.attributes import flag_modified
                session_factory = get_session_factory()
                async with session_factory() as session:
                    res = await session.execute(
                        select(SystemSettings).where(SystemSettings.key == "api_keys")
                    )
                    existing = res.scalars().first()
                    if existing and isinstance(existing.value_json, dict):
                        updated = dict(existing.value_json)
                        updated["telegram_chat_id"] = chat_id_str
                        existing.value_json = updated
                        flag_modified(existing, "value_json")
                    else:
                        session.add(
                            SystemSettings(
                                key="api_keys",
                                value_json={"telegram_chat_id": chat_id_str},
                            )
                        )
                    await session.commit()
            except Exception as db_exc:
                logger.debug(f"Could not persist discovered chat_id to DB: {db_exc}")
        elif str(self.chat_id) != chat_id_str:
            logger.warning(
                f"Unauthorized command '{command_text}' from chat_id {chat_id_str} (configured: {self.chat_id})"
            )
            await self.send_text_message(
                "⛔ *Access Denied*\nThis OSINT Alert Bot is restricted to authorized intelligence watch channels.",
                chat_id=chat_id_str,
            )
            return {"status": "unauthorized", "chat_id": chat_id_str}

        # 2. Parse command and arguments
        parts = command_text.strip().split()
        if not parts:
            return {"status": "empty"}

        cmd = parts[0].lower().split("@")[0]
        args = [p.lower() for p in parts[1:]]

        # Command: /start or /help
        if cmd in ("/start", "/help"):
            help_msg = (
                "🛡️ *OSINT Intelligence Alert Bot*\n\n"
                "Available Commands:\n"
                "• `/summary [window] [refresh]` - Generate or retrieve AI Situational Summary (SitRep)\n"
                "  _Windows:_ `6h`, `24h`, `3d`, `7d`, `all` (default: `24h`)\n"
                "  _Examples:_ `/summary`, `/summary 6h`, `/summary all refresh`\n"
                "• `/briefing` or `/sitrep` - Aliases for `/summary`\n"
                "• `/latest` - View the 5 most recent critical alerts\n"
                "• `/threshold [score]` - View or adjust alert sensitivity threshold (1-10)\n"
                "• `/status` - View current monitoring telemetry, active theme, and alert rules\n"
                "• `/help` - Show this guidance menu"
            )
            await self.send_text_message(help_msg, chat_id=chat_id_str)
            return {"status": "delivered", "command": "help"}

        # Command: /status
        elif cmd in ("/status", "/health"):
            theme_name = "All Monitored Themes"
            alert_thresh = 7
            ev_count = 0
            try:
                session_factory = get_session_factory()
                async with session_factory() as session:
                    theme_stmt = select(Theme).where(Theme.is_active == True).limit(1)
                    active_t = (await session.execute(theme_stmt)).scalars().first()
                    if active_t:
                        theme_name = active_t.name
                        alert_thresh = active_t.alert_threshold or 7

                    now = utc_now()
                    since_24h = now - timedelta(hours=24)
                    count_stmt = select(func.count(Event.id)).where(Event.published_at >= since_24h)
                    ev_count = (await session.execute(count_stmt)).scalar() or 0
            except Exception as exc:
                logger.debug(f"Status query error: {exc}")

            status_msg = (
                f"🛰️ *OSINT Watch Station Status*\n\n"
                f"🎯 *Active Theme:* {theme_name}\n"
                f"🚨 *Alert Threshold:* ≥{alert_thresh}/10\n"
                f"🔊 *Audio Alerts:* Scores 9-10 (Loud & Vibration)\n"
                f"🔕 *Silent Alerts:* Scores 7-8 (Silent Notification)\n"
                f"⏱️ *Rate Limit Cooldown:* {self.rate_limit_seconds}s\n"
                f"📊 *Reports Ingested (Last 24h):* {ev_count}\n"
                f"🤖 *Alert Dispatcher:* Operational"
            )
            await self.send_text_message(status_msg, chat_id=chat_id_str)
            return {"status": "delivered", "command": "status"}

        # Command: /latest or /recent or /alerts
        elif cmd in ("/latest", "/recent", "/alerts"):
            try:
                session_factory = get_session_factory()
                async with session_factory() as session:
                    theme_stmt = select(Theme).where(Theme.is_active == True).limit(1)
                    active_t = (await session.execute(theme_stmt)).scalars().first()
                    thresh = active_t.alert_threshold if active_t and active_t.alert_threshold else 7

                    ev_stmt = (
                        select(Event)
                        .where(Event.importance_score >= thresh)
                        .order_by(Event.published_at.desc())
                        .limit(5)
                    )
                    recent_evs = list((await session.execute(ev_stmt)).scalars().all())

                    if not recent_evs:
                        ev_stmt_top = (
                            select(Event)
                            .order_by(Event.importance_score.desc(), Event.published_at.desc())
                            .limit(5)
                        )
                        recent_evs = list((await session.execute(ev_stmt_top)).scalars().all())

                if not recent_evs:
                    await self.send_text_message(
                        "ℹ️ No recorded alerts in current observation window.",
                        chat_id=chat_id_str,
                    )
                    return {"status": "delivered", "command": "latest", "count": 0}

                lines = [f"🚨 <b>Recent Critical OSINT Alerts (Top {len(recent_evs)})</b>\n"]
                for i, ev in enumerate(recent_evs, 1):
                    sc = ev.importance_score or 5
                    badge = "🔴" if sc >= 9 else ("🟠" if sc >= 8 else "🟡")
                    head = (ev.headline or ev.title or "Untitled Report").strip()
                    time_s = ev.published_at.strftime("%m-%d %H:%M UTC") if ev.published_at else "Recent"
                    source = ev.source_name or "OSINT Stream"

                    if ev.source_url:
                        title_line = f"<a href=\"{html.escape(ev.source_url)}\">{html.escape(head)}</a>"
                    else:
                        title_line = html.escape(head)

                    lines.append(f"{i}. {badge} <b>[{sc}/10]</b> {title_line}")
                    lines.append(f"   📡 <i>{html.escape(source)}</i> | ⏱️ <i>{time_s}</i>")
                    if ev.summary:
                        brief_sum = html.escape(ev.summary.strip()[:180])
                        if len(ev.summary.strip()) > 180:
                            brief_sum += "..."
                        lines.append(f"   <i>{brief_sum}</i>")
                    lines.append("")

                recap_msg = "\n".join(lines).strip()
                await self.send_text_message(recap_msg, chat_id=chat_id_str, parse_mode="HTML")
                return {"status": "delivered", "command": "latest", "count": len(recent_evs)}
            except Exception as exc:
                logger.error(f"Failed to fetch latest alerts for Telegram: {exc}", exc_info=True)
                await self.send_text_message(
                    f"⚠️ Failed to retrieve recent alerts: `{str(exc)}`",
                    chat_id=chat_id_str,
                )
                return {"status": "failed", "command": "latest", "error": str(exc)}

        # Command: /threshold or /sensitivity
        elif cmd in ("/threshold", "/sensitivity", "/cutoff"):
            if not args:
                current_thresh = 7
                try:
                    session_factory = get_session_factory()
                    async with session_factory() as session:
                        theme_stmt = select(Theme).where(Theme.is_active == True).limit(1)
                        active_t = (await session.execute(theme_stmt)).scalars().first()
                        if active_t and active_t.alert_threshold:
                            current_thresh = active_t.alert_threshold
                except Exception as exc:
                    logger.debug(f"Threshold query error: {exc}")

                info_msg = (
                    f"🚨 <b>Alert Sensitivity Threshold</b>\n\n"
                    f"🎯 <b>Current Threshold:</b> ≥{current_thresh}/10\n"
                    f"🔊 <b>Audio Notification:</b> Scores 9-10\n"
                    f"🔕 <b>Silent Notification:</b> Scores 7-8\n\n"
                    f"To adjust threshold, send: <code>/threshold &lt;1-10&gt;</code>\n"
                    f"<i>Example:</i> <code>/threshold 8</code>"
                )
                await self.send_text_message(info_msg, chat_id=chat_id_str, parse_mode="HTML")
                return {"status": "delivered", "command": "threshold", "threshold": current_thresh}

            try:
                new_thresh = int(args[0])
                if not (1 <= new_thresh <= 10):
                    raise ValueError()
            except Exception:
                err_text = (
                    "⚠️ <b>Invalid Threshold Value</b>\n\n"
                    "Please provide an integer between <b>1</b> and <b>10</b>.\n"
                    "<i>Example:</i> <code>/threshold 8</code>"
                )
                await self.send_text_message(err_text, chat_id=chat_id_str, parse_mode="HTML")
                return {"status": "invalid_argument", "command": "threshold"}

            try:
                from sqlalchemy.orm.attributes import flag_modified
                session_factory = get_session_factory()
                async with session_factory() as session:
                    theme_stmt = select(Theme).where(Theme.is_active == True)
                    themes = (await session.execute(theme_stmt)).scalars().all()
                    for t in themes:
                        t.alert_threshold = new_thresh

                    st_thresh = (
                        await session.execute(
                            select(SystemSettings).where(SystemSettings.key == "notification_threshold")
                        )
                    ).scalars().first()
                    if st_thresh:
                        st_thresh.value_json = {"threshold": new_thresh}
                        flag_modified(st_thresh, "value_json")
                    else:
                        session.add(SystemSettings(key="notification_threshold", value_json={"threshold": new_thresh}))

                    st_cfg = (
                        await session.execute(
                            select(SystemSettings).where(SystemSettings.key == "notification_settings")
                        )
                    ).scalars().first()
                    if st_cfg and isinstance(st_cfg.value_json, dict):
                        updated = dict(st_cfg.value_json)
                        updated["default_alert_threshold"] = new_thresh
                        st_cfg.value_json = updated
                        flag_modified(st_cfg, "value_json")

                    await session.commit()

                if self.redis_bus and self.redis_bus.is_connected:
                    await self.redis_bus.publish(
                        CHANNEL_CONFIG_RELOAD,
                        {"source": "telegram_command", "threshold": new_thresh},
                    )

                confirm_msg = (
                    f"✅ <b>Alert Notification Threshold Updated</b>\n\n"
                    f"🎯 <b>New Sensitivity:</b> ≥{new_thresh}/10\n"
                    f"Alerts with importance score ≥{new_thresh} will now trigger notifications."
                )
                await self.send_text_message(confirm_msg, chat_id=chat_id_str, parse_mode="HTML")
                return {"status": "delivered", "command": "threshold", "new_threshold": new_thresh}
            except Exception as exc:
                logger.error(f"Failed to update threshold from Telegram: {exc}", exc_info=True)
                await self.send_text_message(f"⚠️ Failed to update threshold: `{str(exc)}`", chat_id=chat_id_str)
                return {"status": "failed", "command": "threshold", "error": str(exc)}

        # Command: /summary, /briefing, /sitrep, /ai_summary
        elif cmd in ("/summary", "/briefing", "/sitrep", "/ai_summary"):
            time_range = "24h"
            force_refresh = False

            for a in args:
                if a in ("6h", "24h", "3d", "7d", "all"):
                    time_range = a
                elif a in ("refresh", "fresh", "force", "reload"):
                    force_refresh = True

            # Indicate bot activity
            await self.send_chat_action(action="typing", chat_id=chat_id_str)
            await self.send_text_message(
                f"🔄 *Generating AI Situational Summary ({time_range.upper()})...*\n"
                "_Synthesizing semantic storyline clusters, multi-source corroboration, and live telemetry..._",
                chat_id=chat_id_str,
            )

            try:
                from backend.app.services.summary_service import get_summary_service
                session_factory = get_session_factory()
                async with session_factory() as session:
                    summary_svc = get_summary_service()
                    summary_data = await summary_svc.generate_or_get_summary(
                        session=session,
                        theme_id=None,
                        time_range=time_range,
                        force_refresh=force_refresh,
                    )

                formatted_sitrep = self.format_summary_message(summary_data)
                res = await self.send_text_message(formatted_sitrep, chat_id=chat_id_str)
                return {
                    "status": res.get("status", "delivered"),
                    "command": "summary",
                    "time_range": time_range,
                    "force_refresh": force_refresh,
                    "summary": summary_data,
                }
            except Exception as exc:
                logger.error(f"Failed to generate situational summary for Telegram: {exc}", exc_info=True)
                err_msg = (
                    f"⚠️ *Failed to generate AI Situational Summary:*\n"
                    f"`{str(exc)}`\n"
                    "Please verify system status or try again shortly."
                )
                await self.send_text_message(err_msg, chat_id=chat_id_str)
                return {"status": "failed", "command": "summary", "error": str(exc)}

        # Unrecognized command
        elif cmd.startswith("/"):
            unknown_msg = f"❓ Unknown command `{cmd}`. Use /help to see available commands or /summary to request an AI SitRep."
            await self.send_text_message(unknown_msg, chat_id=chat_id_str)
            return {"status": "unknown_command", "command": cmd}

        return {"status": "ignored"}

    async def handle_update(self, update: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Process an incoming update object from Telegram getUpdates API."""
        if not isinstance(update, dict):
            return None

        msg = update.get("message") or update.get("channel_post")
        if not msg or not isinstance(msg, dict):
            return None

        text = str(msg.get("text") or msg.get("caption") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id")) if chat.get("id") is not None else None
        msg_id = msg.get("message_id")

        if not chat_id or not text:
            return None

        if text.startswith("/"):
            return await self.handle_command(command_text=text, chat_id=chat_id, message_id=msg_id)

        return None

    async def _poll_updates_loop(self) -> None:
        """
        Continuous long-polling loop fetching Telegram updates and dispatching commands.
        """
        logger.info("Telegram update polling loop started.")
        consecutive_errors = 0
        while self._is_running:
            if not self.bot_token:
                await asyncio.sleep(5.0)
                continue
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                params = {
                    "offset": self._last_update_id + 1,
                    "timeout": 20,
                    "allowed_updates": json.dumps(["message", "channel_post"]),
                }
                resp = await self.http_client.get(url, params=params, timeout=25.0)
                if resp.status_code == 200:
                    consecutive_errors = 0
                    data = resp.json()
                    updates = data.get("result", [])
                    for upd in updates:
                        upd_id = upd.get("update_id", 0)
                        if upd_id > self._last_update_id:
                            self._last_update_id = upd_id
                        try:
                            await self.handle_update(upd)
                        except Exception as upd_exc:
                            logger.error(f"Error handling Telegram update {upd_id}: {upd_exc}", exc_info=True)
                elif resp.status_code in (401, 404):
                    logger.warning(f"Telegram polling unauthorized/invalid token (HTTP {resp.status_code}). Backing off.")
                    await asyncio.sleep(30.0)
                elif resp.status_code == 409:
                    logger.warning("Telegram polling conflict (HTTP 409: another instance polling). Backing off 10s.")
                    await asyncio.sleep(10.0)
                else:
                    logger.warning(f"Telegram getUpdates returned HTTP {resp.status_code}. Backing off.")
                    await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                logger.info("Telegram polling loop cancelled.")
                break
            except (httpx.TimeoutException, httpx.ReadTimeout):
                continue
            except Exception as exc:
                consecutive_errors += 1
                backoff = min(30.0, 2.0 * consecutive_errors)
                logger.debug(f"Telegram polling error ({exc}). Backing off {backoff:.1f}s.")
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
        logger.info("Telegram update polling loop terminated.")

    async def _handle_bus_alert(self, channel: str, data: Any) -> None:
        """Process incoming alerts from the Redis event bus."""
        try:
            if isinstance(data, str):
                data = json.loads(data)
            if not isinstance(data, dict):
                return

            event_id = data.get("event_id") or data.get("id")
            if not event_id:
                return

            pub_at = data.get("published_at")
            if isinstance(pub_at, str):
                try:
                    pub_date = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                except Exception:
                    pub_date = utc_now()
            elif isinstance(pub_at, datetime):
                pub_date = pub_at
            else:
                pub_date = utc_now()

            # Extract rich fields
            meta = data.get("metadata_json") or data.get("metadata") or {}
            scoring_bd = meta.get("scoring_breakdown") or {}
            corrob_st = data.get("corroboration_status") or scoring_bd.get("corroboration_status")
            corrob_cnt = data.get("corroboration_count") or scoring_bd.get("corroboration_count", 0)
            trend_cat = data.get("trend_category") or meta.get("trend_category")
            entities = data.get("entities") or meta.get("entities") or []

            payload = TelegramAlertPayload(
                event_id=str(event_id),
                title=str(data.get("title", "")),
                headline=data.get("headline"),
                summary=str(data.get("summary") or data.get("title", "")),
                importance_score=int(data.get("importance_score", 7)),
                source_name=str(data.get("source_name", "OSINT Stream")),
                published_at=pub_date,
                source_url=data.get("source_url"),
                trend_category=trend_cat,
                entities=entities if isinstance(entities, list) else [],
                corroboration_status=corrob_st,
                corroboration_count=int(corrob_cnt or 0),
                score_rationale=data.get("score_rationale"),
            )
            res = await self.send_alert(payload)
            # If throttled by cooldown, wait for remaining cooldown under lock and retry once so breaking news batches aren't discarded
            if res.get("status") == "throttled" and res.get("reason") == "rate_limit_cooldown":
                async with self._retry_lock:
                    remaining = max(0.5, self.rate_limit_seconds - (time.time() - self.last_sent_timestamp) + 0.1)
                    if os.environ.get("PYTEST_CURRENT_TEST"):
                        wait_sec = min(remaining, 2.0)
                    else:
                        wait_sec = min(remaining, 35.0)
                    await asyncio.sleep(wait_sec)
                    await self.send_alert(payload)
        except Exception as exc:
            logger.error(f"Error handling bus alert: {exc}", exc_info=True)

    async def register_bot_commands(self) -> bool:
        """Register Telegram Bot commands menu with Telegram API."""
        if not self.bot_token or os.environ.get("PYTEST_CURRENT_TEST"):
            return True
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/setMyCommands"
            body = {
                "commands": [
                    {"command": "summary", "description": "Generate AI Situational Summary (SitRep)"},
                    {"command": "latest", "description": "View recent critical alerts recap"},
                    {"command": "threshold", "description": "View or adjust alert sensitivity threshold"},
                    {"command": "status", "description": "View station telemetry & audio rules"},
                    {"command": "help", "description": "Show bot commands guidance menu"},
                ]
            }
            resp = await self.http_client.post(url, json=body, timeout=8.0)
            if resp.status_code == 200:
                logger.info("Registered Telegram bot commands menu.")
                return True
        except Exception as exc:
            logger.debug(f"Failed to register Telegram bot commands: {exc}")
        return False

    async def _handle_bus_config_reload(self, channel: str, data: Any) -> None:
        """Hot-reload bot configuration and credentials from DB upon reload event."""
        try:
            session_factory = get_session_factory()
            async with session_factory() as session:
                stmt = select(SystemSettings).where(
                    SystemSettings.key.in_(["notification_settings", "api_keys"])
                )
                rows = (await session.execute(stmt)).scalars().all()
                for row in rows:
                    if not row or not isinstance(row.value_json, dict):
                        continue
                    if row.key == "notification_settings":
                        cfg = row.value_json
                        if "cooldown_seconds" in cfg:
                            self.rate_limit_seconds = float(cfg["cooldown_seconds"])
                        if "bot_token" in cfg and cfg["bot_token"]:
                            self.bot_token = cfg["bot_token"]
                        if "chat_id" in cfg and cfg["chat_id"]:
                            self.chat_id = cfg["chat_id"]
                    elif row.key == "api_keys":
                        keys = row.value_json
                        if keys.get("telegram_bot_token"):
                            self.bot_token = keys["telegram_bot_token"]
                        if keys.get("telegram_chat_id"):
                            self.chat_id = keys["telegram_chat_id"]
                logger.info("Hot-reloaded Telegram bot settings from DB.")
                if self._is_running and self.bot_token and not os.environ.get("PYTEST_CURRENT_TEST"):
                    if self._polling_task is None or self._polling_task.done():
                        self._polling_task = asyncio.create_task(self._poll_updates_loop())
        except Exception as exc:
            logger.debug(f"Failed to hot-reload bot settings from DB: {exc}")

    async def start(self) -> None:
        """Start listening to Redis alert channels and load initial DB configuration."""
        if self._is_running:
            return
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            await self._handle_bus_config_reload(CHANNEL_CONFIG_RELOAD, {})
            if self.bot_token and not self.chat_id:
                await self.auto_discover_chat_id()
        self.redis_bus.subscribe(CHANNEL_EVENTS_ALERT, self._handle_bus_alert)
        self.redis_bus.subscribe(CHANNEL_CONFIG_RELOAD, self._handle_bus_config_reload)
        self._is_running = True

        if not os.environ.get("PYTEST_CURRENT_TEST") and self.bot_token:
            try:
                await self.register_bot_commands()
            except Exception as cmd_exc:
                logger.debug(f"Could not register Telegram bot commands: {cmd_exc}")
            if self._polling_task is None or self._polling_task.done():
                self._polling_task = asyncio.create_task(self._poll_updates_loop())

        logger.info("TelegramBotService started and subscribed to alert channels.")

    async def stop(self) -> None:
        """Stop listening and cleanly close HTTP client and polling task."""
        if not self._is_running:
            return
        self._is_running = False
        self.redis_bus.unsubscribe(CHANNEL_EVENTS_ALERT, self._handle_bus_alert)
        self.redis_bus.unsubscribe(CHANNEL_CONFIG_RELOAD, self._handle_bus_config_reload)

        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None

        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        logger.info("TelegramBotService stopped.")


# Singleton instance
_telegram_bot_instance: Optional[TelegramBotService] = None


def get_telegram_bot() -> TelegramBotService:
    """Return global TelegramBotService singleton instance."""
    global _telegram_bot_instance
    if _telegram_bot_instance is None:
        _telegram_bot_instance = TelegramBotService()
    return _telegram_bot_instance
