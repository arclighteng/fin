import logging
from .config import Config

REDACTION_NOTE = "Logs are redacted by default (no merchant/description/PII)."

def setup_logging(cfg: Config) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

