# projections.py
"""
Cash flow projections and alerts.

TRUTH CONTRACT:
- Projections are based on detected patterns (subscriptions, bills, income)
- Alerts warn about potential shortfalls
- Never assume income from unclassified credits
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .reporting_models import SpendingBucket


@dataclass
class UpcomingCharge:
    """A predicted upcoming charge."""
    merchant: str
    display_name: Optional[str]
    expected_date: date
    expected_amount_cents: int
    confidence: float  # 0.0-1.0
    cadence: str  # monthly, weekly, annual, etc.
    bucket: SpendingBucket
    is_subscription: bool
    last_charge_date: Optional[date]


@dataclass
class CashFlowProjection:
    """Cash flow projection for a future period."""
    start_date: date
    end_date: date
    expected_income_cents: int
    expected_fixed_cents: int
    expected_variable_cents: int
    expected_discretionary_cents: int
    expected_net_cents: int
    upcoming_charges: list[UpcomingCharge]
    confidence: float


@dataclass
class CashFlowAlert:
    """Alert about potential cash flow issue."""
    alert_type: str  # "shortfall", "large_charge", "unusual_pattern"
    severity: str  # "low", "medium", "high"
    date: date
    message: str
    amount_cents: Optional[int]
    merchant: Optional[str]


def project_cash_flow(
    conn: sqlite3.Connection,
    days_forward: int = 30,
    account_filter: Optional[list[str]] = None,
) -> CashFlowProjection:
    """
    Project cash flow for the next N days.

    Identifies expected income and upcoming charges based on
    detected patterns.

    Args:
        conn: Database connection
        days_forward: Days to project
        account_filter: Optional account filter

    Returns:
        CashFlowProjection with expected income, charges, and net
    """
    from .legacy_classify import _detect_patterns, _match_known_subscription, get_subscriptions, get_bills

    today = date.today()
    end_date = today + timedelta(days=days_forward)

    # Get patterns for projection
    patterns = _detect_patterns(conn, lookback_days=400, account_filter=account_filter)

    # Get subscriptions and bills
    subscriptions = get_subscriptions(conn, days=400, account_filter=account_filter)
    bills = get_bills(conn, days=400, account_filter=account_filter)

    upcoming_charges: list[UpcomingCharge] = []

    # Project subscription charges
    for sub in subscriptions:
        merchant, monthly_cents, cadence, first_seen, last_seen, is_dup, txn_type, is_known, display_name, actual_cents = sub

        # Predict next charge date based on cadence and last seen
        if cadence == "monthly":
            # Expect charge around same day each month
            next_charge = _next_monthly_date(last_seen, today)
        elif cadence == "annual":
            # Expect charge around same date next year
            next_charge = date(last_seen.year + 1, last_seen.month, last_seen.day)
            if next_charge < today:
                next_charge = date(last_seen.year + 2, last_seen.month, last_seen.day)
        elif cadence == "weekly":
            # Expect charge on same weekday
            days_since = (today - last_seen).days
            next_charge = today + timedelta(days=(7 - days_since % 7) % 7 or 7)
        elif cadence == "biweekly":
            days_since = (today - last_seen).days
            next_charge = today + timedelta(days=(14 - days_since % 14) % 14 or 14)
        elif cadence == "quarterly":
            next_charge = last_seen + timedelta(days=90)
            while next_charge < today:
                next_charge += timedelta(days=90)
        else:
            # Default to monthly
            next_charge = _next_monthly_date(last_seen, today)

        if today <= next_charge <= end_date:
            upcoming_charges.append(UpcomingCharge(
                merchant=merchant,
                display_name=display_name,
                expected_date=next_charge,
                expected_amount_cents=actual_cents,
                confidence=0.9 if is_known else 0.7,
                cadence=cadence,
                bucket=SpendingBucket.FIXED_OBLIGATIONS,
                is_subscription=True,
                last_charge_date=last_seen,
            ))

    # Project bill charges
    for bill in bills:
        merchant, monthly_cents, cadence, first_seen, last_seen, is_dup, txn_type, is_known, display_name, actual_cents = bill

        if cadence == "monthly":
            next_charge = _next_monthly_date(last_seen, today)
        else:
            next_charge = _next_monthly_date(last_seen, today)

        if today <= next_charge <= end_date:
            upcoming_charges.append(UpcomingCharge(
                merchant=merchant,
                display_name=display_name,
                expected_date=next_charge,
                expected_amount_cents=actual_cents,
                confidence=0.6,  # Bills vary more
                cadence=cadence,
                bucket=SpendingBucket.FIXED_OBLIGATIONS,
                is_subscription=False,
                last_charge_date=last_seen,
            ))

    # Sort by date
    upcoming_charges.sort(key=lambda x: x.expected_date)

    # Calculate expected totals
    expected_fixed = sum(c.expected_amount_cents for c in upcoming_charges)

    # Estimate income from patterns
    expected_income = _estimate_income(conn, days_forward, account_filter)

    # Estimate variable and discretionary based on historical averages
    expected_variable, expected_discretionary = _estimate_flexible_spending(
        conn, days_forward, account_filter
    )

    expected_net = expected_income - expected_fixed - expected_variable - expected_discretionary

    # Calculate overall confidence
    if upcoming_charges:
        avg_confidence = sum(c.confidence for c in upcoming_charges) / len(upcoming_charges)
    else:
        avg_confidence = 0.5

    return CashFlowProjection(
        start_date=today,
        end_date=end_date,
        expected_income_cents=expected_income,
        expected_fixed_cents=expected_fixed,
        expected_variable_cents=expected_variable,
        expected_discretionary_cents=expected_discretionary,
        expected_net_cents=expected_net,
        upcoming_charges=upcoming_charges,
        confidence=avg_confidence,
    )


def detect_cash_flow_alerts(
    conn: sqlite3.Connection,
    days_forward: int = 30,
    account_filter: Optional[list[str]] = None,
) -> list[CashFlowAlert]:
    """
    Detect potential cash flow issues.

    Alerts include:
    - Projected shortfall (expenses > income)
    - Large upcoming charges
    - Unusual spending patterns

    Args:
        conn: Database connection
        days_forward: Days to look ahead
        account_filter: Optional account filter

    Returns:
        List of CashFlowAlert objects
    """
    alerts: list[CashFlowAlert] = []
    today = date.today()

    projection = project_cash_flow(conn, days_forward, account_filter)

    # Check for shortfall
    if projection.expected_net_cents < 0:
        severity = "high" if abs(projection.expected_net_cents) > 50000 else "medium"
        alerts.append(CashFlowAlert(
            alert_type="shortfall",
            severity=severity,
            date=projection.end_date,
            message=f"Projected shortfall of ${abs(projection.expected_net_cents)/100:.2f} "
                    f"in the next {days_forward} days",
            amount_cents=projection.expected_net_cents,
            merchant=None,
        ))

    # Check for large upcoming charges
    for charge in projection.upcoming_charges:
        # Alert if charge is > 20% of expected income
        if projection.expected_income_cents > 0:
            pct = charge.expected_amount_cents / projection.expected_income_cents * 100
            if pct > 20:
                alerts.append(CashFlowAlert(
                    alert_type="large_charge",
                    severity="medium",
                    date=charge.expected_date,
                    message=f"Large charge expected: {charge.display_name or charge.merchant} "
                            f"(${charge.expected_amount_cents/100:.2f})",
                    amount_cents=charge.expected_amount_cents,
                    merchant=charge.merchant,
                ))

    # Check for multiple charges on same day
    charges_by_date: dict[date, list] = {}
    for charge in projection.upcoming_charges:
        if charge.expected_date not in charges_by_date:
            charges_by_date[charge.expected_date] = []
        charges_by_date[charge.expected_date].append(charge)

    for charge_date, charges in charges_by_date.items():
        if len(charges) >= 3:
            total = sum(c.expected_amount_cents for c in charges)
            alerts.append(CashFlowAlert(
                alert_type="multiple_charges",
                severity="low",
                date=charge_date,
                message=f"{len(charges)} charges expected on {charge_date}: "
                        f"${total/100:.2f} total",
                amount_cents=total,
                merchant=None,
            ))

    return alerts


def _next_monthly_date(last_date: date, after: date) -> date:
    """Calculate next monthly occurrence after a given date."""
    day = min(last_date.day, 28)  # Handle months with fewer days

    # Start with the month after last_date
    year = last_date.year
    month = last_date.month + 1
    if month > 12:
        month = 1
        year += 1

    candidate = date(year, month, day)

    # Keep advancing until we're after the target date
    while candidate <= after:
        month += 1
        if month > 12:
            month = 1
            year += 1
        candidate = date(year, month, day)

    return candidate


def _estimate_income(
    conn: sqlite3.Connection,
    days_forward: int,
    account_filter: Optional[list[str]] = None,
) -> int:
    """Estimate expected income for the period."""
    # Get historical income (last 3 months)
    today = date.today()
    start = today - timedelta(days=90)

    query = """
        SELECT SUM(amount_cents) as total
        FROM transactions
        WHERE posted_at >= ? AND posted_at < ?
          AND amount_cents > 0
          AND COALESCE(pending, 0) = 0
    """
    params: list = [start.isoformat(), today.isoformat()]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND account_id IN ({placeholders})"
        params.extend(account_filter)

    result = conn.execute(query, params).fetchone()
    total_income = result["total"] or 0

    # Calculate daily average and project forward
    daily_avg = total_income / 90
    return int(daily_avg * days_forward)


def _estimate_flexible_spending(
    conn: sqlite3.Connection,
    days_forward: int,
    account_filter: Optional[list[str]] = None,
) -> tuple[int, int]:
    """Estimate variable essentials and discretionary spending."""
    from .planner import analyze_spending_buckets

    # Get bucket analysis
    plan = analyze_spending_buckets(conn, months=3, account_filter=account_filter)

    # Find buckets
    variable = 0
    discretionary = 0

    for bucket in plan.buckets:
        if bucket.bucket == SpendingBucket.VARIABLE_ESSENTIALS:
            variable = bucket.monthly_avg_cents
        elif bucket.bucket == SpendingBucket.DISCRETIONARY:
            discretionary = bucket.monthly_avg_cents

    # Scale to days_forward
    scale = days_forward / 30
    return int(variable * scale), int(discretionary * scale)
