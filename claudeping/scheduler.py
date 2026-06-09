"""
Logique de scheduling : mode intelligent (détection quota) 
et mode fallback (heure fixe).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import AppConfig
from .detector import DetectorError, QuotaInfo, run_usage_check
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

PING_RETRY_DELAY = 120
PING_MAX_RETRIES = 5


class Scheduler:
    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def run(self) -> None:
        logger.info("Scheduler démarré.")
        self._initial_probe()

        while not self._stop:
            now = datetime.now(timezone.utc)
            ping_scheduled_at = self.state.get_ping_scheduled_at()

            if ping_scheduled_at is not None and now >= ping_scheduled_at:
                self._do_ping()
            else:
                self._do_probe()

            sleep_seconds = self._compute_sleep(ping_scheduled_at)
            logger.debug(f"Sleep {sleep_seconds:.0f}s avant prochaine action.")
            self._interruptible_sleep(sleep_seconds)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def _initial_probe(self) -> None:
        """Probe unique au démarrage — récupère le reset_at réel via /usage."""
        logger.info("Probe initial au démarrage...")
        try:
            self._handle_probe_result(run_usage_check())
        except DetectorError as e:
            logger.warning(f"Probe initial échoué : {e}")
            if self.config.fallback.enabled:
                self._schedule_fallback()

    def _do_probe(self) -> None:
        try:
            self._handle_probe_result(run_usage_check())
        except DetectorError as e:
            logger.warning(f"Probe échoué : {e}")
            if self.config.notifications.enabled and self.config.notifications.on_error:
                notify_error(str(e))

    def _handle_probe_result(self, result: QuotaInfo) -> None:
        """
        Traite le résultat de /usage.

        Cas possibles :
        1. quota_hit=True                → schedule au reset_at (ou fallback)
        2. quota_hit=False, reset_at set → session active, schedule au reset_at réel
        3. quota_hit=False, reset_at=None → session pas encore démarrée → ping immédiat
        """
        if result.quota_hit:
            # Quota 100% atteint
            if result.reset_at is not None:
                logger.info("Quota atteint — schedule ping au reset.")
                self._schedule_ping(result.reset_at, source="intelligent")
            else:
                logger.warning("Quota atteint mais heure de reset non parsée → fallback.")
                if self.config.fallback.enabled:
                    self._schedule_fallback()
            return

        if result.reset_at is not None:
            # Session en cours : on connaît l'heure de reset réelle.
            # On ne reschedule que si rien n'est déjà prévu ou si l'heure
            # diffère significativement (> 2 min) de ce qui est schedulé.
            current = self.state.get_ping_scheduled_at()
            new_ping_at = result.reset_at + timedelta(seconds=30)

            if current is None or abs((new_ping_at - current).total_seconds()) > 120:
                tz = ZoneInfo(self.config.fallback.timezone)
                reset_local = result.reset_at.astimezone(tz).strftime("%d/%m %H:%M:%S")
                logger.info(
                    f"Session active ({result.session_pct}% utilisé) — "
                    f"prochain reset prévu à {reset_local}, ping schedulé."
                )
                self._schedule_ping(result.reset_at, source="intelligent", notify=False)
            else:
                logger.debug(
                    f"Session active ({result.session_pct}% utilisé) — "
                    f"ping déjà schedulé au bon moment, rien à faire."
                )
            return

        # Pas de reset_at → session pas encore démarrée (compteur à 0)
        # On ping immédiatement pour démarrer le compteur.
        logger.info("Aucune session active détectée → ping immédiat pour démarrer le compteur.")
        ping_now = datetime.now(timezone.utc) + timedelta(seconds=5)
        self.state.set_ping_scheduled_at(ping_now)

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _schedule_ping(self, reset_at: datetime, source: str, notify: bool = True) -> None:
        ping_at = reset_at + timedelta(seconds=30)
        now = datetime.now(timezone.utc)

        if ping_at <= now:
            logger.info(f"Heure de reset déjà passée ({source}), ping immédiat.")
            ping_at = now + timedelta(seconds=5)

        self.state.set_ping_scheduled_at(ping_at)

        tz = ZoneInfo(self.config.fallback.timezone)
        ping_at_local = ping_at.astimezone(tz).strftime("%H:%M:%S")

        logger.info(
            f"Ping schedulé ({source})",
            extra={"ping_at_utc": ping_at.isoformat(), "ping_at_local": ping_at_local},
        )

        if notify and self.config.notifications.enabled and self.config.notifications.on_quota_detected:
            notify_quota_detected(ping_at_local)

    def _schedule_fallback(self) -> None:
        tz = ZoneInfo(self.config.fallback.timezone)
        now_local = datetime.now(tz)
        h, m = map(int, self.config.fallback.time.split(":"))
        fallback_local = now_local.replace(hour=h, minute=m, second=0, microsecond=0)
        if fallback_local <= now_local:
            fallback_local += timedelta(days=1)
        fallback_utc = fallback_local.astimezone(timezone.utc)
        self._schedule_ping(fallback_utc, source="fallback", notify=False)

    # ------------------------------------------------------------------
    # Ping
    # ------------------------------------------------------------------

    def _do_ping(self) -> None:
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
                    extra={"sent_at_local": sent_local, "ping_response": result.response},
                )
                if self.config.notifications.enabled and self.config.notifications.on_ping_sent:
                    notify_ping_sent(sent_local, result.response)

                self.state.clear_ping_scheduled_at()
                self.state.record_ping(result.sent_at)

                # Après un ping réussi : on refait un /usage pour connaître
                # le prochain reset et le reschedule immédiatement.
                self._schedule_next_after_ping()
                return

            if result.quota_still_active and attempt < PING_MAX_RETRIES:
                logger.info(f"Quota toujours actif, retry dans {PING_RETRY_DELAY}s...")
                time.sleep(PING_RETRY_DELAY)
                continue

            break

        error_msg = f"Ping échoué après {PING_MAX_RETRIES} tentatives : {result.error}"
        logger.error(error_msg)
        if self.config.notifications.enabled and self.config.notifications.on_error:
            notify_ping_failed(result.error)

        retry_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.state.set_ping_scheduled_at(retry_at)

    def _schedule_next_after_ping(self) -> None:
        """
        Après un ping réussi, interroge /usage pour connaître le prochain
        reset et le schedule. Attend 10s pour laisser le serveur mettre
        à jour le compteur.
        """
        logger.info("Attente 10s puis /usage pour récupérer le prochain reset...")
        time.sleep(10)
        try:
            usage = run_usage_check()
            if usage.reset_at is not None:
                self._schedule_ping(usage.reset_at, source="intelligent", notify=True)
            else:
                logger.warning("Impossible de récupérer le prochain reset → fallback.")
                if self.config.fallback.enabled:
                    self._schedule_fallback()
        except DetectorError as e:
            logger.warning(f"/usage post-ping échoué : {e} → fallback.")
            if self.config.fallback.enabled:
                self._schedule_fallback()

    # ------------------------------------------------------------------
    # Utilitaires
    # ------------------------------------------------------------------

    def _compute_sleep(self, ping_scheduled_at: datetime | None) -> float:
        probe_interval = self.config.probe.interval_minutes * 60
        now = datetime.now(timezone.utc)

        if ping_scheduled_at is not None:
            seconds_until_ping = (ping_scheduled_at - now).total_seconds()
            sleep = min(max(seconds_until_ping - 5, 1), probe_interval)
        else:
            sleep = probe_interval

        return float(sleep)

    def _interruptible_sleep(self, seconds: float) -> None:
        elapsed = 0.0
        while elapsed < seconds and not self._stop:
            time.sleep(1)
            elapsed += 1
