"""
Tests for dates.py - canonical date handling.

TRUTH CONTRACT verification:
- All date ranges are end-exclusive
- start <= posted_at < end_exclusive
"""
from datetime import date
import pytest

from fin.dates import (
    TimePeriod,
    period_bounds,
    this_month,
    last_month,
    this_quarter,
    this_year,
    prev_period_start,
    period_label,
    custom_bounds,
    date_range_days,
    iter_periods,
    is_in_range,
    days_until_end_of_month,
)


class TestPeriodBounds:
    """Test period boundary calculations."""

    def test_month_mid_month(self):
        """Mid-month date should give first-to-first bounds."""
        start, end = period_bounds(TimePeriod.MONTH, date(2026, 1, 15))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 1)

    def test_month_first_day(self):
        """First day of month should work correctly."""
        start, end = period_bounds(TimePeriod.MONTH, date(2026, 1, 1))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 1)

    def test_month_last_day(self):
        """Last day of month should still use first-to-first."""
        start, end = period_bounds(TimePeriod.MONTH, date(2026, 1, 31))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 1)

    def test_month_december_to_january(self):
        """December should roll to January of next year."""
        start, end = period_bounds(TimePeriod.MONTH, date(2026, 12, 15))
        assert start == date(2026, 12, 1)
        assert end == date(2027, 1, 1)

    def test_quarter_q1(self):
        """Q1 is Jan-Mar."""
        start, end = period_bounds(TimePeriod.QUARTER, date(2026, 2, 15))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 4, 1)

    def test_quarter_q2(self):
        """Q2 is Apr-Jun."""
        start, end = period_bounds(TimePeriod.QUARTER, date(2026, 5, 15))
        assert start == date(2026, 4, 1)
        assert end == date(2026, 7, 1)

    def test_quarter_q3(self):
        """Q3 is Jul-Sep."""
        start, end = period_bounds(TimePeriod.QUARTER, date(2026, 8, 15))
        assert start == date(2026, 7, 1)
        assert end == date(2026, 10, 1)

    def test_quarter_q4(self):
        """Q4 is Oct-Dec."""
        start, end = period_bounds(TimePeriod.QUARTER, date(2026, 11, 15))
        assert start == date(2026, 10, 1)
        assert end == date(2027, 1, 1)

    def test_year(self):
        """Year bounds are Jan 1 to Jan 1."""
        start, end = period_bounds(TimePeriod.YEAR, date(2026, 6, 15))
        assert start == date(2026, 1, 1)
        assert end == date(2027, 1, 1)


class TestEndExclusiveInvariants:
    """Verify end-exclusive behavior."""

    def test_jan_31_not_in_february(self):
        """Jan 31 should be in January, not February."""
        _, jan_end = period_bounds(TimePeriod.MONTH, date(2026, 1, 15))
        feb_start, _ = period_bounds(TimePeriod.MONTH, date(2026, 2, 15))

        # Jan end IS Feb start (both Feb 1)
        assert jan_end == feb_start

        # Jan 31 is < Feb 1, so it's in January
        assert date(2026, 1, 31) < jan_end
        assert not (date(2026, 1, 31) >= feb_start)

    def test_feb_1_is_in_february(self):
        """Feb 1 should be in February."""
        feb_start, feb_end = period_bounds(TimePeriod.MONTH, date(2026, 2, 15))

        assert is_in_range(date(2026, 2, 1), feb_start, feb_end)
        assert not is_in_range(date(2026, 1, 31), feb_start, feb_end)

    def test_no_gaps_between_months(self):
        """End of one month equals start of next (no gaps)."""
        _, jan_end = period_bounds(TimePeriod.MONTH, date(2026, 1, 15))
        feb_start, feb_end = period_bounds(TimePeriod.MONTH, date(2026, 2, 15))
        mar_start, _ = period_bounds(TimePeriod.MONTH, date(2026, 3, 15))

        assert jan_end == feb_start
        assert feb_end == mar_start

    def test_no_overlaps_between_months(self):
        """Same date can't be in two months."""
        jan_start, jan_end = period_bounds(TimePeriod.MONTH, date(2026, 1, 15))
        feb_start, feb_end = period_bounds(TimePeriod.MONTH, date(2026, 2, 15))

        # Feb 1 is in February only
        assert is_in_range(date(2026, 2, 1), feb_start, feb_end)
        assert not is_in_range(date(2026, 2, 1), jan_start, jan_end)


class TestPrevPeriod:
    """Test previous period calculation."""

    def test_prev_month_standard(self):
        """Previous month in same year."""
        prev = prev_period_start(TimePeriod.MONTH, date(2026, 3, 1))
        assert prev == date(2026, 2, 1)

    def test_prev_month_year_boundary(self):
        """January -> December of previous year."""
        prev = prev_period_start(TimePeriod.MONTH, date(2026, 1, 1))
        assert prev == date(2025, 12, 1)

    def test_prev_quarter_same_year(self):
        """Q2 -> Q1."""
        prev = prev_period_start(TimePeriod.QUARTER, date(2026, 4, 1))
        assert prev == date(2026, 1, 1)

    def test_prev_quarter_year_boundary(self):
        """Q1 -> Q4 of previous year."""
        prev = prev_period_start(TimePeriod.QUARTER, date(2026, 1, 1))
        assert prev == date(2025, 10, 1)

    def test_prev_year(self):
        """2026 -> 2025."""
        prev = prev_period_start(TimePeriod.YEAR, date(2026, 1, 1))
        assert prev == date(2025, 1, 1)


class TestPeriodLabel:
    """Test human-readable labels."""

    def test_month_label(self):
        assert period_label(TimePeriod.MONTH, date(2026, 1, 1)) == "Jan 2026"
        assert period_label(TimePeriod.MONTH, date(2026, 12, 1)) == "Dec 2026"

    def test_quarter_label(self):
        assert period_label(TimePeriod.QUARTER, date(2026, 1, 1)) == "Q1 2026"
        assert period_label(TimePeriod.QUARTER, date(2026, 4, 1)) == "Q2 2026"
        assert period_label(TimePeriod.QUARTER, date(2026, 7, 1)) == "Q3 2026"
        assert period_label(TimePeriod.QUARTER, date(2026, 10, 1)) == "Q4 2026"

    def test_year_label(self):
        assert period_label(TimePeriod.YEAR, date(2026, 1, 1)) == "2026"


class TestCustomBounds:
    """Test user-inclusive to end-exclusive conversion."""

    def test_full_month_inclusive(self):
        """User enters Jan 1-31, we convert to Jan 1 - Feb 1."""
        start, end = custom_bounds(date(2026, 1, 1), date(2026, 1, 31))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 2, 1)

    def test_single_day(self):
        """Single day range: Jan 15-15 -> Jan 15-16."""
        start, end = custom_bounds(date(2026, 1, 15), date(2026, 1, 15))
        assert start == date(2026, 1, 15)
        assert end == date(2026, 1, 16)


class TestDateRangeDays:
    """Test day counting."""

    def test_one_month(self):
        """January has 31 days."""
        days = date_range_days(date(2026, 1, 1), date(2026, 2, 1))
        assert days == 31

    def test_february_non_leap(self):
        """February 2026 has 28 days."""
        days = date_range_days(date(2026, 2, 1), date(2026, 3, 1))
        assert days == 28

    def test_single_day(self):
        """Single day range."""
        days = date_range_days(date(2026, 1, 15), date(2026, 1, 16))
        assert days == 1


class TestIterPeriods:
    """Test period iteration."""

    def test_iter_months(self):
        """Should return requested number of months, most recent first."""
        periods = iter_periods(TimePeriod.MONTH, 3, anchor=date(2026, 3, 15))

        assert len(periods) == 3

        # Most recent first
        assert periods[0] == (date(2026, 3, 1), date(2026, 4, 1), "Mar 2026")
        assert periods[1] == (date(2026, 2, 1), date(2026, 3, 1), "Feb 2026")
        assert periods[2] == (date(2026, 1, 1), date(2026, 2, 1), "Jan 2026")

    def test_iter_across_year(self):
        """Should handle year boundary."""
        periods = iter_periods(TimePeriod.MONTH, 2, anchor=date(2026, 1, 15))

        assert periods[0] == (date(2026, 1, 1), date(2026, 2, 1), "Jan 2026")
        assert periods[1] == (date(2025, 12, 1), date(2026, 1, 1), "Dec 2025")

    def test_iter_quarters(self):
        """Should iterate quarters correctly."""
        periods = iter_periods(TimePeriod.QUARTER, 2, anchor=date(2026, 5, 15))

        assert periods[0] == (date(2026, 4, 1), date(2026, 7, 1), "Q2 2026")
        assert periods[1] == (date(2026, 1, 1), date(2026, 4, 1), "Q1 2026")


class TestIsInRange:
    """Test range membership check."""

    def test_in_range(self):
        assert is_in_range(date(2026, 1, 15), date(2026, 1, 1), date(2026, 2, 1))

    def test_at_start(self):
        """Start date is in range (inclusive)."""
        assert is_in_range(date(2026, 1, 1), date(2026, 1, 1), date(2026, 2, 1))

    def test_at_end(self):
        """End date is NOT in range (exclusive)."""
        assert not is_in_range(date(2026, 2, 1), date(2026, 1, 1), date(2026, 2, 1))

    def test_before_range(self):
        assert not is_in_range(date(2025, 12, 31), date(2026, 1, 1), date(2026, 2, 1))

    def test_after_range(self):
        assert not is_in_range(date(2026, 2, 2), date(2026, 1, 1), date(2026, 2, 1))


class TestConvenienceFunctions:
    """Test this_month, last_month, etc."""

    def test_this_month_returns_tuple(self):
        """Should return (start, end) tuple."""
        result = this_month()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], date)
        assert isinstance(result[1], date)

    def test_last_month_returns_tuple(self):
        result = last_month()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_last_month_is_before_this_month(self):
        """Last month should end where this month starts."""
        last_start, last_end = last_month()
        this_start, _ = this_month()

        # Last month's end should equal this month's start
        assert last_end == this_start

    def test_this_quarter_returns_tuple(self):
        result = this_quarter()
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_this_year_returns_tuple(self):
        result = this_year()
        assert isinstance(result, tuple)
        assert len(result) == 2
