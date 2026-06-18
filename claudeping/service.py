"""Service backend de ClaudePing.

Ce module expose une couche de service qui permet à une UI ou à un mode
service de démarrer le scheduler, de consulter l'état et de piloter le ping.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, load_config
from .detector import QuotaInfo, check_claude_cli, execute_claude_auth
from .logger import get_logger
from .scheduler import PingResult, Scheduler
from .state import StateManager

logger = get_logger()


@dataclass
class ServiceStatus:
    running: bool
    next_ping_at: datetime | None
    last_ping_at: datetime | None
    ping_count: int
    fallback_enabled: bool
    fallback_time: str
    claude_executable: str
    last_probe_at: datetime | None
    quota_hit: bool | None
    session_pct: int | None
    reset_at: datetime | None
    daily_used: float | None = None
    daily_limit: float | None = None
    weekly_used: float | None = None
    weekly_limit: float | None = None
    monthly_used: float | None = None
    monthly_limit: float | None = None
    auth_status: str = "inconnu"
    cli_available: bool = False
    quota_status_daily: str = "inconnu"
    quota_status_weekly: str = "inconnu"
    quota_status_monthly: str = "inconnu"
    probe_interval_minutes: int = 30


class ClaudePingService:
    def __init__(
        self,
        config_path: Path | str = Path("config.yaml"),
        state_path: Path | str = Path("claudeping_state.json"),
    ) -> None:
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.config = load_config(self.config_path)
        self.state = StateManager(self.state_path)
        self.scheduler = Scheduler(self.config, self.state)
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        logger.info("Démarrage du service ClaudePing...")
        self._thread = threading.Thread(
            target=self.scheduler.run,
            daemon=True,
            name="ClaudePingScheduler",
        )
        self._thread.start()
        self._started = True

    def stop(self, timeout: float = 5.0) -> None:
        if not self._started:
            return

        logger.info("Arrêt du service ClaudePing...")
        self.scheduler.stop()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self._started = False

    def is_running(self) -> bool:
        return self._started and self._thread is not None and self._thread.is_alive()

    def trigger_ping_now(self) -> PingResult:
        return self.scheduler.trigger_ping_now()

    def schedule_manual_ping(self, time_str: str) -> datetime:
        return self.scheduler.schedule_manual_ping(time_str)

    def refresh_claude_status(self) -> QuotaInfo:
        status = check_claude_cli(self.config.claude_executable)
        self.scheduler.last_quota_info = status
        self.state.clear_last_notified_ping_at()
        return status

    def login_claude(self) -> tuple[bool, str]:
        success, message = execute_claude_auth(self.config.claude_executable, "login")
        if success:
            self.refresh_claude_status()
        return success, message

    def logout_claude(self) -> tuple[bool, str]:
        success, message = execute_claude_auth(self.config.claude_executable, "logout")
        if success:
            self.refresh_claude_status()
        return success, message

    def set_fallback_enabled(self, enabled: bool, persist: bool = True) -> None:
        self.config.fallback.enabled = enabled
        logger.info(f"Fallback {'activé' if enabled else 'désactivé'} via UI.")
        if persist:
            self._save_config()

    def set_fallback_time(self, time_str: str, persist: bool = True) -> None:
        self.config.fallback.time = time_str
        logger.info(f"Heure de fallback mise à jour : {time_str}")
        if persist:
            self._save_config()

    def set_claude_executable(self, path: str, persist: bool = True) -> None:
        self.config.claude_executable = path
        logger.info(f"Chemin Claude Code CLI configuré : {path}")
        if persist:
            self._save_config()

    def set_probe_interval(self, minutes: int, persist: bool = True) -> None:
        self.config.probe.interval_minutes = max(1, minutes)
        logger.info(f"Intervalle de probe configuré : {self.config.probe.interval_minutes} min")
        if persist:
            self._save_config()

    def _save_config(self) -> None:
        from .config import save_config

        try:
            save_config(self.config, self.config_path)
        except Exception as e:
            logger.error(f"Impossible de sauvegarder la configuration : {e}")

    def get_status(self) -> ServiceStatus:
        quota_info = self.scheduler.last_quota_info
        return ServiceStatus(
            running=self.is_running(),
            next_ping_at=self.state.get_ping_scheduled_at(),
            last_ping_at=self.state.get_last_ping_at(),
            ping_count=self.state.get_ping_count(),
            fallback_enabled=self.config.fallback.enabled,
            fallback_time=self.config.fallback.time,
            claude_executable=self.config.claude_executable,
            last_probe_at=self.scheduler.last_probe_at,
            quota_hit=quota_info.quota_hit if quota_info is not None else None,
            session_pct=quota_info.session_pct if quota_info is not None else None,
            reset_at=quota_info.reset_at if quota_info is not None else None,
            daily_used=quota_info.daily_used if quota_info is not None else None,
            daily_limit=quota_info.daily_limit if quota_info is not None else None,
            weekly_used=quota_info.weekly_used if quota_info is not None else None,
            weekly_limit=quota_info.weekly_limit if quota_info is not None else None,
            monthly_used=quota_info.monthly_used if quota_info is not None else None,
            monthly_limit=quota_info.monthly_limit if quota_info is not None else None,
            auth_status=quota_info.auth_status if quota_info is not None else "inconnu",
            cli_available=quota_info.cli_available if quota_info is not None else False,
            quota_status_daily=self._compute_quota_status(quota_info),
            quota_status_weekly=self._compute_quota_status(quota_info, weekly=True),
            quota_status_monthly=self._compute_quota_status(quota_info, monthly=True),
            probe_interval_minutes=self.config.probe.interval_minutes,
        )

    def _compute_quota_status(
        self,
        quota_info: QuotaInfo | None,
        weekly: bool = False,
        monthly: bool = False,
    ) -> str:
        if quota_info is None:
            return "—"
        if quota_info.quota_hit:
            return "quota atteint"

        if monthly:
            if quota_info.monthly_used is None or quota_info.monthly_limit is None:
                return "—"
            return "ok" if quota_info.monthly_used < quota_info.monthly_limit else "quota atteint"
        if weekly:
            if quota_info.weekly_used is None or quota_info.weekly_limit is None:
                return "—"
            return "ok" if quota_info.weekly_used < quota_info.weekly_limit else "quota atteint"

        # Session (fenêtre 5h de Claude) — utilise session_pct en priorité
        if quota_info.session_pct is not None:
            return "ok" if quota_info.session_pct < 100 else "quota atteint"
        if quota_info.daily_used is not None and quota_info.daily_limit is not None:
            return "ok" if quota_info.daily_used < quota_info.daily_limit else "quota atteint"
        return "—"

    @classmethod
    def create(cls, config_path: Path | str = Path("config.yaml"), state_path: Path | str = Path("claudeping_state.json")) -> ClaudePingService:
        try:
            return cls(config_path=config_path, state_path=state_path)
        except ConfigError as e:
            logger.error(f"Impossible de charger la configuration : {e}")
            raise
