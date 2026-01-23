"""
Integration tests for fin CLI commands.

Tests the CLI commands that use the new analysis and classification features:
- export-sketchy
- export-duplicates
- export-summary
- dashboard-cli
- export-csv (enhanced)
"""
import csv
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin import db as dbmod


runner = CliRunner()


@pytest.fixture
def temp_exports_dir():
    """Create a temporary directory for exports."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_config(temp_db_path):
    """Mock configuration to use temp database."""
    class MockConfig:
        db_path = temp_db_path
        simplefin_access_url = ""
        log_level = "INFO"
        log_format = "simple"

    with patch("fin.cli.load_config", return_value=MockConfig()):
        yield MockConfig()


@pytest.fixture
def populated_cli_db(temp_db_path):
    """Create a populated database for CLI testing."""
    conn = dbmod.connect(temp_db_path)
    dbmod.init_db(conn)

    today = date.today()

    def insert_txn(posted_at, amount_cents, merchant):
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
                f"fp_{posted_at}_{merchant}_{amount_cents}_{id(posted_at)}",
            ),
        )

    # Income
    for weeks_ago in range(12):
        d = today - timedelta(weeks=weeks_ago * 2)
        insert_txn(d, 250000, "EMPLOYER PAYROLL")

    # Monthly subscriptions
    for months_ago in range(6):
        d = today - timedelta(days=months_ago * 30)
        insert_txn(d, -1599, "NETFLIX")
        insert_txn(d + timedelta(days=1), -1099, "SPOTIFY")

    # Sketchy charges
    insert_txn(today - timedelta(days=5), -2999, "SKETCHY")
    insert_txn(today - timedelta(days=3), -2999, "SKETCHY")  # Duplicate
    insert_txn(today - timedelta(days=10), -50, "TEST VENDOR")  # Test charge

    # Duplicate subscriptions (same name variants)
    for months_ago in range(4):
        d = today - timedelta(days=months_ago * 30 + 5)
        insert_txn(d, -1599, "NETFLIX.COM")

    conn.commit()
    conn.close()

    return temp_db_path


class TestExportSketchyCommand:
    """Test the export-sketchy CLI command."""

    def test_export_sketchy_creates_csv(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should create sketchy_charges.csv file."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, [
            "export-sketchy",
            "--out", temp_exports_dir,
            "--days", "60",
        ])

        assert result.exit_code == 0, f"Command failed: {result.output}"
        csv_path = Path(temp_exports_dir) / "sketchy_charges.csv"
        assert csv_path.exists(), "CSV file not created"

    def test_export_sketchy_csv_has_correct_columns(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """CSV should have correct column headers."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-sketchy",
            "--out", temp_exports_dir,
        ])

        csv_path = Path(temp_exports_dir) / "sketchy_charges.csv"
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            headers = next(reader)

        expected = ["posted_at", "merchant", "amount_usd", "pattern_type", "severity", "detail"]
        assert headers == expected

    def test_export_sketchy_detects_patterns(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should detect sketchy patterns in the data."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-sketchy",
            "--out", temp_exports_dir,
        ])

        csv_path = Path(temp_exports_dir) / "sketchy_charges.csv"
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Should have detected some patterns
        assert len(rows) >= 1
        pattern_types = {r["pattern_type"] for r in rows}
        # Should have detected duplicate or test charge
        assert len(pattern_types) >= 1


class TestExportDuplicatesCommand:
    """Test the export-duplicates CLI command."""

    def test_export_duplicates_creates_csv(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should create duplicates.csv file."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, [
            "export-duplicates",
            "--out", temp_exports_dir,
        ])

        assert result.exit_code == 0
        csv_path = Path(temp_exports_dir) / "duplicates.csv"
        assert csv_path.exists()

    def test_export_duplicates_csv_has_correct_columns(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """CSV should have correct column headers."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-duplicates",
            "--out", temp_exports_dir,
        ])

        csv_path = Path(temp_exports_dir) / "duplicates.csv"
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            headers = next(reader)

        expected = ["group_type", "merchants", "monthly_total_usd", "severity", "detail"]
        assert headers == expected


class TestExportSummaryCommand:
    """Test the export-summary CLI command."""

    def test_export_summary_creates_csv(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should create summary CSV file."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, [
            "export-summary",
            "--out", temp_exports_dir,
            "--period", "month",
        ])

        assert result.exit_code == 0
        csv_path = Path(temp_exports_dir) / "month_summary.csv"
        assert csv_path.exists()

    def test_export_summary_quarter_creates_csv(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should create quarterly summary CSV."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, [
            "export-summary",
            "--out", temp_exports_dir,
            "--period", "quarter",
        ])

        assert result.exit_code == 0
        csv_path = Path(temp_exports_dir) / "quarter_summary.csv"
        assert csv_path.exists()

    def test_export_summary_has_correct_columns(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Summary CSV should have correct columns."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-summary",
            "--out", temp_exports_dir,
            "--period", "month",
        ])

        csv_path = Path(temp_exports_dir) / "month_summary.csv"
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            headers = next(reader)

        assert "period" in headers
        assert "income_usd" in headers
        assert "recurring_usd" in headers
        assert "discretionary_usd" in headers
        assert "net_usd" in headers
        assert "avg_income_usd" in headers
        assert "income_trend" in headers

    def test_export_summary_num_periods(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should export requested number of periods."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-summary",
            "--out", temp_exports_dir,
            "--period", "month",
            "--num-periods", "6",
        ])

        csv_path = Path(temp_exports_dir) / "month_summary.csv"
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 6


class TestDashboardCliCommand:
    """Test the dashboard-cli command."""

    def test_dashboard_cli_runs(self, populated_cli_db, mock_config):
        """Dashboard CLI should run without errors."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, ["dashboard-cli"])

        assert result.exit_code == 0

    def test_dashboard_cli_shows_financial_health(
        self, populated_cli_db, mock_config
    ):
        """Should display financial health information."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, ["dashboard-cli"])

        assert "FINANCIAL HEALTH" in result.output
        assert "Income" in result.output
        assert "Recurring" in result.output
        assert "Net" in result.output

    def test_dashboard_cli_period_option(
        self, populated_cli_db, mock_config
    ):
        """Should accept period option."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, ["dashboard-cli", "--period", "quarter"])

        assert result.exit_code == 0
        assert "Q" in result.output  # Quarter label


class TestExportCsvEnhanced:
    """Test that export-csv includes new files."""

    def test_export_csv_creates_all_files(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should create all CSV files including new ones."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, [
            "export-csv",
            "--out", temp_exports_dir,
        ])

        assert result.exit_code == 0

        # Check original files
        assert (Path(temp_exports_dir) / "transactions.csv").exists()
        assert (Path(temp_exports_dir) / "daily_rollup.csv").exists()
        assert (Path(temp_exports_dir) / "weekly_rollup.csv").exists()
        assert (Path(temp_exports_dir) / "monthly_rollup.csv").exists()
        assert (Path(temp_exports_dir) / "subscription_candidates.csv").exists()
        assert (Path(temp_exports_dir) / "actions.csv").exists()

        # Check new files
        assert (Path(temp_exports_dir) / "sketchy_charges.csv").exists()
        assert (Path(temp_exports_dir) / "duplicates.csv").exists()
        assert (Path(temp_exports_dir) / "monthly_summary.csv").exists()

    def test_export_csv_success_message(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Should show success message with all file names."""
        mock_config.db_path = populated_cli_db

        result = runner.invoke(app, [
            "export-csv",
            "--out", temp_exports_dir,
        ])

        assert "export complete" in result.output
        assert "sketchy_charges.csv" in result.output
        assert "duplicates.csv" in result.output
        assert "monthly_summary.csv" in result.output


class TestCSVDataIntegrity:
    """Test that exported CSV data is accurate."""

    def test_amounts_format_correctly(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """USD amounts should be formatted correctly."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-summary",
            "--out", temp_exports_dir,
        ])

        csv_path = Path(temp_exports_dir) / "month_summary.csv"
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Check amount format (should be like "1234.56")
                income = row["income_usd"]
                assert "." in income, f"Amount should have decimal: {income}"
                parts = income.split(".")
                assert len(parts[1]) == 2, f"Should have 2 decimal places: {income}"

    def test_dates_format_correctly(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Dates should be in ISO format."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-sketchy",
            "--out", temp_exports_dir,
        ])

        csv_path = Path(temp_exports_dir) / "sketchy_charges.csv"
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                posted_at = row["posted_at"]
                # Should be ISO format YYYY-MM-DD
                assert len(posted_at) == 10
                assert posted_at[4] == "-"
                assert posted_at[7] == "-"

    def test_no_empty_required_fields(
        self, populated_cli_db, temp_exports_dir, mock_config
    ):
        """Required fields should not be empty."""
        mock_config.db_path = populated_cli_db

        runner.invoke(app, [
            "export-sketchy",
            "--out", temp_exports_dir,
        ])

        csv_path = Path(temp_exports_dir) / "sketchy_charges.csv"
        with open(csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                assert row["posted_at"], "posted_at should not be empty"
                assert row["merchant"], "merchant should not be empty"
                assert row["amount_usd"], "amount_usd should not be empty"
                assert row["pattern_type"], "pattern_type should not be empty"
                assert row["severity"], "severity should not be empty"


class TestEmptyDatabase:
    """Test CLI behavior with empty database."""

    def test_export_sketchy_empty_db(self, empty_db, temp_exports_dir):
        """Should handle empty database gracefully."""
        with patch("fin.cli.load_config") as mock_load:
            class MockConfig:
                db_path = ":memory:"
                log_level = "INFO"
                log_format = "simple"

            mock_load.return_value = MockConfig()

            # Create actual empty db file
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
                temp_db = f.name

            conn = dbmod.connect(temp_db)
            dbmod.init_db(conn)
            conn.close()

            MockConfig.db_path = temp_db

            result = runner.invoke(app, [
                "export-sketchy",
                "--out", temp_exports_dir,
            ])

            # Should complete without error
            assert result.exit_code == 0

            # CSV should exist but have only headers
            csv_path = Path(temp_exports_dir) / "sketchy_charges.csv"
            assert csv_path.exists()

            Path(temp_db).unlink(missing_ok=True)

    def test_dashboard_cli_empty_db(self, temp_db_path):
        """Dashboard should handle empty database gracefully."""
        conn = dbmod.connect(temp_db_path)
        dbmod.init_db(conn)
        conn.close()

        with patch("fin.cli.load_config") as mock_load:
            class MockConfig:
                db_path = temp_db_path
                log_level = "INFO"
                log_format = "simple"

            mock_load.return_value = MockConfig()

            result = runner.invoke(app, ["dashboard-cli"])

            # Should complete (may show "no data" message)
            assert result.exit_code == 0
