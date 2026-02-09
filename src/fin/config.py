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
    2. Local development: data/fin.db relative to current working directory
    """
    # Check if we're in a Docker container by looking for /.dockerenv
    # or if cwd is /app (typical Docker workdir)
    in_docker = os.path.exists("/.dockerenv") or os.getcwd() == "/app"
    if in_docker:
        return "/app/data/fin.db"

    # Local development: use data/fin.db in current directory
    return "data/fin.db"


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
