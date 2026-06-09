"""
Logique de scheduling : mode intelligent (détection quota) 
et mode fallback (heure fixe).

Le scheduler tourne dans une boucle principale et gère :
- Les probes périodiques
- La détection du quota
- Le scheduling du ping au bon moment
- Le fallback si la détection échoue
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import AppConfig
from .detector import DetectorError, QuotaInfo, run_probe
from .logger import get_logger
from .notifier import (
    notify_error,
    notify_ping_failed,
    notify_ping_sent,
    notify_quota_detected,
)
from .pinger import PingResult, send_ping
from .state import StateManager

logger = get_logger()

# Délai de retry si le quota est toujours actif au moment du ping (secondes)
PING_RETRY_DELAY = 120   # 2 minutes
PING_MAX_RETRIES = 5


class Scheduler:
    """
    Orchestre les probes et les pings.

    États internes :
    - PROBING   : On sonde périodiquement si le quota est atteint
    - WAITING   : Quota détecté, on attend l'heure de reset
    - PINGING   : On est à l'heure de reset, on envoie le ping
    """

    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state
        self._stop = False

    def stop(self) -> None:
        """Demande l'arrêt propre de la boucle principale."""
        self._stop = True

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Boucle principale du scheduler.
        Tourne indéfiniment jusqu'à self.stop().
        """
        logger.info("Scheduler démarré.")

        # Au démarrage : probe immédiat pour voir l'état courant
        self._initial_probe()

        while not self._stop:
            now = datetime.now(timezone.utc)

            ping_scheduled_at = self.state.get_ping_scheduled_at()

            if ping_scheduled_at is not None and now >= ping_scheduled_at:
                # Il est temps de pinger !
                self._do_ping()

            else:
                # Pas de ping schedulé ou pas encore l'heure → probe
                self._do_probe()

            # Calcul du prochain wake-up
            sleep_seconds = self._compute_sleep(ping_scheduled_at)
            logger.debug(f"Sleep {sleep_seconds:.0f}s avant prochaine action.")
            self._interruptible_sleep(sleep_seconds)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def _initial_probe(self) -> None:
        """Probe unique au démarrage pour détecter l'état actuel."""
        logger.info("Probe initial au démarrage...")
        try:
            self._handle_probe_result(
                run_probe(self.config.probe.model, self.config.probe.message)
            )
        except DetectorError as e:
            logger.warning(f"Probe initial échoué : {e}")

    def _do_probe(self) -> None:
        """Effectue un probe et traite le résultat."""
        try:
            result = run_probe(self.config.probe.model, self.config.probe.message)
            self._handle_probe_result(result)
        except DetectorError as e:
            logger.warning(f"Probe échoué : {e}")
            if self.config.notifications.enabled and self.config.notifications.on_error:
                notify_error(str(e))

    def _handle_probe_result(self, result: QuotaInfo) -> None:
        """Traite le résultat d'un probe."""
        if not result.quota_hit:
            # Pas de quota → s'assurer que le fallback est schedulé si activé
            if self.config.fallback.enabled and self.state.get_ping_scheduled_at() is None:
                self._schedule_fallback()
            return

        # Quota détecté !
        if result.reset_at is not None:
            self._schedule_ping(result.reset_at, source="intelligent")
        else:
            # Heure de reset non parsée → fallback
            logger.warning("Quota détecté mais heure de reset non parsée → fallback.")
            if self.config.fallback.enabled:
                self._schedule_fallback()

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _schedule_ping(self, reset_at: datetime, source: str, notify: bool = True) -> None:
        """
        Schedule le ping à l'heure de reset.

        Args:
            reset_at: datetime UTC du reset.
            source: "intelligent" ou "fallback" (pour les logs).
        """
        # Petite marge de sécurité : on ping 30s après le reset
        # pour laisser le temps au serveur Claude de reset
        ping_at = reset_at + timedelta(seconds=30)

        # Si l'heure est déjà passée (ex: démarrage tardif du service)
        # on ping immédiatement
        now = datetime.now(timezone.utc)
        if ping_at <= now:
            logger.info(
                f"Heure de reset déjà passée ({source}), ping immédiat."
            )
            ping_at = now + timedelta(seconds=5)

        self.state.set_ping_scheduled_at(ping_at)

        # Formatage en heure locale pour la notification
        tz = ZoneInfo(self.config.fallback.timezone)
        ping_at_local = ping_at.astimezone(tz).strftime("%H:%M:%S")

        logger.info(
            f"Ping schedulé ({source})",
            extra={"ping_at_utc": ping_at.isoformat(), "ping_at_local": ping_at_local},
        )


        if notify and self.config.notifications.enabled and self.config.notifications.on_quota_detected:
            notify_quota_detected(ping_at_local)

    def _schedule_fallback(self) -> None:
        """Schedule le ping fallback à l'heure fixe configurée."""
        tz = ZoneInfo(self.config.fallback.timezone)
        now_local = datetime.now(tz)

        h, m = map(int, self.config.fallback.time.split(":"))
        fallback_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)

        # Si l'heure est déjà passée aujourd'hui → demain
        if fallback_local <= now_local:
            fallback_local += timedelta(days=1)

        fallback_utc = fallback_local.astimezone(timezone.utc)
        self._schedule_ping(fallback_utc, source="fallback", notify=False)  # ← notify=False

    # ------------------------------------------------------------------
    # Ping
    # ------------------------------------------------------------------

    def _do_ping(self) -> None:
        """Envoie le ping et traite le résultat."""
        tz = ZoneInfo(self.config.fallback.timezone)

        for attempt in range(1, PING_MAX_RETRIES + 1):
            logger.info(f"Tentative de ping #{attempt}/{PING_MAX_RETRIES}")
            result: PingResult = send_ping(
                self.config.ping.model,
                self.config.ping.message,
            )

            if result.success:
                sent_local = result.sent_at.astimezone(tz).strftime("%H:%M:%S")
                logger.info(
                    "Ping réussi — compteur 5h démarré",
                    extra={"sent_at_local": sent_local, "response": result.response},
                )
                if self.config.notifications.enabled and self.config.notifications.on_ping_sent:
                    notify_ping_sent(sent_local, result.response)

                # Reset l'état : plus de ping schedulé
                self.state.clear_ping_scheduled_at()
                self.state.record_ping(result.sent_at)
                return

            if result.quota_still_active and attempt < PING_MAX_RETRIES:
                logger.info(
                    f"Quota toujours actif, retry dans {PING_RETRY_DELAY}s..."
                )
                time.sleep(PING_RETRY_DELAY)
                continue

            # Échec non récupérable
            break

        error_msg = f"Ping échoué après {PING_MAX_RETRIES} tentatives : {result.error}"
        logger.error(error_msg)
        if self.config.notifications.enabled and self.config.notifications.on_error:
            notify_ping_failed(result.error)

        # On réessaie dans 10 minutes
        retry_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.state.set_ping_scheduled_at(retry_at)

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    def _compute_sleep(self, ping_scheduled_at: datetime | None) -> float:
        """
        Calcule le temps de sleep optimal.

        - Si un ping est schedulé : sleep jusqu'à l'heure du ping
          (max : intervalle de probe)
        - Sinon : sleep pendant l'intervalle de probe
        """
        probe_interval = self.config.probe.interval_minutes * 60
        now = datetime.now(timezone.utc)

        if ping_scheduled_at is not None:
            seconds_until_ping = (ping_scheduled_at - now).total_seconds()
            # On se réveille 5s avant le ping prévu pour être précis,
            # mais pas plus longtemps que l'intervalle de probe
            sleep = min(max(seconds_until_ping - 5, 1), probe_interval)
        else:
            sleep = probe_interval

        return float(sleep)

    def _interruptible_sleep(self, seconds: float) -> None:
        """
        Sleep interruptible par tranches de 1s pour réagir à stop().
        """
        elapsed = 0.0
        while elapsed < seconds and not self._stop:
            time.sleep(1)
            elapsed += 1
