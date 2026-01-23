"""
Tests for CSV import functionality.

Tests:
- Basic CSV import with standard columns
- Custom column name handling
- Date format parsing
- Duplicate detection via fingerprint
- Error handling for malformed data
- Dry run mode
"""
import csv
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
def temp_db_path():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = dbmod.connect(path)
    dbmod.init_db(conn)
    conn.close()
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def sample_csv_file():
    """Create a sample CSV file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description"])
        writer.writerow(["2026-01-15", "-15.99", "NETFLIX.COM"])
        writer.writerow(["2026-01-16", "-10.99", "SPOTIFY"])
        writer.writerow(["2026-01-17", "2500.00", "EMPLOYER PAYROLL"])
        writer.writerow(["2026-01-18", "-45.50", "GROCERY STORE"])
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def custom_columns_csv():
    """Create a CSV with custom column names."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Posted Date", "Amount", "Description", "Memo"])
        writer.writerow(["01/15/2026", "-25.00", "AMAZON", "Prime membership"])
        writer.writerow(["01/16/2026", "-100.00", "UTILITIES", "Electric bill"])
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def accounting_format_csv():
    """Create a CSV with accounting format (parentheses for negatives)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "amount", "description"])
        writer.writerow(["2026-01-15", "(50.00)", "EXPENSE ONE"])
        writer.writerow(["2026-01-16", "$1,234.56", "INCOME"])
        writer.writerow(["2026-01-17", "($99.99)", "EXPENSE TWO"])
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


class TestBasicCSVImport:
    """Test basic CSV import functionality."""

    def test_import_csv_dry_run(self, temp_db_path, sample_csv_file):
        """Dry run should preview without importing."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            result = runner.invoke(app, ["import-csv", sample_csv_file, "--dry-run"])

        assert result.exit_code == 0
        assert "Import Summary" in result.output
        assert "Dry run" in result.output
        assert "Transactions: 4" in result.output

        # Verify nothing was imported
        conn = dbmod.connect(temp_db_path)
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        assert count == 0

    def test_import_csv_inserts_transactions(self, temp_db_path, sample_csv_file):
        """Should insert transactions into database."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            result = runner.invoke(app, ["import-csv", sample_csv_file])

        assert result.exit_code == 0
        assert "Import complete" in result.output
        assert "Inserted: 4" in result.output

        # Verify transactions were imported
        conn = dbmod.connect(temp_db_path)
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        assert count == 4

    def test_import_csv_correct_amounts(self, temp_db_path, sample_csv_file):
        """Should parse amounts correctly as cents."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            runner.invoke(app, ["import-csv", sample_csv_file])

        conn = dbmod.connect(temp_db_path)
        rows = conn.execute(
            "SELECT merchant, amount_cents FROM transactions ORDER BY posted_at"
        ).fetchall()
        conn.close()

        # Netflix: -15.99 = -1599 cents
        netflix = next(r for r in rows if "NETFLIX" in r["merchant"])
        assert netflix["amount_cents"] == -1599

        # Employer payroll: 2500.00 = 250000 cents
        income = next(r for r in rows if "EMPLOYER" in r["merchant"])
        assert income["amount_cents"] == 250000


class TestCustomColumnNames:
    """Test custom column name handling."""

    def test_custom_date_column(self, temp_db_path, custom_columns_csv):
        """Should handle custom date column name."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            result = runner.invoke(app, [
                "import-csv", custom_columns_csv,
                "--date-col", "Posted Date",
                "--amount-col", "Amount",
                "--description-col", "Description",
                "--date-format", "%m/%d/%Y",
            ])

        assert result.exit_code == 0
        assert "Inserted: 2" in result.output

    def test_missing_column_error(self, temp_db_path, sample_csv_file):
        """Should error on missing required column."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            result = runner.invoke(app, [
                "import-csv", sample_csv_file,
                "--date-col", "nonexistent_column",
            ])

        assert result.exit_code == 1
        assert "Missing columns" in result.output


class TestDateFormatParsing:
    """Test various date format handling."""

    def test_auto_detect_date_format(self, temp_db_path, custom_columns_csv):
        """Should auto-detect common date formats."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            result = runner.invoke(app, [
                "import-csv", custom_columns_csv,
                "--date-col", "Posted Date",
                "--amount-col", "Amount",
                "--description-col", "Description",
            ])

        # Should succeed by auto-detecting MM/DD/YYYY format
        assert result.exit_code == 0


class TestAccountingFormat:
    """Test accounting format handling."""

    def test_parentheses_negative_amounts(self, temp_db_path, accounting_format_csv):
        """Should parse parentheses as negative amounts."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            result = runner.invoke(app, ["import-csv", accounting_format_csv])

        assert result.exit_code == 0

        conn = dbmod.connect(temp_db_path)
        rows = conn.execute(
            "SELECT description, amount_cents FROM transactions ORDER BY posted_at"
        ).fetchall()
        conn.close()

        # (50.00) should be -5000 cents
        expense1 = next(r for r in rows if "EXPENSE ONE" in r["description"])
        assert expense1["amount_cents"] == -5000

        # $1,234.56 should be 123456 cents
        income = next(r for r in rows if "INCOME" in r["description"])
        assert income["amount_cents"] == 123456

        # ($99.99) should be -9999 cents
        expense2 = next(r for r in rows if "EXPENSE TWO" in r["description"])
        assert expense2["amount_cents"] == -9999


class TestDuplicateHandling:
    """Test duplicate detection via fingerprints."""

    def test_skip_duplicate_transactions(self, temp_db_path, sample_csv_file):
        """Should skip duplicate transactions on reimport."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            # First import
            result1 = runner.invoke(app, ["import-csv", sample_csv_file])
            assert result1.exit_code == 0
            assert "Inserted: 4" in result1.output

            # Second import of same file
            result2 = runner.invoke(app, ["import-csv", sample_csv_file])
            assert result2.exit_code == 0
            assert "Inserted: 0" in result2.output
            assert "Skipped (duplicates): 4" in result2.output

        # Verify only 4 transactions exist
        conn = dbmod.connect(temp_db_path)
        count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        conn.close()
        assert count == 4


class TestErrorHandling:
    """Test error handling for malformed data."""

    def test_file_not_found(self, temp_db_path):
        """Should error on missing file."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            result = runner.invoke(app, ["import-csv", "nonexistent_file.csv"])

        assert result.exit_code == 1
        assert "File not found" in result.output

    def test_empty_csv_file(self, temp_db_path):
        """Should handle empty CSV gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write("")
            empty_path = f.name

        try:
            with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
                result = runner.invoke(app, ["import-csv", empty_path])

            assert result.exit_code == 1
            assert "empty" in result.output.lower()
        finally:
            Path(empty_path).unlink(missing_ok=True)

    def test_malformed_rows_reported(self, temp_db_path):
        """Should report parse errors but continue."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "amount", "description"])
            writer.writerow(["2026-01-15", "-15.99", "VALID ROW"])
            writer.writerow(["invalid-date", "-10.00", "INVALID DATE"])
            writer.writerow(["2026-01-17", "not-a-number", "INVALID AMOUNT"])
            writer.writerow(["2026-01-18", "-20.00", "ANOTHER VALID"])
            path = f.name

        try:
            with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
                result = runner.invoke(app, ["import-csv", path])

            # Should succeed but report errors
            assert "Parse errors" in result.output
            assert "Inserted: 2" in result.output  # Only valid rows
        finally:
            Path(path).unlink(missing_ok=True)


class TestAccountIdAssignment:
    """Test account ID handling."""

    def test_default_account_id(self, temp_db_path, sample_csv_file):
        """Should use default account ID 'manual-import'."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            runner.invoke(app, ["import-csv", sample_csv_file])

        conn = dbmod.connect(temp_db_path)
        row = conn.execute(
            "SELECT DISTINCT account_id FROM transactions"
        ).fetchone()
        conn.close()

        assert row["account_id"] == "manual-import"

    def test_custom_account_id(self, temp_db_path, sample_csv_file):
        """Should use custom account ID when specified."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            runner.invoke(app, [
                "import-csv", sample_csv_file,
                "--account-id", "chase-checking",
            ])

        conn = dbmod.connect(temp_db_path)
        row = conn.execute(
            "SELECT DISTINCT account_id FROM transactions"
        ).fetchone()
        conn.close()

        assert row["account_id"] == "chase-checking"
