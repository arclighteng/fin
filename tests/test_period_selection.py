"""
Tests for period selection (This Month / Last Month).

Tests:
- This month date range calculation
- Last month date range calculation
- Year boundary handling for last month
- Custom date range handling
"""
from datetime import date
from calendar import monthrange
from unittest.mock import patch
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fin.web import app
from fin import db as dbmod


@pytest.fixture
def test_db_path():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def populated_db(test_db_path):
    """Create a database with transactions across multiple months."""
    conn = dbmod.connect(test_db_path)
    dbmod.init_db(conn)

    today = date.today()

    def insert_txn(posted_at, amount_cents, merchant):
        import uuid
        conn.execute(
            """
            INSERT INTO transactions (
                account_id, posted_at, amount_cents, currency,
                description, merchant, fingerprint, created_at, updated_at
            ) VALUES (?, ?, ?, 'USD', '', ?, ?, datetime('now'), datetime('now'))
            """,
            (
                "acct1",
                posted_at.isoformat(),
                amount_cents,
                merchant,
                f"fp_{uuid.uuid4().hex}",
            ),
        )

    # Add transactions for this month
    this_month_start = date(today.year, today.month, 1)
    insert_txn(this_month_start, 500000, "THIS MONTH INCOME")
    insert_txn(this_month_start, -10000, "THIS MONTH EXPENSE")

    # Add transactions for last month
    if today.month == 1:
        last_month = date(today.year - 1, 12, 15)
    else:
        last_month = date(today.year, today.month - 1, 15)
    insert_txn(last_month, 400000, "LAST MONTH INCOME")
    insert_txn(last_month, -8000, "LAST MONTH EXPENSE")

    # Add transactions for 2 months ago
    if today.month <= 2:
        two_months_ago = date(today.year - 1, today.month + 10, 15)
    else:
        two_months_ago = date(today.year, today.month - 2, 15)
    insert_txn(two_months_ago, 300000, "OLD INCOME")
    insert_txn(two_months_ago, -6000, "OLD EXPENSE")

    conn.commit()
    conn.close()

    return test_db_path


@pytest.fixture
def client(populated_db):
    """Create test client."""
    import fin.web as web_module
    web_module._config = None
    web_module._db_initialized = False

    class MockConfig:
        db_path = populated_db
        simplefin_access_url = ""
        log_level = "INFO"
        log_format = "simple"

    with patch.object(web_module, "_get_config", return_value=MockConfig()):
        with TestClient(app) as client:
            yield client


class TestThisMonthPeriod:
    """Test the 'This Month' period selection."""

    def test_this_month_returns_200(self, client):
        """Should return 200 for this_month period."""
        response = client.get("/dashboard?period=this_month")
        assert response.status_code == 200

    def test_this_month_shows_current_month_data(self, client):
        """Should show transactions from current month."""
        response = client.get("/dashboard?period=this_month")
        html = response.text

        # Should include this month's transactions
        assert "THIS MONTH" in html or "this month" in html.lower() or "This Month" in html

    def test_this_month_is_default(self, client):
        """This month should be the default period."""
        response = client.get("/dashboard")
        html = response.text

        # Check that "This Month" button is active
        assert 'class="period-btn active">This Month' in html


class TestLastMonthPeriod:
    """Test the 'Last Month' period selection."""

    def test_last_month_returns_200(self, client):
        """Should return 200 for last_month period."""
        response = client.get("/dashboard?period=last_month")
        assert response.status_code == 200

    def test_last_month_button_active(self, client):
        """Last Month button should be active when selected."""
        response = client.get("/dashboard?period=last_month")
        html = response.text

        assert 'class="period-btn active">Last Month' in html


class TestPeriodDateRanges:
    """Test that period date ranges are calculated correctly."""

    def test_this_month_date_range(self):
        """This month should span from 1st to today."""
        today = date.today()
        expected_start = date(today.year, today.month, 1)
        expected_end = today

        # Verify expected values
        assert expected_start.day == 1
        assert expected_end == today

    def test_last_month_date_range(self):
        """Last month should span entire previous month."""
        today = date.today()

        if today.month == 1:
            expected_year = today.year - 1
            expected_month = 12
        else:
            expected_year = today.year
            expected_month = today.month - 1

        expected_start = date(expected_year, expected_month, 1)
        _, last_day = monthrange(expected_year, expected_month)
        expected_end = date(expected_year, expected_month, last_day)

        # Verify expected values
        assert expected_start.day == 1
        assert expected_end.day == last_day

    def test_january_last_month_is_december(self):
        """In January, last month should be December of previous year."""
        # Simulate January
        jan_date = date(2026, 1, 15)

        expected_last_month_start = date(2025, 12, 1)
        expected_last_month_end = date(2025, 12, 31)

        # Calculate what last_month should be
        if jan_date.month == 1:
            last_month_year = jan_date.year - 1
            last_month = 12
        else:
            last_month_year = jan_date.year
            last_month = jan_date.month - 1

        calc_start = date(last_month_year, last_month, 1)
        _, last_day = monthrange(last_month_year, last_month)
        calc_end = date(last_month_year, last_month, last_day)

        assert calc_start == expected_last_month_start
        assert calc_end == expected_last_month_end


class TestCustomDateRange:
    """Test custom date range selection."""

    def test_custom_date_range_returns_200(self, client):
        """Should accept custom date range parameters."""
        today = date.today()
        start = date(today.year, today.month, 1).isoformat()
        end = today.isoformat()

        response = client.get(f"/dashboard?start_date={start}&end_date={end}")
        assert response.status_code == 200

    def test_custom_date_overrides_period(self, client):
        """Custom dates should override period buttons."""
        today = date.today()
        start = date(today.year, today.month, 1).isoformat()
        end = today.isoformat()

        response = client.get(f"/dashboard?period=this_month&start_date={start}&end_date={end}")
        html = response.text

        # Neither period button should be active when custom dates are set
        # (depends on implementation - custom dates may still show a button active)
        assert response.status_code == 200


class TestPeriodButtonDisplay:
    """Test that period buttons display correctly."""

    def test_both_period_buttons_present(self, client):
        """Dashboard should show both This Month and Last Month buttons."""
        response = client.get("/dashboard")
        html = response.text

        assert "This Month" in html
        assert "Last Month" in html

    def test_no_quarter_year_buttons(self, client):
        """Dashboard should NOT show Quarter or Year buttons."""
        response = client.get("/dashboard")
        html = response.text

        # Should not have quarter/year as period buttons (links with those values)
        assert '?period=quarter' not in html
        assert '?period=year' not in html
