import pytest
from fin.web import _compute_savings_tier, _compute_pace_data


# --- Savings Tier ---

def test_savings_tier_wealth_building():
    assert _compute_savings_tier(30.0) == "wealth-building"

def test_savings_tier_above_30():
    assert _compute_savings_tier(45.0) == "wealth-building"

def test_savings_tier_progress_exactly_20():
    assert _compute_savings_tier(20.0) == "progress"

def test_savings_tier_progress_mid():
    assert _compute_savings_tier(25.0) == "progress"

def test_savings_tier_survival():
    assert _compute_savings_tier(5.0) == "survival"
    assert _compute_savings_tier(0.1) == "survival"

def test_savings_tier_negative_zero():
    assert _compute_savings_tier(0.0) == "negative"

def test_savings_tier_negative():
    assert _compute_savings_tier(-10.0) == "negative"


# --- Intra-Month Pace ---

def test_pace_data_zero_days_in_month_returns_none():
    """Returns None when days_in_month is 0 (defensive guard)."""
    result = _compute_pace_data(
        total_expenses_cents=100000,
        days_elapsed=5,
        days_in_month=0,
        avg_monthly_expenses_cents=300000,
        category_breakdown=[],
        category_averages={},
    )
    assert result is None


def test_pace_data_too_early_returns_none():
    """Returns None when < 3 days elapsed — too early to project reliably."""
    result = _compute_pace_data(
        total_expenses_cents=5000,
        days_elapsed=2,
        days_in_month=30,
        avg_monthly_expenses_cents=300000,
        category_breakdown=[],
        category_averages={},
    )
    assert result is None


def test_pace_data_basic_projection():
    """At day 15 of 30, spending $2000 projects to $4000 end-of-month."""
    result = _compute_pace_data(
        total_expenses_cents=200000,  # $2000 spent so far
        days_elapsed=15,
        days_in_month=30,
        avg_monthly_expenses_cents=360000,  # $3600 avg
        category_breakdown=[],
        category_averages={},
    )
    assert result is not None
    assert result["days_elapsed"] == 15
    assert result["days_in_month"] == 30
    # $2000 / (15/30) = $4000 projected
    assert result["projected_spend_cents"] == 400000
    # $4000 - $3600 = +$400
    assert result["variance_cents"] == 40000
    # int(40000 * 100 / 360000) = int(11.11) = 11
    assert result["variance_pct"] == 11


def test_pace_data_under_budget():
    """When projected < avg, variance is negative (good)."""
    result = _compute_pace_data(
        total_expenses_cents=100000,  # $1000 so far
        days_elapsed=15,
        days_in_month=30,
        avg_monthly_expenses_cents=360000,  # $3600 avg
        category_breakdown=[],
        category_averages={},
    )
    assert result is not None
    # $1000 / 0.5 = $2000 projected
    assert result["projected_spend_cents"] == 200000
    # $2000 - $3600 = -$1600
    assert result["variance_cents"] == -160000
    assert result["variance_pct"] < 0


def test_pace_data_top_drivers_surfaces_over_pace_categories():
    """Top drivers include categories where projected > avg by >$20."""
    from types import SimpleNamespace
    dining = SimpleNamespace(id="dining", label="Dining")

    result = _compute_pace_data(
        total_expenses_cents=200000,
        days_elapsed=15,
        days_in_month=30,
        avg_monthly_expenses_cents=300000,
        category_breakdown=[(dining, 50000, 5, 50000, 0)],  # $500 dining so far
        category_averages={"dining": 30000},  # avg $300/month
    )
    # Projected dining: $500 / 0.5 = $1000; avg $300 → +$700 variance
    assert result is not None
    assert len(result["top_drivers"]) == 1
    driver = result["top_drivers"][0]
    assert driver["category_id"] == "dining"
    assert driver["projected_cents"] == 100000  # $1000
    assert driver["variance_cents"] == 70000    # +$700


def test_pace_data_no_drivers_when_under_avg():
    """No top drivers shown when all categories are under their averages."""
    from types import SimpleNamespace
    dining = SimpleNamespace(id="dining", label="Dining")

    result = _compute_pace_data(
        total_expenses_cents=50000,
        days_elapsed=15,
        days_in_month=30,
        avg_monthly_expenses_cents=300000,
        category_breakdown=[(dining, 10000, 2, 10000, 0)],  # $100 dining so far
        category_averages={"dining": 30000},  # avg $300
    )
    # Projected dining: $100 / 0.5 = $200 < $300 avg → no driver
    assert result is not None
    assert result["top_drivers"] == []


def test_pace_data_has_required_keys():
    """Result dict contains all required keys for template rendering."""
    result = _compute_pace_data(
        total_expenses_cents=150000,
        days_elapsed=10,
        days_in_month=31,
        avg_monthly_expenses_cents=300000,
        category_breakdown=[],
        category_averages={},
    )
    assert result is not None
    required_keys = {
        "days_elapsed", "days_in_month", "pacing_factor",
        "total_spend_so_far_cents", "projected_spend_cents",
        "avg_monthly_expenses_cents", "variance_cents",
        "variance_pct", "top_drivers",
    }
    assert required_keys.issubset(set(result.keys()))
