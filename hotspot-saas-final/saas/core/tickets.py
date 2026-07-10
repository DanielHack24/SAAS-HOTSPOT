"""
tickets.py — Vendeurs, tickets pré-générés et attribution des ventes.

Module partagé entre l'application web (création des vendeurs, génération
des lots de tickets) et le hub multi-tenant (attribution d'une vente au
bon vendeur au moment du login MikroTik).

Modèle : chaque ticket est fabriqué À L'AVANCE et rattaché à un vendeur.
Le username n'a donc plus besoin de préfixe « vendeur- » : au login, le
hub cherche le code dans la table `tickets` et retrouve directement son
vendeur. Un code inconnu (ticket créé hors plateforme) est rattaché au
vendeur réservé « Non attribué » pour ne jamais perdre une recette de vue.

Les données vivent dans la base de vente du tenant (sales.db), là où le
hub enregistre déjà les ventes. Toutes les fonctions prennent le chemin de
cette base en paramètre : le module reste autonome et testable.
"""
import os
import secrets
import sqlite3
import threading

SAAS_DIR = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")

UNASSIGNED = "Non attribué"      # vendeur réservé pour les codes inconnus
MAX_BATCH  = 1000                # garde-fou : tickets par lot

# Jeux de caractères SANS les ambigus (O/0, I/1, l) — plus faciles à lire
# et à recopier sur une carte.
CHARSETS = {
    "upper": "ABCDEFGHJKLMNPQRSTUVWXYZ",
    "lower": "abcdefghijkmnpqrstuvwxyz",
    "mixed": "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz",
    "alnum": "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789",
}
CHARSET_LABELS = {
    "upper": "Majuscules",
    "lower": "Minuscules",
    "mixed": "Majuscules + minuscules",
    "alnum": "Majuscules + minuscules + chiffres",
}
PW_MODES = {
    "same":     "Mot de passe = identifiant",
    "separate": "Mot de passe séparé",
}

_locks = {}
_locks_guard = threading.Lock()


def sales_db_path(slug: str) -> str:
    """Chemin de la base de vente d'un tenant (identique à tenant_db)."""
    return os.path.join(SAAS_DIR, "tenants", slug, "sales.db")


def _lock(db_path: str) -> threading.Lock:
    with _locks_guard:
        if db_path not in _locks:
            _locks[db_path] = threading.Lock()
        return _locks[db_path]


def _connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ═══════════════════════════════════════════════
# SCHÉMA
# ═══════════════════════════════════════════════

def ensure_schema(db_path: str):
    conn = _connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sellers (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticket_batches (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id  INTEGER NOT NULL,
            profile    TEXT NOT NULL,
            qty        INTEGER NOT NULL,
            code_len   INTEGER,
            charset    TEXT,
            pw_mode    TEXT,
            validity   TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            username   TEXT PRIMARY KEY,
            password   TEXT NOT NULL DEFAULT '',
            profile    TEXT NOT NULL,
            seller_id  INTEGER,
            batch_id   INTEGER,
            status     TEXT DEFAULT 'stock',
            sold_at    TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_seller ON tickets(seller_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_batch ON tickets(batch_id)")
    # Migrations : suivi de la synchronisation vers le routeur (User Manager)
    for ddl in (
        "ALTER TABLE tickets ADD COLUMN pushed INTEGER DEFAULT 0",
        "ALTER TABLE tickets ADD COLUMN pushed_at TEXT",
        "ALTER TABLE ticket_batches ADD COLUMN validity TEXT DEFAULT ''",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════
# VENDEURS
# ═══════════════════════════════════════════════

def _seller_id_by_name(conn, name: str):
    row = conn.execute("SELECT id FROM sellers WHERE name=?", (name,)).fetchone()
    return row["id"] if row else None


def _ensure_seller(conn, name: str) -> int:
    sid = _seller_id_by_name(conn, name)
    if sid is None:
        cur = conn.execute("INSERT INTO sellers (name) VALUES (?)", (name,))
        sid = cur.lastrowid
    return sid


def create_seller(db_path: str, name: str) -> int:
    """Crée un vendeur. Lève ValueError si le nom est vide, réservé ou déjà pris."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Le nom du vendeur ne peut pas être vide.")
    if name.lower() == UNASSIGNED.lower():
        raise ValueError("Ce nom est réservé.")
    ensure_schema(db_path)
    conn = _connect(db_path)
    try:
        if _seller_id_by_name(conn, name) is not None:
            raise ValueError("Un vendeur porte déjà ce nom.")
        cur = conn.execute("INSERT INTO sellers (name) VALUES (?)", (name,))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def rename_seller(db_path: str, seller_id: int, name: str):
    name = (name or "").strip()
    if not name:
        raise ValueError("Le nom du vendeur ne peut pas être vide.")
    if name.lower() == UNASSIGNED.lower():
        raise ValueError("Ce nom est réservé.")
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT name FROM sellers WHERE id=?", (seller_id,)).fetchone()
        if not row:
            raise ValueError("Vendeur introuvable.")
        if row["name"] == UNASSIGNED:
            raise ValueError("Ce vendeur ne peut pas être renommé.")
        clash = conn.execute("SELECT id FROM sellers WHERE name=? AND id<>?",
                             (name, seller_id)).fetchone()
        if clash:
            raise ValueError("Un vendeur porte déjà ce nom.")
        conn.execute("UPDATE sellers SET name=? WHERE id=?", (name, seller_id))
        conn.commit()
    finally:
        conn.close()


def delete_seller(db_path: str, seller_id: int):
    """Supprime un vendeur : ses tickets EN STOCK (invendus) sont retirés,
    ses tickets DÉJÀ VENDUS sont conservés mais détachés (l'historique de
    recettes, lui, reste dans daily_stats). « Non attribué » est protégé."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT name FROM sellers WHERE id=?", (seller_id,)).fetchone()
        if not row:
            return
        if row["name"] == UNASSIGNED:
            raise ValueError("Le vendeur « Non attribué » ne peut pas être supprimé.")
        conn.execute("DELETE FROM tickets WHERE seller_id=? AND status='stock'", (seller_id,))
        conn.execute("UPDATE tickets SET seller_id=NULL WHERE seller_id=?", (seller_id,))
        conn.execute("DELETE FROM sellers WHERE id=?", (seller_id,))
        conn.commit()
    finally:
        conn.close()


def get_seller(db_path: str, seller_id: int):
    conn = _connect(db_path)
    row = conn.execute("SELECT * FROM sellers WHERE id=?", (seller_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_sellers(db_path: str) -> list[dict]:
    """Liste des vendeurs avec inventaire (générés/vendus/stock) et recettes."""
    ensure_schema(db_path)
    conn = _connect(db_path)
    # Ordre de création (le plus récent à droite dans l'UI) ; « Non
    # attribué » toujours en dernier.
    sellers = [dict(r) for r in conn.execute(
        "SELECT * FROM sellers ORDER BY (name=?) ASC, id ASC", (UNASSIGNED,)
    ).fetchall()]
    for s in sellers:
        inv = conn.execute("""
            SELECT COUNT(*) AS generated,
                   SUM(status='sold')  AS sold,
                   SUM(status='stock') AS stock
            FROM tickets WHERE seller_id=?
        """, (s["id"],)).fetchone()
        s["generated"] = inv["generated"] or 0
        s["sold"]      = inv["sold"] or 0
        s["stock"]     = inv["stock"] or 0
        # La table `sales` appartient au hub ; elle peut ne pas encore
        # exister sur un tenant tout neuf (aucune vente reçue).
        try:
            rev = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(amount),0) AS r FROM sales WHERE seller=?",
                (s["name"],)).fetchone()
            s["revenue"]        = rev["r"] or 0
            s["sales_recorded"] = rev["n"] or 0
        except sqlite3.OperationalError:
            s["revenue"]        = 0
            s["sales_recorded"] = 0
    conn.close()
    return sellers


# ═══════════════════════════════════════════════
# GÉNÉRATION DE TICKETS
# ═══════════════════════════════════════════════

def _gen_code(charset: str, length: int) -> str:
    return "".join(secrets.choice(charset) for _ in range(length))


def generate_batch(db_path: str, seller_id: int, profile: str, qty: int,
                   code_len: int = 5, charset: str = "alnum",
                   pw_mode: str = "same", validity: str = "") -> dict:
    """Génère `qty` tickets uniques pour un vendeur/profil et les enregistre.
    Retourne {batch_id, tickets:[{username,password,profile}], ...}."""
    if charset not in CHARSETS:
        raise ValueError("Jeu de caractères invalide.")
    if pw_mode not in PW_MODES:
        raise ValueError("Mode de mot de passe invalide.")
    if not (4 <= code_len <= 7):
        raise ValueError("La longueur du code doit être comprise entre 4 et 7.")
    if not (1 <= qty <= MAX_BATCH):
        raise ValueError(f"La quantité doit être comprise entre 1 et {MAX_BATCH}.")
    if not (profile or "").strip():
        raise ValueError("Profil requis.")

    alphabet = CHARSETS[charset]
    # Assez d'espace de codes pour éviter les collisions à répétition
    if len(alphabet) ** code_len < qty * 4:
        raise ValueError("Trop de tickets pour ce format de code : "
                         "augmentez la longueur ou élargissez le jeu de caractères.")

    ensure_schema(db_path)
    with _lock(db_path):
        conn = _connect(db_path)
        try:
            if not conn.execute("SELECT 1 FROM sellers WHERE id=?", (seller_id,)).fetchone():
                raise ValueError("Vendeur introuvable.")
            existing = {r["username"] for r in conn.execute("SELECT username FROM tickets")}

            cur = conn.execute("""
                INSERT INTO ticket_batches (seller_id, profile, qty, code_len, charset, pw_mode, validity)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (seller_id, profile, qty, code_len, charset, pw_mode, validity))
            batch_id = cur.lastrowid

            tickets = []
            seen = set()
            for _ in range(qty):
                for _attempt in range(10_000):
                    code = _gen_code(alphabet, code_len)
                    if code not in existing and code not in seen:
                        break
                else:
                    raise ValueError("Impossible de générer des codes uniques : "
                                     "espace de codes saturé.")
                seen.add(code)
                pwd = code if pw_mode == "same" else _gen_code(alphabet, code_len)
                tickets.append({"username": code, "password": pwd, "profile": profile})

            conn.executemany("""
                INSERT INTO tickets (username, password, profile, seller_id, batch_id, status)
                VALUES (?, ?, ?, ?, ?, 'stock')
            """, [(t["username"], t["password"], profile, seller_id, batch_id) for t in tickets])
            conn.commit()
        finally:
            conn.close()

    return {"batch_id": batch_id, "seller_id": seller_id, "profile": profile,
            "pw_mode": pw_mode, "tickets": tickets}


def list_batches(db_path: str, seller_id: int) -> list[dict]:
    ensure_schema(db_path)
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT b.*,
               (SELECT COUNT(*) FROM tickets WHERE batch_id=b.id) AS total,
               (SELECT COUNT(*) FROM tickets WHERE batch_id=b.id AND status='sold') AS sold
        FROM ticket_batches b
        WHERE b.seller_id=?
        ORDER BY b.id DESC
    """, (seller_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_batch(db_path: str, batch_id: int) -> dict | None:
    conn = _connect(db_path)
    batch = conn.execute("SELECT * FROM ticket_batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        conn.close()
        return None
    tickets = conn.execute(
        "SELECT username, password, profile, status FROM tickets WHERE batch_id=? ORDER BY username",
        (batch_id,)).fetchall()
    seller = conn.execute("SELECT name FROM sellers WHERE id=?", (batch["seller_id"],)).fetchone()
    conn.close()
    return {"batch": dict(batch),
            "seller_name": seller["name"] if seller else "",
            "tickets": [dict(t) for t in tickets]}


# ═══════════════════════════════════════════════
# SYNCHRONISATION VERS LE ROUTEUR (User Manager)
# ═══════════════════════════════════════════════

def pending_push(db_path: str) -> list[dict]:
    """Tickets restant à créer sur le routeur (jamais poussés).
    On ne pousse pas les tickets « Non attribué » (créés a posteriori)."""
    ensure_schema(db_path)
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT t.username, t.password, t.profile,
               COALESCE(b.validity, '') AS validity
        FROM tickets t
        LEFT JOIN ticket_batches b ON t.batch_id = b.id
        WHERE t.pushed=0 AND t.batch_id IS NOT NULL
        ORDER BY t.rowid
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_pushed(db_path: str, usernames: list[str]):
    if not usernames:
        return
    conn = _connect(db_path)
    conn.executemany(
        "UPDATE tickets SET pushed=1, pushed_at=datetime('now','localtime') WHERE username=?",
        [(u,) for u in usernames])
    conn.commit()
    conn.close()


def push_counts(db_path: str) -> dict:
    """{pending, pushed} pour l'affichage de l'état de synchro."""
    ensure_schema(db_path)
    conn = _connect(db_path)
    row = conn.execute("""
        SELECT SUM(pushed=0 AND batch_id IS NOT NULL) AS pending,
               SUM(pushed=1) AS pushed
        FROM tickets
    """).fetchone()
    conn.close()
    return {"pending": row["pending"] or 0, "pushed": row["pushed"] or 0}


# ═══════════════════════════════════════════════
# ATTRIBUTION AU LOGIN (appelée par le hub)
# ═══════════════════════════════════════════════

def resolve_sale(db_path: str, username: str, reported_profile: str) -> dict:
    """Attribue un login à un vendeur, de façon atomique et idempotente.

    Retourne {seller, profile, record} :
      - `record` True s'il s'agit d'une PREMIÈRE vente à comptabiliser,
        False si le ticket était déjà vendu (reconnexion — on ne recompte pas).
      - `seller` : nom du vendeur (ou « Non attribué » si code inconnu).
      - `profile` : profil du ticket (fait foi sur celui rapporté par le routeur).
    """
    ensure_schema(db_path)
    with _lock(db_path):
        conn = _connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tickets WHERE username=?", (username,)).fetchone()

            if row:
                seller = conn.execute("SELECT name FROM sellers WHERE id=?",
                                      (row["seller_id"],)).fetchone()
                seller_name = seller["name"] if seller else UNASSIGNED
                profile = row["profile"]
                if row["status"] == "sold":
                    conn.commit()
                    return {"seller": seller_name, "profile": profile, "record": False}
                conn.execute(
                    "UPDATE tickets SET status='sold', sold_at=datetime('now','localtime') "
                    "WHERE username=?", (username,))
                conn.commit()
                return {"seller": seller_name, "profile": profile, "record": True}

            # Code inconnu → vendeur « Non attribué ». On insère un ticket
            # synthétique (déjà vendu) pour ne pas recompter à chaque reconnexion.
            sid = _ensure_seller(conn, UNASSIGNED)
            conn.execute("""
                INSERT INTO tickets (username, password, profile, seller_id, batch_id, status, sold_at)
                VALUES (?, '', ?, ?, NULL, 'sold', datetime('now','localtime'))
            """, (username, reported_profile or "", sid))
            conn.commit()
            return {"seller": UNASSIGNED, "profile": reported_profile or "", "record": True}
        finally:
            conn.close()
