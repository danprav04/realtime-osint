"""
backend/app/services/server_logs.py - In-memory Server Logs Ring Buffer & Diagnostics

Provides high-performance, thread-safe buffering of application logs,
automatic secret redaction, structured filtering, and telemetry statistics.
"""

import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# Redaction patterns for secrets, credentials, and API keys
SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"gsk_[a-zA-Z0-9_\-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"(bot)?\d{8,12}:[A-Za-z0-9_\-]{35}"),
    re.compile(r"Bearer\s+([A-Za-z0-9_\-\.]{15,})", re.IGNORECASE),
    re.compile(r"(password|secret|api_key|token)[\"']?\s*[:=]\s*[\"']?([^\"'\s,]+)", re.IGNORECASE),
]


def sanitize_log_message(msg: str) -> str:
    """Redact sensitive keys, passwords, and authorization tokens from log text."""
    if not msg:
        return ""
    sanitized = str(msg)
    for pat in SECRET_PATTERNS:
        # If pattern captures groups, mask group 2 or whole match
        if pat.groups > 1:
            sanitized = pat.sub(r'\1: "[REDACTED]"', sanitized)
        elif pat.groups == 1:
            sanitized = pat.sub(r'Bearer [REDACTED]', sanitized)
        else:
            sanitized = pat.sub("[REDACTED_SECRET]", sanitized)
    return sanitized


class ServerLogBuffer:
    """Thread-safe circular ring buffer for runtime server logs and diagnostics."""

    def __init__(self, max_size: int = 2000):
        self._max_size = max_size
        self._buffer: deque = deque(maxlen=max_size)
        self._counter: int = 0
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._created_at = datetime.now(timezone.utc).isoformat()

    def append_record(self, record: logging.LogRecord) -> Dict[str, Any]:
        """Convert a standard LogRecord to a sanitized structured log dictionary."""
        with self._lock:
            self._counter += 1
            entry_id = self._counter

        raw_msg = record.getMessage()
        safe_msg = sanitize_log_message(raw_msg)

        trace_id = getattr(record, "trace_id", None)
        if not trace_id:
            try:
                from backend.app.main import trace_id_ctx
                trace_id = trace_id_ctx.get()
            except Exception:
                trace_id = None

        exception_str: Optional[str] = None
        if record.exc_info:
            try:
                formatter = logging.Formatter()
                exception_str = formatter.formatException(record.exc_info)
            except Exception:
                exception_str = str(record.exc_info)

        extra_data = getattr(record, "extra_data", None)
        if isinstance(extra_data, dict):
            # Sanitize any string values inside extra_data
            safe_extra = {}
            for k, v in extra_data.items():
                if isinstance(v, str):
                    safe_extra[k] = sanitize_log_message(v)
                else:
                    safe_extra[k] = v
            extra_data = safe_extra

        # Format timestamp
        if hasattr(record, "created"):
            dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
            timestamp_str = dt.isoformat()
        else:
            timestamp_str = datetime.now(timezone.utc).isoformat()

        entry: Dict[str, Any] = {
            "id": entry_id,
            "timestamp": timestamp_str,
            "level": record.levelname,
            "logger": record.name,
            "message": safe_msg,
            "trace_id": trace_id,
            "source_file": record.filename if record.levelno >= logging.WARNING else None,
            "line_number": record.lineno if record.levelno >= logging.WARNING else None,
            "function": record.funcName if record.levelno >= logging.WARNING else None,
            "exception": exception_str,
            "extra_data": extra_data,
        }

        with self._lock:
            self._buffer.append(entry)

        return entry

    def append_manual(
        self,
        level: str,
        logger_name: str,
        message: str,
        trace_id: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Manually append a log entry to the buffer (used for testing or system events)."""
        with self._lock:
            self._counter += 1
            entry_id = self._counter

        entry: Dict[str, Any] = {
            "id": entry_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "logger": logger_name,
            "message": sanitize_log_message(message),
            "trace_id": trace_id,
            "source_file": None,
            "line_number": None,
            "function": None,
            "exception": None,
            "extra_data": extra_data,
        }

        with self._lock:
            self._buffer.append(entry)

        return entry

    def get_logs(
        self,
        level: Optional[str] = None,
        logger_name: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        since_id: Optional[int] = None,
        reverse: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Query and filter logs from the buffer.
        By default returns the newest logs first (reverse=True).
        """
        limit = max(1, min(limit, 1000))
        level_filter = level.upper().strip() if level else None
        logger_filter = logger_name.lower().strip() if logger_name else None
        search_filter = search.lower().strip() if search else None

        with self._lock:
            entries = list(self._buffer)

        # Apply since_id for incremental updates
        if since_id is not None:
            entries = [e for e in entries if e["id"] > since_id]

        # Apply level filter (supports exact or comma-separated list)
        if level_filter and level_filter != "ALL":
            allowed_levels = [lvl.strip() for lvl in level_filter.split(",")]
            entries = [e for e in entries if e["level"] in allowed_levels]

        # Apply logger filter
        if logger_filter and logger_filter != "all":
            entries = [e for e in entries if logger_filter in e["logger"].lower()]

        # Apply search query filter
        if search_filter:
            entries = [
                e for e in entries
                if search_filter in e["message"].lower()
                or (e.get("trace_id") and search_filter in str(e["trace_id"]).lower())
                or search_filter in e["logger"].lower()
            ]

        # Sorting: if reverse=True, newest first; otherwise most recent entries in chronological order
        if reverse:
            entries.reverse()
            return entries[:limit]

        return entries[-limit:]

    def clear(self) -> None:
        """Clear all entries in the ring buffer."""
        with self._lock:
            self._buffer.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate statistical distribution and health metrics of buffered logs."""
        with self._lock:
            entries = list(self._buffer)

        counts: Dict[str, int] = {
            "DEBUG": 0,
            "INFO": 0,
            "WARNING": 0,
            "ERROR": 0,
            "CRITICAL": 0,
        }
        loggers_set = set()

        for e in entries:
            lvl = e.get("level", "INFO")
            counts[lvl] = counts.get(lvl, 0) + 1
            loggers_set.add(e.get("logger", "unknown"))

        oldest = entries[0]["timestamp"] if entries else None
        newest = entries[-1]["timestamp"] if entries else None

        return {
            "total_buffered": len(entries),
            "max_capacity": self._max_size,
            "counts_by_level": counts,
            "active_loggers": sorted(list(loggers_set))[:20],
            "oldest_timestamp": oldest,
            "newest_timestamp": newest,
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "created_at": self._created_at,
        }

    def export_text(self, limit: int = 1000) -> str:
        """Format buffered logs into standard plaintext console format."""
        logs = self.get_logs(limit=limit, reverse=False)
        lines = []
        for e in logs:
            trace_str = f" [trace:{e['trace_id']}]" if e.get("trace_id") else ""
            lines.append(f"{e['timestamp']} [{e['level']}] {e['logger']}{trace_str}: {e['message']}")
            if e.get("exception"):
                lines.append(e["exception"])
        return "\n".join(lines)


class ServerLogBufferHandler(logging.Handler):
    """Logging handler that streams log records directly into the ServerLogBuffer."""

    def __init__(self, buffer: ServerLogBuffer, level: int = logging.NOTSET):
        super().__init__(level)
        self.buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append_record(record)
        except Exception:
            self.handleError(record)


# Global buffer and handler singletons
_GLOBAL_BUFFER = ServerLogBuffer(max_size=2000)
_GLOBAL_HANDLER = ServerLogBufferHandler(_GLOBAL_BUFFER)


def get_server_log_buffer() -> ServerLogBuffer:
    """Return the global server log buffer singleton."""
    return _GLOBAL_BUFFER


def get_server_log_buffer_handler() -> ServerLogBufferHandler:
    """Return the global server log buffer handler singleton."""
    return _GLOBAL_HANDLER
