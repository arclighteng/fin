# versioning.py
"""
Version and snapshot tracking for reproducible reports.

TRUTH CONTRACT:
- Every report includes version info for reproducibility
- snapshot_id uniquely identifies the data state at report time
- Versions change when classification logic changes
"""
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# Bump these when classification logic changes
CLASSIFIER_VERSION = "1.0.0"
REPORT_VERSION = "1.0.0"


@dataclass
class SnapshotInfo:
    """Information about the data snapshot used for a report."""
    snapshot_id: str
    transaction_count: int
    latest_posted_at: Optional[str]
    latest_ingest_run: Optional[int]
    computed_at: str


def compute_snapshot_id(conn: sqlite3.Connection) -> SnapshotInfo:
    """
    Compute a snapshot ID that uniquely identifies the current data state.

    The snapshot_id changes when:
    - Transactions are added, modified, or deleted
    - New ingest runs occur

    Returns:
        SnapshotInfo with hash and metadata
    """
    # Get transaction count and latest posted_at
    row = conn.execute("""
        SELECT
            COUNT(*) as cnt,
            MAX(posted_at) as latest_posted,
            MAX(COALESCE(updated_at, posted_at)) as latest_updated
        FROM transactions
    """).fetchone()

    txn_count = row["cnt"] or 0
    latest_posted = row["latest_posted"]
    latest_updated = row["latest_updated"]

    # Get latest ingest run if the table exists
    latest_ingest_run = None
    try:
        ingest_row = conn.execute(
            "SELECT MAX(id) as latest FROM ingest_runs"
        ).fetchone()
        latest_ingest_run = ingest_row["latest"] if ingest_row else None
    except sqlite3.OperationalError:
        pass  # Table doesn't exist

    # Compute hash from key values
    hash_input = f"{txn_count}|{latest_posted}|{latest_updated}|{latest_ingest_run}"
    snapshot_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:12]

    return SnapshotInfo(
        snapshot_id=snapshot_hash,
        transaction_count=txn_count,
        latest_posted_at=latest_posted,
        latest_ingest_run=latest_ingest_run,
        computed_at=datetime.now().isoformat(),
    )


def get_version_info() -> dict:
    """Get current version information."""
    return {
        "classifier_version": CLASSIFIER_VERSION,
        "report_version": REPORT_VERSION,
    }
