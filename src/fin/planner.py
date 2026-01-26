# planner.py
"""
Budget planning module using spending buckets.

TRUTH CONTRACT:
- FIXED_OBLIGATIONS: Predictable cadence subscriptions/utilities
- VARIABLE_ESSENTIALS: Habitual but irregular necessities (groceries, gas)
- DISCRETIONARY: Optional/lifestyle spending
- ONE_OFFS: Truly one-time purchases

Habitual spending (groceries 6x/month) is NOT fixed obligations.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .reporting_models import SpendingBucket


@dataclass
class BucketSummary:
    """Summary for a single spending bucket."""
    bucket: SpendingBucket
    label: str
    description: str
    monthly_avg_cents: int
    monthly_min_cents: int
    monthly_max_cents: int
    trend: str  # "stable", "increasing", "decreasing"
    trend_percent: float
    predictability: float  # 0.0-1.0, how predictable is this bucket
    merchant_count: int
    transaction_count: int


@dataclass
class BudgetPlan:
    """A budget plan based on historical spending."""
    period_months: int
    total_monthly_income_cents: int
    total_monthly_spend_cents: int
    net_monthly_cents: int
    buckets: list[BucketSummary]
    savings_rate: float  # % of income saved
    suggestions: list[str]
    health_score: float  # 0.0-1.0


@dataclass
class BucketDetail:
    """Detailed breakdown of a spending bucket."""
    bucket: SpendingBucket
    merchants: list[dict]  # [{merchant, monthly_cents, count, trend}]
    monthly_totals: list[dict]  # [{month, amount_cents}]


def analyze_spending_buckets(
    conn: sqlite3.Connection,
    months: int = 6,
    account_filter: Optional[list[str]] = None,
) -> BudgetPlan:
    """
    Analyze spending by bucket for budget planning.

    Args:
        conn: Database connection
        months: Number of months to analyze
        account_filter: Optional account filter

    Returns:
        BudgetPlan with bucket summaries and suggestions
    """
    from .classifier import classify_transaction, OverrideRegistry, _detect_patterns

    # Calculate date range
    today = date.today()
    start_date = date(today.year, today.month, 1) - timedelta(days=30 * months)
    end_date = date(today.year, today.month, 1)

    # Load patterns and overrides
    patterns = _detect_patterns(conn, lookback_days=months * 35, account_filter=account_filter)
    override_registry = OverrideRegistry()
    override_registry.load_from_db(conn)

    # Query transactions
    query = """
        SELECT
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm,
            t.account_id
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND COALESCE(t.pending, 0) = 0
    """
    params: list = [start_date.isoformat(), end_date.isoformat()]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    query += " ORDER BY t.posted_at"
    rows = conn.execute(query, params).fetchall()

    # Classify and bucket transactions
    bucket_data: dict[SpendingBucket, dict] = {
        b: {"amounts": [], "merchants": set(), "by_month": {}}
        for b in SpendingBucket
    }
    income_by_month: dict[str, int] = {}

    for row in rows:
        amount = row["amount_cents"]
        merchant = row["merchant_norm"]
        posted = row["posted_at"][:7]  # YYYY-MM
        pattern = patterns.get(merchant)

        # Classify transaction
        result = classify_transaction(
            amount_cents=amount,
            merchant_norm=merchant,
            is_credit_card_account=False,
            override_registry=override_registry,
            pattern=pattern,
        )

        if amount > 0:
            # Income
            income_by_month[posted] = income_by_month.get(posted, 0) + amount
        elif result.spending_bucket:
            bucket = result.spending_bucket
            abs_amount = abs(amount)
            bucket_data[bucket]["amounts"].append(abs_amount)
            bucket_data[bucket]["merchants"].add(merchant)

            if posted not in bucket_data[bucket]["by_month"]:
                bucket_data[bucket]["by_month"][posted] = 0
            bucket_data[bucket]["by_month"][posted] += abs_amount

    # Calculate bucket summaries
    bucket_summaries = []
    total_monthly_spend = 0

    bucket_labels = {
        SpendingBucket.FIXED_OBLIGATIONS: ("Fixed Obligations", "Predictable subscriptions & utilities"),
        SpendingBucket.VARIABLE_ESSENTIALS: ("Variable Essentials", "Groceries, gas, medicine"),
        SpendingBucket.DISCRETIONARY: ("Discretionary", "Dining, entertainment, shopping"),
        SpendingBucket.ONE_OFFS: ("One-offs", "Large purchases, annual fees"),
    }

    for bucket, (label, description) in bucket_labels.items():
        data = bucket_data[bucket]
        monthly_amounts = list(data["by_month"].values())

        if monthly_amounts:
            monthly_avg = sum(monthly_amounts) // len(monthly_amounts)
            monthly_min = min(monthly_amounts)
            monthly_max = max(monthly_amounts)

            # Calculate trend
            if len(monthly_amounts) >= 3:
                recent = sum(monthly_amounts[-2:]) / 2
                older = sum(monthly_amounts[:-2]) / max(len(monthly_amounts) - 2, 1)
                if older > 0:
                    trend_pct = (recent - older) / older * 100
                    if trend_pct > 10:
                        trend = "increasing"
                    elif trend_pct < -10:
                        trend = "decreasing"
                    else:
                        trend = "stable"
                else:
                    trend = "stable"
                    trend_pct = 0
            else:
                trend = "stable"
                trend_pct = 0

            # Calculate predictability (coefficient of variation)
            if len(monthly_amounts) > 1:
                mean = sum(monthly_amounts) / len(monthly_amounts)
                variance = sum((a - mean) ** 2 for a in monthly_amounts) / len(monthly_amounts)
                std_dev = variance ** 0.5
                cv = std_dev / mean if mean > 0 else 0
                predictability = max(0, 1 - cv)
            else:
                predictability = 0.5

            total_monthly_spend += monthly_avg
        else:
            monthly_avg = monthly_min = monthly_max = 0
            trend = "stable"
            trend_pct = 0
            predictability = 0

        bucket_summaries.append(BucketSummary(
            bucket=bucket,
            label=label,
            description=description,
            monthly_avg_cents=monthly_avg,
            monthly_min_cents=monthly_min,
            monthly_max_cents=monthly_max,
            trend=trend,
            trend_percent=trend_pct,
            predictability=predictability,
            merchant_count=len(data["merchants"]),
            transaction_count=len(data["amounts"]),
        ))

    # Calculate income
    total_monthly_income = sum(income_by_month.values()) // max(len(income_by_month), 1)
    net_monthly = total_monthly_income - total_monthly_spend

    # Calculate savings rate
    savings_rate = net_monthly / total_monthly_income * 100 if total_monthly_income > 0 else 0

    # Calculate health score
    health_factors = []
    if savings_rate >= 20:
        health_factors.append(1.0)
    elif savings_rate >= 10:
        health_factors.append(0.7)
    elif savings_rate >= 0:
        health_factors.append(0.4)
    else:
        health_factors.append(0.1)

    # Predictability of fixed obligations
    fixed_bucket = next((b for b in bucket_summaries if b.bucket == SpendingBucket.FIXED_OBLIGATIONS), None)
    if fixed_bucket:
        health_factors.append(fixed_bucket.predictability)

    health_score = sum(health_factors) / len(health_factors) if health_factors else 0.5

    # Generate suggestions
    suggestions = []

    if savings_rate < 10:
        suggestions.append(
            f"Savings rate is {savings_rate:.1f}%. Consider reducing discretionary spending."
        )

    discretionary = next((b for b in bucket_summaries if b.bucket == SpendingBucket.DISCRETIONARY), None)
    if discretionary and discretionary.trend == "increasing":
        suggestions.append(
            f"Discretionary spending is up {discretionary.trend_percent:.1f}% recently."
        )

    one_offs = next((b for b in bucket_summaries if b.bucket == SpendingBucket.ONE_OFFS), None)
    if one_offs and one_offs.monthly_avg_cents > total_monthly_income * 0.2:
        suggestions.append(
            "One-off spending is high. Review for unexpected large purchases."
        )

    if fixed_bucket and fixed_bucket.predictability < 0.7:
        suggestions.append(
            "Fixed obligations vary more than expected. Review subscriptions for price changes."
        )

    return BudgetPlan(
        period_months=months,
        total_monthly_income_cents=total_monthly_income,
        total_monthly_spend_cents=total_monthly_spend,
        net_monthly_cents=net_monthly,
        buckets=bucket_summaries,
        savings_rate=savings_rate,
        suggestions=suggestions,
        health_score=health_score,
    )


def get_bucket_detail(
    conn: sqlite3.Connection,
    bucket: SpendingBucket,
    months: int = 6,
    account_filter: Optional[list[str]] = None,
) -> BucketDetail:
    """
    Get detailed breakdown for a specific spending bucket.

    Args:
        conn: Database connection
        bucket: Bucket to analyze
        months: Number of months

    Returns:
        BucketDetail with merchant breakdown and monthly totals
    """
    from .classifier import classify_transaction, OverrideRegistry, _detect_patterns

    # Calculate date range
    today = date.today()
    start_date = date(today.year, today.month, 1) - timedelta(days=30 * months)
    end_date = date(today.year, today.month, 1)

    # Load patterns and overrides
    patterns = _detect_patterns(conn, lookback_days=months * 35, account_filter=account_filter)
    override_registry = OverrideRegistry()
    override_registry.load_from_db(conn)

    # Query transactions
    query = """
        SELECT
            t.posted_at,
            t.amount_cents,
            TRIM(LOWER(COALESCE(NULLIF(t.merchant,''), NULLIF(t.description,''), ''))) AS merchant_norm
        FROM transactions t
        WHERE t.posted_at >= ? AND t.posted_at < ?
          AND t.amount_cents < 0
          AND COALESCE(t.pending, 0) = 0
    """
    params: list = [start_date.isoformat(), end_date.isoformat()]

    if account_filter:
        placeholders = ",".join("?" * len(account_filter))
        query += f" AND t.account_id IN ({placeholders})"
        params.extend(account_filter)

    rows = conn.execute(query, params).fetchall()

    # Classify and filter to bucket
    merchant_data: dict[str, dict] = {}
    monthly_totals: dict[str, int] = {}

    for row in rows:
        amount = abs(row["amount_cents"])
        merchant = row["merchant_norm"]
        posted = row["posted_at"][:7]  # YYYY-MM
        pattern = patterns.get(merchant)

        result = classify_transaction(
            amount_cents=row["amount_cents"],
            merchant_norm=merchant,
            is_credit_card_account=False,
            override_registry=override_registry,
            pattern=pattern,
        )

        if result.spending_bucket != bucket:
            continue

        # Track by merchant
        if merchant not in merchant_data:
            merchant_data[merchant] = {"total": 0, "count": 0, "months": set()}
        merchant_data[merchant]["total"] += amount
        merchant_data[merchant]["count"] += 1
        merchant_data[merchant]["months"].add(posted)

        # Track monthly totals
        monthly_totals[posted] = monthly_totals.get(posted, 0) + amount

    # Build merchant list
    merchants = []
    num_months = max(len(monthly_totals), 1)
    for merchant, data in merchant_data.items():
        monthly_avg = data["total"] // num_months
        merchants.append({
            "merchant": merchant,
            "monthly_cents": monthly_avg,
            "total_cents": data["total"],
            "count": data["count"],
            "active_months": len(data["months"]),
        })

    merchants.sort(key=lambda x: -x["monthly_cents"])

    # Build monthly totals list
    monthly_list = [
        {"month": m, "amount_cents": a}
        for m, a in sorted(monthly_totals.items())
    ]

    return BucketDetail(
        bucket=bucket,
        merchants=merchants[:50],  # Top 50
        monthly_totals=monthly_list,
    )


def project_monthly_budget(
    conn: sqlite3.Connection,
    months_history: int = 6,
    months_forward: int = 3,
    account_filter: Optional[list[str]] = None,
) -> dict:
    """
    Project future monthly budget based on historical spending.

    Returns a simplified projection of expected income and expenses.
    """
    plan = analyze_spending_buckets(conn, months_history, account_filter)

    # Project forward
    projections = []
    today = date.today()

    for i in range(months_forward):
        future_month = date(today.year, today.month, 1) + timedelta(days=30 * i)
        month_str = future_month.strftime("%Y-%m")

        projections.append({
            "month": month_str,
            "projected_income_cents": plan.total_monthly_income_cents,
            "projected_fixed_cents": next(
                (b.monthly_avg_cents for b in plan.buckets if b.bucket == SpendingBucket.FIXED_OBLIGATIONS), 0
            ),
            "projected_variable_cents": next(
                (b.monthly_avg_cents for b in plan.buckets if b.bucket == SpendingBucket.VARIABLE_ESSENTIALS), 0
            ),
            "projected_discretionary_cents": next(
                (b.monthly_avg_cents for b in plan.buckets if b.bucket == SpendingBucket.DISCRETIONARY), 0
            ),
            "projected_net_cents": plan.net_monthly_cents,
        })

    return {
        "based_on_months": months_history,
        "projections": projections,
        "assumptions": [
            "Income remains stable",
            "Fixed obligations stay constant",
            "Variable essentials follow historical average",
            "Discretionary spending follows historical average",
        ],
    }
