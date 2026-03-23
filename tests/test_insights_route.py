import pytest
from unittest.mock import MagicMock
from fin.web import _compute_insights_data


def _make_report(income_cents, net_cents, year=2026, month=1):
    """Create a minimal mock FinancialReport matching the real Report structure."""
    from datetime import date
    report = MagicMock()
    report.start_date = date(year, month, 1)
    # totals mirrors reporting_models.PeriodTotals
    report.totals = MagicMock()
    report.totals.income_cents = income_cents
    report.totals.net_cents = net_cents
    return report


# --- savings_history ---

def test_insights_empty_reports():
    result = _compute_insights_data([])
    assert result["savings_history"] == []
    assert result["months_with_data"] == 0
    assert result["savings_streak"] == 0


def test_insights_savings_history_single_month():
    r = _make_report(income_cents=500000, net_cents=100000, year=2026, month=3)
    result = _compute_insights_data([r])
    assert len(result["savings_history"]) == 1
    entry = result["savings_history"][0]
    assert entry["net_cents"] == 100000
    assert entry["income_cents"] == 500000
    assert entry["savings_rate_pct"] == pytest.approx(20.0)


def test_insights_savings_history_chronological_order():
    """Most-recent-first input should produce oldest-first output."""
    reports = [
        _make_report(400000, 80000, year=2026, month=3),   # newest
        _make_report(400000, 40000, year=2026, month=2),
        _make_report(400000, 20000, year=2026, month=1),   # oldest
    ]
    result = _compute_insights_data(reports)
    # oldest first: Jan=5%, Feb=10%, Mar=20%
    assert result["savings_history"][0]["savings_rate_pct"] == pytest.approx(5.0)   # Jan
    assert result["savings_history"][-1]["savings_rate_pct"] == pytest.approx(20.0)  # Mar


def test_insights_skips_zero_income_months():
    reports = [
        _make_report(400000, 80000, year=2026, month=3),
        _make_report(0, 0, year=2026, month=2),  # no income — should be excluded
        _make_report(400000, 40000, year=2026, month=1),
    ]
    result = _compute_insights_data(reports)
    assert result["months_with_data"] == 2
    assert all(e["income_cents"] > 0 for e in result["savings_history"])


# --- avg_savings_rate_pct ---

def test_insights_avg_savings_rate():
    reports = [
        _make_report(400000, 80000),   # 20%
        _make_report(400000, 40000),   # 10%
    ]
    result = _compute_insights_data(reports)
    assert result["avg_savings_rate_pct"] == pytest.approx(15.0)


# --- income_cv and income_stability ---

def test_insights_stable_income():
    """Identical income every month → cv = 0 → stable."""
    reports = [_make_report(400000, 80000, month=i) for i in range(1, 4)]
    result = _compute_insights_data(reports)
    assert result["income_cv"] == pytest.approx(0.0)
    assert result["income_stability"] == "stable"


def test_insights_variable_income():
    """Highly variable income → cv > 25% → variable."""
    reports = [
        _make_report(100000, 10000, month=1),
        _make_report(900000, 90000, month=2),
    ]
    result = _compute_insights_data(reports)
    assert result["income_cv"] > 25.0
    assert result["income_stability"] == "variable"


def test_insights_income_stability_single_month():
    """Only one month of data → cv = 0, stability = stable."""
    result = _compute_insights_data([_make_report(400000, 80000)])
    assert result["income_cv"] == 0.0
    assert result["income_stability"] == "stable"


# --- savings_streak ---

def test_insights_streak_all_positive():
    reports = [
        _make_report(400000, 80000, month=3),
        _make_report(400000, 40000, month=2),
        _make_report(400000, 20000, month=1),
    ]
    result = _compute_insights_data(reports)
    assert result["savings_streak"] == 3


def test_insights_streak_broken_by_negative():
    """Streak counts from most recent; resets at first negative."""
    reports = [
        _make_report(400000, 80000, month=3),    # positive
        _make_report(400000, -10000, month=2),   # negative — breaks streak
        _make_report(400000, 20000, month=1),    # positive but doesn't count
    ]
    result = _compute_insights_data(reports)
    assert result["savings_streak"] == 1


def test_insights_streak_zero_when_latest_negative():
    reports = [
        _make_report(400000, -10000, month=3),
        _make_report(400000, 40000, month=2),
    ]
    result = _compute_insights_data(reports)
    assert result["savings_streak"] == 0


# --- required keys ---

def test_insights_required_keys():
    result = _compute_insights_data([_make_report(400000, 80000)])
    required = {
        "savings_history", "avg_savings_rate_pct", "income_cv",
        "income_stability", "savings_streak", "months_with_data",
    }
    assert required.issubset(set(result.keys()))


# ============================================================
# Route smoke tests
# ============================================================
from unittest.mock import patch
from fastapi.testclient import TestClient
from fin.web import app


@pytest.fixture
def client(temp_db_path):
    """TestClient with an isolated database (matches pattern in test_period_selection.py)."""
    import fin.web as web_module
    import fin.db as dbmod
    conn = dbmod.connect(temp_db_path)
    dbmod.init_db(conn)
    conn.close()

    web_module._config = None
    web_module._db_initialized = False

    class MockConfig:
        db_path = temp_db_path
        simplefin_access_url = ""
        log_level = "INFO"
        log_format = "simple"

    with patch.object(web_module, "_get_config", return_value=MockConfig()):
        with TestClient(app) as c:
            yield c


def test_insights_route_returns_200(client):
    response = client.get("/insights")
    assert response.status_code == 200


def test_insights_route_contains_heading(client):
    response = client.get("/insights")
    assert "Financial Insights" in response.text


def test_plan_route_returns_200(client):
    response = client.get("/plan")
    assert response.status_code == 200


def test_plan_route_contains_heading(client):
    response = client.get("/plan")
    assert "Plan" in response.text
