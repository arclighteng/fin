"""
Tests for CLI security gates.

Covers:
- Item 3: startup_security_check — auth-disabled hard-block on non-loopback
- Item 5: FDE hard-block and bypass flags
- Item 6: export-backup encryption requirement
- Item 8: --no-tls hard-block on non-loopback

Design notes:
- Tests call startup_security_check() and _check_fde() directly where possible
  to avoid the full web-startup chain (TLS cert generation, uvicorn, etc.).
- Integration tests for the `web` CLI command patch uvicorn.run and tls.ensure_cert
  so the server never actually starts.
"""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from typer.testing import CliRunner

from fin.cli import app, startup_security_check, _check_fde

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    """Temporary, initialised database."""
    from fin import db as dbmod
    db_file = tmp_path / "test.db"
    conn = dbmod.connect(str(db_file))
    dbmod.init_db(conn)
    conn.close()
    return str(db_file)


# ---------------------------------------------------------------------------
# Item 3 — startup_security_check() function
# ---------------------------------------------------------------------------

class TestStartupSecurityCheck:
    """Direct unit tests for startup_security_check()."""

    # --- Hard-block cases ---

    def test_auth_disabled_nonloopback_0000_exits_1(self, capsys):
        with pytest.raises(SystemExit) as exc:
            startup_security_check("0.0.0.0", auth_disabled=True)
        assert exc.value.code == 1

    def test_auth_disabled_nonloopback_192168_exits_1(self, capsys):
        with pytest.raises(SystemExit) as exc:
            startup_security_check("192.168.1.1", auth_disabled=True)
        assert exc.value.code == 1

    def test_auth_disabled_nonloopback_error_message_mentions_context(self, capsys):
        """Error output must name the env var so users know how to fix it."""
        with pytest.raises(SystemExit):
            startup_security_check("0.0.0.0", auth_disabled=True)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # The message must reference auth or the env var
        assert (
            "FIN_AUTH_DISABLED" in combined
            or "auth" in combined.lower()
            or "authentication" in combined.lower()
        )

    # --- Loopback: warning, no exit ---

    def test_auth_disabled_loopback_127001_does_not_exit(self):
        # Must complete without raising SystemExit
        startup_security_check("127.0.0.1", auth_disabled=True)

    def test_auth_disabled_loopback_localhost_does_not_exit(self):
        startup_security_check("localhost", auth_disabled=True)

    def test_auth_disabled_ipv6_loopback_does_not_exit(self):
        startup_security_check("::1", auth_disabled=True)

    def test_loopback_auth_disabled_prints_warning(self, capsys):
        startup_security_check("127.0.0.1", auth_disabled=True)
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        # Must warn — either the word WARNING or "disabled"
        assert (
            "WARNING" in combined.upper()
            or "warning" in combined.lower()
            or "disabled" in combined.lower()
        )

    # --- Auth enabled: always safe regardless of host ---

    def test_auth_enabled_nonloopback_does_not_exit(self):
        startup_security_check("0.0.0.0", auth_disabled=False)

    def test_auth_enabled_loopback_does_not_exit(self):
        startup_security_check("127.0.0.1", auth_disabled=False)


# ---------------------------------------------------------------------------
# Item 8 — --no-tls hard-block via CLI
# ---------------------------------------------------------------------------

class TestNoTlsHardBlock:
    """The `web` command must refuse --no-tls on a non-loopback host."""

    def _base_env(self, db_path):
        return {"FIN_DB_PATH": db_path}

    def test_no_tls_on_nonloopback_exits_1(self, db_path):
        """--no-tls with --host 0.0.0.0 must exit with code 1."""
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            # Patch startup_security_check so Item 3 doesn't fire first
            with patch("fin.cli.startup_security_check"):
                with patch("fin.cli._check_fde", return_value=(True, "OK")):
                    result = runner.invoke(app, [
                        "web",
                        "--host", "0.0.0.0",
                        "--no-tls",
                        "--port", "19999",
                    ])
        assert result.exit_code == 1

    def test_no_tls_on_nonloopback_error_message(self, db_path):
        """Error message should mention --no-tls or TLS or loopback."""
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            with patch("fin.cli.startup_security_check"):
                with patch("fin.cli._check_fde", return_value=(True, "OK")):
                    result = runner.invoke(app, [
                        "web",
                        "--host", "0.0.0.0",
                        "--no-tls",
                        "--port", "19999",
                    ])
        combined = result.output + (result.stderr or "")
        assert (
            "--no-tls" in combined
            or "tls" in combined.lower()
            or "loopback" in combined.lower()
            or "non-loopback" in combined.lower()
        )

    def test_no_tls_loopback_warns_and_proceeds(self, db_path):
        """--no-tls on 127.0.0.1 is allowed with a warning (uvicorn.run is patched)."""
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            with patch("fin.cli._check_fde", return_value=(True, "OK")):
                with patch("fin.cli.startup_security_check"):
                    with patch("uvicorn.run"):
                        result = runner.invoke(app, [
                            "web",
                            "--host", "127.0.0.1",
                            "--no-tls",
                            "--port", "19999",
                        ])
        assert result.exit_code == 0
        combined = result.output + (result.stderr or "")
        # Should mention HTTP or warning
        assert (
            "WARNING" in combined.upper()
            or "http" in combined.lower()
            or "warning" in combined.lower()
        )


# ---------------------------------------------------------------------------
# Item 5 — FDE hard-block
# ---------------------------------------------------------------------------

class TestFdeHardBlock:
    """FDE off without bypass → exit 1; bypass flags allow startup."""

    def _base_env(self, db_path):
        return {"FIN_DB_PATH": db_path}

    def test_fde_off_exits_1_without_bypass(self, db_path):
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            os.environ.pop("FIN_SKIP_FDE_CHECK", None)
            with patch("fin.cli._check_fde", return_value=(False, "FileVault is Off")):
                with patch("fin.cli.startup_security_check"):
                    result = runner.invoke(app, [
                        "web",
                        "--host", "127.0.0.1",
                        "--no-tls",
                        "--port", "19999",
                    ])
        assert result.exit_code == 1

    def test_fde_off_error_mentions_encryption(self, db_path):
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            os.environ.pop("FIN_SKIP_FDE_CHECK", None)
            with patch("fin.cli._check_fde", return_value=(False, "FileVault is Off")):
                with patch("fin.cli.startup_security_check"):
                    result = runner.invoke(app, [
                        "web",
                        "--host", "127.0.0.1",
                        "--no-tls",
                        "--port", "19999",
                    ])
        combined = result.output + (result.stderr or "")
        assert (
            "encrypt" in combined.lower()
            or "filevault" in combined.lower()
            or "bitlocker" in combined.lower()
            or "fde" in combined.lower()
        )

    def test_fde_off_with_cli_flag_bypass_exits_0(self, db_path):
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            os.environ.pop("FIN_SKIP_FDE_CHECK", None)
            with patch("fin.cli._check_fde", return_value=(False, "FileVault is Off")):
                with patch("fin.cli.startup_security_check"):
                    with patch("uvicorn.run"):
                        result = runner.invoke(app, [
                            "web",
                            "--host", "127.0.0.1",
                            "--no-tls",
                            "--i-understand-no-fde",
                            "--port", "19999",
                        ])
        assert result.exit_code == 0

    def test_fde_off_with_env_var_bypass_exits_0(self, db_path):
        env = {**self._base_env(db_path), "FIN_SKIP_FDE_CHECK": "1"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            with patch("fin.cli._check_fde", return_value=(False, "FileVault is Off")):
                with patch("fin.cli.startup_security_check"):
                    with patch("uvicorn.run"):
                        result = runner.invoke(app, [
                            "web",
                            "--host", "127.0.0.1",
                            "--no-tls",
                            "--port", "19999",
                        ])
        assert result.exit_code == 0

    def test_fde_unknown_shows_info_and_continues(self, db_path):
        """When FDE status cannot be determined, startup continues with an info message."""
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            with patch("fin.cli._check_fde", return_value=(None, "Cannot determine")):
                with patch("fin.cli.startup_security_check"):
                    with patch("uvicorn.run"):
                        result = runner.invoke(app, [
                            "web",
                            "--host", "127.0.0.1",
                            "--no-tls",
                            "--port", "19999",
                        ])
        assert result.exit_code == 0

    def test_fde_enabled_does_not_block_startup(self, db_path):
        """When FDE is confirmed on, startup is not blocked."""
        with patch.dict(os.environ, self._base_env(db_path), clear=False):
            os.environ.pop("FIN_AUTH_DISABLED", None)
            with patch("fin.cli._check_fde", return_value=(True, "FileVault is enabled")):
                with patch("fin.cli.startup_security_check"):
                    with patch("uvicorn.run"):
                        result = runner.invoke(app, [
                            "web",
                            "--host", "127.0.0.1",
                            "--no-tls",
                            "--port", "19999",
                        ])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Item 6 — export-backup encryption requirement
# ---------------------------------------------------------------------------

class TestExportBackup:
    """export-backup must warn on --no-encrypt and require encryption by default."""

    def test_no_encrypt_exits_0(self, db_path, tmp_path):
        """--no-encrypt is allowed but must warn; exit code is 0."""
        output = tmp_path / "backup.sqlite"
        with patch.dict(os.environ, {"FIN_DB_PATH": db_path}, clear=False):
            result = runner.invoke(app, [
                "export-backup",
                "--no-encrypt",
                "--output", str(output),
            ])
        assert result.exit_code == 0

    def test_no_encrypt_prints_warning(self, db_path, tmp_path):
        """--no-encrypt must print a visible warning."""
        output = tmp_path / "backup.sqlite"
        with patch.dict(os.environ, {"FIN_DB_PATH": db_path}, clear=False):
            result = runner.invoke(app, [
                "export-backup",
                "--no-encrypt",
                "--output", str(output),
            ])
        combined = result.output + (result.stderr or "")
        assert (
            "WARNING" in combined.upper()
            or "unencrypted" in combined.lower()
            or "warning" in combined.lower()
        )

    def test_no_encrypt_creates_output_file(self, db_path, tmp_path):
        """--no-encrypt must actually copy the database to the output path."""
        output = tmp_path / "backup.sqlite"
        with patch.dict(os.environ, {"FIN_DB_PATH": db_path}, clear=False):
            runner.invoke(app, [
                "export-backup",
                "--no-encrypt",
                "--output", str(output),
            ])
        assert output.exists()

    def test_passphrase_and_recipient_flags_mutually_exclusive(self, db_path):
        """Providing both --passphrase and --recipient must fail with exit code 1."""
        with patch.dict(os.environ, {"FIN_DB_PATH": db_path}, clear=False):
            with patch("shutil.which", return_value="/usr/bin/age"):
                result = runner.invoke(app, [
                    "export-backup",
                    "--passphrase",
                    "--recipient", "age1abc123",
                ])
        assert result.exit_code == 1

    def test_env_var_password_triggers_passphrase_mode(self, db_path, tmp_path):
        """FIN_BACKUP_PASSWORD env var should be used to encrypt without interactive prompt."""
        output = tmp_path / "backup.finbak"
        env = {
            "FIN_DB_PATH": db_path,
            "FIN_BACKUP_PASSWORD": "secret-pass",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch("shutil.which", return_value="/usr/bin/age"):
                mock_run = MagicMock(return_value=MagicMock(returncode=0))
                with patch("subprocess.run", mock_run):
                    result = runner.invoke(app, [
                        "export-backup",
                        "--output", str(output),
                    ])
        assert result.exit_code == 0
        # Verify age was called with passphrase flag
        assert mock_run.called
        call_args = mock_run.call_args[0][0]
        assert "-p" in call_args

    def test_no_flags_no_env_var_no_silent_plaintext(self, db_path):
        """Without a password or --no-encrypt, the command must not silently create plaintext."""
        with patch.dict(os.environ, {"FIN_DB_PATH": db_path}, clear=False):
            os.environ.pop("FIN_BACKUP_PASSWORD", None)
            with patch("shutil.which", return_value="/usr/bin/age"):
                # Patch subprocess.run so it doesn't actually run age
                # but do not patch out the passphrase-prompt path
                mock_run = MagicMock(return_value=MagicMock(returncode=0))
                with patch("subprocess.run", mock_run):
                    result = runner.invoke(app, [
                        "export-backup",
                        "--output", "/dev/null",
                    ])
        # If exit 0, output must mention passphrase / password / encrypt
        if result.exit_code == 0:
            combined = result.output + (result.stderr or "")
            assert (
                "passphrase" in combined.lower()
                or "password" in combined.lower()
                or "encrypt" in combined.lower()
            )

    def test_age_not_installed_exits_1(self, db_path):
        """If the age CLI is not found, the command must exit with code 1."""
        with patch.dict(os.environ, {"FIN_DB_PATH": db_path}, clear=False):
            with patch("shutil.which", return_value=None):
                result = runner.invoke(app, ["export-backup", "--passphrase"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Task 3 — _build_browser_open_url helper
# ---------------------------------------------------------------------------

class TestBuildBrowserOpenUrl:
    """Tests for the _build_browser_open_url helper."""

    def test_returns_auto_login_url_when_token_present(self):
        from fin.cli import _build_browser_open_url
        url = _build_browser_open_url("https", 8000, "my-token", False)
        assert url == "https://127.0.0.1:8000/auto-login?t=my-token"

    def test_returns_dashboard_url_when_auth_disabled(self):
        from fin.cli import _build_browser_open_url
        url = _build_browser_open_url("https", 8000, "my-token", True)
        assert url == "https://127.0.0.1:8000/dashboard"

    def test_returns_dashboard_url_when_no_token(self):
        from fin.cli import _build_browser_open_url
        url = _build_browser_open_url("http", 8000, None, False)
        assert url == "http://127.0.0.1:8000/dashboard"

    def test_includes_token_in_query_param(self):
        from fin.cli import _build_browser_open_url
        url = _build_browser_open_url("https", 9000, "abc-xyz-123", False)
        assert "?t=abc-xyz-123" in url
        assert "9000" in url
