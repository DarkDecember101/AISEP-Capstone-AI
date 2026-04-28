import logging
import sys
import os
import json
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from src.shared.config.settings import settings

# ── Secrets that must never appear in logs ──────────────────────────
_SECRET_FIELDS = {"OPENAI_API_KEY", "TAVILY_API_KEY",
                  "AISEP_INTERNAL_TOKEN", "X-Internal-Token", "api_key",
                  "TAILY_API_KEY"}

_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_LOG_BACKUP_COUNT = 5


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


def _build_file_handler(filename: str) -> RotatingFileHandler:
    log_dir = settings.LOG_DIR
    os.makedirs(log_dir, exist_ok=True)
    handler = RotatingFileHandler(
        os.path.join(log_dir, filename),
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(_SafeFormatter())
    return handler


def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    if not logger.handlers:
        formatter = _SafeFormatter()

        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

        logger.addHandler(_build_file_handler("aisep.log"))

    return logger


def configure_root_logger(filename: str = "aisep.log") -> None:
    """Attach JSON file + console handlers to the root logger so every log
    line (including uvicorn / celery / 3rd-party libraries) is captured.

    Call once at process startup. `filename` lets API and worker write to
    separate files to avoid multi-process rotation conflicts.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Avoid double-attaching when called twice (e.g. uvicorn reload).
    marker = f"__aisep_file_handler__:{filename}"
    if any(getattr(h, "_aisep_marker", None) == marker for h in root.handlers):
        return

    formatter = _SafeFormatter()

    has_stream = any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    if not has_stream:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    fh = _build_file_handler(filename)
    fh._aisep_marker = marker  # type: ignore[attr-defined]
    root.addHandler(fh)
