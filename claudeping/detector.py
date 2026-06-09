"""
Détection du quota et récupération de l'heure de reset via `claude -p "/usage"`.
"""

from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from .logger import get_logger

logger = get_logger()

CLI_TIMEOUT = 30

# "resets Jun 10, 4:40am (Europe/Brussels)"  ← avec date
# "resets 4:40am (Europe/Brussels)"           ← sans date (même jour)
_RESET_WITH_DATE = re.compile(
    r"resets\s+([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}:\d{2}(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_RESET_TIME_ONLY = re.compile(
    r"resets\s+(\d{1,2}:\d{2}(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)

# Quota 100% atteint — message probable (à ajuster si nécessaire)
_QUOTA_HIT = re.compile(
    r"you'?ve hit your (session|usage) limit",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


@dataclass
class QuotaInfo:
    """Résultat d'une interrogation /usage."""
    quota_hit: bool
    reset_at: datetime | None = None   # UTC
    session_pct: int | None = None     # % utilisé (0-100)
    raw_output: str = ""


class DetectorError(Exception):
    """CLI inaccessible, timeout, etc."""


def _parse_time_str(time_str: str) -> tuple[int, int]:
    """'4:40am' → (4, 40),  '2:00pm' → (14, 0)"""
    t = time_str.lower().strip()
    is_pm = t.endswith("pm")
    is_am = t.endswith("am")
    t = t.replace("am", "").replace("pm", "")
    hour, minute = map(int, t.split(":"))
    if is_pm and hour != 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0
    return hour, minute


def _build_reset_datetime(hour: int, minute: int, month: int | None,
                           day: int | None, tz: ZoneInfo) -> datetime:
    """Construit le datetime UTC du reset."""
    now = datetime.now(tz)

    if month is not None and day is not None:
        # Date explicite dans le message
        year = now.year
        dt = datetime(year, month, day, hour, minute, tzinfo=tz)
        # Si la date est déjà passée cette année → année suivante
        if dt <= now:
            dt = dt.replace(year=year + 1)
    else:
        # Pas de date → aujourd'hui ou demain
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if dt <= now:
            dt += timedelta(days=1)

    return dt.astimezone(timezone.utc)


def parse_usage_output(output: str) -> QuotaInfo:
    """Parse l'output de `claude -p "/usage"`."""

    # Quota atteint ?
    if _QUOTA_HIT.search(output):
        logger.warning("Quota atteint détecté dans l'output.")
        return QuotaInfo(quota_hit=True, raw_output=output)

    # % de session utilisé
    pct_match = re.search(r"Current session:\s*(\d+)%", output, re.IGNORECASE)
    session_pct = int(pct_match.group(1)) if pct_match else None

    # Heure de reset — format avec date en priorité
    reset_at = None
    m = _RESET_WITH_DATE.search(output)
    if m:
        month_str, day_str, time_str, tz_str = m.group(1), m.group(2), m.group(3), m.group(4)
        try:
            month = _MONTH_MAP[month_str[:3].lower()]
            day = int(day_str)
            hour, minute = _parse_time_str(time_str)
            tz = ZoneInfo(tz_str)
            reset_at = _build_reset_datetime(hour, minute, month, day, tz)
        except Exception as e:
            logger.warning(f"Parsing reset (avec date) échoué : {e}")
    else:
        m2 = _RESET_TIME_ONLY.search(output)
        if m2:
            time_str, tz_str = m2.group(1), m2.group(2)
            try:
                hour, minute = _parse_time_str(time_str)
                tz = ZoneInfo(tz_str)
                reset_at = _build_reset_datetime(hour, minute, None, None, tz)
            except Exception as e:
                logger.warning(f"Parsing reset (heure seule) échoué : {e}")

    if reset_at:
        logger.info(
            "Reset détecté via /usage",
            extra={
                "reset_at_utc": reset_at.isoformat(),
                "session_pct": session_pct,
            },
        )
    else:
        logger.warning("Impossible de parser l'heure de reset depuis /usage.")

    return QuotaInfo(
        quota_hit=False,
        reset_at=reset_at,
        session_pct=session_pct,
        raw_output=output,
    )


def run_usage_check() -> QuotaInfo:
    """
    Lance `claude -p "/usage"` et retourne l'état du quota.

    Returns:
        QuotaInfo avec reset_at en UTC si parsé.

    Raises:
        DetectorError: CLI inaccessible ou timeout.
    """
    cmd = ["claude", "-p", "/usage", "--output-format", "json"]
    logger.debug(f"Usage check : {' '.join(cmd)}")

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
        raise DetectorError("Claude Code CLI introuvable.")
    except subprocess.TimeoutExpired:
        raise DetectorError(f"Timeout après {CLI_TIMEOUT}s.")
    except OSError as e:
        raise DetectorError(f"Erreur OS : {e}")

    # Parser le JSON
    try:
        data = json.loads(result.stdout)
        text_output = data.get("result", "")
    except (json.JSONDecodeError, AttributeError):
        # Fallback sur stdout brut si JSON invalide
        logger.warning("JSON invalide, fallback sur stdout brut.")
        text_output = result.stdout

    logger.debug("Output /usage", extra={"output": text_output[:500]})

    # Pas de session active : result vide ou sans "Current session:"
    if not re.search(r"Current session:", text_output, re.IGNORECASE):
        logger.info("Pas de session active (aucun compteur démarré).")
        return QuotaInfo(quota_hit=False, reset_at=None, session_pct=0, raw_output=text_output)

    return parse_usage_output(text_output)


# Rétrocompatibilité : run_probe redirige vers run_usage_check
def run_probe(model: str = "", message: str = "") -> QuotaInfo:
    """Alias déprécié → run_usage_check."""
    return run_usage_check()
