"""
backend/tests/test_m6_challenger.py - Milestone 6 Adversarial Verification & Container Benchmark

Adversarial Stress & Verification Suite for:
1. Docker Compose Configuration & YAML Schema:
   - Programmatic YAML validation of services, volumes, networks
   - Strict memory limits (<900MB total budget, 830MB target allocation)
   - Reservation bounds verification (reservations <= limits)
   - Port conflict detection (5432, 6379, 8000, 3000)
   - Healthcheck timeout & interval invariants (timeout < interval)
   - Cascading dependency graph & lifecycle constraints
2. Postgres & Redis Low-Memory Tuning Verification:
   - PostgreSQL shared_buffers <= 32MB, max_connections <= 20, work_mem <= 2MB
   - Redis maxmemory <= 32mb, allkeys-lru eviction, save "" (no fork COW)
3. Multi-Stage Dockerfile Security & Architecture:
   - Non-root user validation (appuser UID 10001, nextjs UID 1001)
   - Signal handling via dumb-init for PID 1 graceful shutdown
   - V8 heap limits (NODE_OPTIONS="--max-old-space-size=180")
   - Multi-stage target hygiene and .dockerignore coverage
4. Runtime Observability & Logging Invariants:
   - JSON structured logging with trace ID correlation
   - Health probe contracts (/health, /api/health, /ready, /live)
"""

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
BACKEND_DOCKERFILE = REPO_ROOT / "backend" / "Dockerfile"
FRONTEND_DOCKERFILE = REPO_ROOT / "frontend" / "Dockerfile"
ROOT_DOCKERIGNORE = REPO_ROOT / ".dockerignore"
BACKEND_DOCKERIGNORE = REPO_ROOT / "backend" / ".dockerignore"
FRONTEND_DOCKERIGNORE = REPO_ROOT / "frontend" / ".dockerignore"


def parse_memory_to_bytes(mem_str: str) -> int:
    """Parse memory strings like '150M', '32mb', '1G' into bytes."""
    mem_str = str(mem_str).strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)?$", mem_str)
    if not match:
        raise ValueError(f"Unable to parse memory string: {mem_str}")
    val = float(match.group(1))
    unit = (match.group(2) or "b").upper()
    if unit in ("B", "BYTES"):
        return int(val)
    elif unit in ("K", "KB", "KIB"):
        return int(val * 1024)
    elif unit in ("M", "MB", "MIB"):
        return int(val * 1024 * 1024)
    elif unit in ("G", "GB", "GIB"):
        return int(val * 1024 * 1024 * 1024)
    else:
        raise ValueError(f"Unknown unit: {unit} in {mem_str}")


def load_compose_dict() -> Dict[str, Any]:
    assert COMPOSE_FILE.exists(), f"docker-compose.yml missing at {COMPOSE_FILE}"
    with open(COMPOSE_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), "docker-compose.yml must be a valid YAML dictionary"
    return data


# ==============================================================================
# 1. DOCKER COMPOSE CONFIGURATION & MEMORY BUDGET
# ==============================================================================

def test_compose_structure_and_required_services():
    """Verify docker-compose.yml has valid top-level schema and all 4 microservices."""
    compose = load_compose_dict()
    assert "services" in compose, "Compose must have 'services' key"
    assert "networks" in compose, "Compose must declare isolated 'networks'"
    assert "volumes" in compose, "Compose must declare 'volumes'"

    services = compose["services"]
    required_services = {"postgres", "redis", "backend", "frontend"}
    assert set(services.keys()) == required_services, (
        f"Expected services {required_services}, got {set(services.keys())}"
    )


def test_memory_limits_budget_under_900mb():
    """Verify every service has limits, reservations <= limits, and total memory <= 900MB."""
    compose = load_compose_dict()
    services = compose["services"]

    total_limit_bytes = 0
    total_res_bytes = 0
    service_limits = {}
    service_reservations = {}

    for name, svc in services.items():
        assert "deploy" in svc, f"Service {name} lacks 'deploy' section"
        resources = svc["deploy"].get("resources", {})
        assert "limits" in resources, f"Service {name} lacks 'resources.limits'"
        assert "memory" in resources["limits"], f"Service {name} lacks 'limits.memory'"
        assert "reservations" in resources, f"Service {name} lacks 'resources.reservations'"
        assert "memory" in resources["reservations"], f"Service {name} lacks 'reservations.memory'"

        limit_bytes = parse_memory_to_bytes(resources["limits"]["memory"])
        res_bytes = parse_memory_to_bytes(resources["reservations"]["memory"])

        # Invariant: reservations must never exceed limits
        assert res_bytes <= limit_bytes, (
            f"Service {name} reservation ({res_bytes}) exceeds limit ({limit_bytes})"
        )

        service_limits[name] = limit_bytes / (1024 * 1024)
        service_reservations[name] = res_bytes / (1024 * 1024)
        total_limit_bytes += limit_bytes
        total_res_bytes += res_bytes

    total_limit_mb = total_limit_bytes / (1024 * 1024)
    total_res_mb = total_res_bytes / (1024 * 1024)

    # Specific budgeted limits per PROJECT.md architecture
    assert service_limits["postgres"] <= 150.0, f"Postgres limit too high: {service_limits['postgres']}MB"
    assert service_limits["redis"] <= 80.0, f"Redis limit too high: {service_limits['redis']}MB"
    assert service_limits["backend"] <= 380.0, f"Backend limit too high: {service_limits['backend']}MB"
    assert service_limits["frontend"] <= 220.0, f"Frontend limit too high: {service_limits['frontend']}MB"

    # Total must strictly satisfy <900MB cap
    assert total_limit_mb <= 900.0, f"Total memory limit ({total_limit_mb}MB) exceeds 900MB budget!"
    assert total_limit_mb == 830.0, f"Expected exactly 830MB target allocation, got {total_limit_mb}MB"
    assert total_res_mb == 346.0, f"Expected exactly 346MB total reservation, got {total_res_mb}MB"

    # Host buffer on 1GB (1024MB) VPS must be >= 120MB
    host_headroom_mb = 1024.0 - total_limit_mb
    assert host_headroom_mb >= 120.0, f"Host headroom too small: {host_headroom_mb}MB"


def test_port_bindings_collision_free():
    """Verify host port bindings do not conflict and match standard specifications."""
    compose = load_compose_dict()
    services = compose["services"]

    published_ports = []
    expected_ports = {
        "postgres": 5432,
        "redis": 6379,
        "backend": 8000,
        "frontend": 3000,
    }

    for name, svc in services.items():
        ports = svc.get("ports", [])
        assert ports, f"Service {name} should expose a port mapping"
        for p in ports:
            # Format "host:container"
            host_port = int(str(p).split(":")[0])
            container_port = int(str(p).split(":")[1])
            published_ports.append(host_port)
            assert host_port == expected_ports[name], (
                f"Service {name} published port {host_port} != expected {expected_ports[name]}"
            )
            assert container_port == host_port, (
                f"Service {name} host port {host_port} does not match container port {container_port}"
            )

    # Assert no collisions across any published ports
    assert len(published_ports) == len(set(published_ports)), (
        f"Port collision detected in: {published_ports}"
    )


def test_healthcheck_intervals_and_timeouts():
    """Verify healthcheck timeout < interval, retries >= 3, and valid probe syntax."""
    compose = load_compose_dict()
    services = compose["services"]

    for name, svc in services.items():
        assert "healthcheck" in svc, f"Service {name} lacks healthcheck"
        hc = svc["healthcheck"]

        assert "test" in hc, f"Service {name} healthcheck lacks test"
        assert "interval" in hc, f"Service {name} healthcheck lacks interval"
        assert "timeout" in hc, f"Service {name} healthcheck lacks timeout"
        assert "retries" in hc, f"Service {name} healthcheck lacks retries"
        assert "start_period" in hc, f"Service {name} healthcheck lacks start_period"

        # Convert interval and timeout to seconds
        def to_sec(s: str) -> float:
            s = str(s).strip()
            if s.endswith("s"):
                return float(s[:-1])
            if s.endswith("m"):
                return float(s[:-1]) * 60
            return float(s)

        interval_sec = to_sec(hc["interval"])
        timeout_sec = to_sec(hc["timeout"])
        start_sec = to_sec(hc["start_period"])
        retries = int(hc["retries"])

        # Healthcheck timeout MUST be strictly less than interval to prevent overlaps
        assert timeout_sec < interval_sec, (
            f"Service {name} timeout ({timeout_sec}s) >= interval ({interval_sec}s)"
        )
        assert interval_sec >= 5.0, f"Service {name} interval too aggressive ({interval_sec}s)"
        assert retries >= 3, f"Service {name} retries too low ({retries})"
        assert start_sec >= 5.0, f"Service {name} start_period too short ({start_sec}s)"

    # Check probe commands specifically
    assert "pg_isready" in str(services["postgres"]["healthcheck"]["test"])
    assert "redis-cli" in str(services["redis"]["healthcheck"]["test"])
    assert "/api/health" in str(services["backend"]["healthcheck"]["test"])
    assert "3000" in str(services["frontend"]["healthcheck"]["test"])


def test_startup_dependencies_and_lifecycle():
    """Verify dependency graph is acyclic and uses service_healthy conditions."""
    compose = load_compose_dict()
    services = compose["services"]

    # Backend depends on postgres and redis healthy
    backend_deps = services["backend"].get("depends_on", {})
    assert "postgres" in backend_deps
    assert backend_deps["postgres"]["condition"] == "service_healthy"
    assert "redis" in backend_deps
    assert backend_deps["redis"]["condition"] == "service_healthy"

    # Frontend depends on backend healthy
    frontend_deps = services["frontend"].get("depends_on", {})
    assert "backend" in frontend_deps
    assert frontend_deps["backend"]["condition"] == "service_healthy"

    # Postgres and Redis have no dependencies
    assert "depends_on" not in services["postgres"]
    assert "depends_on" not in services["redis"]

    # Verify restart policies
    for name, svc in services.items():
        assert svc.get("restart") in ("unless-stopped", "always"), (
            f"Service {name} lacks reliable restart policy: {svc.get('restart')}"
        )


def test_network_and_volume_isolation():
    """Verify all containers connect to osint-net and postgres uses local volume."""
    compose = load_compose_dict()
    services = compose["services"]

    for name, svc in services.items():
        networks = svc.get("networks", [])
        assert "osint-net" in networks, f"Service {name} does not join osint-net"

    assert compose["networks"]["osint-net"]["driver"] == "bridge"
    assert compose["volumes"]["postgres_data"]["driver"] == "local"
    assert "postgres_data:/var/lib/postgresql/data" in services["postgres"]["volumes"]


# ==============================================================================
# 2. POSTGRES & REDIS LOW-MEMORY COMMAND TUNING
# ==============================================================================

def test_postgres_low_memory_tuning_parameters():
    """Verify postgres command flags fit within the 150MB container ceiling."""
    compose = load_compose_dict()
    cmd = str(compose["services"]["postgres"]["command"])

    # Extract flags
    assert "-c shared_buffers=32MB" in cmd, "Postgres shared_buffers should be 32MB"
    assert "-c max_connections=20" in cmd, "Postgres max_connections should be capped at 20"
    assert "-c work_mem=2MB" in cmd, "Postgres work_mem should be 2MB"
    assert "-c maintenance_work_mem=16MB" in cmd, "Postgres maintenance_work_mem should be 16MB"
    assert "-c effective_cache_size=64MB" in cmd, "Postgres effective_cache_size should be 64MB"


def test_redis_low_memory_tuning_parameters():
    """Verify redis command disables disk snapshots and caps memory at 32MB."""
    compose = load_compose_dict()
    cmd = str(compose["services"]["redis"]["command"])

    assert "--maxmemory 32mb" in cmd, "Redis maxmemory should be 32mb"
    assert "--maxmemory-policy allkeys-lru" in cmd, "Redis maxmemory-policy should be allkeys-lru"
    assert '--save ""' in cmd or "--save ''" in cmd or 'save ""' in cmd, "Redis save snapshotting should be disabled"
    assert "--appendonly no" in cmd, "Redis AOF should be disabled"


# ==============================================================================
# 3. DOCKERFILE ARCHITECTURE & SECURITY
# ==============================================================================

def test_backend_dockerfile_multi_stage_and_security():
    """Verify backend Dockerfile uses multi-stage build, unprivileged user, and dumb-init."""
    assert BACKEND_DOCKERFILE.exists()
    content = BACKEND_DOCKERFILE.read_text(encoding="utf-8")

    # Multi-stage verification
    assert "AS builder" in content, "Backend Dockerfile must define builder stage"
    assert "AS runner" in content, "Backend Dockerfile must define runner stage"

    # Base image version pinning
    assert "python:3.11-slim" in content, "Must use python:3.11-slim base"

    # Non-root user verification
    assert "appuser" in content, "Must create and use unprivileged appuser"
    assert "USER appuser" in content, "Must switch to USER appuser"
    assert "UID 10001" in content or "10001" in content, "Must assign non-system UID (10001)"

    # Signal propagation with dumb-init
    assert "dumb-init" in content, "Must install dumb-init"
    assert 'ENTRYPOINT ["/usr/bin/dumb-init", "--"]' in content, "Entrypoint must be dumb-init"

    # Performance environment variables
    assert "PYTHONUNBUFFERED=1" in content
    assert "PYTHONDONTWRITEBYTECODE=1" in content

    # Single uvicorn worker for memory predictability
    assert "--workers 1" in content or '"--workers", "1"' in content


def test_frontend_dockerfile_multi_stage_and_heap_limits():
    """Verify frontend Dockerfile uses multi-stage, standalone mode, non-root user, and V8 heap cap."""
    assert FRONTEND_DOCKERFILE.exists()
    content = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")

    # Multi-stage verification
    assert "AS deps" in content
    assert "AS builder" in content
    assert "AS runner" in content

    # Base image
    assert "node:20-alpine" in content

    # Non-root user
    assert "nextjs" in content
    assert "USER nextjs" in content

    # Memory cap: max-old-space-size
    assert "--max-old-space-size=180" in content, "V8 heap must be capped to 180MB"

    # Standalone artifact copy with chown
    assert "--chown=nextjs:nodejs /app/.next/standalone" in content
    assert "--chown=nextjs:nodejs /app/.next/static" in content
    assert "server.js" in content


def test_dockerignore_rules():
    """Verify .dockerignore prevents sensitive or heavy files from entering build context."""
    for path, name in [
        (ROOT_DOCKERIGNORE, "Root"),
        (BACKEND_DOCKERIGNORE, "Backend"),
        (FRONTEND_DOCKERIGNORE, "Frontend"),
    ]:
        assert path.exists(), f"{name} .dockerignore missing"
        content = path.read_text(encoding="utf-8")
        assert ".git" in content or "node_modules" in content or "__pycache__" in content


# ==============================================================================
# 4. RUNTIME OBSERVABILITY, TRACING & HEALTH ENDPOINTS
# ==============================================================================

def test_json_log_formatter_output():
    """Verify JSONLogFormatter outputs valid structured JSON with required fields."""
    from backend.app.main import JSONLogFormatter, trace_id_ctx

    formatter = JSONLogFormatter()
    token = trace_id_ctx.set("trace-test-12345")

    try:
        record = logging.LogRecord(
            name="test.logger",
            level=logging.WARNING,
            pathname="backend/app/main.py",
            lineno=42,
            msg="Adversarial stress test log message: %s",
            args=("success",),
            exc_info=None,
        )
        record.funcName = "test_func"
        output = formatter.format(record)

        data = json.loads(output)
        assert data["level"] == "WARNING"
        assert data["logger"] == "test.logger"
        assert data["message"] == "Adversarial stress test log message: success"
        assert data["trace_id"] == "trace-test-12345"
        assert data["source_file"] == "main.py"
        assert data["line_number"] == 42
        assert "timestamp" in data
        assert "environment" in data
    finally:
        trace_id_ctx.reset(token)


@pytest.mark.asyncio
async def test_health_endpoints_and_memory_reporting():
    """Verify /health, /api/health, /ready, /live return conformant contracts."""
    from httpx import AsyncClient, ASGITransport
    from backend.app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. /health
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] in ("healthy", "degraded")
        assert "memory_mb" in data
        assert isinstance(data["memory_mb"], (int, float))
        assert data["memory_mb"] <= 900.0
        assert "memory" in data
        assert data["memory"]["budget_compliant"] is True

        # 2. /api/health alias
        res_alias = await client.get("/api/health")
        assert res_alias.status_code == 200
        assert res_alias.json()["status"] == data["status"]

        # 3. /ready probe
        res_ready = await client.get("/ready")
        assert res_ready.status_code == 200
        assert res_ready.json()["ready"] is True

        # 4. /live probe
        res_live = await client.get("/live")
        assert res_live.status_code == 200
        assert res_live.json()["status"] == "alive"
