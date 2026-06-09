"""
Gestion de la configuration YAML de ClaudePing.
Valide les champs obligatoires et fournit des valeurs par défaut.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


DEFAULT_CONFIG_PATH = Path("config.yaml")


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
class AppConfig:
    probe: ProbeConfig = field(default_factory=ProbeConfig)
    ping: PingConfig = field(default_factory=PingConfig)
    fallback: FallbackConfig = field(default_factory=FallbackConfig)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
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


def _merge(base: dict, override: dict) -> dict:
    """Merge récursif de deux dicts (override écrase base)."""
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """
    Charge et valide la configuration depuis un fichier YAML.

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

    try:
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Erreur de parsing YAML : {e}")

    if not isinstance(raw, dict):
        raise ConfigError("Le fichier de configuration doit être un dictionnaire YAML.")

    # Construction des dataclasses depuis le YAML
    try:
        probe_raw = raw.get("probe", {})
        ping_raw = raw.get("ping", {})
        fallback_raw = raw.get("fallback", {})
        notif_raw = raw.get("notifications", {})
        log_raw = raw.get("logging", {})

        probe = ProbeConfig(
            interval_minutes=int(probe_raw.get("interval_minutes", 30)),
            model=str(probe_raw.get("model", "haiku")),
            message=str(probe_raw.get("message", "reply with just: ok")),
        )

        ping = PingConfig(
            message=str(ping_raw.get("message", "reply with just: ok")),
            model=str(ping_raw.get("model", "haiku")),
        )

        fallback = FallbackConfig(
            enabled=bool(fallback_raw.get("enabled", True)),
            time=str(fallback_raw.get("time", "07:00")),
            timezone=str(fallback_raw.get("timezone", "Europe/Brussels")),
        )

        notifications = NotificationsConfig(
            enabled=bool(notif_raw.get("enabled", True)),
            on_quota_detected=bool(notif_raw.get("on_quota_detected", True)),
            on_ping_sent=bool(notif_raw.get("on_ping_sent", True)),
            on_error=bool(notif_raw.get("on_error", False)),
        )

        logging_cfg = LoggingConfig(
            level=str(log_raw.get("level", "INFO")),
            file=str(log_raw.get("file", "claudeping.log")),
            max_bytes=int(log_raw.get("max_bytes", 1_048_576)),
            backup_count=int(log_raw.get("backup_count", 3)),
        )

    except (TypeError, ValueError) as e:
        raise ConfigError(f"Valeur de configuration invalide : {e}")

    # Validations métier
    if probe.interval_minutes < 1:
        raise ConfigError("probe.interval_minutes doit être >= 1")

    _parse_time(fallback.time)          # valide le format HH:MM
    _validate_timezone(fallback.timezone)

    return AppConfig(
        probe=probe,
        ping=ping,
        fallback=fallback,
        notifications=notifications,
        logging=logging_cfg,
    )
