# reconciliation.py
"""
Statement reconciliation module.

TRUTH CONTRACT:
- Compare calculated balances against official statement totals
- Flag discrepancies for resolution
- Track reconciliation history for audit

Reconciliation process:
1. User enters statement ending balance and date from bank statement
2. System calculates sum of all transactions up to that date
3. Delta = statement_balance - calculated_balance
4. If delta > threshold, flag for investigation
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional


class ReconciliationStatus(Enum):
    """Status of a reconciliation attempt."""
    MATCHED = "matched"           # Delta within threshold
    DISCREPANCY = "discrepancy"   # Delta exceeds threshold
    PENDING = "pending"           # User needs to review
    RESOLVED = "resolved"         # User marked as resolved


@dataclass
class ReconciliationEvent:
    """A reconciliation attempt for an account."""
    id: Optional[int]
    account_id: str
    statement_date: date
    statement_balance_cents: int  # Ending balance from statement
    calculated_balance_cents: int  # Sum of transactions
    delta_cents: int  # statement - calculated
    status: ReconciliationStatus
    notes: Optional[str]
    created_at: datetime
    resolved_at: Optional[datetime] = None

    @property
    def is_matched(self) -> bool:
        """Check if reconciliation is within tolerance."""
        return abs(self.delta_cents) <= 100  # $1 tolerance


@dataclass
class ReconciliationResult:
    """Result of computing reconciliation for an account."""
    account_id: str
    account_name: str
    statement_date: date
    statement_balance_cents: int
    calculated_balance_cents: int
    delta_cents: int
    transaction_count: int
    first_transaction_date: Optional[date]
    last_transaction_date: Optional[date]

    @property
    def is_matched(self) -> bool:
        return abs(self.delta_cents) <= 100

    @property
    def delta_direction(self) -> str:
        """Human-readable delta direction."""
        if self.delta_cents > 0:
            return "missing_income"  # Statement shows more than we have
        elif self.delta_cents < 0:
            return "missing_expense"  # Statement shows less than we have
        return "balanced"


def init_reconciliation_tables(conn: sqlite3.Connection) -> None:
    """Create reconciliation tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reconciliation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            statement_date TEXT NOT NULL,
            statement_balance_cents INTEGER NOT NULL,
            calculated_balance_cents INTEGER NOT NULL,
            delta_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            UNIQUE(account_id, statement_date)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_recon_account
        ON reconciliation_events(account_id)
    """)
    conn.commit()


def compute_account_balance(
    conn: sqlite3.Connection,
    account_id: str,
    as_of_date: date,
) -> tuple[int, int, Optional[date], Optional[date]]:
    """
    Compute running balance for an account up to a date.

    Returns:
        (balance_cents, transaction_count, first_date, last_date)
    """
    result = conn.execute(
        """
        SELECT
            COALESCE(SUM(amount_cents), 0) as balance,
            COUNT(*) as txn_count,
            MIN(posted_at) as first_date,
            MAX(posted_at) as last_date
        FROM transactions
        WHERE account_id = ?
          AND posted_at <= ?
          AND COALESCE(pending, 0) = 0
        """,
        (account_id, as_of_date.isoformat()),
    ).fetchone()

    balance = result["balance"]
    count = result["txn_count"]
    first_date = date.fromisoformat(result["first_date"][:10]) if result["first_date"] else None
    last_date = date.fromisoformat(result["last_date"][:10]) if result["last_date"] else None

    return balance, count, first_date, last_date


def reconcile_account(
    conn: sqlite3.Connection,
    account_id: str,
    statement_date: date,
    statement_balance_cents: int,
) -> ReconciliationResult:
    """
    Reconcile an account against a statement balance.

    Args:
        conn: Database connection
        account_id: Account to reconcile
        statement_date: Statement ending date
        statement_balance_cents: Ending balance from statement

    Returns:
        ReconciliationResult with computed balance and delta
    """
    # Get account name
    row = conn.execute(
        "SELECT name FROM accounts WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    account_name = row["name"] if row else account_id

    # Compute balance
    calc_balance, txn_count, first_date, last_date = compute_account_balance(
        conn, account_id, statement_date
    )

    delta = statement_balance_cents - calc_balance

    return ReconciliationResult(
        account_id=account_id,
        account_name=account_name,
        statement_date=statement_date,
        statement_balance_cents=statement_balance_cents,
        calculated_balance_cents=calc_balance,
        delta_cents=delta,
        transaction_count=txn_count,
        first_transaction_date=first_date,
        last_transaction_date=last_date,
    )


def save_reconciliation(
    conn: sqlite3.Connection,
    result: ReconciliationResult,
    notes: Optional[str] = None,
) -> ReconciliationEvent:
    """
    Save a reconciliation event to the database.

    Returns:
        The created ReconciliationEvent
    """
    init_reconciliation_tables(conn)

    status = ReconciliationStatus.MATCHED if result.is_matched else ReconciliationStatus.DISCREPANCY
    now = datetime.now().isoformat()

    conn.execute(
        """
        INSERT INTO reconciliation_events
        (account_id, statement_date, statement_balance_cents, calculated_balance_cents,
         delta_cents, status, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, statement_date) DO UPDATE SET
            statement_balance_cents = excluded.statement_balance_cents,
            calculated_balance_cents = excluded.calculated_balance_cents,
            delta_cents = excluded.delta_cents,
            status = excluded.status,
            notes = COALESCE(excluded.notes, notes)
        """,
        (
            result.account_id,
            result.statement_date.isoformat(),
            result.statement_balance_cents,
            result.calculated_balance_cents,
            result.delta_cents,
            status.value,
            notes,
            now,
        ),
    )
    conn.commit()

    # Return the event
    row = conn.execute(
        """
        SELECT id, account_id, statement_date, statement_balance_cents,
               calculated_balance_cents, delta_cents, status, notes,
               created_at, resolved_at
        FROM reconciliation_events
        WHERE account_id = ? AND statement_date = ?
        """,
        (result.account_id, result.statement_date.isoformat()),
    ).fetchone()

    return ReconciliationEvent(
        id=row["id"],
        account_id=row["account_id"],
        statement_date=date.fromisoformat(row["statement_date"]),
        statement_balance_cents=row["statement_balance_cents"],
        calculated_balance_cents=row["calculated_balance_cents"],
        delta_cents=row["delta_cents"],
        status=ReconciliationStatus(row["status"]),
        notes=row["notes"],
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
    )


def resolve_reconciliation(
    conn: sqlite3.Connection,
    account_id: str,
    statement_date: date,
    notes: Optional[str] = None,
) -> None:
    """Mark a reconciliation as resolved."""
    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE reconciliation_events
        SET status = ?, resolved_at = ?, notes = COALESCE(?, notes)
        WHERE account_id = ? AND statement_date = ?
        """,
        (ReconciliationStatus.RESOLVED.value, now, notes, account_id, statement_date.isoformat()),
    )
    conn.commit()


def get_reconciliation_history(
    conn: sqlite3.Connection,
    account_id: Optional[str] = None,
    limit: int = 50,
) -> list[ReconciliationEvent]:
    """Get reconciliation history, optionally filtered by account."""
    init_reconciliation_tables(conn)

    if account_id:
        rows = conn.execute(
            """
            SELECT * FROM reconciliation_events
            WHERE account_id = ?
            ORDER BY statement_date DESC
            LIMIT ?
            """,
            (account_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM reconciliation_events
            ORDER BY statement_date DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [
        ReconciliationEvent(
            id=r["id"],
            account_id=r["account_id"],
            statement_date=date.fromisoformat(r["statement_date"]),
            statement_balance_cents=r["statement_balance_cents"],
            calculated_balance_cents=r["calculated_balance_cents"],
            delta_cents=r["delta_cents"],
            status=ReconciliationStatus(r["status"]),
            notes=r["notes"],
            created_at=datetime.fromisoformat(r["created_at"]),
            resolved_at=datetime.fromisoformat(r["resolved_at"]) if r["resolved_at"] else None,
        )
        for r in rows
    ]


def get_pending_reconciliations(conn: sqlite3.Connection) -> list[ReconciliationEvent]:
    """Get all unresolved reconciliation discrepancies."""
    init_reconciliation_tables(conn)

    rows = conn.execute(
        """
        SELECT * FROM reconciliation_events
        WHERE status = ?
        ORDER BY ABS(delta_cents) DESC
        """,
        (ReconciliationStatus.DISCREPANCY.value,),
    ).fetchall()

    return [
        ReconciliationEvent(
            id=r["id"],
            account_id=r["account_id"],
            statement_date=date.fromisoformat(r["statement_date"]),
            statement_balance_cents=r["statement_balance_cents"],
            calculated_balance_cents=r["calculated_balance_cents"],
            delta_cents=r["delta_cents"],
            status=ReconciliationStatus(r["status"]),
            notes=r["notes"],
            created_at=datetime.fromisoformat(r["created_at"]),
            resolved_at=datetime.fromisoformat(r["resolved_at"]) if r["resolved_at"] else None,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Reconciliation Learning
# ---------------------------------------------------------------------------
@dataclass
class ReconciliationPattern:
    """A detected pattern in reconciliation discrepancies."""
    account_id: str
    account_name: str
    pattern_type: str  # "consistent_delta", "growing_delta", "periodic_delta"
    avg_delta_cents: int
    delta_count: int
    confidence: float
    suggestion: str


@dataclass
class ReconciliationInsight:
    """Insights learned from reconciliation history."""
    patterns: list[ReconciliationPattern]
    accounts_with_issues: int
    total_unresolved_delta_cents: int
    suggestions: list[str]


def analyze_reconciliation_patterns(
    conn: sqlite3.Connection,
    min_events: int = 3,
) -> ReconciliationInsight:
    """
    Analyze reconciliation history to detect patterns and suggest improvements.

    Learns from:
    - Consistent deltas (same direction, similar magnitude)
    - Growing deltas (increasing over time)
    - Accounts with frequent issues

    Args:
        conn: Database connection
        min_events: Minimum reconciliation events to analyze patterns

    Returns:
        ReconciliationInsight with detected patterns and suggestions
    """
    init_reconciliation_tables(conn)

    # Get all reconciliation events grouped by account
    rows = conn.execute(
        """
        SELECT
            r.account_id,
            COALESCE(a.name, r.account_id) as account_name,
            r.statement_date,
            r.delta_cents,
            r.status
        FROM reconciliation_events r
        LEFT JOIN accounts a ON r.account_id = a.account_id
        ORDER BY r.account_id, r.statement_date
        """
    ).fetchall()

    # Group by account
    account_events: dict[str, list] = {}
    account_names: dict[str, str] = {}
    for row in rows:
        aid = row["account_id"]
        if aid not in account_events:
            account_events[aid] = []
            account_names[aid] = row["account_name"]
        account_events[aid].append({
            "date": row["statement_date"],
            "delta": row["delta_cents"],
            "status": row["status"],
        })

    patterns: list[ReconciliationPattern] = []
    suggestions: list[str] = []
    accounts_with_issues = 0
    total_unresolved = 0

    for account_id, events in account_events.items():
        if len(events) < min_events:
            continue

        deltas = [e["delta"] for e in events]
        unresolved = [e for e in events if e["status"] == "discrepancy"]

        if unresolved:
            accounts_with_issues += 1
            total_unresolved += sum(abs(e["delta"]) for e in unresolved)

        # Detect consistent delta pattern (same direction, low variance)
        if len(deltas) >= min_events:
            avg_delta = sum(deltas) // len(deltas)
            all_positive = all(d > 0 for d in deltas)
            all_negative = all(d < 0 for d in deltas)

            if (all_positive or all_negative) and abs(avg_delta) > 100:
                # Calculate coefficient of variation
                mean = abs(avg_delta)
                variance = sum((abs(d) - mean) ** 2 for d in deltas) / len(deltas)
                std_dev = variance ** 0.5
                cv = std_dev / mean if mean > 0 else 0

                if cv < 0.3:  # Low variance = consistent pattern
                    direction = "higher" if avg_delta > 0 else "lower"
                    patterns.append(ReconciliationPattern(
                        account_id=account_id,
                        account_name=account_names[account_id],
                        pattern_type="consistent_delta",
                        avg_delta_cents=avg_delta,
                        delta_count=len(deltas),
                        confidence=1.0 - cv,
                        suggestion=f"Statement consistently {direction} than calculated by ${abs(avg_delta)/100:.2f}. "
                                   f"Check for missing {'income' if avg_delta > 0 else 'expenses'}.",
                    ))

        # Detect growing delta (trending worse)
        if len(deltas) >= min_events:
            recent = deltas[-3:]
            older = deltas[:-3] if len(deltas) > 3 else deltas[:1]

            avg_recent = sum(abs(d) for d in recent) / len(recent)
            avg_older = sum(abs(d) for d in older) / len(older) if older else 0

            if avg_older > 0 and avg_recent > avg_older * 1.5:
                patterns.append(ReconciliationPattern(
                    account_id=account_id,
                    account_name=account_names[account_id],
                    pattern_type="growing_delta",
                    avg_delta_cents=int(avg_recent),
                    delta_count=len(deltas),
                    confidence=0.7,
                    suggestion=f"Discrepancy growing over time (${avg_older/100:.2f} → ${avg_recent/100:.2f}). "
                               f"May indicate systematic missing transactions.",
                ))

    # Generate overall suggestions
    if accounts_with_issues > 0:
        suggestions.append(
            f"{accounts_with_issues} account(s) have unresolved discrepancies "
            f"totaling ${total_unresolved/100:,.2f}."
        )

    consistent_patterns = [p for p in patterns if p.pattern_type == "consistent_delta"]
    if consistent_patterns:
        suggestions.append(
            f"{len(consistent_patterns)} account(s) show consistent discrepancy patterns. "
            f"Consider reviewing transaction sources."
        )

    growing_patterns = [p for p in patterns if p.pattern_type == "growing_delta"]
    if growing_patterns:
        suggestions.append(
            f"{len(growing_patterns)} account(s) have growing discrepancies. "
            f"Investigate recent transaction sync issues."
        )

    return ReconciliationInsight(
        patterns=patterns,
        accounts_with_issues=accounts_with_issues,
        total_unresolved_delta_cents=total_unresolved,
        suggestions=suggestions,
    )


def get_missing_transaction_candidates(
    conn: sqlite3.Connection,
    account_id: str,
    statement_date: date,
    delta_cents: int,
    tolerance_percent: float = 20.0,
) -> list[dict]:
    """
    Find transactions that might explain a reconciliation delta.

    Looks for transactions near the statement date with amounts
    close to the delta magnitude.

    Args:
        conn: Database connection
        account_id: Account to search
        statement_date: Statement ending date
        delta_cents: The reconciliation delta to explain
        tolerance_percent: How close amounts need to be

    Returns:
        List of candidate transactions that might explain the delta
    """
    # Calculate search bounds
    target_amount = abs(delta_cents)
    tolerance = int(target_amount * tolerance_percent / 100)
    min_amount = target_amount - tolerance
    max_amount = target_amount + tolerance

    # Look for transactions in the 30 days before statement date
    # that might have been missed or miscategorized
    start_date = (statement_date - timedelta(days=30)).isoformat()
    end_date = statement_date.isoformat()

    # If delta is positive (statement > calculated), look for missing income
    # If delta is negative (statement < calculated), look for missing expenses
    if delta_cents > 0:
        # Missing income - look for positive amounts
        amount_clause = "amount_cents > 0"
    else:
        # Missing expense - look for negative amounts
        amount_clause = "amount_cents < 0"

    rows = conn.execute(
        f"""
        SELECT
            t.posted_at,
            t.amount_cents,
            COALESCE(t.merchant, t.description, '') as payee,
            t.fingerprint
        FROM transactions t
        WHERE t.account_id = ?
          AND t.posted_at >= ? AND t.posted_at <= ?
          AND {amount_clause}
          AND ABS(t.amount_cents) BETWEEN ? AND ?
        ORDER BY ABS(ABS(t.amount_cents) - ?) ASC
        LIMIT 10
        """,
        (account_id, start_date, end_date, min_amount, max_amount, target_amount),
    ).fetchall()

    candidates = []
    for row in rows:
        candidates.append({
            "date": row["posted_at"][:10],
            "amount_cents": row["amount_cents"],
            "payee": row["payee"],
            "fingerprint": row["fingerprint"],
            "match_quality": 1.0 - abs(abs(row["amount_cents"]) - target_amount) / target_amount,
        })

    return candidates
