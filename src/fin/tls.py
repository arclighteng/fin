# tls.py
"""
Self-signed TLS certificate generation for local HTTPS.

Uses the `cryptography` library (already a dependency) to generate certs —
no openssl binary required. Falls back to openssl subprocess if the library
is somehow unavailable.

Certs are stored next to the database in a 'certs' subdirectory and reused
across restarts. A new cert is generated only when none exists.
"""
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _generate_with_cryptography(cert_path: Path, key_path: Path) -> bool:
    """Generate a self-signed cert using the cryptography library. Returns True on success."""
    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        # Generate RSA private key
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        # Build certificate
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "kept-local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Kept"),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        ])

        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=825))  # ~2 years, browser max
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    x509.IPAddress(ipaddress.IPv6Address("::1")),
                ]),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .sign(key, hashes.SHA256())
        )

        # Write private key
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )

        # Write certificate
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        # Harden key permissions on Unix
        if sys.platform != "win32":
            import os
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass

        return True

    except Exception as exc:
        log.warning("cryptography-based cert generation failed: %s", exc)
        return False


def _generate_with_openssl(cert_path: Path, key_path: Path) -> bool:
    """Fallback: generate cert via openssl subprocess. Returns True on success."""
    import subprocess
    try:
        subprocess.run(
            [
                "openssl", "req",
                "-x509",
                "-newkey", "rsa:2048",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-days", "825",
                "-nodes",
                "-subj", "/CN=kept-local/O=Kept/C=US",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        if sys.platform != "win32":
            import os
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass

        return True

    except FileNotFoundError:
        log.warning("openssl not found and cryptography library unavailable — falling back to HTTP")
        return False
    except subprocess.CalledProcessError as exc:
        log.warning("openssl cert generation failed: %s", exc.stderr.strip())
        return False


def _cert_already_trusted_windows(cert_path: Path) -> bool:
    """Check if the cert's thumbprint is already in the CurrentUser\\Root store."""
    import subprocess
    try:
        tp_result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-PfxCertificate -FilePath '{cert_path}').Thumbprint",
            ],
            capture_output=True, text=True,
        )
        thumbprint = tp_result.stdout.strip()
        if not thumbprint:
            return False

        check_result = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"Test-Path 'Cert:\\CurrentUser\\Root\\{thumbprint}'",
            ],
            capture_output=True, text=True,
        )
        return check_result.stdout.strip().lower() == "true"
    except Exception:
        return False


def print_trust_instructions_windows(cert_path: Path) -> None:
    """
    Print a one-time message telling the user how to make Chrome/Edge trust the cert.
    Windows requires a visible security dialog — this cannot be done silently.
    """
    sys.stderr.write(
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        " Browser shows \"Not Secure\"? Run this once to fix it:\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f' certutil -addstore -user "Root" "{cert_path}"\n'
        "\n"
        " Windows will ask you to confirm — click Yes.\n"
        " Chrome and Edge will then show a green padlock. (Firefox: see docs)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
    )


def ensure_cert(cert_dir: Path) -> tuple[Path, Path] | None:
    """
    Ensure a self-signed TLS certificate exists, generating one if needed.

    Tries the `cryptography` library first (no external tools required),
    then falls back to the openssl subprocess.

    Args:
        cert_dir: Directory to store cert and key files.

    Returns:
        (cert_path, key_path) on success, or None if generation failed.
    """
    cert_path = cert_dir / "fin.crt"
    key_path = cert_dir / "fin.key"

    if cert_path.exists() and key_path.exists():
        if sys.platform == "win32" and not _cert_already_trusted_windows(cert_path):
            print_trust_instructions_windows(cert_path)
        return cert_path, key_path

    cert_dir.mkdir(parents=True, exist_ok=True)

    generated = False
    if _generate_with_cryptography(cert_path, key_path):
        log.info("TLS certificate generated at %s", cert_dir)
        generated = True
    elif _generate_with_openssl(cert_path, key_path):
        log.info("TLS certificate generated via openssl at %s", cert_dir)
        generated = True

    if not generated:
        return None

    # On Windows, check if the cert is trusted. If not, print one-time instructions.
    # Windows requires a visible confirmation dialog — silent install is not possible.
    if sys.platform == "win32":
        if not _cert_already_trusted_windows(cert_path):
            print_trust_instructions_windows(cert_path)

    return cert_path, key_path
