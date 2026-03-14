"""
Tests for security module.

TRUTH CONTRACT verification:
- Token auth protects mutation endpoints
- Auth can be disabled via env var
- Token comparison is constant-time
"""
import os
import pytest
from unittest.mock import patch

from fin.security import (
    get_api_token,
    verify_auth_token,
    get_auth_info,
    _session_token,
)
from fastapi import HTTPException


class TestGetApiToken:
    """Test API token generation and retrieval."""

    def test_generates_session_token(self):
        """Should generate a session token if none set."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear session token
            import fin.security
            fin.security._session_token = None

            token = get_api_token()
            assert token is not None
            assert len(token) > 16  # urlsafe_24 generates ~32 chars

    def test_uses_env_token(self):
        """Should use FIN_API_TOKEN env var if set."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "my-custom-token"}):
            token = get_api_token()
            assert token == "my-custom-token"

    def test_auth_disabled(self):
        """Should return None when FIN_AUTH_DISABLED=1."""
        with patch.dict(os.environ, {"FIN_AUTH_DISABLED": "1"}):
            token = get_api_token()
            assert token is None

    def test_auth_disabled_variations(self):
        """Should handle various true-ish values for disabled."""
        for value in ("1", "true", "TRUE", "yes", "YES"):
            with patch.dict(os.environ, {"FIN_AUTH_DISABLED": value}):
                assert get_api_token() is None


class TestVerifyAuthToken:
    """Test auth token verification."""

    def test_no_auth_required_when_disabled(self):
        """Should pass when auth is disabled."""
        with patch.dict(os.environ, {"FIN_AUTH_DISABLED": "1"}):
            # Should not raise
            result = verify_auth_token(None)
            assert result is True

    def test_401_when_no_header_provided(self):
        """Should return 401 when auth required but no header."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "test-token"}, clear=True):
            with pytest.raises(HTTPException) as exc:
                verify_auth_token(None)
            assert exc.value.status_code == 401

    def test_401_on_malformed_header(self):
        """Should return 401 on malformed Authorization header."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "test-token"}, clear=True):
            with pytest.raises(HTTPException) as exc:
                verify_auth_token("Basic abc123")
            assert exc.value.status_code == 401

    def test_403_on_wrong_token(self):
        """Should return 403 when token doesn't match."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "correct-token"}, clear=True):
            with pytest.raises(HTTPException) as exc:
                verify_auth_token("Bearer wrong-token")
            assert exc.value.status_code == 403

    def test_success_on_correct_token(self):
        """Should return True when token matches."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "correct-token"}, clear=True):
            result = verify_auth_token("Bearer correct-token")
            assert result is True

    def test_bearer_case_insensitive(self):
        """Should accept Bearer in any case."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "test-token"}, clear=True):
            assert verify_auth_token("Bearer test-token") is True
            assert verify_auth_token("bearer test-token") is True
            assert verify_auth_token("BEARER test-token") is True


class TestGetAuthInfo:
    """Test auth info retrieval."""

    def test_shows_disabled_status(self):
        """Should show disabled reason when auth off."""
        with patch.dict(os.environ, {"FIN_AUTH_DISABLED": "1"}):
            info = get_auth_info()
            assert info["auth_enabled"] is False
            assert "Disabled" in info.get("reason", "")

    def test_shows_env_source(self):
        """Should indicate env as source when using env token."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "my-token"}, clear=True):
            info = get_auth_info()
            assert info["auth_enabled"] is True
            assert info["source"] == "env"
            assert info["full_token"] == "my-token"

    def test_shows_auto_source(self):
        """Should indicate auto as source when using session token."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear session token to force regeneration
            import fin.security
            fin.security._session_token = None

            # Also clear FIN_API_TOKEN and FIN_AUTH_DISABLED
            os.environ.pop("FIN_API_TOKEN", None)
            os.environ.pop("FIN_AUTH_DISABLED", None)

            info = get_auth_info()
            assert info["auth_enabled"] is True
            assert info["source"] == "auto"
            assert "full_token" in info

    def test_masks_token_preview(self):
        """Should mask token in preview."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "longtoken123"}, clear=True):
            info = get_auth_info()
            assert info["token_preview"] == "long..."
            assert info["full_token"] == "longtoken123"


# ---------------------------------------------------------------------------
# itsdangerous is an optional dependency — skip gracefully if absent
# ---------------------------------------------------------------------------
itsdangerous = pytest.importorskip("itsdangerous", reason="itsdangerous not installed")


class TestGetSignedSessionToken:
    """Test get_signed_session_token() covering env-token, auto-token, and disabled paths."""

    def _reset_module_state(self):
        import fin.security as sec
        sec._session_token = None
        sec._signed_session_token = None

    def test_returns_env_token_as_is(self):
        """FIN_API_TOKEN is returned verbatim — user manages its lifetime."""
        import fin.security as sec
        self._reset_module_state()
        with patch.dict(os.environ, {"FIN_API_TOKEN": "mytoken"}, clear=True):
            from fin.security import get_signed_session_token
            token = get_signed_session_token()
            assert token == "mytoken"

    def test_auto_token_is_signable(self):
        """Auto-generated tokens are wrapped with TimestampSigner."""
        import fin.security as sec
        self._reset_module_state()
        # Ensure neither env var is present
        env = {k: v for k, v in os.environ.items()
               if k not in ("FIN_API_TOKEN", "FIN_AUTH_DISABLED")}
        with patch.dict(os.environ, env, clear=True):
            from fin.security import get_signed_session_token, _get_secret_key
            signed = get_signed_session_token()
            assert signed is not None
            from itsdangerous import TimestampSigner
            signer = TimestampSigner(_get_secret_key())
            # Should not raise BadSignature or SignatureExpired
            raw = signer.unsign(signed, max_age=28800)
            assert raw is not None

    def test_returns_none_when_auth_disabled(self):
        """Returns None when FIN_AUTH_DISABLED=1."""
        import fin.security as sec
        self._reset_module_state()
        with patch.dict(os.environ, {"FIN_AUTH_DISABLED": "1"}, clear=True):
            from fin.security import get_signed_session_token
            result = get_signed_session_token()
            assert result is None

    def test_signed_token_cached_across_calls(self):
        """Second call returns the same signed token (not re-signed)."""
        import fin.security as sec
        self._reset_module_state()
        env = {k: v for k, v in os.environ.items()
               if k not in ("FIN_API_TOKEN", "FIN_AUTH_DISABLED")}
        with patch.dict(os.environ, env, clear=True):
            from fin.security import get_signed_session_token
            first = get_signed_session_token()
            second = get_signed_session_token()
            assert first == second


class TestVerifyTokenValueExpiry:
    """Test _verify_token_value() with signed tokens — expiry and tampering."""

    def _reset_module_state(self):
        import fin.security as sec
        sec._session_token = None
        sec._signed_session_token = None

    def test_expired_token_raises_401(self):
        """A token signed 9 hours ago should raise 401 with expiry detail."""
        import time
        import fin.security as sec
        from itsdangerous import TimestampSigner
        from fastapi import HTTPException

        self._reset_module_state()
        env = {k: v for k, v in os.environ.items()
               if k not in ("FIN_API_TOKEN", "FIN_AUTH_DISABLED")}
        with patch.dict(os.environ, env, clear=True):
            from fin.security import get_api_token, _get_secret_key, _verify_token_value

            raw = get_api_token()
            signer = TimestampSigner(_get_secret_key())

            # Sign the token but then advance time by 9 hours when verifying
            stale_token = signer.sign(raw).decode()

            with patch("time.time", return_value=time.time() + 9 * 3600):
                with pytest.raises(HTTPException) as exc:
                    _verify_token_value(stale_token)
            assert exc.value.status_code == 401
            assert "expired" in exc.value.detail.lower()

    def test_tampered_signature_raises_403(self):
        """Tampered token signature should raise 403."""
        import fin.security as sec
        from fastapi import HTTPException

        self._reset_module_state()
        env = {k: v for k, v in os.environ.items()
               if k not in ("FIN_API_TOKEN", "FIN_AUTH_DISABLED")}
        with patch.dict(os.environ, env, clear=True):
            from fin.security import get_signed_session_token, _verify_token_value

            signed = get_signed_session_token()
            # Corrupt the signature suffix
            tampered = signed[:-3] + "xxx"

            with pytest.raises(HTTPException) as exc:
                _verify_token_value(tampered)
            assert exc.value.status_code == 403

    def test_env_token_plain_compare_no_expiry(self):
        """FIN_API_TOKEN is validated by plain compare — never expires server-side."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "static-token"}, clear=True):
            from fin.security import _verify_token_value
            result = _verify_token_value("static-token")
            assert result is True

    def test_env_token_wrong_value_raises_403(self):
        """Wrong value against FIN_API_TOKEN raises 403, not 401."""
        from fastapi import HTTPException
        with patch.dict(os.environ, {"FIN_API_TOKEN": "correct"}, clear=True):
            from fin.security import _verify_token_value
            with pytest.raises(HTTPException) as exc:
                _verify_token_value("wrong")
            assert exc.value.status_code == 403


class TestCookieAuth:
    """Test verify_auth_token() cookie and header precedence."""

    def test_verify_auth_token_accepts_cookie(self):
        """verify_auth_token should accept valid token via fin_session cookie."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "tok"}, clear=True):
            result = verify_auth_token(authorization=None, fin_session="tok")
            assert result is True

    def test_verify_auth_token_no_cookie_no_header_raises_401(self):
        """No credentials at all raises 401."""
        from fastapi import HTTPException
        with patch.dict(os.environ, {"FIN_API_TOKEN": "tok"}, clear=True):
            with pytest.raises(HTTPException) as exc:
                verify_auth_token(authorization=None, fin_session=None)
            assert exc.value.status_code == 401

    def test_bearer_header_takes_precedence_over_cookie(self):
        """Valid Bearer header wins even when cookie is present and wrong."""
        with patch.dict(os.environ, {"FIN_API_TOKEN": "tok"}, clear=True):
            result = verify_auth_token(authorization="Bearer tok", fin_session="wrong")
            assert result is True

    def test_wrong_cookie_raises_403(self):
        """Wrong cookie value raises 403."""
        from fastapi import HTTPException
        with patch.dict(os.environ, {"FIN_API_TOKEN": "tok"}, clear=True):
            with pytest.raises(HTTPException) as exc:
                verify_auth_token(authorization=None, fin_session="wrong-cookie")
            assert exc.value.status_code == 403

    def test_auth_disabled_cookie_ignored(self):
        """When auth is disabled, any cookie is irrelevant — always passes."""
        with patch.dict(os.environ, {"FIN_AUTH_DISABLED": "1"}, clear=True):
            result = verify_auth_token(authorization=None, fin_session=None)
            assert result is True
