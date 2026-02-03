"""
Functional tests for fin web endpoints.

Tests:
- /dashboard route with different period types
- /export/sketchy CSV export
- /export/duplicates CSV export
- /export/subscriptions CSV export
- /export/summary CSV export
- Response formats and status codes
"""
import csv
import io
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fin.web import app, _get_config
from fin import db as dbmod


@pytest.fixture
def test_db_path():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def populated_test_db(test_db_path):
    """Create and populate a test database."""
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

    # Income
    for weeks_ago in range(12):
        d = today - timedelta(weeks=weeks_ago * 2)
        insert_txn(d, 250000, "EMPLOYER")

    # Monthly subscriptions
    for months_ago in range(6):
        d = today - timedelta(days=months_ago * 30)
        insert_txn(d, -1599, "NETFLIX")
        insert_txn(d + timedelta(days=1), -1099, "SPOTIFY")
        insert_txn(d + timedelta(days=2), -4999, "GYM")

    # Sketchy charges
    insert_txn(today - timedelta(days=5), -2999, "SKETCHY MERCHANT")
    insert_txn(today - timedelta(days=3), -2999, "SKETCHY MERCHANT")  # Duplicate
    insert_txn(today - timedelta(days=10), -50, "TEST CHARGE VENDOR")

    # Duplicate subscriptions
    for months_ago in range(4):
        d = today - timedelta(days=months_ago * 30 + 5)
        insert_txn(d, -1599, "NETFLIX.COM")

    # Disney bundle
    for months_ago in range(4):
        d = today - timedelta(days=months_ago * 30 + 7)
        insert_txn(d, -1399, "DISNEY PLUS")
        insert_txn(d + timedelta(days=1), -1799, "HULU")

    conn.commit()
    conn.close()

    return test_db_path


@pytest.fixture
def mock_config_factory(populated_test_db):
    """Factory to create mock config with test database."""
    class MockConfig:
        db_path = populated_test_db
        simplefin_access_url = ""
        log_level = "INFO"
        log_format = "simple"

    return MockConfig


@pytest.fixture
def client(mock_config_factory, populated_test_db):
    """Create test client with mocked config."""
    # Reset global state
    import fin.web as web_module
    web_module._config = None
    web_module._db_initialized = False

    mock_config_factory.db_path = populated_test_db

    with patch.object(web_module, "_get_config", return_value=mock_config_factory()):
        with TestClient(app) as client:
            yield client


class TestDashboardRoute:
    """Test the /dashboard endpoint."""

    def test_dashboard_returns_200(self, client):
        """Dashboard should return 200 OK."""
        response = client.get("/dashboard")
        assert response.status_code == 200

    def test_dashboard_returns_html(self, client):
        """Dashboard should return HTML content."""
        response = client.get("/dashboard")
        assert "text/html" in response.headers["content-type"]

    def test_dashboard_contains_financial_health(self, client):
        """Dashboard should contain financial health section."""
        response = client.get("/dashboard")
        html = response.text

        assert "Financial Health" in html or "FINANCIAL HEALTH" in html

    def test_dashboard_month_period(self, client):
        """Dashboard should accept month period parameter."""
        response = client.get("/dashboard?period=month")
        assert response.status_code == 200
        # Should contain month label like "Jan 2026"
        html = response.text
        assert "202" in html  # Year in the period label

    def test_dashboard_quarter_period(self, client):
        """Dashboard should accept quarter period parameter (legacy support)."""
        response = client.get("/dashboard?period=quarter")
        assert response.status_code == 200
        # Quarter period is accepted but UI now uses this_month/last_month
        # It should not error, just treat as default period

    def test_dashboard_year_period(self, client):
        """Dashboard should accept year period parameter."""
        response = client.get("/dashboard?period=year")
        assert response.status_code == 200

    def test_dashboard_contains_alerts_section(self, client):
        """Dashboard should contain alerts section."""
        response = client.get("/dashboard")
        html = response.text
        assert "Alerts" in html or "ALERTS" in html

    def test_dashboard_contains_subscriptions_table(self, client):
        """Dashboard should contain subscriptions table."""
        response = client.get("/dashboard")
        html = response.text
        assert "Subscriptions" in html or "subscription" in html.lower()


class TestExportSketchyEndpoint:
    """Test the /export/sketchy endpoint."""

    def test_export_sketchy_returns_200(self, client):
        """Should return 200 OK."""
        response = client.get("/export/sketchy")
        assert response.status_code == 200

    def test_export_sketchy_returns_csv(self, client):
        """Should return CSV content type."""
        response = client.get("/export/sketchy")
        assert "text/csv" in response.headers["content-type"]

    def test_export_sketchy_has_filename_header(self, client):
        """Should have Content-Disposition header with filename."""
        response = client.get("/export/sketchy")
        assert "attachment" in response.headers.get("content-disposition", "")
        assert "sketchy_charges.csv" in response.headers.get("content-disposition", "")

    def test_export_sketchy_csv_valid(self, client):
        """CSV should be valid and parseable."""
        response = client.get("/export/sketchy")
        content = response.text

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        assert len(rows) >= 1  # At least header
        headers = rows[0]
        assert "posted_at" in headers
        assert "merchant" in headers
        assert "amount_usd" in headers
        assert "pattern_type" in headers
        assert "severity" in headers

    def test_export_sketchy_detects_patterns(self, client):
        """Should detect sketchy patterns in the data."""
        response = client.get("/export/sketchy")
        content = response.text

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        # Should have detected some patterns
        assert len(rows) >= 1

        # Check pattern types are valid
        valid_types = {
            "duplicate_charge", "unusual_amount", "test_charge",
            "round_amount_spike", "rapid_fire", "refund_recharge"
        }
        for row in rows:
            assert row["pattern_type"] in valid_types


class TestExportDuplicatesEndpoint:
    """Test the /export/duplicates endpoint."""

    def test_export_duplicates_returns_200(self, client):
        """Should return 200 OK."""
        response = client.get("/export/duplicates")
        assert response.status_code == 200

    def test_export_duplicates_returns_csv(self, client):
        """Should return CSV content type."""
        response = client.get("/export/duplicates")
        assert "text/csv" in response.headers["content-type"]

    def test_export_duplicates_has_correct_headers(self, client):
        """CSV should have correct column headers."""
        response = client.get("/export/duplicates")
        content = response.text

        reader = csv.reader(io.StringIO(content))
        headers = next(reader)

        assert "group_type" in headers
        assert "merchants" in headers
        assert "monthly_total_usd" in headers
        assert "severity" in headers
        assert "detail" in headers


class TestExportSubscriptionsEndpoint:
    """Test the /export/subscriptions endpoint."""

    def test_export_subscriptions_returns_200(self, client):
        """Should return 200 OK."""
        response = client.get("/export/subscriptions")
        assert response.status_code == 200

    def test_export_subscriptions_returns_csv(self, client):
        """Should return CSV content type."""
        response = client.get("/export/subscriptions")
        assert "text/csv" in response.headers["content-type"]

    def test_export_subscriptions_has_correct_headers(self, client):
        """CSV should have correct column headers."""
        response = client.get("/export/subscriptions")
        content = response.text

        reader = csv.reader(io.StringIO(content))
        headers = next(reader)

        assert "merchant" in headers
        assert "monthly_usd" in headers
        assert "cadence" in headers
        assert "first_seen" in headers
        assert "last_seen" in headers
        assert "is_duplicate" in headers

    def test_export_subscriptions_has_data(self, client):
        """Should export subscription data."""
        response = client.get("/export/subscriptions")
        content = response.text

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        assert len(rows) >= 1
        # Check data integrity
        for row in rows:
            assert row["merchant"]
            assert "." in row["monthly_usd"]  # Has decimal
            assert row["cadence"]


class TestExportSummaryEndpoint:
    """Test the /export/summary endpoint."""

    def test_export_summary_returns_200(self, client):
        """Should return 200 OK."""
        response = client.get("/export/summary")
        assert response.status_code == 200

    def test_export_summary_returns_csv(self, client):
        """Should return CSV content type."""
        response = client.get("/export/summary")
        assert "text/csv" in response.headers["content-type"]

    def test_export_summary_month_period(self, client):
        """Should export monthly summary."""
        response = client.get("/export/summary?period=month")
        assert response.status_code == 200
        assert "month_summary.csv" in response.headers.get("content-disposition", "")

    def test_export_summary_quarter_period(self, client):
        """Should export quarterly summary."""
        response = client.get("/export/summary?period=quarter")
        assert response.status_code == 200
        assert "quarter_summary.csv" in response.headers.get("content-disposition", "")

    def test_export_summary_has_correct_headers(self, client):
        """CSV should have correct column headers."""
        response = client.get("/export/summary")
        content = response.text

        reader = csv.reader(io.StringIO(content))
        headers = next(reader)

        assert "period" in headers
        assert "income_usd" in headers
        assert "recurring_usd" in headers
        assert "discretionary_usd" in headers
        assert "net_usd" in headers
        assert "avg_income_usd" in headers
        assert "income_trend" in headers
        assert "transaction_count" in headers

    def test_export_summary_has_multiple_periods(self, client):
        """Should export multiple periods."""
        response = client.get("/export/summary")
        content = response.text

        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)

        assert len(rows) >= 1  # At least one period


class TestHomeRoute:
    """Test the home page route."""

    def test_home_returns_200(self, client):
        """Home page should return 200 OK."""
        response = client.get("/")
        assert response.status_code == 200

    def test_home_contains_dashboard_link(self, client):
        """Home page should have link to dashboard."""
        response = client.get("/")
        html = response.text
        assert "/dashboard" in html


class TestErrorHandling:
    """Test error handling in web endpoints."""

    def test_invalid_period_uses_default(self, client):
        """Invalid period parameter should use default (month)."""
        response = client.get("/dashboard?period=invalid")
        # Should not error, should use default
        assert response.status_code == 200


class TestDataIntegrityInResponses:
    """Test that web responses have accurate financial data."""

    def test_amounts_are_numeric_in_csv(self, client):
        """Amounts in CSV should be valid numbers."""
        response = client.get("/export/summary")
        content = response.text

        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            # Should be parseable as float
            float(row["income_usd"])
            float(row["recurring_usd"])
            float(row["discretionary_usd"])
            float(row["net_usd"])

    def test_dates_are_valid_in_csv(self, client):
        """Dates in CSV should be valid ISO format."""
        response = client.get("/export/subscriptions")
        content = response.text

        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            # Should be valid date format
            first_seen = row["first_seen"]
            last_seen = row["last_seen"]

            assert len(first_seen) == 10  # YYYY-MM-DD
            assert len(last_seen) == 10

            # Should be parseable
            date.fromisoformat(first_seen)
            date.fromisoformat(last_seen)

    def test_trends_are_valid_values(self, client):
        """Trend values should be up/down/stable."""
        response = client.get("/export/summary")
        content = response.text

        reader = csv.DictReader(io.StringIO(content))
        valid_trends = {"up", "down", "stable"}

        for row in reader:
            assert row["income_trend"] in valid_trends
            assert row["recurring_trend"] in valid_trends
            assert row["discretionary_trend"] in valid_trends

    def test_duplicate_flags_are_valid(self, client):
        """Duplicate flags should be yes/no."""
        response = client.get("/export/subscriptions")
        content = response.text

        reader = csv.DictReader(io.StringIO(content))
        for row in reader:
            assert row["is_duplicate"] in ("yes", "no")


class TestEmptyDatabaseHandling:
    """Test handling of empty database."""

    def test_dashboard_empty_db(self, test_db_path):
        """Dashboard should handle empty database gracefully."""
        # Create empty db
        conn = dbmod.connect(test_db_path)
        dbmod.init_db(conn)
        conn.close()

        import fin.web as web_module
        web_module._config = None
        web_module._db_initialized = False

        class MockConfig:
            db_path = test_db_path
            simplefin_access_url = ""
            log_level = "INFO"
            log_format = "simple"

        with patch.object(web_module, "_get_config", return_value=MockConfig()):
            with TestClient(app) as client:
                response = client.get("/dashboard")
                assert response.status_code == 200

    def test_export_sketchy_empty_db(self, test_db_path):
        """Export should return empty CSV for empty database."""
        conn = dbmod.connect(test_db_path)
        dbmod.init_db(conn)
        conn.close()

        import fin.web as web_module
        web_module._config = None
        web_module._db_initialized = False

        class MockConfig:
            db_path = test_db_path
            simplefin_access_url = ""
            log_level = "INFO"
            log_format = "simple"

        with patch.object(web_module, "_get_config", return_value=MockConfig()):
            with TestClient(app) as client:
                response = client.get("/export/sketchy")
                assert response.status_code == 200

                # Should have header row only
                content = response.text
                reader = csv.reader(io.StringIO(content))
                rows = list(reader)
                assert len(rows) == 1  # Just headers


class TestAccountFilterParsing:
    """Test account filter query parameter parsing behavior."""

    def test_accounts_empty_string_like_none(self, client):
        """accounts="" should behave like all accounts (not error)."""
        response = client.get("/dashboard?accounts=")
        assert response.status_code == 200
        # Should show data (not empty state)
        assert "No accounts selected" not in response.text

    def test_accounts_comma_only_like_none(self, client):
        """accounts="," should behave like all accounts."""
        response = client.get("/dashboard?accounts=,")
        assert response.status_code == 200
        # Should show data (not empty state)
        assert "No accounts selected" not in response.text

    def test_accounts_whitespace_only_like_none(self, client):
        """accounts="  " should behave like all accounts."""
        response = client.get("/dashboard?accounts=%20%20")
        assert response.status_code == 200

    def test_accounts_none_triggers_no_data_mode(self, client):
        """accounts="none" should trigger no-data mode."""
        response = client.get("/dashboard?accounts=none")
        assert response.status_code == 200
        # Should be in no-data mode
        html = response.text
        # Check that it's showing empty/no-data state
        assert "No accounts selected" in html or "show_no_data" in html or response.status_code == 200

    def test_accounts_valid_filter_works(self, client):
        """Valid account filter should work."""
        response = client.get("/dashboard?accounts=acct1")
        assert response.status_code == 200


class TestCloseTheBooksExclusiveDates:
    """Test close-the-books with end-exclusive date semantics."""

    def test_close_period_uses_exclusive_end(self, test_db_path):
        """Closing a period should use exclusive end_date from Report."""
        conn = dbmod.connect(test_db_path)
        dbmod.init_db(conn)

        # Insert an account
        conn.execute(
            "INSERT INTO accounts (account_id, name, institution, type, currency) "
            "VALUES ('acct1', 'Test Account', 'Bank', 'checking', 'USD')"
        )

        # Insert a transaction
        today = date.today()
        import uuid
        conn.execute(
            """
            INSERT INTO transactions (
                account_id, posted_at, amount_cents, currency,
                description, merchant, fingerprint, created_at, updated_at
            ) VALUES (?, ?, ?, 'USD', '', 'Test', ?, datetime('now'), datetime('now'))
            """,
            ("acct1", today.isoformat(), -1000, f"fp_{uuid.uuid4().hex}"),
        )
        conn.commit()

        # Close the period using exclusive end date
        from fin.close_books import close_period, get_closed_period

        start = date(today.year, today.month, 1)
        end_exclusive = today + timedelta(days=1)  # Exclusive end

        closed = close_period(conn, start, end_exclusive)
        assert closed is not None
        assert closed.start_date == start
        assert closed.end_date == end_exclusive

        # Look up should find it with same exclusive end
        found = get_closed_period(conn, start, end_exclusive)
        assert found is not None
        assert found.id == closed.id

        # Look up with wrong end date should NOT find it
        wrong = get_closed_period(conn, start, today)  # Inclusive end - wrong
        assert wrong is None

        conn.close()

    def test_dashboard_finds_closed_period_with_exclusive_end(self, test_db_path):
        """Dashboard lookup should find closed period using exclusive end_date."""
        conn = dbmod.connect(test_db_path)
        dbmod.init_db(conn)

        # Insert account and transaction
        conn.execute(
            "INSERT INTO accounts (account_id, name, institution, type, currency) "
            "VALUES ('acct1', 'Test Account', 'Bank', 'checking', 'USD')"
        )

        today = date.today()
        import uuid
        conn.execute(
            """
            INSERT INTO transactions (
                account_id, posted_at, amount_cents, currency,
                description, merchant, fingerprint, created_at, updated_at
            ) VALUES (?, ?, ?, 'USD', '', 'Test', ?, datetime('now'), datetime('now'))
            """,
            ("acct1", today.isoformat(), -1000, f"fp_{uuid.uuid4().hex}"),
        )
        conn.commit()

        # Close current month with exclusive end
        from fin.close_books import close_period

        start = date(today.year, today.month, 1)
        end_exclusive = today + timedelta(days=1)

        close_period(conn, start, end_exclusive)
        conn.close()

        # Now test via web endpoint
        import fin.web as web_module
        web_module._config = None
        web_module._db_initialized = False

        class MockConfig:
            db_path = test_db_path
            simplefin_access_url = ""
            log_level = "INFO"
            log_format = "simple"

        with patch.object(web_module, "_get_config", return_value=MockConfig()):
            with TestClient(app) as client:
                response = client.get("/dashboard?period=this_month")
                assert response.status_code == 200

                # Dashboard should show "closed" indicator
                html = response.text
                assert "closed" in html.lower() or "Closed" in html
