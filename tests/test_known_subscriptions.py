"""
Tests for known subscription service detection.

Tests:
- Known subscription service matching
- Longest-pattern-first matching (YouTube TV vs YouTube)
- Display name and cadence lookup
- Integration with get_subscriptions
"""
import sqlite3
from datetime import date, timedelta

import pytest

from fin.legacy_classify import (
    KNOWN_SUBSCRIPTIONS,
    _SORTED_SUBSCRIPTION_PATTERNS,
    _match_known_subscription,
    get_subscriptions,
)
from fin import db as dbmod


class TestKnownSubscriptionsRegistry:
    """Test the known subscriptions registry."""

    def test_registry_not_empty(self):
        """Registry should have entries."""
        assert len(KNOWN_SUBSCRIPTIONS) > 100, "Should have 100+ known services"

    def test_registry_has_common_services(self):
        """Registry should include common streaming services."""
        # Check for patterns that exist in the registry
        common_services = ["netflix", "spotify", "hulu", "disney+", "hbo max"]
        for service in common_services:
            assert service in KNOWN_SUBSCRIPTIONS, f"{service} missing from registry"

    def test_registry_values_have_correct_format(self):
        """Each entry should have (display_name, cadence) tuple."""
        for pattern, value in KNOWN_SUBSCRIPTIONS.items():
            assert isinstance(value, tuple), f"Value for {pattern} should be tuple"
            assert len(value) == 2, f"Value for {pattern} should have 2 elements"
            display_name, cadence = value
            assert isinstance(display_name, str), f"Display name for {pattern} should be str"
            assert isinstance(cadence, str), f"Cadence for {pattern} should be str"
            assert cadence in ("monthly", "annual", "weekly"), \
                f"Invalid cadence '{cadence}' for {pattern}"


class TestSortedPatterns:
    """Test that patterns are sorted by length for longest-first matching."""

    def test_patterns_sorted_by_length(self):
        """Patterns should be sorted longest first."""
        lengths = [len(p[0]) for p in _SORTED_SUBSCRIPTION_PATTERNS]
        assert lengths == sorted(lengths, reverse=True), \
            "Patterns not sorted by length descending"

    def test_youtube_tv_before_youtube(self):
        """YouTube TV should match before generic YouTube."""
        # Find indices of patterns
        patterns = [p[0] for p in _SORTED_SUBSCRIPTION_PATTERNS]

        youtube_tv_idx = None
        youtube_idx = None

        for i, pattern in enumerate(patterns):
            if pattern == "youtube tv":
                youtube_tv_idx = i
            elif pattern == "youtube":
                youtube_idx = i

        if youtube_tv_idx is not None and youtube_idx is not None:
            assert youtube_tv_idx < youtube_idx, \
                "youtube tv should come before youtube for longest-first matching"


class TestLookupKnownSubscription:
    """Test the _match_known_subscription function."""

    @pytest.mark.parametrize("merchant,expected_name", [
        ("NETFLIX.COM", "Netflix"),
        ("Netflix Inc", "Netflix"),
        ("SPOTIFY USA", "Spotify"),
        ("HBO MAX", "Max"),  # HBO Max was rebranded to Max
        ("DISNEY PLUS", "Disney+"),
        ("AMAZON PRIME", "Amazon Prime"),
    ])
    def test_lookup_common_services(self, merchant, expected_name):
        """Should find common services by merchant name."""
        result = _match_known_subscription(merchant.lower())
        assert result is not None, f"Should find {merchant}"
        display_name, cadence = result
        assert display_name == expected_name

    def test_lookup_returns_none_for_unknown(self):
        """Should return None for unknown merchants."""
        result = _match_known_subscription("random unknown merchant xyz")
        assert result is None

    @pytest.mark.parametrize("merchant", [
        "BURGER KING",           # Should NOT match "ring"
        "SPRING WATER CO",       # Should NOT match "ring"
        "CARLO'S BAKERY",        # Should NOT match "arlo"
        "EARNEST FINANCIAL",     # Should NOT match "nest"
        "HONEST TEA",            # Should NOT match "nest"
        "STEAMBOAT SPRINGS",     # Could potentially match "steam" - acceptable
        "CALMART GROCERY",       # Should NOT match "calm"
        "ADT AUTO PARTS",        # Should NOT match "adt" (needs "adt security")
    ])
    def test_no_false_positives_for_common_words(self, merchant):
        """Should NOT match merchants that happen to contain subscription keywords."""
        result = _match_known_subscription(merchant.lower())
        # These should all return None (no false match)
        assert result is None, f"False positive: {merchant} matched as {result}"

    @pytest.mark.parametrize("merchant,expected_name", [
        ("YOUTUBE TV", "YouTube TV"),
        ("YOUTUBETV", "YouTube TV"),
        ("YOUTUBE PREMIUM", "YouTube Premium"),
        ("YOUTUBE MUSIC", "YouTube Music"),
    ])
    def test_distinguishes_youtube_services(self, merchant, expected_name):
        """Should correctly distinguish YouTube TV, Premium, and Music."""
        result = _match_known_subscription(merchant.lower())
        assert result is not None, f"Should find {merchant}"
        display_name, cadence = result
        assert display_name == expected_name, \
            f"Expected {expected_name} for {merchant}, got {display_name}"

    @pytest.mark.parametrize("merchant,expected_name", [
        ("GOOGLE FIBER", "Google Fiber"),
        ("GOOGLE FI", "Google Fi"),
        ("GOOGLE ONE", "Google One"),
    ])
    def test_distinguishes_google_services(self, merchant, expected_name):
        """Should correctly distinguish Google Fiber, Fi, and One."""
        result = _match_known_subscription(merchant.lower())
        assert result is not None, f"Should find {merchant}"
        display_name, cadence = result
        assert display_name == expected_name, \
            f"Expected {expected_name} for {merchant}, got {display_name}"


class TestKnownServiceIntegration:
    """Integration tests for known service detection in get_subscriptions."""

    @pytest.fixture
    def db_with_known_services(self, temp_db_path):
        """Create database with known subscription services."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()

        def insert_txn(posted_at, amount_cents, merchant):
            import uuid
            conn.execute(
                """
                INSERT INTO transactions (
                    account_id, posted_at, amount_cents, currency,
                    description, merchant, fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, 'USD', '', ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    "acct1",
                    posted_at.isoformat(),
                    amount_cents,
                    merchant,
                    f"fp_{uuid.uuid4().hex}",
                ),
            )

        # Add known services with recurring patterns
        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            insert_txn(d, -1599, "NETFLIX.COM")
            insert_txn(d + timedelta(days=1), -1099, "SPOTIFY")
            insert_txn(d + timedelta(days=2), -6499, "YOUTUBE TV")

        conn.commit()
        yield conn
        conn.close()

    def test_subscriptions_include_is_known_flag(self, db_with_known_services):
        """Subscriptions should include is_known_service flag."""
        subs = get_subscriptions(db_with_known_services, days=400)

        # Find Netflix
        netflix_subs = [s for s in subs if "netflix" in s[0].lower()]
        assert len(netflix_subs) >= 1

        # Check is_known flag (index 7)
        for sub in netflix_subs:
            is_known = sub[7]
            assert is_known is True, "Netflix should be marked as known service"

    def test_subscriptions_include_display_name(self, db_with_known_services):
        """Known subscriptions should include display name."""
        subs = get_subscriptions(db_with_known_services, days=400)

        # Find Netflix
        netflix_subs = [s for s in subs if "netflix" in s[0].lower()]
        assert len(netflix_subs) >= 1

        # Check display_name (index 8)
        for sub in netflix_subs:
            display_name = sub[8]
            assert display_name == "Netflix", f"Expected 'Netflix', got '{display_name}'"

    def test_unknown_merchant_not_marked_known(self, temp_db_path):
        """Unknown merchants should not be marked as known."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)

        today = date.today()

        for months_ago in range(6):
            d = today - timedelta(days=months_ago * 30)
            import uuid
            conn.execute(
                """
                INSERT INTO transactions (
                    account_id, posted_at, amount_cents, currency,
                    description, merchant, fingerprint, created_at, updated_at
                ) VALUES (?, ?, ?, 'USD', '', ?, ?, datetime('now'), datetime('now'))
                """,
                ("acct1", d.isoformat(), -999, "RANDOM UNKNOWN SERVICE", f"fp_{uuid.uuid4().hex}"),
            )

        conn.commit()

        subs = get_subscriptions(conn, days=400)
        conn.close()

        random_subs = [s for s in subs if "random" in s[0].lower()]
        if random_subs:
            is_known = random_subs[0][7]
            assert is_known is False, "Unknown service should not be marked as known"
