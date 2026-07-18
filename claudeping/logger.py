"""
Logging structuré en JSON Lines avec rotation automatique.
"""

import logging
import logging.handlers
import json
import sys
import threading
import traceback
from collections import deque
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
    logger.propagate = False
    logger.handlers.clear()

    # Handler fichier avec rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JsonLineFormatter())
    logger.addHandler(file_handler)

    # Handler console (format humain, coloré par compte dans un vrai terminal)
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setFormatter(
        ColorConsoleFormatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(console_handler)

    return logger


def get_logger() -> logging.Logger:
    """Retourne le logger ClaudePing (après setup_logger appelé)."""
    return logging.getLogger("claudeping")


class AccountLoggerAdapter(logging.LoggerAdapter):
    """Préfixe chaque message par [nom_compte] et ajoute extra={"account": nom}.

    Permet à un même logger/fichier de log partagé de rester lisible quand
    plusieurs comptes tournent en parallèle.
    """

    def __init__(self, logger: logging.Logger, account_name: str) -> None:
        super().__init__(logger, {"account": account_name})

    def process(self, msg, kwargs):
        extra = {**self.extra, **kwargs.get("extra", {})}
        kwargs["extra"] = extra
        return f"[{self.extra['account']}] {msg}", kwargs


# Palette (code ANSI console, couleur hex pour l'UI) partagée entre le
# terminal et l'onglet Logs de l'UI : chaque compte se voit assigner une
# couleur stable (dérivée de son nom), pour distinguer d'un coup d'œil
# les logs de plusieurs comptes qui tournent en parallèle.
ACCOUNT_COLOR_PALETTE: list[tuple[int, str]] = [
    (31, "#c0392b"),   # rouge
    (32, "#2e8b40"),   # vert
    (34, "#2980b9"),   # bleu
    (35, "#8e44ad"),   # violet
    (36, "#16a085"),   # turquoise
    (33, "#b8860b"),   # ambre
    (91, "#d63384"),   # rose
    (94, "#00838f"),   # cyan foncé
]


_account_color_assignments: dict[str, int] = {}
_account_color_lock = threading.Lock()


def account_color(account_name: str) -> tuple[int, str]:
    """Retourne (code_ansi, couleur_hex) pour ce nom de compte.

    Les couleurs sont assignées dans l'ordre de première rencontre (pas par
    hash du nom) : tant qu'il y a ≤ 8 comptes actifs, chacun a une couleur
    garantie unique — deux noms proches ("perso"/"pro") ne se retrouvent
    jamais avec la même couleur. Au-delà de 8 comptes simultanés, la
    palette boucle. Thread-safe (les comptes logguent depuis des threads
    différents).
    """
    with _account_color_lock:
        index = _account_color_assignments.get(account_name)
        if index is None:
            index = len(_account_color_assignments) % len(ACCOUNT_COLOR_PALETTE)
            _account_color_assignments[account_name] = index
    return ACCOUNT_COLOR_PALETTE[index]


class ColorConsoleFormatter(logging.Formatter):
    """Colore toute la ligne selon le compte (extra 'account'), en terminal only."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        account = getattr(record, "account", None)
        if account and sys.stdout.isatty():
            ansi_code, _ = account_color(account)
            return f"\x1b[{ansi_code}m{base}\x1b[0m"
        return base


class GUILogHandler(logging.Handler):
    """Stocke les lignes de log en mémoire pour l'affichage dans l'UI."""

    def __init__(self, maxlen: int = 1000) -> None:
        super().__init__()
        self._buffer: deque[str] = deque(maxlen=maxlen)
        self.setFormatter(logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._buffer.append(self.format(record))
        except Exception:
            self.handleError(record)

    def get_lines(self) -> list[str]:
        return list(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()


_gui_handler: "GUILogHandler | None" = None


def install_gui_log_handler(maxlen: int = 1000) -> GUILogHandler:
    """Installe un handler mémoire pour l'UI et le retourne."""
    global _gui_handler
    _gui_handler = GUILogHandler(maxlen=maxlen)
    logging.getLogger("claudeping").addHandler(_gui_handler)
    return _gui_handler


def get_gui_log_handler() -> "GUILogHandler | None":
    return _gui_handler
