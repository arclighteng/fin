"""
Tests for web security middleware, /auth/session endpoint, CSRF, and security headers.

Covers:
- Item 1: API token not leaked into HTML
- Item 2: require_api_auth middleware on all /api/* endpoints
- CSRF middleware (POST without X-CSRF-Token header returns 403)
- Security headers (CSP, X-Frame-Options, X-Content-Type-Options)
- /auth/session endpoint: cookie flags, token validation, session-enables-API-access
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from fin.web import app, _get_config
from fin import db as dbmod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_config(db_path: str):
    """Return a mock Config-like object pointing at the given db_path."""
    class MockConfig:
        simplefin_access_url = ""
        log_level = "INFO"
        log_format = "simple"
    cfg = MockConfig()
    cfg.db_path = db_path
    return cfg


def _reset_web_globals():
    """Reset web.py module-level cache so each fixture gets a fresh config."""
    import fin.web as web_module
    web_module._config = None
    web_module._db_initialized = False


def _reset_security_globals():
    """Reset security module token cache so env changes take effect."""
    import fin.security as sec
    sec._session_token = None
    sec._signed_session_token = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    """Temporary, initialised SQLite database."""
    db_file = tmp_path / "test.db"
    conn = dbmod.connect(str(db_file))
    dbmod.init_db(conn)
    conn.close()
    return str(db_file)


@pytest.fixture()
def authed_client(tmp_db):
    """
    TestClient with FIN_API_TOKEN set to a known value.

    Auth disabled flag is absent so all /api/* routes enforce auth.
    """
    token = "test-bearer-token-abc123"
    mock_cfg = _make_mock_config(tmp_db)

    _reset_security_globals()
    _reset_web_globals()

    with patch.dict(os.environ, {"FIN_API_TOKEN": token}, clear=False):
        # Ensure auth-disabled flag absent
        os.environ.pop("FIN_AUTH_DISABLED", None)
        import fin.security as sec
        sec._session_token = None
        sec._signed_session_token = None

        with patch.object(__import__("fin.web", fromlist=["_get_config"]), "_get_config", return_value=mock_cfg):
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, token


@pytest.fixture()
def noauth_client(tmp_db):
    """TestClient with auth fully disabled (FIN_AUTH_DISABLED=1)."""
    mock_cfg = _make_mock_config(tmp_db)

    _reset_security_globals()
    _reset_web_globals()

    with patch.dict(os.environ, {"FIN_AUTH_DISABLED": "1"}, clear=False):
        os.environ.pop("FIN_API_TOKEN", None)
        import fin.security as sec
        sec._session_token = None
        sec._signed_session_token = None

        with patch.object(__import__("fin.web", fromlist=["_get_config"]), "_get_config", return_value=mock_cfg):
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client


# ---------------------------------------------------------------------------
# Convenience: build an authed client without the fixture for one-off tests
# ---------------------------------------------------------------------------

def _make_authed_client(tmp_db, token="test-bearer-token-abc123"):
    """Context manager returning (client, token) with FIN_API_TOKEN set."""
    import fin.web as web_module
    mock_cfg = _make_mock_config(tmp_db)

    import fin.security as sec
    sec._session_token = None
    sec._signed_session_token = None
    web_module._config = None
    web_module._db_initialized = False

    return token, mock_cfg


# ---------------------------------------------------------------------------
# Item 2 — require_api_auth middleware
# ---------------------------------------------------------------------------

class TestRequireApiAuthMiddleware:
    """All /api/* routes must enforce auth; non-/api/* routes must not."""

    def test_api_search_without_credentials_returns_401(self, authed_client):
        client, _ = authed_client
        response = client.get("/api/search?q=netflix")
        assert response.status_code == 401

    def test_api_search_with_bearer_header_returns_non_401(self, authed_client):
        client, token = authed_client
        response = client.get(
            "/api/search?q=netflix",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code != 401

    def test_api_search_with_session_cookie_returns_non_401(self, authed_client):
        client, token = authed_client
        client.cookies.set("fin_session", token)
        response = client.get("/api/search?q=netflix")
        assert response.status_code != 401

    def test_wrong_bearer_token_returns_403(self, authed_client):
        client, _ = authed_client
        response = client.get(
            "/api/search?q=test",
            headers={"Authorization": "Bearer totally-wrong"},
        )
        assert response.status_code == 403

    def test_malformed_auth_header_format_returns_401(self, authed_client):
        """Authorization header that is not 'Bearer <token>' should be 401."""
        client, _ = authed_client
        response = client.get(
            "/api/search?q=test",
            headers={"Authorization": "NotBearer abc"},
        )
        assert response.status_code == 401

    def test_auth_session_endpoint_reachable_without_prior_session(self, authed_client):
        """POST /auth/session must not be blocked by the require_api_auth middleware."""
        client, token = authed_client
        response = client.post("/auth/session", json={"token": token})
        # Middleware must pass it through; endpoint returns 200 or validation error
        assert response.status_code in (200, 401, 422)
        # Critically: must not get a plain middleware 401 with "API access" wording
        if response.status_code == 401:
            assert "API access" not in response.text

    def test_auth_disabled_allows_api_access_without_credentials(self, noauth_client):
        """When FIN_AUTH_DISABLED=1, /api/* endpoints are accessible without credentials."""
        response = noauth_client.get("/api/search?q=test")
        assert response.status_code != 401

    def test_non_api_path_accessible_without_credentials(self, authed_client):
        """Static routes (e.g. /dashboard redirect) must not be blocked by API auth middleware."""
        client, _ = authed_client
        # / redirects to /dashboard; neither is an /api/ path
        response = client.get("/", follow_redirects=False)
        # Not a 401 from auth middleware
        assert response.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# /auth/session endpoint
# ---------------------------------------------------------------------------

class TestCreateSession:
    """Tests for POST /auth/session — cookie issuance and validation."""

    def test_valid_token_returns_200(self, authed_client):
        client, token = authed_client
        response = client.post("/auth/session", json={"token": token})
        assert response.status_code == 200

    def test_valid_token_sets_fin_session_cookie(self, authed_client):
        client, token = authed_client
        response = client.post("/auth/session", json={"token": token})
        assert response.status_code == 200
        set_cookie = response.headers.get("set-cookie", "")
        assert "fin_session" in set_cookie

    def test_cookie_is_httponly(self, authed_client):
        client, token = authed_client
        response = client.post("/auth/session", json={"token": token})
        set_cookie = response.headers.get("set-cookie", "")
        assert "httponly" in set_cookie.lower()

    def test_cookie_samesite_strict(self, authed_client):
        client, token = authed_client
        response = client.post("/auth/session", json={"token": token})
        set_cookie = response.headers.get("set-cookie", "")
        assert "samesite=strict" in set_cookie.lower()

    def test_wrong_token_returns_401(self, authed_client):
        client, _ = authed_client
        response = client.post("/auth/session", json={"token": "wrong-token"})
        assert response.status_code in (401, 403)

    def test_session_cookie_enables_subsequent_api_access(self, authed_client):
        """After obtaining a session cookie via /auth/session, API calls should pass auth."""
        client, token = authed_client
        login = client.post("/auth/session", json={"token": token})
        assert login.status_code == 200
        # TestClient carries the set-cookie automatically on subsequent requests
        api = client.get("/api/search?q=test")
        assert api.status_code != 401

    def test_missing_token_field_returns_422(self, authed_client):
        """Missing JSON body or token field should return 422 Unprocessable Entity."""
        client, _ = authed_client
        response = client.post("/auth/session", json={})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# CSRF middleware
# ---------------------------------------------------------------------------

class TestCsrfMiddleware:
    """POST/PUT/DELETE requests without X-CSRF-Token header must return 403."""

    def test_post_mutation_without_csrf_header_returns_403(self, authed_client):
        """Any mutation endpoint reached with session cookie but no CSRF header → 403."""
        client, token = authed_client
        # Obtain session cookie so the auth layer passes
        login = client.post("/auth/session", json={"token": token})
        assert login.status_code == 200

        # Now POST to a mutation endpoint without X-CSRF-Token
        # /api/sync is a POST endpoint that exists; even if it fails for other reasons
        # it must first pass CSRF or return 403
        response = client.post("/api/sync")
        assert response.status_code == 403

    def test_auth_session_endpoint_skips_csrf_check(self, authed_client):
        """POST /auth/session must succeed without X-CSRF-Token (it is the login endpoint)."""
        client, token = authed_client
        response = client.post("/auth/session", json={"token": token})
        # Must not be blocked by CSRF middleware
        assert response.status_code != 403

    def test_csrf_check_bypassed_when_auth_disabled(self, noauth_client):
        """When auth is disabled (loopback), CSRF is also skipped."""
        # A POST without cookie or CSRF header should not be blocked by CSRF middleware
        # (it may fail for other reasons, but not with 403 from CSRF)
        response = noauth_client.post("/api/sync")
        assert response.status_code != 403

    def test_post_with_correct_csrf_header_passes_middleware(self, authed_client):
        """A POST carrying the correct X-CSRF-Token should pass CSRF middleware."""
        from fin.security import get_csrf_token
        client, token = authed_client
        client.cookies.set("fin_session", token)
        csrf = get_csrf_token()
        # Even if the endpoint itself returns an error, the CSRF layer should pass it through
        response = client.post("/api/sync", headers={"X-CSRF-Token": csrf})
        assert response.status_code != 403


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------

class TestSecurityHeaders:
    """Every response must carry the required security headers."""

    def test_csp_header_present(self, authed_client):
        client, _ = authed_client
        response = client.get("/")
        assert "content-security-policy" in response.headers

    def test_x_frame_options_deny(self, authed_client):
        client, _ = authed_client
        response = client.get("/")
        assert response.headers.get("x-frame-options", "").upper() == "DENY"

    def test_x_content_type_options_nosniff(self, authed_client):
        client, _ = authed_client
        response = client.get("/")
        assert response.headers.get("x-content-type-options", "").lower() == "nosniff"

    def test_csp_header_on_api_endpoint(self, authed_client):
        """Security headers must be on API responses too, not just HTML pages."""
        client, token = authed_client
        response = client.get(
            "/api/search?q=test",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert "content-security-policy" in response.headers


# ---------------------------------------------------------------------------
# Item 1 — token not leaked into HTML
# ---------------------------------------------------------------------------

class TestTokenNotInHtml:
    """The API token must never appear as a literal string in rendered HTML."""

    def test_api_token_not_in_dashboard_html(self, authed_client):
        """Dashboard HTML must not contain the raw API token string."""
        client, token = authed_client
        # Dashboard is not behind /api/ so it doesn't require auth headers
        response = client.get("/dashboard")
        if response.status_code == 200:
            assert token not in response.text

    def test_api_token_not_in_root_html(self, authed_client):
        """Root redirect response must not expose the API token."""
        client, token = authed_client
        response = client.get("/", follow_redirects=True)
        if response.status_code == 200:
            assert token not in response.text


class TestAutoLoginRoute:
    """Tests for GET /auto-login."""

    def test_valid_token_sets_cookie_and_redirects(self, tmp_db):
        """Valid ?t= param sets fin_session cookie and redirects to /dashboard."""
        token = "valid-test-token-xyz"
        _reset_security_globals()
        _reset_web_globals()
        mock_cfg = _make_mock_config(tmp_db)

        with patch.dict(os.environ, {"FIN_API_TOKEN": token}, clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            import fin.security as sec
            sec._session_token = None
            sec._signed_session_token = None
            with patch.object(
                __import__("fin.web", fromlist=["_get_config"]),
                "_get_config",
                return_value=mock_cfg,
            ):
                with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
                    resp = client.get(f"/auto-login?t={token}")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        assert "fin_session" in resp.cookies
        assert resp.cookies["fin_session"] == token

    def test_invalid_token_redirects_without_cookie(self, tmp_db):
        """Invalid ?t= param redirects to /dashboard without setting a cookie."""
        token = "valid-test-token-xyz"
        mock_cfg = _make_mock_config(tmp_db)
        _reset_security_globals()
        _reset_web_globals()

        with patch.dict(os.environ, {"FIN_API_TOKEN": token}, clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            import fin.security as sec
            sec._session_token = None
            sec._signed_session_token = None
            with patch.object(
                __import__("fin.web", fromlist=["_get_config"]),
                "_get_config",
                return_value=mock_cfg,
            ):
                with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
                    resp = client.get("/auto-login?t=wrong-token")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        assert "fin_session" not in resp.cookies

    def test_missing_token_param_redirects_without_cookie(self, tmp_db):
        """Missing ?t param redirects to /dashboard without setting a cookie."""
        token = "valid-test-token-xyz"
        mock_cfg = _make_mock_config(tmp_db)
        _reset_security_globals()
        _reset_web_globals()

        with patch.dict(os.environ, {"FIN_API_TOKEN": token}, clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            import fin.security as sec
            sec._session_token = None
            sec._signed_session_token = None
            with patch.object(
                __import__("fin.web", fromlist=["_get_config"]),
                "_get_config",
                return_value=mock_cfg,
            ):
                with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
                    resp = client.get("/auto-login")

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard"
        assert "fin_session" not in resp.cookies

    def test_auth_disabled_redirects_without_cookie(self, tmp_db):
        """When auth is disabled, /auto-login redirects without setting a cookie."""
        mock_cfg = _make_mock_config(tmp_db)
        _reset_security_globals()
        _reset_web_globals()

        with patch.dict(os.environ, {"FIN_AUTH_DISABLED": "1"}, clear=False):
            os.environ.pop("FIN_API_TOKEN", None)
            import fin.security as sec
            sec._session_token = None
            sec._signed_session_token = None
            with patch.object(
                __import__("fin.web", fromlist=["_get_config"]),
                "_get_config",
                return_value=mock_cfg,
            ):
                with TestClient(app, raise_server_exceptions=False, follow_redirects=False) as client:
                    resp = client.get("/auto-login?t=anything")

        assert resp.status_code == 302
        assert "fin_session" not in resp.cookies
