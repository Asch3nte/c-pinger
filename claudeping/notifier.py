"""
Notifications desktop cross-platform.
Ordre de tentative : gdbus (Linux/GNOME) → plyer → silencieux.
"""

from __future__ import annotations

import subprocess
import sys
from .logger import get_logger

logger = get_logger()

APP_NAME = "ClaudePing"


def _notify_gdbus(title: str, message: str, timeout_ms: int = 8000) -> bool:
    """D-Bus direct via gdbus — toujours disponible sur Ubuntu/GNOME."""
    try:
        subprocess.Popen(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.Notifications",
                "--object-path", "/org/freedesktop/Notifications",
                "--method", "org.freedesktop.Notifications.Notify",
                APP_NAME, "0", "dialog-information",
                title, message, "[]", "{}", str(timeout_ms),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def _notify_plyer(title: str, message: str, timeout: int = 8) -> bool:
    try:
        from plyer import notification as _n
        _n.notify(title=title, message=message, app_name=APP_NAME, timeout=timeout)
        return True
    except Exception:
        return False


def _notify(title: str, message: str, timeout: int = 8) -> None:
    if sys.platform != "win32":
        if _notify_gdbus(title, message, timeout * 1000):
            return
    if not _notify_plyer(title, message, timeout):
        logger.debug(f"Notification non envoyée (aucun backend disponible) : {title}")


def notify_quota_detected(account_name: str, reset_at_local: str) -> None:
    _notify(
        title=f"{APP_NAME} — Quota atteint [{account_name}]",
        message=f"Ping automatique schedulé à {reset_at_local}.",
    )


def notify_ping_sent(account_name: str, sent_at_local: str, response: str) -> None:
    _notify(
        title=f"{APP_NAME} — Ping envoyé [{account_name}]",
        message=f"Compteur 5h démarré à {sent_at_local}. Réponse : {response}",
    )


def notify_ping_failed(account_name: str, error: str) -> None:
    _notify(
        title=f"{APP_NAME} — Ping échoué [{account_name}]",
        message=f"Erreur : {error[:120]}",
    )


def notify_error(error: str, account_name: str | None = None) -> None:
    title = f"{APP_NAME} — Erreur" + (f" [{account_name}]" if account_name else "")
    _notify(title=title, message=error[:150])


def notify_service_started(account_count: int | None = None) -> None:
    detail = f" ({account_count} compte(s))" if account_count is not None else ""
    _notify(
        title=f"{APP_NAME} — Démarré",
        message=f"Le service de ping automatique est actif{detail}.",
        timeout=5,
    )


def notify_running_in_background() -> None:
    _notify(
        title=f"{APP_NAME} — Actif en arrière-plan",
        message="Le service continue à tourner. Icône dans la barre système pour rouvrir.",
        timeout=5,
    )
