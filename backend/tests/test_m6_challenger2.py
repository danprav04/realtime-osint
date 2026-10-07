"""
backend/tests/test_m6_challenger2.py - Milestone 6 Adversarial Challenge & Stress Test Suite

Adversarially challenges:
1. Structured JSON Logging:
   - JSON schema compliance: timestamp, level, logger, message, environment.
   - JSON serialization resilience with special characters, unicode, trace IDs, and exception tracebacks.
   - Non-serializable object fallback via default=str.
2. Request Tracing & Correlation:
   - RequestTracingMiddleware behavior with existing X-Request-ID.
   - Fallback to X-Trace-ID when X-Request-ID is absent.
   - Precedence of X-Request-ID over X-Trace-ID.
   - Missing trace header generates valid UUID4.
   - ContextVar cleanup ensuring zero trace ID leakage across requests.
   - Concurrency isolation with 30 simultaneous requests.
3. Health & Readiness Probe Endpoints:
   - Endpoint coverage: /health, /api/health, /ready, /live, /health/ready, /health/live.
   - Top-level "memory_mb" presence and numeric value <= 900.
   - Nested "memory" budget metrics compliance.
   - Liveness probe resilience during total infrastructure failure.
4. Simulated Degradation & Failure Modes:
   - Redis degraded (in-memory fallback) -> /health 200 (degraded), /ready 200 (ready).
   - Redis unhealthy -> /health 503, /ready 503.
   - Database unhealthy -> /health 503, /ready 503.
   - Both DB & Redis unhealthy -> /health 503, /ready 503, /live 200 (alive).
5. High Concurrency Burst:
   - 50 concurrent requests across all probes with zero errors and fast response.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.app.config import get_settings
from backend.app.db.session import close_db, init_db
from backend.app.main import JSONLogFormatter, RequestTracingMiddleware, app, trace_id_ctx
from backend.app.services.redis_bus import RedisBus, get_redis_bus


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_db()
    yield
    await close_db()


# ==============================================================================
# SECTION 1: STRUCTURED JSON LOGGING SCHEMA & RESILIENCE
# ==============================================================================

def test_json_formatter_schema_compliance():
    """
    Verify log record JSON schema strictly contains:
    timestamp, level, logger, message, environment.
    """
    formatter = JSONLogFormatter()
    record = logging.LogRecord(
        name="test.schema",
        level=logging.INFO,
        pathname="test.py",
        lineno=42,
        msg="Structured log verification event",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    data = json.loads(output)

    assert "timestamp" in data
    assert "level" in data
    assert "logger" in data
    assert "message" in data
    assert "environment" in data

    assert data["level"] == "INFO"
    assert data["logger"] == "test.schema"
    assert data["message"] == "Structured log verification event"
    assert data["environment"] == os.getenv("ENVIRONMENT", "production")

    # Timestamp must parse as valid ISO-8601 UTC
    dt = datetime.fromisoformat(data["timestamp"])
    assert dt.tzinfo is not None


def test_json_formatter_levels_and_warning_enrichment():
    """
    Verify that WARNING and ERROR levels include source_file, line_number, and function,
    while INFO levels do not leak unnecessary stack metadata.
    """
    formatter = JSONLogFormatter()

    # INFO record
    info_rec = logging.LogRecord(
        name="test.info",
        level=logging.INFO,
        pathname="path/to/source.py",
        lineno=101,
        msg="Informational message",
        args=(),
        exc_info=None,
    )
    info_rec.funcName = "do_something"
    info_data = json.loads(formatter.format(info_rec))
    assert "source_file" not in info_data
    assert "line_number" not in info_data
    assert "function" not in info_data

    # WARNING record
    warn_rec = logging.LogRecord(
        name="test.warn",
        level=logging.WARNING,
        pathname="path/to/source.py",
        lineno=102,
        msg="Warning message",
        args=(),
        exc_info=None,
    )
    warn_rec.funcName = "warn_user"
    warn_data = json.loads(formatter.format(warn_rec))
    assert warn_data["source_file"] == "source.py"
    assert warn_data["line_number"] == 102
    assert warn_data["function"] == "warn_user"

    # ERROR record
    err_rec = logging.LogRecord(
        name="test.error",
        level=logging.ERROR,
        pathname="path/to/critical.py",
        lineno=205,
        msg="Error condition detected",
        args=(),
        exc_info=None,
    )
    err_rec.funcName = "handle_failure"
    err_data = json.loads(formatter.format(err_rec))
    assert err_data["source_file"] == "critical.py"
    assert err_data["line_number"] == 205
    assert err_data["function"] == "handle_failure"


def test_json_formatter_serialization_resilience_unicode_and_specials():
    """
    Adversarially test JSON serialization with Persian/Arabic script, Chinese,
    Cyrillic, emojis, unescaped quotes, null bytes, backslashes, and multiline text.
    """
    formatter = JSONLogFormatter()

    adversarial_messages = [
        "Persian/Arabic: نیروهای دریایی سپاه یک فروند نفتکش را توقیف کردند - تنگه هرمز",
        "Chinese/Cyrillic: 实时开源情报监测 - перехват тактических данных",
        "Emojis & Symbols: 🚨⚠️💥🚢⚓🎯 [CRITICAL_PRIORITY]",
        'Quotes & Escapes: "quoted" and \'single\' and \\n \\t \\r \\\\ and { "nested": "fake_json" }',
        "Multiline:\nLine 1\nLine 2\nLine 3\n\tTabbed Indent",
        "Null byte resilience: test\x00character\x1b[31mescape\x1b[0m",
    ]

    for raw_msg in adversarial_messages:
        record = logging.LogRecord(
            name="test.adversarial_text",
            level=logging.INFO,
            pathname="adv.py",
            lineno=1,
            msg=raw_msg,
            args=(),
            exc_info=None,
        )
        formatted_json = formatter.format(record)
        assert isinstance(formatted_json, str)

        # Must parse as valid JSON
        parsed = json.loads(formatted_json)
        # Message content must match raw message
        assert parsed["message"] == raw_msg


def test_json_formatter_exception_traceback_capture():
    """
    Verify that when an exception occurs, exc_info is formatted and captured
    in the 'exception' JSON field with complete traceback information.
    """
    formatter = JSONLogFormatter()

    try:
        raise ValueError("Simulated catastrophic division or pipeline error: 0xDEADBEEF")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="test.exception",
        level=logging.ERROR,
        pathname="fail.py",
        lineno=99,
        msg="Failure during AI inference",
        args=(),
        exc_info=exc_info,
    )
    data = json.loads(formatter.format(record))

    assert "exception" in data
    assert "ValueError: Simulated catastrophic division" in data["exception"]
    assert "Traceback (most recent call last):" in data["exception"]


def test_json_formatter_non_serializable_fallback():
    """
    Verify default=str serializes non-standard objects without raising TypeError.
    """
    formatter = JSONLogFormatter()

    class CustomObject:
        def __str__(self):
            return "<CustomUnserializableObject id=42>"

    record = logging.LogRecord(
        name="test.unserializable",
        level=logging.INFO,
        pathname="obj.py",
        lineno=10,
        msg="Object serialization test",
        args=(),
        exc_info=None,
    )
    record.extra_data = {
        "complex_object": CustomObject(),
        "custom_set": {1, 2, 3},
        "byte_val": b"binary_data",
        "uuid_val": uuid.uuid4(),
        "timestamp_obj": datetime.now(timezone.utc),
    }

    formatted = formatter.format(record)
    parsed = json.loads(formatted)

    assert "<CustomUnserializableObject id=42>" in parsed["complex_object"]
    assert parsed["byte_val"] == "b'binary_data'"
    assert isinstance(parsed["uuid_val"], str)


# ==============================================================================
# SECTION 2: REQUEST TRACING MIDDLEWARE & TRACE ID CORRELATION
# ==============================================================================

@pytest.mark.asyncio
async def test_tracing_with_existing_x_request_id():
    """
    Verify client-supplied X-Request-ID is honored, returned in response header,
    and bound to trace_id_ctx during request execution.
    """
    custom_trace = f"client-req-{uuid.uuid4()}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/", headers={"X-Request-ID": custom_trace})
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == custom_trace


@pytest.mark.asyncio
async def test_tracing_with_fallback_to_x_trace_id():
    """
    Verify that when X-Request-ID is absent but X-Trace-ID is provided,
    the middleware extracts X-Trace-ID and maps it to X-Request-ID response header.
    """
    custom_trace = f"datadog-trace-{uuid.uuid4()}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/", headers={"X-Trace-ID": custom_trace})
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == custom_trace


@pytest.mark.asyncio
async def test_tracing_precedence_x_request_id_over_x_trace_id():
    """
    Verify precedence: when BOTH X-Request-ID and X-Trace-ID are present,
    X-Request-ID takes priority.
    """
    req_id = "req-id-priority"
    trace_id = "trace-id-secondary"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/",
            headers={"X-Request-ID": req_id, "X-Trace-ID": trace_id},
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Request-ID") == req_id


@pytest.mark.asyncio
async def test_tracing_missing_header_generates_valid_uuid4():
    """
    Verify that when no trace headers are supplied, a valid UUID4 is generated
    and injected into response headers.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        generated_id = resp.headers.get("X-Request-ID")
        assert generated_id is not None
        # Verify valid UUID4
        parsed_uuid = uuid.UUID(generated_id, version=4)
        assert str(parsed_uuid) == generated_id


@pytest.mark.asyncio
async def test_tracing_context_cleanup_prevents_leakage():
    """
    Verify trace_id_ctx resets cleanly after request execution and does not leak.
    """
    assert trace_id_ctx.get() is None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/", headers={"X-Request-ID": "test-leak-probe"})
        assert resp.status_code == 200

    # Ensure after request finishes, ContextVar returns to None
    assert trace_id_ctx.get() is None


@pytest.mark.asyncio
async def test_tracing_concurrency_isolation_30_requests():
    """
    Adversarial Concurrency Stress Test:
    Fires 30 simultaneous requests with distinct trace IDs concurrently.
    Verifies that every single response header matches its corresponding request header
    with 100% correlation fidelity and zero crosstalk.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        trace_ids = [f"concurrent-trace-{i}-{uuid.uuid4()}" for i in range(30)]

        async def fetch(t_id: str):
            res = await client.get("/", headers={"X-Request-ID": t_id})
            return t_id, res.headers.get("X-Request-ID")

        results = await asyncio.gather(*(fetch(t) for t in trace_ids))

        for sent_id, received_id in results:
            assert sent_id == received_id, f"Trace ID mismatch! Sent {sent_id}, got {received_id}"


# ==============================================================================
# SECTION 3: HEALTH PROBE ENDPOINTS & MEMORY METRICS VERIFICATION
# ==============================================================================

@pytest.mark.asyncio
async def test_all_health_probe_endpoints_exist_and_respond():
    """
    Verify that all standard probe endpoints respond with 200:
    - /health
    - /api/health
    - /ready
    - /health/ready
    - /live
    - /health/live
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        endpoints = [
            "/health",
            "/api/health",
            "/ready",
            "/health/ready",
            "/live",
            "/health/live",
        ]
        for ep in endpoints:
            resp = await client.get(ep)
            assert resp.status_code == 200, f"Endpoint {ep} failed with {resp.status_code}"


@pytest.mark.asyncio
async def test_health_payload_top_level_memory_mb_and_budget_cap():
    """
    Verify that /health and /api/health contain top-level 'memory_mb' <= 900.0,
    and that nested 'memory' metrics conform to expected schema.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for ep in ("/health", "/api/health"):
            resp = await client.get(ep)
            assert resp.status_code == 200
            data = resp.json()

            # Top-level memory_mb verification
            assert "memory_mb" in data, f"Missing top-level 'memory_mb' in {ep}"
            assert isinstance(data["memory_mb"], (int, float))
            assert data["memory_mb"] <= 900.0, f"Memory {data['memory_mb']}MB exceeded 900MB limit!"

            # Nested memory details verification
            assert "memory" in data
            mem = data["memory"]
            assert "budget_limit_mb" in mem
            assert "budget_compliant" in mem
            assert mem["budget_compliant"] is True
            assert mem["budget_limit_mb"] <= 900


@pytest.mark.asyncio
async def test_liveness_probes_format_and_timestamp():
    """
    Verify /live and /health/live return {status: 'alive', timestamp: '<iso>'}
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for ep in ("/live", "/health/live"):
            resp = await client.get(ep)
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "alive"
            assert "timestamp" in data
            dt = datetime.fromisoformat(data["timestamp"])
            assert dt.tzinfo is not None


# ==============================================================================
# SECTION 4: SIMULATED DEGRADED & OFFLINE STATES
# ==============================================================================

@pytest.mark.asyncio
async def test_degraded_redis_behavior(monkeypatch):
    """
    Simulate Redis in 'degraded' state (in-memory pub/sub fallback mode):
    - /health overall status must be 'degraded' with HTTP 200 (system continues operating).
    - /ready and /health/ready must return HTTP 200 with ready=True.
    """
    async def mock_degraded_redis(self):
        return {
            "status": "degraded",
            "is_connected": False,
            "fallback_mode": True,
            "note": "Using in-memory pub/sub bus",
            "subscribed_channels": ["osint:events:new"],
        }

    monkeypatch.setattr(RedisBus, "check_health", mock_degraded_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Check /health
        h_resp = await client.get("/health")
        assert h_resp.status_code == 200
        h_data = h_resp.json()
        assert h_data["status"] == "degraded"
        assert h_data["redis"]["status"] == "degraded"

        # Check /ready
        r_resp = await client.get("/ready")
        assert r_resp.status_code == 200
        r_data = r_resp.json()
        assert r_data["ready"] is True
        assert r_data["redis"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_unhealthy_redis_behavior(monkeypatch):
    """
    Simulate Redis in 'unhealthy' state:
    - /health must return HTTP 503 Service Unavailable with overall status 'unhealthy'.
    - /ready and /health/ready must return HTTP 503 Service Unavailable.
    """
    async def mock_unhealthy_redis(self):
        return {
            "status": "unhealthy",
            "is_connected": False,
            "fallback_mode": False,
            "error": "Connection refused to redis:6379",
            "subscribed_channels": [],
        }

    monkeypatch.setattr(RedisBus, "check_health", mock_unhealthy_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # /health -> 503
        h_resp = await client.get("/health")
        assert h_resp.status_code == 503
        assert h_resp.json()["status"] == "unhealthy"

        # /api/health -> 503
        api_h_resp = await client.get("/api/health")
        assert api_h_resp.status_code == 503
        assert api_h_resp.json()["status"] == "unhealthy"

        # /ready -> 503
        r_resp = await client.get("/ready")
        assert r_resp.status_code == 503
        assert r_resp.json()["detail"] == "Service not ready"

        # /health/ready -> 503
        hr_resp = await client.get("/health/ready")
        assert hr_resp.status_code == 503


@pytest.mark.asyncio
async def test_unhealthy_database_behavior(monkeypatch):
    """
    Simulate Database connection failure:
    - /health must return HTTP 503 with status 'unhealthy'.
    - /ready must return HTTP 503.
    """
    async def mock_unhealthy_db():
        return {
            "status": "unhealthy",
            "dialect": "postgresql",
            "error": "psycopg2.OperationalError: could not connect to server",
            "latency_ms": 2500.0,
        }

    monkeypatch.setattr("backend.app.api.health.check_db_health", mock_unhealthy_db)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        h_resp = await client.get("/health")
        assert h_resp.status_code == 503
        assert h_resp.json()["status"] == "unhealthy"

        r_resp = await client.get("/ready")
        assert r_resp.status_code == 503
        assert r_resp.json()["detail"] == "Service not ready"


@pytest.mark.asyncio
async def test_total_infrastructure_failure_liveness_survives(monkeypatch):
    """
    Adversarial Resilience Test:
    When BOTH Database and Redis suffer catastrophic fatal outages,
    verify that:
    1. /health and /ready return HTTP 503.
    2. /live and /health/live STILL return HTTP 200 OK (liveness probe does not
       trigger premature pod kill loops while dependencies recover).
    """
    async def mock_unhealthy_db():
        return {"status": "unhealthy", "error": "Fatal DB Crash"}

    async def mock_unhealthy_redis(self):
        return {"status": "unhealthy", "error": "Fatal Redis OOM"}

    monkeypatch.setattr("backend.app.api.health.check_db_health", mock_unhealthy_db)
    monkeypatch.setattr(RedisBus, "check_health", mock_unhealthy_redis)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # /health and /ready fail as expected
        assert (await client.get("/health")).status_code == 503
        assert (await client.get("/ready")).status_code == 503

        # LIVENESS PROBES MUST SURVIVE!
        live_resp = await client.get("/live")
        assert live_resp.status_code == 200
        assert live_resp.json()["status"] == "alive"

        hlive_resp = await client.get("/health/live")
        assert hlive_resp.status_code == 200
        assert hlive_resp.json()["status"] == "alive"


# ==============================================================================
# SECTION 5: HIGH CONCURRENCY BURST ON PROBES
# ==============================================================================

@pytest.mark.asyncio
async def test_health_probes_concurrent_burst():
    """
    Stress-test health probe endpoints under a burst of 50 concurrent requests.
    Verifies that health checks do not block or deadlock under simultaneous polling.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        endpoints = [
            "/health",
            "/api/health",
            "/ready",
            "/live",
            "/health/live",
        ]
        start_time = time.perf_counter()

        tasks = [client.get(endpoints[i % len(endpoints)]) for i in range(50)]
        responses = await asyncio.gather(*tasks)

        elapsed = time.perf_counter() - start_time
        assert elapsed < 3.0, f"Burst of 50 health requests took too long: {elapsed:.2f}s"

        for resp in responses:
            assert resp.status_code == 200


# ==============================================================================
# SECTION 6: END-TO-END JSON LOG CAPTURE & TRACE ID EMISSION
# ==============================================================================

@pytest.mark.asyncio
async def test_request_tracing_log_emission_contains_trace_id_and_metrics():
    """
    Adversarial verification of end-to-end log emission:
    Attaches a capturing handler with JSONLogFormatter to the root logger.
    Executes a request with a custom X-Request-ID.
    Verifies that the emitted access log record contains:
    - trace_id matching X-Request-ID
    - http_method == 'GET'
    - http_path == '/'
    - status_code == 200
    - duration_ms >= 0
    """
    import io

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(JSONLogFormatter())
    
    test_logger = logging.getLogger("osint.main")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.INFO)

    custom_trace = f"audit-trace-{uuid.uuid4()}"

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/", headers={"X-Request-ID": custom_trace})
            assert resp.status_code == 200
            assert resp.headers.get("X-Request-ID") == custom_trace

        log_output = log_stream.getvalue()
        assert log_output, "No logs were emitted during request!"

        # Parse every JSON line
        found_target_log = False
        for line in log_output.strip().split("\n"):
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("trace_id") == custom_trace:
                found_target_log = True
                assert entry["http_method"] == "GET"
                assert entry["http_path"] == "/"
                assert entry["status_code"] == 200
                assert isinstance(entry["duration_ms"], (int, float))
                assert entry["level"] == "INFO"
                assert entry["logger"] == "osint.main"

        assert found_target_log, f"Emitted logs did not contain trace_id={custom_trace}:\n{log_output}"
    finally:
        test_logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_application_internal_log_inherits_request_trace_id():
    """
    Adversarially verify that any internal logger called inside a request handler
    automatically inherits the ambient request trace_id without manual passing.
    """
    import io

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(JSONLogFormatter())

    app_logger = logging.getLogger("backend.app.internal")
    app_logger.addHandler(handler)
    app_logger.setLevel(logging.INFO)

    req_trace = f"internal-correlate-{uuid.uuid4()}"

    # Define a temporary route on the app to test inner logging
    @app.get("/test/challenger-inner-log")
    async def temp_inner_endpoint():
        app_logger.info("Executing sensitive internal calculation")
        return {"ok": True}

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/test/challenger-inner-log", headers={"X-Request-ID": req_trace})
            assert resp.status_code == 200

        log_output = log_stream.getvalue()
        entries = [json.loads(line) for line in log_output.strip().split("\n") if line.strip()]
        
        inner_entries = [e for e in entries if e.get("message") == "Executing sensitive internal calculation"]
        assert len(inner_entries) == 1
        assert inner_entries[0]["trace_id"] == req_trace, (
            f"Internal log failed to inherit trace_id! Got: {inner_entries[0].get('trace_id')}"
        )
    finally:
        app_logger.removeHandler(handler)

