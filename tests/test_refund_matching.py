"""
Tests for refund_matching.py - refund detection and matching.

TRUTH CONTRACT verification:
- REFUND = matched to prior expense
- Refunds reduce net spend
- Same merchant, within 90 days
"""
import sqlite3
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from fin.refund_matching import (
    detect_refund_matches,
    store_refund_matches,
    get_matched_expense_for_refund,
    RefundMatch,
    RefundMatchingResult,
    _has_refund_keyword,
    _merchants_match,
)
from fin import db as dbmod


@pytest.fixture
def test_db():
    """Create a temporary database with transactions."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = dbmod.connect(path)
    dbmod.init_db(conn)
    yield conn, path

    conn.close()
    Path(path).unlink(missing_ok=True)


def insert_txn(conn, account_id, posted_at, amount_cents, merchant, description=""):
    """Helper to insert a transaction."""
    fp = f"fp_{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT INTO transactions (
            account_id, posted_at, amount_cents, currency,
            description, merchant, fingerprint, pending, created_at, updated_at
        ) VALUES (?, ?, ?, 'USD', ?, ?, ?, 0, datetime('now'), datetime('now'))
        """,
        (account_id, posted_at.isoformat(), amount_cents, description, merchant, fp),
    )
    conn.commit()
    return fp


class TestRefundKeywords:
    """Test refund keyword detection."""

    def test_refund_keywords(self):
        """Should detect refund keywords."""
        assert _has_refund_keyword("amazon refund") is True
        assert _has_refund_keyword("credit adjustment") is True
        assert _has_refund_keyword("return credit") is True
        assert _has_refund_keyword("amazon purchase") is False


class TestMerchantMatching:
    """Test merchant similarity matching."""

    def test_exact_match(self):
        """Exact matches should match."""
        assert _merchants_match("amazon", "amazon") is True

    def test_substring_match(self):
        """Substring should match."""
        assert _merchants_match("amazon", "amazon marketplace") is True
        assert _merchants_match("amazon marketplace", "amazon") is True

    def test_first_word_match(self):
        """First significant word match."""
        assert _merchants_match("amazon.com", "amazon prime") is True

    def test_no_match(self):
        """Different merchants should not match."""
        assert _merchants_match("amazon", "walmart") is False


class TestRefundMatching:
    """Test refund detection and matching."""

    def test_match_same_merchant(self, test_db):
        """Should match refund to expense with same merchant."""
        conn, _ = test_db
        today = date.today()

        # Insert expense
        expense_fp = insert_txn(conn, "checking", today - timedelta(days=5), -5000, "amazon")
        # Insert refund
        refund_fp = insert_txn(conn, "checking", today, 5000, "amazon refund")

        result = detect_refund_matches(
            conn,
            today - timedelta(days=10),
            today + timedelta(days=1),
        )

        assert len(result.matched_refunds) == 1
        match = result.matched_refunds[0]
        assert match.refund_fingerprint == refund_fp
        assert match.expense_fingerprint == expense_fp
        assert match.is_full_refund

    def test_partial_refund(self, test_db):
        """Should match partial refund."""
        conn, _ = test_db
        today = date.today()

        # Insert expense
        expense_fp = insert_txn(conn, "checking", today - timedelta(days=3), -10000, "store")
        # Insert partial refund
        refund_fp = insert_txn(conn, "checking", today, 5000, "store refund")

        result = detect_refund_matches(
            conn,
            today - timedelta(days=10),
            today + timedelta(days=1),
        )

        assert len(result.matched_refunds) == 1
        match = result.matched_refunds[0]
        assert match.is_partial_refund

    def test_no_match_different_merchant(self, test_db):
        """Should not match refund to different merchant expense."""
        conn, _ = test_db
        today = date.today()

        # Insert expense from one merchant
        insert_txn(conn, "checking", today - timedelta(days=5), -5000, "amazon")
        # Insert refund from different merchant (no refund keyword)
        insert_txn(conn, "checking", today, 5000, "walmart")

        result = detect_refund_matches(
            conn,
            today - timedelta(days=10),
            today + timedelta(days=1),
        )

        assert len(result.matched_refunds) == 0

    def test_no_match_too_old(self, test_db):
        """Should not match refund to expense older than lookback."""
        conn, _ = test_db
        today = date.today()

        # Insert old expense
        insert_txn(conn, "checking", today - timedelta(days=100), -5000, "amazon")
        # Insert refund
        insert_txn(conn, "checking", today, 5000, "amazon refund")

        result = detect_refund_matches(
            conn,
            today - timedelta(days=10),
            today + timedelta(days=1),
            lookback_days=90,
        )

        # The refund should be unmatched (has refund keyword)
        assert len(result.matched_refunds) == 0
        assert len(result.unmatched_refunds) == 1

    def test_refund_keyword_helps_match(self, test_db):
        """Refund keyword should help matching even if merchant differs slightly."""
        conn, _ = test_db
        today = date.today()

        # Insert expense
        expense_fp = insert_txn(conn, "checking", today - timedelta(days=2), -7500, "store")
        # Insert refund with different merchant but refund keyword
        refund_fp = insert_txn(conn, "checking", today, 7500, "refund credit store")

        result = detect_refund_matches(
            conn,
            today - timedelta(days=10),
            today + timedelta(days=1),
        )

        assert len(result.matched_refunds) == 1


class TestRefundStorage:
    """Test refund match storage."""

    def test_store_and_retrieve(self, test_db):
        """Should store and retrieve refund matches."""
        conn, _ = test_db
        today = date.today()

        expense_fp = insert_txn(conn, "checking", today - timedelta(days=5), -5000, "amazon")
        refund_fp = insert_txn(conn, "checking", today, 5000, "amazon refund")

        result = detect_refund_matches(
            conn,
            today - timedelta(days=10),
            today + timedelta(days=1),
        )

        store_refund_matches(conn, result)

        # Retrieve
        matched_expense = get_matched_expense_for_refund(conn, refund_fp)
        assert matched_expense == expense_fp


class TestRefundMatchResult:
    """Test RefundMatchingResult methods."""

    def test_get_matched_fingerprints(self):
        """Should return all matched refund fingerprints."""
        match = RefundMatch(
            refund_fingerprint="fp_refund",
            expense_fingerprint="fp_expense",
            refund_amount_cents=5000,
            expense_amount_cents=-5000,
            merchant_norm="amazon",
            days_apart=3,
            confidence=0.9,
            match_reason="same merchant",
        )
        result = RefundMatchingResult(matched_refunds=[match])

        fps = result.get_matched_fingerprints()
        assert "fp_refund" in fps

    def test_get_expense_for_refund(self):
        """Should return expense fingerprint for a refund."""
        match = RefundMatch(
            refund_fingerprint="fp_refund",
            expense_fingerprint="fp_expense",
            refund_amount_cents=5000,
            expense_amount_cents=-5000,
            merchant_norm="amazon",
            days_apart=3,
            confidence=0.9,
            match_reason="same merchant",
        )
        result = RefundMatchingResult(matched_refunds=[match])

        assert result.get_expense_for_refund("fp_refund") == "fp_expense"
        assert result.get_expense_for_refund("fp_other") is None


class TestRefundMatchProperties:
    """Test RefundMatch properties."""

    def test_full_refund(self):
        """Should detect full refund."""
        match = RefundMatch(
            refund_fingerprint="r1",
            expense_fingerprint="e1",
            refund_amount_cents=5000,
            expense_amount_cents=-5000,
            merchant_norm="amazon",
            days_apart=1,
            confidence=0.9,
            match_reason="test",
        )
        assert match.is_full_refund is True
        assert match.is_partial_refund is False

    def test_partial_refund(self):
        """Should detect partial refund."""
        match = RefundMatch(
            refund_fingerprint="r1",
            expense_fingerprint="e1",
            refund_amount_cents=2500,
            expense_amount_cents=-5000,
            merchant_norm="amazon",
            days_apart=1,
            confidence=0.9,
            match_reason="test",
        )
        assert match.is_full_refund is False
        assert match.is_partial_refund is True
