"""
Tests for transfer_pairing.py - transfer detection and pair matching.

TRUTH CONTRACT verification:
- Matched transfers (both legs) net to $0
- Unmatched transfers are flagged
- Pair IDs link both legs
"""
import sqlite3
import tempfile
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

from fin.transfer_pairing import (
    detect_transfer_pairs,
    store_transfer_pairs,
    get_pair_info,
    get_paired_fingerprint,
    TransferLeg,
    TransferPair,
    TransferPairingResult,
    _is_transfer_pattern,
    _is_bank_pattern,
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


def insert_txn(conn, account_id, posted_at, amount_cents, merchant):
    """Helper to insert a transaction."""
    fp = f"fp_{uuid.uuid4().hex[:8]}"
    conn.execute(
        """
        INSERT INTO transactions (
            account_id, posted_at, amount_cents, currency,
            description, merchant, fingerprint, pending, created_at, updated_at
        ) VALUES (?, ?, ?, 'USD', '', ?, ?, 0, datetime('now'), datetime('now'))
        """,
        (account_id, posted_at.isoformat(), amount_cents, merchant, fp),
    )
    conn.commit()
    return fp


class TestTransferPatterns:
    """Test transfer pattern detection."""

    def test_transfer_keywords(self):
        """Should detect transfer keywords."""
        assert _is_transfer_pattern("zelle payment") is True
        assert _is_transfer_pattern("venmo from friend") is True
        assert _is_transfer_pattern("ach transfer") is True
        assert _is_transfer_pattern("netflix subscription") is False

    def test_bank_keywords(self):
        """Should detect bank patterns."""
        assert _is_bank_pattern("chase checking") is True
        assert _is_bank_pattern("wells fargo savings") is True
        assert _is_bank_pattern("amazon purchase") is False


class TestTransferPairMatching:
    """Test transfer pair detection."""

    def test_exact_match(self, test_db):
        """Should match exact opposite amounts on different accounts."""
        conn, _ = test_db
        today = date.today()

        # Insert matching transfer legs
        fp1 = insert_txn(conn, "savings", today, -100000, "transfer to checking")
        fp2 = insert_txn(conn, "checking", today, 100000, "transfer from savings")

        result = detect_transfer_pairs(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert len(result.matched_pairs) == 1
        pair = result.matched_pairs[0]
        assert pair.outflow.fingerprint == fp1
        assert pair.inflow.fingerprint == fp2
        assert pair.amount_diff_cents == 0
        assert pair.is_balanced

    def test_amount_tolerance(self, test_db):
        """Should match with small amount difference (ACH fees)."""
        conn, _ = test_db
        today = date.today()

        # Insert transfer with $2 ACH fee
        fp1 = insert_txn(conn, "external_bank", today, -100200, "ach transfer out")
        fp2 = insert_txn(conn, "checking", today, 100000, "ach transfer in")

        result = detect_transfer_pairs(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
            tolerance_cents=300,
        )

        assert len(result.matched_pairs) == 1
        assert result.matched_pairs[0].amount_diff_cents == 200

    def test_date_tolerance(self, test_db):
        """Should match transactions within date tolerance."""
        conn, _ = test_db
        today = date.today()

        # Insert transfer with 2 day gap
        fp1 = insert_txn(conn, "savings", today - timedelta(days=2), -50000, "transfer")
        fp2 = insert_txn(conn, "checking", today, 50000, "transfer")

        result = detect_transfer_pairs(
            conn,
            today - timedelta(days=5),
            today + timedelta(days=1),
            tolerance_days=3,
        )

        assert len(result.matched_pairs) == 1

    def test_no_match_same_account(self, test_db):
        """Should not match transactions on same account."""
        conn, _ = test_db
        today = date.today()

        # Insert on same account
        insert_txn(conn, "checking", today, -50000, "transfer")
        insert_txn(conn, "checking", today, 50000, "transfer")

        result = detect_transfer_pairs(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert len(result.matched_pairs) == 0

    def test_unmatched_transfer(self, test_db):
        """Should report unmatched transfer-like transactions."""
        conn, _ = test_db
        today = date.today()

        # Insert only outflow, no matching inflow
        insert_txn(conn, "savings", today, -50000, "transfer to external")

        result = detect_transfer_pairs(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        assert len(result.matched_pairs) == 0
        assert len(result.unmatched_outflows) == 1
        assert result.has_unmatched


class TestPairStorage:
    """Test pair ID storage and retrieval."""

    def test_store_and_retrieve(self, test_db):
        """Should store pair IDs in database."""
        conn, _ = test_db
        today = date.today()

        fp1 = insert_txn(conn, "savings", today, -100000, "transfer")
        fp2 = insert_txn(conn, "checking", today, 100000, "transfer")

        result = detect_transfer_pairs(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )

        store_transfer_pairs(conn, result)

        # Retrieve pair info
        info1 = get_pair_info(conn, fp1)
        info2 = get_pair_info(conn, fp2)

        assert info1 is not None
        assert info2 is not None
        assert info1[0] == info2[0]  # Same pair_id

    def test_get_paired_fingerprint(self, test_db):
        """Should retrieve the other fingerprint in a pair."""
        conn, _ = test_db
        today = date.today()

        fp1 = insert_txn(conn, "savings", today, -100000, "transfer")
        fp2 = insert_txn(conn, "checking", today, 100000, "transfer")

        result = detect_transfer_pairs(
            conn,
            today - timedelta(days=1),
            today + timedelta(days=1),
        )
        store_transfer_pairs(conn, result)

        # Get paired fingerprint
        paired_of_1 = get_paired_fingerprint(conn, fp1)
        paired_of_2 = get_paired_fingerprint(conn, fp2)

        assert paired_of_1 == fp2
        assert paired_of_2 == fp1


class TestTransferPairResult:
    """Test TransferPairingResult methods."""

    def test_get_paired_fingerprints(self):
        """Should return all fingerprints from matched pairs."""
        outflow = TransferLeg(
            fingerprint="fp_out",
            account_id="savings",
            posted_at=date.today(),
            amount_cents=-10000,
            merchant_norm="transfer",
            is_outflow=True,
        )
        inflow = TransferLeg(
            fingerprint="fp_in",
            account_id="checking",
            posted_at=date.today(),
            amount_cents=10000,
            merchant_norm="transfer",
            is_outflow=False,
        )
        pair = TransferPair(
            pair_id="pair1",
            outflow=outflow,
            inflow=inflow,
            confidence=0.9,
            match_reason="exact amount",
            amount_diff_cents=0,
        )

        result = TransferPairingResult(matched_pairs=[pair])
        fps = result.get_paired_fingerprints()

        assert "fp_out" in fps
        assert "fp_in" in fps

    def test_get_pair_id(self):
        """Should return pair ID for fingerprint."""
        outflow = TransferLeg(
            fingerprint="fp_out",
            account_id="savings",
            posted_at=date.today(),
            amount_cents=-10000,
            merchant_norm="transfer",
            is_outflow=True,
        )
        inflow = TransferLeg(
            fingerprint="fp_in",
            account_id="checking",
            posted_at=date.today(),
            amount_cents=10000,
            merchant_norm="transfer",
            is_outflow=False,
        )
        pair = TransferPair(
            pair_id="pair123",
            outflow=outflow,
            inflow=inflow,
            confidence=0.9,
            match_reason="exact amount",
            amount_diff_cents=0,
        )

        result = TransferPairingResult(matched_pairs=[pair])

        assert result.get_pair_id("fp_out") == "pair123"
        assert result.get_pair_id("fp_in") == "pair123"
        assert result.get_pair_id("fp_other") is None


class TestNetBalance:
    """Test that matched transfers net to zero."""

    def test_balanced_pair(self):
        """Matched pair should net to zero."""
        outflow = TransferLeg(
            fingerprint="fp_out",
            account_id="savings",
            posted_at=date.today(),
            amount_cents=-50000,
            merchant_norm="transfer",
            is_outflow=True,
        )
        inflow = TransferLeg(
            fingerprint="fp_in",
            account_id="checking",
            posted_at=date.today(),
            amount_cents=50000,
            merchant_norm="transfer",
            is_outflow=False,
        )
        pair = TransferPair(
            pair_id="pair1",
            outflow=outflow,
            inflow=inflow,
            confidence=0.9,
            match_reason="exact amount",
            amount_diff_cents=0,
        )

        assert pair.net_cents == 0
        assert pair.is_balanced

    def test_small_fee_still_balanced(self):
        """Small ACH fee should still count as balanced."""
        outflow = TransferLeg(
            fingerprint="fp_out",
            account_id="external",
            posted_at=date.today(),
            amount_cents=-50200,  # Extra $2 for ACH fee
            merchant_norm="ach transfer",
            is_outflow=True,
        )
        inflow = TransferLeg(
            fingerprint="fp_in",
            account_id="checking",
            posted_at=date.today(),
            amount_cents=50000,
            merchant_norm="ach transfer",
            is_outflow=False,
        )
        pair = TransferPair(
            pair_id="pair1",
            outflow=outflow,
            inflow=inflow,
            confidence=0.85,
            match_reason="±$2.00",
            amount_diff_cents=200,
        )

        assert pair.net_cents == -200  # $2 fee
        assert pair.is_balanced  # Within $5 tolerance
