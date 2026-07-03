"""
tenant_db.py — Base de données centrale HotspotPro SaaS
Gère les tenants (clients), leurs slugs, tokens et configuration.

Depuis la v2, tous les tenants sont servis par un seul processus
(tenant_hub.py). Le port par tenant est conservé uniquement pour
compatibilité avec d'anciennes installations.
"""
import sqlite3, os, threading, json

SAAS_DIR   = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")
CENTRAL_DB = os.path.join(SAAS_DIR, "central.db")

BASE_PORT = 8001   # Legacy : premier port attribué
MAX_PORT  = 8999   # Legacy : port maximum
_lock     = threading.Lock()


# ═══════════════════════════════════════════════
# INIT
# ═══════════════════════════════════════════════

def init_central_db():
    """Crée la base centrale si elle n'existe pas, applique les migrations."""
    os.makedirs(SAAS_DIR, exist_ok=True)
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tenants (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            slug         TEXT    NOT NULL UNIQUE,
            port         INTEGER NOT NULL UNIQUE,
            bot_token    TEXT,
            chat_id      TEXT,
            mikrotik_ip  TEXT,
            active       INTEGER DEFAULT 1,
            created_at   TEXT    DEFAULT (datetime('now'))
        )
    """)
    # Migrations silencieuses pour bases existantes
    for ddl in (
        "ALTER TABLE tenants ADD COLUMN prices TEXT DEFAULT NULL",
        "ALTER TABLE tenants ADD COLUMN router_name TEXT DEFAULT ''",
        "ALTER TABLE tenants ADD COLUMN router_token TEXT DEFAULT ''",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(CENTRAL_DB), exist_ok=True)
    c = sqlite3.connect(CENTRAL_DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=10000")
    return c


# ═══════════════════════════════════════════════
# PORTS (legacy — conservé pour compatibilité)
# ═══════════════════════════════════════════════

def next_available_port() -> int:
    """Retourne le prochain port libre entre BASE_PORT et MAX_PORT."""
    conn = _conn()
    used = {r[0] for r in conn.execute("SELECT port FROM tenants")}
    conn.close()
    for p in range(BASE_PORT, MAX_PORT + 1):
        if p not in used:
            return p
    raise RuntimeError("Plus de ports disponibles (8001-8999 tous occupés)")


# ═══════════════════════════════════════════════
# CRUD TENANTS
# ═══════════════════════════════════════════════

def add_tenant(name: str, slug: str, bot_token: str,
               chat_id: str, mikrotik_ip: str,
               router_name: str = "", router_token: str = "",
               prices: dict | None = None) -> dict:
    """Crée un nouveau tenant."""
    with _lock:
        port = next_available_port()
        conn = _conn()
        conn.execute("""
            INSERT INTO tenants (name, slug, port, bot_token, chat_id,
                                 mikrotik_ip, router_name, router_token, prices)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, slug, port, bot_token, chat_id, mikrotik_ip,
              router_name, router_token,
              json.dumps(prices) if prices else None))
        conn.commit()
        row = conn.execute("SELECT * FROM tenants WHERE slug=?", (slug,)).fetchone()
        conn.close()
        return dict(row)


def get_tenant(slug: str) -> dict | None:
    """Retourne un tenant par son slug."""
    conn = _conn()
    row  = conn.execute("SELECT * FROM tenants WHERE slug=?", (slug,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_tenants(active_only: bool = False) -> list[dict]:
    """Retourne tous les tenants."""
    conn  = _conn()
    query = "SELECT * FROM tenants"
    if active_only:
        query += " WHERE active=1"
    rows = conn.execute(query + " ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def slug_exists(slug: str) -> bool:
    conn = _conn()
    r    = conn.execute("SELECT 1 FROM tenants WHERE slug=?", (slug,)).fetchone()
    conn.close()
    return r is not None


def delete_tenant(slug: str):
    """Supprime un tenant de la base centrale."""
    conn = _conn()
    conn.execute("DELETE FROM tenants WHERE slug=?", (slug,))
    conn.commit()
    conn.close()


def update_tenant(slug: str, **kwargs):
    """Met à jour des champs d'un tenant."""
    allowed = {"bot_token", "chat_id", "mikrotik_ip", "active", "name",
               "router_name", "router_token", "prices"}
    fields  = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    if "prices" in fields and isinstance(fields["prices"], dict):
        fields["prices"] = json.dumps(fields["prices"])
    set_clause = ", ".join(f"{k}=?" for k in fields)
    conn = _conn()
    conn.execute(f"UPDATE tenants SET {set_clause} WHERE slug=?",
                 (*fields.values(), slug))
    conn.commit()
    conn.close()


def set_tenant_active(slug: str, active: bool):
    """Active/suspend un tenant (le hub cesse d'accepter ses ventes)."""
    update_tenant(slug, active=1 if active else 0)


# ═══════════════════════════════════════════════
# DONNÉES DE VENTE (lecture directe des sales.db)
# ═══════════════════════════════════════════════

def tenant_sales_db(slug: str) -> str:
    return os.path.join(SAAS_DIR, "tenants", slug, "sales.db")


def get_last_activity(slug: str) -> dict:
    """Retourne la dernière vente enregistrée pour un tenant."""
    tenant_db_path = tenant_sales_db(slug)
    if not os.path.exists(tenant_db_path):
        return {"last_seen": None, "seller": None}
    try:
        conn = sqlite3.connect(tenant_db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        row  = conn.execute(
            "SELECT seller, created_at FROM sales ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return {"last_seen": row["created_at"], "seller": row["seller"]}
    except Exception:
        pass
    return {"last_seen": None, "seller": None}
