# view_models.py
"""
View models for adapting Report data to template expectations.

This module bridges the canonical Report model to what templates expect,
allowing gradual migration without breaking existing templates.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .reporting_models import Report, TransactionType


@dataclass
class PeriodViewModel:
    """
    View model adapting Report to template-expected fields.

    Maps the canonical Report structure to the legacy field names
    that templates expect (income_cents, recurring_cents, etc.)
    """
    # Core period info
    period_label: str
    start_date: date
    end_date: date

    # Financial totals (template-expected names)
    income_cents: int
    credit_cents: int  # Refunds + credits_other
    recurring_cents: int  # Fixed obligations (subscriptions/bills)
    discretionary_cents: int  # Variable + discretionary + one-offs
    transfer_cents: int
    incoming_transfer_cents: int
    net_cents: int

    # Rolling averages (for trend comparisons)
    # Default to current values; can be computed from period history
    avg_income_cents: int = 0
    avg_recurring_cents: int = 0
    avg_discretionary_cents: int = 0

    # Trend indicators
    income_trend: str = "stable"
    recurring_trend: str = "stable"
    discretionary_trend: str = "stable"

    # Previous period values (for comparison)
    prev_income_cents: Optional[int] = None
    prev_recurring_cents: Optional[int] = None
    prev_discretionary_cents: Optional[int] = None

    # Drill-down items (aggregated by merchant)
    income_items: list[tuple[str, int]] = field(default_factory=list)
    credit_items: list[tuple[str, int]] = field(default_factory=list)
    transfer_items: list[tuple[str, int]] = field(default_factory=list)

    # Transaction count
    transaction_count: int = 0

    # Integrity info
    raw_sum_cents: int = 0
    classification_sum_cents: int = 0
    checksum_valid: bool = True

    # Report metadata
    report_hash: Optional[str] = None
    snapshot_id: Optional[str] = None

    @classmethod
    def from_report(cls, report: Report) -> "PeriodViewModel":
        """
        Convert a canonical Report to a PeriodViewModel.

        This is the single point of translation from the truth engine
        to template-expected format.
        """
        totals = report.totals

        # Map canonical buckets to legacy field names:
        # - recurring_cents = fixed_obligations (predictable subscriptions)
        # - discretionary_cents = variable_essentials + discretionary + one_offs
        recurring = totals.fixed_obligations_cents
        discretionary = (
            totals.variable_essentials_cents +
            totals.discretionary_cents +
            totals.one_offs_cents
        )

        # Credits = refunds + unclassified credits
        credits = totals.refunds_cents + totals.credits_other_cents

        # Transfers
        transfer_total = totals.transfers_in_cents + totals.transfers_out_cents

        # Net = income + refunds - total expenses
        # (credits_other excluded from net since it's unclassified)
        net = totals.income_cents + totals.refunds_cents - (
            totals.fixed_obligations_cents +
            totals.variable_essentials_cents +
            totals.discretionary_cents +
            totals.one_offs_cents
        )

        # Aggregate transactions for drill-down
        income_items = _aggregate_by_merchant(report, TransactionType.INCOME)
        credit_items = _aggregate_by_merchant(report, TransactionType.REFUND)
        credit_items.extend(_aggregate_by_merchant(report, TransactionType.CREDIT_OTHER))
        transfer_items = _aggregate_by_merchant(report, TransactionType.TRANSFER)

        return cls(
            period_label=report.period_label,
            start_date=report.start_date,
            end_date=report.end_date,
            income_cents=totals.income_cents,
            credit_cents=credits,
            recurring_cents=recurring,
            discretionary_cents=discretionary,
            transfer_cents=transfer_total,
            incoming_transfer_cents=totals.transfers_in_cents,
            net_cents=net,
            # Default averages to current values (can be overridden with historical data)
            avg_income_cents=totals.income_cents,
            avg_recurring_cents=recurring,
            avg_discretionary_cents=discretionary,
            income_items=income_items,
            credit_items=credit_items,
            transfer_items=transfer_items,
            transaction_count=report.transaction_count,
            raw_sum_cents=0,  # Computed in report engine
            classification_sum_cents=0,
            checksum_valid=True,
            report_hash=report.report_hash,
            snapshot_id=report.snapshot_id,
        )

    def to_json_dict(self) -> dict:
        """Convert to dict for JSON serialization (e.g., for Chart.js)."""
        return {
            "period_label": self.period_label,
            "income_cents": self.income_cents,
            "credit_cents": self.credit_cents,
            "recurring_cents": self.recurring_cents,
            "discretionary_cents": self.discretionary_cents,
            "net_cents": self.net_cents,
        }


def _aggregate_by_merchant(report: Report, txn_type: TransactionType) -> list[tuple[str, int]]:
    """Aggregate transactions by merchant for drill-down."""
    by_merchant: dict[str, int] = {}
    for txn in report.transactions:
        if txn.txn_type == txn_type:
            merchant = txn.merchant_norm
            by_merchant[merchant] = by_merchant.get(merchant, 0) + txn.amount_cents

    # Sort by amount descending (absolute value for expenses)
    items = list(by_merchant.items())
    items.sort(key=lambda x: -abs(x[1]))
    return items


def reports_to_view_models(reports: list[Report]) -> list[PeriodViewModel]:
    """Convert a list of Reports to view models."""
    return [PeriodViewModel.from_report(r) for r in reports]


def reports_to_json(reports: list[Report]) -> list[dict]:
    """Convert Reports to JSON-serializable dicts for charts."""
    return [PeriodViewModel.from_report(r).to_json_dict() for r in reports]


def compute_period_trends(reports: list[Report], avg_window: int = 3) -> list[PeriodViewModel]:
    """
    Convert Reports to PeriodViewModels with computed trends and rolling averages.

    This is the canonical way to get trend data for CLI/exports.

    Args:
        reports: List of Reports (most recent first)
        avg_window: Number of periods for rolling average

    Returns:
        List of PeriodViewModels with populated avg_* and *_trend fields
    """
    view_models = []

    for i, report in enumerate(reports):
        vm = PeriodViewModel.from_report(report)

        # Compute rolling averages from subsequent periods (which are older)
        avg_periods = reports[i:i + avg_window]
        if avg_periods:
            vm.avg_income_cents = sum(
                PeriodViewModel.from_report(r).income_cents for r in avg_periods
            ) // len(avg_periods)
            vm.avg_recurring_cents = sum(
                PeriodViewModel.from_report(r).recurring_cents for r in avg_periods
            ) // len(avg_periods)
            vm.avg_discretionary_cents = sum(
                PeriodViewModel.from_report(r).discretionary_cents for r in avg_periods
            ) // len(avg_periods)

        # Compute trends vs previous period
        if i + 1 < len(reports):
            prev = PeriodViewModel.from_report(reports[i + 1])
            vm.prev_income_cents = prev.income_cents
            vm.prev_recurring_cents = prev.recurring_cents
            vm.prev_discretionary_cents = prev.discretionary_cents

            vm.income_trend = _compute_trend(vm.income_cents, prev.income_cents)
            vm.recurring_trend = _compute_trend(vm.recurring_cents, prev.recurring_cents)
            vm.discretionary_trend = _compute_trend(vm.discretionary_cents, prev.discretionary_cents)

        view_models.append(vm)

    return view_models


def _compute_trend(current: int, previous: int, threshold: float = 0.05) -> str:
    """Compute trend direction: up, down, or stable."""
    if previous == 0:
        return "stable" if current == 0 else "up"
    pct_change = (current - previous) / abs(previous)
    if pct_change > threshold:
        return "up"
    elif pct_change < -threshold:
        return "down"
    return "stable"


@dataclass
class CLISummary:
    """
    CLI-focused summary that replaces legacy MonthSummary.

    Provides the structured data needed for CLI status/drill commands.
    """
    year: int
    month: int

    # Core totals
    income_cents: int
    recurring_cents: int  # FIXED_OBLIGATIONS
    one_off_cents: int    # DISCRETIONARY + ONE_OFFS + VARIABLE_ESSENTIALS
    transfer_cents: int

    # Derived
    baseline_cents: int   # income - recurring
    net_cents: int        # income - recurring - one_offs

    # Drill-down lists: (merchant_name, total_cents, metadata)
    # metadata is cadence for recurring, count for one-offs
    recurring_expenses: list[tuple[str, int, str]] = field(default_factory=list)
    one_off_expenses: list[tuple[str, int, int]] = field(default_factory=list)
    income_sources: list[tuple[str, int]] = field(default_factory=list)
    transfers: list[tuple[str, int]] = field(default_factory=list)

    @property
    def is_sustainable(self) -> bool:
        """Whether baseline (income - recurring) is positive."""
        return self.baseline_cents >= 0

    @classmethod
    def from_report(cls, report: Report, year: int, month: int) -> "CLISummary":
        """
        Build CLI summary from a Report.

        This is the canonical way to get summary data for CLI output.
        """
        from collections import defaultdict
        from .reporting_models import SpendingBucket

        totals = report.totals

        # Core totals from Report.totals (already computed correctly)
        income_cents = totals.income_cents
        recurring_cents = totals.fixed_obligations_cents
        one_off_cents = (
            totals.variable_essentials_cents +
            totals.discretionary_cents +
            totals.one_offs_cents
        )
        transfer_cents = totals.transfers_in_cents + totals.transfers_out_cents

        # Derived
        baseline_cents = income_cents - recurring_cents
        net_cents = income_cents - recurring_cents - one_off_cents

        # Aggregate by merchant for drill-down
        recurring_by_merchant: dict[str, int] = defaultdict(int)
        oneoff_by_merchant: dict[str, list[int]] = defaultdict(list)  # amounts for count
        income_by_merchant: dict[str, int] = defaultdict(int)
        transfer_by_merchant: dict[str, int] = defaultdict(int)

        for txn in report.transactions:
            merchant = txn.merchant_norm or "Unknown"

            if txn.txn_type == TransactionType.INCOME:
                income_by_merchant[merchant] += txn.amount_cents
            elif txn.txn_type == TransactionType.TRANSFER:
                transfer_by_merchant[merchant] += abs(txn.amount_cents)
            elif txn.txn_type == TransactionType.EXPENSE:
                if txn.spending_bucket == SpendingBucket.FIXED_OBLIGATIONS:
                    recurring_by_merchant[merchant] += abs(txn.amount_cents)
                else:
                    oneoff_by_merchant[merchant].append(abs(txn.amount_cents))

        # Build sorted lists
        recurring_expenses = [
            (merchant, cents, "recurring")  # Cadence is "recurring" for fixed obligations
            for merchant, cents in sorted(
                recurring_by_merchant.items(),
                key=lambda x: -x[1]
            )
        ]

        one_off_expenses = [
            (merchant, sum(amounts), len(amounts))
            for merchant, amounts in sorted(
                oneoff_by_merchant.items(),
                key=lambda x: -sum(x[1])
            )
        ]

        income_sources = sorted(
            income_by_merchant.items(),
            key=lambda x: -x[1]
        )

        transfers = sorted(
            transfer_by_merchant.items(),
            key=lambda x: -x[1]
        )

        return cls(
            year=year,
            month=month,
            income_cents=income_cents,
            recurring_cents=recurring_cents,
            one_off_cents=one_off_cents,
            transfer_cents=transfer_cents,
            baseline_cents=baseline_cents,
            net_cents=net_cents,
            recurring_expenses=recurring_expenses,
            one_off_expenses=one_off_expenses,
            income_sources=income_sources,
            transfers=transfers,
        )


def category_breakdown_from_report(report: Report) -> list[tuple[str, int, int, int]]:
    """
    Compute category breakdown from a Report's transactions.

    Returns: list of (category_id, gross_cents, refund_cents, net_cents)
    Sorted by net_cents descending (absolute value).

    This is THE canonical way to get category breakdowns - no separate DB query.
    """
    from collections import defaultdict

    # Aggregate by category
    category_totals: dict[str, dict[str, int]] = defaultdict(
        lambda: {"gross": 0, "refunds": 0}
    )

    for txn in report.transactions:
        cat = txn.category_id or "other"

        if txn.txn_type == TransactionType.EXPENSE:
            category_totals[cat]["gross"] += abs(txn.amount_cents)
        elif txn.txn_type == TransactionType.REFUND:
            category_totals[cat]["refunds"] += txn.amount_cents

    # Build result list
    result = []
    for cat_id, totals in category_totals.items():
        gross = totals["gross"]
        refunds = totals["refunds"]
        net = gross - refunds
        result.append((cat_id, gross, refunds, net))

    # Sort by net descending
    result.sort(key=lambda x: -x[3])
    return result
