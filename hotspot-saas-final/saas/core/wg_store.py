"""
wg_store.py — Stockage des pairs WireGuard par tenant (central.db).

Deux tables dans la base centrale :
  wg_server : la paire de clés du serveur + son endpoint public (une ligne).
  wg_peers  : un pair par tenant (slug) — IP de tunnel, clés, identifiants API.

Les secrets (clé privée du routeur, mot de passe API, clé privée serveur)
sont chiffrés au repos (secretbox). Les champs de routage non secrets
(IP de tunnel, clé PUBLIQUE du routeur) restent en clair : le service root
qui applique les pairs (`wg set`) n'a besoin que de ceux-là.
"""
import os
import sqlite3

import secretbox
import wireguard as wg

SAAS_DIR   = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")
CENTRAL_DB = os.path.join(SAAS_DIR, "central.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(CENTRAL_DB), exist_ok=True)
    c = sqlite3.connect(CENTRAL_DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    return c


def ensure_schema():
    c = _conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS wg_server (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            private_key TEXT NOT NULL,
            public_key  TEXT NOT NULL,
            endpoint    TEXT NOT NULL,
            udp_port    INTEGER NOT NULL DEFAULT 51820
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS wg_peers (
            slug               TEXT PRIMARY KEY,
            tunnel_ip          TEXT NOT NULL UNIQUE,
            router_public_key  TEXT NOT NULL,
            router_private_key TEXT NOT NULL,
            api_user           TEXT NOT NULL,
            api_pass           TEXT NOT NULL,
            created_at         TEXT DEFAULT (datetime('now'))
        )
    """)
    # Pairs « opérateur » (VPN d'accès au routeur, forfaits 8000/15000) :
    # le poste du client se connecte au serveur et atteint son routeur.
    # Multi-appareils (forfait 12 mois) : plusieurs lignes par slug -> clé
    # primaire sur un id auto-incrémenté + un libellé d'appareil.
    admin_cols = [r["name"] for r in
                  c.execute("PRAGMA table_info(wg_admin_peers)").fetchall()]
    need_migrate = bool(admin_cols) and "id" not in admin_cols
    if need_migrate:
        # Ancien schéma (une ligne par slug) -> multi. Le RENAME peut échouer
        # si un autre process a déjà migré entre-temps (2 workers) : on tolère.
        try:
            c.execute("ALTER TABLE wg_admin_peers RENAME TO wg_admin_peers_old")
        except sqlite3.OperationalError:
            need_migrate = False
    c.execute("""
        CREATE TABLE IF NOT EXISTS wg_admin_peers (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slug        TEXT NOT NULL,
            tunnel_ip   TEXT NOT NULL UNIQUE,
            public_key  TEXT NOT NULL,
            private_key TEXT NOT NULL,
            label       TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_admin_slug ON wg_admin_peers(slug)")
    if need_migrate and c.execute("PRAGMA table_info(wg_admin_peers_old)").fetchall():
        c.execute("""INSERT INTO wg_admin_peers
                        (slug, tunnel_ip, public_key, private_key, label, created_at)
                     SELECT slug, tunnel_ip, public_key, private_key,
                            'Appareil 1', created_at
                     FROM wg_admin_peers_old""")
        c.execute("DROP TABLE wg_admin_peers_old")
    # Dernier handshake par clé publique : écrit par le service root wg_sync
    # (seul à pouvoir lire `wg show`), lu par l'app web pour afficher l'état
    # de connexion (routeur en ligne, poste opérateur connecté).
    c.execute("""
        CREATE TABLE IF NOT EXISTS wg_handshakes (
            public_key     TEXT PRIMARY KEY,
            last_handshake INTEGER NOT NULL DEFAULT 0,
            updated_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    c.commit()
    c.close()


# ── Serveur ─────────────────────────────────────────────────────

def set_server(endpoint: str, udp_port: int = wg.WG_UDP_PORT,
               private_key: str = None, public_key: str = None) -> dict:
    """Initialise (ou met à jour) la paire serveur + endpoint. Génère la
    paire si elle n'est pas fournie. Retourne l'état serveur."""
    ensure_schema()
    if not private_key or not public_key:
        private_key, public_key = wg.gen_keypair()
    c = _conn()
    row = c.execute("SELECT * FROM wg_server WHERE id=1").fetchone()
    if row:
        c.execute("UPDATE wg_server SET endpoint=?, udp_port=? WHERE id=1",
                  (endpoint, udp_port))
        priv, pub = row["private_key"], row["public_key"]
    else:
        c.execute("""INSERT INTO wg_server (id, private_key, public_key, endpoint, udp_port)
                     VALUES (1, ?, ?, ?, ?)""",
                  (secretbox.encrypt(private_key), public_key, endpoint, udp_port))
        priv, pub = secretbox.encrypt(private_key), public_key
    c.commit()
    c.close()
    return {"public_key": pub, "endpoint": endpoint, "udp_port": udp_port,
            "private_key": secretbox.decrypt(priv)}


def get_server() -> dict | None:
    ensure_schema()
    c = _conn()
    row = c.execute("SELECT * FROM wg_server WHERE id=1").fetchone()
    c.close()
    if not row:
        return None
    return {"public_key": row["public_key"], "endpoint": row["endpoint"],
            "udp_port": row["udp_port"],
            "private_key": secretbox.decrypt(row["private_key"])}


# ── Pairs (tenants) ─────────────────────────────────────────────

def provision_peer(slug: str) -> dict:
    """Crée (ou retourne) le pair d'un tenant et rend le bloc à coller.
    Idempotent : rappeler pour le même slug renvoie le même pair."""
    server = get_server()
    if not server:
        raise RuntimeError("Serveur WireGuard non initialisé (set_server).")

    ensure_schema()
    existing = get_peer(slug)
    if existing:
        priv, pub = existing["router_private_key"], existing["router_public_key"]
        tunnel_ip = existing["tunnel_ip"]
        api_user, api_pass = existing["api_user"], existing["api_pass"]
    else:
        priv, pub = wg.gen_keypair()
        api_user, api_pass = wg.API_USER, wg.gen_api_password()
        c = _conn()
        used = {r["tunnel_ip"] for r in c.execute("SELECT tunnel_ip FROM wg_peers")}
        tunnel_ip = wg.next_tunnel_ip(used)
        c.execute("""
            INSERT INTO wg_peers (slug, tunnel_ip, router_public_key,
                                  router_private_key, api_user, api_pass)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (slug, tunnel_ip, pub, secretbox.encrypt(priv),
              api_user, secretbox.encrypt(api_pass)))
        c.commit()
        c.close()

    block = wg.render_client_block(
        tunnel_ip=tunnel_ip, router_private_key=priv,
        server_public_key=server["public_key"], vps_endpoint=server["endpoint"],
        api_user=api_user, api_pass=api_pass, wg_port=server["udp_port"])
    return {"slug": slug, "tunnel_ip": tunnel_ip, "router_public_key": pub,
            "api_user": api_user, "block": block}


def get_peer(slug: str) -> dict | None:
    """Pair d'un tenant, secrets déchiffrés (pour la génération du bloc et
    l'API vers le routeur)."""
    ensure_schema()
    c = _conn()
    row = c.execute("SELECT * FROM wg_peers WHERE slug=?", (slug,)).fetchone()
    c.close()
    if not row:
        return None
    d = dict(row)
    d["router_private_key"] = secretbox.decrypt(d["router_private_key"])
    d["api_pass"] = secretbox.decrypt(d["api_pass"])
    return d


def all_peers_public() -> list[dict]:
    """Infos NON secrètes de tous les pairs, pour le service root qui
    applique la config (`wg set`) : slug, IP de tunnel, clé publique."""
    ensure_schema()
    c = _conn()
    rows = c.execute("SELECT slug, tunnel_ip, router_public_key FROM wg_peers").fetchall()
    c.close()
    return [dict(r) for r in rows]


# ── Pairs opérateur (VPN d'accès au routeur) ────────────────────

def _used_ips(c) -> set:
    """Toutes les IP de tunnel déjà attribuées (routeurs + opérateurs)."""
    used = {r["tunnel_ip"] for r in c.execute("SELECT tunnel_ip FROM wg_peers")}
    used |= {r["tunnel_ip"] for r in c.execute("SELECT tunnel_ip FROM wg_admin_peers")}
    return used


def _admin_config(server: dict, router_ip: str, priv: str, ip: str) -> str:
    return wg.render_admin_config(
        admin_private_key=priv, admin_ip=ip,
        server_public_key=server["public_key"], vps_endpoint=server["endpoint"],
        router_ip=router_ip, wg_port=server["udp_port"])


def _require_server_router(slug: str):
    server = get_server()
    if not server:
        raise RuntimeError("Serveur WireGuard non initialisé (set_server).")
    router = get_peer(slug)
    if not router:
        raise RuntimeError("Routeur non provisionné pour ce tenant.")
    return server, router


def add_admin_peer(slug: str, label: str = "") -> dict:
    """Crée un NOUVEAU poste opérateur (nouvelle clé + IP) pour ce tenant et
    rend son fichier .conf. Utilisé pour le multi-appareils (12 mois)."""
    server, router = _require_server_router(slug)
    ensure_schema()
    priv, pub = wg.gen_keypair()
    c = _conn()
    ip = wg.next_tunnel_ip(_used_ips(c))
    cur = c.execute("""INSERT INTO wg_admin_peers (slug, tunnel_ip, public_key,
                                                   private_key, label)
                       VALUES (?, ?, ?, ?, ?)""",
                    (slug, ip, pub, secretbox.encrypt(priv),
                     (label or "").strip()[:60]))
    dev_id = cur.lastrowid
    c.commit()
    c.close()
    return {"id": dev_id, "slug": slug, "tunnel_ip": ip,
            "router_ip": router["tunnel_ip"], "label": (label or "").strip()[:60],
            "config": _admin_config(server, router["tunnel_ip"], priv, ip)}


def provision_admin_peer(slug: str) -> dict:
    """Garantit qu'AU MOINS un poste opérateur existe et rend le plus ancien
    (idempotent). Chemin mono-appareil (forfait 8000) et amorçage du multi.

    Nécessite que le routeur du tenant soit déjà provisionné."""
    _require_server_router(slug)          # lève si serveur/routeur absent
    ensure_schema()
    cfg = get_admin_config(slug)          # le plus ancien
    if cfg:
        return cfg
    return add_admin_peer(slug, "Appareil 1")


def _admin_row(slug: str, device_id: int | None):
    c = _conn()
    if device_id is None:
        row = c.execute("SELECT * FROM wg_admin_peers WHERE slug=? ORDER BY id LIMIT 1",
                        (slug,)).fetchone()
    else:
        row = c.execute("SELECT * FROM wg_admin_peers WHERE slug=? AND id=?",
                        (slug, device_id)).fetchone()
    c.close()
    return row


def get_admin_config(slug: str, device_id: int | None = None) -> dict | None:
    """Config .conf d'un appareil (le plus ancien si device_id absent)."""
    ensure_schema()
    server = get_server()
    router = get_peer(slug)
    if not server or not router:
        return None
    row = _admin_row(slug, device_id)
    if not row:
        return None
    priv = secretbox.decrypt(row["private_key"])
    return {"id": row["id"], "slug": slug, "tunnel_ip": row["tunnel_ip"],
            "router_ip": router["tunnel_ip"], "label": row["label"],
            "config": _admin_config(server, router["tunnel_ip"], priv, row["tunnel_ip"])}


def get_admin_peer(slug: str, device_id: int | None = None) -> dict | None:
    """Pair opérateur, secrets déchiffrés. Le plus ancien si device_id absent
    (compatibilité mono-appareil)."""
    ensure_schema()
    row = _admin_row(slug, device_id)
    if not row:
        return None
    d = dict(row)
    d["private_key"] = secretbox.decrypt(d["private_key"])
    return d


def list_admin_peers(slug: str) -> list[dict]:
    """Appareils opérateur du tenant, SANS secret, pour l'affichage UI."""
    ensure_schema()
    c = _conn()
    rows = c.execute("""SELECT id, slug, tunnel_ip, public_key, label, created_at
                        FROM wg_admin_peers WHERE slug=? ORDER BY id""",
                     (slug,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def count_admin_peers(slug: str) -> int:
    ensure_schema()
    c = _conn()
    n = c.execute("SELECT COUNT(*) FROM wg_admin_peers WHERE slug=?", (slug,)).fetchone()[0]
    c.close()
    return int(n)


def delete_admin_peer(slug: str, device_id: int) -> bool:
    """Supprime un poste opérateur. wg_sync retirera ensuite le pair de
    l'interface ; l'IP redevient disponible."""
    ensure_schema()
    c = _conn()
    cur = c.execute("DELETE FROM wg_admin_peers WHERE slug=? AND id=?",
                    (slug, device_id))
    c.commit()
    n = cur.rowcount
    c.close()
    return n > 0


def all_admin_peers_public() -> list[dict]:
    """slug, IP de tunnel, clé publique des pairs opérateur (pour wg set)."""
    ensure_schema()
    c = _conn()
    rows = c.execute("SELECT slug, tunnel_ip, public_key FROM wg_admin_peers").fetchall()
    c.close()
    return [dict(r) for r in rows]


def delete_all_for_slug(slug: str) -> None:
    """Supprime le pair routeur ET tous les pairs opérateur d'un tenant
    (suppression de compte). wg_sync retirera ensuite ces pairs de l'interface
    au prochain passage. Best-effort : n'échoue pas si rien à supprimer."""
    ensure_schema()
    c = _conn()
    c.execute("DELETE FROM wg_admin_peers WHERE slug=?", (slug,))
    c.execute("DELETE FROM wg_peers WHERE slug=?", (slug,))
    c.commit()
    c.close()


# ── Handshakes (état de connexion) ──────────────────────────────

def record_handshakes(mapping: dict) -> None:
    """Persiste l'epoch unix du dernier handshake par clé publique.
    `mapping` : {clé_publique: epoch_int}. Appelé par wg_sync (root)."""
    if not mapping:
        return
    ensure_schema()
    c = _conn()
    c.executemany("""
        INSERT INTO wg_handshakes (public_key, last_handshake, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(public_key) DO UPDATE SET
            last_handshake = excluded.last_handshake,
            updated_at     = datetime('now')
    """, [(k, int(v or 0)) for k, v in mapping.items()])
    c.commit()
    c.close()


def last_handshake(public_key: str) -> int:
    """Epoch unix du dernier handshake connu pour cette clé publique (0 si
    inconnu). Comparer à time.time() pour juger de la fraîcheur/connexion."""
    if not public_key:
        return 0
    ensure_schema()
    c = _conn()
    row = c.execute("SELECT last_handshake FROM wg_handshakes WHERE public_key=?",
                    (public_key,)).fetchone()
    c.close()
    return int(row["last_handshake"]) if row else 0
