"""
Détection du quota atteint via l'output de Claude Code CLI.
Parse l'heure de reset depuis le message de quota.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .logger import get_logger

logger = get_logger()

# Pattern du message de quota Claude Code :
# "You've hit your session limit · resets 2:50pm (Europe/Brussels)"
# Le séparateur peut être · (U+00B7) ou • (U+2022) ou un tiret
QUOTA_PATTERN = re.compile(
    r"you'?ve hit your session limit"
    r"[\s\S]*?"                          # séparateur flexible
    r"resets\s+(\d{1,2}:\d{2}(?:am|pm))"
    r"\s+\(([^)]+)\)",
    re.IGNORECASE,
)

# Timeout pour l'appel CLI (secondes)
CLI_TIMEOUT = 30


@dataclass
class QuotaInfo:
    """Résultat d'une détection de quota."""
    quota_hit: bool
    reset_at: datetime | None = None      # datetime UTC si quota_hit=True
    raw_message: str = ""


class DetectorError(Exception):
    """Erreur lors de la détection (CLI non disponible, timeout, etc.)."""


def _parse_reset_time(time_str: str, tz_str: str) -> datetime:
    """
    Parse "2:50pm" + "Europe/Brussels" → datetime UTC du prochain reset.

    La date n'est pas dans le message, on assume que le reset est
    aujourd'hui ou demain (si l'heure est déjà passée).

    Args:
        time_str: Heure au format "H:MMam" ou "H:MMpm".
        tz_str: Nom de timezone IANA (ex: "Europe/Brussels").

    Returns:
        datetime en UTC.

    Raises:
        ValueError: Si le parsing échoue.
    """
    tz = ZoneInfo(tz_str)
    now_local = datetime.now(tz)

    # Parse "2:50pm" → heure et minute
    time_clean = time_str.lower().strip()
    is_pm = time_clean.endswith("pm")
    is_am = time_clean.endswith("am")
    time_clean = time_clean.replace("am", "").replace("pm", "")
    hour, minute = map(int, time_clean.split(":"))

    if is_pm and hour != 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0

    # Construire le datetime local pour aujourd'hui
    reset_local = now_local.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )

    # Si l'heure est déjà passée aujourd'hui → c'est demain
    if reset_local <= now_local:
        from datetime import timedelta
        reset_local = reset_local + timedelta(days=1)

    return reset_local.astimezone(timezone.utc)


def parse_quota_from_output(output: str) -> QuotaInfo:
    """
    Analyse l'output CLI pour détecter le message de quota.

    Args:
        output: Contenu stdout + stderr de la commande claude.

    Returns:
        QuotaInfo avec quota_hit=True et reset_at parsé si quota détecté.
    """
    match = QUOTA_PATTERN.search(output)
    if not match:
        return QuotaInfo(quota_hit=False)

    time_str = match.group(1)   # ex: "2:50pm"
    tz_str = match.group(2)     # ex: "Europe/Brussels"

    try:
        reset_at = _parse_reset_time(time_str, tz_str)
        logger.info(
            "Quota détecté",
            extra={
                "reset_at_utc": reset_at.isoformat(),
                "reset_time_raw": time_str,
                "reset_tz": tz_str,
            },
        )
        return QuotaInfo(quota_hit=True, reset_at=reset_at, raw_message=match.group(0))
    except (ValueError, KeyError) as e:
        logger.warning(f"Quota détecté mais parsing de l'heure échoué : {e}")
        return QuotaInfo(quota_hit=True, reset_at=None, raw_message=match.group(0))


def run_probe(model: str, message: str) -> QuotaInfo:
    """
    Lance une commande probe Claude Code CLI et analyse le résultat.

    Args:
        model: Modèle à utiliser (ex: "haiku").
        message: Message à envoyer.

    Returns:
        QuotaInfo.

    Raises:
        DetectorError: Si le CLI est inaccessible ou timeout.
    """
    cmd = ["claude", "-p", message, "--model", model]

    logger.debug(f"Probe CLI : {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        raise DetectorError(
            "Claude Code CLI introuvable. "
            "Vérifiez que 'claude' est installé et dans le PATH."
        )
    except subprocess.TimeoutExpired:
        raise DetectorError(f"Timeout après {CLI_TIMEOUT}s lors du probe CLI.")
    except OSError as e:
        raise DetectorError(f"Erreur OS lors du probe : {e}")

    # On analyse stdout + stderr combinés car Claude Code
    # peut écrire dans l'un ou l'autre selon la version
    combined_output = result.stdout + "\n" + result.stderr

    logger.debug(
        "Résultat probe",
        extra={
            "returncode": result.returncode,
            "stdout_len": len(result.stdout),
            "stderr_len": len(result.stderr),
        },
    )

    return parse_quota_from_output(combined_output)
