"""
Tests for transaction type override API.

Tests:
- Set override by fingerprint
- Set override by merchant pattern
- Remove override
- Get all overrides
"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fin.web import app
from fin import db as dbmod


@pytest.fixture
def test_db_path():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def client(test_db_path):
    """Create test client with initialized database."""
    import fin.web as web_module
    web_module._config = None
    web_module._db_initialized = False

    class MockConfig:
        db_path = test_db_path
        simplefin_access_url = ""
        log_level = "INFO"
        log_format = "simple"

    with patch.object(web_module, "_get_config", return_value=MockConfig()):
        with TestClient(app) as client:
            yield client


class TestTxnTypeOverrideAPI:
    """Test /api/txn-type-override endpoints."""

    def test_set_override_by_fingerprint(self, client):
        """Should set override for specific transaction."""
        resp = client.post("/api/txn-type-override", json={
            "fingerprint": "abc123def456",
            "target_type": "INCOME",
            "reason": "User marked as payroll",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["fingerprint"] == "abc123def456"
        assert data["target_type"] == "INCOME"

    def test_set_override_by_merchant(self, client):
        """Should set override for merchant pattern."""
        resp = client.post("/api/txn-type-override", json={
            "merchant_pattern": "internal transfer",
            "target_type": "TRANSFER",
        })

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["merchant_pattern"] == "internal transfer"
        assert data["target_type"] == "TRANSFER"

    def test_invalid_target_type(self, client):
        """Should reject invalid target type."""
        resp = client.post("/api/txn-type-override", json={
            "fingerprint": "abc123",
            "target_type": "INVALID",
        })

        assert resp.status_code == 400
        assert "Invalid target_type" in resp.json()["error"]

    def test_missing_identifier(self, client):
        """Should require fingerprint or merchant_pattern."""
        resp = client.post("/api/txn-type-override", json={
            "target_type": "INCOME",
        })

        assert resp.status_code == 400
        assert "Must specify" in resp.json()["error"]

    def test_both_identifiers(self, client):
        """Should reject both fingerprint and merchant_pattern."""
        resp = client.post("/api/txn-type-override", json={
            "fingerprint": "abc123",
            "merchant_pattern": "payroll",
            "target_type": "INCOME",
        })

        assert resp.status_code == 400
        assert "not both" in resp.json()["error"]

    def test_get_overrides(self, client):
        """Should list all overrides."""
        # Create some overrides
        client.post("/api/txn-type-override", json={
            "fingerprint": "fp1",
            "target_type": "INCOME",
        })
        client.post("/api/txn-type-override", json={
            "merchant_pattern": "transfer",
            "target_type": "TRANSFER",
        })

        # Get all overrides
        resp = client.get("/api/txn-type-overrides")

        assert resp.status_code == 200
        data = resp.json()

        assert len(data["fingerprint_overrides"]) == 1
        assert data["fingerprint_overrides"][0]["fingerprint"] == "fp1"
        assert data["fingerprint_overrides"][0]["target_type"] == "INCOME"

        assert len(data["merchant_overrides"]) == 1
        assert data["merchant_overrides"][0]["merchant_pattern"] == "transfer"
        assert data["merchant_overrides"][0]["target_type"] == "TRANSFER"

    def test_remove_override_by_fingerprint(self, client):
        """Should remove fingerprint override."""
        # Create override
        client.post("/api/txn-type-override", json={
            "fingerprint": "fp_to_delete",
            "target_type": "INCOME",
        })

        # Verify it exists
        resp = client.get("/api/txn-type-overrides")
        assert len(resp.json()["fingerprint_overrides"]) == 1

        # Remove it
        resp = client.delete("/api/txn-type-override?fingerprint=fp_to_delete")
        assert resp.status_code == 200

        # Verify it's gone
        resp = client.get("/api/txn-type-overrides")
        assert len(resp.json()["fingerprint_overrides"]) == 0

    def test_remove_override_by_merchant(self, client):
        """Should remove merchant pattern override."""
        # Create override
        client.post("/api/txn-type-override", json={
            "merchant_pattern": "payroll",
            "target_type": "INCOME",
        })

        # Remove it
        resp = client.delete("/api/txn-type-override?merchant_pattern=payroll")
        assert resp.status_code == 200

        # Verify it's gone
        resp = client.get("/api/txn-type-overrides")
        assert len(resp.json()["merchant_overrides"]) == 0


class TestOverrideTypes:
    """Test each valid transaction type."""

    @pytest.mark.parametrize("target_type", [
        "INCOME",
        "EXPENSE",
        "TRANSFER",
        "REFUND",
        "CREDIT_OTHER",
    ])
    def test_valid_types(self, client, target_type):
        """All valid types should be accepted."""
        resp = client.post("/api/txn-type-override", json={
            "fingerprint": f"fp_{target_type}",
            "target_type": target_type,
        })

        assert resp.status_code == 200
        assert resp.json()["target_type"] == target_type
