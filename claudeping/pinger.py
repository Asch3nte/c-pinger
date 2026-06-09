"""
Envoi du ping via Claude Code CLI au moment du reset du quota.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from .detector import CLI_TIMEOUT, parse_usage_output
from .logger import get_logger

logger = get_logger()


@dataclass
class PingResult:
    success: bool
    sent_at: datetime
    response: str = ""
    error: str = ""
    quota_still_active: bool = False


def send_ping(model: str, message: str) -> PingResult:
    cmd = ["claude", "-p", message, "--model", model]
    sent_at = datetime.now(timezone.utc)

    logger.info("Envoi du ping", extra={"ping_model": model, "ping_message": message})

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
        error_msg = "Claude Code CLI introuvable (commande 'claude' absente du PATH)."
        logger.error(error_msg)
        return PingResult(success=False, sent_at=sent_at, error=error_msg)
    except subprocess.TimeoutExpired:
        error_msg = f"Timeout après {CLI_TIMEOUT}s lors du ping."
        logger.error(error_msg)
        return PingResult(success=False, sent_at=sent_at, error=error_msg)
    except OSError as e:
        error_msg = f"Erreur OS lors du ping : {e}"
        logger.error(error_msg)
        return PingResult(success=False, sent_at=sent_at, error=error_msg)

    combined_output = result.stdout + "\n" + result.stderr

    # Parser directement l'output du ping — pas de second appel CLI
    quota_info = parse_usage_output(combined_output)
    if quota_info.quota_hit:
        logger.warning(
            "Ping envoyé mais quota toujours actif",
            extra={"reset_at": quota_info.reset_at.isoformat() if quota_info.reset_at else None},
        )
        return PingResult(
            success=False,
            sent_at=sent_at,
            error="Quota toujours actif au moment du ping.",
            quota_still_active=True,
        )

    if result.returncode != 0:
        error_msg = f"CLI retourné code {result.returncode} : {result.stderr[:200]}"
        logger.error(error_msg)
        return PingResult(success=False, sent_at=sent_at, error=error_msg)

    response = result.stdout.strip()
    logger.info("Ping réussi", extra={"response": response[:100], "returncode": result.returncode})
    return PingResult(success=True, sent_at=sent_at, response=response)
