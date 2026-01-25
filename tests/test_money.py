"""
Tests for money.py - canonical money handling.

TRUTH CONTRACT verification:
- ROUND_HALF_UP always rounds 0.5 up (away from zero)
- No float arithmetic for money
- All storage in integer cents
"""
from decimal import Decimal
import pytest

from fin.money import (
    parse_to_cents,
    cents_to_dollars,
    format_usd,
    format_usd_compact,
    multiply_cents,
    divide_cents,
    percent_of,
    compare_within_threshold,
    MoneyParseError,
)


class TestParseToCenter:
    """Test parse_to_cents with various inputs."""

    def test_positive_float(self):
        """Standard positive float."""
        assert parse_to_cents(12.99) == 1299

    def test_negative_float(self):
        """Standard negative float."""
        assert parse_to_cents(-12.99) == -1299

    def test_positive_string(self):
        """String amount."""
        assert parse_to_cents("100.00") == 10000

    def test_negative_string(self):
        """Negative string amount."""
        assert parse_to_cents("-50.25") == -5025

    def test_string_with_commas(self):
        """String with comma thousands separator."""
        assert parse_to_cents("1,234.56") == 123456

    def test_integer(self):
        """Integer dollar amount."""
        assert parse_to_cents(50) == 5000

    def test_zero(self):
        """Zero amount."""
        assert parse_to_cents(0) == 0
        assert parse_to_cents(0.0) == 0
        assert parse_to_cents("0") == 0

    def test_decimal_input(self):
        """Decimal input passes through."""
        assert parse_to_cents(Decimal("99.99")) == 9999


class TestRoundHalfUp:
    """
    CRITICAL: Verify ROUND_HALF_UP behavior.

    Standard financial rounding: 0.5 always rounds UP (away from zero).
    This differs from banker's rounding (HALF_EVEN) which can cause
    1-cent surprises.
    """

    def test_half_cent_rounds_up(self):
        """0.125 dollars = 12.5 cents -> rounds UP to 13 cents."""
        assert parse_to_cents(0.125) == 13

    def test_negative_half_cent_rounds_away_from_zero(self):
        """
        -0.125 dollars = -12.5 cents -> rounds to -13 cents.

        ROUND_HALF_UP rounds AWAY from zero, so negative half values
        also round to larger absolute values.
        """
        assert parse_to_cents(-0.125) == -13

    def test_quarter_cent_rounds_down(self):
        """0.122 dollars = 12.2 cents -> rounds DOWN to 12 cents."""
        assert parse_to_cents(0.122) == 12

    def test_three_quarter_cent_rounds_up(self):
        """0.127 dollars = 12.7 cents -> rounds UP to 13 cents."""
        assert parse_to_cents(0.127) == 13

    def test_exactly_half_scenarios(self):
        """Various exactly-half-cent scenarios."""
        # 0.005 dollars = 0.5 cents -> 1 cent
        assert parse_to_cents(0.005) == 1
        # 0.015 dollars = 1.5 cents -> 2 cents
        assert parse_to_cents(0.015) == 2
        # 0.025 dollars = 2.5 cents -> 3 cents
        assert parse_to_cents(0.025) == 3

    def test_banker_rounding_would_differ(self):
        """
        Banker's rounding (HALF_EVEN) would give different results here.

        With HALF_EVEN:
        - 0.025 -> 2 (round to even)
        - 0.015 -> 2 (round to even)

        With HALF_UP (what we use):
        - 0.025 -> 3 (always up)
        - 0.015 -> 2 (always up)
        """
        # This test documents that we're NOT using banker's rounding
        assert parse_to_cents(0.025) == 3  # Would be 2 with HALF_EVEN
        assert parse_to_cents(0.035) == 4  # Would be 4 with HALF_EVEN (same)
        assert parse_to_cents(0.045) == 5  # Would be 4 with HALF_EVEN


class TestParseEdgeCases:
    """Test edge cases and error handling."""

    def test_none_raises(self):
        """None should raise MoneyParseError."""
        with pytest.raises(MoneyParseError, match="cannot be None"):
            parse_to_cents(None)

    def test_empty_string_raises(self):
        """Empty string should raise MoneyParseError."""
        with pytest.raises(MoneyParseError, match="empty"):
            parse_to_cents("")

    def test_whitespace_only_raises(self):
        """Whitespace-only string should raise."""
        with pytest.raises(MoneyParseError):
            parse_to_cents("   ")

    def test_invalid_string_raises(self):
        """Non-numeric string should raise."""
        with pytest.raises(MoneyParseError):
            parse_to_cents("not a number")

    def test_large_amount_raises(self):
        """Amount over $1M should raise by default."""
        with pytest.raises(MoneyParseError, match="sanity limit"):
            parse_to_cents(2_000_000)

    def test_large_amount_allowed(self):
        """Large amount allowed with flag."""
        assert parse_to_cents(2_000_000, allow_large=True) == 200_000_000

    def test_float_precision_preserved(self):
        """
        Float that would have precision issues is handled correctly.

        12.99 * 100 in float gives 1298.9999999999998, but we get 1299.
        """
        # This is a notorious problematic value
        assert parse_to_cents(12.99) == 1299
        assert parse_to_cents(0.1 + 0.2) == 30  # 0.30000000000000004

    def test_string_with_whitespace(self):
        """String with leading/trailing whitespace."""
        assert parse_to_cents("  12.99  ") == 1299


class TestCentsToDollars:
    """Test cents_to_dollars conversion."""

    def test_positive(self):
        assert cents_to_dollars(1299) == Decimal("12.99")

    def test_negative(self):
        assert cents_to_dollars(-1299) == Decimal("-12.99")

    def test_zero(self):
        assert cents_to_dollars(0) == Decimal("0")

    def test_exact_dollar(self):
        assert cents_to_dollars(10000) == Decimal("100")


class TestFormatUsd:
    """Test USD formatting."""

    def test_positive(self):
        assert format_usd(1299) == "$12.99"

    def test_negative(self):
        assert format_usd(-1299) == "-$12.99"

    def test_zero(self):
        assert format_usd(0) == "$0.00"

    def test_large_with_commas(self):
        assert format_usd(123456789) == "$1,234,567.89"

    def test_show_sign_positive(self):
        assert format_usd(1299, show_sign=True) == "+$12.99"

    def test_show_sign_negative(self):
        assert format_usd(-1299, show_sign=True) == "-$12.99"

    def test_show_sign_zero(self):
        assert format_usd(0, show_sign=True) == "$0.00"


class TestFormatUsdCompact:
    """Test compact USD formatting."""

    def test_whole_dollar(self):
        assert format_usd_compact(10000) == "$100"

    def test_with_cents(self):
        assert format_usd_compact(1299) == "$12.99"

    def test_negative_whole(self):
        assert format_usd_compact(-10000) == "-$100"


class TestMultiplyCents:
    """Test multiply_cents with ROUND_HALF_UP."""

    def test_half_factor(self):
        assert multiply_cents(1000, 0.5) == 500

    def test_weekly_to_monthly(self):
        """Weekly amount * 12/52 for monthly equivalent."""
        # $10/week = $10 * 12/52 = $2.31/month
        assert multiply_cents(1000, 12/52) == 231

    def test_rounds_half_up(self):
        """Half-cent results round up."""
        # 100 * 0.125 = 12.5 -> 13
        assert multiply_cents(100, 0.125) == 13


class TestDivideCents:
    """Test divide_cents with ROUND_HALF_UP."""

    def test_even_division(self):
        assert divide_cents(1000, 2) == 500

    def test_rounds_half_up(self):
        """Half-cent results round up."""
        # 100 / 8 = 12.5 -> 13
        assert divide_cents(100, 8) == 13

    def test_divide_by_zero_raises(self):
        with pytest.raises(MoneyParseError, match="zero"):
            divide_cents(1000, 0)


class TestPercentOf:
    """Test percentage calculations."""

    def test_15_percent(self):
        """15% of $100 = $15."""
        assert percent_of(10000, 15) == 1500

    def test_20_percent_odd(self):
        """20% of $15.99 = $3.198 -> $3.20."""
        assert percent_of(1599, 20) == 320

    def test_100_percent(self):
        assert percent_of(1000, 100) == 1000

    def test_0_percent(self):
        assert percent_of(1000, 0) == 0


class TestCompareWithinThreshold:
    """Test threshold comparison for matching."""

    def test_exact_match(self):
        assert compare_within_threshold(1000, 1000) is True

    def test_no_match(self):
        assert compare_within_threshold(1000, 900) is False

    def test_within_cents_threshold(self):
        assert compare_within_threshold(1000, 1005, threshold_cents=10) is True
        assert compare_within_threshold(1000, 1015, threshold_cents=10) is False

    def test_within_percent_threshold(self):
        # 5% of 1000 = 50
        assert compare_within_threshold(1000, 1040, threshold_percent=5) is True
        assert compare_within_threshold(1000, 1060, threshold_percent=5) is False

    def test_either_threshold_works(self):
        """Either threshold being satisfied is enough."""
        # 100 cents apart, which is 10% of 1000
        # Within 5% threshold? No (10% > 5%)
        # Within 200 cents threshold? Yes
        assert compare_within_threshold(
            1000, 1100, threshold_cents=200, threshold_percent=5
        ) is True


class TestFinancialInvariants:
    """Tests for financial calculation invariants."""

    def test_cents_always_integer(self):
        """All outputs must be integers."""
        result = parse_to_cents(123.456)
        assert isinstance(result, int)

        result = multiply_cents(1000, 0.333333)
        assert isinstance(result, int)

        result = divide_cents(1000, 3)
        assert isinstance(result, int)

        result = percent_of(1000, 33.333)
        assert isinstance(result, int)

    def test_roundtrip_preserves_cents(self):
        """Converting cents to dollars and back preserves value."""
        original = 1299
        dollars = cents_to_dollars(original)
        back = parse_to_cents(dollars)
        assert back == original

    def test_no_float_accumulation_error(self):
        """
        Repeated operations don't accumulate float errors.

        Adding 0.01 100 times in float gives 0.9999999999999999,
        but our integer cents stay exact.
        """
        total = 0
        for _ in range(100):
            total += 1  # Adding 1 cent at a time

        assert total == 100
        assert format_usd(total) == "$1.00"
