import hashlib
import hmac
import secrets
import time
from typing import Optional
from fastapi import Depends, Header, HTTPException, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from backend.app.config import get_settings

security_bearer = HTTPBearer(auto_error=False)


def verify_api_key_string(provided_key: Optional[str]) -> bool:
    """Compare provided API key against configured key in constant time."""
    if not provided_key:
        return False
    settings = get_settings()
    configured_key = settings.API_KEY
    if not configured_key:
        return True  # If no API key configured, permit access (dev mode)
    try:
        return secrets.compare_digest(
            provided_key.encode("utf-8"),
            configured_key.encode("utf-8"),
        )
    except Exception:
        return False


def verify_admin_password(provided_password: Optional[str]) -> bool:
    """Compare provided password against configured admin password in constant time."""
    if not provided_password:
        return False
    settings = get_settings()
    configured_password = settings.ADMIN_PASSWORD or ""
    try:
        return secrets.compare_digest(
            provided_password.encode("utf-8"),
            configured_password.encode("utf-8"),
        )
    except Exception:
        return False


async def require_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    auth_header: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    query_key: Optional[str] = Query(None, alias="api_key"),
) -> str:
    """
    FastAPI dependency to validate API Key from X-API-Key header,
    Authorization Bearer token, or query parameter.
    Raises HTTP 401 Unauthorized if invalid or missing.
    """
    # Extract candidate key in priority order:
    candidate_key: Optional[str] = None

    if x_api_key:
        candidate_key = x_api_key
    elif auth_header and auth_header.credentials:
        candidate_key = auth_header.credentials
    elif query_key:
        candidate_key = query_key

    if not candidate_key or not verify_api_key_string(candidate_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return candidate_key


async def optional_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    auth_header: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    query_key: Optional[str] = Query(None, alias="api_key"),
) -> Optional[str]:
    """
    FastAPI dependency that attempts to validate API Key if present,
    returning None if absent without raising an exception.
    """
    candidate_key: Optional[str] = None

    if x_api_key:
        candidate_key = x_api_key
    elif auth_header and auth_header.credentials:
        candidate_key = auth_header.credentials
    elif query_key:
        candidate_key = query_key

    if candidate_key and verify_api_key_string(candidate_key):
        return candidate_key

    return None


class SimpleRateLimiter:
    """Sliding-window IP rate limiter for authentication attempts."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def is_limited(self, key: str) -> tuple[bool, int]:
        now = time.time()
        window_start = now - self.window_seconds
        records = self._attempts.get(key, [])
        valid_records = [t for t in records if t > window_start]
        self._attempts[key] = valid_records

        if len(valid_records) >= self.max_attempts:
            oldest = valid_records[0]
            retry_after = max(1, int(oldest + self.window_seconds - now))
            return True, retry_after
        return False, 0

    def record_attempt(self, key: str) -> None:
        now = time.time()
        window_start = now - self.window_seconds
        records = [t for t in self._attempts.get(key, []) if t > window_start]
        records.append(now)
        self._attempts[key] = records

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


auth_rate_limiter = SimpleRateLimiter(max_attempts=5, window_seconds=300)


def generate_viewer_token(expires_in_seconds: int = 86400) -> str:
    """Generate an HMAC-SHA256 signed viewer session token."""
    settings = get_settings()
    expiry = int(time.time()) + expires_in_seconds
    msg = f"viewer:{expiry}".encode("utf-8")
    secret = settings.PUBLIC_DISPLAY_SECRET.encode("utf-8")
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"v1.{expiry}.{sig}"


def verify_viewer_token(token: Optional[str]) -> bool:
    """Verify viewer token or admin API key."""
    if not token:
        return False
    if verify_api_key_string(token):
        return True

    settings = get_settings()
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return False

    try:
        expiry = int(parts[1])
        if time.time() > expiry:
            return False
        provided_sig = parts[2]
        msg = f"viewer:{expiry}".encode("utf-8")
        secret = settings.PUBLIC_DISPLAY_SECRET.encode("utf-8")
        expected_sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        return secrets.compare_digest(provided_sig, expected_sig)
    except Exception:
        return False


def verify_public_password(provided_password: Optional[str]) -> bool:
    """Verify candidate password against PUBLIC_DISPLAY_PASSWORD."""
    if not provided_password:
        return False
    settings = get_settings()
    expected = settings.PUBLIC_DISPLAY_PASSWORD or "osint-2026"
    try:
        return secrets.compare_digest(
            provided_password.encode("utf-8"),
            expected.encode("utf-8"),
        )
    except Exception:
        return False


async def require_viewer_token(
    x_viewer_token: Optional[str] = Header(None, alias="X-Viewer-Token"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    auth_header: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    query_token: Optional[str] = Query(None, alias="viewer_token"),
    query_key: Optional[str] = Query(None, alias="api_key"),
) -> str:
    """
    FastAPI dependency requiring a valid viewer session token or admin API key.
    Raises HTTP 401 Unauthorized if missing/invalid to prevent heavy query loading.
    """
    candidate: Optional[str] = None
    if x_viewer_token:
        candidate = x_viewer_token
    elif x_api_key:
        candidate = x_api_key
    elif auth_header and auth_header.credentials:
        candidate = auth_header.credentials
    elif query_token:
        candidate = query_token
    elif query_key:
        candidate = query_key

    if not candidate or not verify_viewer_token(candidate):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please enter display access password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return candidate

