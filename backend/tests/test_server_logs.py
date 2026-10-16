"""
backend/tests/test_server_logs.py - Unit and Integration Tests for Server Logs System
"""

import logging
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.app.config import get_settings
from backend.app.main import app
from backend.app.services.server_logs import (
    ServerLogBuffer,
    sanitize_log_message,
    get_server_log_buffer,
)


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def test_sanitize_secrets():
    # OpenAI / generic secret
    msg1 = "Failed to connect with sk-abcdef1234567890abcdef1234567890"
    sanitized1 = sanitize_log_message(msg1)
    assert "sk-" not in sanitized1
    assert "[REDACTED" in sanitized1

    # Groq secret
    msg2 = "Groq initialization with gsk_1234567890123456789012345678"
    sanitized2 = sanitize_log_message(msg2)
    assert "gsk_" not in sanitized2
    assert "[REDACTED" in sanitized2

    # Telegram bot token
    msg3 = "Telegram token bot123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678"
    sanitized3 = sanitize_log_message(msg3)
    assert "ABCdefGhIJKlmNoPQRsTUVwxyZ" not in sanitized3
    assert "[REDACTED" in sanitized3

    # Bearer token
    msg4 = "Authorization: Bearer supersecrettokenvalue12345"
    sanitized4 = sanitize_log_message(msg4)
    assert "supersecrettokenvalue" not in sanitized4
    assert "Bearer [REDACTED]" in sanitized4


def test_server_log_buffer_operations():
    buf = ServerLogBuffer(max_size=10)
    buf.clear()
    assert len(buf.get_logs()) == 0

    # Add logs with various levels and loggers
    buf.append_manual("INFO", "osint.pipeline", "Processing event 101")
    buf.append_manual("WARNING", "osint.supervisor", "RSS feed timeout")
    buf.append_manual("ERROR", "osint.telegram", "Failed to dispatch alert")
    buf.append_manual("DEBUG", "osint.db", "Executed query in 2ms")

    # Check total
    logs = buf.get_logs(limit=10, reverse=False)
    assert len(logs) == 4
    assert logs[0]["message"] == "Processing event 101"

    # Filter by level
    err_logs = buf.get_logs(level="ERROR")
    assert len(err_logs) == 1
    assert err_logs[0]["level"] == "ERROR"

    # Filter by multiple comma-separated levels
    alert_logs = buf.get_logs(level="WARNING,ERROR")
    assert len(alert_logs) == 2

    # Filter by logger
    sup_logs = buf.get_logs(logger_name="supervisor")
    assert len(sup_logs) == 1
    assert "supervisor" in sup_logs[0]["logger"]

    # Filter by search query
    search_logs = buf.get_logs(search="query")
    assert len(search_logs) == 1
    assert search_logs[0]["level"] == "DEBUG"

    # Incremental polling with since_id
    second_id = logs[1]["id"]
    newer = buf.get_logs(since_id=second_id, reverse=False)
    assert len(newer) == 2
    assert newer[0]["id"] > second_id

    # Capacity overflow test
    for i in range(15):
        buf.append_manual("INFO", "osint.test", f"Overflow message {i}")
    assert len(buf.get_logs(limit=100)) == 10

    # Stats
    stats = buf.get_stats()
    assert stats["total_buffered"] == 10
    assert stats["max_capacity"] == 10
    assert "counts_by_level" in stats

    # Plaintext export
    exported = buf.export_text()
    assert "Overflow message" in exported

    # Clear
    buf.clear()
    assert len(buf.get_logs()) == 0


@pytest.mark.asyncio
async def test_server_logs_api_endpoints():
    settings = get_settings()
    api_key = settings.API_KEY

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Unauthorized access without API key
        resp = await client.get("/api/settings/logs")
        assert resp.status_code == 401

        # 2. Authorized access with X-API-Key
        headers = {"X-API-Key": api_key}
        resp = await client.get("/api/settings/logs", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "logs" in data
        assert "total" in data
        assert "stats" in data

        # 3. Emit a test log
        test_payload = {
            "level": "INFO",
            "message": "Operator integration verification log",
        }
        emit_resp = await client.post("/api/settings/logs/test", json=test_payload, headers=headers)
        assert emit_resp.status_code == 200
        emit_data = emit_resp.json()
        assert emit_data["status"] == "emitted"

        # 4. Fetch logs and verify emitted entry exists
        verify_resp = await client.get("/api/settings/logs?q=verification", headers=headers)
        assert verify_resp.status_code == 200
        verify_data = verify_resp.json()
        assert len(verify_data["logs"]) >= 1
        assert "Operator integration verification log" in verify_data["logs"][0]["message"]

        # 5. Fetch stats endpoint
        stats_resp = await client.get("/api/settings/logs/stats", headers=headers)
        assert stats_resp.status_code == 200
        stats_data = stats_resp.json()
        assert "total_buffered" in stats_data
        assert "counts_by_level" in stats_data

        # 6. Export text format
        export_text_resp = await client.get("/api/settings/logs/export?format=text", headers=headers)
        assert export_text_resp.status_code == 200
        assert "text/plain" in export_text_resp.headers.get("content-type", "")
        assert "Operator integration verification log" in export_text_resp.text

        # 7. Export json format
        export_json_resp = await client.get("/api/settings/logs/export?format=json", headers=headers)
        assert export_json_resp.status_code == 200
        assert "application/json" in export_json_resp.headers.get("content-type", "")
        assert "logs" in export_json_resp.json()

        # 8. Clear logs
        del_resp = await client.delete("/api/settings/logs", headers=headers)
        assert del_resp.status_code == 200
        del_data = del_resp.json()
        assert del_data["status"] == "cleared"
