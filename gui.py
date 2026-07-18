"""Interface graphique minimale pour ClaudePing (multi-comptes)."""

from __future__ import annotations

import html
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStatusBar,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from claudeping.activation import ActivationServer
from claudeping.logger import account_color, get_gui_log_handler, get_logger
from claudeping.notifier import notify_running_in_background
from claudeping.service import AccountService, ClaudePingManager


def format_timestamp(dt: datetime | None, tz: ZoneInfo) -> str:
    if dt is None:
        return "—"
    try:
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return dt.isoformat()


# Format produit par claudeping.logger (console / GUILogHandler) :
# "2026-07-18 16:43:44 [INFO] [nom_compte] message..."
_LOG_LINE_RE = re.compile(
    r"^(?P<prefix>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[[A-Z]+\]) "
    r"\[(?P<account>[^\]]+)\](?P<rest>.*)$"
)


def render_log_html(lines: list[str]) -> str:
    """Reconstruit les lignes de log en HTML, avec le tag [compte] coloré."""
    rows = []
    for line in lines:
        match = _LOG_LINE_RE.match(line)
        if match:
            _, color = account_color(match.group("account"))
            rows.append(
                f'{html.escape(match.group("prefix"))} '
                f'<span style="color:{color}; font-weight:600;">'
                f'[{html.escape(match.group("account"))}]</span>'
                f'{html.escape(match.group("rest"))}'
            )
        else:
            rows.append(html.escape(line))
    body = "<br>".join(rows) if rows else ""
    return f'<div style="font-family:monospace; font-size:9pt;">{body}</div>'


class _AsyncWorker(QObject):
    """Exécute `work()` dans un QThread et rapporte le résultat via un signal.

    Deux pièges Qt à éviter ici, tous deux silencieux (pas d'exception, pas
    d'erreur — juste un bouton qui reste désactivé et rien ne se passe) :

    1. Un simple `threading.Thread` + `QTimer.singleShot(0, callback)` ne
       fonctionne pas : un QTimer créé depuis un thread Python sans event
       loop Qt n'est jamais déclenché.
    2. Connecter un signal émis depuis un QThread à une closure/lambda
       (au lieu d'une vraie méthode liée d'un QObject) ne garantit PAS
       une connexion "queued" vers le thread GUI : Qt ne peut détecter le
       thread du destinataire que via `callable.__self__`. Résultat : le
       slot s'exécute sur le thread du worker, pas sur le thread GUI —
       manipuler des widgets depuis là est non défini (et fait planter
       `QThread.wait()` appelé sur lui-même).

    D'où : le contexte de fin de tâche est stocké comme attributs sur ce
    `_AsyncWorker` (un QObject), et le signal `finished` est connecté à une
    vraie méthode liée du QObject appelant (thread GUI garanti).
    """

    finished = Signal(object, object)  # (worker, résultat_ou_exception)

    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self._work = work
        # Rempli par l'appelant avant thread.start()
        self.owner_thread: QThread | None = None
        self.on_done: Callable[[object], None] | None = None
        self.disable_widgets: tuple = ()

    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:  # ne jamais laisser le thread mourir en silence
            result = exc
        self.finished.emit(self, result)


class AddAccountDialog(QDialog):
    """Petite boîte de dialogue pour ajouter un nouveau compte Claude."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajouter un compte")
        self.setMinimumWidth(420)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("ex : perso, pro, client-x…")

        self.config_dir_input = QLineEdit()
        self.config_dir_input.setPlaceholderText(
            "vide = config Claude par défaut du poste"
        )
        browse_button = QPushButton("Parcourir…")
        browse_button.clicked.connect(self._browse_dir)

        dir_layout = QHBoxLayout()
        dir_layout.addWidget(self.config_dir_input)
        dir_layout.addWidget(browse_button)

        form = QFormLayout()
        form.addRow("Nom du compte :", self.name_input)
        form.addRow("Dossier de config Claude :", dir_layout)

        hint = QLabel(
            "Un dossier de config dédié isole entièrement les identifiants\n"
            "de ce compte (variable CLAUDE_CONFIG_DIR), pour faire tourner\n"
            "plusieurs comptes Claude en parallèle sur la même machine."
        )
        hint.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(hint)
        layout.addWidget(buttons)

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choisir (ou créer) le dossier de config Claude", str(Path.home())
        )
        if path:
            self.config_dir_input.setText(path)

    def values(self) -> tuple[str, str]:
        return self.name_input.text().strip(), self.config_dir_input.text().strip()


class AccountPanel(QWidget):
    """Panneau de statut/réglages pour un seul compte Claude."""

    def __init__(
        self,
        manager: ClaudePingManager,
        service: AccountService,
        on_removed,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.service = service
        self._on_removed = on_removed
        self._active_async = []  # garde (QThread, _AsyncWorker) en vie tant que ça tourne
        self._build_ui()

    def _build_ui(self) -> None:
        self.status_label = QLabel("Chargement...")
        self.next_ping_label = QLabel("—")
        self.last_ping_label = QLabel("—")
        self.ping_count_label = QLabel("—")
        self.quota_status_label = QLabel("—")
        self.session_quota_label = QLabel("—")
        self.weekly_quota_label = QLabel("—")
        self.cli_status_label = QLabel("—")
        self.last_probe_label = QLabel("—")

        self.fallback_checkbox = QCheckBox(
            "Activer le fallback horaire (ping de secours si la détection automatique échoue)"
        )
        self.fallback_time_input = QLineEdit()
        self.probe_interval_input = QLineEdit()
        self.probe_interval_input.setPlaceholderText("ex : 30")
        self.probe_interval_input.setFixedWidth(60)
        self.claude_path_input = QLineEdit()
        self.claude_path_input.setPlaceholderText("chemin vers l'exécutable claude ou 'claude'")
        self.config_dir_input = QLineEdit()
        self.config_dir_input.setPlaceholderText("vide = config Claude par défaut du poste")
        self.manual_time_input = QLineEdit()

        self.claude_browse_button = QPushButton("Parcourir")
        self.config_dir_browse_button = QPushButton("Parcourir…")
        self.open_config_dir_button = QPushButton("Ouvrir dossier de config")
        self.check_cli_button = QPushButton("Vérifier CLI")
        self.login_button = QPushButton("Se connecter")
        self.logout_button = QPushButton("Se déconnecter")
        self.apply_button = QPushButton("Enregistrer la configuration")
        self.manual_ping_button = QPushButton("Ping immédiat")
        self.schedule_ping_button = QPushButton("Programmer le ping")
        self.remove_account_button = QPushButton("Supprimer ce compte")

        self.claude_browse_button.clicked.connect(self._browse_claude_path)
        self.config_dir_browse_button.clicked.connect(self._browse_config_dir)
        self.open_config_dir_button.clicked.connect(self._open_config_dir)
        self.check_cli_button.clicked.connect(self._check_cli_status)
        self.login_button.clicked.connect(self._login_claude)
        self.logout_button.clicked.connect(self._logout_claude)
        self.apply_button.clicked.connect(self._apply_settings)
        self.manual_ping_button.clicked.connect(self._manual_ping)
        self.schedule_ping_button.clicked.connect(self._schedule_manual_ping)
        self.remove_account_button.clicked.connect(self._remove_account)

        status_layout = QFormLayout()
        status_layout.addRow("Statut service :", self.status_label)
        status_layout.addRow("Prochain ping :", self.next_ping_label)
        status_layout.addRow("Dernier ping :", self.last_ping_label)
        status_layout.addRow("Total pings :", self.ping_count_label)
        status_layout.addRow("Quota (session / hebdo) :", self.quota_status_label)
        status_layout.addRow("Quota session :", self.session_quota_label)
        status_layout.addRow("Quota hebdo :", self.weekly_quota_label)
        status_layout.addRow("Dernière probe :", self.last_probe_label)

        settings_layout = QFormLayout()

        probe_layout = QHBoxLayout()
        probe_layout.addWidget(self.probe_interval_input)
        probe_layout.addWidget(QLabel("min"))
        probe_layout.addStretch(1)
        settings_layout.addRow("Probe quota toutes les :", probe_layout)

        settings_layout.addRow(self.fallback_checkbox)
        settings_layout.addRow("Heure de fallback (HH:MM) :", self.fallback_time_input)

        claude_path_layout = QHBoxLayout()
        claude_path_layout.addWidget(self.claude_path_input)
        claude_path_layout.addWidget(self.claude_browse_button)
        claude_path_layout.addWidget(self.check_cli_button)
        settings_layout.addRow("Claude CLI (exécutable) :", claude_path_layout)
        settings_layout.addRow("État CLI :", self.cli_status_label)

        config_dir_layout = QHBoxLayout()
        config_dir_layout.addWidget(self.config_dir_input)
        config_dir_layout.addWidget(self.config_dir_browse_button)
        config_dir_layout.addWidget(self.open_config_dir_button)
        settings_layout.addRow("Dossier de config Claude :", config_dir_layout)

        auth_buttons_layout = QHBoxLayout()
        auth_buttons_layout.addWidget(self.login_button)
        auth_buttons_layout.addWidget(self.logout_button)
        settings_layout.addRow(auth_buttons_layout)

        manual_layout = QHBoxLayout()
        manual_layout.addWidget(self.manual_time_input)
        manual_layout.addWidget(self.schedule_ping_button)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.apply_button)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.manual_ping_button)

        layout = QVBoxLayout(self)
        layout.addLayout(status_layout)
        layout.addSpacing(20)
        layout.addLayout(settings_layout)
        layout.addSpacing(10)
        layout.addWidget(QLabel(
            "Ping manuel ponctuel (HH:MM) — force un ping unique à cette heure :"
        ))
        layout.addLayout(manual_layout)
        layout.addSpacing(10)
        layout.addLayout(buttons_layout)
        layout.addSpacing(20)

        remove_layout = QHBoxLayout()
        remove_layout.addStretch(1)
        remove_layout.addWidget(self.remove_account_button)
        layout.addLayout(remove_layout)

        layout.addStretch(1)

    # ------------------------------------------------------------------
    # Rafraîchissement
    # ------------------------------------------------------------------

    def refresh_status(self) -> None:
        try:
            status = self.service.get_status()
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible de récupérer le statut : {exc}")
            return

        tz = ZoneInfo(self.service.account.fallback.timezone)

        self.status_label.setText("En cours" if status.running else "Arrêté")
        self.next_ping_label.setText(format_timestamp(status.next_ping_at, tz))
        self.last_ping_label.setText(format_timestamp(status.last_ping_at, tz))
        self.ping_count_label.setText(str(status.ping_count))
        self.quota_status_label.setText(
            f"session: {status.quota_status_session} | hebdo: {status.quota_status_weekly}"
        )
        session_reset_str = (
            f" (reset {format_timestamp(status.reset_at, tz)})" if status.reset_at else ""
        )
        self.session_quota_label.setText(
            f"{status.session_pct if status.session_pct is not None else '—'}%{session_reset_str}"
        )
        weekly_reset_str = (
            f" (reset {format_timestamp(status.weekly_reset_at, tz)})" if status.weekly_reset_at else ""
        )
        self.weekly_quota_label.setText(
            f"{status.weekly_used if status.weekly_used is not None else '—'}%{weekly_reset_str}"
        )
        self.cli_status_label.setText(
            f"{status.auth_status} {'(disponible)' if status.cli_available else '(absent)'}"
        )
        self.last_probe_label.setText(format_timestamp(status.last_probe_at, tz))

        self.fallback_checkbox.setChecked(status.fallback_enabled)
        self.fallback_time_input.setText(status.fallback_time)
        self.probe_interval_input.setText(str(status.probe_interval_minutes))
        self.claude_path_input.setText(status.claude_executable)
        if not self.config_dir_input.hasFocus():
            self.config_dir_input.setText(status.claude_config_dir)

    # ------------------------------------------------------------------
    # Dossier de config
    # ------------------------------------------------------------------

    def _browse_claude_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner l'exécutable Claude CLI",
            str(Path.home()),
            "Executable (*.exe);;All files (*)",
        )
        if path:
            self.claude_path_input.setText(path)

    def _browse_config_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Choisir (ou créer) le dossier de config Claude", str(Path.home())
        )
        if path:
            self.config_dir_input.setText(path)

    def _open_config_dir(self) -> None:
        if not self._apply_settings(save_config=False):
            return
        try:
            path = self.manager.open_account_config_dir(self.service.name)
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible d'ouvrir le dossier : {exc}")
            return
        self.status_bar_message(f"Dossier ouvert : {path}")

    def status_bar_message(self, message: str) -> None:
        window = self.window()
        if hasattr(window, "status_bar"):
            window.status_bar.showMessage(message)

    # ------------------------------------------------------------------
    # Exécution asynchrone (les appels CLI peuvent prendre jusqu'à 30s,
    # voire plusieurs minutes pour un ping avec retries — on ne bloque
    # jamais le thread GUI, sinon toute l'appli (tous les comptes) gèle).
    # ------------------------------------------------------------------

    def _run_async(
        self,
        work: Callable[[], object],
        on_done: Callable[[object], None],
        disable_widgets: tuple = (),
    ) -> None:
        for widget in disable_widgets:
            widget.setEnabled(False)

        thread = QThread(self)
        worker = _AsyncWorker(work)
        worker.owner_thread = thread
        worker.on_done = on_done
        worker.disable_widgets = disable_widgets
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        # Connexion à une vraie méthode liée (self._on_async_finished) :
        # c'est ce qui garantit à Qt une exécution sur le thread GUI. Voir
        # la docstring de _AsyncWorker.
        worker.finished.connect(self._on_async_finished)
        thread.finished.connect(worker.deleteLater)
        # Référence forte : sinon le GC peut libérer thread/worker avant
        # la fin de l'exécution en tâche de fond.
        self._active_async.append((thread, worker))
        thread.start()

    def _on_async_finished(self, worker: "_AsyncWorker", result: object) -> None:
        for widget in worker.disable_widgets:
            widget.setEnabled(True)
        thread = worker.owner_thread
        thread.quit()
        thread.wait()
        entry = (thread, worker)
        if entry in self._active_async:
            self._active_async.remove(entry)
        if isinstance(result, Exception):
            QMessageBox.warning(self, "Erreur", str(result))
            self.refresh_status()
        else:
            worker.on_done(result)

    # ------------------------------------------------------------------
    # CLI / auth
    # ------------------------------------------------------------------

    def _check_cli_status(self) -> None:
        if not self._apply_settings(save_config=False):
            return

        def done(status) -> None:
            QMessageBox.information(
                self,
                "État Claude CLI",
                f"Statut : {status.auth_status}\n" +
                f"CLI disponible : {'oui' if status.cli_available else 'non'}\n" +
                f"Quota session : {status.session_pct if status.session_pct is not None else '—'}%\n" +
                f"Quota hebdo : {status.weekly_used if status.weekly_used is not None else '—'}%"
            )
            self.refresh_status()

        self.status_bar_message(f"[{self.service.name}] Vérification CLI en cours…")
        self._run_async(
            self.service.refresh_claude_status,
            done,
            disable_widgets=(self.check_cli_button, self.login_button, self.logout_button),
        )

    def _login_claude(self) -> None:
        self._run_cli_auth("login", "Connexion Claude CLI")

    def _logout_claude(self) -> None:
        self._run_cli_auth("logout", "Déconnexion Claude CLI")

    def _run_cli_auth(self, action: str, title: str) -> None:
        if not self._apply_settings(save_config=False):
            return

        if action == "login":
            QMessageBox.information(
                self,
                "Connexion Claude CLI",
                "Un navigateur va s'ouvrir pour authentifier ce compte Claude.\n"
                "Complétez la connexion dans le navigateur, puis attendez.\n\n"
                "Le délai maximum est de 2 minutes.",
            )

        def work():
            return self.service.login_claude() if action == "login" else self.service.logout_claude()

        def done(result) -> None:
            success, message = result
            QMessageBox.information(
                self,
                title,
                message or (f"{title} réussie" if success else f"{title} échouée"),
            )
            self.refresh_status()

        self.status_bar_message(f"[{self.service.name}] {title} en cours…")
        self._run_async(
            work,
            done,
            disable_widgets=(self.login_button, self.logout_button, self.check_cli_button),
        )

    # ------------------------------------------------------------------
    # Réglages / actions
    # ------------------------------------------------------------------

    def _apply_settings(self, save_config: bool = True) -> bool:
        fallback_time = self.fallback_time_input.text().strip()
        claude_path = self.claude_path_input.text().strip()
        config_dir = self.config_dir_input.text().strip()
        probe_interval_str = self.probe_interval_input.text().strip()

        if not re.fullmatch(r"\d{2}:\d{2}", fallback_time):
            QMessageBox.warning(self, "Erreur", "Format d'heure invalide. Utilisez HH:MM.")
            return False

        try:
            probe_minutes = int(probe_interval_str)
            if probe_minutes < 1:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Erreur", "L'intervalle de probe doit être un entier ≥ 1.")
            return False

        self.service.set_fallback_enabled(self.fallback_checkbox.isChecked(), persist=save_config)
        self.service.set_fallback_time(fallback_time, persist=save_config)
        self.service.set_claude_executable(claude_path, persist=save_config)
        self.service.set_claude_config_dir(config_dir, persist=save_config)
        self.service.set_probe_interval(probe_minutes, persist=save_config)

        if save_config:
            self.status_bar_message("Configuration enregistrée.")
            QMessageBox.information(self, "Configuration", "Paramètres sauvegardés avec succès.")
        self.refresh_status()
        return True

    def _manual_ping(self) -> None:
        def done(result) -> None:
            if result.success:
                QMessageBox.information(self, "Ping réussi", f"Réponse : {result.response}")
            else:
                QMessageBox.warning(self, "Ping échoué", result.error)
            self.refresh_status()

        self.status_bar_message(
            f"[{self.service.name}] Ping en cours… (peut prendre plusieurs minutes en cas de retry)"
        )
        self._run_async(
            self.service.trigger_ping_now,
            done,
            disable_widgets=(self.manual_ping_button, self.schedule_ping_button),
        )

    def _schedule_manual_ping(self) -> None:
        time_str = self.manual_time_input.text().strip()
        if not re.fullmatch(r"\d{2}:\d{2}", time_str):
            QMessageBox.warning(self, "Erreur", "Format d'heure invalide. Utilisez HH:MM.")
            return

        try:
            scheduled = self.service.schedule_manual_ping(time_str)
            tz = ZoneInfo(self.service.account.fallback.timezone)
            QMessageBox.information(
                self,
                "Ping programmé",
                f"Ping programmé à {scheduled.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}.",
            )
            self.refresh_status()
        except ValueError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))

    def _remove_account(self) -> None:
        reply = QMessageBox.question(
            self,
            "Supprimer ce compte",
            f"Supprimer le compte '{self.service.name}' ?\n\n"
            "Le scheduler de ce compte sera arrêté et le compte retiré de\n"
            "config.yaml. Les fichiers d'état/logs existants ne sont pas\n"
            "supprimés du disque.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        name = self.service.name
        self.manager.remove_account(name)
        self._on_removed(name)


class ClaudePingWindow(QMainWindow):
    # Émis depuis le thread d'écoute de l'ActivationServer (pas le thread
    # GUI) quand une nouvelle instance --ui demande le réaffichage de la
    # fenêtre. Se connecter à une vraie méthode liée de ce QObject garantit
    # à Qt une exécution du slot sur le thread GUI (cf. _AsyncWorker).
    activation_requested = Signal()

    def __init__(self, manager: ClaudePingManager, base_dir: Path) -> None:
        super().__init__()
        self.manager = manager
        self.setWindowTitle("ClaudePing")
        self.setMinimumSize(720, 540)
        self._account_panels: dict[str, AccountPanel] = {}

        self._build_ui()
        self._setup_menu()
        self._setup_tray()

        self.activation_requested.connect(self._on_activation_requested)
        self._activation_server = ActivationServer(base_dir, on_activate=self.activation_requested.emit)
        self._activation_server.start()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self.refresh_all)
        self._refresh_timer.start()

        # Pas de pré-chauffage synchrone ici : ça bloquerait toute la
        # fenêtre (jusqu'à 30s PAR compte) avant même d'être affichée.
        # Chaque scheduler fait déjà son propre probe initial en tâche
        # de fond dès start_all() ; le premier tick de refresh_all()
        # (5s plus tard) affichera son résultat.
        self.refresh_all()
        self.manager.start_all()

    def _on_activation_requested(self) -> None:
        get_logger().info("Réaffichage demandé par une nouvelle instance --ui.")
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _setup_menu(self) -> None:
        # Toujours disponible (contrairement au menu du tray, qui dépend
        # d'un system tray présent sur le poste) : garantit un moyen
        # explicite de masquer/quitter même sans tray.
        menu = self.menuBar().addMenu("Fichier")

        hide_action = QAction("Masquer", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)

        refresh_action = QAction("Rafraîchir", self)
        refresh_action.triggered.connect(self.refresh_all)
        menu.addAction(refresh_action)

        menu.addSeparator()

        quit_action = QAction("Quitter", self)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)

    def _build_ui(self) -> None:
        self._tabs = QTabWidget()

        for service in self.manager.accounts.values():
            self._add_account_tab(service, select=False)

        # Onglet logs (partagé entre tous les comptes, tag [nom_compte] coloré)
        self._last_log_text = ""
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        font = self.log_view.font()
        font.setFamily("Monospace")
        font.setPointSize(9)
        self.log_view.setFont(font)

        clear_log_button = QPushButton("Effacer")
        clear_log_button.setFixedWidth(80)
        clear_log_button.clicked.connect(self._clear_logs)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Logs en temps réel (tous comptes, tag [nom_compte] coloré par compte) :"))
        log_header.addStretch(1)
        log_header.addWidget(clear_log_button)

        self.log_tab = QWidget()
        log_tab_layout = QVBoxLayout(self.log_tab)
        log_tab_layout.addLayout(log_header)
        log_tab_layout.addWidget(self.log_view)

        self._tabs.addTab(self.log_tab, "Logs")

        add_button = QPushButton("+ Ajouter un compte")
        add_button.clicked.connect(self._add_account_dialog)
        self._tabs.setCornerWidget(add_button, Qt.TopRightCorner)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._tabs)
        self.setCentralWidget(central)

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

    def _add_account_tab(self, service: AccountService, select: bool = True) -> None:
        panel = AccountPanel(self.manager, service, on_removed=self._on_account_removed)
        self._account_panels[service.name] = panel
        logs_index = self._tabs.indexOf(self.log_tab) if hasattr(self, "log_tab") else -1
        if logs_index == -1:
            index = self._tabs.addTab(panel, service.name)
        else:
            index = logs_index
            self._tabs.insertTab(index, panel, service.name)
        if select:
            self._tabs.setCurrentIndex(index)

    def _add_account_dialog(self) -> None:
        dialog = AddAccountDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        name, config_dir = dialog.values()
        if not name:
            QMessageBox.warning(self, "Erreur", "Le nom du compte est obligatoire.")
            return
        try:
            service = self.manager.add_account(name, config_dir)
        except ValueError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))
            return
        self._add_account_tab(service, select=True)
        self.refresh_all()

    def _on_account_removed(self, name: str) -> None:
        panel = self._account_panels.pop(name, None)
        if panel is None:
            return
        index = self._tabs.indexOf(panel)
        if index != -1:
            self._tabs.removeTab(index)
        panel.deleteLater()

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        icon = self.style().standardIcon(QStyle.SP_DesktopIcon)
        self.tray_icon = QSystemTrayIcon(icon, parent=self)
        self.tray_icon.setToolTip("ClaudePing")

        menu = QMenu(self)
        open_action = QAction("Ouvrir", self)
        refresh_action = QAction("Rafraîchir", self)
        hide_action = QAction("Masquer", self)
        quit_action = QAction("Quitter", self)

        open_action.triggered.connect(self.showNormal)
        refresh_action.triggered.connect(self.refresh_all)
        hide_action.triggered.connect(self.hide)
        quit_action.triggered.connect(self._quit)

        menu.addAction(open_action)
        menu.addAction(refresh_action)
        menu.addAction(hide_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            self.showNormal()

    def refresh_all(self) -> None:
        for panel in self._account_panels.values():
            panel.refresh_status()
        self._refresh_logs()

        tz_name = next(iter(self.manager.accounts.values())).account.fallback.timezone \
            if self.manager.accounts else "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("UTC")
        self.status_bar.showMessage(
            f"Mis à jour : {datetime.now(tz).strftime('%H:%M:%S')} — "
            f"{len(self._account_panels)} compte(s)"
        )

    def _refresh_logs(self) -> None:
        handler = get_gui_log_handler()
        if handler is None:
            return
        lines = handler.get_lines()
        new_text = "\n".join(lines)
        if new_text == self._last_log_text:
            return
        self._last_log_text = new_text
        self.log_view.setHtml(render_log_html(lines))
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _clear_logs(self) -> None:
        handler = get_gui_log_handler()
        if handler is not None:
            handler.clear()
        self._last_log_text = ""
        self.log_view.clear()

    def closeEvent(self, event) -> None:
        # X masque la fenêtre mais laisse le service tourner en arrière-plan
        # (avec ou sans tray système) : relancer `claudeping --ui` réaffiche
        # cette même fenêtre via l'ActivationServer, donc masquer est
        # toujours sûr — "Quitter" (menu Fichier ou tray) reste le seul
        # moyen explicite d'arrêter vraiment le service.
        event.ignore()
        self.hide()
        notify_running_in_background()

    def _quit(self) -> None:
        self._activation_server.stop()
        self.manager.stop_all()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        QApplication.quit()


def launch_ui(manager: ClaudePingManager, base_dir: Path) -> None:
    app = QApplication(sys.argv)
    window = ClaudePingWindow(manager, base_dir)
    window.show()
    app.exec()
