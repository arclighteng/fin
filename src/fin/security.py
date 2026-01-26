# security.py
"""
Security utilities for the web API.

TRUTH CONTRACT:
- Mutation endpoints (POST, DELETE) require auth when enabled
- Read-only endpoints (GET) are always public for local use
- Token can be set via FIN_API_TOKEN env var or auto-generated per session
"""
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException

# Session token - generated once per process lifetime if not set via env
_session_token: Optional[str] = None


def get_api_token() -> Optional[str]:
    """
    Get the API token for mutation endpoint auth.

    Priority:
    1. FIN_API_TOKEN env var (user-configured)
    2. Auto-generated session token (for basic local protection)
    3. None if FIN_AUTH_DISABLED=1 (explicitly disabled)

    Returns:
        Token string or None if auth is disabled
    """
    global _session_token

    # Check if auth is explicitly disabled
    if os.getenv("FIN_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return None

    # Use env var if set
    env_token = os.getenv("FIN_API_TOKEN")
    if env_token:
        return env_token

    # Generate session token on first call
    if _session_token is None:
        _session_token = secrets.token_urlsafe(24)

    return _session_token


def verify_auth_token(authorization: Optional[str] = Header(None)) -> bool:
    """
    FastAPI dependency to verify bearer token for mutation endpoints.

    If auth is disabled (FIN_AUTH_DISABLED=1), always passes.
    If no auth header provided when required, returns 401.
    If auth header doesn't match, returns 403.

    Usage:
        @app.post("/api/endpoint")
        def endpoint(auth: bool = Depends(verify_auth_token)):
            ...
    """
    required_token = get_api_token()

    # If no token configured (auth disabled), allow request
    if required_token is None:
        return True

    # Token required but not provided
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header required for mutation endpoints. "
                   "Use 'Authorization: Bearer <token>' or set FIN_AUTH_DISABLED=1",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Parse bearer token
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Use 'Authorization: Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    provided_token = parts[1]

    # Constant-time comparison to prevent timing attacks
    if not secrets.compare_digest(provided_token, required_token):
        raise HTTPException(
            status_code=403,
            detail="Invalid API token",
        )

    return True


def get_auth_info() -> dict:
    """
    Get auth configuration info for display.

    Returns:
        Dict with auth_enabled, token (masked), and source
    """
    token = get_api_token()

    if token is None:
        return {
            "auth_enabled": False,
            "reason": "Disabled via FIN_AUTH_DISABLED=1",
        }

    source = "env" if os.getenv("FIN_API_TOKEN") else "auto"

    # Mask token for display (show first 4 chars)
    masked = token[:4] + "..." if len(token) > 4 else "***"

    return {
        "auth_enabled": True,
        "token_preview": masked,
        "full_token": token,  # For CLI to show once
        "source": source,
    }
