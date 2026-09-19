"""
dbconn.py — Ouverture des bases SQLite, réglée pour la charge.

Toutes les bases du moteur (central.db, sales.db de chaque tenant) passent
par `connect()` puis `keep_open()` :

  - journal WAL + synchronous=NORMAL : un commit n'attend plus une écriture
    physique du disque (fsync) à chaque fois, seulement aux points de
    contrôle. En WAL, ce mode ne peut PAS corrompre la base ; au pire une
    coupure de courant du serveur perd les dernières secondes d'écriture
    (les sauvegardes nocturnes restent la protection de fond).

  - `keep_open(path)` : garde une connexion « témoin » ouverte sur la base.
    Sans elle, chaque fermeture de la DERNIÈRE connexion force SQLite à
    reverser tout le journal WAL dans la base puis à le supprimer : c'était
    le coût principal mesuré par vente (débit x4 avec le témoin). Les témoins
    sont plafonnés (MAX_KEEPERS, les moins récemment utilisés sont fermés)
    pour borner les descripteurs de fichiers : 3 par base (db, -wal, -shm).
"""
import os
import sqlite3
import threading
from collections import OrderedDict

MAX_KEEPERS = 200

# chemin absolu -> (connexion témoin, identifiant du fichier)
_keepers: "OrderedDict[str, tuple[sqlite3.Connection, int]]" = OrderedDict()
_keepers_lock = threading.Lock()


def connect(path: str, timeout: float = 10) -> sqlite3.Connection:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _close_quietly(conn: sqlite3.Connection):
    try:
        conn.close()
    except sqlite3.Error:
        pass


def keep_open(path: str) -> None:
    """Garde une connexion témoin ouverte sur `path` (base déjà créée).

    Si le fichier a été supprimé puis recréé (tenant retiré puis réinscrit),
    l'ancien témoin pointe vers l'ancien fichier : on le remplace."""
    key = os.path.abspath(path)
    try:
        ino = os.stat(path).st_ino
    except OSError:
        return
    with _keepers_lock:
        cur = _keepers.get(key)
        if cur is not None and cur[1] == ino:
            _keepers.move_to_end(key)
            return
        if cur is not None:
            _close_quietly(cur[0])
            del _keepers[key]
        try:
            # check_same_thread=False : le témoin peut être fermé (éviction)
            # depuis un autre thread que celui qui l'a ouvert.
            conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
        except sqlite3.Error:
            return
        _keepers[key] = (conn, ino)
        while len(_keepers) > MAX_KEEPERS:
            _, (old, _) = _keepers.popitem(last=False)
            _close_quietly(old)
