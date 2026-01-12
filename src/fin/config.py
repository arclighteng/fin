from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Config:
    simplefin_access_url: str
    db_path: str
    log_level: str

def load_config() -> Config:
    access_url = os.getenv("SIMPLEFIN_ACCESS_URL", "").strip()
    db_path = os.getenv("FIN_DB_PATH", "/app/data/fin.db").strip()
    log_level = os.getenv("FIN_LOG_LEVEL", "INFO").strip().upper()

    # Don’t crash on missing secrets here; CLI will validate only when needed.
    return Config(
        simplefin_access_url=access_url,
        db_path=db_path,
        log_level=log_level,
    )
