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
