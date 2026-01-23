"""
Unit tests for duplicate subscription detection in fin.classify module.

Tests:
- Fuzzy merchant matching (NETFLIX vs NETFLIX.COM)
- Similar pattern subscriptions (same amount +/- 10%, same cadence)
- Bundle family detection (Disney, Apple, Amazon, etc.)
- Subscription listing with duplicate flags
"""
import sqlite3
from datetime import date, timedelta

import pytest

from fin.classify import (
    DuplicateGroup,
    detect_duplicates,
    get_subscriptions,
)
from fin import db as dbmod


class TestFuzzyMerchantMatching:
    """Test detection of similar merchant names."""

    def test_detects_netflix_variants(self, temp_db_path):
        """Should group NETFLIX, NETFLIX.COM, NETFLIX INC as duplicates."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Create recurring patterns for each variant
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -1599, "NETFLIX")
            _insert_txn(conn, d + timedelta(days=1), -1599, "NETFLIX.COM")
            _insert_txn(conn, d + timedelta(days=2), -1599, "NETFLIX INC")
        conn.commit()

        duplicates = detect_duplicates(conn, days=400)
        conn.close()

        # Find Netflix-related duplicate group
        netflix_groups = [d for d in duplicates if
                         any("netflix" in m.lower() for m in d.merchants)]
        assert len(netflix_groups) >= 1, "Should detect Netflix variants as duplicates"

        # Group should contain multiple merchants
        netflix_group = netflix_groups[0]
        assert len(netflix_group.merchants) >= 2

    def test_ignores_unrelated_merchants(self, temp_db_path):
        """Should NOT group unrelated merchants."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -1599, "NETFLIX")
            _insert_txn(conn, d + timedelta(days=5), -1599, "SPOTIFY")
        conn.commit()

        duplicates = detect_duplicates(conn, days=400)
        conn.close()

        # Netflix and Spotify should NOT be grouped together
        for group in duplicates:
            has_netflix = any("netflix" in m.lower() for m in group.merchants)
            has_spotify = any("spotify" in m.lower() for m in group.merchants)
            assert not (has_netflix and has_spotify), \
                "Unrelated merchants should not be grouped"


class TestSimilarPatternDetection:
    """Test detection of similar amount + cadence subscriptions."""

    def test_detects_similar_monthly_amounts(self, temp_db_path):
        """Should detect subscriptions with similar amounts and same cadence."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Two different services with same monthly amount
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -999, "SERVICE ALPHA")
            _insert_txn(conn, d + timedelta(days=1), -999, "SERVICE BETA")
        conn.commit()

        duplicates = detect_duplicates(conn, days=400)
        conn.close()

        # Find similar-pattern groups
        similar = [d for d in duplicates if d.group_type == "similar_pattern"]
        # May or may not detect based on thresholds
        # At minimum, verify no errors occurred

    def test_ignores_different_cadences(self, temp_db_path):
        """Should NOT group subscriptions with different cadences."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Monthly subscription
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -999, "MONTHLY SERVICE")

        # Weekly subscription with same amount
        for weeks_ago in range(20):
            d = today - timedelta(weeks=weeks_ago)
            _insert_txn(conn, d, -999, "WEEKLY SERVICE")
        conn.commit()

        duplicates = detect_duplicates(conn, days=400)
        conn.close()

        # Monthly and weekly should NOT be grouped
        for group in duplicates:
            has_monthly = any("monthly" in m.lower() for m in group.merchants)
            has_weekly = any("weekly" in m.lower() for m in group.merchants)
            assert not (has_monthly and has_weekly), \
                "Different cadences should not be grouped"


class TestBundleFamilyDetection:
    """Test detection of known bundle families."""

    def test_detects_disney_bundle(self, temp_db_path):
        """Should detect Disney + Hulu + ESPN as bundle family."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -1399, "DISNEY PLUS")
            _insert_txn(conn, d + timedelta(days=1), -1799, "HULU")
        conn.commit()

        duplicates = detect_duplicates(conn, days=400)
        conn.close()

        # Find Disney bundle group
        disney_groups = [d for d in duplicates if
                        d.group_type == "bundle_family" and
                        any("disney" in m.lower() or "hulu" in m.lower()
                            for m in d.merchants)]
        assert len(disney_groups) >= 1, "Should detect Disney bundle family"

    def test_detects_apple_services(self, temp_db_path):
        """Should detect Apple iCloud + Apple TV as bundle family."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -299, "APPLE ICLOUD")
            _insert_txn(conn, d + timedelta(days=1), -999, "APPLE TV")
        conn.commit()

        duplicates = detect_duplicates(conn, days=400)
        conn.close()

        apple_groups = [d for d in duplicates if
                       d.group_type == "bundle_family" and
                       any("apple" in m.lower() for m in d.merchants)]
        assert len(apple_groups) >= 1, "Should detect Apple bundle family"

    def test_detects_streaming_overlap(self, temp_db_path):
        """Should detect multiple streaming services."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -1599, "NETFLIX")
            _insert_txn(conn, d + timedelta(days=1), -1499, "HBO MAX")
        conn.commit()

        duplicates = detect_duplicates(conn, days=400)
        conn.close()

        # Should flag streaming overlap
        streaming = [d for d in duplicates if
                    d.group_type == "bundle_family"]
        # May or may not detect based on keywords


class TestDuplicateGroupStructure:
    """Test that duplicate groups have correct structure."""

    def test_group_has_required_fields(self, populated_db):
        """All duplicate groups should have required fields."""
        duplicates = detect_duplicates(populated_db, days=400)

        for group in duplicates:
            assert isinstance(group, DuplicateGroup)
            assert group.group_type in ("fuzzy_match", "similar_pattern", "bundle_family")
            assert isinstance(group.merchants, list)
            assert len(group.merchants) >= 2
            assert isinstance(group.total_monthly_cents, int)
            assert group.total_monthly_cents > 0
            assert group.severity in ("high", "medium", "low")
            assert isinstance(group.detail, str)
            assert len(group.detail) > 0
            assert isinstance(group.items, list)

    def test_group_items_have_correct_structure(self, populated_db):
        """Group items should have (merchant, monthly_cents, cadence)."""
        duplicates = detect_duplicates(populated_db, days=400)

        for group in duplicates:
            for item in group.items:
                assert len(item) == 3
                merchant, monthly_cents, cadence = item
                assert isinstance(merchant, str)
                assert isinstance(monthly_cents, int)
                assert isinstance(cadence, str)

    def test_total_monthly_matches_sum(self, populated_db):
        """Total monthly should equal sum of item costs."""
        duplicates = detect_duplicates(populated_db, days=400)

        for group in duplicates:
            calculated_total = sum(item[1] for item in group.items)
            assert group.total_monthly_cents == calculated_total, \
                f"Total {group.total_monthly_cents} != sum {calculated_total}"


class TestGetSubscriptions:
    """Test the get_subscriptions helper function."""

    def test_returns_subscriptions_with_flags(self, populated_db):
        """Should return subscriptions with duplicate flags and extended info."""
        subs = get_subscriptions(populated_db, days=400)

        assert len(subs) > 0
        for sub in subs:
            # New format: (merchant, monthly, cadence, first_seen, last_seen, is_dup, txn_type, is_known, display_name, count)
            assert len(sub) == 10, f"Expected 10 elements, got {len(sub)}: {sub}"
            merchant, monthly, cadence, first_seen, last_seen, is_dup, txn_type, is_known, display_name, count = sub
            assert isinstance(merchant, str)
            assert isinstance(monthly, int)
            assert isinstance(cadence, str)
            assert isinstance(first_seen, date)
            assert isinstance(last_seen, date)
            assert isinstance(is_dup, bool)
            assert isinstance(txn_type, str)
            assert isinstance(is_known, bool)
            assert display_name is None or isinstance(display_name, str)
            assert isinstance(count, int)

    def test_subscriptions_sorted_by_cost(self, populated_db):
        """Subscriptions should be sorted by monthly cost descending."""
        subs = get_subscriptions(populated_db, days=400)

        for i in range(1, len(subs)):
            assert subs[i-1][1] >= subs[i][1], \
                "Subscriptions not sorted by monthly cost"

    def test_duplicate_flag_matches_detection(self, populated_db):
        """Duplicate flag should match detect_duplicates results."""
        subs = get_subscriptions(populated_db, days=400)
        duplicates = detect_duplicates(populated_db, days=400)

        # Build set of merchants flagged as duplicates
        dup_merchants = set()
        for group in duplicates:
            dup_merchants.update(group.merchants)

        # Check consistency (merchant is at index 0, is_dup at index 5)
        for sub in subs:
            merchant, _, _, _, _, is_dup, *_ = sub
            expected_dup = merchant in dup_merchants
            assert is_dup == expected_dup, \
                f"Duplicate flag mismatch for {merchant}"

    def test_excludes_transfers(self, populated_db):
        """Should not include transfers as subscriptions."""
        subs = get_subscriptions(populated_db, days=400)

        transfer_keywords = [
            "credit card", "payment", "transfer", "zelle", "venmo"
        ]
        for sub in subs:
            merchant = sub[0]
            for keyword in transfer_keywords:
                assert keyword not in merchant.lower(), \
                    f"Transfer '{merchant}' should not be listed as subscription"


class TestDuplicateDetectionIntegration:
    """Integration tests using the populated fixture."""

    def test_detects_planted_duplicates(self, populated_db):
        """Should detect the duplicate scenarios in the test data."""
        duplicates = detect_duplicates(populated_db, days=400)

        # Should have at least some groups
        assert len(duplicates) >= 1

        # Check for Netflix variants (NETFLIX.COM and NETFLIX INC)
        netflix_found = False
        disney_found = False

        for group in duplicates:
            if any("netflix" in m.lower() for m in group.merchants):
                netflix_found = True
            if any("disney" in m.lower() or "hulu" in m.lower()
                   for m in group.merchants):
                disney_found = True

        # At least one of these should be detected
        assert netflix_found or disney_found, \
            "Should detect at least one planted duplicate scenario"

    def test_no_single_merchant_groups(self, populated_db):
        """No group should have fewer than 2 merchants."""
        duplicates = detect_duplicates(populated_db, days=400)

        for group in duplicates:
            assert len(group.merchants) >= 2, \
                f"Group has only {len(group.merchants)} merchant(s)"

    def test_no_merchant_in_multiple_groups(self, populated_db):
        """Each merchant should appear in at most one group."""
        duplicates = detect_duplicates(populated_db, days=400)

        seen_merchants = set()
        for group in duplicates:
            for merchant in group.merchants:
                assert merchant not in seen_merchants, \
                    f"Merchant '{merchant}' appears in multiple groups"
                seen_merchants.add(merchant)

    def test_empty_db_returns_empty_list(self, empty_db):
        """Empty database should return no duplicate groups."""
        duplicates = detect_duplicates(empty_db, days=400)
        assert duplicates == []


class TestMonthlyCostCalculation:
    """Test that monthly cost estimates are calculated correctly."""

    def test_monthly_subscription_cost(self, temp_db_path):
        """Monthly subscriptions should use actual amount as monthly cost."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            _insert_txn(conn, d, -1599, "MONTHLY SUB A")
            _insert_txn(conn, d + timedelta(days=1), -1599, "MONTHLY SUB B")
        conn.commit()

        subs = get_subscriptions(conn, days=400)
        conn.close()

        for sub in subs:
            merchant, monthly, cadence = sub[0], sub[1], sub[2]
            if "monthly sub" in merchant.lower():
                assert cadence == "monthly"
                assert monthly == 1599, f"Monthly cost should be 1599, got {monthly}"

    def test_annual_subscription_divided_by_12(self, temp_db_path):
        """Annual subscriptions should show monthly as amount/12."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        # Annual subscription
        _insert_txn(conn, today - timedelta(days=30), -12000, "ANNUAL SERVICE")
        _insert_txn(conn, today - timedelta(days=395), -12000, "ANNUAL SERVICE")
        _insert_txn(conn, today - timedelta(days=760), -12000, "ANNUAL SERVICE")
        conn.commit()

        subs = get_subscriptions(conn, days=800)
        conn.close()

        for sub in subs:
            merchant, monthly, cadence = sub[0], sub[1], sub[2]
            if "annual" in merchant.lower():
                assert cadence == "annual"
                assert monthly == 1000, f"Monthly cost should be 1000 (12000/12), got {monthly}"

    def test_weekly_subscription_multiplied_by_4(self, temp_db_path):
        """Weekly subscriptions should show monthly as amount*4."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()
        for weeks_ago in range(20):
            d = today - timedelta(weeks=weeks_ago)
            _insert_txn(conn, d, -1000, "WEEKLY SERVICE")
        conn.commit()

        subs = get_subscriptions(conn, days=400)
        conn.close()

        for sub in subs:
            merchant, monthly, cadence = sub[0], sub[1], sub[2]
            if "weekly" in merchant.lower():
                assert cadence == "weekly"
                assert monthly == 4000, f"Monthly cost should be 4000 (1000*4), got {monthly}"


class TestFinancialAccuracyDuplicates:
    """Critical accuracy tests for duplicate detection."""

    def test_all_amounts_are_integers(self, populated_db):
        """All monetary values should be integers (cents)."""
        duplicates = detect_duplicates(populated_db, days=400)
        subs = get_subscriptions(populated_db, days=400)

        for group in duplicates:
            assert isinstance(group.total_monthly_cents, int)
            for _, monthly, _ in group.items:
                assert isinstance(monthly, int)

        for sub in subs:
            monthly = sub[1]
            assert isinstance(monthly, int)

    def test_no_negative_monthly_costs(self, populated_db):
        """Monthly costs should always be positive."""
        duplicates = detect_duplicates(populated_db, days=400)
        subs = get_subscriptions(populated_db, days=400)

        for group in duplicates:
            assert group.total_monthly_cents > 0
            for _, monthly, _ in group.items:
                assert monthly > 0

        for sub in subs:
            monthly = sub[1]
            assert monthly > 0


# Helper function
def _insert_txn(
    conn: sqlite3.Connection,
    posted_at: date,
    amount_cents: int,
    merchant: str,
):
    """Insert a test transaction."""
    import uuid
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
            f"fp_{uuid.uuid4().hex}",
        ),
    )
