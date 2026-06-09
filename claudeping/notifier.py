"""
Notifications desktop cross-platform via plyer.
Fallback gracieux si plyer n'est pas disponible ou si
la plateforme ne supporte pas les notifications.
"""

from __future__ import annotations

from .logger import get_logger

logger = get_logger()

APP_NAME = "ClaudePing"

try:
    from plyer import notification as _plyer_notification
    _PLYER_AVAILABLE = True
except ImportError:
    _PLYER_AVAILABLE = False
    logger.debug("plyer non disponible — notifications desktop désactivées.")


def _notify(title: str, message: str, timeout: int = 8) -> None:
    """
    Envoie une notification desktop si plyer est disponible.

    Args:
        title: Titre de la notification.
        message: Corps de la notification.
        timeout: Durée d'affichage en secondes.
    """
    if not _PLYER_AVAILABLE:
        return

    try:
        _plyer_notification.notify(
            title=title,
            message=message,
            app_name=APP_NAME,
            timeout=timeout,
        )
    except Exception as e:
        # On ne laisse jamais une erreur de notification crasher le programme
        logger.debug(f"Erreur notification desktop : {e}")


def notify_quota_detected(reset_at_local: str) -> None:
    """Notification : quota atteint, ping schedulé."""
    _notify(
        title=f"⏳ {APP_NAME} — Quota atteint",
        message=f"Session limit atteinte.\nPing automatique schedulé à {reset_at_local}.",
    )


def notify_ping_sent(sent_at_local: str, response: str) -> None:
    """Notification : ping envoyé avec succès."""
    _notify(
        title=f"✅ {APP_NAME} — Ping envoyé",
        message=f"Compteur 5h démarré à {sent_at_local}.\nRéponse : {response}",
    )


def notify_ping_failed(error: str) -> None:
    """Notification : ping échoué."""
    _notify(
        title=f"❌ {APP_NAME} — Ping échoué",
        message=f"Erreur : {error[:120]}",
    )


def notify_error(error: str) -> None:
    """Notification : erreur générale."""
    _notify(
        title=f"⚠️ {APP_NAME} — Erreur",
        message=error[:150],
    )


def notify_service_started() -> None:
    """Notification : service démarré."""
    _notify(
        title=f"🚀 {APP_NAME} — Démarré",
        message="Le service de ping automatique est actif.",
        timeout=5,
    )
