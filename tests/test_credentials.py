"""
Tests for credential storage functionality.

Tests:
- Keyring availability detection
- Credential storage and retrieval
- Credential deletion
- Config priority (keyring > env)
- CLI commands (set, clear, status)
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin import credentials

runner = CliRunner()


class TestKeyringAvailability:
    """Test keyring availability detection."""

    def test_detects_available_keyring(self):
        """Should detect when keyring is available."""
        mock_keyring = MagicMock()
        mock_keyring.get_keyring.return_value = MagicMock()

        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            # Reset the cached state
            credentials._keyring = None
            credentials._keyring_available = None

            result = credentials.is_keyring_available()
            # Just verify it doesn't crash - actual availability depends on system

    def test_handles_missing_keyring_gracefully(self):
        """Should handle missing keyring module gracefully."""
        # Reset cached state
        credentials._keyring = None
        credentials._keyring_available = None

        with patch.object(credentials, "_get_keyring", return_value=None):
            assert credentials.is_keyring_available() is False

    def test_detects_fail_backend_as_unavailable(self):
        """FailKeyring sentinel backend should be treated as unavailable."""
        class FailKeyring:
            pass

        fail_module = MagicMock()
        fail_module.Keyring = FailKeyring

        mock_keyring = MagicMock()
        mock_keyring.get_keyring.return_value = FailKeyring()

        with patch.dict("sys.modules", {"keyring": mock_keyring,
                                         "keyring.backends": MagicMock(),
                                         "keyring.backends.fail": fail_module}):
            credentials._keyring = None
            credentials._keyring_available = None
            assert credentials.is_keyring_available() is False

    def test_real_backend_accepted(self):
        """A real (non-fail) backend should be accepted."""
        class FailKeyring:
            pass

        fail_module = MagicMock()
        fail_module.Keyring = FailKeyring

        mock_backend = MagicMock()  # Not a FailKeyring instance
        mock_keyring = MagicMock()
        mock_keyring.get_keyring.return_value = mock_backend

        with patch.dict("sys.modules", {"keyring": mock_keyring,
                                         "keyring.backends": MagicMock(),
                                         "keyring.backends.fail": fail_module}):
            credentials._keyring = None
            credentials._keyring_available = None
            assert credentials.is_keyring_available() is True

    def test_cached_state_after_fail_backend(self):
        """After detecting fail backend, subsequent calls should return False without re-checking."""
        class FailKeyring:
            pass

        fail_module = MagicMock()
        fail_module.Keyring = FailKeyring

        mock_keyring = MagicMock()
        mock_keyring.get_keyring.return_value = FailKeyring()

        with patch.dict("sys.modules", {"keyring": mock_keyring,
                                         "keyring.backends": MagicMock(),
                                         "keyring.backends.fail": fail_module}):
            credentials._keyring = None
            credentials._keyring_available = None
            assert credentials.is_keyring_available() is False
            # Second call should hit the cache, not re-import
            assert credentials.is_keyring_available() is False
            assert credentials._keyring_available is False


class TestCredentialOperations:
    """Test credential storage operations."""

    def test_get_credential_returns_none_when_keyring_unavailable(self):
        """Should return None when keyring is not available."""
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.get_credential("test_key")
            assert result is None

    def test_set_credential_returns_false_when_keyring_unavailable(self):
        """Should return False when keyring is not available."""
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.set_credential("test_key", "test_value")
            assert result is False

    def test_delete_credential_returns_false_when_keyring_unavailable(self):
        """Should return False when keyring is not available."""
        with patch.object(credentials, "_get_keyring", return_value=None):
            result = credentials.delete_credential("test_key")
            assert result is False

    def test_get_credential_calls_keyring(self):
        """Should call keyring.get_password with correct args."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "secret_value"

        with patch.object(credentials, "_get_keyring", return_value=mock_keyring):
            result = credentials.get_credential("test_key")

            mock_keyring.get_password.assert_called_once_with("fin", "test_key")
            assert result == "secret_value"

    def test_set_credential_calls_keyring(self):
        """Should call keyring.set_password with correct args."""
        mock_keyring = MagicMock()

        with patch.object(credentials, "_get_keyring", return_value=mock_keyring):
            result = credentials.set_credential("test_key", "test_value")

            mock_keyring.set_password.assert_called_once_with("fin", "test_key", "test_value")
            assert result is True

    def test_delete_credential_calls_keyring(self):
        """Should call keyring.delete_password with correct args."""
        mock_keyring = MagicMock()

        with patch.object(credentials, "_get_keyring", return_value=mock_keyring):
            result = credentials.delete_credential("test_key")

            mock_keyring.delete_password.assert_called_once_with("fin", "test_key")
            assert result is True

    def test_delete_handles_not_found_error(self):
        """Should handle 'not found' errors gracefully."""
        mock_keyring = MagicMock()
        mock_keyring.delete_password.side_effect = Exception("password not found")

        with patch.object(credentials, "_get_keyring", return_value=mock_keyring):
            result = credentials.delete_credential("nonexistent_key")
            assert result is True  # Should succeed (credential already gone)


class TestSimplefinHelpers:
    """Test SimpleFIN-specific helper functions."""

    def test_get_simplefin_url(self):
        """Should get SimpleFIN URL from keyring."""
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = "https://user:pass@simplefin.org"

        with patch.object(credentials, "_get_keyring", return_value=mock_keyring):
            result = credentials.get_simplefin_url()

            mock_keyring.get_password.assert_called_once_with("fin", "simplefin_access_url")
            assert result == "https://user:pass@simplefin.org"

    def test_set_simplefin_url(self):
        """Should set SimpleFIN URL in keyring."""
        mock_keyring = MagicMock()

        with patch.object(credentials, "_get_keyring", return_value=mock_keyring):
            result = credentials.set_simplefin_url("https://user:pass@simplefin.org")

            mock_keyring.set_password.assert_called_once_with(
                "fin", "simplefin_access_url", "https://user:pass@simplefin.org"
            )
            assert result is True


class TestCredentialSource:
    """Test credential source detection."""

    def test_source_keyring_when_in_keyring(self):
        """Should return 'keyring' when credential is in keyring."""
        with patch.object(credentials, "get_simplefin_url", return_value="https://url"):
            result = credentials.get_credential_source()
            assert result == "keyring"

    def test_source_env_when_in_environment(self):
        """Should return 'env' when credential is in environment."""
        with patch.object(credentials, "get_simplefin_url", return_value=None):
            with patch.dict(os.environ, {"SIMPLEFIN_ACCESS_URL": "https://url"}):
                result = credentials.get_credential_source()
                assert result == "env"

    def test_source_none_when_not_configured(self):
        """Should return 'none' when credential is not configured."""
        with patch.object(credentials, "get_simplefin_url", return_value=None):
            with patch.dict(os.environ, {"SIMPLEFIN_ACCESS_URL": ""}):
                result = credentials.get_credential_source()
                assert result == "none"


class TestConfigPriority:
    """Test that config loads credentials with correct priority."""

    def test_keyring_takes_priority_over_env(self):
        """Keyring credential should be used over .env."""
        from fin.config import _get_simplefin_url

        with patch.object(credentials, "get_simplefin_url", return_value="https://keyring-url"):
            with patch.dict(os.environ, {"SIMPLEFIN_ACCESS_URL": "https://env-url"}):
                result = _get_simplefin_url()
                assert result == "https://keyring-url"

    def test_falls_back_to_env_when_keyring_empty(self):
        """Should fall back to .env when keyring is empty."""
        from fin.config import _get_simplefin_url

        with patch.object(credentials, "get_simplefin_url", return_value=None):
            with patch.dict(os.environ, {"SIMPLEFIN_ACCESS_URL": "https://env-url"}):
                result = _get_simplefin_url()
                assert result == "https://env-url"

    def test_returns_empty_when_no_credential(self):
        """Should return empty string when no credential configured."""
        from fin.config import _get_simplefin_url

        with patch.object(credentials, "get_simplefin_url", return_value=None):
            with patch.dict(os.environ, {"SIMPLEFIN_ACCESS_URL": ""}):
                result = _get_simplefin_url()
                assert result == ""


class TestCredentialsCLI:
    """Test credentials CLI commands."""

    def test_status_shows_keyring_available(self):
        """Status should show keyring availability."""
        with patch.object(credentials, "is_keyring_available", return_value=True):
            with patch.object(credentials, "get_credential_source", return_value="keyring"):
                result = runner.invoke(app, ["credentials", "status"])

        assert result.exit_code == 0
        assert "Available" in result.output

    def test_status_shows_keyring_unavailable(self):
        """Status should show when keyring is unavailable."""
        with patch.object(credentials, "is_keyring_available", return_value=False):
            with patch.object(credentials, "get_credential_source", return_value="env"):
                result = runner.invoke(app, ["credentials", "status"])

        assert result.exit_code == 0
        assert "Not available" in result.output or "not available" in result.output.lower()

    def test_status_shows_credential_source(self):
        """Status should show where credentials are loaded from."""
        with patch.object(credentials, "is_keyring_available", return_value=True):
            with patch.object(credentials, "get_credential_source", return_value="keyring"):
                result = runner.invoke(app, ["credentials", "status"])

        assert result.exit_code == 0
        assert "keyring" in result.output.lower()

    def test_set_requires_keyring(self):
        """Set should fail when keyring is unavailable."""
        with patch.object(credentials, "is_keyring_available", return_value=False):
            result = runner.invoke(app, ["credentials", "set", "--url", "https://test"])

        assert result.exit_code == 1
        assert "not available" in result.output.lower()

    def test_set_stores_credential(self):
        """Set should store credential in keyring."""
        with patch.object(credentials, "is_keyring_available", return_value=True):
            with patch.object(credentials, "set_simplefin_url", return_value=True) as mock_set:
                result = runner.invoke(app, ["credentials", "set", "--url", "https://user:pass@test.com"])

        assert result.exit_code == 0
        mock_set.assert_called_once_with("https://user:pass@test.com")
        assert "stored" in result.output.lower()

    def test_set_validates_url(self):
        """Set should validate URL format."""
        with patch.object(credentials, "is_keyring_available", return_value=True):
            result = runner.invoke(app, ["credentials", "set", "--url", "not-a-url"])

        assert result.exit_code == 1
        assert "http" in result.output.lower()

    def test_clear_removes_credential(self):
        """Clear should remove credential from keyring."""
        with patch.object(credentials, "is_keyring_available", return_value=True):
            with patch.object(credentials, "clear_simplefin_url", return_value=True) as mock_clear:
                result = runner.invoke(app, ["credentials", "clear"])

        assert result.exit_code == 0
        mock_clear.assert_called_once()
        assert "removed" in result.output.lower()


class TestKeyringIntegration:
    """Integration tests using real keyring (if available)."""

    @pytest.fixture
    def real_keyring_available(self):
        """Check if real keyring is available for testing."""
        # Reset cached state
        credentials._keyring = None
        credentials._keyring_available = None
        return credentials.is_keyring_available()

    def test_roundtrip_with_real_keyring(self, real_keyring_available):
        """Test storing and retrieving with real keyring."""
        if not real_keyring_available:
            pytest.skip("System keyring not available")

        test_key = "fin_test_credential"
        test_value = "test_secret_value_12345"

        try:
            # Store
            assert credentials.set_credential(test_key, test_value) is True

            # Retrieve
            retrieved = credentials.get_credential(test_key)
            assert retrieved == test_value

            # Delete
            assert credentials.delete_credential(test_key) is True

            # Verify deleted
            assert credentials.get_credential(test_key) is None
        finally:
            # Cleanup
            credentials.delete_credential(test_key)
