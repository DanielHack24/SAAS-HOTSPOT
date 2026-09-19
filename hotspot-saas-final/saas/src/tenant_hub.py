"""
tenant_hub.py — Serveur multi-tenant HotspotPro (remplace tenant_app.py)

Un SEUL processus sert tous les clients :
  - GET/POST /t/<slug>/login   → réception des ventes MikroTik
  - GET      /t/<slug>/stats   → stats du jour du tenant
  - GET      /t/<slug>/health  → état du tenant
  - POST     /reload?key=…     → recharge les tenants depuis central.db
  - GET      /health           → état du hub

Avantages vs 1 processus/client :
  - RAM constante quel que soit le nombre de clients
  - plus de limite de ports (8001-8999)
  - tarifs rechargés à chaud (plus besoin de redéployer)
  - provisioning = simple INSERT en base + POST /reload
"""
import os, sqlite3, threading, json, time, html, secrets, hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import http.client


def esc(v) -> str:
    """Échappe pour les messages Telegram parse_mode=HTML. username/profile
    viennent de la requête MikroTik : sans échappement, un ticket nommé
    <b>… casserait ou falsifierait les notifications."""
    return html.escape(str(v), quote=False)

SAAS_DIR = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")
HUB_PORT = int(os.environ.get("TENANT_HUB_PORT", "8010"))
HUB_KEY  = os.environ.get("HUB_KEY", "")   # protège /reload
REFRESH_INTERVAL = 60                       # re-lecture périodique de central.db

import sys
sys.path.insert(0, os.path.join(SAAS_DIR, "core"))
from tenant_db import get_all_tenants, tenant_sales_db  # noqa: E402
import tickets  # noqa: E402  (module partagé : vendeurs + attribution)
import dbconn   # noqa: E402  (connexions SQLite réglées pour la charge)
import access_log  # noqa: E402  (journal d'accès routeur + détection de partage)

app = Flask(__name__)

DEFAULT_PRICES = {"1h": 100, "12h": 500, "24h": 1000,
                  "3j": 2500, "7j": 5000, "30j": 15000}


# ═══════════════════════════════════════════════
# REGISTRE DES TENANTS
# ═══════════════════════════════════════════════

class TenantRegistry:
    """Cache des tenants actifs, rechargé depuis central.db."""

    def __init__(self):
        self._lock    = threading.Lock()
        self._tenants = {}   # slug -> dict
        self._known   = set()

    def refresh(self):
        tenants = {t["slug"]: self._normalize(t) for t in get_all_tenants()}
        with self._lock:
            self._tenants = tenants
            new_slugs   = set(tenants) - self._known
            self._known |= new_slugs
        for slug in new_slugs:
            init_sales_db(slug)
        bot_manager.sync([t for t in tenants.values() if t["active"]])

    @staticmethod
    def _normalize(t: dict) -> dict:
        prices = {}
        try:
            raw = json.loads(t.get("prices") or "{}")
            for k, v in raw.items():
                prices[k] = int(v["price"]) if isinstance(v, dict) else int(v)
        except Exception:
            prices = {}
        t["price_map"] = prices or dict(DEFAULT_PRICES)
        t["active"]    = bool(t.get("active", 1))
        return t

    def get(self, slug: str) -> dict | None:
        with self._lock:
            return self._tenants.get(slug)

    def all(self) -> list[dict]:
        with self._lock:
            return list(self._tenants.values())


registry = None  # initialisé au lancement


def _refresh_loop():
    while True:
        time.sleep(REFRESH_INTERVAL)
        try:
            registry.refresh()
        except Exception as e:
            print(f"[HUB] Erreur refresh: {e}", flush=True)


# ═══════════════════════════════════════════════
# BASE DE DONNÉES PAR TENANT (sales.db conservées)
# ═══════════════════════════════════════════════

_db_locks = {}
_db_locks_guard = threading.Lock()


def _db_lock(slug: str) -> threading.Lock:
    with _db_locks_guard:
        if slug not in _db_locks:
            _db_locks[slug] = threading.Lock()
        return _db_locks[slug]


def _sales_conn(slug: str) -> sqlite3.Connection:
    path = tenant_sales_db(slug)
    conn = dbconn.connect(path)
    dbconn.keep_open(path)
    return conn


def init_sales_db(slug: str):
    conn = _sales_conn(slug)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sales (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            seller     TEXT NOT NULL,
            username   TEXT NOT NULL,
            profile    TEXT NOT NULL,
            amount     INTEGER DEFAULT 0,
            comment    TEXT DEFAULT '',
            ip         TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            date    TEXT NOT NULL,
            seller  TEXT NOT NULL,
            sales   INTEGER DEFAULT 0,
            revenue INTEGER DEFAULT 0,
            UNIQUE(date, seller)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS registered_tickets (
            username   TEXT PRIMARY KEY,
            profile    TEXT NOT NULL,
            seller     TEXT NOT NULL,
            sale_id    INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()
    # Tables vendeurs / tickets / lots (module partagé)
    tickets.ensure_schema(tenant_sales_db(slug))


def is_already_registered(slug: str, username: str) -> bool:
    conn = _sales_conn(slug)
    row  = conn.execute(
        "SELECT username FROM registered_tickets WHERE username=?", (username,)
    ).fetchone()
    conn.close()
    return row is not None


def record_sale(slug: str, seller, username, profile, amount, comment, ip):
    today = datetime.now().strftime("%Y-%m-%d")
    with _db_lock(slug):
        conn    = _sales_conn(slug)
        cur     = conn.execute(
            "INSERT INTO sales (seller,username,profile,amount,comment,ip) VALUES(?,?,?,?,?,?)",
            (seller, username, profile, amount, comment, ip)
        )
        sale_id = cur.lastrowid
        conn.execute("""
            INSERT INTO daily_stats (date,seller,sales,revenue) VALUES(?,?,1,?)
            ON CONFLICT(date,seller) DO UPDATE SET
                sales=sales+1, revenue=revenue+excluded.revenue
        """, (today, seller, amount))
        conn.execute(
            "INSERT OR IGNORE INTO registered_tickets (username,profile,seller,sale_id) VALUES(?,?,?,?)",
            (username, profile, seller, sale_id)
        )
        conn.commit()
        conn.close()
    return sale_id


def get_all_sellers(slug: str):
    conn = _sales_conn(slug)
    rows = conn.execute("SELECT DISTINCT seller FROM sales ORDER BY seller ASC").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_today_stats(slug: str, seller=None):
    today = datetime.now().strftime("%Y-%m-%d")
    conn  = _sales_conn(slug)
    if seller:
        rows = conn.execute("SELECT * FROM daily_stats WHERE date=? AND seller=?",
                            (today, seller)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM daily_stats WHERE date=? ORDER BY revenue DESC",
                            (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats_period(slug: str, seller: str, period: str):
    now   = datetime.now()
    today = now.strftime("%Y-%m-%d")
    if period == "today":
        conn = _sales_conn(slug)
        row  = conn.execute(
            "SELECT sales,revenue FROM daily_stats WHERE date=? AND seller=?",
            (today, seller)).fetchone()
        conn.close()
        return {"label": f"Aujourd'hui ({today})",
                "sales": row["sales"] if row else 0,
                "revenue": row["revenue"] if row else 0}
    date_from, label = {
        "week":  ((now - timedelta(days=now.weekday())).strftime("%Y-%m-%d"), "Cette semaine"),
        "month": (now.strftime("%Y-%m-01"), f"Ce mois ({now.strftime('%B %Y')})"),
        "year":  (now.strftime("%Y-01-01"), f"Cette année ({now.year})"),
    }.get(period, (today, "Aujourd'hui"))
    conn = _sales_conn(slug)
    row  = conn.execute(
        "SELECT SUM(sales) as s, SUM(revenue) as r FROM daily_stats WHERE seller=? AND date>=?",
        (seller, date_from)
    ).fetchone()
    conn.close()
    return {"label": label,
            "sales": (row["s"] or 0) if row else 0,
            "revenue": (row["r"] or 0) if row else 0}


def get_seller_total(slug: str, seller: str):
    conn = _sales_conn(slug)
    row  = conn.execute(
        "SELECT COUNT(*) as s, SUM(amount) as r FROM sales WHERE seller=?",
        (seller,)).fetchone()
    conn.close()
    if row:
        return {"total_sales": row["s"] or 0, "total_revenue": row["r"] or 0}
    return {"total_sales": 0, "total_revenue": 0}


def get_week_stats(slug: str):
    conn = _sales_conn(slug)
    days = []
    for i in range(6, -1, -1):
        d   = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT SUM(sales) as s, SUM(revenue) as r FROM daily_stats WHERE date=?", (d,)
        ).fetchone()
        days.append({"date": d, "sales": row["s"] or 0, "revenue": row["r"] or 0})
    conn.close()
    return days


# ═══════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════

# Une connexion HTTPS PERSISTANTE par thread (keep-alive) : chaque bot
# interroge Telegram toutes les 30 s ; rouvrir une connexion chiffrée à
# chaque appel coûtait une négociation TLS complète, soit l'essentiel du
# processeur consommé au repos quand les clients sont nombreux.
_tg_local = threading.local()


def _tg_conn(timeout: float) -> http.client.HTTPSConnection:
    conn = getattr(_tg_local, "conn", None)
    if conn is None:
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
        _tg_local.conn = conn
    conn.timeout = timeout
    if conn.sock is not None:
        conn.sock.settimeout(timeout)
    return conn


def _tg_drop():
    conn = getattr(_tg_local, "conn", None)
    if conn is not None:
        conn.close()
    _tg_local.conn = None


def _tg(bot_token, method, payload, timeout=10):
    if not bot_token:
        return {}
    body = json.dumps(payload).encode()
    quiet = method == "getUpdates"   # long-polling : pas de bruit dans les logs
    for attempt in (1, 2):
        conn = _tg_conn(timeout)
        try:
            conn.request("POST", f"/bot{bot_token}/{method}", body=body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            raw  = resp.read()
        except TimeoutError:
            # Pas de nouvel essai : la requête a pu être traitée (doublon).
            _tg_drop()
            if not quiet:
                print(f"[TG] {method} échec : délai dépassé", flush=True)
            return {}
        except (http.client.HTTPException, ConnectionError) as e:
            # Connexion keep-alive fermée côté Telegram : on rouvre une fois.
            _tg_drop()
            if attempt == 1:
                continue
            if not quiet:
                print(f"[TG] {method} échec : {type(e).__name__}", flush=True)
            return {}
        except OSError as e:
            _tg_drop()
            if not quiet:
                print(f"[TG] {method} échec : {type(e).__name__}", flush=True)
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        if resp.status >= 400:
            # Telegram refuse (message trop long, chat introuvable, bouton
            # invalide…) : on journalise la raison au lieu d'échouer en silence.
            if not quiet:
                print(f"[TG] {method} refusé ({resp.status}) : "
                      f"{data.get('description', '')}", flush=True)
            return {}
        return data
    return {}


def send_msg(bot_token, chat_id, text, markup=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if markup:
        p["reply_markup"] = markup
    _tg(bot_token, "sendMessage", p)


def edit_msg(bot_token, chat_id, msg_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML"}
    if markup:
        p["reply_markup"] = markup
    _tg(bot_token, "editMessageText", p)


# ── Volet de navigation FIXE (reply keyboard persistant) ─────
# Reste docké en bas de la conversation même quand des notifications de
# vente arrivent (les inline keyboards, eux, défilent avec les messages).

BTN_SELLERS = "📊 Vendeurs"
BTN_RANKING = "🏆 Classement du jour"
BTN_GLOBAL  = "📈 Résumé global"


def kb_persistent():
    return {
        "keyboard": [
            [{"text": BTN_SELLERS}],
            [{"text": BTN_RANKING}, {"text": BTN_GLOBAL}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


# ── Claviers inline (drill-down vendeur → période) ───────────
# Telegram limite callback_data à 64 octets : on n'y met donc JAMAIS le nom
# du vendeur (un nom long faisait échouer tout le message), mais une clé
# courte dérivée du nom, retrouvée ensuite dans la liste des vendeurs.
# Le séparateur « : » évite aussi l'ambiguïté des noms contenant « _ ».

PERIODS = ("today", "week", "month", "year")


def seller_key(seller: str) -> str:
    return hashlib.sha1(seller.encode("utf-8")).hexdigest()[:12]


def find_seller(slug: str, key: str) -> str | None:
    for s in get_all_sellers(slug):
        if seller_key(s) == key:
            return s
    return None


def kb_sellers(sellers):
    rows = [[{"text": f"👤 {s}", "callback_data": f"s:{seller_key(s)}"}] for s in sellers]
    rows.append([{"text": "🔙 Menu principal", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


def kb_periods(seller):
    k = seller_key(seller)
    return {"inline_keyboard": [
        [{"text": "📅 Aujourd'hui", "callback_data": f"p:{k}:today"},
         {"text": "📆 Semaine",     "callback_data": f"p:{k}:week"}],
        [{"text": "🗓 Mois",        "callback_data": f"p:{k}:month"},
         {"text": "📊 Année",       "callback_data": f"p:{k}:year"}],
        [{"text": "🔙 Retour vendeurs", "callback_data": "menu_sellers"}],
    ]}


def parse_callback(slug: str, data: str):
    """Décode un callback vendeur. Retourne (action, vendeur, période) avec
    action 'seller' | 'stat', ou None si inconnu. Accepte aussi l'ancien
    format (seller_<nom> / stat_<nom>_<période>) des messages déjà envoyés."""
    if data.startswith("s:"):
        seller = find_seller(slug, data[2:])
        return ("seller", seller, None) if seller else None
    if data.startswith("p:"):
        parts = data.split(":")
        if len(parts) == 3 and parts[2] in PERIODS:
            seller = find_seller(slug, parts[1])
            return ("stat", seller, parts[2]) if seller else None
        return None
    if data.startswith("seller_"):
        return ("seller", data[7:], None)
    if data.startswith("stat_"):
        seller, _, period = data[5:].rpartition("_")
        if seller and period in PERIODS:
            return ("stat", seller, period)
    return None


# ── Messages ─────────────────────────────────────────────────

def msg_welcome():
    return ("🏪 <b>Comptabilité Hotspot MikroTik</b>\n\n"
            "Bienvenue ! Le menu reste toujours accessible en bas de l'écran. "
            "Touchez un bouton pour consulter vos statistiques.")


def msg_sellers(sellers):
    if not sellers:
        return "👥 <b>Aucun vendeur</b>\n\nAucune vente enregistrée pour l'instant."
    return f"👥 <b>Liste des vendeurs</b>\n\n<b>{len(sellers)}</b> vendeur(s) — choisissez-en un :"


def msg_stats(slug, seller, period):
    d = get_stats_period(slug, seller, period)
    t = get_seller_total(slug, seller)
    e = {"today": "📅", "week": "📆", "month": "🗓", "year": "📊"}.get(period, "📊")
    return (
        f"👤 <b>{esc(seller)}</b>\n{'─'*28}\n"
        f"{e} <b>{d['label']}</b>\n\n"
        f"🎟️ Ventes   : <b>{d['sales']}</b>\n"
        f"💰 Recettes : <b>{d['revenue']:,} FCFA</b>\n"
        f"{'─'*28}\n"
        f"📦 All-time : <b>{t['total_sales']} ventes</b> · <b>{t['total_revenue']:,} FCFA</b>"
    )


def msg_ranking(slug):
    stats = get_today_stats(slug)
    if not stats:
        return "🏆 <b>Classement du jour</b>\n\nAucune vente aujourd'hui."
    medals = ["🥇", "🥈", "🥉"]
    lines  = [f"🏆 <b>Classement — {datetime.now().strftime('%d/%m/%Y')}</b>\n"]
    for i, s in enumerate(stats):
        m = medals[i] if i < 3 else f"  {i+1}."
        lines.append(f"{m} <b>{esc(s['seller'])}</b> — {s['sales']} vente(s) · {s['revenue']:,} FCFA")
    lines.append(f"\n💼 Total : <b>{sum(s['sales'] for s in stats)} ventes</b> · "
                 f"<b>{sum(s['revenue'] for s in stats):,} FCFA</b>")
    return "\n".join(lines)


def msg_global(slug):
    now   = datetime.now()
    today = now.strftime("%Y-%m-%d")
    month = now.strftime("%Y-%m-01")
    conn  = _sales_conn(slug)
    rt = conn.execute("SELECT SUM(sales) s,SUM(revenue) r FROM daily_stats WHERE date=?", (today,)).fetchone()
    rm = conn.execute("SELECT SUM(sales) s,SUM(revenue) r FROM daily_stats WHERE date>=?", (month,)).fetchone()
    ra = conn.execute("SELECT COUNT(*) s,SUM(amount) r FROM sales").fetchone()
    conn.close()

    def v(r, k):
        return (r[k] or 0) if r else 0
    return (
        f"📈 <b>Résumé global</b>\n{'─'*28}\n"
        f"📅 Aujourd'hui : <b>{v(rt,'s')} ventes</b> · <b>{v(rt,'r'):,} FCFA</b>\n"
        f"🗓 Ce mois     : <b>{v(rm,'s')} ventes</b> · <b>{v(rm,'r'):,} FCFA</b>\n"
        f"📦 All-time    : <b>{v(ra,'s')} ventes</b> · <b>{v(ra,'r'):,} FCFA</b>\n"
        f"{'─'*28}\n<i>{now.strftime('%d/%m/%Y à %H:%M')}</i>"
    )


def msg_sale(slug, seller, username, profile, amount, sale_id, router_display=""):
    now   = datetime.now().strftime("%H:%M")
    _emap = {"1h": "⚡", "2h": "⚡", "3h": "⚡", "6h": "🌅", "12h": "🌙", "24h": "☀️", "1j": "☀️",
             "2j": "📅", "3j": "📅", "5j": "📆", "7j": "📆", "14j": "📆", "30j": "🗓️", "1m": "🗓️"}
    emoji = _emap.get(profile, "🎫")
    stats = get_today_stats(slug, seller)
    ds = stats[0]["sales"]   if stats else 1
    dr = stats[0]["revenue"] if stats else amount
    router_line = f"\n📡 Routeur  : <b>{esc(router_display)}</b>" if router_display else ""
    return (
        f"🔔 <b>Nouvelle vente</b> — {now}\n{'─'*28}\n"
        f"{emoji} Profil   : <b>{esc(profile)}</b>\n"
        f"👤 Vendeur : <b>{esc(seller)}</b>\n"
        f"🎟️ Ticket  : <code>{esc(username)}</code>\n"
        f"💰 Montant : <b>{amount:,} FCFA</b>{router_line}\n"
        f"{'─'*28}\n"
        f"📊 Aujourd'hui ({esc(seller)}) : <b>{ds} vente(s)</b> · <b>{dr:,} FCFA</b>\n"
        f"<i>#{sale_id}</i>"
    )


# ═══════════════════════════════════════════════
# BOT MANAGER — un thread de polling par bot_token
# ═══════════════════════════════════════════════

class BotWorker(threading.Thread):
    def __init__(self, bot_token: str, slug: str):
        super().__init__(daemon=True)
        self.bot_token = bot_token
        self.slug      = slug
        self.stop_flag = threading.Event()
        self._offset   = 0

    def run(self):
        _tg(self.bot_token, "deleteWebhook", {"drop_pending_updates": False})
        while not self.stop_flag.is_set():
            try:
                result = _tg(self.bot_token, "getUpdates",
                             {"offset": self._offset, "timeout": 30, "limit": 100,
                              "allowed_updates": ["message", "callback_query"]},
                             timeout=40)
                for upd in result.get("result", []):
                    self._offset = upd["update_id"] + 1
                    try:
                        self.handle_update(upd)
                    except Exception:
                        pass
            except Exception:
                self.stop_flag.wait(5)

    def _authorized(self, chat_id: str, user_id: str) -> bool:
        """Seul le propriétaire du tenant peut consulter ses chiffres : la
        conversation (privée ou groupe) doit être le chat_id configuré, ou
        l'expéditeur doit en être l'identifiant. Sans chat_id configuré,
        personne n'a accès."""
        tenant = registry.get(self.slug) if registry else None
        owner  = str((tenant or {}).get("chat_id") or "").strip()
        return bool(owner) and owner in (chat_id, user_id)

    def handle_update(self, update):
        slug = self.slug
        if "message" in update:
            msg     = update["message"]
            chat_id = str(msg["chat"]["id"])
            user_id = str((msg.get("from") or {}).get("id", ""))
            text    = msg.get("text", "").strip()
            if not self._authorized(chat_id, user_id):
                send_msg(self.bot_token, chat_id,
                         "🔒 <b>Bot privé</b>\n\nCe bot est réservé à son propriétaire.\n"
                         f"Votre identifiant Telegram : <code>{esc(chat_id)}</code>")
                return
            # Boutons du volet fixe (arrivent comme des messages texte)
            if text == BTN_GLOBAL or text.startswith("/stats"):
                send_msg(self.bot_token, chat_id, msg_global(slug))
            elif text == BTN_RANKING:
                send_msg(self.bot_token, chat_id, msg_ranking(slug))
            elif text == BTN_SELLERS or text.startswith("/vendeurs"):
                sellers = get_all_sellers(slug)
                send_msg(self.bot_token, chat_id, msg_sellers(sellers), markup=kb_sellers(sellers))
            else:
                # /start, /menu ou autre : (re)dock le volet fixe en bas
                send_msg(self.bot_token, chat_id, msg_welcome(), markup=kb_persistent())

        elif "callback_query" in update:
            cb      = update["callback_query"]
            chat_id = str(cb["message"]["chat"]["id"])
            msg_id  = cb["message"]["message_id"]
            data    = cb.get("data", "")
            user_id = str((cb.get("from") or {}).get("id", ""))
            if not self._authorized(chat_id, user_id):
                _tg(self.bot_token, "answerCallbackQuery",
                    {"callback_query_id": cb["id"], "text": "Accès refusé"})
                return
            _tg(self.bot_token, "answerCallbackQuery", {"callback_query_id": cb["id"]})

            if data == "menu_main":
                edit_msg(self.bot_token, chat_id, msg_id, msg_welcome())
                return
            if data == "menu_sellers":
                sellers = get_all_sellers(slug)
                edit_msg(self.bot_token, chat_id, msg_id, msg_sellers(sellers), markup=kb_sellers(sellers))
                return
            parsed = parse_callback(slug, data)
            if not parsed:
                return
            action, seller, period = parsed
            if action == "seller":
                edit_msg(self.bot_token, chat_id, msg_id,
                         f"👤 <b>{esc(seller)}</b>\n\nChoisissez la période :", markup=kb_periods(seller))
            else:
                edit_msg(self.bot_token, chat_id, msg_id,
                         msg_stats(slug, seller, period), markup=kb_periods(seller))


class BotManager:
    """Démarre/arrête les threads de polling selon les tenants actifs.
    Un seul thread par bot_token (deux tenants partageant un token
    entreraient en conflit sur getUpdates)."""

    def __init__(self):
        self._lock    = threading.Lock()
        self._workers = {}   # bot_token -> BotWorker

    def sync(self, active_tenants: list[dict]):
        desired = {}   # bot_token -> slug
        for t in active_tenants:
            token = (t.get("bot_token") or "").strip()
            if token and token not in desired:
                desired[token] = t["slug"]

        with self._lock:
            # Arrêter les workers obsolètes (token retiré ou slug changé)
            for token in list(self._workers):
                w = self._workers[token]
                if token not in desired or desired[token] != w.slug:
                    w.stop_flag.set()
                    del self._workers[token]
            # Démarrer les nouveaux
            for token, slug in desired.items():
                if token not in self._workers:
                    w = BotWorker(token, slug)
                    self._workers[token] = w
                    w.start()

    def count(self):
        with self._lock:
            return len(self._workers)


bot_manager = BotManager()


# ═══════════════════════════════════════════════
# RATE LIMITING (protection flood /t/<slug>/login)
# ═══════════════════════════════════════════════
# Deux compteurs distincts, appliqués APRÈS la vérification du jeton :
#   - « slug:<slug> » : requêtes AUTHENTIFIÉES d'un tenant ;
#   - « bad:<ip> »    : requêtes à jeton invalide, par IP source.
# Ainsi un tiers qui connaît le slug mais pas le jeton ne peut plus épuiser
# le quota du tenant et faire ignorer ses vraies ventes.

RATE_MAX      = 120   # requêtes max…
RATE_WINDOW   = 60    # …par fenêtre de 60 s et par clé
RATE_BAD_MAX  = 30    # requêtes à jeton invalide max par IP et par fenêtre
RATE_MAX_KEYS = 5000  # au-delà, purge des clés inactives (mémoire bornée)

_rate: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


def rate_limited(key: str, max_hits: int = RATE_MAX) -> bool:
    now = time.time()
    with _rate_lock:
        if len(_rate) > RATE_MAX_KEYS:
            for k in [k for k, v in _rate.items() if not v or now - v[-1] >= RATE_WINDOW]:
                del _rate[k]
        stamps = [t for t in _rate.get(key, []) if now - t < RATE_WINDOW]
        stamps.append(now)
        _rate[key] = stamps
        return len(stamps) > max_hits


def _hub_key_ok() -> bool:
    """Clé exigée en en-tête X-Hub-Key UNIQUEMENT (une query string ?key=
    finirait dans les logs). Comparaison a temps constant. Si HUB_KEY n'est
    pas configurée, on REFUSE : ces endpoints exposent le chiffre d'affaires
    des clients."""
    if not HUB_KEY:
        return False
    sent = request.headers.get("X-Hub-Key", "")
    return bool(sent) and secrets.compare_digest(sent, HUB_KEY)


# ═══════════════════════════════════════════════
# TRAITEMENT VENTE
# ═══════════════════════════════════════════════

# Pool borné : un thread par requête permettrait à un flood de créer
# des milliers de threads.
_sale_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="sale")

def process_sale(tenant: dict, username, profile, ip, router_display=""):
    """Traite la vente en arrière-plan APRÈS avoir répondu à MikroTik.

    Le vendeur est déterminé par le ticket pré-généré (plus de préfixe
    « vendeur- » dans le username). Un code inconnu tombe dans « Non
    attribué ». L'opération est idempotente : une reconnexion ne recompte
    pas la vente."""
    slug = tenant["slug"]
    result  = tickets.resolve_sale(tenant_sales_db(slug), username, profile)
    if not result["record"]:
        return   # ticket déjà vendu (reconnexion) — on ne recompte pas

    seller  = result["seller"]
    profile = result["profile"] or profile
    amount  = tenant["price_map"].get(profile, 0)

    sale_id = record_sale(slug, seller, username, profile, amount, "", ip)
    if tenant.get("bot_token") and tenant.get("chat_id"):
        msg = msg_sale(slug, seller, username, profile, amount, sale_id, router_display)
        send_msg(tenant["bot_token"], tenant["chat_id"], msg)


# ═══════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════

@app.route("/health")
def hub_health():
    return jsonify({
        "status":  "ok",
        "tenants": len(registry.all()),
        "bots":    bot_manager.count(),
        "time":    datetime.now().isoformat(),
    })


@app.route("/reload", methods=["POST"])
def hub_reload():
    if HUB_KEY and not _hub_key_ok():
        return jsonify({"error": "unauthorized"}), 403
    registry.refresh()
    return jsonify({"status": "ok", "tenants": len(registry.all())})


def _resolve_tenant(slug: str):
    tenant = registry.get(slug)
    if not tenant:
        return None, (jsonify({"error": "tenant inconnu"}), 404)
    if not tenant["active"]:
        return None, (jsonify({"error": "tenant suspendu"}), 403)
    return tenant, None


@app.route("/t/<slug>/health")
def tenant_health(slug):
    tenant = registry.get(slug)
    if not tenant:
        return jsonify({"error": "tenant inconnu"}), 404
    return jsonify({"status": "ok" if tenant["active"] else "suspended",
                    "slug": slug, "time": datetime.now().isoformat()})


@app.route("/t/<slug>/login", methods=["GET", "POST"])
def tenant_login(slug):
    """Reçoit une vente depuis MikroTik. Répond immédiatement,
    traite en arrière-plan."""
    tenant, err = _resolve_tenant(slug)
    if err:
        return err

    data      = request.form if request.method == "POST" else request.args
    username  = data.get("username", "").strip()
    profile   = data.get("profile", "").strip()
    # Jeton : en-tête X-Router-Token (scripts actuels, jamais journalisé),
    # sinon paramètre « token » (anciens scripts encore en service).
    token     = (request.headers.get("X-Router-Token") or data.get("token", "")).strip()
    identity  = data.get("router", "").strip()
    serial    = data.get("serial", "").strip()
    client_ip = request.headers.get("X-Real-IP") or request.remote_addr or ""
    # Empreinte STABLE de l'appareil : numéro de série matériel si disponible,
    # sinon le hostname. C'est elle qui sert à détecter le partage (l'IP change
    # sur les réseaux mobiles et provoquait de fausses alertes).
    device_id = serial or identity

    # Seul le username est requis : le profil (et son prix) est retrouvé
    # dans la base à partir du ticket pré-généré. Le script On Login n'envoie
    # d'ailleurs pas de profil ; celui rapporté ne sert que de repli pour un
    # code inconnu.
    if not username:
        return jsonify({"error": "username requis"}), 400

    # Token OBLIGATOIRE. Comparaison a temps constant pour ne pas exposer le
    # token via une attaque temporelle. Un tenant SANS token (config legacy
    # antérieure à la v2) est désormais refusé : n'importe qui connaissant
    # son slug pouvait sinon lui injecter de fausses ventes.
    router_token = tenant.get("router_token") or ""
    if router_token:
        token_ok = bool(token) and secrets.compare_digest(token, router_token)
    else:
        token_ok = False
        print(f"[SECURITY] {slug}: aucun router_token configuré — vente REFUSÉE. "
              f"Régénérez le script de ce tenant depuis la plateforme.", flush=True)

    if not token_ok:
        # Flood à jeton invalide : limité par IP, sans toucher au quota du
        # tenant. Au-delà du seuil on ne journalise même plus (disque/logs).
        if rate_limited(f"bad:{client_ip}", RATE_BAD_MAX):
            return jsonify({"status": "ok"})
        access_log.record(slug, client_ip, identity, False, device_id=device_id)
        print(f"[SECURITY] {slug}: token invalide depuis {client_ip} — ignoré", flush=True)
        return jsonify({"status": "ok"})

    # Journal d'accès : IP publique + empreinte appareil (détection de partage).
    access_log.record(slug, client_ip, identity, True, device_id=device_id)

    if rate_limited(f"slug:{slug}"):
        print(f"[SECURITY] {slug}: rate limit dépassé — vente ignorée ({username})", flush=True)
        return jsonify({"status": "ok"})

    router_display = identity or tenant.get("router_name") or ""

    _sale_executor.submit(process_sale, tenant, username, profile,
                          client_ip, router_display)

    return jsonify({"status": "ok"})


@app.route("/t/<slug>/stats")
def tenant_stats(slug):
    tenant, err = _resolve_tenant(slug)
    if err:
        return err
    # Endpoint interne (web app) : HUB_KEY exigée. Sans clé configurée,
    # on refuse tout — ces stats sont le chiffre d'affaires du client.
    if not _hub_key_ok():
        return jsonify({"error": "unauthorized"}), 403
    ts = get_today_stats(slug)
    return jsonify({"date": datetime.now().strftime("%Y-%m-%d"), "sellers": ts,
                    "total_sales": sum(s["sales"] for s in ts),
                    "total_revenue": sum(s["revenue"] for s in ts)})


@app.route("/t/<slug>/stats/week")
def tenant_stats_week(slug):
    tenant, err = _resolve_tenant(slug)
    if err:
        return err
    if not _hub_key_ok():
        return jsonify({"error": "unauthorized"}), 403
    return jsonify({"days": get_week_stats(slug)})


# ═══════════════════════════════════════════════
# LANCEMENT
# ═══════════════════════════════════════════════

def create_app():
    """Initialise le registre (appelé au démarrage, gunicorn inclus)."""
    global registry
    if registry is None:
        registry = TenantRegistry()
        registry.refresh()
        threading.Thread(target=_refresh_loop, daemon=True).start()
        print(f"[HUB] {len(registry.all())} tenant(s) chargé(s), "
              f"{bot_manager.count()} bot(s) démarré(s)", flush=True)
    return app


create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=HUB_PORT, debug=False)
