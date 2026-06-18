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
CLI_AUTH_TIMEOUT = 120  # browser OAuth can take longer

# Quota 100% atteint
_QUOTA_HIT = re.compile(
    r"you'?ve hit your (session|usage) limit",
    re.IGNORECASE,
)

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Regexes spécifiques par ligne pour ne pas mélanger session et hebdo
# "Current session: 76% used · resets Jun 18, 9:20pm (Europe/Brussels)"
_SESSION_LINE = re.compile(
    r"Current session:[^\n]*?(\d+)%[^\n]*?resets\s+([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}(?::\d{2})?(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_SESSION_LINE_TIME_ONLY = re.compile(
    r"Current session:[^\n]*?(\d+)%[^\n]*?resets\s+(\d{1,2}(?::\d{2})?(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)
# "Current week (all models): 86% used · resets Jun 22, 2pm (Europe/Brussels)"
_WEEKLY_LINE = re.compile(
    r"Current week[^\n]*?(\d+)%[^\n]*?resets\s+([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{1,2}(?::\d{2})?(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)
_WEEKLY_LINE_TIME_ONLY = re.compile(
    r"Current week[^\n]*?(\d+)%[^\n]*?resets\s+(\d{1,2}(?::\d{2})?(?:am|pm))\s*\(([^)]+)\)",
    re.IGNORECASE,
)

_AUTH_REQUIRED = re.compile(
    r"(not logged in|please login|login required|unauthorized|invalid auth|account required|must authenticate)",
    re.IGNORECASE,
)
_AUTH_OK = re.compile(
    r"(authenticated|logged in|account active|session active|Current session:)",
    re.IGNORECASE,
)


@dataclass
class QuotaInfo:
    """Résultat d'une interrogation /usage."""
    quota_hit: bool
    reset_at: datetime | None = None         # UTC — heure de reset de la session
    session_pct: int | None = None           # % session utilisé (0-100)
    weekly_used: float | None = None         # % hebdo utilisé
    weekly_reset_at: datetime | None = None  # UTC — heure de reset hebdo
    raw_output: str = ""
    auth_status: str = "inconnu"
    cli_available: bool = True


class DetectorError(Exception):
    """CLI inaccessible, timeout, etc."""


def _parse_time_str(time_str: str) -> tuple[int, int]:
    """'4:40am' → (4, 40),  '2pm' → (14, 0),  '2:00pm' → (14, 0)"""
    t = time_str.lower().strip()
    is_pm = t.endswith("pm")
    is_am = t.endswith("am")
    t = t.replace("am", "").replace("pm", "")
    if ":" in t:
        hour, minute = map(int, t.split(":"))
    else:
        hour, minute = int(t), 0
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


def _parse_reset_from_match(groups: tuple, with_date: bool) -> datetime | None:
    """Construit un datetime UTC depuis les groupes d'un match de regex reset."""
    try:
        if with_date:
            month_str, day_str, time_str, tz_str = groups
            month = _MONTH_MAP[month_str[:3].lower()]
            day = int(day_str)
            hour, minute = _parse_time_str(time_str)
            tz = ZoneInfo(tz_str)
            return _build_reset_datetime(hour, minute, month, day, tz)
        else:
            time_str, tz_str = groups
            hour, minute = _parse_time_str(time_str)
            tz = ZoneInfo(tz_str)
            return _build_reset_datetime(hour, minute, None, None, tz)
    except Exception as e:
        logger.warning(f"Parsing reset échoué : {e}")
        return None


def _detect_auth_status(output: str) -> str:
    if _AUTH_REQUIRED.search(output):
        return "déconnecté"
    if _AUTH_OK.search(output):
        return "connecté"
    return "inconnu"


def parse_usage_output(output: str) -> QuotaInfo:
    """Parse l'output de `claude -p "/usage"`."""

    auth_status = _detect_auth_status(output)

    if _QUOTA_HIT.search(output):
        logger.warning("Quota atteint détecté dans l'output.")
        return QuotaInfo(quota_hit=True, raw_output=output, auth_status=auth_status)

    # --- Session ---
    session_pct: int | None = None
    reset_at: datetime | None = None

    m = _SESSION_LINE.search(output)
    if m:
        session_pct = int(m.group(1))
        reset_at = _parse_reset_from_match(m.group(2, 3, 4, 5), with_date=True)
    else:
        m2 = _SESSION_LINE_TIME_ONLY.search(output)
        if m2:
            session_pct = int(m2.group(1))
            reset_at = _parse_reset_from_match(m2.group(2, 3), with_date=False)

    if reset_at is None and session_pct is None:
        logger.info("Aucune session active dans /usage (compteur à 0 ou non démarré).")

    # --- Hebdo ---
    weekly_used: float | None = None
    weekly_reset_at: datetime | None = None

    wm = _WEEKLY_LINE.search(output)
    if wm:
        weekly_used = float(wm.group(1))
        weekly_reset_at = _parse_reset_from_match(wm.group(2, 3, 4, 5), with_date=True)
    else:
        wm2 = _WEEKLY_LINE_TIME_ONLY.search(output)
        if wm2:
            weekly_used = float(wm2.group(1))
            weekly_reset_at = _parse_reset_from_match(wm2.group(2, 3), with_date=False)

    return QuotaInfo(
        quota_hit=False,
        reset_at=reset_at,
        session_pct=session_pct,
        weekly_used=weekly_used,
        weekly_reset_at=weekly_reset_at,
        raw_output=output,
        auth_status=auth_status,
    )


def _parse_cli_auth_status(stdout: str, stderr: str | None = None) -> str:
    raw = stdout + ("\n" + stderr if stderr else "")
    if _AUTH_REQUIRED.search(raw):
        return "déconnecté"
    if _AUTH_OK.search(raw):
        return "connecté"
    return "inconnu"


def _check_auth_status_json(executable: str) -> str | None:
    """Retourne l'état d'auth via `claude auth status` (JSON) ou None si indisponible."""
    executable = _resolve_executable(executable)
    try:
        result = subprocess.run(
            [executable, "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
            encoding="utf-8",
            errors="replace",
        )
        raw = result.stdout.strip()
        if raw:
            data = json.loads(raw)
            return "connecté" if data.get("loggedIn") else "déconnecté"
    except Exception:
        pass
    return None


def check_claude_cli(executable: str = "claude") -> QuotaInfo:
    """Vérifie la disponibilité du CLI et retourne l'état auth/quota si possible."""
    executable = _resolve_executable(executable)
    # Auth status fiable via `claude auth status` (JSON)
    auth_status_real = _check_auth_status_json(executable)

    cmd = [executable, "-p", "/usage", "--model", "haiku"]
    logger.debug(f"Vérification CLI : {' '.join(cmd)}")

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
        return QuotaInfo(
            quota_hit=False,
            raw_output="",
            auth_status="manquant",
            cli_available=False,
        )
    except subprocess.TimeoutExpired:
        return QuotaInfo(
            quota_hit=False,
            raw_output="",
            auth_status=auth_status_real or "timeout",
            cli_available=True,
        )
    except OSError as e:
        return QuotaInfo(
            quota_hit=False,
            raw_output=str(e),
            auth_status=auth_status_real or "erreur",
            cli_available=True,
        )

    output = result.stdout or ""
    error_output = result.stderr or ""
    auth_status = auth_status_real or _parse_cli_auth_status(output, error_output)

    if result.returncode != 0 and auth_status != "connecté":
        return QuotaInfo(
            quota_hit=False,
            raw_output=output + error_output,
            auth_status=auth_status,
            cli_available=True,
        )

    if not output.strip():
        output = result.stderr or output

    quota_info = parse_usage_output(output)
    quota_info.cli_available = True
    quota_info.auth_status = auth_status
    return quota_info


def _resolve_executable(executable: str) -> str:
    """Sur Windows, résout 'claude' → 'claude.cmd' si nécessaire."""
    import shutil
    import sys as _sys
    if _sys.platform == "win32" and not executable.lower().endswith((".exe", ".cmd", ".bat")):
        found = shutil.which(executable + ".cmd") or shutil.which(executable + ".exe")
        if found:
            return found
    return executable


def execute_claude_auth(executable: str, action: str) -> tuple[bool, str]:
    """Tente d'exécuter `claude auth login` ou `claude auth logout`."""
    executable = _resolve_executable(executable)
    cmd = [executable, "auth", action]
    timeout = CLI_AUTH_TIMEOUT if action == "login" else CLI_TIMEOUT
    logger.debug(f"Exécution auth CLI : {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        output = (result.stdout or "") + (result.stderr or "")
        success = result.returncode == 0
        return success, output.strip()
    except FileNotFoundError:
        return False, "Claude Code CLI introuvable."
    except subprocess.TimeoutExpired:
        return False, f"Timeout après {timeout}s."
    except OSError as e:
        return False, f"Erreur OS : {e}"


def run_usage_check(executable: str = "claude") -> QuotaInfo:
    """
    Lance `claude -p "/usage"` et retourne l'état du quota.

    Args:
        executable: Chemin vers l'exécutable Claude Code CLI.

    Returns:
        QuotaInfo avec reset_at en UTC si parsé.

    Raises:
        DetectorError: CLI inaccessible, timeout.
    """
    status = check_claude_cli(executable)
    if not status.cli_available:
        raise DetectorError("Claude Code CLI introuvable.")
    if status.auth_status == "déconnecté":
        raise DetectorError("Claude Code CLI non connecté ou session expirée.")
    if status.auth_status == "timeout":
        raise DetectorError(f"Timeout après {CLI_TIMEOUT}s.")
    if status.auth_status == "erreur":
        raise DetectorError("Erreur lors de l'exécution du CLI Claude.")

    return status


