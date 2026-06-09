"""
Point d'entrée principal de ClaudePing.

Usage :
    python main.py              # Lance le service
    python main.py status       # Affiche le dashboard CLI
    python main.py ping-now     # Force un ping immédiat
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

from claudeping.config import ConfigError, load_config
from claudeping.logger import setup_logger, get_logger
from claudeping.notifier import notify_service_started
from claudeping.scheduler import Scheduler
from claudeping.state import StateManager


CONFIG_PATH = Path("config.yaml")
STATE_PATH = Path("claudeping_state.json")


def cmd_status(state: StateManager, config) -> None:
    """Affiche le dashboard CLI."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(config.fallback.timezone)
    now = datetime.now(tz)

    scheduled = state.get_ping_scheduled_at()
    last_ping = state.get_last_ping_at()
    ping_count = state.get_ping_count()

    scheduled_local = (
        scheduled.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        if scheduled else "—"
    )
    last_ping_local = (
        last_ping.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        if last_ping else "—"
    )

    # Calcul du temps restant avant le prochain ping
    if scheduled:
        delta = scheduled - datetime.now(timezone.utc)
        total_sec = int(delta.total_seconds())
        if total_sec > 0:
            h, rem = divmod(total_sec, 3600)
            m, s = divmod(rem, 60)
            time_left = f"{h}h {m:02d}m {s:02d}s"
        else:
            time_left = "imminent"
    else:
        time_left = "—"

    mode = "Intelligent" if not config.fallback.enabled else "Intelligent + Fallback"

    print()
    print("╭─────────────────────────────────────────────────────╮")
    print("│              ClaudePing — Dashboard                  │")
    print("├─────────────────────────────────────────────────────┤")
    print(f"│  Heure locale    : {now.strftime('%Y-%m-%d %H:%M:%S %Z'):<33}│")
    print(f"│  Mode            : {mode:<33}│")
    print(f"│  Probe interval  : {config.probe.interval_minutes} min{'':<28}│")
    print("├─────────────────────────────────────────────────────┤")
    print(f"│  Prochain ping   : {scheduled_local:<33}│")
    print(f"│  Dans            : {time_left:<33}│")
    print(f"│  Dernier ping    : {last_ping_local:<33}│")
    print(f"│  Total pings     : {ping_count:<33}│")
    print("╰─────────────────────────────────────────────────────╯")
    print()


def cmd_ping_now(config, state: StateManager) -> None:
    """Force un ping immédiat."""
    from claudeping.pinger import send_ping
    from zoneinfo import ZoneInfo

    logger = get_logger()
    logger.info("Ping forcé manuellement.")
    result = send_ping(config.ping.model, config.ping.message)
    tz = ZoneInfo(config.fallback.timezone)

    if result.success:
        sent_local = result.sent_at.astimezone(tz).strftime("%H:%M:%S")
        print(f"✅ Ping réussi à {sent_local}. Réponse : {result.response}")
        state.record_ping(result.sent_at)
    else:
        print(f"❌ Ping échoué : {result.error}")


def main() -> None:
    # Chargement de la config
    try:
        config = load_config(CONFIG_PATH)
    except ConfigError as e:
        print(f"[ERREUR CONFIG] {e}", file=sys.stderr)
        sys.exit(1)

    # Setup du logger
    setup_logger(
        log_file=config.logging.file,
        level=config.logging.level,
        max_bytes=config.logging.max_bytes,
        backup_count=config.logging.backup_count,
    )
    logger = get_logger()

    # État persistant
    state = StateManager(STATE_PATH)

    # Gestion des sous-commandes
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "status":
            cmd_status(state, config)
            return
        elif cmd == "ping-now":
            cmd_ping_now(config, state)
            return
        else:
            print(f"Commande inconnue : '{cmd}'")
            print("Usage : python main.py [status|ping-now]")
            sys.exit(1)

    # Mode service
    logger.info("ClaudePing démarré en mode service.")
    if config.notifications.enabled:
        notify_service_started()

    scheduler = Scheduler(config, state)

    # Gestion du signal SIGTERM / SIGINT pour arrêt propre
    def _shutdown(signum, frame):
        logger.info(f"Signal {signum} reçu, arrêt propre...")
        scheduler.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        scheduler.run()
    except Exception as e:
        logger.exception(f"Erreur fatale : {e}")
        sys.exit(1)

    logger.info("ClaudePing arrêté.")


if __name__ == "__main__":
    main()
