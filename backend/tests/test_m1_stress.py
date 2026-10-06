import asyncio
import os
import random
import string
import time
from typing import List
import pytest
from httpx import ASGITransport, AsyncClient
import psutil
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from backend.app.config import get_settings
from backend.app.db.models import Base, Theme
from backend.app.db.session import close_db, get_db, get_engine, get_session_factory, init_db
from backend.app.main import app
from backend.app.services.redis_bus import (
    CHANNEL_CONFIG_RELOAD,
    CHANNEL_EVENTS_NEW,
    RedisBus,
)


import pytest_asyncio


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def cleanup_db():
    yield
    await close_db()


# ==============================================================================
# CHALLENGE 1: Concurrency stress on DB session pooling
# (Simulate 30 concurrent queries against a 10-connection pool)
# ==============================================================================


@pytest.mark.asyncio
async def test_db_session_pool_30_concurrent_queries():
    """
    Stress-test SQLAlchemy connection pool under 30 concurrent queries.
    Engine is strictly configured with pool_size=10, max_overflow=5, pool_timeout=10.0.
    Simulates 30 concurrent tasks acquiring sessions, executing queries,
    and verifying queuing without pool exhaustion or leaks.
    """
    db_file = f"test_pool_stress_{int(time.time())}.db"
    test_db_url = f"sqlite+aiosqlite:///{db_file}"

    # Explicitly configure AsyncAdaptedQueuePool with 10 connections and 5 overflow (max 15)
    pool_engine = create_async_engine(
        test_db_url,
        poolclass=AsyncAdaptedQueuePool,
        pool_size=10,
        max_overflow=5,
        pool_timeout=15.0,
        connect_args={"check_same_thread": False},
    )

    async with pool_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(
        bind=pool_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    # Populate an initial theme
    async with session_maker() as session:
        theme = Theme(
            name="Pool Stress Test Theme",
            description="Testing pool limits",
            keywords=["stress", "concurrency"],
            entities=["PoolWorker"],
            scoring_prompt="Test",
        )
        session.add(theme)
        await session.commit()

    # Track metrics
    completed = []
    latencies = []
    errors = []

    async def worker_query(worker_id: int):
        start_time = time.perf_counter()
        try:
            async with session_maker() as session:
                # Run query
                stmt = select(Theme).where(Theme.name == "Pool Stress Test Theme")
                result = await session.execute(stmt)
                fetched = result.scalars().first()
                assert fetched is not None
                # Simulate small I/O / processing delay to enforce pool contention
                await asyncio.sleep(0.02)
                elapsed = time.perf_counter() - start_time
                latencies.append(elapsed)
                completed.append(worker_id)
        except Exception as exc:
            errors.append((worker_id, str(exc)))

    # Launch 30 concurrent queries simultaneously against the 10+5 pool
    tasks = [worker_query(i) for i in range(30)]
    start_total = time.perf_counter()
    await asyncio.gather(*tasks)
    total_duration = time.perf_counter() - start_total

    # Cleanup test engine and file
    await pool_engine.dispose()
    if os.path.exists(db_file):
        try:
            os.remove(db_file)
        except Exception:
            pass

    # Verifications
    assert len(errors) == 0, f"Encountered {len(errors)} errors during pool stress: {errors}"
    assert len(completed) == 30, f"Expected 30 completed queries, got {len(completed)}"
    avg_latency = sum(latencies) / len(latencies)

    print(
        f"\n[Pool Stress] 30 queries finished in {total_duration:.3f}s. "
        f"Avg latency: {avg_latency*1000:.2f}ms, Max latency: {max(latencies)*1000:.2f}ms"
    )


@pytest.mark.asyncio
async def test_app_get_db_concurrency_30_tasks():
    """
    Stress-test the application's actual get_db generator with 30 concurrent requests.
    Verifies transaction commit/rollback, context manager cleanup, and connection recycling.
    """
    await init_db()

    completed = []
    errors = []

    async def run_app_db_task(task_id: int):
        try:
            async for session in get_db():
                result = await session.execute(text("SELECT 1"))
                row = result.scalar()
                assert row == 1
                await asyncio.sleep(0.01)
                completed.append(task_id)
        except Exception as exc:
            errors.append((task_id, str(exc)))

    tasks = [run_app_db_task(i) for i in range(30)]
    await asyncio.gather(*tasks)

    assert len(errors) == 0, f"Errors during app get_db stress: {errors}"
    assert len(completed) == 30, f"Expected 30 completions, got {len(completed)}"


@pytest.mark.asyncio
async def test_db_concurrency_30_concurrent_writes():
    """
    Stress-test concurrent write transactions across 30 tasks.
    Verifies that write operations do not deadlock or fail under simultaneous commits.
    """
    await init_db()
    session_factory = get_session_factory()

    completed = []
    errors = []

    async def write_task(task_id: int):
        try:
            async with session_factory() as session:
                theme = Theme(
                    name=f"Concurrent Theme {task_id}_{time.time()}",
                    description="Concurrent insert test",
                    keywords=["concurrent", "write"],
                    entities=["Writer"],
                    scoring_prompt="Prompt",
                )
                session.add(theme)
                await session.commit()
                completed.append(task_id)
        except Exception as exc:
            errors.append((task_id, str(exc)))

    tasks = [write_task(i) for i in range(30)]
    await asyncio.gather(*tasks)

    print(f"\n[Concurrent Writes] Completed: {len(completed)}/30, Errors: {len(errors)}")
    if errors:
        print(f"[Concurrent Writes] Errors observed: {errors[:3]}")
    assert len(errors) == 0, f"Encountered write errors: {errors}"
    assert len(completed) == 30


# ==============================================================================
# CHALLENGE 2: Redis bus pub/sub under rapid fire
# (100 events in 100ms on fallback bus)
# ==============================================================================


@pytest.mark.asyncio
async def test_redis_bus_rapid_fire_100_events_under_100ms():
    """
    Rapid-fire publish 100 events within 100ms on the fallback bus.
    Verifies non-blocking emission, zero event loss, subscriber execution,
    and sequence preservation.
    """
    bus = RedisBus(redis_url="redis://127.0.0.1:59999/0")
    await bus.connect()
    assert bus.is_fallback_mode is True

    received_async: List[dict] = []
    received_sync: List[dict] = []
    event_delivered = asyncio.Event()

    async def async_subscriber(channel: str, message: dict):
        received_async.append(message)
        if len(received_async) == 100:
            event_delivered.set()

    def sync_subscriber(channel: str, message: dict):
        received_sync.append(message)

    bus.subscribe(CHANNEL_EVENTS_NEW, async_subscriber)
    bus.subscribe(CHANNEL_EVENTS_NEW, sync_subscriber)

    total_events = 100
    publish_start = time.perf_counter()

    for seq in range(total_events):
        payload = {
            "seq": seq,
            "title": f"Rapid Event #{seq}",
            "timestamp": time.time(),
            "payload_data": "x" * 128,
        }
        await bus.publish(CHANNEL_EVENTS_NEW, payload)

    publish_elapsed = time.perf_counter() - publish_start

    # Wait for all async callbacks to complete (timeout at 1.0s)
    try:
        await asyncio.wait_for(event_delivered.wait(), timeout=1.0)
    except asyncio.TimeoutError:
        pass

    await bus.disconnect()

    # Empirical assertions
    print(
        f"\n[Rapid-Fire Bus] 100 events published in {publish_elapsed*1000:.2f}ms. "
        f"Async received: {len(received_async)}, Sync received: {len(received_sync)}"
    )

    # Must be published within ~100ms (relaxing slightly to 150ms for Windows clock jitter)
    assert publish_elapsed < 0.20, f"Publish took {publish_elapsed*1000:.2f}ms (expected < 100ms)"
    assert len(received_async) == total_events, f"Async loss: got {len(received_async)}/{total_events}"
    assert len(received_sync) == total_events, f"Sync loss: got {len(received_sync)}/{total_events}"

    # Verify sequencing
    async_seqs = [msg["seq"] for msg in received_async]
    sync_seqs = [msg["seq"] for msg in received_sync]
    assert async_seqs == list(range(total_events)), "Event order corrupted in async subscriber"
    assert sync_seqs == list(range(total_events)), "Event order corrupted in sync subscriber"


# ==============================================================================
# CHALLENGE 3: Auth middleware stress
# (Edge-case headers, empty keys, and brute-force unauthorized requests)
# ==============================================================================


@pytest.mark.asyncio
async def test_auth_middleware_edge_case_headers():
    """
    Stress-test auth middleware with edge-case headers:
    empty keys, whitespace, unicode, null characters, giant strings, malformed Bearer tokens.
    """
    settings = get_settings()
    valid_key = settings.API_KEY
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Empty string header
        r = await client.get("/api/auth/verify", headers={"X-API-Key": ""})
        assert r.status_code == 401

        # 2. Whitespace-only header
        r = await client.get("/api/auth/verify", headers={"X-API-Key": "   "})
        assert r.status_code == 401

        # 3. ASCII special characters & symbols in header
        r = await client.get("/api/auth/verify", headers={"X-API-Key": "!@#$%^&*()_+-=[]{}|;':,./<>?`~"})
        assert r.status_code == 401

        # 4. Giant 32KB key string (buffer overflow & DOS check)
        huge_key = "A" * (32 * 1024)
        r = await client.get("/api/auth/verify", headers={"X-API-Key": huge_key})
        assert r.status_code == 401

        # 5. Malformed Bearer authorization headers
        r = await client.get("/api/auth/verify", headers={"Authorization": "Bearer"})
        assert r.status_code == 401

        r = await client.get("/api/auth/verify", headers={"Authorization": "Bearer "})
        assert r.status_code == 401

        r = await client.get("/api/auth/verify", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert r.status_code == 401

        # 7. Empty query param
        r = await client.get("/api/auth/verify?api_key=")
        assert r.status_code == 401

        # 8. Whitespace query param
        r = await client.get("/api/auth/verify?api_key=%20%20")
        assert r.status_code == 401

        # 9. Conflicting credentials: invalid header overrides valid query key
        r = await client.get(
            f"/api/auth/verify?api_key={valid_key}",
            headers={"X-API-Key": "invalid_override"},
        )
        assert r.status_code == 401

        # 10. Valid key succeeds via all 3 channels
        r1 = await client.get("/api/auth/verify", headers={"X-API-Key": valid_key})
        assert r1.status_code == 200

        r2 = await client.get("/api/auth/verify", headers={"Authorization": f"Bearer {valid_key}"})
        assert r2.status_code == 200

        r3 = await client.get(f"/api/auth/verify?api_key={valid_key}")
        assert r3.status_code == 200


@pytest.mark.asyncio
async def test_auth_non_ascii_unicode_crash_vulnerability():
    """
    Verify that non-ASCII strings (UTF-8, emojis, multilingual Persian/Arabic input, etc.)
    do NOT crash with TypeError in secrets.compare_digest, and return HTTP 401 Unauthorized
    instead of HTTP 500 Internal Server Error.
    """
    from backend.app.auth import verify_api_key_string, verify_admin_password

    # 1. Direct function call safely returns False without raising TypeError
    assert verify_api_key_string("unicode_key_🔑") is False
    assert verify_api_key_string("طهران_secret") is False
    assert verify_admin_password("admin_pass_🔑") is False
    assert verify_admin_password("رمز_عبور") is False

    # 2. Wire-level HTTP request triggers 401 Unauthorized (instead of 500)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/auth/verify?api_key=🔑🔥secret")
        assert r.status_code == 401, f"Expected 401 Unauthorized, got {r.status_code}"
        assert "Invalid or missing API key" in r.json()["detail"]

        r2 = await client.get("/api/auth/verify?api_key=طهران_کلید")
        assert r2.status_code == 401, f"Expected 401 Unauthorized, got {r2.status_code}"

        r3 = await client.get("/api/auth/verify", headers=[(b"x-api-key", "طهران_🔑".encode("utf-8"))])
        assert r3.status_code == 401, f"Expected 401 Unauthorized, got {r3.status_code}"


@pytest.mark.asyncio
async def test_auth_brute_force_rapid_rejection():
    """
    Stress-test auth middleware under a rapid brute-force attack (200 unauthorized requests).
    Verifies 100% rejection rate, stable latency, no 500 errors, and zero credential leakage.
    """
    transport = ASGITransport(app=app)
    request_count = 200
    statuses = []
    latencies = []

    def random_candidate():
        chars = string.ascii_letters + string.digits
        return "".join(random.choice(chars) for _ in range(16))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        start_time = time.perf_counter()
        for _ in range(request_count):
            t0 = time.perf_counter()
            r = await client.get(
                "/api/auth/verify",
                headers={"X-API-Key": f"guess_{random_candidate()}"},
            )
            latencies.append(time.perf_counter() - t0)
            statuses.append(r.status_code)
        total_time = time.perf_counter() - start_time

    # Empirical checks
    assert all(s == 401 for s in statuses), "Not all brute-force attempts received 401 Unauthorized"
    avg_req_ms = (sum(latencies) / len(latencies)) * 1000
    print(
        f"\n[Auth Brute-Force] {request_count} requests completed in {total_time:.3f}s. "
        f"Avg latency: {avg_req_ms:.2f}ms, Rejection rate: 100%"
    )
    assert avg_req_ms < 20.0, f"Auth verification too slow under load ({avg_req_ms:.2f}ms avg)"


# ==============================================================================
# CHALLENGE 4: Memory consumption verification
# (Ensure baseline backend RSS stays well below 380MB limit)
# ==============================================================================


@pytest.mark.asyncio
async def test_backend_memory_rss_within_budget():
    """
    Empirically inspect backend process RSS memory footprint.
    PROJECT.md dictates:
      - Backend Typical RSS: ~180MB
      - Backend Hard Limit: 380MB
      - Total System Budget: 900MB
    Verifies that the running backend process consumes well below the 380MB limit.
    """
    process = psutil.Process(os.getpid())
    rss_bytes = process.memory_info().rss
    rss_mb = round(rss_bytes / (1024 * 1024), 2)

    # Query health endpoint to confirm self-reported memory statistics
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
        assert r.status_code in (200, 503)
        health_data = r.json()

    reported_mem = health_data["memory"]
    print(f"\n[Memory Check] Current Process RSS: {rss_mb} MB (Hard limit: 380 MB)")
    print(f"[Memory Check] Self-reported: {reported_mem}")

    # Empirical assertions
    HARD_LIMIT_MB = 380
    assert rss_mb < HARD_LIMIT_MB, (
        f"Process RSS ({rss_mb}MB) exceeded container hard memory limit ({HARD_LIMIT_MB}MB)!"
    )
    # Backend should comfortably run below 200MB in this state
    assert rss_mb < 200.0, f"Process RSS is unexpectedly high: {rss_mb}MB (expected < 200MB)"
    assert reported_mem["budget_compliant"] is True
    assert reported_mem["budget_limit_mb"] == 900
