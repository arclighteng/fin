# audit.py
"""
Audit trail and versioning module.

TRUTH CONTRACT:
- All user overrides are logged with timestamp
- Report snapshots can be exported for reproducibility
- Classifier version tracked in every report

Audit events:
- OVERRIDE_SET: User set a classification override
- OVERRIDE_REMOVED: User removed an override
- RECONCILIATION_CREATED: Statement reconciliation recorded
- RECONCILIATION_RESOLVED: Discrepancy marked resolved
- REPORT_EXPORTED: Report snapshot exported
"""
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from .reporting import CLASSIFIER_VERSION, REPORT_VERSION


class AuditEventType(Enum):
    """Types of auditable events."""
    OVERRIDE_SET = "override_set"
    OVERRIDE_REMOVED = "override_removed"
    INCOME_RULE_SET = "income_rule_set"
    INCOME_RULE_REMOVED = "income_rule_removed"
    CATEGORY_OVERRIDE_SET = "category_override_set"
    CATEGORY_OVERRIDE_REMOVED = "category_override_removed"
    RECONCILIATION_CREATED = "reconciliation_created"
    RECONCILIATION_RESOLVED = "reconciliation_resolved"
    REPORT_EXPORTED = "report_exported"
    ALERT_ACTION = "alert_action"
    DUPLICATE_DISMISSED = "duplicate_dismissed"


@dataclass
class AuditEvent:
    """A single audit log entry."""
    id: Optional[int]
    event_type: AuditEventType
    timestamp: datetime
    entity_type: str  # e.g., "transaction", "merchant", "account"
    entity_id: str    # e.g., fingerprint, merchant_norm, account_id
    old_value: Optional[str]
    new_value: Optional[str]
    metadata: Optional[dict]
    classifier_version: str
    report_version: str


def init_audit_tables(conn: sqlite3.Connection) -> None:
    """Create audit tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            metadata TEXT,
            classifier_version TEXT NOT NULL,
            report_version TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp
        ON audit_log(timestamp DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_entity
        ON audit_log(entity_type, entity_id)
    """)
    conn.commit()


def log_audit_event(
    conn: sqlite3.Connection,
    event_type: AuditEventType,
    entity_type: str,
    entity_id: str,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> AuditEvent:
    """
    Log an audit event.

    Args:
        conn: Database connection
        event_type: Type of event
        entity_type: Type of entity being modified
        entity_id: ID of the entity
        old_value: Previous value (if applicable)
        new_value: New value (if applicable)
        metadata: Additional context

    Returns:
        The created AuditEvent
    """
    init_audit_tables(conn)

    now = datetime.now(timezone.utc)
    meta_json = json.dumps(metadata) if metadata else None

    cursor = conn.execute(
        """
        INSERT INTO audit_log
        (event_type, timestamp, entity_type, entity_id, old_value, new_value,
         metadata, classifier_version, report_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type.value,
            now.isoformat(),
            entity_type,
            entity_id,
            old_value,
            new_value,
            meta_json,
            CLASSIFIER_VERSION,
            REPORT_VERSION,
        ),
    )
    conn.commit()

    return AuditEvent(
        id=cursor.lastrowid,
        event_type=event_type,
        timestamp=now,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
        metadata=metadata,
        classifier_version=CLASSIFIER_VERSION,
        report_version=REPORT_VERSION,
    )


def get_audit_log(
    conn: sqlite3.Connection,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    event_type: Optional[AuditEventType] = None,
    since: Optional[datetime] = None,
    limit: int = 100,
) -> list[AuditEvent]:
    """
    Query the audit log with optional filters.

    Args:
        conn: Database connection
        entity_type: Filter by entity type
        entity_id: Filter by entity ID
        event_type: Filter by event type
        since: Only events after this timestamp
        limit: Max number of events to return

    Returns:
        List of AuditEvent objects
    """
    init_audit_tables(conn)

    query = "SELECT * FROM audit_log WHERE 1=1"
    params: list = []

    if entity_type:
        query += " AND entity_type = ?"
        params.append(entity_type)

    if entity_id:
        query += " AND entity_id = ?"
        params.append(entity_id)

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type.value)

    if since:
        query += " AND timestamp >= ?"
        params.append(since.isoformat())

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    return [
        AuditEvent(
            id=r["id"],
            event_type=AuditEventType(r["event_type"]),
            timestamp=datetime.fromisoformat(r["timestamp"]),
            entity_type=r["entity_type"],
            entity_id=r["entity_id"],
            old_value=r["old_value"],
            new_value=r["new_value"],
            metadata=json.loads(r["metadata"]) if r["metadata"] else None,
            classifier_version=r["classifier_version"],
            report_version=r["report_version"],
        )
        for r in rows
    ]


def get_entity_history(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: str,
) -> list[AuditEvent]:
    """Get complete audit history for a specific entity."""
    return get_audit_log(conn, entity_type=entity_type, entity_id=entity_id, limit=1000)


def export_report_snapshot(
    conn: sqlite3.Connection,
    report: "Report",  # Forward reference to avoid circular import
) -> dict:
    """
    Export a report snapshot for reproducibility.

    Includes:
    - Report data
    - Classifier/report versions
    - Active overrides at time of generation
    - Timestamp

    Returns:
        Dict that can be serialized to JSON
    """
    from . import db as dbmod
    from .reporting_models import Report

    # Get current overrides
    fp_overrides, merchant_overrides = dbmod.get_txn_type_overrides(conn)
    income_sources, excluded_sources = dbmod.get_income_rules(conn)
    category_overrides = dbmod.get_category_overrides(conn)

    snapshot = {
        "snapshot_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "classifier_version": CLASSIFIER_VERSION,
        "report_version": REPORT_VERSION,
        "report": {
            "period_label": report.period_label,
            "start_date": report.start_date.isoformat(),
            "end_date": report.end_date.isoformat(),
            "report_hash": report.report_hash,
            "transaction_count": report.transaction_count,
            "pending_count": report.pending_count,
            "totals": {
                "income_cents": report.totals.income_cents,
                "fixed_obligations_cents": report.totals.fixed_obligations_cents,
                "variable_essentials_cents": report.totals.variable_essentials_cents,
                "discretionary_cents": report.totals.discretionary_cents,
                "one_offs_cents": report.totals.one_offs_cents,
                "refunds_cents": report.totals.refunds_cents,
                "credits_other_cents": report.totals.credits_other_cents,
                "transfers_in_cents": report.totals.transfers_in_cents,
                "transfers_out_cents": report.totals.transfers_out_cents,
            },
            "integrity": {
                "score": report.integrity.score,
                "flags": [f.value for f in report.integrity.flags],
                "unclassified_credit_count": report.integrity.unclassified_credit_count,
                "unmatched_transfer_count": report.integrity.unmatched_transfer_count,
            },
        },
        "active_overrides": {
            "fingerprint_overrides": list(fp_overrides.keys()),
            "merchant_overrides": list(merchant_overrides.keys()),
            "income_sources": list(income_sources),
            "excluded_sources": list(excluded_sources),
            "category_overrides": list(category_overrides.keys()),
        },
    }

    # Log the export
    log_audit_event(
        conn,
        AuditEventType.REPORT_EXPORTED,
        "report",
        report.report_hash or "unknown",
        metadata={
            "period_label": report.period_label,
            "transaction_count": report.transaction_count,
        },
    )

    return snapshot


def get_version_info() -> dict:
    """Get current classifier and report version info."""
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "report_version": REPORT_VERSION,
    }
