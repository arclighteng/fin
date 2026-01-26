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
