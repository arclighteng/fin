"""
Tests for encrypted backup export functionality.

Tests:
- age CLI detection
- Passphrase mode validation
- Recipient mode validation
- Error handling for missing age
- Output file naming
- End-to-end encryption/decryption validation
"""
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from fin.cli import app
from fin import db as dbmod

runner = CliRunner()

# Check if age is installed for integration tests
AGE_AVAILABLE = shutil.which("age") is not None
AGE_KEYGEN_AVAILABLE = shutil.which("age-keygen") is not None


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

    def test_defaults_to_passphrase_mode_with_env_var(self, temp_db_path):
        """No flags needed — FIN_BACKUP_PASSWORD env var triggers passphrase mode by default."""
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path, "FIN_BACKUP_PASSWORD": "test-secret"}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0)
                    result = runner.invoke(app, ["export-backup"])

        assert result.exit_code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert "-p" in call_args

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

        fixed_date = dt_date(2026, 2, 28)
        with patch.dict("os.environ", {"FIN_DB_PATH": temp_db_path}):
            with patch("shutil.which", return_value="/usr/bin/age"):
                with patch("fin.cli.dates_mod.today", return_value=fixed_date):
                    with patch("subprocess.run") as mock_run:
                        mock_run.return_value = MagicMock(returncode=0)
                        result = runner.invoke(app, ["export-backup", "-p"])

        # Verify output filename contains date
        call_args = mock_run.call_args[0][0]
        output_arg = call_args[call_args.index("-o") + 1]
        assert "20260228" in output_arg
        assert output_arg.endswith(".finbak")


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


# ---------------------------------------------------------------------------
# End-to-end encryption tests (require age to be installed)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_with_data():
    """Create a database with test data for encryption tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name

    conn = dbmod.connect(path)
    dbmod.init_db(conn)

    # Insert test data
    conn.execute(
        """
        INSERT INTO accounts (account_id, institution, name, type, currency, last_seen_at)
        VALUES ('test-acct', 'Test Bank', 'Checking', 'checking', 'USD', datetime('now'))
        """
    )
    conn.execute(
        """
        INSERT INTO transactions (
            account_id, posted_at, amount_cents, currency,
            description, merchant, fingerprint, created_at, updated_at
        ) VALUES
        ('test-acct', '2026-01-15', -1599, 'USD', 'Netflix', 'NETFLIX', 'fp1', datetime('now'), datetime('now')),
        ('test-acct', '2026-01-16', -1099, 'USD', 'Spotify', 'SPOTIFY', 'fp2', datetime('now'), datetime('now')),
        ('test-acct', '2026-01-17', 250000, 'USD', 'Payroll', 'EMPLOYER', 'fp3', datetime('now'), datetime('now'))
        """
    )
    conn.commit()
    conn.close()

    yield path
    Path(path).unlink(missing_ok=True)


@pytest.mark.skipif(not AGE_AVAILABLE, reason="age CLI not installed")
class TestEncryptionIntegration:
    """End-to-end tests that validate actual encryption/decryption."""

    def test_encrypt_decrypt_with_recipient_key(self, db_with_data):
        """Should encrypt and decrypt successfully with recipient key."""
        if not AGE_KEYGEN_AVAILABLE:
            pytest.skip("age-keygen not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            key_file = tmpdir / "key.txt"
            encrypted_file = tmpdir / "backup.db.age"
            decrypted_file = tmpdir / "restored.db"

            # Generate a keypair
            keygen_result = subprocess.run(
                ["age-keygen", "-o", str(key_file)],
                capture_output=True,
                text=True,
            )
            assert keygen_result.returncode == 0, f"age-keygen failed: {keygen_result.stderr}"

            # Extract public key from keygen output
            public_key = None
            for line in keygen_result.stderr.split("\n"):
                if line.startswith("Public key:"):
                    public_key = line.split(":")[1].strip()
                    break

            assert public_key and public_key.startswith("age1"), "Failed to extract public key"

            # Encrypt the database using our CLI
            with patch.dict("os.environ", {"FIN_DB_PATH": db_with_data}):
                result = runner.invoke(app, [
                    "export-backup",
                    "-r", public_key,
                    "-o", str(encrypted_file),
                ])

            assert result.exit_code == 0, f"Encryption failed: {result.output}"
            assert encrypted_file.exists(), "Encrypted file not created"
            assert encrypted_file.stat().st_size > 0, "Encrypted file is empty"

            # Verify encrypted file is not plaintext (shouldn't contain SQLite header)
            with open(encrypted_file, "rb") as f:
                header = f.read(16)
            assert b"SQLite" not in header, "File doesn't appear to be encrypted"

            # Decrypt using age CLI directly
            decrypt_result = subprocess.run(
                ["age", "-d", "-i", str(key_file), "-o", str(decrypted_file), str(encrypted_file)],
                capture_output=True,
                text=True,
            )
            assert decrypt_result.returncode == 0, f"Decryption failed: {decrypt_result.stderr}"
            assert decrypted_file.exists(), "Decrypted file not created"

            # Verify decrypted database is valid and contains our data
            conn = dbmod.connect(str(decrypted_file))
            try:
                accounts = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
                transactions = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

                assert accounts == 1, f"Expected 1 account, got {accounts}"
                assert transactions == 3, f"Expected 3 transactions, got {transactions}"

                # Verify specific data
                netflix = conn.execute(
                    "SELECT amount_cents FROM transactions WHERE merchant = 'NETFLIX'"
                ).fetchone()
                assert netflix[0] == -1599, "Transaction data corrupted"
            finally:
                conn.close()

    def test_encrypted_file_differs_from_original(self, db_with_data):
        """Encrypted file should be different from original (not just renamed)."""
        if not AGE_KEYGEN_AVAILABLE:
            pytest.skip("age-keygen not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            key_file = tmpdir / "key.txt"
            encrypted_file = tmpdir / "backup.db.age"

            # Generate keypair
            subprocess.run(["age-keygen", "-o", str(key_file)], capture_output=True)

            # Get public key
            with open(key_file) as f:
                for line in f:
                    if line.startswith("# public key:"):
                        public_key = line.split(":")[1].strip()
                        break

            # Encrypt
            with patch.dict("os.environ", {"FIN_DB_PATH": db_with_data}):
                runner.invoke(app, ["export-backup", "-r", public_key, "-o", str(encrypted_file)])

            # Read both files
            with open(db_with_data, "rb") as f:
                original_content = f.read()
            with open(encrypted_file, "rb") as f:
                encrypted_content = f.read()

            # They should be different
            assert original_content != encrypted_content, "Encrypted file matches original"

            # Encrypted file should not contain plaintext markers
            assert b"SQLite format" not in encrypted_content
            assert b"NETFLIX" not in encrypted_content
            assert b"SPOTIFY" not in encrypted_content

    def test_wrong_key_fails_decryption(self, db_with_data):
        """Decryption should fail with wrong key."""
        if not AGE_KEYGEN_AVAILABLE:
            pytest.skip("age-keygen not installed")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            key1_file = tmpdir / "key1.txt"
            key2_file = tmpdir / "key2.txt"
            encrypted_file = tmpdir / "backup.db.age"
            decrypted_file = tmpdir / "restored.db"

            # Generate two different keypairs
            subprocess.run(["age-keygen", "-o", str(key1_file)], capture_output=True)
            subprocess.run(["age-keygen", "-o", str(key2_file)], capture_output=True)

            # Get public key from first keypair
            with open(key1_file) as f:
                for line in f:
                    if line.startswith("# public key:"):
                        public_key = line.split(":")[1].strip()
                        break

            # Encrypt with key1
            with patch.dict("os.environ", {"FIN_DB_PATH": db_with_data}):
                runner.invoke(app, ["export-backup", "-r", public_key, "-o", str(encrypted_file)])

            # Try to decrypt with key2 (should fail)
            decrypt_result = subprocess.run(
                ["age", "-d", "-i", str(key2_file), "-o", str(decrypted_file), str(encrypted_file)],
                capture_output=True,
                text=True,
            )

            assert decrypt_result.returncode != 0, "Decryption should fail with wrong key"
            assert not decrypted_file.exists() or decrypted_file.stat().st_size == 0
