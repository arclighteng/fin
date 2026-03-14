from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from current directory or parent directories
load_dotenv()

@dataclass(frozen=True)
class Config:
    simplefin_access_url: str
    db_path: str
    log_level: str
    log_format: str = "simple"
    timezone: str = "UTC"  # IANA timezone name (e.g., "America/Chicago")

    def __repr__(self) -> str:
        return f"Config(db_path={self.db_path!r}, log_level={self.log_level!r}, simplefin_access_url='***')"


def _get_default_db_path() -> str:
    """
    Determine the default database path.

    Priority:
    1. Docker environment: /app/data/fin.db (if running inside Docker container)
    2. Windows: %APPDATA%\\fin\\fin.db (user-scoped, persists across CWD changes)
    3. macOS / Linux: $XDG_DATA_HOME/fin/fin.db (or ~/.local/share/fin/fin.db)
    """
    # Check if we're in a Docker container by looking for /.dockerenv
    # or if cwd is /app (typical Docker workdir)
    in_docker = os.path.exists("/.dockerenv") or os.getcwd() == "/app"
    if in_docker:
        return "/app/data/fin.db"

    # On Windows, default to %APPDATA%\fin\fin.db (user-scoped, persists across CWD changes)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        return str(Path(appdata) / "fin" / "fin.db")

    # macOS / Linux: XDG or home-relative
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return str(Path(xdg) / "fin" / "fin.db")


def ensure_data_dir(db_path: str) -> None:
    """
    Create the parent directory of db_path and harden its permissions.

    Directory creation is mandatory; ACL/chmod hardening is best-effort and
    will never raise — a failure here must not block application startup.
    """
    import subprocess
    import getpass

    dir_path = Path(db_path).parent
    dir_path.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        # Restrict the directory to the current user only via icacls.
        # /inheritance:r  — remove inherited ACEs
        # /grant:r        — replace (not add) the grant for this user
        # (OI)(CI)F       — object inherit, container inherit, full control
        username = getpass.getuser()
        try:
            subprocess.run(
                ["icacls", str(dir_path), "/inheritance:r", "/grant:r", f"{username}:(OI)(CI)F"],
                check=True,
                capture_output=True,
            )
        except Exception:
            pass  # ACL hardening is best-effort; don't block startup if it fails
    else:
        try:
            dir_path.chmod(0o700)
        except Exception:
            pass  # chmod is best-effort on non-writable or already-correct dirs


def _get_simplefin_url() -> str:
    """
    Get SimpleFIN access URL with keyring priority.

    Priority:
    1. System keyring (most secure)
    2. Environment variable / .env file (fallback)
    """
    # Try keyring first
    try:
        from . import credentials
        keyring_url = credentials.get_simplefin_url()
        if keyring_url:
            return keyring_url
    except Exception:
        pass  # Keyring not available, fall through to env

    # Fall back to environment variable
    return os.getenv("SIMPLEFIN_ACCESS_URL", "").strip()


def load_config() -> Config:
    access_url = _get_simplefin_url()
    db_path = os.getenv("FIN_DB_PATH", "").strip() or _get_default_db_path()
    log_level = os.getenv("FIN_LOG_LEVEL", "INFO").strip().upper()
    log_format = os.getenv("FIN_LOG_FORMAT", "simple").strip().lower()
    timezone = os.getenv("FIN_TZ", "UTC").strip()

    # Don't crash on missing secrets here; CLI will validate only when needed.
    return Config(
        simplefin_access_url=access_url,
        db_path=db_path,
        log_level=log_level,
        log_format=log_format,
        timezone=timezone,
    )
