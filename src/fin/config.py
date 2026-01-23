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


def load_config() -> Config:
    access_url = os.getenv("SIMPLEFIN_ACCESS_URL", "").strip()
    db_path = os.getenv("FIN_DB_PATH", "").strip() or _get_default_db_path()
    log_level = os.getenv("FIN_LOG_LEVEL", "INFO").strip().upper()

    # Don't crash on missing secrets here; CLI will validate only when needed.
    return Config(
        simplefin_access_url=access_url,
        db_path=db_path,
        log_level=log_level,
    )
