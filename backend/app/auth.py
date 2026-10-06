import secrets
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
