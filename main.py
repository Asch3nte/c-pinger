"""Point d'entrée principal de ClaudePing.

Usage :
    python main.py --service       # Lance le backend en mode service
    python main.py --ui            # Lance l'interface graphique
    python main.py status          # Affiche l'état CLI
    python main.py ping-now        # Force un ping immédiat
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

from claudeping.config import ConfigError, load_config
from claudeping.logger import get_logger, install_gui_log_handler, setup_logger
from claudeping.notifier import notify_service_started
from claudeping.service import ClaudePingService

try:
    from gui import launch_ui
except ImportError:
    launch_ui = None


if getattr(sys, "frozen", False):
    # Exécutable PyInstaller — config dans le dossier de l'exe
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.yaml"
STATE_PATH = BASE_DIR / "claudeping_state.json"


def _create_default_config(config_path: Path) -> None:
    """Crée un config.yaml depuis le template embarqué (mode bundle)."""
    import shutil as _shutil

    # Chercher config.yaml.example à côté de l'exe ou dans le bundle
    candidates = [
        config_path.parent / "config.yaml.example",
        Path(getattr(sys, "_MEIPASS", "")) / "config.yaml.example",
    ]
    for src in candidates:
        if src.exists():
            _shutil.copy(src, config_path)
            return

    # Fallback : écrire un template minimal
    config_path.write_text(
        "probe:\n  interval_minutes: 30\n  model: haiku\n  message: 'reply with just: ok'\n"
        "ping:\n  message: 'reply with just: ok'\n  model: haiku\n"
        "fallback:\n  enabled: true\n  time: '07:00'\n  timezone: Europe/Brussels\n"
        "notifications:\n  enabled: true\n  on_quota_detected: true\n  on_ping_sent: true\n  on_error: false\n"
        "logging:\n  level: INFO\n  file: claudeping.log\n  max_bytes: 1048576\n  backup_count: 3\n"
        "claude_executable: claude\n",
        encoding="utf-8",
    )


def print_usage() -> None:
    print(__doc__)


def cmd_status(service: ClaudePingService) -> None:
    status = service.get_status()
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(service.config.fallback.timezone)
    now = datetime.now(tz)

    scheduled = status.next_ping_at
    last_ping = status.last_ping_at

    scheduled_local = (
        scheduled.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        if scheduled else "—"
    )
    last_ping_local = (
        last_ping.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
        if last_ping else "—"
    )

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

    mode = "Intelligent" if not service.config.fallback.enabled else "Intelligent + Fallback"

    print()
    print("╭─────────────────────────────────────────────────────╮")
    print("│              ClaudePing — Dashboard                  │")
    print("├─────────────────────────────────────────────────────╮")
    print(f"│  Heure locale    : {now.strftime('%Y-%m-%d %H:%M:%S %Z'):<33}│")
    print(f"│  Mode            : {mode:<33}│")
    print(f"│  Probe interval  : {service.config.probe.interval_minutes} min{'':<28}│")
    print("├─────────────────────────────────────────────────────╮")
    print(f"│  Prochain ping   : {scheduled_local:<33}│")
    print(f"│  Dans            : {time_left:<33}│")
    print(f"│  Dernier ping    : {last_ping_local:<33}│")
    print(f"│  Total pings     : {status.ping_count:<33}│")
    print("╰─────────────────────────────────────────────────────╯")
    print()


def cmd_ping_now(service: ClaudePingService) -> None:
    result = service.trigger_ping_now()
    if result.success:
        print(f"✅ Ping réussi. Réponse : {result.response}")
    else:
        print(f"❌ Ping échoué : {result.error}")


def run_service(service: ClaudePingService) -> None:
    logger = get_logger()
    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info(f"Signal {signum} reçu, arrêt propre...")
        service.stop()
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    service.start()
    stop_event.wait()


def main() -> None:
    if len(sys.argv) == 1:
        # Double-clic sur le bundle → UI ; ligne de commande sans arg → service
        cmd = "--ui" if getattr(sys, "frozen", False) else "--service"
    else:
        cmd = sys.argv[1].lower()

    if cmd in ("-h", "--help", "help"):
        print_usage()
        return

    if cmd not in ("--service", "service", "--ui", "ui", "status", "ping-now", "ping_now"):
        print(f"Commande inconnue : '{cmd}'")
        print_usage()
        sys.exit(1)

    # Si le config n'existe pas et qu'on est en bundle, on le crée depuis l'exemple intégré
    if not CONFIG_PATH.exists() and getattr(sys, "frozen", False):
        _create_default_config(CONFIG_PATH)

    try:
        config = load_config(CONFIG_PATH)
    except ConfigError as e:
        print(f"[ERREUR CONFIG] {e}", file=sys.stderr)
        sys.exit(1)

    setup_logger(
        log_file=config.logging.file,
        level=config.logging.level,
        max_bytes=config.logging.max_bytes,
        backup_count=config.logging.backup_count,
    )
    logger = get_logger()

    service = ClaudePingService(config_path=CONFIG_PATH, state_path=STATE_PATH)

    if cmd == "status":
        cmd_status(service)
        return

    if cmd in ("ping-now", "ping_now"):
        cmd_ping_now(service)
        return

    if cmd in ("--ui", "ui"):
        if launch_ui is None:
            logger.error("UI non disponible : installez PySide6 pour l'exécuter.")
            print("UI non disponible : installez PySide6 avec 'pip install PySide6'.")
            sys.exit(1)
        install_gui_log_handler()
        logger.info("Lancement de l'interface graphique...")
        launch_ui(service)
        return

    if cmd in ("--service", "service"):
        logger.info("ClaudePing démarré en mode service.")
        if config.notifications.enabled:
            notify_service_started()
        run_service(service)
        logger.info("ClaudePing arrêté.")


if __name__ == "__main__":
    main()
