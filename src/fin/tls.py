# tls.py
"""
Self-signed TLS certificate generation for local HTTPS.

Uses openssl subprocess to generate certs — no Python dependencies needed.
Certs are stored next to the database in a 'certs' directory.
"""
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def ensure_cert(cert_dir: Path) -> tuple[Path, Path] | None:
    """
    Ensure a self-signed TLS certificate exists, generating one if needed.

    Args:
        cert_dir: Directory to store cert and key files

    Returns:
        (cert_path, key_path) tuple, or None if generation failed
    """
    cert_path = cert_dir / "fin.crt"
    key_path = cert_dir / "fin.key"

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    cert_dir.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [
                "openssl", "req",
                "-x509",
                "-newkey", "rsa:2048",
                "-keyout", str(key_path),
                "-out", str(cert_path),
                "-days", "365",
                "-nodes",
                "-subj", "/CN=fin-local/O=fin/C=US",
                "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        log.warning("openssl not found — falling back to HTTP")
        return None
    except subprocess.CalledProcessError as e:
        log.warning("Certificate generation failed: %s", e.stderr.strip())
        return None

    # Restrict key file permissions on Unix
    import sys
    if sys.platform != "win32":
        import os
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass

    return cert_path, key_path
