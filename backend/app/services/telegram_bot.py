"""
Telegram Notification Bot & Alert Dispatcher.
Dispatches high-importance alerts via Telegram Bot API with token-bucket cooldown,
importance score 10 bypass, 24-hour URL deduplication, and database alert tracking.
"""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.config import get_settings
from backend.app.db.models import Alert, SystemSettings, utc_now
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
        self.simulate_error: Optional[str] = None

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

    def _get_dedup_hash(self, payload: TelegramAlertPayload) -> str:
        """Compute SHA-256 hash for 24h deduplication."""
        identifier = payload.source_url or payload.event_id or payload.title
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
            logger.info(
                f"Throttled alert for event {payload.event_id} (cooldown: {self.rate_limit_seconds}s)"
            )
            await self._record_alert_in_db(
                payload.event_id, payload.importance_score, status="throttled"
            )
            return {"status": "throttled", "reason": "rate_limit_cooldown"}

        # 4. Format message text
        formatted_text = self.format_message(payload)

        # 5. Check credentials
        if self.bot_token and not self.chat_id:
            await self.auto_discover_chat_id()

        if not self.bot_token or not self.chat_id:
            logger.warning(
                "Telegram bot token or chat ID not configured. Simulating successful local alert."
            )
            # In testing or development without live credentials, deliver to in-memory log
            self.last_sent_timestamp = time.time()
            message_record = {
                "message_id": len(self.sent_messages) + 1,
                "text": formatted_text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "payload": payload.model_dump(),
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
            "text": formatted_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }

        try:
            response = await self.http_client.post(url, json=body)
            self.last_sent_timestamp = time.time()

            if response.status_code == 200:
                resp_json = response.json()
                msg_id = resp_json.get("result", {}).get("message_id", 0)
                message_record = {
                    "message_id": msg_id,
                    "text": formatted_text,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "payload": payload.model_dump(),
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

            payload = TelegramAlertPayload(
                event_id=str(event_id),
                title=str(data.get("title", "")),
                summary=str(data.get("summary") or data.get("title", "")),
                importance_score=int(data.get("importance_score", 7)),
                source_name=str(data.get("source_name", "OSINT Stream")),
                published_at=pub_date,
                source_url=data.get("source_url"),
            )
            await self.send_alert(payload)
        except Exception as exc:
            logger.error(f"Error handling bus alert: {exc}", exc_info=True)

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
        except Exception as exc:
            logger.debug(f"Failed to hot-reload bot settings from DB: {exc}")

    async def start(self) -> None:
        """Start listening to Redis alert channels."""
        if self._is_running:
            return
        self.redis_bus.subscribe(CHANNEL_EVENTS_ALERT, self._handle_bus_alert)
        self.redis_bus.subscribe(CHANNEL_CONFIG_RELOAD, self._handle_bus_config_reload)
        self._is_running = True
        logger.info("TelegramBotService started and subscribed to alert channels.")

    async def stop(self) -> None:
        """Stop listening and cleanly close HTTP client."""
        if not self._is_running:
            return
        self.redis_bus.unsubscribe(CHANNEL_EVENTS_ALERT, self._handle_bus_alert)
        self.redis_bus.unsubscribe(CHANNEL_CONFIG_RELOAD, self._handle_bus_config_reload)
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._is_running = False
        logger.info("TelegramBotService stopped.")


# Singleton instance
_telegram_bot_instance: Optional[TelegramBotService] = None


def get_telegram_bot() -> TelegramBotService:
    """Return global TelegramBotService singleton instance."""
    global _telegram_bot_instance
    if _telegram_bot_instance is None:
        _telegram_bot_instance = TelegramBotService()
    return _telegram_bot_instance
