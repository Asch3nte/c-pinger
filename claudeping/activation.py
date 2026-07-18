"""Réactivation d'une fenêtre ClaudePing déjà ouverte, entre deux process.

Quand le verrou mono-instance (claudeping.singleton) empêche une nouvelle
instance `--ui` de démarrer, on veut — plutôt que simplement échouer — que
la fenêtre de l'instance déjà active se réaffiche (visible ou masquée en
tray/derrière d'autres fenêtres). Utile en particulier sur les
environnements où l'icône system tray n'est pas disponible (ex : Linux
sans les paquets de notification adéquats), où il n'existe sinon aucun
moyen de retrouver la fenêtre une fois masquée.

Mécanisme : un petit socket TCP en loopback (127.0.0.1), sur un port
dérivé du dossier d'installation (BASE_DIR) pour que deux installations
indépendantes de ClaudePing sur le même poste n'interfèrent jamais entre
elles. Ce socket est un pur "coup de coude" best-effort : s'il ne peut pas
être ouvert (port pris par autre chose) ou si personne ne répond, l'appli
continue de fonctionner normalement — ce n'est jamais ce mécanisme qui
fait autorité sur l'exclusivité mono-instance (c'est le verrou OS de
`claudeping.singleton`).
"""

from __future__ import annotations

import hashlib
import socket
import threading
from pathlib import Path
from typing import Callable

ACTIVATION_HOST = "127.0.0.1"
_ACTIVATION_MAGIC = b"CLAUDEPING_ACTIVATE"
_ACK = b"OK\n"


def activation_port_for(base_dir: Path) -> int:
    """Port TCP local déterministe, stable pour un même dossier d'installation.

    Dérivé par hash de BASE_DIR (pas par `hash()` qui est salé aléatoirement
    par process) pour que deux lancements du même install s'accordent
    toujours sur le même port, et que deux installs différentes n'utilisent
    quasiment jamais le même port.
    """
    digest = hashlib.sha256(str(Path(base_dir).resolve()).encode("utf-8")).digest()
    offset = int.from_bytes(digest[:2], "big") % 10_000
    return 50_000 + offset  # plage haute, quasi-privée (IANA dynamique/privée)


def request_activation(base_dir: Path, timeout: float = 1.0) -> bool:
    """Demande à une éventuelle instance UI déjà lancée de se réafficher.

    Retourne True si une instance UI a répondu (donc sa fenêtre devrait
    s'être réaffichée), False sinon — soit personne n'écoute sur ce port
    (aucune instance, ou une instance `--service` headless sans fenêtre),
    soit la demande a échoué pour une autre raison. Ne lève jamais.
    """
    port = activation_port_for(base_dir)
    try:
        with socket.create_connection((ACTIVATION_HOST, port), timeout=timeout) as sock:
            sock.sendall(_ACTIVATION_MAGIC + b"\n")
            sock.settimeout(timeout)
            return sock.recv(16) == _ACK
    except OSError:
        return False


class ActivationServer:
    """Écoute les demandes de réaffichage pour cette instance UI.

    `on_activate` est appelé depuis le thread d'écoute (PAS le thread GUI) :
    à l'appelant de le faire retomber sur le thread GUI (ex : via un signal
    Qt), comme pour tout callback cross-thread dans cette appli.
    """

    def __init__(self, base_dir: Path, on_activate: Callable[[], None]) -> None:
        self._port = activation_port_for(base_dir)
        self._on_activate = on_activate
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = False

    def start(self) -> bool:
        """Démarre l'écoute. Retourne False si le port est indisponible
        (dégradation gracieuse : la réactivation à distance ne marchera
        juste pas pour cette instance, rien d'autre n'est affecté)."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((ACTIVATION_HOST, self._port))
            sock.listen(4)
            sock.settimeout(1.0)
        except OSError:
            sock.close()
            return False

        self._sock = sock
        self._thread = threading.Thread(
            target=self._serve, daemon=True, name="ClaudePingActivation"
        )
        self._thread.start()
        return True

    def _serve(self) -> None:
        while not self._stop:
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(64)
                if data.startswith(_ACTIVATION_MAGIC):
                    self._on_activate()
                    conn.sendall(_ACK)
            except OSError:
                pass
            finally:
                conn.close()

    def stop(self) -> None:
        self._stop = True
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
