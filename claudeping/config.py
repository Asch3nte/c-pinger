"""
Gestion de la configuration YAML de ClaudePing.
Valide les champs obligatoires et fournit des valeurs par défaut.

Le fichier config.yaml décrit une liste de "comptes" (accounts), chacun
représentant un compte Claude distinct avec son propre cycle probe/ping.
Un compte peut pointer vers un dossier de config Claude isolé
(claude_config_dir, transmis au CLI via la variable d'environnement
CLAUDE_CONFIG_DIR) afin de faire cohabiter plusieurs comptes Claude sur
la même machine. Laissé vide, le compte utilise la config Claude par
défaut du poste.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

yaml = None
try:
    import yaml
except ImportError:
    pass


DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_ACCOUNT_NAME = "default"


@dataclass
class ProbeConfig:
    interval_minutes: int = 30
    model: str = "haiku"
    message: str = "reply with just: ok"


@dataclass
class PingConfig:
    message: str = "reply with just: ok"
    model: str = "haiku"


@dataclass
class FallbackConfig:
    enabled: bool = True
    time: str = "07:00"          # format HH:MM
    timezone: str = "Europe/Brussels"


@dataclass
class NotificationsConfig:
    enabled: bool = True
    on_quota_detected: bool = True
    on_ping_sent: bool = True
    on_error: bool = False        # moins verbeux par défaut


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "claudeping.log"
    max_bytes: int = 1_048_576
    backup_count: int = 3


@dataclass
class AccountConfig:
    name: str = DEFAULT_ACCOUNT_NAME
    # Dossier de config Claude isolé pour ce compte (CLAUDE_CONFIG_DIR).
    # Vide = config Claude par défaut de la machine (~/.claude).
    claude_config_dir: str = ""
    claude_executable: str = "claude"
    probe: ProbeConfig = field(default_factory=ProbeConfig)
    ping: PingConfig = field(default_factory=PingConfig)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


@dataclass
class AppConfig:
    accounts: list[AccountConfig] = field(default_factory=list)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


class ConfigError(Exception):
    """Erreur de configuration."""


def _parse_time(value: str) -> tuple[int, int]:
    """
    Parse une heure au format HH:MM.
    Retourne (hour, minute) ou lève ConfigError.
    """
    try:
        h, m = value.strip().split(":")
        hour, minute = int(h), int(m)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except ValueError:
        raise ConfigError(f"Format d'heure invalide : '{value}' (attendu HH:MM)")


def _validate_timezone(tz: str) -> None:
    """Lève ConfigError si la timezone est invalide."""
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        raise ConfigError(f"Timezone invalide : '{tz}'")


def _build_probe(raw: dict) -> ProbeConfig:
    return ProbeConfig(
        interval_minutes=int(raw.get("interval_minutes", 30)),
        model=str(raw.get("model", "haiku")),
        message=str(raw.get("message", "reply with just: ok")),
    )


def _build_ping(raw: dict) -> PingConfig:
    return PingConfig(
        message=str(raw.get("message", "reply with just: ok")),
        model=str(raw.get("model", "haiku")),
    )


def _build_fallback(raw: dict) -> FallbackConfig:
    return FallbackConfig(
        enabled=bool(raw.get("enabled", True)),
        time=str(raw.get("time", "07:00")),
        timezone=str(raw.get("timezone", "Europe/Brussels")),
    )


def _build_notifications(raw: dict) -> NotificationsConfig:
    return NotificationsConfig(
        enabled=bool(raw.get("enabled", True)),
        on_quota_detected=bool(raw.get("on_quota_detected", True)),
        on_ping_sent=bool(raw.get("on_ping_sent", True)),
        on_error=bool(raw.get("on_error", False)),
    )


def _build_account(raw: dict, default_name: str) -> AccountConfig:
    name = str(raw.get("name") or default_name).strip()
    if not name:
        raise ConfigError("Le nom d'un compte ne peut pas être vide.")

    account = AccountConfig(
        name=name,
        claude_config_dir=str(raw.get("claude_config_dir", "") or ""),
        claude_executable=str(raw.get("claude_executable", "claude")),
        probe=_build_probe(raw.get("probe", {}) or {}),
        ping=_build_ping(raw.get("ping", {}) or {}),
        fallback=_build_fallback(raw.get("fallback", {}) or {}),
        notifications=_build_notifications(raw.get("notifications", {}) or {}),
    )

    if account.probe.interval_minutes < 1:
        raise ConfigError(f"[{name}] probe.interval_minutes doit être >= 1")

    _parse_time(account.fallback.time)
    _validate_timezone(account.fallback.timezone)

    return account


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """
    Charge et valide la configuration depuis un fichier YAML.

    Supporte deux formats :
    - Nouveau format multi-comptes : clé `accounts` (liste).
    - Ancien format à plat (un seul compte implicite) : migré en mémoire
      vers un unique compte nommé "default", sans réécrire le fichier.

    Args:
        path: Chemin vers le fichier config.yaml.

    Returns:
        AppConfig validé.

    Raises:
        ConfigError: Si le fichier est invalide ou introuvable.
    """
    config_path = Path(path)

    if not config_path.exists():
        raise ConfigError(
            f"Fichier de configuration introuvable : {config_path}\n"
            f"Copiez config.yaml.example vers config.yaml et adaptez-le."
        )

    if yaml is None:
        raise ConfigError(
            "Le module PyYAML n'est pas installé. "
            "Installez les dépendances : python3 -m pip install -r requirements.txt"
        )

    try:
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Erreur de parsing YAML : {e}")

    if not isinstance(raw, dict):
        raise ConfigError("Le fichier de configuration doit être un dictionnaire YAML.")

    try:
        log_raw = raw.get("logging", {}) or {}
        logging_cfg = LoggingConfig(
            level=str(log_raw.get("level", "INFO")),
            file=str(log_raw.get("file", "claudeping.log")),
            max_bytes=int(log_raw.get("max_bytes", 1_048_576)),
            backup_count=int(log_raw.get("backup_count", 3)),
        )

        accounts_raw = raw.get("accounts")
        if accounts_raw is not None:
            if not isinstance(accounts_raw, list) or not accounts_raw:
                raise ConfigError("La clé 'accounts' doit être une liste non vide.")
            accounts = [
                _build_account(acc_raw or {}, default_name=f"compte-{i + 1}")
                for i, acc_raw in enumerate(accounts_raw)
            ]
        else:
            # Ancien format à plat : un seul compte implicite "default".
            accounts = [_build_account(raw, default_name=DEFAULT_ACCOUNT_NAME)]

    except (TypeError, ValueError) as e:
        raise ConfigError(f"Valeur de configuration invalide : {e}")

    names = [a.name for a in accounts]
    if len(names) != len(set(names)):
        raise ConfigError(f"Des comptes ont le même nom : {names}")

    return AppConfig(accounts=accounts, logging=logging_cfg)


def _account_to_dict(account: AccountConfig) -> dict:
    return {
        "name": account.name,
        "claude_config_dir": account.claude_config_dir,
        "claude_executable": account.claude_executable,
        "probe": {
            "interval_minutes": account.probe.interval_minutes,
            "model": account.probe.model,
            "message": account.probe.message,
        },
        "ping": {
            "message": account.ping.message,
            "model": account.ping.model,
        },
        "fallback": {
            "enabled": account.fallback.enabled,
            "time": account.fallback.time,
            "timezone": account.fallback.timezone,
        },
        "notifications": {
            "enabled": account.notifications.enabled,
            "on_quota_detected": account.notifications.on_quota_detected,
            "on_ping_sent": account.notifications.on_ping_sent,
            "on_error": account.notifications.on_error,
        },
    }


def save_config(config: AppConfig, path: str | Path = DEFAULT_CONFIG_PATH) -> None:
    """Sauvegarde la configuration (format multi-comptes) dans un fichier YAML."""
    if yaml is None:
        raise ConfigError(
            "Le module PyYAML n'est pas installé. "
            "Installez les dépendances : python3 -m pip install -r requirements.txt"
        )

    data = {
        "accounts": [_account_to_dict(a) for a in config.accounts],
        "logging": {
            "level": config.logging.level,
            "file": config.logging.file,
            "max_bytes": config.logging.max_bytes,
            "backup_count": config.logging.backup_count,
        },
    }

    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def build_account_env(account: AccountConfig) -> dict[str, str] | None:
    """
    Construit l'environnement des sous-process `claude` pour ce compte.

    Retourne None si le compte utilise la config Claude par défaut de la
    machine (aucune variable à surcharger), sinon une copie de
    l'environnement courant avec CLAUDE_CONFIG_DIR pointé sur le dossier
    de config isolé de ce compte.
    """
    if not account.claude_config_dir.strip():
        return None
    resolved = Path(account.claude_config_dir).expanduser()
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = str(resolved)
    return env


def resolve_account_config_dir(account: AccountConfig) -> Path:
    """Retourne le dossier de config Claude effectif de ce compte."""
    if account.claude_config_dir.strip():
        return Path(account.claude_config_dir).expanduser()
    return Path.home() / ".claude"
