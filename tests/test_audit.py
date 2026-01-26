"""
Tests for audit trail and versioning.

TRUTH CONTRACT verification:
- All overrides are logged
- Report snapshots are exportable
- Version info is tracked
"""
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from fin.audit import (
    AuditEventType,
    init_audit_tables,
    log_audit_event,
    get_audit_log,
    get_entity_history,
    get_version_info,
)
from fin import db as dbmod


@pytest.fixture
def test_db():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = dbmod.connect(path)
    dbmod.init_db(conn)
    init_audit_tables(conn)

    yield conn, path

    conn.close()
    Path(path).unlink(missing_ok=True)


class TestAuditLogging:
    """Test audit event logging."""

    def test_log_event(self, test_db):
        """Should log an audit event."""
        conn, _ = test_db

        event = log_audit_event(
            conn,
            AuditEventType.OVERRIDE_SET,
            entity_type="merchant",
            entity_id="netflix",
            old_value=None,
            new_value="INCOME",
            metadata={"reason": "User marked as income"},
        )

        assert event.id is not None
        assert event.event_type == AuditEventType.OVERRIDE_SET
        assert event.entity_type == "merchant"
        assert event.entity_id == "netflix"
        assert event.new_value == "INCOME"
        assert event.metadata["reason"] == "User marked as income"

    def test_log_multiple_events(self, test_db):
        """Should log multiple events."""
        conn, _ = test_db

        log_audit_event(conn, AuditEventType.OVERRIDE_SET, "merchant", "netflix", new_value="INCOME")
        log_audit_event(conn, AuditEventType.OVERRIDE_SET, "merchant", "spotify", new_value="EXPENSE")
        log_audit_event(conn, AuditEventType.OVERRIDE_REMOVED, "merchant", "netflix")

        events = get_audit_log(conn)
        assert len(events) == 3

    def test_version_tracking(self, test_db):
        """Should track classifier version."""
        conn, _ = test_db

        event = log_audit_event(
            conn,
            AuditEventType.RECONCILIATION_CREATED,
            "account",
            "checking",
        )

        assert event.classifier_version is not None
        assert event.report_version is not None


class TestAuditQuery:
    """Test audit log querying."""

    def test_filter_by_entity_type(self, test_db):
        """Should filter by entity type."""
        conn, _ = test_db

        log_audit_event(conn, AuditEventType.OVERRIDE_SET, "merchant", "netflix")
        log_audit_event(conn, AuditEventType.RECONCILIATION_CREATED, "account", "checking")

        events = get_audit_log(conn, entity_type="merchant")
        assert len(events) == 1
        assert events[0].entity_type == "merchant"

    def test_filter_by_event_type(self, test_db):
        """Should filter by event type."""
        conn, _ = test_db

        log_audit_event(conn, AuditEventType.OVERRIDE_SET, "merchant", "netflix")
        log_audit_event(conn, AuditEventType.OVERRIDE_REMOVED, "merchant", "netflix")

        events = get_audit_log(conn, event_type=AuditEventType.OVERRIDE_SET)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.OVERRIDE_SET

    def test_get_entity_history(self, test_db):
        """Should get complete history for an entity."""
        conn, _ = test_db

        log_audit_event(conn, AuditEventType.OVERRIDE_SET, "merchant", "netflix", new_value="INCOME")
        log_audit_event(conn, AuditEventType.OVERRIDE_REMOVED, "merchant", "netflix")
        log_audit_event(conn, AuditEventType.OVERRIDE_SET, "merchant", "spotify", new_value="EXPENSE")

        history = get_entity_history(conn, "merchant", "netflix")
        assert len(history) == 2
        assert all(e.entity_id == "netflix" for e in history)

    def test_limit_results(self, test_db):
        """Should respect limit parameter."""
        conn, _ = test_db

        for i in range(10):
            log_audit_event(conn, AuditEventType.OVERRIDE_SET, "merchant", f"merchant_{i}")

        events = get_audit_log(conn, limit=5)
        assert len(events) == 5


class TestVersionInfo:
    """Test version info retrieval."""

    def test_get_version_info(self):
        """Should return version info."""
        info = get_version_info()

        assert "classifier_version" in info
        assert "report_version" in info
        assert info["classifier_version"] is not None
        assert info["report_version"] is not None
