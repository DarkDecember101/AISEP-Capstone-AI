import logging
import sys
import os
import json
from datetime import datetime, timezone
from src.shared.config.settings import settings

# ── Secrets that must never appear in logs ──────────────────────────
_SECRET_FIELDS = {"OPENAI_API_KEY", "TAVILY_API_KEY",
                  "AISEP_INTERNAL_TOKEN", "X-Internal-Token", "api_key",
                  "TAILY_API_KEY"}


class _SafeFormatter(logging.Formatter):
    """JSON-structured log lines with automatic secret masking."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        # Mask known secrets from message
        for key in _SECRET_FIELDS:
            val = os.getenv(key, "")
            if val and len(val) > 4 and val in msg:
                msg = msg.replace(val, f"{key}=****")
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        # Attach correlation_id if available
        try:
            from src.shared.correlation import get_correlation_id
            cid = get_correlation_id()
            if cid:
                entry["correlation_id"] = cid
        except Exception:
            pass
        return json.dumps(entry, default=str)


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    if not logger.handlers:
        formatter = _SafeFormatter()

        # Console handler
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        # File handler
        log_dir = os.path.join(settings.STORAGE_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(log_dir, "aisep.log"))
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger
