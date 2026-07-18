"""Verrou mono-instance cross-platform (Windows/Linux/macOS).

Empêche de faire tourner plusieurs instances longue-durée de ClaudePing en
même temps (mode --service et/ou --ui, dans n'importe quelle combinaison),
ce qui provoquerait des probes/pings dupliqués pour les mêmes comptes —
et donc des notifications en double, voire des pings envoyés plusieurs
fois pour le même reset.

Utilise un verrou exclusif au niveau de l'OS sur un fichier dédié
(flock sur Linux/macOS, msvcrt.locking sur Windows) plutôt qu'un fichier
PID : si le process précédent a crashé, l'OS libère le verrou tout seul —
pas de nettoyage manuel, pas de faux "déjà lancé" après un crash.
"""

from __future__ import annotations

import atexit
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class SingleInstanceError(RuntimeError):
    """Une autre instance de ClaudePing tient déjà le verrou."""


# Référence forte au handle du fichier verrou : ne jamais la laisser être
# ramassée par le GC / le handle fermé, sinon le verrou OS est relâché.
_lock_handle = None


def acquire_single_instance_lock(lock_path: Path) -> None:
    """Acquiert le verrou mono-instance pour tout le process courant.

    Lève SingleInstanceError si une autre instance (UI ou service) tourne
    déjà. Le verrou est automatiquement relâché à la fin du process (sortie
    normale via atexit, ou par l'OS si le process se termine brutalement).
    """
    global _lock_handle

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")

    if sys.platform == "win32":
        # msvcrt.locking exige au moins 1 octet dans le fichier.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write("0")
            handle.flush()
        handle.seek(0)

    try:
        if sys.platform == "win32":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise SingleInstanceError(
            "Une autre instance de ClaudePing (UI ou service) tourne déjà "
            f"sur ce poste (verrou : {lock_path})."
        )

    # PID courant écrit pour du debug manuel uniquement (pas utilisé pour
    # détecter les instances : c'est le verrou OS qui fait foi).
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
    except OSError:
        pass

    _lock_handle = handle
    atexit.register(release_single_instance_lock)


def release_single_instance_lock() -> None:
    """Relâche le verrou explicitement (aussi appelé automatiquement à la sortie)."""
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        if sys.platform == "win32":
            _lock_handle.seek(0)
            msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        _lock_handle.close()
        _lock_handle = None
