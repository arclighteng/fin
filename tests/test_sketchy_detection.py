"""
Unit tests for sketchy charge detection in fin.classify module.

Tests all patterns:
- Duplicate charges (same merchant + amount within 3 days)
- Unusual amounts (>2x median for merchant)
- Test charges ($0.01-$1.00)
- Round amount spikes ($50/$100/$200 first time)
- Rapid-fire charges (3+ in 24h)
- Refund + recharge patterns
"""
import sqlite3
from datetime import date, timedelta

import pytest

from fin.classify import (
    SketchyCharge,
    detect_sketchy,
    _normalize_merchant_fuzzy,
)
from fin import db as dbmod


class TestMerchantNormalization:
    """Test fuzzy merchant name normalization."""

    @pytest.mark.parametrize("input_name,expected", [
        ("NETFLIX", "netflix"),
        ("NETFLIX.COM", "netflix"),
        ("NETFLIX INC", "netflix"),
        ("NETFLIX INC.", "netflix"),
        ("Netflix LLC", "netflix"),
        ("AMAZON.COM", "amazon"),
        ("AMAZON*PRIME", "amazon"),
        ("SPOTIFY AB", "spotify ab"),  # AB is not a standard suffix
        ("  STORE NAME  ", "store name"),
        ("MERCHANT 12345678", "merchant"),  # Strip trailing IDs
        ("STORE***", "store"),
    ])
    def test_merchant_normalization(self, input_name, expected):
        """Merchant names should normalize consistently."""
        result = _normalize_merchant_fuzzy(input_name)
        assert result == expected


class TestDuplicateChargeDetection:
    """Test detection of duplicate charges (same merchant + amount within 3 days)."""

    def test_detects_duplicate_within_3_days(self, temp_db_path):
        """Should detect same merchant + amount within 3 days."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Insert duplicate charges
        _insert_txn(conn, today - timedelta(days=5), -2999, "MERCHANT X")
        _insert_txn(conn, today - timedelta(days=3), -2999, "MERCHANT X")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        dup_alerts = [a for a in alerts if a.pattern_type == "duplicate_charge"]
        assert len(dup_alerts) == 1
        assert dup_alerts[0].merchant_norm == "merchant x"
        assert dup_alerts[0].amount_cents == 2999
        assert dup_alerts[0].severity == "high"

    def test_ignores_charges_more_than_3_days_apart(self, temp_db_path):
        """Should NOT flag charges more than 3 days apart."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Charges 5 days apart - not a duplicate
        _insert_txn(conn, today - timedelta(days=10), -2999, "MERCHANT Y")
        _insert_txn(conn, today - timedelta(days=5), -2999, "MERCHANT Y")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        dup_alerts = [a for a in alerts if a.pattern_type == "duplicate_charge"]
        assert len(dup_alerts) == 0

    def test_ignores_different_amounts(self, temp_db_path):
        """Should NOT flag different amounts as duplicates."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        _insert_txn(conn, today - timedelta(days=2), -2999, "MERCHANT Z")
        _insert_txn(conn, today - timedelta(days=1), -3999, "MERCHANT Z")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        dup_alerts = [a for a in alerts if a.pattern_type == "duplicate_charge"]
        assert len(dup_alerts) == 0

    def test_ignores_income_duplicates(self, temp_db_path):
        """Should only flag expense duplicates, not income."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Positive amounts (income) - should not be flagged
        _insert_txn(conn, today - timedelta(days=2), 5000, "EMPLOYER")
        _insert_txn(conn, today - timedelta(days=1), 5000, "EMPLOYER")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        dup_alerts = [a for a in alerts if a.pattern_type == "duplicate_charge"]
        assert len(dup_alerts) == 0


class TestUnusualAmountDetection:
    """Test detection of amounts >2x median for a merchant."""

    def test_detects_unusual_amount(self, temp_db_path):
        """Should detect charge >2x the merchant's median."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Build history with median ~$50
        for i in range(5):
            _insert_txn(conn, today - timedelta(days=30*i + 100), -5000, "REGULAR STORE")

        # Add unusual charge of $150 (3x median)
        _insert_txn(conn, today - timedelta(days=5), -15000, "REGULAR STORE")
        conn.commit()

        alerts = detect_sketchy(conn, days=60)
        conn.close()

        unusual = [a for a in alerts if a.pattern_type == "unusual_amount"]
        assert len(unusual) == 1
        assert unusual[0].amount_cents == 15000
        assert "3.0x" in unusual[0].detail

    def test_ignores_normal_variation(self, temp_db_path):
        """Should NOT flag charges within normal range."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # History with median ~$50
        for i in range(5):
            _insert_txn(conn, today - timedelta(days=30*i + 100), -5000, "NORMAL STORE")

        # Charge of $80 (1.6x median) - within normal range
        _insert_txn(conn, today - timedelta(days=5), -8000, "NORMAL STORE")
        conn.commit()

        alerts = detect_sketchy(conn, days=60)
        conn.close()

        unusual = [a for a in alerts if a.pattern_type == "unusual_amount"
                   and "normal store" in a.merchant_norm]
        assert len(unusual) == 0

    def test_requires_history(self, temp_db_path):
        """Should require 3+ historical charges to detect unusual."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Only 2 historical charges
        _insert_txn(conn, today - timedelta(days=200), -5000, "NEW STORE")
        _insert_txn(conn, today - timedelta(days=100), -5000, "NEW STORE")
        _insert_txn(conn, today - timedelta(days=5), -15000, "NEW STORE")
        conn.commit()

        alerts = detect_sketchy(conn, days=60)
        conn.close()

        unusual = [a for a in alerts if a.pattern_type == "unusual_amount"
                   and "new store" in a.merchant_norm]
        assert len(unusual) == 0


class TestTestChargeDetection:
    """Test detection of test/verification charges ($0.01-$1.00)."""

    @pytest.mark.parametrize("amount_cents", [1, 10, 50, 99, 100])
    def test_detects_test_charges(self, temp_db_path, amount_cents):
        """Should detect charges between $0.01 and $1.00."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        _insert_txn(conn, today - timedelta(days=5), -amount_cents, "TEST VENDOR")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        test_alerts = [a for a in alerts if a.pattern_type == "test_charge"]
        assert len(test_alerts) == 1
        assert test_alerts[0].severity == "medium"

    @pytest.mark.parametrize("amount_cents", [0, 101, 500, 1000])
    def test_ignores_non_test_amounts(self, temp_db_path, amount_cents):
        """Should NOT flag charges outside $0.01-$1.00 range."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        _insert_txn(conn, today - timedelta(days=5), -amount_cents, "NORMAL VENDOR")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        test_alerts = [a for a in alerts if a.pattern_type == "test_charge"]
        assert len(test_alerts) == 0


class TestRoundAmountSpikeDetection:
    """Test detection of round amount spikes from new merchants."""

    @pytest.mark.parametrize("amount_cents", [5000, 10000, 15000, 20000, 25000])
    def test_detects_round_amounts_from_new_merchant(self, temp_db_path, amount_cents):
        """Should detect round amounts from first-time merchants."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # First and only charge from this merchant
        _insert_txn(conn, today - timedelta(days=5), -amount_cents, f"NEW MERCHANT {amount_cents}")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        round_alerts = [a for a in alerts if a.pattern_type == "round_amount_spike"]
        matching = [a for a in round_alerts if str(amount_cents) in a.merchant_norm
                    or a.amount_cents == amount_cents]
        assert len(matching) == 1

    def test_ignores_round_from_established_merchant(self, temp_db_path):
        """Should NOT flag round amounts from established merchants."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Establish history
        _insert_txn(conn, today - timedelta(days=100), -3500, "ESTABLISHED")
        _insert_txn(conn, today - timedelta(days=5), -10000, "ESTABLISHED")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        round_alerts = [a for a in alerts if a.pattern_type == "round_amount_spike"
                       and "established" in a.merchant_norm]
        assert len(round_alerts) == 0


class TestRapidFireDetection:
    """Test detection of rapid-fire charges (3+ in 24h)."""

    def test_detects_rapid_fire_charges(self, temp_db_path):
        """Should detect 3+ charges from same merchant in 24h."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # 4 charges on same day
        for _ in range(4):
            _insert_txn(conn, today - timedelta(days=5), -1500, "RAPID VENDOR")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        rapid = [a for a in alerts if a.pattern_type == "rapid_fire"]
        assert len(rapid) == 1
        assert rapid[0].severity == "medium"
        assert "4 charges" in rapid[0].detail

    def test_ignores_less_than_3_charges(self, temp_db_path):
        """Should NOT flag fewer than 3 charges in 24h."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Only 2 charges on same day
        _insert_txn(conn, today - timedelta(days=5), -1500, "DOUBLE VENDOR")
        _insert_txn(conn, today - timedelta(days=5), -1500, "DOUBLE VENDOR")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        rapid = [a for a in alerts if a.pattern_type == "rapid_fire"
                 and "double vendor" in a.merchant_norm]
        assert len(rapid) == 0


class TestRefundRechargeDetection:
    """Test detection of refund followed by similar charge."""

    def test_detects_refund_recharge_pattern(self, temp_db_path):
        """Should detect refund followed by similar charge within 7 days."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Refund (positive amount)
        _insert_txn(conn, today - timedelta(days=10), 5000, "REFUND STORE")
        # Similar charge within 7 days
        _insert_txn(conn, today - timedelta(days=5), -4800, "REFUND STORE")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        refund = [a for a in alerts if a.pattern_type == "refund_recharge"]
        assert len(refund) == 1
        assert refund[0].severity == "low"

    def test_ignores_refund_without_recharge(self, temp_db_path):
        """Should NOT flag refund without subsequent charge."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        _insert_txn(conn, today - timedelta(days=10), 5000, "ONLY REFUND")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        refund = [a for a in alerts if a.pattern_type == "refund_recharge"
                  and "only refund" in a.merchant_norm]
        assert len(refund) == 0


class TestSketchyDetectionIntegration:
    """Integration tests using the populated fixture."""

    def test_detects_all_patterns_in_populated_db(
        self, populated_db, expected_sketchy_patterns
    ):
        """Should detect all planted sketchy patterns."""
        alerts = detect_sketchy(populated_db, days=60)

        by_type = {}
        for a in alerts:
            by_type.setdefault(a.pattern_type, []).append(a)

        # Check each expected pattern
        assert len(by_type.get("duplicate_charge", [])) >= expected_sketchy_patterns["duplicate_charge"]
        assert len(by_type.get("unusual_amount", [])) >= expected_sketchy_patterns["unusual_amount"]
        assert len(by_type.get("test_charge", [])) >= expected_sketchy_patterns["test_charge"]
        assert len(by_type.get("round_amount_spike", [])) >= expected_sketchy_patterns["round_amount_spike"]
        assert len(by_type.get("rapid_fire", [])) >= expected_sketchy_patterns["rapid_fire"]
        # refund_recharge may vary based on timing

    def test_alerts_have_correct_structure(self, populated_db):
        """All alerts should have required fields."""
        alerts = detect_sketchy(populated_db, days=60)

        for alert in alerts:
            assert isinstance(alert, SketchyCharge)
            assert isinstance(alert.posted_at, date)
            assert isinstance(alert.merchant_norm, str)
            assert isinstance(alert.amount_cents, int)
            assert alert.amount_cents >= 0  # Stored as positive
            assert alert.pattern_type in (
                "duplicate_charge", "unusual_amount", "test_charge",
                "round_amount_spike", "rapid_fire", "refund_recharge"
            )
            assert alert.severity in ("high", "medium", "low")
            assert isinstance(alert.detail, str)
            assert len(alert.detail) > 0

    def test_alerts_sorted_by_severity(self, populated_db):
        """Alerts should be sorted by severity (high first)."""
        alerts = detect_sketchy(populated_db, days=60)

        severity_order = {"high": 0, "medium": 1, "low": 2}
        for i in range(1, len(alerts)):
            prev_sev = severity_order[alerts[i-1].severity]
            curr_sev = severity_order[alerts[i].severity]
            assert prev_sev <= curr_sev, "Alerts not sorted by severity"

    def test_empty_db_returns_empty_list(self, empty_db):
        """Empty database should return no alerts."""
        alerts = detect_sketchy(empty_db, days=60)
        assert alerts == []


class TestFinancialAccuracySketchyCharges:
    """
    Critical accuracy tests for sketchy charge detection.
    Financial applications must be precise.
    """

    def test_amounts_stored_as_positive_cents(self, populated_db):
        """All amounts in alerts should be positive integers."""
        alerts = detect_sketchy(populated_db, days=60)

        for alert in alerts:
            assert isinstance(alert.amount_cents, int)
            assert alert.amount_cents >= 0, f"Amount should be positive: {alert.amount_cents}"

    def test_no_false_positives_on_normal_subscription(self, temp_db_path):
        """Regular subscription charges should NOT be flagged."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Normal monthly subscription pattern
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -1599, "NETFLIX")
        conn.commit()

        alerts = detect_sketchy(conn, days=30)
        conn.close()

        # Should have no alerts for normal subscription
        netflix_alerts = [a for a in alerts if "netflix" in a.merchant_norm]
        assert len(netflix_alerts) == 0, "Normal subscription flagged as sketchy"

    def test_edge_case_exactly_2x_median(self, temp_db_path):
        """Exactly 2x median should NOT be flagged (must be >2x)."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # History with median $50
        for i in range(5):
            _insert_txn(conn, today - timedelta(days=30*i + 100), -5000, "EDGE STORE")

        # Exactly 2x = $100 - should NOT be flagged
        _insert_txn(conn, today - timedelta(days=5), -10000, "EDGE STORE")
        conn.commit()

        alerts = detect_sketchy(conn, days=60)
        conn.close()

        unusual = [a for a in alerts if a.pattern_type == "unusual_amount"
                   and "edge store" in a.merchant_norm]
        assert len(unusual) == 0, "Exactly 2x median should not be flagged"


# Counter for unique fingerprints in tests
_txn_counter = 0

# Helper function for inserting test transactions
def _insert_txn(
    conn: sqlite3.Connection,
    posted_at: date,
    amount_cents: int,
    merchant: str,
):
    """Insert a test transaction."""
    global _txn_counter
    _txn_counter += 1
    conn.execute(
        """
        INSERT INTO transactions (
            account_id, posted_at, amount_cents, currency,
            description, merchant, fingerprint, created_at, updated_at
        ) VALUES (?, ?, ?, 'USD', '', ?, ?, datetime('now'), datetime('now'))
        """,
        (
            "test_acct",
            posted_at.isoformat(),
            amount_cents,
            merchant,
            f"fp_{posted_at}_{merchant}_{amount_cents}_{_txn_counter}",
        ),
    )
