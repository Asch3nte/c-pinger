"""Interface graphique minimale pour ClaudePing."""

from __future__ import annotations

import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStatusBar,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from claudeping.logger import get_gui_log_handler
from claudeping.service import ClaudePingService


def format_timestamp(dt: datetime | None, tz: ZoneInfo) -> str:
    if dt is None:
        return "—"
    try:
        return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return dt.isoformat()


class ClaudePingWindow(QMainWindow):
    def __init__(self, service: ClaudePingService) -> None:
        super().__init__()
        self.service = service
        self.setWindowTitle("ClaudePing")
        self.setMinimumSize(640, 440)

        self._build_ui()
        self._setup_tray()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self.refresh_status)
        self._refresh_timer.start()

        try:
            self.service.refresh_claude_status()
        except Exception:
            pass

        self.refresh_status()
        self.service.start()

    def _build_ui(self) -> None:
        self.status_label = QLabel("Chargement...")
        self.next_ping_label = QLabel("—")
        self.last_ping_label = QLabel("—")
        self.ping_count_label = QLabel("—")
        self.quota_status_label = QLabel("—")
        self.session_quota_label = QLabel("—")
        self.weekly_quota_label = QLabel("—")
        self.monthly_quota_label = QLabel("—")
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
        self.manual_time_input = QLineEdit()

        self.claude_browse_button = QPushButton("Parcourir")
        self.check_cli_button = QPushButton("Vérifier CLI")
        self.login_button = QPushButton("Se connecter")
        self.logout_button = QPushButton("Se déconnecter")
        self.apply_button = QPushButton("Enregistrer la configuration")
        self.manual_ping_button = QPushButton("Ping immédiat")
        self.schedule_ping_button = QPushButton("Programmer le ping")

        self.claude_browse_button.clicked.connect(self._browse_claude_path)
        self.check_cli_button.clicked.connect(self._check_cli_status)
        self.login_button.clicked.connect(self._login_claude)
        self.logout_button.clicked.connect(self._logout_claude)
        self.apply_button.clicked.connect(self._apply_settings)
        self.manual_ping_button.clicked.connect(self._manual_ping)
        self.schedule_ping_button.clicked.connect(self._schedule_manual_ping)

        status_layout = QFormLayout()
        status_layout.addRow("Statut service :", self.status_label)
        status_layout.addRow("Prochain ping :", self.next_ping_label)
        status_layout.addRow("Dernier ping :", self.last_ping_label)
        status_layout.addRow("Total pings :", self.ping_count_label)
        status_layout.addRow("Quota (session/hebdo/mensuel) :", self.quota_status_label)
        status_layout.addRow("Quota session :", self.session_quota_label)
        status_layout.addRow("Quota hebdo :", self.weekly_quota_label)
        status_layout.addRow("Quota mensuel :", self.monthly_quota_label)
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

        status_tab = QWidget()
        status_tab_layout = QVBoxLayout(status_tab)
        status_tab_layout.addLayout(status_layout)
        status_tab_layout.addSpacing(20)
        status_tab_layout.addLayout(settings_layout)
        status_tab_layout.addSpacing(10)
        status_tab_layout.addWidget(QLabel(
            "Ping manuel ponctuel (HH:MM) — force un ping unique à cette heure :"
        ))
        status_tab_layout.addLayout(manual_layout)
        status_tab_layout.addSpacing(10)
        status_tab_layout.addLayout(buttons_layout)
        status_tab_layout.addStretch(1)

        # Onglet logs
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        font = self.log_view.font()
        font.setFamily("Monospace")
        font.setPointSize(9)
        self.log_view.setFont(font)

        clear_log_button = QPushButton("Effacer")
        clear_log_button.setFixedWidth(80)
        clear_log_button.clicked.connect(self._clear_logs)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Logs en temps réel :"))
        log_header.addStretch(1)
        log_header.addWidget(clear_log_button)

        log_tab = QWidget()
        log_tab_layout = QVBoxLayout(log_tab)
        log_tab_layout.addLayout(log_header)
        log_tab_layout.addWidget(self.log_view)

        self._tabs = QTabWidget()
        self._tabs.addTab(status_tab, "Statut")
        self._tabs.addTab(log_tab, "Logs")

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._tabs)
        self.setCentralWidget(central)

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

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
        refresh_action.triggered.connect(self.refresh_status)
        hide_action.triggered.connect(self.hide)
        quit_action.triggered.connect(self.close)

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

    def refresh_status(self) -> None:
        try:
            status = self.service.get_status()
        except Exception as exc:
            QMessageBox.warning(self, "Erreur", f"Impossible de récupérer le statut : {exc}")
            return

        tz = ZoneInfo(self.service.config.fallback.timezone)

        self.status_label.setText("En cours" if status.running else "Arrêté")
        self.next_ping_label.setText(format_timestamp(status.next_ping_at, tz))
        self.last_ping_label.setText(format_timestamp(status.last_ping_at, tz))
        self.ping_count_label.setText(str(status.ping_count))
        self.quota_status_label.setText(
            f"session: {status.quota_status_daily} | "
            f"hebdo: {status.quota_status_weekly} | "
            f"mensuel: {status.quota_status_monthly}"
        )
        self.session_quota_label.setText(
            f"{status.session_pct if status.session_pct is not None else '—'}% / 100%"
        )
        self.weekly_quota_label.setText(
            f"{status.weekly_used if status.weekly_used is not None else '—'}% / "
            f"{status.weekly_limit if status.weekly_limit is not None else '—'}%"
        )
        self.monthly_quota_label.setText(
            f"{status.monthly_used if status.monthly_used is not None else '—'}% / "
            f"{status.monthly_limit if status.monthly_limit is not None else '—'}%"
        )
        self.cli_status_label.setText(
            f"{status.auth_status} {'(disponible)' if status.cli_available else '(absent)'}"
        )
        self.last_probe_label.setText(format_timestamp(status.last_probe_at, tz))

        self.fallback_checkbox.setChecked(status.fallback_enabled)
        self.fallback_time_input.setText(status.fallback_time)
        self.probe_interval_input.setText(str(status.probe_interval_minutes))
        self.claude_path_input.setText(status.claude_executable)

        self._refresh_logs()

        self.status_bar.showMessage(
            f"Mis à jour : {datetime.now(tz).strftime('%H:%M:%S')}"
        )

    def _refresh_logs(self) -> None:
        handler = get_gui_log_handler()
        if handler is None:
            return
        lines = handler.get_lines()
        new_text = "\n".join(lines)
        if new_text != self.log_view.toPlainText():
            self.log_view.setPlainText(new_text)
            self.log_view.verticalScrollBar().setValue(
                self.log_view.verticalScrollBar().maximum()
            )

    def _clear_logs(self) -> None:
        handler = get_gui_log_handler()
        if handler is not None:
            handler.clear()
        self.log_view.clear()

    def _browse_claude_path(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner l'exécutable Claude CLI",
            str(Path.home()),
            "Executable (*.exe);;All files (*)",
        )
        if path:
            self.claude_path_input.setText(path)

    def _check_cli_status(self) -> None:
        self._apply_settings(save_config=False)
        status = self.service.refresh_claude_status()
        QMessageBox.information(
            self,
            "État Claude CLI",
            f"Statut : {status.auth_status}\n" +
            f"CLI disponible : {'oui' if status.cli_available else 'non'}\n" +
            f"Quota journalier : {status.daily_used or '—'} / {status.daily_limit or '—'}\n" +
            f"Quota hebdo : {status.weekly_used or '—'} / {status.weekly_limit or '—'}\n" +
            f"Quota mensuel : {status.monthly_used or '—'} / {status.monthly_limit or '—'}"
        )
        self.refresh_status()

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
                "Un navigateur va s'ouvrir pour authentifier votre compte Claude.\n"
                "Complétez la connexion dans le navigateur, puis attendez.\n\n"
                "Le délai maximum est de 2 minutes.",
            )

        self.login_button.setEnabled(False)
        self.logout_button.setEnabled(False)
        self.check_cli_button.setEnabled(False)

        def worker() -> None:
            if action == "login":
                success, message = self.service.login_claude()
            else:
                success, message = self.service.logout_claude()

            def on_done() -> None:
                self.login_button.setEnabled(True)
                self.logout_button.setEnabled(True)
                self.check_cli_button.setEnabled(True)
                QMessageBox.information(
                    self,
                    title,
                    message or (f"{title} réussie" if success else f"{title} échouée"),
                )
                self.refresh_status()

            QTimer.singleShot(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_settings(self, save_config: bool = True) -> bool:
        fallback_time = self.fallback_time_input.text().strip()
        claude_path = self.claude_path_input.text().strip()
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
        self.service.set_probe_interval(probe_minutes, persist=save_config)

        if save_config:
            self.status_bar.showMessage("Configuration enregistrée.")
            QMessageBox.information(self, "Configuration", "Paramètres sauvegardés avec succès.")
        self.refresh_status()
        return True

    def _manual_ping(self) -> None:
        result = self.service.trigger_ping_now()
        if result.success:
            QMessageBox.information(self, "Ping réussi", f"Réponse : {result.response}")
        else:
            QMessageBox.warning(self, "Ping échoué", result.error)
        self.refresh_status()

    def _schedule_manual_ping(self) -> None:
        time_str = self.manual_time_input.text().strip()
        if not re.fullmatch(r"\d{2}:\d{2}", time_str):
            QMessageBox.warning(self, "Erreur", "Format d'heure invalide. Utilisez HH:MM.")
            return

        try:
            scheduled = self.service.schedule_manual_ping(time_str)
            tz = ZoneInfo(self.service.config.fallback.timezone)
            QMessageBox.information(
                self,
                "Ping programmé",
                f"Ping programmé à {scheduled.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S %Z')}.",
            )
            self.refresh_status()
        except ValueError as exc:
            QMessageBox.warning(self, "Erreur", str(exc))

    def closeEvent(self, event) -> None:
        self.service.stop()
        if hasattr(self, "tray_icon"):
            self.tray_icon.hide()
        super().closeEvent(event)


def launch_ui(service: ClaudePingService) -> None:
    app = QApplication(sys.argv)
    window = ClaudePingWindow(service)
    window.show()
    app.exec()
