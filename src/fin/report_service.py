# report_service.py
"""
Canonical Report Service - THE ONLY source of truth for user-facing numbers.

TRUTH CONTRACT:
- ALL user-facing totals/labels/buckets/alerts MUST come from this service
- Web, CLI, and exports call these functions ONLY
- No parallel recomputation of totals anywhere else

This module provides:
- report_period(): Single period analysis
- report_month(): Convenience for month-based reports
- report_periods(): Multiple periods with proper per-period anchoring
- All reports include report_hash + snapshot_id + versions + integrity flags
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

from .reporting import report_period as _report_period, report_month as _report_month
from .reporting_models import Report, PeriodTotals, IntegrityReport, IntegrityFlag
from .versioning import compute_snapshot_id, SnapshotInfo, CLASSIFIER_VERSION, REPORT_VERSION
from .dates import period_bounds, period_label, TimePeriod
from .cache import get_report_cache, cache_key


@dataclass
class EnhancedReport(Report):
    """Report with additional metadata for reproducibility."""
    snapshot_id: str = ""
    as_of_date: Optional[date] = None


class ReportService:
    """
    The canonical report service.

    Usage:
        service = ReportService(conn)
        report = service.report_period(start, end)
        reports = service.report_periods([...])
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._snapshot: Optional[SnapshotInfo] = None
        self._cache = get_report_cache()

    @property
    def snapshot(self) -> SnapshotInfo:
        """Get or compute the current snapshot info."""
        if self._snapshot is None:
            self._snapshot = compute_snapshot_id(self.conn)
        return self._snapshot

    def report_period(
        self,
        start_date: date,
        end_date: date,
        include_pending: bool = False,
        account_filter: Optional[list[str]] = None,
        as_of: Optional[date] = None,
    ) -> Report:
        """
        Generate a canonical report for a date range.

        This is THE function that produces user-visible totals.

        Args:
            start_date: Start of period (inclusive)
            end_date: End of period (exclusive)
            include_pending: Whether to include pending transactions
            account_filter: Optional list of account IDs to filter
                - None: All accounts
                - []: Empty list returns empty report with flag
                - list: Filter to these accounts only
            as_of: Historical anchor date (caps all lookbacks to prevent future leakage)

        Returns:
            Report with totals, transactions, integrity info, and version metadata
        """
        # Handle empty account filter explicitly
        if account_filter is not None and len(account_filter) == 0:
            return self._empty_report(start_date, end_date)

        # Check cache
        cache_k = cache_key(
            "report_period",
            start_date.isoformat(),
            end_date.isoformat(),
            include_pending,
            tuple(sorted(account_filter)) if account_filter else None,
            as_of.isoformat() if as_of else None,
            self.snapshot.snapshot_id,
        )
        cached = self._cache.get(cache_k)
        if cached is not None:
            return cached

        # Generate report
        report = _report_period(
            self.conn,
            start_date,
            end_date,
            include_pending,
            account_filter,
        )

        # Enhance with snapshot info
        report.snapshot_id = self.snapshot.snapshot_id

        # Cache and return
        self._cache.set(cache_k, report, ttl=60.0)
        return report

    def report_month(
        self,
        year: int,
        month: int,
        include_pending: bool = False,
        account_filter: Optional[list[str]] = None,
        as_of: Optional[date] = None,
    ) -> Report:
        """
        Generate report for a specific month.

        Args:
            year: Year
            month: Month (1-12)
            include_pending: Whether to include pending transactions
            account_filter: Optional account filter
            as_of: Historical anchor date

        Returns:
            Report for the month
        """
        ref = date(year, month, 15)
        start, end = period_bounds(TimePeriod.MONTH, ref)
        report = self.report_period(start, end, include_pending, account_filter, as_of)
        report.period_label = period_label(TimePeriod.MONTH, start)
        return report

    def report_periods(
        self,
        period_type: TimePeriod,
        num_periods: int = 6,
        include_pending: bool = False,
        account_filter: Optional[list[str]] = None,
        end_date: Optional[date] = None,
    ) -> list[Report]:
        """
        Generate reports for multiple periods.

        CRITICAL: Each period is anchored to its own end date to prevent
        future data leakage in historical reports.

        Args:
            period_type: MONTH, QUARTER, or YEAR
            num_periods: Number of periods to generate
            include_pending: Whether to include pending transactions
            account_filter: Optional account filter
            end_date: Reference date (defaults to today)

        Returns:
            List of Reports, most recent first
        """
        if end_date is None:
            end_date = date.today()

        reports: list[Report] = []

        # Generate each period, anchored to its own end date
        current_ref = end_date
        for _ in range(num_periods):
            start, end = period_bounds(period_type, current_ref)

            # Generate report anchored to this period's end
            report = self.report_period(
                start,
                end,
                include_pending,
                account_filter,
                as_of=end,  # Anchor to period end
            )
            report.period_label = period_label(period_type, start)
            reports.append(report)

            # Move to previous period
            current_ref = start - timedelta(days=1)

        return reports

    def report_this_month(
        self,
        include_pending: bool = False,
        account_filter: Optional[list[str]] = None,
    ) -> Report:
        """Generate report for current month."""
        today = date.today()
        return self.report_month(today.year, today.month, include_pending, account_filter)

    def invalidate_cache(self) -> None:
        """Invalidate the report cache (call after sync or data changes)."""
        self._cache.clear()
        self._snapshot = None

    def _empty_report(self, start_date: date, end_date: date) -> Report:
        """Create an empty report for empty account filter."""
        return Report(
            period_label=f"{start_date.isoformat()} to {end_date.isoformat()}",
            start_date=start_date,
            end_date=end_date,
            totals=PeriodTotals(),
            transactions=[],
            integrity=IntegrityReport(
                flags=[IntegrityFlag.EMPTY_ACCOUNT_FILTER],
                unmatched_transfer_count=0,
                unclassified_credit_count=0,
                unclassified_credit_cents=0,
            ),
            classifier_version=CLASSIFIER_VERSION,
            report_version=REPORT_VERSION,
            transaction_count=0,
            pending_count=0,
            report_hash="",
            snapshot_id=self.snapshot.snapshot_id,
        )


# Convenience functions for direct import
def report_period(
    conn: sqlite3.Connection,
    start_date: date,
    end_date: date,
    include_pending: bool = False,
    account_filter: Optional[list[str]] = None,
    as_of: Optional[date] = None,
) -> Report:
    """
    Generate a canonical report for a date range.

    This is a convenience wrapper around ReportService.report_period().
    """
    service = ReportService(conn)
    return service.report_period(start_date, end_date, include_pending, account_filter, as_of)


def report_month(
    conn: sqlite3.Connection,
    year: int,
    month: int,
    include_pending: bool = False,
    account_filter: Optional[list[str]] = None,
    as_of: Optional[date] = None,
) -> Report:
    """
    Generate report for a specific month.

    This is a convenience wrapper around ReportService.report_month().
    """
    service = ReportService(conn)
    return service.report_month(year, month, include_pending, account_filter, as_of)


def report_periods(
    conn: sqlite3.Connection,
    period_type: TimePeriod,
    num_periods: int = 6,
    include_pending: bool = False,
    account_filter: Optional[list[str]] = None,
    end_date: Optional[date] = None,
) -> list[Report]:
    """
    Generate reports for multiple periods.

    This is a convenience wrapper around ReportService.report_periods().
    """
    service = ReportService(conn)
    return service.report_periods(period_type, num_periods, include_pending, account_filter, end_date)
