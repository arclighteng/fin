"""
Tests for canonical report engine.

TRUTH CONTRACT verification:
- report_period is the single source of truth
- All types are mutually exclusive
- Integrity gating works
"""
import sqlite3
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from fin.reporting import report_period, report_month, _compute_report_hash
from fin.reporting_models import TransactionType, IntegrityFlag
from fin.integrity import assess_integrity, can_show_recommendations
from fin import db as dbmod


@pytest.fixture
def test_db():
    """Create a temporary database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = dbmod.connect(path)
    dbmod.init_db(conn)
    yield conn, path

    conn.close()
    Path(path).unlink(missing_ok=True)


def insert_txn(conn, account_id, posted_at, amount_cents, merchant, pending=0):
    """Helper to insert a transaction."""
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


class TestReportPeriod:
    """Test the canonical report_period function."""

    def test_basic_report(self, test_db):
        """Should generate a basic report."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, -5000, "grocery store")
        insert_txn(conn, "checking", today, 300000, "payroll direct deposit")

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert report.transaction_count == 2
        assert report.totals.income_cents == 300000
        # Grocery should be expense (discretionary by default)
        assert report.totals.total_expenses_cents == 5000

    def test_pending_excluded_by_default(self, test_db):
        """Pending transactions should be excluded by default."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, -5000, "grocery", pending=0)
        insert_txn(conn, "checking", today, -3000, "pending charge", pending=1)

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert report.transaction_count == 1
        assert report.pending_count == 1

    def test_pending_included_when_requested(self, test_db):
        """Pending transactions included when flag set."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, -5000, "posted", pending=0)
        insert_txn(conn, "checking", today, -3000, "pending", pending=1)

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
            include_pending=True,
        )

        assert report.transaction_count == 2

    def test_report_hash_reproducible(self, test_db):
        """Same data should produce same hash."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, -5000, "grocery")

        report1 = report_period(conn, today - timedelta(days=1), today + timedelta(days=1))
        report2 = report_period(conn, today - timedelta(days=1), today + timedelta(days=1))

        assert report1.report_hash == report2.report_hash

    def test_transfers_net_to_zero(self, test_db):
        """Matched transfers should net to zero."""
        conn, _ = test_db
        today = date.today()

        # Insert matched transfer
        insert_txn(conn, "savings", today, -100000, "transfer to checking")
        insert_txn(conn, "checking", today, 100000, "transfer from savings")

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        # Both should be classified as transfers
        assert report.totals.transfers_in_cents == 100000
        assert report.totals.transfers_out_cents == 100000
        assert report.totals.transfer_balance_cents == 0


class TestIntegrityGating:
    """Test integrity score and recommendation gating."""

    def test_clean_report_is_actionable(self, test_db):
        """Report with no issues should allow recommendations."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 300000, "payroll direct deposit")
        insert_txn(conn, "checking", today, -5000, "grocery")

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert can_show_recommendations(report)

    def test_unclassified_credits_reduce_score(self, test_db):
        """Unclassified credits should reduce integrity score."""
        conn, _ = test_db
        today = date.today()

        # Insert a positive amount that won't match income/refund/transfer patterns
        insert_txn(conn, "checking", today, 50000, "mysterious deposit xyz")

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert IntegrityFlag.UNCLASSIFIED_CREDIT in report.integrity.flags
        assert report.totals.credits_other_cents == 50000

    def test_resolution_tasks_generated(self, test_db):
        """Should generate resolution tasks for issues."""
        conn, _ = test_db
        today = date.today()

        # Use a merchant without refund/transfer keywords to get CREDIT_OTHER
        insert_txn(conn, "checking", today, 50000, "mysterious deposit xyz")

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        # Verify it was classified as CREDIT_OTHER
        assert report.totals.credits_other_cents == 50000

        # Verify the flag is set
        assert IntegrityFlag.UNCLASSIFIED_CREDIT in report.integrity.flags
        assert report.integrity.unclassified_credit_count == 1

        assessment = assess_integrity(report)
        task_types = [t.task_type for t in assessment.resolution_tasks]

        assert "CLASSIFY_CREDIT" in task_types


class TestTypeClassification:
    """Test that types are classified correctly."""

    def test_payroll_is_income(self, test_db):
        """Payroll should be classified as income."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 300000, "acme corp payroll")

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert report.totals.income_cents == 300000

    def test_random_positive_is_credit_other(self, test_db):
        """Random positive should NOT be income."""
        conn, _ = test_db
        today = date.today()

        insert_txn(conn, "checking", today, 10000, "random merchant")

        report = report_period(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert report.totals.income_cents == 0
        assert report.totals.credits_other_cents == 10000
