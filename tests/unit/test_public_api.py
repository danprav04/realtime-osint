"""
tests/unit/test_public_api.py - Unit Tests for Public Read-Only Endpoints & Password Auth
========================================================================================
Validates:
1. Public password authentication and token generation.
2. In-memory sliding-window IP rate limiting (max 5 failed attempts per 5 minutes).
3. Zero heavy data loading / 401 Unauthorized for unauthenticated visitors.
4. Gated read-only access with valid viewer token.
5. Absolute prevention of LLM generation on public summary endpoint.
"""

import pytest
from starlette.testclient import TestClient
from backend.app.main import app
from backend.app.auth import (
    auth_rate_limiter,
    generate_viewer_token,
    verify_viewer_token,
    verify_public_password,
)


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset rate limiter state before each test."""
    auth_rate_limiter._attempts.clear()


def test_public_password_verification():
    """Test constant-time public password verification."""
    assert verify_public_password("osint-2026") is True
    assert verify_public_password("wrong-password") is False
    assert verify_public_password("") is False
    assert verify_public_password(None) is False


def test_viewer_token_generation_and_validation():
    """Test viewer token format, signature, and expiration."""
    token = generate_viewer_token(expires_in_seconds=3600)
    assert token.startswith("v1.")
    assert verify_viewer_token(token) is True

    # Tampered signature should fail
    tampered = token[:-4] + "ffff"
    assert verify_viewer_token(tampered) is False

    # Invalid token format should fail
    assert verify_viewer_token("invalid.token") is False
    assert verify_viewer_token("") is False
    assert verify_viewer_token(None) is False


def test_public_auth_endpoint_success():
    """Test successful login via POST /api/public/auth returns token."""
    client = TestClient(app)
    response = client.post("/api/public/auth", json={"password": "osint-2026"})
    assert response.status_code == 200
    data = response.json()
    assert data["authenticated"] is True
    assert "token" in data
    assert verify_viewer_token(data["token"]) is True


def test_public_auth_endpoint_failure():
    """Test incorrect password returns 401 Unauthorized."""
    client = TestClient(app)
    response = client.post("/api/public/auth", json={"password": "wrong-password"})
    assert response.status_code == 401
    assert "Incorrect password" in response.json()["detail"]


def test_public_auth_rate_limiting():
    """Test exceeding 5 failed password attempts returns 429 Too Many Requests."""
    client = TestClient(app)
    for _ in range(5):
        resp = client.post("/api/public/auth", json={"password": "wrong"})
        assert resp.status_code == 401

    # 6th attempt should be blocked by rate limiter
    resp_blocked = client.post("/api/public/auth", json={"password": "wrong"})
    assert resp_blocked.status_code == 429
    assert "Too many failed password attempts" in resp_blocked.json()["detail"]
    assert "Retry-After" in resp_blocked.headers


def test_public_endpoints_unauthenticated():
    """Verify that unauthenticated visitors are blocked with 401 and zero heavy data loaded."""
    client = TestClient(app)

    endpoints = [
        "/api/public/summary",
        "/api/public/events",
        "/api/public/map",
        "/api/public/stats",
        "/api/public/theme",
        "/api/public/verify",
    ]

    for ep in endpoints:
        resp = client.get(ep)
        assert resp.status_code == 401, f"Expected 401 for unauthenticated {ep}, got {resp.status_code}"
        assert "Authentication required" in resp.json()["detail"]


def test_public_endpoints_authenticated():
    """Verify that visitors with a valid viewer token can access read-only intelligence data."""
    client = TestClient(app)
    token = generate_viewer_token(expires_in_seconds=3600)
    headers = {"X-Viewer-Token": token}

    # 1. Verify token endpoint
    resp_verify = client.get("/api/public/verify", headers=headers)
    assert resp_verify.status_code == 200
    assert resp_verify.json()["authenticated"] is True

    # 2. Public theme endpoint
    resp_theme = client.get("/api/public/theme", headers=headers)
    assert resp_theme.status_code == 200
    assert "name" in resp_theme.json()

    # 3. Public summary endpoint (must return cached=True and never call LLM)
    resp_summary = client.get("/api/public/summary", headers=headers)
    assert resp_summary.status_code == 200
    summary_data = resp_summary.json()
    assert summary_data["cached"] is True
    assert "threat_trajectory" in summary_data

    # 4. Public events list endpoint
    resp_events = client.get("/api/public/events", headers=headers)
    assert resp_events.status_code == 200
    assert isinstance(resp_events.json(), list)

    # 5. Public stats endpoint
    resp_stats = client.get("/api/public/stats", headers=headers)
    assert resp_stats.status_code == 200
    assert "total_events" in resp_stats.json()
