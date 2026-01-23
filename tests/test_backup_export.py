"""
Tests for encrypted backup export functionality.

Tests:
- age CLI detection
- Passphrase mode validation
- Recipient mode validation
- Error handling for missing age
- Output file naming
"""
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

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


class TestAgeDetection:
    """Test age CLI detection."""

    def test_error_when_age_not_installed(self, temp_db_path):
        """Should error when age is not found."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value=None):
                result = runner.invoke(app, ["export-backup", "-p"])

        assert result.exit_code == 1
        assert "age" in result.output.lower()
        assert "not found" in result.output.lower()

    def test_shows_install_instructions(self, temp_db_path):
        """Should show install instructions when age not found."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value=None):
                result = runner.invoke(app, ["export-backup", "-p"])

        assert "winget" in result.output or "brew" in result.output
        assert "install" in result.output.lower()


class TestModeValidation:
    """Test encryption mode validation."""

    def test_requires_passphrase_or_recipient(self, temp_db_path):
        """Should require either -p or -r flag."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                result = runner.invoke(app, ["export-backup"])

        assert result.exit_code == 1
        assert "Must specify" in result.output
        assert "--passphrase" in result.output or "-p" in result.output

    def test_cannot_use_both_modes(self, temp_db_path):
        """Should reject both -p and -r together."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                result = runner.invoke(app, [
                    "export-backup",
                    "-p",
                    "-r", "age1abc123",
                ])

        assert result.exit_code == 1
        assert "Cannot use both" in result.output


class TestDatabaseValidation:
    """Test database file validation."""

    def test_error_when_db_not_found(self):
        """Should error when database doesn't exist."""
        with patch.dict("os.environ", {"FIN_DB_PATH": "/nonexistent/path/fin.db"}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                result = runner.invoke(app, ["export-backup", "-p"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestBackupExecution:
    """Test backup execution (mocked)."""

    def test_passphrase_mode_calls_age_correctly(self, temp_db_path):
        """Should call age with -p flag for passphrase mode."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = runner.invoke(app, ["export-backup", "-p"])

        # Verify age was called with -p flag
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "-p" in call_args
        assert "-o" in call_args

    def test_recipient_mode_calls_age_correctly(self, temp_db_path):
        """Should call age with -r flag for recipient mode."""
        recipient_key = "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"

        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = runner.invoke(app, ["export-backup", "-r", recipient_key])

        # Verify age was called with -r flag and recipient
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "-r" in call_args
        assert recipient_key in call_args

    def test_custom_output_path(self, temp_db_path):
        """Should use custom output path when specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "custom_backup.db.age"

            with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
                with patch("shutil.which", return_value="/usr/bin/age"):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        result = runner.invoke(app, [
                            "export-backup", "-p",
                            "-o", str(output_path),
                        ])

            # Verify custom output path was used
            call_args = mock_run.call_args[0][0]
            assert str(output_path) in call_args

    def test_default_output_filename(self, temp_db_path):
        """Should generate default filename with date."""
        from datetime import date as dt_date

        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = runner.invoke(app, ["export-backup", "-p"])

        # Verify output filename contains date
        call_args = mock_run.call_args[0][0]
        today = dt_date.today().strftime("%Y%m%d")
        output_arg = call_args[call_args.index("-o") + 1]
        assert today in output_arg
        assert output_arg.endswith(".db.age")


class TestBackupErrorHandling:
    """Test error handling during backup."""

    def test_handles_age_failure(self, temp_db_path):
        """Should handle age command failure gracefully."""
        import subprocess

        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("subprocess.run") as mock_run:
                    mock_run.side_effect = subprocess.CalledProcessError(1, "age")
                    result = runner.invoke(app, ["export-backup", "-p"])

        assert result.exit_code == 1
        assert "failed" in result.output.lower()


class TestBackupSuccessMessages:
    """Test success messages and instructions."""

    def test_shows_decrypt_instructions_passphrase(self, temp_db_path):
        """Should show decryption instructions for passphrase mode."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = runner.invoke(app, ["export-backup", "-p"])

        assert "Backup complete" in result.output
        assert "age -d" in result.output

    def test_shows_decrypt_instructions_recipient(self, temp_db_path):
        """Should show decryption instructions for recipient mode."""
        recipient_key = "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p"

        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = runner.invoke(app, ["export-backup", "-r", recipient_key])

        assert "Backup complete" in result.output
        assert "age -d" in result.output
        assert "-i" in result.output  # Should mention key file for decryption
