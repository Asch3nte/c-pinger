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
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStatusBar,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from claudeping.activation import ActivationServer
from claudeping.logger import account_color, get_gui_log_handler, get_logger
from claudeping.notifier import notify_running_in_background
from claudeping.service import AccountService, ClaudePingManager


def asset_path(name: str) -> str:
    """Résout le chemin d'un fichier sous assets/, en source comme en bundle
    PyInstaller (où les données embarquées vivent sous sys._MEIPASS)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).resolve().parent
    return str(base / "assets" / name)


def app_icon() -> QIcon:
    return QIcon(asset_path("icon.png"))


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


class CollapsibleSection(QWidget):
    """Section repliable, repliée par défaut — regroupe des réglages
    secondaires (ex : configuration avancée Claude CLI) pour garder la
    fenêtre compacte sans rien cacher définitivement."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title
        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(False)
        self._toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._toggle.setStyleSheet("QToolButton { border: none; font-weight: 600; }")
        self._toggle.toggled.connect(self._on_toggled)
        self._update_text(False)

        self._content = QWidget()
        self._content.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._toggle)
        layout.addWidget(self._content)

    def _update_text(self, checked: bool) -> None:
        self._toggle.setText(f"{'▾' if checked else '▸'} {self._title}")

    def _on_toggled(self, checked: bool) -> None:
        self._update_text(checked)
        self._content.setVisible(checked)

    def set_content_layout(self, content_layout) -> None:
        self._content.setLayout(content_layout)


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
    ) -> None:
        super().__init__()
        self.manager = manager
        self.service = service
        self._active_async = []  # garde (QThread, _AsyncWorker) en vie tant que ça tourne
        # Champs modifiés par l'utilisateur mais pas encore enregistrés : le
        # refresh périodique (5s) ne doit jamais les écraser, sinon la saisie
        # est perdue avant même que l'utilisateur ait pu cliquer "Enregistrer".
        self._dirty_fields: set[str] = set()
        self._build_ui()

    def _mark_dirty(self, field: str) -> None:
        self._dirty_fields.add(field)

    def _build_ui(self) -> None:
        self.status_label = QLabel("Chargement...")
        self.next_ping_label = QLabel("—")
        self.last_ping_label = QLabel("—")
        self.ping_count_label = QLabel("—")
        self.session_quota_label = QLabel("—")
        self.weekly_quota_label = QLabel("—")
        self.cli_status_label = QLabel("—")
        self.last_probe_label = QLabel("—")

        self.fallback_checkbox = QCheckBox("Activer le fallback horaire")
        self.fallback_checkbox.setToolTip(
            "Ping de secours à heure fixe si la détection automatique du quota échoue."
        )
        self.fallback_time_input = QLineEdit()
        self.fallback_time_input.setPlaceholderText("HH:MM")
        self.fallback_time_input.setFixedWidth(70)
        self.probe_interval_input = QLineEdit()
        self.probe_interval_input.setPlaceholderText("30")
        self.probe_interval_input.setFixedWidth(50)
        self.claude_path_input = QLineEdit()
        self.claude_path_input.setPlaceholderText("chemin vers l'exécutable claude ou 'claude'")
        self.config_dir_input = QLineEdit()
        self.config_dir_input.setPlaceholderText("vide = config Claude par défaut du poste")
        self.manual_time_input = QLineEdit()
        self.manual_time_input.setPlaceholderText("HH:MM")

        self.claude_browse_button = QPushButton("Parcourir")
        self.config_dir_browse_button = QPushButton("Parcourir…")
        self.open_config_dir_button = QPushButton("Ouvrir")
        self.check_cli_button = QPushButton("Vérifier CLI")
        self.login_button = QPushButton("Se connecter")
        self.logout_button = QPushButton("Se déconnecter")
        self.apply_button = QPushButton("Enregistrer la configuration")
        self.manual_ping_button = QPushButton("Ping immédiat")
        self.schedule_ping_button = QPushButton("Programmer le ping")

        self.claude_browse_button.clicked.connect(self._browse_claude_path)
        self.config_dir_browse_button.clicked.connect(self._browse_config_dir)
        self.open_config_dir_button.clicked.connect(self._open_config_dir)
        self.check_cli_button.clicked.connect(self._check_cli_status)
        self.login_button.clicked.connect(self._login_claude)
        self.logout_button.clicked.connect(self._logout_claude)
        # NB : QPushButton.clicked émet un bool (checked=False) — connecter
        # directement à _apply_settings(save_config: bool = True) ferait
        # passer ce False comme save_config, donc appliquerait les réglages
        # SANS jamais les persister sur disque. D'où le lambda qui l'ignore.
        self.apply_button.clicked.connect(lambda _checked=False: self._apply_settings())
        self.manual_ping_button.clicked.connect(self._manual_ping)
        self.schedule_ping_button.clicked.connect(self._schedule_manual_ping)

        # textEdited (contrairement à textChanged) n'est émis que sur une
        # saisie utilisateur, jamais sur nos propres setText() de refresh —
        # donc pas de faux-positif "dirty" quand refresh_status() réécrit
        # un champ non modifié.
        self.fallback_checkbox.toggled.connect(lambda _checked: self._mark_dirty("fallback_enabled"))
        self.fallback_time_input.textEdited.connect(lambda _text: self._mark_dirty("fallback_time"))
        self.probe_interval_input.textEdited.connect(lambda _text: self._mark_dirty("probe_interval"))
        self.claude_path_input.textEdited.connect(lambda _text: self._mark_dirty("claude_path"))
        self.config_dir_input.textEdited.connect(lambda _text: self._mark_dirty("config_dir"))

        # Entrée dans un champ de réglage = sauvegarde immédiate, comme
        # cliquer sur "Enregistrer la configuration".
        self.fallback_time_input.returnPressed.connect(self._apply_settings)
        self.probe_interval_input.returnPressed.connect(self._apply_settings)
        self.claude_path_input.returnPressed.connect(self._apply_settings)
        self.config_dir_input.returnPressed.connect(self._apply_settings)

        # -- État (lecture seule) : grille 2 colonnes pour tenir sur peu de
        # hauteur, sans doublon (le statut quota est fondu dans les % ).
        status_box = QGroupBox("État")
        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(16)
        status_grid.addWidget(QLabel("Statut service :"), 0, 0)
        status_grid.addWidget(self.status_label, 0, 1)
        status_grid.addWidget(QLabel("État CLI :"), 0, 2)
        status_grid.addWidget(self.cli_status_label, 0, 3)
        status_grid.addWidget(QLabel("Prochain ping :"), 1, 0)
        status_grid.addWidget(self.next_ping_label, 1, 1)
        status_grid.addWidget(QLabel("Dernier ping :"), 1, 2)
        status_grid.addWidget(self.last_ping_label, 1, 3)
        status_grid.addWidget(QLabel("Total pings :"), 2, 0)
        status_grid.addWidget(self.ping_count_label, 2, 1)
        status_grid.addWidget(QLabel("Dernière probe :"), 2, 2)
        status_grid.addWidget(self.last_probe_label, 2, 3)
        status_grid.addWidget(QLabel("Quota session :"), 3, 0)
        status_grid.addWidget(self.session_quota_label, 3, 1)
        status_grid.addWidget(QLabel("Quota hebdo :"), 3, 2)
        status_grid.addWidget(self.weekly_quota_label, 3, 3)
        status_box.setLayout(status_grid)

        # -- Réglages principaux : ce qu'on ajuste le plus souvent.
        settings_box = QGroupBox("Réglages")
        settings_layout = QFormLayout()
        settings_layout.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        probe_layout = QHBoxLayout()
        probe_layout.addWidget(self.probe_interval_input)
        probe_layout.addWidget(QLabel("min"))
        probe_layout.addStretch(1)
        settings_layout.addRow("Probe quota toutes les :", probe_layout)

        fallback_layout = QHBoxLayout()
        fallback_layout.addWidget(self.fallback_checkbox)
        fallback_layout.addSpacing(12)
        fallback_layout.addWidget(QLabel("Heure :"))
        fallback_layout.addWidget(self.fallback_time_input)
        fallback_layout.addStretch(1)
        settings_layout.addRow(fallback_layout)
        settings_box.setLayout(settings_layout)

        # -- Configuration Claude CLI : repliée par défaut (rarement
        # modifiée une fois le compte configuré), pour garder la fenêtre
        # compacte au quotidien.
        claude_section = CollapsibleSection("Configuration Claude CLI")
        claude_layout = QFormLayout()
        claude_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        claude_path_layout = QHBoxLayout()
        claude_path_layout.addWidget(self.claude_path_input)
        claude_path_layout.addWidget(self.claude_browse_button)
        claude_path_layout.addWidget(self.check_cli_button)
        claude_layout.addRow("Exécutable CLI :", claude_path_layout)

        config_dir_layout = QHBoxLayout()
        config_dir_layout.addWidget(self.config_dir_input)
        config_dir_layout.addWidget(self.config_dir_browse_button)
        config_dir_layout.addWidget(self.open_config_dir_button)
        claude_layout.addRow("Dossier de config :", config_dir_layout)

        auth_buttons_layout = QHBoxLayout()
        auth_buttons_layout.addWidget(self.login_button)
        auth_buttons_layout.addWidget(self.logout_button)
        auth_buttons_layout.addStretch(1)
        claude_layout.addRow(auth_buttons_layout)

        claude_section.set_content_layout(claude_layout)

        # -- Ping manuel ponctuel.
        manual_box = QGroupBox("Ping manuel ponctuel")
        manual_box.setToolTip("Force un ping unique à l'heure indiquée (HH:MM).")
        manual_layout = QHBoxLayout()
        manual_layout.addWidget(self.manual_time_input)
        manual_layout.addWidget(self.schedule_ping_button)
        manual_layout.addStretch(1)
        manual_box.setLayout(manual_layout)

        buttons_layout = QHBoxLayout()
        buttons_layout.addWidget(self.apply_button)
        buttons_layout.addStretch(1)
        buttons_layout.addWidget(self.manual_ping_button)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setSpacing(10)
        content_layout.addWidget(status_box)
        content_layout.addWidget(settings_box)
        content_layout.addWidget(claude_section)
        content_layout.addWidget(manual_box)
        content_layout.addLayout(buttons_layout)
        content_layout.addStretch(1)

        # Défilable : déplier "Configuration Claude CLI" (ou juste une
        # fenêtre redimensionnée en dessous du confort) ne doit jamais
        # comprimer/chevaucher le contenu — au pire ça scrolle.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(content)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

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

        def _quota_html(pct_text: str, reset_at, quota_status: str) -> str:
            reset_str = f" (reset {format_timestamp(reset_at, tz)})" if reset_at else ""
            color = "#e06c75" if quota_status == "quota atteint" else "#98c379"
            badge = f" <span style='color:{color};'>● {quota_status}</span>" if quota_status != "—" else ""
            return f"{pct_text}{reset_str}{badge}"

        self.session_quota_label.setText(
            _quota_html(
                f"{status.session_pct}%" if status.session_pct is not None else "—",
                status.reset_at,
                status.quota_status_session,
            )
        )
        self.weekly_quota_label.setText(
            _quota_html(
                f"{status.weekly_used:.0f}%" if status.weekly_used is not None else "—",
                status.weekly_reset_at,
                status.quota_status_weekly,
            )
        )
        self.cli_status_label.setText(
            f"{status.auth_status} {'(disponible)' if status.cli_available else '(absent)'}"
        )
        self.last_probe_label.setText(format_timestamp(status.last_probe_at, tz))

        # Ne jamais écraser un champ modifié mais pas encore enregistré :
        # sinon le refresh périodique (toutes les 5s) écrase la saisie avant
        # même que l'utilisateur ait pu cliquer sur "Enregistrer", ce qui
        # ressemble à un rollback fantôme de l'UI. `blockSignals` évite que
        # ce setText/setChecked programmatique ne soit lui-même interprété
        # comme une modification utilisateur (pour la checkbox, dont le
        # signal `toggled` ne distingue pas saisie et code).
        if "fallback_enabled" not in self._dirty_fields:
            self.fallback_checkbox.blockSignals(True)
            self.fallback_checkbox.setChecked(status.fallback_enabled)
            self.fallback_checkbox.blockSignals(False)
        if "fallback_time" not in self._dirty_fields:
            self.fallback_time_input.setText(status.fallback_time)
        if "probe_interval" not in self._dirty_fields:
            self.probe_interval_input.setText(str(status.probe_interval_minutes))
        if "claude_path" not in self._dirty_fields:
            self.claude_path_input.setText(status.claude_executable)
        if "config_dir" not in self._dirty_fields:
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

        # Les champs sont désormais synchronisés avec l'état du service :
        # plus rien à protéger du prochain refresh périodique.
        self._dirty_fields.clear()

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
        self.setWindowIcon(app_icon())
        self._account_panels: dict[str, AccountPanel] = {}

        self._build_ui()
        self._setup_menu()
        self._setup_tray()

        # Le contenu de chaque onglet défile (QScrollArea) : la fenêtre peut
        # donc descendre sous un plancher raisonnable sans jamais comprimer
        # ni chevaucher le texte (au pire, une barre de défilement apparaît).
        self.setMinimumSize(420, 320)
        # Taille de départ confortable : tout est visible sans défiler pour
        # un compte dont la section "Configuration Claude CLI" est repliée
        # (l'état par défaut) — mesurée sur le contenu réel du panneau.
        self.resize(620, 540)

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
        self._tabs.currentChanged.connect(lambda _idx: self._update_remove_button_state())

        # Coin de la barre d'onglets : "Compte :" suivi des deux actions
        # (ajouter / supprimer l'onglet actuellement affiché), sur une seule
        # ligne compacte — pas besoin d'aller chercher un bouton par panneau.
        corner_widget = QWidget()
        corner_layout = QHBoxLayout(corner_widget)
        corner_layout.setContentsMargins(4, 2, 4, 2)
        corner_layout.setSpacing(4)
        corner_label = QLabel("Compte :")
        add_button = QPushButton("Ajouter")
        add_button.setToolTip("Ajouter un nouveau compte Claude")
        add_button.clicked.connect(self._add_account_dialog)
        self.remove_account_button = QPushButton("Supprimer")
        self.remove_account_button.setToolTip(
            "Supprimer le compte de l'onglet actuellement affiché"
        )
        self.remove_account_button.clicked.connect(self._remove_current_account)
        corner_layout.addWidget(corner_label)
        corner_layout.addWidget(add_button)
        corner_layout.addWidget(self.remove_account_button)
        self._tabs.setCornerWidget(corner_widget, Qt.TopRightCorner)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._tabs)
        self.setCentralWidget(central)

        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        self._update_remove_button_state()

    def _update_remove_button_state(self) -> None:
        current = self._tabs.currentWidget()
        self.remove_account_button.setEnabled(current in self._account_panels.values())

    def _add_account_tab(self, service: AccountService, select: bool = True) -> None:
        panel = AccountPanel(self.manager, service)
        self._account_panels[service.name] = panel
        logs_index = self._tabs.indexOf(self.log_tab) if hasattr(self, "log_tab") else -1
        if logs_index == -1:
            index = self._tabs.addTab(panel, service.name)
        else:
            index = logs_index
            self._tabs.insertTab(index, panel, service.name)
        if select:
            self._tabs.setCurrentIndex(index)
        if hasattr(self, "remove_account_button"):
            self._update_remove_button_state()

    def _remove_current_account(self) -> None:
        current = self._tabs.currentWidget()
        name = next(
            (n for n, p in self._account_panels.items() if p is current), None
        )
        if name is None:
            return
        reply = QMessageBox.question(
            self,
            "Supprimer ce compte",
            f"Supprimer le compte '{name}' ?\n\n"
            "Le scheduler de ce compte sera arrêté et le compte retiré de\n"
            "config.yaml. Les fichiers d'état/logs existants ne sont pas\n"
            "supprimés du disque.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.manager.remove_account(name)
        self._on_account_removed(name)

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
        self._update_remove_button_state()

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = QSystemTrayIcon(app_icon(), parent=self)
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
    app.setWindowIcon(app_icon())
    # Sous Wayland/GNOME, l'icône de la barre des tâches/dock est résolue
    # via le fichier .desktop associé à l'"app-id" de la fenêtre, pas
    # directement depuis le pixmap fourni ici — setWindowIcon() seul ne
    # suffit pas. desktopFileName() déclare cet app-id ; encore faut-il
    # qu'un ~/.local/share/applications/claudeping.desktop (Icon=claudeping)
    # existe réellement, voir install/install_linux.sh.
    app.setDesktopFileName("claudeping")
    window = ClaudePingWindow(manager, base_dir)
    window.show()
    app.exec()
