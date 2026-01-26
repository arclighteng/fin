"""
Tests for statement reconciliation.

TRUTH CONTRACT verification:
- Calculated balance matches sum of transactions
- Discrepancies are flagged correctly
- History is tracked for audit
"""
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from fin.reconciliation import (
    ReconciliationStatus,
    compute_account_balance,
    reconcile_account,
    save_reconciliation,
    resolve_reconciliation,
    get_reconciliation_history,
    get_pending_reconciliations,
    init_reconciliation_tables,
)
from fin import db as dbmod


@pytest.fixture
def test_db():
    """Create a temporary database with sample data."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = dbmod.connect(path)
    dbmod.init_db(conn)
    init_reconciliation_tables(conn)

    # Insert test account
    conn.execute(
        """
        INSERT INTO accounts (account_id, name, institution, type, currency)
        VALUES ('checking', 'Main Checking', 'Test Bank', 'checking', 'USD')
        """
    )
    conn.commit()

    yield conn, path

    conn.close()
    Path(path).unlink(missing_ok=True)


def insert_txn(conn, account_id, posted_at, amount_cents, merchant, pending=0):
    """Helper to insert a transaction."""
    import uuid
    fp = f"fp_{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT INTO transactions (
            account_id, posted_at, amount_cents, currency,
            description, merchant, fingerprint, pending, created_at, updated_at
        ) VALUES (?, ?, ?, 'USD', '', ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (account_id, posted_at.isoformat(), amount_cents, merchant, fp, pending),
    )
    conn.commit()
    return fp


class TestComputeAccountBalance:
    """Test balance computation."""

    def test_empty_account(self, test_db):
        """Empty account should have zero balance."""
        conn, _ = test_db
        balance, count, first, last = compute_account_balance(
            conn, "checking", date.today()
        )
        assert balance == 0
        assert count == 0
        assert first is None
        assert last is None

    def test_single_transaction(self, test_db):
        """Single transaction balance."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        balance, count, first, last = compute_account_balance(
            conn, "checking", today
        )

        assert balance == 10000
        assert count == 1
        assert first == today
        assert last == today

    def test_multiple_transactions(self, test_db):
        """Multiple transaction balance."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today - timedelta(days=5), 100000, "payroll")
        insert_txn(conn, "checking", today - timedelta(days=3), -5000, "grocery")
        insert_txn(conn, "checking", today - timedelta(days=1), -2000, "coffee")

        balance, count, first, last = compute_account_balance(
            conn, "checking", today
        )

        assert balance == 100000 - 5000 - 2000  # 93000
        assert count == 3

    def test_excludes_pending(self, test_db):
        """Pending transactions should be excluded."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "posted", pending=0)
        insert_txn(conn, "checking", today, 5000, "pending", pending=1)

        balance, count, _, _ = compute_account_balance(
            conn, "checking", today
        )

        assert balance == 10000
        assert count == 1

    def test_respects_date_cutoff(self, test_db):
        """Should only include transactions up to as_of_date."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today - timedelta(days=10), 10000, "old")
        insert_txn(conn, "checking", today, 5000, "new")

        # Query as of 5 days ago
        balance, count, _, _ = compute_account_balance(
            conn, "checking", today - timedelta(days=5)
        )

        assert balance == 10000  # Only old transaction
        assert count == 1


class TestReconcileAccount:
    """Test account reconciliation."""

    def test_matched_reconciliation(self, test_db):
        """Should match when delta is small."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        result = reconcile_account(
            conn, "checking", today, statement_balance_cents=10000
        )

        assert result.is_matched
        assert result.delta_cents == 0
        assert result.calculated_balance_cents == 10000

    def test_discrepancy_detected(self, test_db):
        """Should detect discrepancy when delta exceeds threshold."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        # Statement shows $150 but we only have $100
        result = reconcile_account(
            conn, "checking", today, statement_balance_cents=15000
        )

        assert not result.is_matched
        assert result.delta_cents == 5000  # Missing $50
        assert result.delta_direction == "missing_income"

    def test_negative_delta(self, test_db):
        """Should detect when we have more than statement shows."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 20000, "deposit")

        # Statement shows $100 but we have $200
        result = reconcile_account(
            conn, "checking", today, statement_balance_cents=10000
        )

        assert result.delta_cents == -10000  # Extra $100
        assert result.delta_direction == "missing_expense"

    def test_within_tolerance(self, test_db):
        """$1 tolerance should allow small differences."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        # Statement shows $100.50 vs calculated $100.00
        result = reconcile_account(
            conn, "checking", today, statement_balance_cents=10050
        )

        # Delta of 50 cents is within $1 tolerance
        assert result.is_matched
        assert result.delta_cents == 50


class TestSaveReconciliation:
    """Test saving reconciliation events."""

    def test_saves_matched_event(self, test_db):
        """Should save matched reconciliation."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        result = reconcile_account(conn, "checking", today, 10000)
        event = save_reconciliation(conn, result)

        assert event.status == ReconciliationStatus.MATCHED
        assert event.account_id == "checking"

    def test_saves_discrepancy_event(self, test_db):
        """Should save discrepancy reconciliation."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        result = reconcile_account(conn, "checking", today, 20000)
        event = save_reconciliation(conn, result, notes="Investigating")

        assert event.status == ReconciliationStatus.DISCREPANCY
        assert event.notes == "Investigating"

    def test_upserts_on_same_date(self, test_db):
        """Should update existing reconciliation for same date."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        # First reconciliation
        result1 = reconcile_account(conn, "checking", today, 20000)
        save_reconciliation(conn, result1)

        # Second reconciliation with corrected balance
        result2 = reconcile_account(conn, "checking", today, 10000)
        event = save_reconciliation(conn, result2)

        # Should update, not insert new
        history = get_reconciliation_history(conn, "checking")
        assert len(history) == 1
        assert history[0].delta_cents == 0


class TestResolveReconciliation:
    """Test resolving reconciliation discrepancies."""

    def test_resolve_discrepancy(self, test_db):
        """Should mark discrepancy as resolved."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "deposit")

        result = reconcile_account(conn, "checking", today, 20000)
        save_reconciliation(conn, result)

        # Resolve it
        resolve_reconciliation(conn, "checking", today, "Manual adjustment made")

        # Check status
        history = get_reconciliation_history(conn, "checking")
        assert history[0].status == ReconciliationStatus.RESOLVED
        assert history[0].notes == "Manual adjustment made"
        assert history[0].resolved_at is not None


class TestReconciliationHistory:
    """Test reconciliation history retrieval."""

    def test_get_history_by_account(self, test_db):
        """Should filter history by account."""
        conn, _ = test_db
        today = date.today()

        # Add another account
        conn.execute(
            """
            INSERT INTO accounts (account_id, name, institution, type, currency)
            VALUES ('savings', 'Savings', 'Test Bank', 'savings', 'USD')
            """
        )
        conn.commit()

        # Reconcile both accounts
        insert_txn(conn, "checking", today, 10000, "deposit")
        insert_txn(conn, "savings", today, 50000, "deposit")

        result1 = reconcile_account(conn, "checking", today, 10000)
        save_reconciliation(conn, result1)

        result2 = reconcile_account(conn, "savings", today, 50000)
        save_reconciliation(conn, result2)

        # Filter by checking only
        history = get_reconciliation_history(conn, "checking")
        assert len(history) == 1
        assert history[0].account_id == "checking"

    def test_get_pending_discrepancies(self, test_db):
        """Should return only unresolved discrepancies."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today - timedelta(days=1), 10000, "deposit")
        insert_txn(conn, "checking", today, 20000, "deposit")

        # Create a discrepancy
        result1 = reconcile_account(conn, "checking", today - timedelta(days=1), 50000)
        save_reconciliation(conn, result1)

        # Create a matched reconciliation
        result2 = reconcile_account(conn, "checking", today, 30000)
        save_reconciliation(conn, result2)

        pending = get_pending_reconciliations(conn)
        assert len(pending) == 1
        assert pending[0].statement_date == today - timedelta(days=1)
