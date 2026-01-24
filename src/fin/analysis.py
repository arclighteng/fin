# analysis.py
"""
Flexible time period analysis engine for income vs spend.

Supports:
- Monthly view: Last N months
- Quarterly view: Last N quarters
- Yearly view: Last N years
- Configurable rolling average window
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Callable

from .classify import (
    _detect_patterns,
    _is_transfer,
    _is_credit_card_account,
    _is_income_transfer,
    _is_cc_payment_expense,
    classify_transaction,
)


class TimePeriod(Enum):
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


@dataclass
class PeriodAnalysis:
    """Analysis for a single time period."""
    period_type: TimePeriod | None
    period_label: str          # "Jan 2026", "Q1 2026", "2025", or custom range
    start_date: date
    end_date: date

    income_cents: int
    credit_cents: int          # Refunds, rewards, adjustments (not income)
    recurring_cents: int
    discretionary_cents: int
    transfer_cents: int
    incoming_transfer_cents: int  # Transfers IN (e.g., from savings)
    net_cents: int             # income + credits - recurring - discretionary

    # Comparison to previous period
    prev_income_cents: int | None
    prev_recurring_cents: int | None
    prev_discretionary_cents: int | None
    income_trend: str          # "up", "down", "stable"
    recurring_trend: str
    discretionary_trend: str

    # Rolling averages (configurable window)
    avg_income_cents: int      # Average over N periods
    avg_recurring_cents: int
    avg_discretionary_cents: int

    # Counts
    transaction_count: int

    # Breakdown items for drill-down
    income_items: list[tuple[str, int]] | None = None  # (merchant, amount_cents)
    credit_items: list[tuple[str, int]] | None = None  # (merchant, amount_cents) - refunds etc.
    transfer_items: list[tuple[str, int]] | None = None

    # Checksum verification
    raw_sum_cents: int = 0           # Sum of all raw transaction amounts
    classification_sum_cents: int = 0  # Computed: income + credits + transfers_in - recurring - discretionary - transfers_out
    checksum_valid: bool = True      # Whether raw_sum == classification_sum

    # === Financial Health Metrics ===
    # Based on widely-used personal finance frameworks:
    # - 50/30/20 rule (Senator Elizabeth Warren, "All Your Worth")
    # - CFP Board guidelines for emergency funds and expense ratios
    #
    # IMPORTANT: These are general guidelines, not personalized advice.
    # Individual circumstances vary significantly.

    @property
    def savings_rate_pct(self) -> float:
        """
        Net savings as percentage of income.

        Calculation: (Income - All Expenses) / Income × 100

        Common benchmarks (not prescriptive):
        - 20%+ often cited as healthy target (50/30/20 rule)
        - 10-20% considered adequate by many planners
        - <10% may limit long-term financial security
        - Negative means spending exceeds income
        """
        if self.income_cents <= 0:
            return 0.0
        return (self.net_cents / self.income_cents) * 100

    @property
    def fixed_expense_ratio_pct(self) -> float:
        """
        Recurring/fixed expenses as percentage of income.

        Includes: subscriptions, bills, regular payments detected as recurring.
        Does NOT include: rent/mortgage unless detected as recurring charge.

        Common benchmarks:
        - 50% is the "needs" allocation in 50/30/20 framework
        - Lower ratios provide more flexibility
        """
        if self.income_cents <= 0:
            return 0.0
        return (self.recurring_cents / self.income_cents) * 100

    @property
    def discretionary_ratio_pct(self) -> float:
        """
        Non-recurring spending as percentage of income.

        Includes: one-time purchases, variable expenses, dining, shopping, etc.

        Common benchmarks:
        - 30% is the "wants" allocation in 50/30/20 framework
        """
        if self.income_cents <= 0:
            return 0.0
        return (self.discretionary_cents / self.income_cents) * 100

    @property
    def total_expenses_cents(self) -> int:
        """Total expenses (recurring + discretionary)."""
        return self.recurring_cents + self.discretionary_cents

    @property
    def expense_coverage_months(self) -> float | None:
        """
        If current net savings continued, how many months of expenses covered?
        Returns None if not calculable (no expenses or negative savings).
        """
        if self.total_expenses_cents <= 0 or self.net_cents <= 0:
            return None
        # This is a simplified view - actual calculation would need total savings
        return None  # We don't have total balance data

    @property
    def financial_health_score(self) -> str:
        """
        Summary indicator based on this period's cash flow.

        NOT a comprehensive financial health assessment - only reflects
        income vs. spending for this specific time period.

        'positive': Income exceeds expenses
        'breakeven': Roughly balanced (within 5%)
        'negative': Expenses exceed income
        """
        if self.income_cents <= 0:
            return "no_income"

        ratio = self.net_cents / self.income_cents

        if ratio >= 0.15:  # Saving 15%+ of income
            return "positive"
        elif ratio >= -0.05:  # Within 5% of breakeven
            return "breakeven"
        else:
            return "negative"

    @property
    def health_insights(self) -> list[tuple[str, str, str]]:
        """
        Factual observations about this period's finances.
        Returns: list of (icon, message, severity)

        These are observations, not advice. Severity indicates
        whether the observation is typically favorable or unfavorable.
        """
        insights = []

        # Cash flow observation
        if self.income_cents > 0:
            savings = self.savings_rate_pct
            if self.net_cents > 0:
                insights.append((
                    "↑",
                    f"Net positive: ${self.net_cents/100:,.0f} ({savings:.0f}% of income)",
                    "good"
                ))
            elif self.net_cents < 0:
                insights.append((
                    "↓",
                    f"Net negative: ${abs(self.net_cents)/100:,.0f} over income",
                    "critical"
                ))

        # Income vs. average observation (factual comparison)
        if self.avg_income_cents > 0 and self.income_cents > 0:
            diff = self.income_cents - self.avg_income_cents
            diff_pct = (diff / self.avg_income_cents) * 100
            if abs(diff_pct) > 20:
                if diff > 0:
                    insights.append((
                        "↑",
                        f"Income ${diff/100:,.0f} ({diff_pct:+.0f}%) vs. recent average",
                        "info"
                    ))
                else:
                    insights.append((
                        "↓",
                        f"Income ${abs(diff)/100:,.0f} ({diff_pct:.0f}%) vs. recent average",
                        "warning"
                    ))

        # Recurring expense observation
        if self.income_cents > 0:
            fixed_pct = self.fixed_expense_ratio_pct
            if fixed_pct > 0:
                severity = "info"
                if fixed_pct > 60:
                    severity = "warning"
                insights.append((
                    "→",
                    f"Recurring expenses: {fixed_pct:.0f}% of income (${self.recurring_cents/100:,.0f})",
                    severity
                ))

        return insights


def _get_period_bounds(period_type: TimePeriod, ref_date: date) -> tuple[date, date]:
    """Get start and end date for a period containing ref_date."""
    if period_type == TimePeriod.MONTH:
        start = date(ref_date.year, ref_date.month, 1)
        if ref_date.month == 12:
            end = date(ref_date.year + 1, 1, 1)
        else:
            end = date(ref_date.year, ref_date.month + 1, 1)
    elif period_type == TimePeriod.QUARTER:
        quarter = (ref_date.month - 1) // 3
        start_month = quarter * 3 + 1
        start = date(ref_date.year, start_month, 1)
        end_month = start_month + 3
        if end_month > 12:
            end = date(ref_date.year + 1, end_month - 12, 1)
        else:
            end = date(ref_date.year, end_month, 1)
    else:  # YEAR
        start = date(ref_date.year, 1, 1)
        end = date(ref_date.year + 1, 1, 1)
    return start, end


def _get_period_label(period_type: TimePeriod, start: date) -> str:
    """Generate human-readable period label."""
    if period_type == TimePeriod.MONTH:
        return start.strftime("%b %Y")  # "Jan 2026"
    elif period_type == TimePeriod.QUARTER:
        quarter = (start.month - 1) // 3 + 1
        return f"Q{quarter} {start.year}"
    else:
        return str(start.year)


def _prev_period_start(period_type: TimePeriod, current_start: date) -> date:
    """Get the start date of the previous period."""
    if period_type == TimePeriod.MONTH:
        if current_start.month == 1:
            return date(current_start.year - 1, 12, 1)
        else:
            return date(current_start.year, current_start.month - 1, 1)
    elif period_type == TimePeriod.QUARTER:
        if current_start.month <= 3:
            return date(current_start.year - 1, 10, 1)
        else:
            return date(current_start.year, current_start.month - 3, 1)
    else:  # YEAR
        return date(current_start.year - 1, 1, 1)


def _compute_trend(current: int, previous: int | None, threshold_pct: float = 5.0) -> str:
    """Compute trend indicator."""
    if previous is None or previous == 0:
        return "stable"
    change_pct = ((current - previous) / abs(previous)) * 100
    if change_pct > threshold_pct:
        return "up"
    elif change_pct < -threshold_pct:
        return "down"
    return "stable"


def _analyze_single_period(
    conn: sqlite3.Connection,
    period_type: TimePeriod,
    start: date,
    end: date,
    patterns: dict,
    income_sources: set[str] | None = None,
    excluded_sources: set[str] | None = None,
    account_filter: list[str] | None = None,
) -> dict:
    """
    Analyze a single period and return raw values.

    Uses the shared classify_transaction() function for consistent classification.
    """
    income_sources = income_sources or set()
    excluded_sources = excluded_sources or set()

    # Get account info to determine account types
    account_types: dict[str, bool] = {}  # account_id -> is_credit_card
    for acc in conn.execute("SELECT account_id, name FROM accounts").fetchall():
        account_types[acc["account_id"]] = _is_credit_card_account(acc["name"])

    # Build query with optional account filter (end-exclusive for period bounds)
    query = """
        SELECT
            t.account_id,
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
    """
    params: list = [start.isoformat(), end.isoformat()]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    query += " ORDER BY t.posted_at"
    rows = conn.execute(query, params).fetchall()

    income_cents = 0
    credit_cents = 0  # Refunds, rewards, adjustments
    recurring_cents = 0
    discretionary_cents = 0
    transfer_cents = 0

    # For data integrity checks - track transfers by direction
    incoming_transfer_cents = 0  # Positive amounts classified as transfers
    outgoing_transfer_cents = 0  # Negative amounts classified as transfers

    # Track components for drill-down
    income_items: list[tuple[str, int]] = []
    credit_items: list[tuple[str, int]] = []
    transfer_items: list[tuple[str, int]] = []

    for r in rows:
        amount = r["amount_cents"]
        merchant_norm = r["merchant_norm"]
        account_id = r["account_id"]
        is_cc = account_types.get(account_id, False)
        pattern = patterns.get(merchant_norm)

        # Use shared classification function
        classification = classify_transaction(
            amount_cents=amount,
            merchant_norm=merchant_norm,
            is_credit_card=is_cc,
            pattern=pattern,
            income_sources=income_sources,
            excluded_sources=excluded_sources,
        )

        if classification == "income":
            income_cents += amount
            income_items.append((merchant_norm, amount))

        elif classification == "credit":
            credit_cents += amount
            credit_items.append((merchant_norm, amount))

        elif classification == "transfer":
            if amount > 0:
                incoming_transfer_cents += amount
                transfer_items.append((merchant_norm, amount))
            else:
                outgoing_transfer_cents += abs(amount)
            transfer_cents += abs(amount)

        elif classification == "recurring":
            recurring_cents += abs(amount)

        else:  # one-off / discretionary
            discretionary_cents += abs(amount)

    # Net includes credits as they reduce effective spend
    net_cents = income_cents + credit_cents - recurring_cents - discretionary_cents

    # === DATA INTEGRITY ASSERTION ===
    # Every transaction must be classified into exactly one bucket.
    # Formula: positive_sum - negative_sum = income + credits + incoming_transfers - recurring - discretionary - outgoing_transfers
    total_from_transactions = sum(r["amount_cents"] for r in rows)
    total_from_classification = (
        income_cents + credit_cents + incoming_transfer_cents
        - recurring_cents - discretionary_cents - outgoing_transfer_cents
    )

    if total_from_transactions != total_from_classification:
        import logging
        log = logging.getLogger("fin.analysis")
        log.error(
            f"DATA INTEGRITY ERROR in _analyze_single_period: "
            f"Txn sum ({total_from_transactions}) != Classification sum ({total_from_classification}). "
            f"Income={income_cents}, Credit={credit_cents}, InTransfer={incoming_transfer_cents}, "
            f"OutTransfer={outgoing_transfer_cents}, Recurring={recurring_cents}, Discretionary={discretionary_cents}"
        )

    return {
        "income_cents": income_cents,
        "credit_cents": credit_cents,
        "recurring_cents": recurring_cents,
        "discretionary_cents": discretionary_cents,
        "transfer_cents": transfer_cents,
        "incoming_transfer_cents": incoming_transfer_cents,
        "net_cents": net_cents,
        "transaction_count": len(rows),
        "income_items": income_items,
        "credit_items": credit_items,
        "transfer_items": transfer_items,
        "raw_sum_cents": total_from_transactions,
        "classification_sum_cents": total_from_classification,
        "checksum_valid": total_from_transactions == total_from_classification,
    }


def analyze_periods(
    conn: sqlite3.Connection,
    period_type: TimePeriod,
    num_periods: int = 6,
    avg_window: int = 3,
    end_date: date | None = None,
    account_filter: list[str] | None = None,
) -> list[PeriodAnalysis]:
    """
    Analyze income vs spend over multiple periods.

    Args:
        conn: Database connection
        period_type: Month, quarter, or year
        num_periods: How many periods to analyze
        avg_window: Rolling average window size
        end_date: Reference date (defaults to today)
        account_filter: Optional list of account_ids to filter by

    Returns:
        List of PeriodAnalysis, most recent first
    """
    from . import db as dbmod

    if end_date is None:
        end_date = date.today()

    # Detect patterns anchored to the reference end_date
    # This ensures historical reports use patterns that existed at that time
    patterns = _detect_patterns(conn, lookback_days=800, anchor_date=end_date)

    # Get user-marked income rules
    income_sources, excluded_sources = dbmod.get_income_rules(conn)

    # Collect period data
    period_data: list[tuple[date, date, str, dict]] = []
    current_start, current_end = _get_period_bounds(period_type, end_date)

    # Go back num_periods + avg_window to have enough data for rolling averages
    periods_needed = num_periods + avg_window

    for _ in range(periods_needed):
        label = _get_period_label(period_type, current_start)
        data = _analyze_single_period(
            conn, period_type, current_start, current_end, patterns,
            income_sources, excluded_sources, account_filter
        )
        period_data.append((current_start, current_end, label, data))

        # Move to previous period
        prev_start = _prev_period_start(period_type, current_start)
        current_end = current_start
        current_start = prev_start

    # Reverse to chronological order for rolling average calculation
    period_data.reverse()

    # Build PeriodAnalysis objects with rolling averages and trends
    results: list[PeriodAnalysis] = []

    for i, (start, end, label, data) in enumerate(period_data):
        # Skip periods only used for rolling average calculation
        if i < avg_window:
            continue

        # Calculate rolling averages
        window_data = period_data[max(0, i - avg_window + 1):i + 1]
        avg_income = sum(d["income_cents"] for _, _, _, d in window_data) // len(window_data)
        avg_recurring = sum(d["recurring_cents"] for _, _, _, d in window_data) // len(window_data)
        avg_discretionary = sum(d["discretionary_cents"] for _, _, _, d in window_data) // len(window_data)

        # Get previous period data
        prev_data = period_data[i - 1][3] if i > 0 else None
        prev_income = prev_data["income_cents"] if prev_data else None
        prev_recurring = prev_data["recurring_cents"] if prev_data else None
        prev_discretionary = prev_data["discretionary_cents"] if prev_data else None

        # Compute trends
        income_trend = _compute_trend(data["income_cents"], prev_income)
        recurring_trend = _compute_trend(data["recurring_cents"], prev_recurring)
        discretionary_trend = _compute_trend(data["discretionary_cents"], prev_discretionary)

        results.append(PeriodAnalysis(
            period_type=period_type,
            period_label=label,
            start_date=start,
            end_date=end,
            income_cents=data["income_cents"],
            credit_cents=data["credit_cents"],
            recurring_cents=data["recurring_cents"],
            discretionary_cents=data["discretionary_cents"],
            transfer_cents=data["transfer_cents"],
            incoming_transfer_cents=data["incoming_transfer_cents"],
            net_cents=data["net_cents"],
            prev_income_cents=prev_income,
            prev_recurring_cents=prev_recurring,
            prev_discretionary_cents=prev_discretionary,
            income_trend=income_trend,
            recurring_trend=recurring_trend,
            discretionary_trend=discretionary_trend,
            avg_income_cents=avg_income,
            avg_recurring_cents=avg_recurring,
            avg_discretionary_cents=avg_discretionary,
            transaction_count=data["transaction_count"],
            income_items=data.get("income_items"),
            credit_items=data.get("credit_items"),
            transfer_items=data.get("transfer_items"),
            raw_sum_cents=data["raw_sum_cents"],
            classification_sum_cents=data["classification_sum_cents"],
            checksum_valid=data["checksum_valid"],
        ))

    # Return most recent first
    results.reverse()
    return results


def get_current_period(
    conn: sqlite3.Connection,
    period_type: TimePeriod,
) -> PeriodAnalysis | None:
    """Get analysis for the current period only."""
    results = analyze_periods(conn, period_type, num_periods=1, avg_window=3)
    return results[0] if results else None


def format_cents_usd(cents: int) -> str:
    """Format cents as USD string."""
    return f"${cents / 100:,.2f}"


def format_trend_symbol(trend: str) -> str:
    """Format trend as symbol."""
    if trend == "up":
        return "\u2191"  # ↑
    elif trend == "down":
        return "\u2193"  # ↓
    return "\u2192"  # →


def analyze_custom_range(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    account_filter: list[str] | None = None,
) -> PeriodAnalysis:
    """
    Analyze income vs spend for a custom date range.

    Uses the shared classify_transaction() function for consistent classification.
    Note: Uses end-INCLUSIVE semantics for user-specified date ranges.

    Args:
        start: Start date (inclusive)
        end: End date (inclusive)
        account_filter: Optional list of account_ids to filter by

    Returns a PeriodAnalysis with income_items for drill-down.
    """
    from . import db as dbmod

    # Get user-marked income rules
    income_sources, excluded_sources = dbmod.get_income_rules(conn)

    # Detect patterns anchored to the END of the custom range
    # This ensures historical reports use patterns that existed at that time
    patterns = _detect_patterns(conn, lookback_days=800, anchor_date=end)

    # Get account info to determine account types
    account_types: dict[str, bool] = {}  # account_id -> is_credit_card
    for acc in conn.execute("SELECT account_id, name FROM accounts").fetchall():
        account_types[acc["account_id"]] = _is_credit_card_account(acc["name"])

    # Build query with optional account filter (end-INCLUSIVE for custom ranges)
    query = """
        SELECT
            t.account_id,
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm,
            a.name AS account_name
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        WHERE t.posted_at >= ? AND t.posted_at <= ?
    """
    params: list = [start.isoformat(), end.isoformat()]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    query += " ORDER BY t.posted_at"
    rows = conn.execute(query, params).fetchall()

    income_cents = 0
    credit_cents = 0  # Refunds, rewards, adjustments
    recurring_cents = 0
    discretionary_cents = 0
    transfer_cents = 0

    # For data integrity checks - track transfers by direction
    incoming_transfer_cents = 0  # Positive amounts classified as transfers
    outgoing_transfer_cents = 0  # Negative amounts classified as transfers

    # Track components for drill-down
    income_items: list[tuple[str, int]] = []
    credit_items: list[tuple[str, int]] = []
    transfer_items: list[tuple[str, int]] = []

    for r in rows:
        amount = r["amount_cents"]
        merchant_norm = r["merchant_norm"]
        account_id = r["account_id"]
        is_cc = account_types.get(account_id, False)
        pattern = patterns.get(merchant_norm)

        # Use shared classification function
        classification = classify_transaction(
            amount_cents=amount,
            merchant_norm=merchant_norm,
            is_credit_card=is_cc,
            pattern=pattern,
            income_sources=income_sources,
            excluded_sources=excluded_sources,
        )

        if classification == "income":
            income_cents += amount
            income_items.append((merchant_norm, amount))

        elif classification == "credit":
            credit_cents += amount
            credit_items.append((merchant_norm, amount))

        elif classification == "transfer":
            if amount > 0:
                incoming_transfer_cents += amount
                transfer_items.append((merchant_norm, amount))
            else:
                outgoing_transfer_cents += abs(amount)
            transfer_cents += abs(amount)

        elif classification == "recurring":
            recurring_cents += abs(amount)

        else:  # one-off / discretionary
            discretionary_cents += abs(amount)

    # Net includes credits as they reduce effective spend
    net_cents = income_cents + credit_cents - recurring_cents - discretionary_cents

    # === DATA INTEGRITY ASSERTION ===
    total_from_transactions = sum(r["amount_cents"] for r in rows)
    total_from_classification = (
        income_cents + credit_cents + incoming_transfer_cents
        - recurring_cents - discretionary_cents - outgoing_transfer_cents
    )

    if total_from_transactions != total_from_classification:
        import logging
        log = logging.getLogger("fin.analysis")
        log.error(
            f"DATA INTEGRITY ERROR in analyze_custom_range: "
            f"Txn sum ({total_from_transactions}) != Classification sum ({total_from_classification}). "
            f"Income={income_cents}, Credit={credit_cents}, InTransfer={incoming_transfer_cents}, "
            f"OutTransfer={outgoing_transfer_cents}, Recurring={recurring_cents}, Discretionary={discretionary_cents}, "
            f"Period={start} to {end}, Txn count={len(rows)}"
        )

    # Aggregate items by merchant
    def aggregate(items: list[tuple[str, int]]) -> list[tuple[str, int]]:
        by_merchant: dict[str, int] = {}
        for merchant, amount in items:
            by_merchant[merchant] = by_merchant.get(merchant, 0) + amount
        return sorted(by_merchant.items(), key=lambda x: -x[1])

    aggregated_income = aggregate(income_items)
    aggregated_credits = aggregate(credit_items)
    aggregated_transfers = aggregate(transfer_items)

    # Create label
    label = f"{start.strftime('%b %d')} - {end.strftime('%b %d, %Y')}"

    checksum_valid = total_from_transactions == total_from_classification

    return PeriodAnalysis(
        period_type=None,  # Custom range
        period_label=label,
        start_date=start,
        end_date=end,
        income_cents=income_cents,
        credit_cents=credit_cents,
        recurring_cents=recurring_cents,
        discretionary_cents=discretionary_cents,
        transfer_cents=transfer_cents,
        incoming_transfer_cents=incoming_transfer_cents,
        net_cents=net_cents,
        prev_income_cents=None,
        prev_recurring_cents=None,
        prev_discretionary_cents=None,
        income_trend="stable",
        recurring_trend="stable",
        discretionary_trend="stable",
        avg_income_cents=income_cents,
        avg_recurring_cents=recurring_cents,
        avg_discretionary_cents=discretionary_cents,
        transaction_count=len(rows),
        income_items=aggregated_income,
        credit_items=aggregated_credits,
        transfer_items=aggregated_transfers,
        raw_sum_cents=total_from_transactions,
        classification_sum_cents=total_from_classification,
        checksum_valid=checksum_valid,
    )
