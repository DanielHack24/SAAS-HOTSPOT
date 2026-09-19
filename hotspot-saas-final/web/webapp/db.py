"""
db.py — Base de données web (clients, abonnements, paiements)
"""
import sqlite3, secrets
from datetime import datetime

import config
from security import hash_password


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(config.WEB_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    # En WAL, NORMAL ne peut pas corrompre la base et évite une écriture
    # physique du disque à chaque commit (voir saas/core/dbconn.py).
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_web_db():
    conn = get_db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT    NOT NULL UNIQUE,
            password_hash TEXT    NOT NULL,
            full_name     TEXT    NOT NULL,
            phone         TEXT,
            created_at    TEXT    DEFAULT (datetime('now','localtime')),
            is_admin      INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id   INTEGER NOT NULL REFERENCES clients(id),
            plan        TEXT    NOT NULL,
            start_date  TEXT    NOT NULL,
            end_date    TEXT    NOT NULL,
            active      INTEGER DEFAULT 1,
            slug        TEXT,
            bot_token   TEXT,
            chat_id     TEXT,
            mikrotik_ip TEXT,
            provisioned INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id       INTEGER NOT NULL REFERENCES clients(id),
            subscription_id INTEGER,
            plan            TEXT    NOT NULL,
            amount          INTEGER NOT NULL,
            method          TEXT    NOT NULL,
            reference       TEXT,
            status          TEXT    DEFAULT 'pending',
            created_at      TEXT    DEFAULT (datetime('now','localtime')),
            confirmed_at    TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            type      TEXT    NOT NULL,
            message   TEXT    NOT NULL,
            sent_at   TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS mikrotik_devices (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
            client_id       INTEGER NOT NULL REFERENCES clients(id),
            label           TEXT    NOT NULL DEFAULT 'Mon MikroTik',
            ip              TEXT    NOT NULL,
            slug            TEXT,
            provisioned     INTEGER DEFAULT 0,
            created_at      TEXT    DEFAULT (datetime('now','localtime'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS mikrotik_payments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id    INTEGER NOT NULL REFERENCES clients(id),
            device_id    INTEGER,
            plan         TEXT    NOT NULL DEFAULT '1m',
            amount       INTEGER NOT NULL DEFAULT 2000,
            method       TEXT    NOT NULL,
            reference    TEXT,
            status       TEXT    DEFAULT 'pending',
            created_at   TEXT    DEFAULT (datetime('now','localtime')),
            confirmed_at TEXT
        )
    """)

    # Inscriptions en attente de vérification e-mail : le compte n'est créé
    # dans `clients` qu'après saisie du code reçu par e-mail (une ligne par
    # e-mail, remplacée à chaque nouvelle demande).
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_registrations (
            email         TEXT PRIMARY KEY,
            full_name     TEXT NOT NULL,
            phone         TEXT,
            password_hash TEXT NOT NULL,
            plan          TEXT,
            code_hash     TEXT NOT NULL,
            expires_at    TEXT NOT NULL,
            attempts      INTEGER DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id      TEXT NOT NULL,
            tg_username  TEXT,
            tg_name      TEXT,
            category     TEXT,
            message      TEXT NOT NULL,
            status       TEXT DEFAULT 'open',
            admin_msg_id TEXT,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            answered_at  TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS support_sessions (
            chat_id    TEXT PRIMARY KEY,
            state      TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS support_accounts (
            chat_id   TEXT PRIMARY KEY,
            client_id INTEGER NOT NULL,
            linked_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS support_link_tokens (
            token      TEXT PRIMARY KEY,
            client_id  INTEGER NOT NULL,
            expires_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS support_notify_state (
            client_id  INTEGER NOT NULL,
            kind       TEXT NOT NULL,
            state      TEXT,
            updated_at TEXT DEFAULT (datetime('now','localtime')),
            PRIMARY KEY (client_id, kind)
        )
    """)
    # Tentatives de connexion (rate limiting partagé entre workers)
    c.execute("""
        CREATE TABLE IF NOT EXISTS rate_attempts (
            key TEXT NOT NULL,
            ts  REAL NOT NULL
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_rate_attempts ON rate_attempts(key, ts)")

    # Migrations silencieuses pour bases existantes
    for ddl in (
        "ALTER TABLE mikrotik_payments ADD COLUMN plan TEXT NOT NULL DEFAULT '1m'",
        "ALTER TABLE subscriptions ADD COLUMN prices TEXT DEFAULT NULL",
        "ALTER TABLE subscriptions ADD COLUMN router_name TEXT DEFAULT 'Routeur principal'",
        "ALTER TABLE subscriptions ADD COLUMN router_token TEXT DEFAULT NULL",
        "ALTER TABLE mikrotik_devices ADD COLUMN end_date TEXT DEFAULT NULL",
        "ALTER TABLE mikrotik_devices ADD COLUMN active INTEGER DEFAULT 1",
        "ALTER TABLE clients ADD COLUMN reset_token_hash TEXT DEFAULT NULL",
        "ALTER TABLE clients ADD COLUMN reset_token_expires TEXT DEFAULT NULL",
        "ALTER TABLE clients ADD COLUMN avatar_color TEXT DEFAULT NULL",
        "ALTER TABLE support_tickets ADD COLUMN client_id INTEGER DEFAULT NULL",
    ):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass

    # Index utiles
    c.execute("CREATE INDEX IF NOT EXISTS idx_subs_client ON subscriptions(client_id, active)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_payments_ref ON payments(reference)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mk_payments_ref ON mikrotik_payments(reference)")

    _bootstrap_admin(c)

    conn.commit()
    conn.close()


def _bootstrap_admin(c):
    """Crée le premier compte admin s'il n'en existe aucun.

    Plus de compte par défaut avec mot de passe connu : soit
    ADMIN_EMAIL/ADMIN_PASSWORD sont fournis en variables d'environnement,
    soit un mot de passe aléatoire est généré et affiché UNE FOIS dans
    les logs.
    """
    row = c.execute("SELECT COUNT(*) FROM clients WHERE is_admin=1").fetchone()
    if row[0] > 0:
        return

    email = config.ADMIN_EMAIL or "admin@hotspotpro.tg"
    pwd   = config.ADMIN_PASSWORD
    generated = False
    if not pwd:
        pwd = secrets.token_urlsafe(12)
        generated = True

    c.execute("""
        INSERT OR IGNORE INTO clients (email, password_hash, full_name, is_admin)
        VALUES (?, ?, 'Administrateur', 1)
    """, (email, hash_password(pwd)))

    if generated:
        print("=" * 60)
        print(f"[HotspotPro] Compte admin créé : {email}")
        print(f"[HotspotPro] Mot de passe (affiché une seule fois) : {pwd}")
        print("[HotspotPro] Changez-le dès la première connexion.")
        print("=" * 60, flush=True)


# ═══════════════════════════════════════════════
# HELPERS MÉTIER
# ═══════════════════════════════════════════════

def get_client(client_id: int) -> dict | None:
    conn = get_db()
    row  = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_active_sub(client_id: int) -> dict | None:
    conn = get_db()
    row  = conn.execute("""
        SELECT * FROM subscriptions
        WHERE client_id=? AND active=1
        ORDER BY end_date DESC LIMIT 1
    """, (client_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_last_provisioned_sub(client_id: int) -> dict | None:
    """Dernier abonnement provisionné du client (pour réutiliser la config)."""
    conn = get_db()
    row  = conn.execute("""
        SELECT * FROM subscriptions
        WHERE client_id=? AND provisioned=1
        ORDER BY id DESC LIMIT 1
    """, (client_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_client_devices(client_id: int, subscription_id: int) -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM mikrotik_devices
        WHERE client_id=? AND subscription_id=?
        ORDER BY created_at ASC
    """, (client_id, subscription_id)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _parse_end_date(end_date_str: str) -> datetime:
    try:
        return datetime.strptime(end_date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.strptime(end_date_str, "%Y-%m-%d")


def days_remaining(end_date_str: str) -> int:
    return max(0, (_parse_end_date(end_date_str) - datetime.now()).days)


def is_expired(end_date_str: str) -> bool:
    """Vrai uniquement quand la date de fin est réellement dépassée —
    un abonnement qui expire ce soir reste actif toute la journée
    (days_remaining tronque et dirait 0 dès minuit)."""
    if not end_date_str:
        return False
    return _parse_end_date(end_date_str) < datetime.now()
