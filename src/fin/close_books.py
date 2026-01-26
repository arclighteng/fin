# close_books.py
"""
Close-the-books functionality for period finalization.

TRUTH CONTRACT:
- A closed period has a snapshot of totals that becomes the "official" numbers
- Any new transactions landing in a closed period are "post-close adjustments"
- Adjustments are surfaced explicitly, never silently mixed into closed totals
- Users can re-close a period after reviewing adjustments

This creates the "trust flywheel" where reconciled periods stay stable.
"""
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from .report_service import ReportService
from .reporting_models import Report, PeriodTotals


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def init_close_books_schema(conn: sqlite3.Connection) -> None:
    """Initialize close-the-books tables."""

    # Closed periods - snapshots of finalized periods
    conn.execute("""
        CREATE TABLE IF NOT EXISTS closed_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            closed_by TEXT,  -- user identifier if available

            -- Snapshot of totals at close time
            income_cents INTEGER NOT NULL,
            fixed_obligations_cents INTEGER NOT NULL,
            variable_essentials_cents INTEGER NOT NULL,
            discretionary_cents INTEGER NOT NULL,
            one_offs_cents INTEGER NOT NULL,
            refunds_cents INTEGER NOT NULL,
            credits_other_cents INTEGER NOT NULL,
            transfers_in_cents INTEGER NOT NULL,
            transfers_out_cents INTEGER NOT NULL,

            -- Integrity snapshot
            report_hash TEXT NOT NULL,
            snapshot_id TEXT NOT NULL,
            transaction_count INTEGER NOT NULL,

            -- Account filter (JSON array or null for all)
            account_filter TEXT,

            -- Status
            status TEXT NOT NULL DEFAULT 'closed',  -- closed, superseded

            -- Notes
            notes TEXT,

            UNIQUE(start_date, end_date, account_filter)
        )
    """)

    # Post-close adjustments - transactions that landed after close
    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_close_adjustments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            closed_period_id INTEGER NOT NULL REFERENCES closed_periods(id),
            fingerprint TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            adjustment_type TEXT NOT NULL,  -- 'new_txn', 'modified_txn', 'deleted_txn'

            -- Transaction details at detection
            posted_at TEXT,
            amount_cents INTEGER,
            merchant_norm TEXT,
            description TEXT,

            -- Resolution
            status TEXT NOT NULL DEFAULT 'pending',  -- pending, acknowledged, incorporated
            resolved_at TEXT,
            resolved_by TEXT,
            resolution_notes TEXT,

            UNIQUE(closed_period_id, fingerprint)
        )
    """)

    # Matched statement transactions - user-confirmed matches
    conn.execute("""
        CREATE TABLE IF NOT EXISTS statement_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_id INTEGER,  -- Reference to statement if we have one
            fingerprint TEXT NOT NULL,
            matched_at TEXT NOT NULL,
            matched_by TEXT,
            confidence TEXT NOT NULL DEFAULT 'user_confirmed',  -- auto, user_confirmed

            -- Statement line details (for verification)
            statement_date TEXT,
            statement_amount_cents INTEGER,
            statement_description TEXT,

            UNIQUE(fingerprint)
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_closed_periods_dates
        ON closed_periods(start_date, end_date)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_adjustments_period
        ON post_close_adjustments(closed_period_id, status)
    """)

    conn.commit()


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------
@dataclass
class ClosedPeriod:
    """A finalized period with snapshot."""
    id: int
    start_date: date
    end_date: date
    closed_at: datetime
    closed_by: Optional[str]

    # Snapshot totals
    income_cents: int
    fixed_obligations_cents: int
    variable_essentials_cents: int
    discretionary_cents: int
    one_offs_cents: int
    refunds_cents: int
    credits_other_cents: int
    transfers_in_cents: int
    transfers_out_cents: int

    # Integrity
    report_hash: str
    snapshot_id: str
    transaction_count: int

    # Filter
    account_filter: Optional[list[str]]

    # Status
    status: str
    notes: Optional[str]

    @property
    def total_expenses_cents(self) -> int:
        return (
            self.fixed_obligations_cents +
            self.variable_essentials_cents +
            self.discretionary_cents +
            self.one_offs_cents
        )

    @property
    def net_cents(self) -> int:
        return self.income_cents + self.refunds_cents - self.total_expenses_cents


@dataclass
class PostCloseAdjustment:
    """A transaction that landed after period close."""
    id: int
    closed_period_id: int
    fingerprint: str
    detected_at: datetime
    adjustment_type: str

    posted_at: Optional[date]
    amount_cents: Optional[int]
    merchant_norm: Optional[str]
    description: Optional[str]

    status: str
    resolved_at: Optional[datetime]
    resolved_by: Optional[str]
    resolution_notes: Optional[str]


@dataclass
class AdjustmentSummary:
    """Summary of adjustments for a closed period."""
    closed_period: ClosedPeriod
    pending_adjustments: list[PostCloseAdjustment]
    total_adjustment_cents: int
    adjusted_net_cents: int

    @property
    def has_pending(self) -> bool:
        return len(self.pending_adjustments) > 0

    @property
    def net_change_cents(self) -> int:
        return self.adjusted_net_cents - self.closed_period.net_cents


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------
def close_period(
    conn: sqlite3.Connection,
    start_date: date,
    end_date: date,
    account_filter: Optional[list[str]] = None,
    closed_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> ClosedPeriod:
    """
    Close a period, creating a snapshot of the current report state.

    This is the "close the books" action that creates an official record.

    Args:
        conn: Database connection
        start_date: Period start (inclusive)
        end_date: Period end (exclusive)
        account_filter: Optional account filter
        closed_by: User identifier
        notes: Optional notes about the close

    Returns:
        ClosedPeriod with snapshot
    """
    init_close_books_schema(conn)

    # Generate the current report
    service = ReportService(conn)
    report = service.report_period(start_date, end_date, account_filter=account_filter)

    # Serialize account filter
    filter_json = json.dumps(sorted(account_filter)) if account_filter else None

    # Check if period already closed
    existing = conn.execute(
        """
        SELECT id FROM closed_periods
        WHERE start_date = ? AND end_date = ? AND account_filter IS ?
        AND status = 'closed'
        """,
        (start_date.isoformat(), end_date.isoformat(), filter_json),
    ).fetchone()

    if existing:
        # Mark existing as superseded
        conn.execute(
            "UPDATE closed_periods SET status = 'superseded' WHERE id = ?",
            (existing["id"],),
        )

    now = datetime.now().isoformat()

    # Insert new closed period
    cursor = conn.execute(
        """
        INSERT INTO closed_periods (
            start_date, end_date, closed_at, closed_by,
            income_cents, fixed_obligations_cents, variable_essentials_cents,
            discretionary_cents, one_offs_cents, refunds_cents, credits_other_cents,
            transfers_in_cents, transfers_out_cents,
            report_hash, snapshot_id, transaction_count,
            account_filter, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            start_date.isoformat(),
            end_date.isoformat(),
            now,
            closed_by,
            report.totals.income_cents,
            report.totals.fixed_obligations_cents,
            report.totals.variable_essentials_cents,
            report.totals.discretionary_cents,
            report.totals.one_offs_cents,
            report.totals.refunds_cents,
            report.totals.credits_other_cents,
            report.totals.transfers_in_cents,
            report.totals.transfers_out_cents,
            report.report_hash,
            report.snapshot_id or "",
            report.transaction_count,
            filter_json,
            "closed",
            notes,
        ),
    )

    period_id = cursor.lastrowid
    conn.commit()

    return ClosedPeriod(
        id=period_id,
        start_date=start_date,
        end_date=end_date,
        closed_at=datetime.fromisoformat(now),
        closed_by=closed_by,
        income_cents=report.totals.income_cents,
        fixed_obligations_cents=report.totals.fixed_obligations_cents,
        variable_essentials_cents=report.totals.variable_essentials_cents,
        discretionary_cents=report.totals.discretionary_cents,
        one_offs_cents=report.totals.one_offs_cents,
        refunds_cents=report.totals.refunds_cents,
        credits_other_cents=report.totals.credits_other_cents,
        transfers_in_cents=report.totals.transfers_in_cents,
        transfers_out_cents=report.totals.transfers_out_cents,
        report_hash=report.report_hash,
        snapshot_id=report.snapshot_id or "",
        transaction_count=report.transaction_count,
        account_filter=account_filter,
        status="closed",
        notes=notes,
    )


def get_closed_period(
    conn: sqlite3.Connection,
    start_date: date,
    end_date: date,
    account_filter: Optional[list[str]] = None,
) -> Optional[ClosedPeriod]:
    """Get the closed period for a date range, if any."""
    init_close_books_schema(conn)

    filter_json = json.dumps(sorted(account_filter)) if account_filter else None

    row = conn.execute(
        """
        SELECT * FROM closed_periods
        WHERE start_date = ? AND end_date = ? AND account_filter IS ?
        AND status = 'closed'
        """,
        (start_date.isoformat(), end_date.isoformat(), filter_json),
    ).fetchone()

    if not row:
        return None

    return _row_to_closed_period(row)


def get_all_closed_periods(conn: sqlite3.Connection) -> list[ClosedPeriod]:
    """Get all closed periods, most recent first."""
    init_close_books_schema(conn)

    rows = conn.execute(
        """
        SELECT * FROM closed_periods
        WHERE status = 'closed'
        ORDER BY closed_at DESC
        """
    ).fetchall()

    return [_row_to_closed_period(r) for r in rows]


def detect_post_close_adjustments(
    conn: sqlite3.Connection,
    closed_period: ClosedPeriod,
) -> list[PostCloseAdjustment]:
    """
    Detect transactions that landed after a period was closed.

    This compares the current state to the closed snapshot.

    Returns:
        List of new adjustments detected
    """
    # Get current transactions in the period
    current_fps = set()
    current_txns = {}

    rows = conn.execute(
        """
        SELECT fingerprint, posted_at, amount_cents,
               TRIM(LOWER(COALESCE(NULLIF(merchant,''), NULLIF(description,''), ''))) AS merchant_norm,
               COALESCE(description, merchant, '') AS description
        FROM transactions
        WHERE posted_at >= ? AND posted_at < ?
        AND COALESCE(pending, 0) = 0
        """,
        (closed_period.start_date.isoformat(), closed_period.end_date.isoformat()),
    ).fetchall()

    for r in rows:
        fp = r["fingerprint"]
        current_fps.add(fp)
        current_txns[fp] = r

    # Get fingerprints that were in the closed period
    # We'll need to track these - for now, detect new ones
    existing_adj_fps = set()
    for row in conn.execute(
        "SELECT fingerprint FROM post_close_adjustments WHERE closed_period_id = ?",
        (closed_period.id,),
    ).fetchall():
        existing_adj_fps.add(row["fingerprint"])

    # Detect new transactions (not yet recorded as adjustments)
    new_adjustments = []
    now = datetime.now().isoformat()

    # Simple heuristic: if transaction count increased, we have new txns
    # More sophisticated: track all fingerprints at close time
    if len(current_fps) > closed_period.transaction_count:
        # Find candidates - transactions not yet recorded
        for fp, txn in current_txns.items():
            if fp in existing_adj_fps:
                continue

            # This is a potential new transaction
            # For now, add all as potential adjustments
            # A smarter approach would track fingerprints at close time
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO post_close_adjustments (
                    closed_period_id, fingerprint, detected_at, adjustment_type,
                    posted_at, amount_cents, merchant_norm, description, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    closed_period.id,
                    fp,
                    now,
                    "new_txn",
                    txn["posted_at"],
                    txn["amount_cents"],
                    txn["merchant_norm"],
                    txn["description"],
                    "pending",
                ),
            )

            if cursor.rowcount > 0:
                new_adjustments.append(PostCloseAdjustment(
                    id=cursor.lastrowid,
                    closed_period_id=closed_period.id,
                    fingerprint=fp,
                    detected_at=datetime.fromisoformat(now),
                    adjustment_type="new_txn",
                    posted_at=datetime.fromisoformat(txn["posted_at"]).date() if txn["posted_at"] else None,
                    amount_cents=txn["amount_cents"],
                    merchant_norm=txn["merchant_norm"],
                    description=txn["description"],
                    status="pending",
                    resolved_at=None,
                    resolved_by=None,
                    resolution_notes=None,
                ))

    conn.commit()
    return new_adjustments


def get_pending_adjustments(
    conn: sqlite3.Connection,
    closed_period_id: Optional[int] = None,
) -> list[PostCloseAdjustment]:
    """Get pending adjustments, optionally filtered by period."""
    init_close_books_schema(conn)

    if closed_period_id:
        rows = conn.execute(
            """
            SELECT * FROM post_close_adjustments
            WHERE closed_period_id = ? AND status = 'pending'
            ORDER BY detected_at DESC
            """,
            (closed_period_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM post_close_adjustments
            WHERE status = 'pending'
            ORDER BY detected_at DESC
            """
        ).fetchall()

    return [_row_to_adjustment(r) for r in rows]


def acknowledge_adjustment(
    conn: sqlite3.Connection,
    adjustment_id: int,
    resolved_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """Acknowledge an adjustment (user has seen it, accepts it)."""
    now = datetime.now().isoformat()

    conn.execute(
        """
        UPDATE post_close_adjustments
        SET status = 'acknowledged', resolved_at = ?, resolved_by = ?, resolution_notes = ?
        WHERE id = ?
        """,
        (now, resolved_by, notes, adjustment_id),
    )
    conn.commit()


def get_adjustment_summary(
    conn: sqlite3.Connection,
    closed_period: ClosedPeriod,
) -> AdjustmentSummary:
    """Get a summary of adjustments for a closed period."""
    # Get pending adjustments
    pending = get_pending_adjustments(conn, closed_period.id)

    # Calculate total adjustment
    total_adjustment = sum(a.amount_cents or 0 for a in pending)

    # Calculate what the net would be with adjustments
    adjusted_net = closed_period.net_cents + total_adjustment

    return AdjustmentSummary(
        closed_period=closed_period,
        pending_adjustments=pending,
        total_adjustment_cents=total_adjustment,
        adjusted_net_cents=adjusted_net,
    )


def check_for_adjustments_on_ingest(
    conn: sqlite3.Connection,
) -> dict[int, list[PostCloseAdjustment]]:
    """
    Check all closed periods for new adjustments.

    Call this after a sync/ingest to detect any transactions
    that landed in closed periods.

    Returns:
        Dict mapping closed_period_id to list of new adjustments
    """
    init_close_books_schema(conn)

    all_adjustments = {}
    for period in get_all_closed_periods(conn):
        new_adj = detect_post_close_adjustments(conn, period)
        if new_adj:
            all_adjustments[period.id] = new_adj

    return all_adjustments


# ---------------------------------------------------------------------------
# Statement Matching
# ---------------------------------------------------------------------------
def save_statement_match(
    conn: sqlite3.Connection,
    fingerprint: str,
    statement_date: Optional[date] = None,
    statement_amount_cents: Optional[int] = None,
    statement_description: Optional[str] = None,
    matched_by: Optional[str] = None,
    confidence: str = "user_confirmed",
) -> None:
    """Save a user-confirmed statement-to-transaction match."""
    init_close_books_schema(conn)

    now = datetime.now().isoformat()

    conn.execute(
        """
        INSERT INTO statement_matches (
            fingerprint, matched_at, matched_by, confidence,
            statement_date, statement_amount_cents, statement_description
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            matched_at = excluded.matched_at,
            matched_by = excluded.matched_by,
            confidence = excluded.confidence,
            statement_date = excluded.statement_date,
            statement_amount_cents = excluded.statement_amount_cents,
            statement_description = excluded.statement_description
        """,
        (
            fingerprint,
            now,
            matched_by,
            confidence,
            statement_date.isoformat() if statement_date else None,
            statement_amount_cents,
            statement_description,
        ),
    )
    conn.commit()


def get_matched_transactions(conn: sqlite3.Connection) -> set[str]:
    """Get fingerprints of all statement-matched transactions."""
    init_close_books_schema(conn)

    rows = conn.execute("SELECT fingerprint FROM statement_matches").fetchall()
    return {r["fingerprint"] for r in rows}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _row_to_closed_period(row) -> ClosedPeriod:
    """Convert database row to ClosedPeriod."""
    account_filter = json.loads(row["account_filter"]) if row["account_filter"] else None

    return ClosedPeriod(
        id=row["id"],
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]),
        closed_at=datetime.fromisoformat(row["closed_at"]),
        closed_by=row["closed_by"],
        income_cents=row["income_cents"],
        fixed_obligations_cents=row["fixed_obligations_cents"],
        variable_essentials_cents=row["variable_essentials_cents"],
        discretionary_cents=row["discretionary_cents"],
        one_offs_cents=row["one_offs_cents"],
        refunds_cents=row["refunds_cents"],
        credits_other_cents=row["credits_other_cents"],
        transfers_in_cents=row["transfers_in_cents"],
        transfers_out_cents=row["transfers_out_cents"],
        report_hash=row["report_hash"],
        snapshot_id=row["snapshot_id"],
        transaction_count=row["transaction_count"],
        account_filter=account_filter,
        status=row["status"],
        notes=row["notes"],
    )


def _row_to_adjustment(row) -> PostCloseAdjustment:
    """Convert database row to PostCloseAdjustment."""
    return PostCloseAdjustment(
        id=row["id"],
        closed_period_id=row["closed_period_id"],
        fingerprint=row["fingerprint"],
        detected_at=datetime.fromisoformat(row["detected_at"]),
        adjustment_type=row["adjustment_type"],
        posted_at=date.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
        amount_cents=row["amount_cents"],
        merchant_norm=row["merchant_norm"],
        description=row["description"],
        status=row["status"],
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        resolved_by=row["resolved_by"],
        resolution_notes=row["resolution_notes"],
    )
