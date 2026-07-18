"""
Logique de scheduling : mode intelligent (détection quota)
et mode fallback (heure fixe).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import AccountConfig, build_account_env
from .detector import DetectorError, QuotaInfo, run_usage_check
from .logger import AccountLoggerAdapter, get_logger
from .notifier import (
    notify_error,
    notify_ping_failed,
    notify_ping_sent,
    notify_quota_detected,
)
from .pinger import PingResult, send_ping
from .state import StateManager

PING_RETRY_DELAY = 120
PING_MAX_RETRIES = 5


class Scheduler:
    def __init__(self, account: AccountConfig, state: StateManager) -> None:
        # Nommé 'config' pour compat : AccountConfig porte les mêmes
        # sous-champs (probe/ping/fallback/notifications) que l'ancien AppConfig.
        self.config = account
        self.state = state
        self.env = build_account_env(account)
        self.logger = AccountLoggerAdapter(get_logger(), account.name)
        self._stop = False
        self._lock = threading.RLock()
        self.last_probe_at: datetime | None = None
        self.last_quota_info: QuotaInfo | None = None

    def stop(self) -> None:
        self._stop = True

    def trigger_ping_now(self) -> PingResult:
        with self._lock:
            self.logger.info("Ping forcé depuis l'interface utilisateur.")
            return self._do_ping()

    def schedule_manual_ping(self, time_str: str) -> datetime:
        with self._lock:
            tz = ZoneInfo(self.config.fallback.timezone)
            now_local = datetime.now(tz)
            hour, minute = self._parse_manual_time(time_str)
            manual_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if manual_local <= now_local:
                manual_local += timedelta(days=1)
            manual_utc = manual_local.astimezone(timezone.utc)
            self.state.set_ping_scheduled_at(manual_utc)
            self.state.clear_last_notified_ping_at()
            self.logger.info(
                f"Ping manuel programmé à {manual_local.strftime('%Y-%m-%d %H:%M:%S %Z')} "
                f"({manual_utc.isoformat()})"
            )
            return manual_utc

    @staticmethod
    def _parse_manual_time(value: str) -> tuple[int, int]:
        try:
            h, m = value.strip().split(":")
            hour, minute = int(h), int(m)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
            return hour, minute
        except ValueError:
            raise ValueError(f"Format d'heure invalide : '{value}'. Utilisez HH:MM.")

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.logger.info("Scheduler démarré.")
        self._initial_probe()

        while not self._stop:
            with self._lock:
                now = datetime.now(timezone.utc)
                ping_scheduled_at = self.state.get_ping_scheduled_at()

                if ping_scheduled_at is not None and now >= ping_scheduled_at:
                    self._do_ping()
                else:
                    self._do_probe()

            # ← Relire APRÈS probe (qui peut avoir modifié le schedule)
            ping_scheduled_at = self.state.get_ping_scheduled_at()
            sleep_seconds = self._compute_sleep(ping_scheduled_at)
            self.logger.debug(f"Sleep {sleep_seconds:.0f}s avant prochaine action.")
            self._interruptible_sleep(sleep_seconds)

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def _log_quota_summary(self, result: QuotaInfo) -> None:
        """Logue un résumé lisible des quotas session + hebdo."""
        tz = ZoneInfo(self.config.fallback.timezone)

        if result.session_pct is not None:
            reset_str = (
                result.reset_at.astimezone(tz).strftime("%d/%m à %H:%M %Z")
                if result.reset_at else "inconnu"
            )
            self.logger.info(f"Session : {result.session_pct}% utilisé — reset le {reset_str}")
        else:
            self.logger.info("Session : aucune session active (compteur à 0)")

        if result.weekly_used is not None:
            w_reset_str = (
                result.weekly_reset_at.astimezone(tz).strftime("%d/%m à %H:%M %Z")
                if result.weekly_reset_at else "inconnu"
            )
            self.logger.info(f"Hebdo   : {result.weekly_used:.0f}% utilisé — reset le {w_reset_str}")

    def _initial_probe(self) -> None:
        """Probe unique au démarrage — récupère le reset_at réel via /usage."""
        self.logger.info("Probe initial au démarrage...")
        try:
            result = run_usage_check(self.config.claude_executable, env=self.env, log=self.logger)
            self.last_probe_at = datetime.now(timezone.utc)
            self.last_quota_info = result
            self._log_quota_summary(result)
            self._handle_probe_result(result)
        except DetectorError as e:
            self.logger.warning(f"Probe initial échoué : {e}")
            if self.config.fallback.enabled:
                self._schedule_fallback()

    def _do_probe(self) -> None:
        # Si un ping est schedulé dans moins de 60s, ne pas sonder
        scheduled = self.state.get_ping_scheduled_at()
        if scheduled is not None:
            seconds_until = (scheduled - datetime.now(timezone.utc)).total_seconds()
            if seconds_until < 60:
                self.logger.debug("Ping imminent, probe ignoré.")
                return
        try:
            result = run_usage_check(self.config.claude_executable, env=self.env, log=self.logger)
            self.last_probe_at = datetime.now(timezone.utc)
            self.last_quota_info = result
            self._log_quota_summary(result)
            self._handle_probe_result(result)
        except DetectorError as e:
            self.logger.warning(f"Probe échoué : {e}")
            if self.config.notifications.enabled and self.config.notifications.on_error:
                notify_error(str(e), account_name=self.config.name)

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
                self.logger.info("Quota atteint — schedule ping au reset.")
                self._schedule_ping(result.reset_at, source="intelligent")
            else:
                self.logger.warning("Quota atteint mais heure de reset non parsée → fallback.")
                if self.config.fallback.enabled:
                    self._schedule_fallback()
            return

        if result.reset_at is not None:
            # Session en cours : reschedule si l'heure diffère de plus de 2 min.
            current = self.state.get_ping_scheduled_at()
            new_ping_at = result.reset_at + timedelta(seconds=30)

            if current is None or abs((new_ping_at - current).total_seconds()) > 120:
                self.logger.info("Ping schedulé au prochain reset de session.")
                self._schedule_ping(result.reset_at, source="intelligent", notify=False)
            else:
                self.logger.info("Ping déjà schedulé au bon moment, aucun changement."
                )
            return

        # Pas de reset_at → session pas encore démarrée (compteur à 0)
        # On ping immédiatement pour démarrer le compteur.
        self.logger.info("Aucune session active détectée → ping immédiat pour démarrer le compteur.")
        current = self.state.get_ping_scheduled_at()
        now = datetime.now(timezone.utc)
        if current is None or (current - now).total_seconds() > 60:
            ping_now = now + timedelta(seconds=5)
            self.state.set_ping_scheduled_at(ping_now)

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def _schedule_ping(self, reset_at: datetime, source: str, notify: bool = True) -> None:
        ping_at = reset_at + timedelta(seconds=30)
        now = datetime.now(timezone.utc)

        if ping_at <= now:
            self.logger.info(f"Heure de reset déjà passée ({source}), ping immédiat.")
            ping_at = now + timedelta(seconds=5)

        current = self.state.get_ping_scheduled_at()
        if current is not None and abs((current - ping_at).total_seconds()) < 60:
            self.logger.info(
                f"Ping déjà schedulé au même moment ({source}) — aucun changement.",
                extra={"ping_at_utc": ping_at.isoformat()},
            )
            if notify and self.config.notifications.enabled and self.config.notifications.on_quota_detected:
                last_notified = self.state.get_last_notified_ping_at()
                if last_notified is None or abs((last_notified - ping_at).total_seconds()) > 1:
                    notify_quota_detected(
                        self.config.name,
                        ping_at.astimezone(ZoneInfo(self.config.fallback.timezone)).strftime("%H:%M:%S"),
                    )
                    self.state.set_last_notified_ping_at(ping_at)
                else:
                    self.logger.debug("Notification de ping déjà envoyée pour cette heure, suppression du doublon.")
            return

        self.state.set_ping_scheduled_at(ping_at)

        tz = ZoneInfo(self.config.fallback.timezone)
        ping_at_local = ping_at.astimezone(tz).strftime("%H:%M:%S")

        self.logger.info(
            f"Ping schedulé ({source})",
            extra={"ping_at_utc": ping_at.isoformat(), "ping_at_local": ping_at_local},
        )

        if notify and self.config.notifications.enabled and self.config.notifications.on_quota_detected:
            notify_quota_detected(self.config.name, ping_at_local)
            self.state.set_last_notified_ping_at(ping_at)

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

    def _do_ping(self) -> PingResult:
        tz = ZoneInfo(self.config.fallback.timezone)

        for attempt in range(1, PING_MAX_RETRIES + 1):
            self.logger.info(f"Tentative de ping #{attempt}/{PING_MAX_RETRIES}")
            result: PingResult = send_ping(
                self.config.claude_executable,
                self.config.ping.model,
                self.config.ping.message,
                env=self.env,
                log=self.logger,
            )

            if result.success:
                sent_local = result.sent_at.astimezone(tz).strftime("%H:%M:%S")
                self.logger.info(
                    "Ping réussi — compteur 5h démarré",
                    extra={"sent_at_local": sent_local, "ping_response": result.response},
                )
                if self.config.notifications.enabled and self.config.notifications.on_ping_sent:
                    notify_ping_sent(self.config.name, sent_local, result.response)

                self.state.clear_ping_scheduled_at()
                self.state.record_ping(result.sent_at)

                # Après un ping réussi : on refait un /usage pour connaître
                # le prochain reset et le reschedule immédiatement.
                self._schedule_next_after_ping()
                return result

            if result.quota_still_active and attempt < PING_MAX_RETRIES:
                self.logger.info(f"Quota toujours actif, retry dans {PING_RETRY_DELAY}s...")
                time.sleep(PING_RETRY_DELAY)
                continue

            break

        error_msg = f"Ping échoué après {PING_MAX_RETRIES} tentatives : {result.error}"
        self.logger.error(error_msg)
        if self.config.notifications.enabled and self.config.notifications.on_error:
            notify_ping_failed(self.config.name, result.error)

        retry_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.state.set_ping_scheduled_at(retry_at)
        return result

    def _schedule_next_after_ping(self) -> None:
        """
        Après un ping réussi, interroge /usage pour connaître le prochain
        reset et le schedule. Attend 10s pour laisser le serveur mettre
        à jour le compteur.
        """
        self.logger.info("Attente 10s puis /usage pour récupérer le prochain reset...")
        time.sleep(10)
        try:
            usage = run_usage_check(self.config.claude_executable, env=self.env, log=self.logger)
            if usage.reset_at is not None:
                self._schedule_ping(usage.reset_at, source="intelligent", notify=True)
            else:
                self.logger.warning("Impossible de récupérer le prochain reset → fallback.")
                if self.config.fallback.enabled:
                    self._schedule_fallback()
        except DetectorError as e:
            self.logger.warning(f"/usage post-ping échoué : {e} → fallback.")
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
