"""
Tests for SimpleFIN setup and authentication.

Tests:
- Setup token claim validation
- Invalid token handling
- Access URL format validation
"""
import base64
from unittest.mock import patch, MagicMock

import pytest
import httpx

from fin.simplefin_client import claim_access_url


class TestClaimAccessUrl:
    """Test the SimpleFIN setup token claim process."""

    def test_rejects_invalid_base64(self):
        """Should reject non-base64 input."""
        with pytest.raises(ValueError) as exc_info:
            claim_access_url("not-valid-base64!!!")
        assert "Invalid setup token" in str(exc_info.value)
        assert "base64" in str(exc_info.value).lower()

    def test_rejects_non_url_token(self):
        """Should reject base64 that doesn't decode to a URL."""
        token = base64.b64encode(b"not-a-url").decode()
        with pytest.raises(ValueError) as exc_info:
            claim_access_url(token)
        assert "Invalid setup token" in str(exc_info.value)
        assert "URL" in str(exc_info.value)

    def test_handles_already_claimed_token(self):
        """Should give clear error for already-claimed tokens (403)."""
        token = base64.b64encode(b"https://example.com/claim/ABC123").decode()

        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch("fin.simplefin_client.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(ValueError) as exc_info:
                claim_access_url(token)

            assert "already been claimed" in str(exc_info.value)

    def test_successful_claim_returns_access_url(self):
        """Should return access URL on successful claim."""
        token = base64.b64encode(b"https://example.com/claim/ABC123").decode()
        expected_access_url = "https://user:pass@example.com/simplefin"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = expected_access_url
        mock_response.raise_for_status = MagicMock()

        with patch("fin.simplefin_client.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = claim_access_url(token)

            assert result == expected_access_url
            mock_instance.post.assert_called_once_with("https://example.com/claim/ABC123")

    def test_validates_access_url_format(self):
        """Should reject unexpected response format."""
        token = base64.b64encode(b"https://example.com/claim/ABC123").decode()

        # Response that doesn't look like an access URL
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "some-random-text"
        mock_response.raise_for_status = MagicMock()

        with patch("fin.simplefin_client.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(ValueError) as exc_info:
                claim_access_url(token)

            assert "Unexpected response" in str(exc_info.value)


class TestSetupTokenFormats:
    """Test various setup token formats."""

    @pytest.mark.parametrize("claim_url", [
        "https://beta-bridge.simplefin.org/simplefin/claim/ABC123",
        "https://bridge.simplefin.org/simplefin/claim/XYZ789",
        "https://example.com/claim/test-token",
    ])
    def test_accepts_various_claim_urls(self, claim_url):
        """Should accept various valid claim URL formats."""
        token = base64.b64encode(claim_url.encode()).decode()
        expected_access_url = "https://user:pass@example.com/simplefin"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = expected_access_url
        mock_response.raise_for_status = MagicMock()

        with patch("fin.simplefin_client.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = claim_access_url(token)

            assert result == expected_access_url

    def test_strips_whitespace_from_access_url(self):
        """Should strip whitespace from returned access URL."""
        token = base64.b64encode(b"https://example.com/claim/ABC123").decode()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "  https://user:pass@example.com/simplefin  \n"
        mock_response.raise_for_status = MagicMock()

        with patch("fin.simplefin_client.httpx.Client") as mock_client:
            mock_instance = MagicMock()
            mock_instance.post.return_value = mock_response
            mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
            mock_client.return_value.__exit__ = MagicMock(return_value=False)

            result = claim_access_url(token)

            assert result == "https://user:pass@example.com/simplefin"
            assert not result.startswith(" ")
            assert not result.endswith("\n")
