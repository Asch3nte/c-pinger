"""Point d'entrée principal de ClaudePing.

Usage :
    python main.py --service       # Lance le backend en mode service (tous les comptes)
    python main.py --ui            # Lance l'interface graphique
    python main.py status          # Affiche l'état CLI de chaque compte
    python main.py ping-now        # Force un ping immédiat sur chaque compte
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

from claudeping.config import ConfigError, load_config
from claudeping.logger import get_logger, install_gui_log_handler, setup_logger
from claudeping.notifier import notify_error, notify_service_started
from claudeping.service import AccountService, ClaudePingManager
from claudeping.singleton import SingleInstanceError, acquire_single_instance_lock

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
LOCK_PATH = BASE_DIR / "claudeping.lock"


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

    # Fallback : écrire un template minimal (un compte "default")
    config_path.write_text(
        "accounts:\n"
        "  - name: DEFAULT\n"
        "    claude_config_dir: ''\n"
        "    claude_executable: claude\n"
        "    probe:\n"
        "      interval_minutes: 30\n"
        "      model: haiku\n"
        "      message: 'reply with just: ok'\n"
        "    ping:\n"
        "      message: 'reply with just: ok'\n"
        "      model: haiku\n"
        "    fallback:\n"
        "      enabled: true\n"
        "      time: '07:00'\n"
        "      timezone: Europe/Brussels\n"
        "    notifications:\n"
        "      enabled: true\n"
        "      on_quota_detected: true\n"
        "      on_ping_sent: true\n"
        "      on_error: false\n"
        "logging:\n"
        "  level: INFO\n"
        "  file: claudeping.log\n"
        "  max_bytes: 1048576\n"
        "  backup_count: 3\n",
        encoding="utf-8",
    )


def print_usage() -> None:
    print(__doc__)


def _print_account_status(service: AccountService) -> None:
    status = service.get_status()
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(service.account.fallback.timezone)
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

    mode = "Intelligent" if not service.account.fallback.enabled else "Intelligent + Fallback"

    print()
    print(f"╭─── Compte : {status.account_name:<41}───╮")
    print(f"│  Heure locale    : {now.strftime('%Y-%m-%d %H:%M:%S %Z'):<33}│")
    print(f"│  Mode            : {mode:<33}│")
    print(f"│  Probe interval  : {service.account.probe.interval_minutes} min{'':<28}│")
    print("├─────────────────────────────────────────────────────╮")
    print(f"│  Prochain ping   : {scheduled_local:<33}│")
    print(f"│  Dans            : {time_left:<33}│")
    print(f"│  Dernier ping    : {last_ping_local:<33}│")
    print(f"│  Total pings     : {status.ping_count:<33}│")
    print("╰─────────────────────────────────────────────────────╯")
    print()


def cmd_status(manager: ClaudePingManager) -> None:
    for service in manager.accounts.values():
        _print_account_status(service)


def cmd_ping_now(manager: ClaudePingManager) -> None:
    for name, service in manager.accounts.items():
        result = service.trigger_ping_now()
        if result.success:
            print(f"✅ [{name}] Ping réussi. Réponse : {result.response}")
        else:
            print(f"❌ [{name}] Ping échoué : {result.error}")


def run_service(manager: ClaudePingManager) -> None:
    logger = get_logger()
    stop_event = threading.Event()

    def _shutdown(signum, frame):
        logger.info(f"Signal {signum} reçu, arrêt propre...")
        manager.stop_all()
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    manager.start_all()
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
        boot_config = load_config(CONFIG_PATH)
    except ConfigError as e:
        print(f"[ERREUR CONFIG] {e}", file=sys.stderr)
        sys.exit(1)

    setup_logger(
        log_file=boot_config.logging.file,
        level=boot_config.logging.level,
        max_bytes=boot_config.logging.max_bytes,
        backup_count=boot_config.logging.backup_count,
    )
    logger = get_logger()

    if cmd in ("--ui", "ui", "--service", "service"):
        # Une seule instance longue-durée (UI et/ou service) à la fois :
        # sinon deux instances scheduleraient et enverraient chacune leurs
        # propres pings/notifications pour les mêmes comptes.
        try:
            acquire_single_instance_lock(LOCK_PATH)
        except SingleInstanceError as e:
            if cmd in ("--ui", "ui"):
                # Plutôt que juste échouer : demander à l'instance --ui déjà
                # lancée (visible ou masquée) de se réafficher.
                from claudeping.activation import request_activation

                if request_activation(BASE_DIR):
                    logger.info("Instance déjà lancée : fenêtre réaffichée.")
                    print("[ClaudePing] Une instance tourne déjà — fenêtre réaffichée.")
                    return
                msg = (
                    "Une autre instance de ClaudePing tourne déjà, mais sans fenêtre "
                    "à réafficher (probablement un `--service` sans interface). "
                    "Arrêtez-la avant de relancer l'UI, ou utilisez `claudeping status` "
                    "/ `claudeping ping-now` en ligne de commande."
                )
            else:
                msg = str(e)
            logger.error(msg)
            print(f"[ClaudePing] {msg}", file=sys.stderr)
            try:
                notify_error(msg)
            except Exception:
                pass
            sys.exit(1)

    manager = ClaudePingManager(config_path=CONFIG_PATH, base_dir=BASE_DIR)

    if cmd == "status":
        cmd_status(manager)
        return

    if cmd in ("ping-now", "ping_now"):
        cmd_ping_now(manager)
        return

    if cmd in ("--ui", "ui"):
        if launch_ui is None:
            logger.error("UI non disponible : installez PySide6 pour l'exécuter.")
            print("UI non disponible : installez PySide6 avec 'pip install PySide6'.")
            sys.exit(1)
        install_gui_log_handler()
        logger.info("Lancement de l'interface graphique...")
        launch_ui(manager, BASE_DIR)
        return

    if cmd in ("--service", "service"):
        logger.info(f"ClaudePing démarré en mode service ({len(manager.accounts)} compte(s)).")
        if any(a.notifications.enabled for a in boot_config.accounts):
            notify_service_started(len(manager.accounts))
        run_service(manager)
        logger.info("ClaudePing arrêté.")


if __name__ == "__main__":
    main()
