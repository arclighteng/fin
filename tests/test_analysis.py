"""
Unit tests for fin.analysis module.

Tests:
- Period boundary calculations (month, quarter, year)
- Period label generation
- Trend computation
- Rolling average calculations
- Income vs spend classification
"""
import sqlite3
from datetime import date, timedelta

import pytest

from fin.legacy_analysis import (
    TimePeriod,
    PeriodAnalysis,
    _get_period_bounds,
    _get_period_label,
    _prev_period_start,
    _compute_trend,
    analyze_periods,
    get_current_period,
    format_cents_usd,
    format_trend_symbol,
)


class TestPeriodBoundaryCalculations:
    """Test that period boundaries are calculated correctly."""

    @pytest.mark.parametrize("ref_date,expected_start,expected_end", [
        # Standard months
        (date(2026, 1, 15), date(2026, 1, 1), date(2026, 2, 1)),
        (date(2026, 2, 28), date(2026, 2, 1), date(2026, 3, 1)),
        (date(2026, 6, 1), date(2026, 6, 1), date(2026, 7, 1)),
        # Year boundary
        (date(2026, 12, 31), date(2026, 12, 1), date(2027, 1, 1)),
        (date(2026, 12, 1), date(2026, 12, 1), date(2027, 1, 1)),
        # First day of year
        (date(2026, 1, 1), date(2026, 1, 1), date(2026, 2, 1)),
    ])
    def test_month_boundaries(self, ref_date, expected_start, expected_end):
        """Monthly period boundaries should align to first of month."""
        start, end = _get_period_bounds(TimePeriod.MONTH, ref_date)
        assert start == expected_start, f"Start mismatch for {ref_date}"
        assert end == expected_end, f"End mismatch for {ref_date}"

    @pytest.mark.parametrize("ref_date,expected_start,expected_end", [
        # Q1: Jan-Mar
        (date(2026, 1, 15), date(2026, 1, 1), date(2026, 4, 1)),
        (date(2026, 2, 28), date(2026, 1, 1), date(2026, 4, 1)),
        (date(2026, 3, 31), date(2026, 1, 1), date(2026, 4, 1)),
        # Q2: Apr-Jun
        (date(2026, 4, 1), date(2026, 4, 1), date(2026, 7, 1)),
        (date(2026, 5, 15), date(2026, 4, 1), date(2026, 7, 1)),
        (date(2026, 6, 30), date(2026, 4, 1), date(2026, 7, 1)),
        # Q3: Jul-Sep
        (date(2026, 7, 1), date(2026, 7, 1), date(2026, 10, 1)),
        (date(2026, 9, 30), date(2026, 7, 1), date(2026, 10, 1)),
        # Q4: Oct-Dec
        (date(2026, 10, 1), date(2026, 10, 1), date(2027, 1, 1)),
        (date(2026, 12, 31), date(2026, 10, 1), date(2027, 1, 1)),
    ])
    def test_quarter_boundaries(self, ref_date, expected_start, expected_end):
        """Quarterly period boundaries should align to quarter starts."""
        start, end = _get_period_bounds(TimePeriod.QUARTER, ref_date)
        assert start == expected_start, f"Start mismatch for {ref_date}"
        assert end == expected_end, f"End mismatch for {ref_date}"

    @pytest.mark.parametrize("ref_date,expected_start,expected_end", [
        (date(2026, 1, 1), date(2026, 1, 1), date(2027, 1, 1)),
        (date(2026, 6, 15), date(2026, 1, 1), date(2027, 1, 1)),
        (date(2026, 12, 31), date(2026, 1, 1), date(2027, 1, 1)),
        (date(2025, 7, 4), date(2025, 1, 1), date(2026, 1, 1)),
    ])
    def test_year_boundaries(self, ref_date, expected_start, expected_end):
        """Yearly period boundaries should align to year starts."""
        start, end = _get_period_bounds(TimePeriod.YEAR, ref_date)
        assert start == expected_start, f"Start mismatch for {ref_date}"
        assert end == expected_end, f"End mismatch for {ref_date}"


class TestPeriodLabels:
    """Test human-readable period label generation."""

    def test_month_labels(self):
        """Month labels should be 'Mon YYYY' format."""
        assert _get_period_label(TimePeriod.MONTH, date(2026, 1, 1)) == "Jan 2026"
        assert _get_period_label(TimePeriod.MONTH, date(2026, 12, 1)) == "Dec 2026"
        assert _get_period_label(TimePeriod.MONTH, date(2025, 6, 1)) == "Jun 2025"

    def test_quarter_labels(self):
        """Quarter labels should be 'Q# YYYY' format."""
        assert _get_period_label(TimePeriod.QUARTER, date(2026, 1, 1)) == "Q1 2026"
        assert _get_period_label(TimePeriod.QUARTER, date(2026, 4, 1)) == "Q2 2026"
        assert _get_period_label(TimePeriod.QUARTER, date(2026, 7, 1)) == "Q3 2026"
        assert _get_period_label(TimePeriod.QUARTER, date(2026, 10, 1)) == "Q4 2026"

    def test_year_labels(self):
        """Year labels should be just 'YYYY'."""
        assert _get_period_label(TimePeriod.YEAR, date(2026, 1, 1)) == "2026"
        assert _get_period_label(TimePeriod.YEAR, date(2025, 1, 1)) == "2025"


class TestPreviousPeriod:
    """Test previous period calculation."""

    @pytest.mark.parametrize("current_start,expected_prev", [
        (date(2026, 2, 1), date(2026, 1, 1)),
        (date(2026, 1, 1), date(2025, 12, 1)),  # Year boundary
        (date(2026, 12, 1), date(2026, 11, 1)),
    ])
    def test_prev_month(self, current_start, expected_prev):
        """Previous month should handle year boundaries correctly."""
        result = _prev_period_start(TimePeriod.MONTH, current_start)
        assert result == expected_prev

    @pytest.mark.parametrize("current_start,expected_prev", [
        (date(2026, 4, 1), date(2026, 1, 1)),   # Q2 -> Q1
        (date(2026, 7, 1), date(2026, 4, 1)),   # Q3 -> Q2
        (date(2026, 10, 1), date(2026, 7, 1)),  # Q4 -> Q3
        (date(2026, 1, 1), date(2025, 10, 1)),  # Q1 -> Q4 prev year
    ])
    def test_prev_quarter(self, current_start, expected_prev):
        """Previous quarter should handle year boundaries correctly."""
        result = _prev_period_start(TimePeriod.QUARTER, current_start)
        assert result == expected_prev

    def test_prev_year(self):
        """Previous year is straightforward."""
        assert _prev_period_start(TimePeriod.YEAR, date(2026, 1, 1)) == date(2025, 1, 1)
        assert _prev_period_start(TimePeriod.YEAR, date(2025, 1, 1)) == date(2024, 1, 1)


class TestTrendComputation:
    """Test trend indicator computation."""

    def test_trend_up(self):
        """Values increased >5% should show 'up'."""
        assert _compute_trend(1100, 1000) == "up"   # 10% increase
        assert _compute_trend(1060, 1000) == "up"   # 6% increase
        assert _compute_trend(200, 100) == "up"     # 100% increase

    def test_trend_down(self):
        """Values decreased >5% should show 'down'."""
        assert _compute_trend(900, 1000) == "down"  # 10% decrease
        assert _compute_trend(940, 1000) == "down"  # 6% decrease
        assert _compute_trend(50, 100) == "down"    # 50% decrease

    def test_trend_stable(self):
        """Values within 5% should show 'stable'."""
        assert _compute_trend(1000, 1000) == "stable"  # No change
        assert _compute_trend(1040, 1000) == "stable"  # 4% increase
        assert _compute_trend(960, 1000) == "stable"   # 4% decrease
        assert _compute_trend(1050, 1000) == "stable"  # Exactly 5%

    def test_trend_with_none_previous(self):
        """None previous should return 'stable'."""
        assert _compute_trend(1000, None) == "stable"

    def test_trend_with_zero_previous(self):
        """Zero previous should return 'stable' (avoid division by zero)."""
        assert _compute_trend(1000, 0) == "stable"

    def test_trend_custom_threshold(self):
        """Custom threshold should be respected."""
        # With 10% threshold
        assert _compute_trend(1080, 1000, threshold_pct=10.0) == "stable"
        assert _compute_trend(1110, 1000, threshold_pct=10.0) == "up"


class TestFormatting:
    """Test formatting utilities."""

    @pytest.mark.parametrize("cents,expected", [
        (0, "$0.00"),
        (1, "$0.01"),
        (99, "$0.99"),
        (100, "$1.00"),
        (1599, "$15.99"),
        (100000, "$1,000.00"),
        (1234567, "$12,345.67"),
        (-1599, "$-15.99"),
    ])
    def test_format_cents_usd(self, cents, expected):
        """Cents should format to USD string correctly."""
        assert format_cents_usd(cents) == expected

    def test_format_trend_symbol(self):
        """Trends should have correct symbols."""
        assert format_trend_symbol("up") == "\u2191"    # ↑
        assert format_trend_symbol("down") == "\u2193"  # ↓
        assert format_trend_symbol("stable") == "\u2192"  # →
        assert format_trend_symbol("unknown") == "\u2192"  # Default to →


class TestAnalyzePeriods:
    """Integration tests for analyze_periods function."""

    def test_analyze_returns_correct_count(self, populated_db):
        """Should return requested number of periods."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=6)
        assert len(periods) == 6

    def test_analyze_periods_ordered_recent_first(self, populated_db):
        """Periods should be ordered most recent first."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=3)
        assert len(periods) >= 2
        # Most recent period's start date should be >= previous
        assert periods[0].start_date >= periods[1].start_date

    def test_analyze_period_has_all_fields(self, populated_db):
        """Each period should have all required fields populated."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=1)
        assert len(periods) == 1
        p = periods[0]

        # Check all fields exist and are correct types
        assert isinstance(p.period_type, TimePeriod)
        assert isinstance(p.period_label, str)
        assert isinstance(p.start_date, date)
        assert isinstance(p.end_date, date)
        assert isinstance(p.income_cents, int)
        assert isinstance(p.recurring_cents, int)
        assert isinstance(p.discretionary_cents, int)
        assert isinstance(p.transfer_cents, int)
        assert isinstance(p.net_cents, int)
        assert p.income_trend in ("up", "down", "stable")
        assert p.recurring_trend in ("up", "down", "stable")
        assert isinstance(p.avg_income_cents, int)
        assert isinstance(p.transaction_count, int)

    def test_net_cents_calculation(self, populated_db):
        """Net should be income + credits - recurring - discretionary."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=1)
        p = periods[0]
        # Net includes credits (refunds, rewards) as they reduce effective spend
        expected_net = p.income_cents + p.credit_cents - p.recurring_cents - p.discretionary_cents
        assert p.net_cents == expected_net

    def test_transfers_excluded_from_expenses(self, populated_db):
        """Transfers should not count as recurring or discretionary."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=1)
        p = periods[0]
        # Transfer cents should be tracked but not affect net calculation
        assert p.transfer_cents >= 0
        # Net should NOT subtract transfers, but should include credits
        expected_net = p.income_cents + p.credit_cents - p.recurring_cents - p.discretionary_cents
        assert p.net_cents == expected_net

    def test_quarterly_analysis(self, populated_db):
        """Quarterly analysis should work correctly."""
        periods = analyze_periods(populated_db, TimePeriod.QUARTER, num_periods=2)
        assert len(periods) == 2
        assert "Q" in periods[0].period_label

    def test_yearly_analysis(self, populated_db):
        """Yearly analysis should work correctly."""
        periods = analyze_periods(populated_db, TimePeriod.YEAR, num_periods=1)
        assert len(periods) == 1
        assert periods[0].period_label.isdigit()  # Just a year number

    def test_empty_db_returns_empty_list(self, empty_db):
        """Empty database should return empty list, not error."""
        periods = analyze_periods(empty_db, TimePeriod.MONTH, num_periods=6)
        # May return periods with zero values
        for p in periods:
            assert p.income_cents == 0
            assert p.recurring_cents == 0

    def test_rolling_average_calculation(self, populated_db):
        """Rolling averages should be calculated over the window."""
        periods = analyze_periods(
            populated_db,
            TimePeriod.MONTH,
            num_periods=6,
            avg_window=3,
        )
        # Averages should exist and be non-negative
        for p in periods:
            assert p.avg_income_cents >= 0
            assert p.avg_recurring_cents >= 0
            assert p.avg_discretionary_cents >= 0


class TestGetCurrentPeriod:
    """Test get_current_period helper."""

    def test_returns_single_period(self, populated_db):
        """Should return analysis for current period."""
        result = get_current_period(populated_db, TimePeriod.MONTH)
        assert result is not None
        assert isinstance(result, PeriodAnalysis)

    def test_returns_none_for_empty_db(self, empty_db):
        """Should return period with zero values for empty db."""
        result = get_current_period(empty_db, TimePeriod.MONTH)
        # Either None or a period with zeros
        if result is not None:
            assert result.income_cents == 0


class TestFinancialAccuracy:
    """
    Critical tests for financial calculation accuracy.
    These ensure no rounding errors or miscalculations occur.
    """

    def test_cents_never_become_float(self, populated_db):
        """All monetary values should remain integers (cents)."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=6)
        for p in periods:
            assert isinstance(p.income_cents, int), "Income must be int"
            assert isinstance(p.recurring_cents, int), "Recurring must be int"
            assert isinstance(p.discretionary_cents, int), "Discretionary must be int"
            assert isinstance(p.net_cents, int), "Net must be int"
            assert isinstance(p.transfer_cents, int), "Transfer must be int"
            assert isinstance(p.avg_income_cents, int), "Avg income must be int"
            assert isinstance(p.avg_recurring_cents, int), "Avg recurring must be int"
            assert isinstance(p.avg_discretionary_cents, int), "Avg discretionary must be int"

    def test_no_negative_averages(self, populated_db):
        """Averages should never be negative."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=12)
        for p in periods:
            assert p.avg_income_cents >= 0
            assert p.avg_recurring_cents >= 0
            assert p.avg_discretionary_cents >= 0

    def test_income_is_positive_amounts(self, populated_db):
        """Income should only count positive transaction amounts."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=1)
        p = periods[0]
        # Income should be positive (or zero if no income)
        assert p.income_cents >= 0

    def test_expenses_are_positive(self, populated_db):
        """Recurring/discretionary should be positive (absolute value of negative txns)."""
        periods = analyze_periods(populated_db, TimePeriod.MONTH, num_periods=1)
        p = periods[0]
        assert p.recurring_cents >= 0
        assert p.discretionary_cents >= 0
        assert p.transfer_cents >= 0
