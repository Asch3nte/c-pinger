"""Service backend de ClaudePing.

Ce module expose deux couches :

- `AccountService` : pilote le scheduler d'un seul compte Claude (démarrage,
  arrêt, ping manuel, login/logout, réglages).
- `ClaudePingManager` : gère une collection d'`AccountService`, un par
  compte défini dans config.yaml, démarrés simultanément. C'est le point
  d'entrée utilisé par l'UI et le mode service en ligne de commande.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import (
    DEFAULT_ACCOUNT_NAME,
    AccountConfig,
    AppConfig,
    ConfigError,
    build_account_env,
    load_config,
    resolve_account_config_dir,
    save_config,
)
from .detector import QuotaInfo, check_claude_cli, execute_claude_auth
from .logger import get_logger
from .scheduler import PingResult, Scheduler
from .state import StateManager

logger = get_logger()


@dataclass
class ServiceStatus:
    account_name: str
    running: bool
    next_ping_at: datetime | None
    last_ping_at: datetime | None
    ping_count: int
    fallback_enabled: bool
    fallback_time: str
    claude_executable: str
    claude_config_dir: str
    last_probe_at: datetime | None
    quota_hit: bool | None
    session_pct: int | None
    reset_at: datetime | None
    weekly_used: float | None = None
    weekly_reset_at: datetime | None = None
    auth_status: str = "inconnu"
    cli_available: bool = False
    quota_status_session: str = "—"
    quota_status_weekly: str = "—"
    probe_interval_minutes: int = 30


class AccountService:
    """Pilote le cycle probe/ping d'un seul compte Claude."""

    def __init__(
        self,
        account: AccountConfig,
        state: StateManager,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.account = account
        self.state = state
        self.scheduler = Scheduler(account, state)
        self._on_change = on_change
        self._thread: threading.Thread | None = None
        self._started = False

    @property
    def name(self) -> str:
        return self.account.name

    # Alias de compat pour le code qui référence encore `.config`
    @property
    def config(self) -> AccountConfig:
        return self.account

    def start(self) -> None:
        if self._started:
            return

        self.scheduler.logger.info("Démarrage du service ClaudePing...")
        self._thread = threading.Thread(
            target=self.scheduler.run,
            daemon=True,
            name=f"ClaudePingScheduler-{self.account.name}",
        )
        self._thread.start()
        self._started = True

    def stop(self, timeout: float = 5.0) -> None:
        if not self._started:
            return

        self.scheduler.logger.info("Arrêt du service ClaudePing...")
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
        status = check_claude_cli(
            self.account.claude_executable,
            env=self.scheduler.env,
            log=self.scheduler.logger,
        )
        self.scheduler.last_quota_info = status
        self.state.clear_last_notified_ping_at()
        return status

    def login_claude(self) -> tuple[bool, str]:
        success, message = execute_claude_auth(
            self.account.claude_executable,
            "login",
            env=self.scheduler.env,
            log=self.scheduler.logger,
        )
        if success:
            self.refresh_claude_status()
        return success, message

    def logout_claude(self) -> tuple[bool, str]:
        success, message = execute_claude_auth(
            self.account.claude_executable,
            "logout",
            env=self.scheduler.env,
            log=self.scheduler.logger,
        )
        if success:
            self.refresh_claude_status()
        return success, message

    def set_fallback_enabled(self, enabled: bool, persist: bool = True) -> None:
        self.account.fallback.enabled = enabled
        self.scheduler.logger.info(f"Fallback {'activé' if enabled else 'désactivé'} via UI.")
        if persist:
            self._notify_change()

    def set_fallback_time(self, time_str: str, persist: bool = True) -> None:
        self.account.fallback.time = time_str
        self.scheduler.logger.info(f"Heure de fallback mise à jour : {time_str}")
        if persist:
            self._notify_change()

    def set_claude_executable(self, path: str, persist: bool = True) -> None:
        self.account.claude_executable = path
        self.scheduler.logger.info(f"Chemin Claude Code CLI configuré : {path}")
        if persist:
            self._notify_change()

    def set_claude_config_dir(self, path: str, persist: bool = True) -> None:
        self.account.claude_config_dir = path.strip()
        self.scheduler.env = build_account_env(self.account)
        self.scheduler.logger.info(
            f"Dossier de config Claude configuré : {path or '(défaut du poste)'}"
        )
        if persist:
            self._notify_change()

    def set_probe_interval(self, minutes: int, persist: bool = True) -> None:
        self.account.probe.interval_minutes = max(1, minutes)
        self.scheduler.logger.info(
            f"Intervalle de probe configuré : {self.account.probe.interval_minutes} min"
        )
        if persist:
            self._notify_change()

    def _notify_change(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def get_status(self) -> ServiceStatus:
        quota_info = self.scheduler.last_quota_info
        return ServiceStatus(
            account_name=self.account.name,
            running=self.is_running(),
            next_ping_at=self.state.get_ping_scheduled_at(),
            last_ping_at=self.state.get_last_ping_at(),
            ping_count=self.state.get_ping_count(),
            fallback_enabled=self.account.fallback.enabled,
            fallback_time=self.account.fallback.time,
            claude_executable=self.account.claude_executable,
            claude_config_dir=self.account.claude_config_dir,
            last_probe_at=self.scheduler.last_probe_at,
            quota_hit=quota_info.quota_hit if quota_info is not None else None,
            session_pct=quota_info.session_pct if quota_info is not None else None,
            reset_at=quota_info.reset_at if quota_info is not None else None,
            weekly_used=quota_info.weekly_used if quota_info is not None else None,
            weekly_reset_at=quota_info.weekly_reset_at if quota_info is not None else None,
            auth_status=quota_info.auth_status if quota_info is not None else "inconnu",
            cli_available=quota_info.cli_available if quota_info is not None else False,
            quota_status_session=self._compute_quota_status(quota_info),
            quota_status_weekly=self._compute_quota_status(quota_info, weekly=True),
            probe_interval_minutes=self.account.probe.interval_minutes,
        )

    def _compute_quota_status(self, quota_info: QuotaInfo | None, weekly: bool = False) -> str:
        if quota_info is None:
            return "—"
        if quota_info.quota_hit:
            return "quota atteint"

        if weekly:
            if quota_info.weekly_used is None:
                return "—"
            return "ok" if quota_info.weekly_used < 100 else "quota atteint"

        if quota_info.session_pct is not None:
            return "ok" if quota_info.session_pct < 100 else "quota atteint"
        return "—"


def _open_in_file_manager(path: Path) -> None:
    """Ouvre `path` dans l'explorateur de fichiers du système d'exploitation."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _slugify_account_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    return slug or "compte"


class ClaudePingManager:
    """Gère l'ensemble des comptes Claude configurés, démarrés en parallèle."""

    def __init__(
        self,
        config_path: Path | str = Path("config.yaml"),
        base_dir: Path | str | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.base_dir = Path(base_dir) if base_dir is not None else self.config_path.parent
        self.config: AppConfig = load_config(self.config_path)
        self.accounts: dict[str, AccountService] = {}
        for account in self.config.accounts:
            self._create_account_service(account)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def start_all(self) -> None:
        for service in self.accounts.values():
            service.start()

    def stop_all(self, timeout: float = 5.0) -> None:
        for service in self.accounts.values():
            service.stop(timeout=timeout)

    def is_any_running(self) -> bool:
        return any(s.is_running() for s in self.accounts.values())

    # ------------------------------------------------------------------
    # Gestion des comptes
    # ------------------------------------------------------------------

    def _state_path_for(self, account: AccountConfig) -> Path:
        # Compat : le compte auto-migré depuis l'ancien format mono-compte
        # garde le fichier d'état historique (claudeping_state.json, sans
        # suffixe), pour ne rien casser chez les utilisateurs existants.
        #
        # ATTENTION : ce test compare le NOM AFFICHÉ du compte au sentinel
        # DEFAULT_ACCOUNT_NAME — donc si un compte explicite (format
        # multi-comptes) est un jour nommé exactement comme ce sentinel, il
        # hérite par erreur du fichier d'état legacy partagé et perd son
        # propre historique (vécu : renommer le sentinel en "DEFAULT" a fait
        # entrer en collision un compte réel nommé "DEFAULT", qui s'est
        # brusquement retrouvé avec ping_count=0). Ne changez DEFAULT_ACCOUNT_NAME
        # qu'en connaissance de cause, et ne renommez jamais un compte réel
        # exactement comme sa valeur actuelle ("default").
        if account.name == DEFAULT_ACCOUNT_NAME:
            return self.base_dir / "claudeping_state.json"
        return self.base_dir / f"claudeping_state_{_slugify_account_name(account.name)}.json"

    def _create_account_service(self, account: AccountConfig) -> AccountService:
        state = StateManager(self._state_path_for(account))
        service = AccountService(account, state, on_change=self._save_config)
        self.accounts[account.name] = service
        return service

    def add_account(self, name: str, claude_config_dir: str = "") -> AccountService:
        name = name.strip()
        if not name:
            raise ValueError("Le nom du compte ne peut pas être vide.")
        if name in self.accounts:
            raise ValueError(f"Un compte nommé '{name}' existe déjà.")

        account = AccountConfig(name=name, claude_config_dir=claude_config_dir.strip())
        self.config.accounts.append(account)
        service = self._create_account_service(account)
        self._save_config()
        service.start()
        logger.info(f"Compte '{name}' ajouté et démarré.")
        return service

    def remove_account(self, name: str) -> None:
        service = self.accounts.pop(name, None)
        if service is None:
            return
        service.stop()
        self.config.accounts = [a for a in self.config.accounts if a.name != name]
        self._save_config()
        logger.info(f"Compte '{name}' supprimé.")

    def open_account_config_dir(self, name: str) -> Path:
        service = self.accounts[name]
        path = resolve_account_config_dir(service.account)
        path.mkdir(parents=True, exist_ok=True)
        _open_in_file_manager(path)
        return path

    def get_status_all(self) -> dict[str, ServiceStatus]:
        return {name: svc.get_status() for name, svc in self.accounts.items()}

    # ------------------------------------------------------------------
    # Persistance
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        try:
            save_config(self.config, self.config_path)
            logger.info(f"Configuration sauvegardée dans {self.config_path.resolve()}")
        except Exception:
            logger.exception(
                f"Impossible de sauvegarder la configuration dans {self.config_path.resolve()}"
            )

    @classmethod
    def create(
        cls,
        config_path: Path | str = Path("config.yaml"),
        base_dir: Path | str | None = None,
    ) -> ClaudePingManager:
        try:
            return cls(config_path=config_path, base_dir=base_dir)
        except ConfigError as e:
            logger.error(f"Impossible de charger la configuration : {e}")
            raise
