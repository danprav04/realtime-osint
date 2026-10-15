"""
Real-Time Socket.IO ASGI Gateway & Redis Pub/Sub Bridge.
Provides WebSocket / Socket.IO live event streaming, memory-tuned connection handling,
API key authentication, and Redis channel integration.
"""

import asyncio
from collections import deque
import logging
from typing import Any, Dict, List, Optional
import urllib.parse
import socketio
from backend.app.auth import verify_api_key_string, verify_viewer_token
from backend.app.services.redis_bus import (
    CHANNEL_EVENTS_ALERT,
    CHANNEL_EVENTS_NEW,
    CHANNEL_STATS_UPDATE,
    CHANNEL_MAP_UPDATE,
    RedisBus,
    get_redis_bus,
)

logger = logging.getLogger("osint.socket_server")

# Tuned for memory efficiency: 256KB max frame buffer, 25s ping, 20s timeout
sio: socketio.AsyncServer = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
    ping_timeout=20,
    ping_interval=25,
    max_http_buffer_size=262144,  # 256 KB max frame buffer
    logger=False,
    engineio_logger=False,
)

# ASGI Application wrapper mounted under /socket.io
sio_app: socketio.ASGIApp = socketio.ASGIApp(
    socketio_server=sio,
    socketio_path="",  # empty string allows Starlette mount at /socket.io to route cleanly
)

# Bounded ring buffer for emitted events (<150KB RSS memory ceiling)
_broadcast_log: deque = deque(maxlen=100)
_active_clients: Dict[str, Dict[str, Any]] = {}
_is_initialized: bool = False


def _extract_auth_token(environ: Dict[str, Any], auth: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Extract authentication credentials from Socket.IO client auth dict,
    query parameters, or HTTP request headers.
    """
    # 1. Socket.IO v4 client auth dict
    if isinstance(auth, dict):
        candidate = (
            auth.get("viewer_token")
            or auth.get("token")
            or auth.get("api_key")
            or auth.get("apiKey")
        )
        if candidate:
            return str(candidate)

    # 2. Query string in environ
    query_str = environ.get("QUERY_STRING", "")
    if query_str:
        qs = urllib.parse.parse_qs(query_str)
        for key in ("viewer_token", "token", "api_key", "apiKey", "key"):
            if key in qs and qs[key]:
                return qs[key][0]

    # 3. HTTP headers (for polling transport handshake)
    if "HTTP_X_VIEWER_TOKEN" in environ:
        return environ["HTTP_X_VIEWER_TOKEN"]
    if "HTTP_X_API_KEY" in environ:
        return environ["HTTP_X_API_KEY"]
    auth_header = environ.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    return None


@sio.event
async def connect(sid: str, environ: Dict[str, Any], auth: Optional[Dict[str, Any]] = None) -> None:
    """
    Validate client API key or viewer token upon connection.
    Rejects unauthenticated or invalid clients with ConnectionRefusedError.
    """
    token = _extract_auth_token(environ, auth)
    is_admin = verify_api_key_string(token)
    is_viewer = False if is_admin else verify_viewer_token(token)

    if not is_admin and not is_viewer:
        logger.warning(f"Rejected unauthorized Socket.IO connection: sid={sid}")
        raise socketio.exceptions.ConnectionRefusedError("Unauthorized: Invalid or missing API key or viewer token")

    role = "admin" if is_admin else "viewer"
    _active_clients[sid] = {
        "connected_at": asyncio.get_event_loop().time(),
        "role": role,
    }
    logger.info(f"Socket.IO client connected ({role}): {sid} (total active: {len(_active_clients)})")


@sio.event
async def disconnect(sid: str) -> None:
    """Handle client disconnection and release session references."""
    _active_clients.pop(sid, None)
    logger.info(f"Socket.IO client disconnected: {sid} (total active: {len(_active_clients)})")


@sio.event
async def subscribe_theme(sid: str, data: Dict[str, Any]) -> None:
    """Join a theme-specific Socket.IO room."""
    if isinstance(data, dict):
        theme_id = data.get("theme_id")
        if theme_id:
            room_name = f"theme:{theme_id}"
            await sio.enter_room(sid, room_name)
            logger.debug(f"Client {sid} joined room {room_name}")


@sio.event
async def unsubscribe_theme(sid: str, data: Dict[str, Any]) -> None:
    """Leave a theme-specific Socket.IO room."""
    if isinstance(data, dict):
        theme_id = data.get("theme_id")
        if theme_id:
            room_name = f"theme:{theme_id}"
            await sio.leave_room(sid, room_name)
            logger.debug(f"Client {sid} left room {room_name}")


async def handle_bus_event_new(channel: str, data: Any) -> None:
    """Broadcast newly ingested and analyzed events to all clients."""
    payload = data if isinstance(data, dict) else {"raw": data}
    _broadcast_log.append({"type": "event:new", "payload": payload})
    await sio.emit("event:new", payload)


async def handle_bus_event_alert(channel: str, data: Any) -> None:
    """Broadcast high-importance critical alerts to all clients."""
    payload = data if isinstance(data, dict) else {"raw": data}
    if isinstance(payload, dict):
        if "event_id" in payload and "id" not in payload:
            payload["id"] = payload["event_id"]
        elif "id" in payload and "event_id" not in payload:
            payload["event_id"] = payload["id"]
        if "published_at" not in payload and "created_at" not in payload and "timestamp" not in payload:
            from datetime import datetime, timezone
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    _broadcast_log.append({"type": "alert:critical", "payload": payload})
    await sio.emit("alert:critical", payload)


async def handle_bus_stats_update(channel: str, data: Any) -> None:
    """Broadcast updated dashboard statistics."""
    payload = data if isinstance(data, dict) else {"raw": data}
    _broadcast_log.append({"type": "stats:update", "payload": payload})
    await sio.emit("stats:update", payload)


async def handle_bus_map_update(channel: str, data: Any) -> None:
    """Broadcast embeddings relevance map update notification."""
    payload = data if isinstance(data, dict) else {"raw": data}
    _broadcast_log.append({"type": "map:update", "payload": payload})
    await sio.emit("map:update", payload)


async def broadcast_summary_update(summary_payload: Dict[str, Any]) -> None:
    """Broadcast refreshed AI situational summary notification to all clients."""
    try:
        payload = {
            "theme_id": summary_payload.get("theme_id"),
            "theme_name": summary_payload.get("theme_name"),
            "generated_at": summary_payload.get("generated_at"),
            "threat_trajectory": summary_payload.get("threat_trajectory"),
            "threat_level_score": summary_payload.get("threat_level_score"),
        }
        _broadcast_log.append({"type": "summary:update", "payload": payload})
        await sio.emit("summary:update", payload)
    except Exception as exc:
        logger.warning(f"Failed to emit summary:update: {exc}")



async def init_socket_server(bus: Optional[RedisBus] = None) -> None:
    """Subscribe Socket.IO broadcasters to the Redis event bus."""
    global _is_initialized
    if _is_initialized:
        return

    event_bus = bus or get_redis_bus()
    event_bus.subscribe(CHANNEL_EVENTS_NEW, handle_bus_event_new)
    event_bus.subscribe(CHANNEL_EVENTS_ALERT, handle_bus_event_alert)
    event_bus.subscribe(CHANNEL_STATS_UPDATE, handle_bus_stats_update)
    event_bus.subscribe(CHANNEL_MAP_UPDATE, handle_bus_map_update)
    _is_initialized = True
    logger.info("Real-Time Gateway Socket.IO server initialized and subscribed to Redis bus.")


async def shutdown_socket_server(bus: Optional[RedisBus] = None) -> None:
    """Unsubscribe handlers and cleanly reset client tracking."""
    global _is_initialized
    if not _is_initialized:
        return

    event_bus = bus or get_redis_bus()
    event_bus.unsubscribe(CHANNEL_EVENTS_NEW, handle_bus_event_new)
    event_bus.unsubscribe(CHANNEL_EVENTS_ALERT, handle_bus_event_alert)
    event_bus.unsubscribe(CHANNEL_STATS_UPDATE, handle_bus_stats_update)
    event_bus.unsubscribe(CHANNEL_MAP_UPDATE, handle_bus_map_update)
    _active_clients.clear()
    _is_initialized = False
    logger.info("Real-Time Gateway Socket.IO server shut down.")


def get_broadcast_logs() -> List[Dict[str, Any]]:
    """Return in-memory ring buffer of broadcast logs for test verification."""
    return list(_broadcast_log)


def clear_broadcast_logs() -> None:
    """Clear broadcast log history."""
    _broadcast_log.clear()


def get_active_client_count() -> int:
    """Return count of currently connected Socket.IO clients."""
    return len(_active_clients)
