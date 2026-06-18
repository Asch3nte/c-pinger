"""
Persistance de l'état minimal de ClaudePing (JSON).
Permet de survivre à un redémarrage du service.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .logger import get_logger

logger = get_logger()

DEFAULT_STATE_FILE = Path("claudeping_state.json")


class StateManager:
    """
    Gère l'état persistant de ClaudePing.

    État stocké :
    - ping_scheduled_at : datetime UTC du prochain ping prévu (ou null)
    - last_ping_at      : datetime UTC du dernier ping réussi (ou null)
    - ping_count        : nombre total de pings réussis
    """

    def __init__(self, state_file: Path = DEFAULT_STATE_FILE) -> None:
        self._file = state_file
        self._state: dict = self._load()

    def _load(self) -> dict:
        """Charge l'état depuis le fichier JSON."""
        default_state = {
            "ping_scheduled_at": None,
            "last_ping_at": None,
            "ping_count": 0,
            "last_notified_ping_at": None,
        }
        if not self._file.exists():
            return default_state
        try:
            with open(self._file, encoding="utf-8") as f:
                state = json.load(f)
            for key, value in default_state.items():
                state.setdefault(key, value)
            return state
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"État corrompu ou illisible, reset : {e}")
            return default_state

    def _save(self) -> None:
        """Persiste l'état dans le fichier JSON."""
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, default=str)
        except OSError as e:
            logger.error(f"Impossible de sauvegarder l'état : {e}")

    # ------------------------------------------------------------------
    # Accesseurs
    # ------------------------------------------------------------------

    def get_ping_scheduled_at(self) -> datetime | None:
        raw = self._state.get("ping_scheduled_at")
        if raw is None:
            return None
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def set_ping_scheduled_at(self, dt: datetime) -> None:
        self._state["ping_scheduled_at"] = dt.isoformat()
        self._save()

    def clear_ping_scheduled_at(self) -> None:
        self._state["ping_scheduled_at"] = None
        self._save()

    def get_last_ping_at(self) -> datetime | None:
        raw = self._state.get("last_ping_at")
        if raw is None:
            return None
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def record_ping(self, sent_at: datetime) -> None:
        self._state["last_ping_at"] = sent_at.isoformat()
        self._state["ping_count"] = self._state.get("ping_count", 0) + 1
        self._save()

    def get_ping_count(self) -> int:
        return self._state.get("ping_count", 0)

    def get_last_notified_ping_at(self) -> datetime | None:
        raw = self._state.get("last_notified_ping_at")
        if raw is None:
            return None
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def set_last_notified_ping_at(self, dt: datetime) -> None:
        self._state["last_notified_ping_at"] = dt.isoformat()
        self._save()

    def clear_last_notified_ping_at(self) -> None:
        self._state["last_notified_ping_at"] = None
        self._save()
