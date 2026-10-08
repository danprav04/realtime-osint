"""
Unit and integration tests for the Pentagon Pizza Meter API endpoint.
Verifies auth protection, canonical schema compliance, Arlington location breakdown,
and simulation override capabilities.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from backend.app.config import get_settings
from backend.app.db.session import close_db, init_db
from backend.app.main import app


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(autouse=True)
async def setup_and_teardown_db():
    await init_db()
    # Reset any simulation override between tests
    from backend.app.api.pizza_meter import calculate_pizza_meter_status
    import backend.app.api.pizza_meter as pm_module
    pm_module._simulated_override = None
    yield
    pm_module._simulated_override = None
    await close_db()


@pytest.mark.asyncio
async def test_pizza_meter_unauthorized_without_key():
    """Verify that requests without an API key or with an invalid key are rejected (401)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # No key
        res = await client.get("/api/pizza-meter")
        assert res.status_code == 401

        # Invalid key
        res_invalid = await client.get("/api/pizza-meter", headers={"X-API-Key": "invalid_wrong_key"})
        assert res_invalid.status_code == 401


@pytest.mark.asyncio
async def test_pizza_meter_get_nominal_success():
    """Verify GET /api/pizza-meter returns complete schema with valid values."""
    settings = get_settings()
    api_key = settings.API_KEY
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/pizza-meter", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.json()

        # Contract assertions
        assert data["status"] in ("NOMINAL", "ELEVATED", "HIGH", "CRITICAL")
        assert 1 <= data["level"] <= 4
        assert 0 <= data["score"] <= 100
        assert data["trend"] in ("stable", "increasing", "decreasing")
        assert data["baseline_multiplier"] >= 1.0
        assert "Arlington" in data["monitored_region"] or "Pentagon" in data["monitored_region"]
        assert len(data["description"]) > 0
        assert "last_updated" in data

        # Location checks: 3 Arlington defense corridor spots
        locations = data["locations"]
        assert len(locations) == 3
        names = [loc["name"] for loc in locations]
        assert any("Domino" in name for name in names)
        assert any("Papa John" in name for name in names)
        assert any("Pizza Hut" in name for name in names)

        for loc in locations:
            assert 0 <= loc["busyness"] <= 100
            assert loc["status"] in ("Normal", "Elevated", "Spike")
            assert "Pentagon" in loc["distance"]

        # History checks: synthetic trend points
        history = data["history"]
        assert len(history) >= 4
        for point in history:
            assert "timestamp" in point
            assert "score" in point


@pytest.mark.asyncio
async def test_pizza_meter_simulate_and_reset():
    """Verify that POST /api/pizza-meter/simulate overrides and resets state."""
    settings = get_settings()
    api_key = settings.API_KEY
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Simulate Level 4 CRITICAL (DEFCON Pizza)
        sim_res = await client.post(
            "/api/pizza-meter/simulate",
            headers={"X-API-Key": api_key},
            json={"level": 4, "status": "CRITICAL", "score": 92, "trend": "increasing"},
        )
        assert sim_res.status_code == 200
        sim_data = sim_res.json()
        assert sim_data["level"] == 4
        assert sim_data["status"] == "CRITICAL"
        assert sim_data["score"] == 92
        assert sim_data["trend"] == "increasing"
        assert sim_data["baseline_multiplier"] >= 3.0

        # Verify subsequent GET reflects the simulation
        get_res = await client.get("/api/pizza-meter", headers={"X-API-Key": api_key})
        assert get_res.status_code == 200
        assert get_res.json()["status"] == "CRITICAL"
        assert get_res.json()["score"] == 92

        # 2. Reset simulation
        reset_res = await client.post(
            "/api/pizza-meter/simulate",
            headers={"X-API-Key": api_key},
            json={"reset": True},
        )
        assert reset_res.status_code == 200
        reset_data = reset_res.json()
        # Should return to calculated state
        assert reset_data["level"] in (1, 2)
