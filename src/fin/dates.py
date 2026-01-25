# dates.py
"""
Canonical date handling with end-exclusive ranges.

TRUTH CONTRACT:
- All internal date ranges are end-exclusive: start <= posted_at < end_exclusive
- January 2026: 2026-01-01 to 2026-02-01 (exclusive)
- User-facing inputs converted at boundary
- SQL queries always use < end_exclusive

This is the ONLY module that should compute period boundaries.
"""
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo


class TimePeriod(Enum):
    """Standard time periods for analysis."""
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


def get_timezone() -> Optional[ZoneInfo]:
    """
    Get configured timezone, or None if not configured.

    Uses lazy import to avoid circular dependencies.
    Returns None if config doesn't exist or has no timezone.
    """
    try:
        from .config import load_config
        cfg = load_config()
        tz_name = getattr(cfg, 'timezone', None)
        if tz_name:
            return ZoneInfo(tz_name)
        return None
    except Exception:
        return None


def today(tz: Optional[ZoneInfo] = None) -> date:
    """
    Get today's date in the configured timezone.

    If no timezone is configured, uses local system date.

    Args:
        tz: Optional timezone override

    Returns:
        Today's date
    """
    if tz is None:
        tz = get_timezone()

    if tz is not None:
        return datetime.now(tz).date()
    else:
        # Use local system date when no timezone configured
        return date.today()


def epoch_to_date(epoch_seconds: int, tz: Optional[ZoneInfo] = None) -> date:
    """
    Convert Unix epoch seconds to a date in the configured timezone.

    Args:
        epoch_seconds: Unix timestamp
        tz: Optional timezone override

    Returns:
        Date extracted from timestamp
    """
    tz = tz or get_timezone()
    return datetime.fromtimestamp(epoch_seconds, tz=tz).date()


def period_bounds(
    period_type: TimePeriod,
    ref_date: Optional[date] = None,
) -> tuple[date, date]:
    """
    Get start and end date for a period containing ref_date.

    Returns end-exclusive bounds: start <= posted_at < end

    Args:
        period_type: MONTH, QUARTER, or YEAR
        ref_date: Reference date (defaults to today)

    Returns:
        (start_date, end_date_exclusive)

    Examples:
        >>> period_bounds(TimePeriod.MONTH, date(2026, 1, 15))
        (date(2026, 1, 1), date(2026, 2, 1))

        >>> period_bounds(TimePeriod.QUARTER, date(2026, 5, 15))
        (date(2026, 4, 1), date(2026, 7, 1))

        >>> period_bounds(TimePeriod.YEAR, date(2026, 6, 15))
        (date(2026, 1, 1), date(2027, 1, 1))
    """
    ref = ref_date or today()

    if period_type == TimePeriod.MONTH:
        start = date(ref.year, ref.month, 1)
        if ref.month == 12:
            end = date(ref.year + 1, 1, 1)
        else:
            end = date(ref.year, ref.month + 1, 1)

    elif period_type == TimePeriod.QUARTER:
        quarter = (ref.month - 1) // 3
        start_month = quarter * 3 + 1
        start = date(ref.year, start_month, 1)
        end_month = start_month + 3
        if end_month > 12:
            end = date(ref.year + 1, end_month - 12, 1)
        else:
            end = date(ref.year, end_month, 1)

    else:  # YEAR
        start = date(ref.year, 1, 1)
        end = date(ref.year + 1, 1, 1)

    return start, end


def this_month() -> tuple[date, date]:
    """
    Get bounds for the current month.

    Returns:
        (start_date, end_date_exclusive)
    """
    return period_bounds(TimePeriod.MONTH)


def last_month() -> tuple[date, date]:
    """
    Get bounds for the previous month.

    Returns:
        (start_date, end_date_exclusive)
    """
    t = today()
    if t.month == 1:
        ref = date(t.year - 1, 12, 15)
    else:
        ref = date(t.year, t.month - 1, 15)
    return period_bounds(TimePeriod.MONTH, ref)


def this_quarter() -> tuple[date, date]:
    """
    Get bounds for the current quarter.

    Returns:
        (start_date, end_date_exclusive)
    """
    return period_bounds(TimePeriod.QUARTER)


def this_year() -> tuple[date, date]:
    """
    Get bounds for the current year.

    Returns:
        (start_date, end_date_exclusive)
    """
    return period_bounds(TimePeriod.YEAR)


def prev_period_start(period_type: TimePeriod, current_start: date) -> date:
    """
    Get the start date of the previous period.

    Args:
        period_type: MONTH, QUARTER, or YEAR
        current_start: Start date of current period

    Returns:
        Start date of previous period
    """
    if period_type == TimePeriod.MONTH:
        if current_start.month == 1:
            return date(current_start.year - 1, 12, 1)
        else:
            return date(current_start.year, current_start.month - 1, 1)

    elif period_type == TimePeriod.QUARTER:
        quarter_start_month = current_start.month
        if quarter_start_month <= 3:  # Q1 -> Q4 of prev year
            return date(current_start.year - 1, 10, 1)
        else:
            return date(current_start.year, quarter_start_month - 3, 1)

    else:  # YEAR
        return date(current_start.year - 1, 1, 1)


def period_label(period_type: TimePeriod, start: date) -> str:
    """
    Generate human-readable period label.

    Args:
        period_type: MONTH, QUARTER, or YEAR
        start: Start date of period

    Returns:
        Label like "Jan 2026", "Q1 2026", or "2026"
    """
    if period_type == TimePeriod.MONTH:
        return start.strftime("%b %Y")  # "Jan 2026"
    elif period_type == TimePeriod.QUARTER:
        quarter = (start.month - 1) // 3 + 1
        return f"Q{quarter} {start.year}"
    else:  # YEAR
        return str(start.year)


def custom_bounds(
    start_date: date,
    end_date_inclusive: date,
) -> tuple[date, date]:
    """
    Convert user-provided inclusive date range to end-exclusive.

    Users typically enter inclusive ranges (e.g., "Jan 1 to Jan 31").
    This converts to internal format (Jan 1 to Feb 1 exclusive).

    Args:
        start_date: First day to include
        end_date_inclusive: Last day to include

    Returns:
        (start_date, end_date_exclusive)
    """
    # Add one day to make exclusive
    end_exclusive = end_date_inclusive + timedelta(days=1)
    return start_date, end_exclusive


def date_range_days(start: date, end_exclusive: date) -> int:
    """
    Count days in a date range.

    Args:
        start: Start date
        end_exclusive: End date (exclusive)

    Returns:
        Number of days
    """
    return (end_exclusive - start).days


def iter_periods(
    period_type: TimePeriod,
    num_periods: int,
    anchor: Optional[date] = None,
) -> list[tuple[date, date, str]]:
    """
    Iterate backwards through periods.

    Args:
        period_type: MONTH, QUARTER, or YEAR
        num_periods: How many periods to generate
        anchor: Starting point (defaults to today)

    Returns:
        List of (start, end_exclusive, label) tuples, most recent first
    """
    anchor = anchor or today()
    current_start, current_end = period_bounds(period_type, anchor)

    periods = []
    for _ in range(num_periods):
        label = period_label(period_type, current_start)
        periods.append((current_start, current_end, label))

        # Move to previous period
        prev_start = prev_period_start(period_type, current_start)
        current_end = current_start
        current_start = prev_start

    return periods


def parse_iso_date(date_str: str) -> date:
    """
    Parse ISO format date string.

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        Parsed date

    Raises:
        ValueError: If format is invalid
    """
    return date.fromisoformat(date_str)


def format_iso_date(d: date) -> str:
    """
    Format date as ISO string.

    Args:
        d: Date to format

    Returns:
        Date in YYYY-MM-DD format
    """
    return d.isoformat()


def is_in_range(
    check_date: date,
    start: date,
    end_exclusive: date,
) -> bool:
    """
    Check if a date falls within an end-exclusive range.

    Args:
        check_date: Date to check
        start: Range start
        end_exclusive: Range end (exclusive)

    Returns:
        True if start <= check_date < end_exclusive
    """
    return start <= check_date < end_exclusive


def days_until_end_of_month(ref: Optional[date] = None) -> int:
    """
    Days remaining in the current month.

    Args:
        ref: Reference date (defaults to today)

    Returns:
        Number of days left including today
    """
    ref = ref or today()
    _, month_end = period_bounds(TimePeriod.MONTH, ref)
    return (month_end - ref).days
