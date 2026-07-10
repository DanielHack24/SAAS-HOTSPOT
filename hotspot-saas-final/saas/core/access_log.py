"""
access_log.py — Journal d'accès des routeurs (central.db), partagé entre le
hub (écrit à chaque notification On-Login) et l'app admin (lecture + détection
de partage).

But : repérer le partage d'un même abonnement sur plusieurs routeurs. Chaque
notification de vente arrive avec l'IP publique du routeur. Un opérateur légitime
a UNE IP WAN (qui change rarement). Deux routeurs qui partagent le même script
On-Login apparaissent comme deux IP publiques distinctes sous le même slug.

Stockage borné : une ligne par (slug, source_ip) — pas une par hit — avec un
compteur. La table reste petite quel que soit le trafic wifi, et le nombre d'IP
distinctes par slug EST le signal de partage.
"""
import ipaddress
import os
import sqlite3

SAAS_DIR   = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")
CENTRAL_DB = os.path.join(SAAS_DIR, "central.db")


def is_public_router_ip(ip: str) -> bool:
    """True si `ip` peut être l'IP WAN publique d'un routeur.

    Un routeur légitime joint le hub par l'internet : son IP source est
    toujours une IP publique. On écarte donc :
      - le loopback (127.0.0.1) quand nginx ne pose pas X-Real-IP,
      - les plages privées / link-local (tunnel WireGuard 10.66.x, LAN),
      - l'IP publique du VPS lui-même (auto-tests, boucle SNAT).
    Ces adresses ne représentent jamais un routeur distinct : les compter
    faisait apparaître 2 « IP » sous un même slug et levait de fausses
    alertes de partage."""
    ip = (ip or "").strip()
    if not ip or ip == os.environ.get("VPS_PUBLIC_IP", "").strip():
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


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
        CREATE TABLE IF NOT EXISTS router_access (
            slug       TEXT NOT NULL,
            source_ip  TEXT NOT NULL,
            router_id  TEXT    DEFAULT '',
            device_id  TEXT    DEFAULT '',
            last_via   TEXT    DEFAULT 'on-login',
            hits       INTEGER DEFAULT 0,
            bad_token  INTEGER DEFAULT 0,
            first_seen TEXT    DEFAULT (datetime('now')),
            last_seen  TEXT    DEFAULT (datetime('now')),
            PRIMARY KEY (slug, source_ip)
        )
    """)
    # Migrations douces pour les bases antérieures
    for ddl in (
        "ALTER TABLE router_access ADD COLUMN last_via TEXT DEFAULT 'on-login'",
        "ALTER TABLE router_access ADD COLUMN device_id TEXT DEFAULT ''",
    ):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass
    c.execute("CREATE INDEX IF NOT EXISTS idx_access_slug ON router_access(slug)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_access_seen ON router_access(last_seen)")
    c.commit()
    c.close()


def record(slug: str, source_ip: str, router_id: str = "",
           token_ok: bool = True, via: str = "on-login", device_id: str = ""):
    """Enregistre un accès (upsert par slug+IP). Best-effort : ne lève jamais
    dans le chemin d'une requête. `token_ok=False` incrémente bad_token.
    `via` : canal d'observation ('on-login' = notif de vente, 'wg-tunnel' =
    endpoint WireGuard vu par le serveur).

    `router_id` = identité (hostname) du routeur, pour l'affichage.
    `device_id` = empreinte STABLE de l'appareil (numéro de série matériel,
    sinon hostname) : c'est ELLE qui sert à détecter le partage, pour ne pas
    lever de fausse alerte quand l'IP WAN change (réseau mobile). Un champ vide
    n'écrase pas la valeur déjà connue."""
    if not slug or not source_ip:
        return
    # Ne journalise que les IP publiques : loopback/tunnel/IP du VPS ne sont
    # pas des routeurs distincts et provoquaient de fausses alertes de partage.
    if not is_public_router_ip(source_ip):
        return
    router_id = (router_id or "").strip()[:120]
    device_id = (device_id or "").strip()[:120]
    via = (via or "on-login").strip()[:20]
    bad = 0 if token_ok else 1
    try:
        ensure_schema()
        c = _conn()
        c.execute("""
            INSERT INTO router_access (slug, source_ip, router_id, device_id, last_via, hits, bad_token)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(slug, source_ip) DO UPDATE SET
                hits      = hits + 1,
                bad_token = bad_token + ?,
                last_via  = excluded.last_via,
                router_id = CASE WHEN excluded.router_id != ''
                                 THEN excluded.router_id ELSE router_access.router_id END,
                device_id = CASE WHEN excluded.device_id != ''
                                 THEN excluded.device_id ELSE router_access.device_id END,
                last_seen = datetime('now')
        """, (slug, source_ip, router_id, device_id, via, bad, bad))
        c.commit()
        c.close()
    except Exception as e:  # journalisation ne doit jamais casser une vente
        print(f"[ACCESS_LOG] échec record {slug}/{source_ip}: {e}", flush=True)


def recent(slug: str = None, limit: int = 300) -> list[dict]:
    """Lignes d'accès (les plus récentes d'abord). Filtre par slug si fourni."""
    ensure_schema()
    c = _conn()
    if slug:
        rows = c.execute("""SELECT * FROM router_access WHERE slug=?
                            ORDER BY last_seen DESC LIMIT ?""", (slug, limit)).fetchall()
    else:
        rows = c.execute("""SELECT * FROM router_access
                            ORDER BY last_seen DESC LIMIT ?""", (limit,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def sharing_report(window_hours: int = 48, min_devices: int = 2) -> list[dict]:
    """Slugs vus depuis >= `min_devices` APPAREILS distincts sur la fenêtre.

    La détection se base sur l'empreinte de l'appareil (`device_id` = numéro de
    série matériel, sinon hostname), PAS sur l'IP : un routeur dont l'IP WAN
    change (réseau mobile) reste UN seul appareil et ne lève donc plus de fausse
    alerte. Deux appareils distincts sous le même slug = script recopié sur un
    2ᵉ routeur non payé."""
    ensure_schema()
    c = _conn()
    rows = c.execute("""
        SELECT slug,
               COUNT(DISTINCT source_ip)                                        AS ip_count,
               COUNT(DISTINCT CASE WHEN device_id != '' THEN device_id END)     AS device_count,
               SUM(hits)                                                        AS total_hits,
               SUM(bad_token)                                                   AS bad_hits,
               GROUP_CONCAT(DISTINCT source_ip)                                 AS ips,
               GROUP_CONCAT(DISTINCT CASE WHEN device_id != '' THEN device_id END) AS devices,
               GROUP_CONCAT(DISTINCT CASE WHEN router_id != '' THEN router_id END) AS names,
               GROUP_CONCAT(DISTINCT last_via)                                  AS vias,
               MAX(last_seen)                                                   AS last_seen
        FROM router_access
        WHERE last_seen >= datetime('now', ?)
        GROUP BY slug
        HAVING device_count >= ?
        ORDER BY device_count DESC, total_hits DESC
    """, (f"-{int(window_hours)} hours", min_devices)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def prune(days: int = 120) -> int:
    """Supprime les IP plus vues depuis `days` jours. Retourne le nb supprimé."""
    ensure_schema()
    c = _conn()
    cur = c.execute("DELETE FROM router_access WHERE last_seen < datetime('now', ?)",
                    (f"-{int(days)} days",))
    n = cur.rowcount
    c.commit()
    c.close()
    return n
