import asyncio
import json
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set
import redis.asyncio as aioredis
from backend.app.config import get_settings

logger = logging.getLogger("osint.redis_bus")

# Standard Pub/Sub channels for Real-time OSINT architecture
CHANNEL_CONFIG_RELOAD = "osint:config:reload"
CHANNEL_EVENTS_NEW = "osint:events:new"
CHANNEL_EVENTS_ALERT = "osint:events:alert"
CHANNEL_STATS_UPDATE = "osint:stats:update"
CHANNEL_MAP_UPDATE = "osint:map:update"

ALL_CHANNELS = [
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_NEW,
    CHANNEL_EVENTS_ALERT,
    CHANNEL_STATS_UPDATE,
    CHANNEL_MAP_UPDATE,
]

HandlerFunc = Callable[[str, Any], Coroutine[Any, Any, None]]


class RedisBus:
    """
    Asynchronous Redis Pub/Sub Event Bus with transparent offline fallback.
    When Redis is unavailable, events are gracefully routed to in-memory listeners
    so that local testing, SQLite mode, and offline environments operate seamlessly.
    """

    def __init__(self, redis_url: Optional[str] = None):
        self.settings = get_settings()
        self.redis_url = redis_url or self.settings.REDIS_URL
        self._client: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._listener_task: Optional[asyncio.Task] = None
        self._is_connected: bool = False
        self._fallback_mode: bool = True
        self._subscribers: Dict[str, Set[HandlerFunc]] = {ch: set() for ch in ALL_CHANNELS}
        self._subscribed_redis_channels: Set[str] = set()

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_fallback_mode(self) -> bool:
        return self._fallback_mode

    async def connect(self) -> bool:
        """
        Connect to Redis server. If connection fails or Redis is offline,
        gracefully fall back to in-memory event distribution without crashing.
        """
        if not self.settings.REDIS_ENABLED:
            logger.info("Redis is disabled via configuration. Running in in-memory fallback mode.")
            self._fallback_mode = True
            self._is_connected = False
            return False

        try:
            self._client = aioredis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
            )
            # Test ping
            await self._client.ping()
            self._is_connected = True
            self._fallback_mode = False
            logger.info(f"Connected to Redis Pub/Sub at {self.redis_url}")

            # Initialize pubsub and start background listener if we have subscriptions
            self._pubsub = self._client.pubsub()
            if self._subscribers:
                await self._refresh_redis_subscriptions()
                if self._listener_task is None or self._listener_task.done():
                    self._listener_task = asyncio.create_task(
                        self._listen_redis_loop(), name="redis_bus_listener"
                    )

            return True

        except Exception as exc:
            self._is_connected = False
            self._fallback_mode = True
            logger.warning(
                f"Redis unavailable at {self.redis_url}: {exc}. "
                "Operating in in-memory fallback mode."
            )
            return False

    async def disconnect(self) -> None:
        """Cleanly terminate Redis connections and background tasks."""
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None

        if self._pubsub:
            try:
                await self._pubsub.close()
            except Exception:
                pass
            self._pubsub = None

        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

        self._is_connected = False
        self._fallback_mode = True
        logger.info("Redis event bus disconnected.")

    async def publish(self, channel: str, message: Any) -> bool:
        """
        Publish a message to the specified channel.
        If Redis is online, publishes via Redis pubsub.
        If Redis is offline, dispatches directly to in-memory listeners.
        """
        if isinstance(message, (dict, list)):
            payload_str = json.dumps(message, default=str)
        elif not isinstance(message, str):
            payload_str = str(message)
        else:
            payload_str = message

        published_to_redis = False

        if self._is_connected and self._client:
            try:
                await self._client.publish(channel, payload_str)
                published_to_redis = True
                logger.debug(f"Published to Redis channel '{channel}'")
            except Exception as exc:
                logger.warning(
                    f"Redis publish failed on '{channel}': {exc}. Falling back to in-memory listeners."
                )
                self._is_connected = False
                self._fallback_mode = True

        # Always notify in-memory subscribers if in fallback mode or if Redis was not used
        if self._fallback_mode or not published_to_redis:
            await self._dispatch_in_memory(channel, payload_str)

        return True

    def subscribe(self, channel: str, handler: HandlerFunc) -> None:
        """Register an async callback handler for a given channel."""
        if channel not in self._subscribers:
            self._subscribers[channel] = set()
        self._subscribers[channel].add(handler)
        logger.debug(f"Registered subscriber for channel '{channel}' (total: {len(self._subscribers[channel])})")

        # If Redis is active, schedule subscription update
        if self._is_connected and self._pubsub:
            asyncio.create_task(self._refresh_redis_subscriptions())

    def unsubscribe(self, channel: str, handler: HandlerFunc) -> None:
        """Remove a subscriber callback from a given channel."""
        if channel in self._subscribers and handler in self._subscribers[channel]:
            self._subscribers[channel].remove(handler)
            logger.debug(f"Unregistered subscriber from channel '{channel}'")

    async def _refresh_redis_subscriptions(self) -> None:
        """Synchronize active channel subscriptions with Redis PubSub."""
        if not self._is_connected or not self._pubsub:
            return

        active_channels = [ch for ch, handlers in self._subscribers.items() if handlers]
        channels_to_add = set(active_channels) - self._subscribed_redis_channels
        
        if channels_to_add:
            try:
                await self._pubsub.subscribe(*channels_to_add)
                self._subscribed_redis_channels.update(channels_to_add)
                logger.info(f"Subscribed to Redis channels: {list(channels_to_add)}")
                
                if self._listener_task is None or self._listener_task.done():
                    self._listener_task = asyncio.create_task(
                        self._listen_redis_loop(), name="redis_bus_listener"
                    )
            except Exception as exc:
                logger.warning(f"Failed to subscribe to Redis channels {channels_to_add}: {exc}")

    async def _listen_redis_loop(self) -> None:
        """Background loop reading published messages from Redis."""
        logger.debug("Redis PubSub listener loop started.")
        while self._is_connected and self._pubsub:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message["type"] == "message":
                    channel = message["channel"]
                    data = message["data"]
                    await self._dispatch_in_memory(channel, data)
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in Redis listener loop: {exc}")
                await asyncio.sleep(1.0)
        logger.debug("Redis PubSub listener loop stopped.")

    async def _dispatch_in_memory(self, channel: str, data: str) -> None:
        """Dispatch a message payload to all registered in-memory handlers."""
        # Try parsing JSON payload if possible
        try:
            parsed_data = json.loads(data)
        except Exception:
            parsed_data = data

        handlers = list(self._subscribers.get(channel, []))
        if not handlers:
            return

        for handler in handlers:
            try:
                res = handler(channel, parsed_data)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as exc:
                logger.error(f"Subscriber handler failed on channel '{channel}': {exc}", exc_info=True)

    async def check_health(self) -> Dict[str, Any]:
        """Check status of Redis connection and reported latency."""
        if not self._is_connected or not self._client:
            return {
                "status": "degraded" if self.settings.REDIS_FALLBACK_ON_ERROR else "unhealthy",
                "is_connected": False,
                "fallback_mode": self._fallback_mode,
                "subscribed_channels": list(self._subscribers.keys()),
            }

        try:
            start = asyncio.get_event_loop().time()
            await self._client.ping()
            latency_ms = round((asyncio.get_event_loop().time() - start) * 1000, 2)
            return {
                "status": "healthy",
                "is_connected": True,
                "fallback_mode": False,
                "latency_ms": latency_ms,
                "subscribed_channels": list(self._subscribers.keys()),
            }
        except Exception as exc:
            return {
                "status": "degraded",
                "is_connected": False,
                "fallback_mode": True,
                "error": str(exc),
                "subscribed_channels": list(self._subscribers.keys()),
            }


_bus_instance: Optional[RedisBus] = None


def get_redis_bus() -> RedisBus:
    """Return the global RedisBus singleton."""
    global _bus_instance
    if _bus_instance is None:
        _bus_instance = RedisBus()
    return _bus_instance
