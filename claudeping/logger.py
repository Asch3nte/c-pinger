"""
Logging structuré en JSON Lines avec rotation automatique.
"""

import logging
import logging.handlers
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path


class JsonLineFormatter(logging.Formatter):
    """Formate chaque log entry en une ligne JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = traceback.format_exception(*record.exc_info)
        # Champs extra passés via logger.info("...", extra={"key": val})
        for key, val in record.__dict__.items():
            if key not in (
                "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "name", "taskName",
            ):
                entry[key] = val
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logger(
    log_file: str | Path,
    level: str = "INFO",
    max_bytes: int = 1_048_576,
    backup_count: int = 3,
) -> logging.Logger:
    """
    Configure et retourne le logger principal de ClaudePing.

    Args:
        log_file: Chemin vers le fichier de log.
        level: Niveau de log ("DEBUG", "INFO", "WARNING", "ERROR").
        max_bytes: Taille max du fichier avant rotation (défaut 1MB).
        backup_count: Nombre de fichiers de backup conservés.

    Returns:
        Logger configuré.
    """
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("claudeping")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Handler fichier avec rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLineFormatter())
    logger.addHandler(file_handler)

    # Handler console (format humain)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(console_handler)

    return logger


def get_logger() -> logging.Logger:
    """Retourne le logger ClaudePing (après setup_logger appelé)."""
    return logging.getLogger("claudeping")
