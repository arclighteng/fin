# security.py
"""
Security utilities for the web API.

TRUTH CONTRACT:
- All /api/* endpoints require auth (GET and mutations alike)
- Token can be set via FIN_API_TOKEN env var (user-managed, no server expiry)
  or auto-generated per process (signed with TimestampSigner, 8-hour expiry)
- FIN_AUTH_DISABLED=1 is only permitted on loopback — cli.py enforces this at startup
- Cookie-based session (fin_session, HttpOnly) is the primary browser auth mechanism
- Authorization: Bearer <token> header is kept for CLI/curl compatibility
"""
import logging
import os
import secrets
from typing import Optional

from fastapi import Cookie, Header, HTTPException, Request

log = logging.getLogger(__name__)

# Session token - generated once per process lifetime if not set via env
_session_token: Optional[str] = None
_signed_session_token: Optional[str] = None  # TimestampSigner-wrapped version
_csrf_token: Optional[str] = None

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _get_secret_key() -> str:
    """
    Get the secret key used for signing session tokens.

    Priority:
    1. FIN_SECRET_KEY env var (persistent across restarts)
    2. Auto-generated per-process (tokens invalidated on restart)
    """
    key = os.getenv("FIN_SECRET_KEY", "")
    if key:
        return key
    # Auto-generate once per process — warn on first call
    global _auto_secret_key_warned
    try:
        already_warned = _auto_secret_key_warned
    except NameError:
        already_warned = False
    if not already_warned:
        log.info(
            "FIN_SECRET_KEY not set — using auto-generated secret key. "
            "Session tokens will be invalidated when the server restarts. "
            "Set FIN_SECRET_KEY for persistent sessions."
        )
        _auto_secret_key_warned = True  # type: ignore[name-defined]  # module-level assignment
    global _process_secret_key
    try:
        return _process_secret_key  # type: ignore[return-value]
    except NameError:
        _process_secret_key = secrets.token_hex(32)  # type: ignore[name-defined]
        return _process_secret_key  # type: ignore[return-value]


def get_api_token() -> Optional[str]:
    """
    Get the API token for endpoint auth.

    Priority:
    1. FIN_API_TOKEN env var (user-configured, no server-side expiry)
    2. Auto-generated signed session token (8-hour expiry via TimestampSigner)
    3. None if FIN_AUTH_DISABLED=1 (only permitted on loopback — enforced at startup)

    Returns:
        Token string or None if auth is disabled.
        For auto-generated tokens the returned value is the *raw* (unsigned) token;
        use get_signed_session_token() to get the bearer-ready signed form.
    """
    global _session_token

    # Check if auth is explicitly disabled
    if os.getenv("FIN_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return None

    # Use env var if set (user manages lifetime)
    env_token = os.getenv("FIN_API_TOKEN")
    if env_token:
        return env_token

    # Generate raw session token on first call
    if _session_token is None:
        _session_token = secrets.token_urlsafe(24)

    return _session_token


def get_signed_session_token() -> Optional[str]:
    """
    Get the TimestampSigner-signed bearer token for auto-generated sessions.

    If FIN_API_TOKEN is set, returns it as-is (no signing — user manages lifetime).
    If auth is disabled, returns None.
    """
    global _signed_session_token

    if os.getenv("FIN_AUTH_DISABLED", "").lower() in ("1", "true", "yes"):
        return None

    env_token = os.getenv("FIN_API_TOKEN")
    if env_token:
        return env_token

    if _signed_session_token is None:
        raw = get_api_token()
        if raw is None:
            return None
        try:
            from itsdangerous import TimestampSigner
            signer = TimestampSigner(_get_secret_key())
            _signed_session_token = signer.sign(raw).decode()
        except ImportError:
            # itsdangerous not installed — fall back to unsigned token with a warning
            log.warning(
                "itsdangerous not installed — session tokens will not expire. "
                "Run: pip install itsdangerous"
            )
            _signed_session_token = raw

    return _signed_session_token


def _verify_token_value(provided_token: str) -> bool:
    """
    Validate a token string against the configured auth token.

    Handles both:
    - User-set FIN_API_TOKEN (plain constant-time compare, no expiry)
    - Auto-generated signed tokens (TimestampSigner validation with 8h max_age)

    Returns True if valid, raises HTTPException on failure.
    """
    env_token = os.getenv("FIN_API_TOKEN")
    if env_token:
        # User-managed token: plain compare, no expiry
        if not secrets.compare_digest(provided_token, env_token):
            raise HTTPException(status_code=403, detail="Invalid API token")
        return True

    # Auto-generated signed token path
    try:
        from itsdangerous import TimestampSigner, SignatureExpired, BadSignature
        signer = TimestampSigner(_get_secret_key())
        try:
            raw = signer.unsign(provided_token, max_age=28800)  # 8 hours
        except SignatureExpired:
            raise HTTPException(
                status_code=401,
                detail="Session token has expired. Restart the server or re-authenticate.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except BadSignature:
            raise HTTPException(status_code=403, detail="Invalid API token")
        # The unsigned raw value must match the current session token
        raw_str = raw.decode() if isinstance(raw, bytes) else raw
        expected = get_api_token()
        if expected is None or not secrets.compare_digest(raw_str, expected):
            raise HTTPException(status_code=403, detail="Invalid API token")
        return True
    except ImportError:
        # itsdangerous unavailable — fall back to plain compare against raw token
        expected = get_api_token()
        if expected is None or not secrets.compare_digest(provided_token, expected):
            raise HTTPException(status_code=403, detail="Invalid API token")
        return True


def verify_auth_token(
    authorization: Optional[str] = Header(None),
    fin_session: Optional[str] = Cookie(None),
) -> bool:
    """
    FastAPI dependency to verify bearer token or session cookie.

    Check order:
    1. Authorization: Bearer <token> header (CLI/curl compatibility; also used by unit tests)
    2. fin_session HttpOnly cookie (browser sessions)

    If auth is disabled (FIN_AUTH_DISABLED=1 — only permitted on loopback),
    always passes.

    NOTE: The middleware `require_api_auth` enforces auth on all /api/* paths.
    This dependency remains on mutation endpoints as a belt-and-suspenders measure
    and to preserve the existing unit-test calling convention
    (tests call verify_auth_token("Bearer token") directly).
    """
    required_token = get_api_token()

    # If no token configured (auth disabled), allow request
    if required_token is None:
        return True

    # --- Try Authorization: Bearer header first ---
    if authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return _verify_token_value(parts[1])
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format. Use 'Authorization: Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # --- Try fin_session cookie ---
    # Guard against FastAPI Cookie(None) sentinel being passed as the default value
    if fin_session and isinstance(fin_session, str):
        return _verify_token_value(fin_session)

    # --- Neither header nor cookie provided ---
    raise HTTPException(
        status_code=401,
        detail="Authentication required. Provide Authorization: Bearer <token> header or a valid fin_session cookie.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_csrf_token() -> str:
    """Get the per-session CSRF token, generating on first call."""
    global _csrf_token
    if _csrf_token is None:
        _csrf_token = secrets.token_urlsafe(32)
    return _csrf_token


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

    # For the CLI display, use the signed token (what users need to put in headers/cookies)
    display_token = get_signed_session_token() or token

    return {
        "auth_enabled": True,
        "token_preview": masked,
        "full_token": display_token,  # For CLI to show once
        "source": source,
    }
