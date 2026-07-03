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
import os, sqlite3, threading, json, time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import urllib.request

SAAS_DIR = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")
HUB_PORT = int(os.environ.get("TENANT_HUB_PORT", "8010"))
HUB_KEY  = os.environ.get("HUB_KEY", "")   # protège /reload
REFRESH_INTERVAL = 60                       # re-lecture périodique de central.db

import sys
sys.path.insert(0, os.path.join(SAAS_DIR, "core"))
from tenant_db import get_all_tenants, tenant_sales_db  # noqa: E402

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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
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

def _tg(bot_token, method, payload, timeout=10):
    if not bot_token:
        return {}
    try:
        url  = f"https://api.telegram.org/bot{bot_token}/{method}"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(url, data=data,
               headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
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


# ── Claviers inline ──────────────────────────────────────────

def kb_main():
    return {"inline_keyboard": [
        [{"text": "📊 Statistiques par vendeur", "callback_data": "menu_sellers"}],
        [{"text": "🏆 Classement du jour",        "callback_data": "menu_ranking"}],
        [{"text": "📈 Résumé global",              "callback_data": "menu_global"}],
    ]}


def kb_sellers(sellers):
    rows = [[{"text": f"👤 {s}", "callback_data": f"seller_{s}"}] for s in sellers]
    rows.append([{"text": "🔙 Menu principal", "callback_data": "menu_main"}])
    return {"inline_keyboard": rows}


def kb_periods(seller):
    return {"inline_keyboard": [
        [{"text": "📅 Aujourd'hui", "callback_data": f"stat_{seller}_today"},
         {"text": "📆 Semaine",     "callback_data": f"stat_{seller}_week"}],
        [{"text": "🗓 Mois",        "callback_data": f"stat_{seller}_month"},
         {"text": "📊 Année",       "callback_data": f"stat_{seller}_year"}],
        [{"text": "🔙 Retour vendeurs", "callback_data": "menu_sellers"}],
    ]}


# ── Messages ─────────────────────────────────────────────────

def msg_welcome():
    return "🏪 <b>Comptabilité Hotspot MikroTik</b>\n\nBienvenue ! Utilisez les boutons ci-dessous."


def msg_sellers(sellers):
    if not sellers:
        return "👥 <b>Aucun vendeur</b>\n\nAucune vente enregistrée pour l'instant."
    return f"👥 <b>Liste des vendeurs</b>\n\n<b>{len(sellers)}</b> vendeur(s) — choisissez-en un :"


def msg_stats(slug, seller, period):
    d = get_stats_period(slug, seller, period)
    t = get_seller_total(slug, seller)
    e = {"today": "📅", "week": "📆", "month": "🗓", "year": "📊"}.get(period, "📊")
    return (
        f"👤 <b>{seller}</b>\n{'─'*28}\n"
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
        lines.append(f"{m} <b>{s['seller']}</b> — {s['sales']} vente(s) · {s['revenue']:,} FCFA")
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
    router_line = f"\n📡 Routeur  : <b>{router_display}</b>" if router_display else ""
    return (
        f"🔔 <b>Nouvelle vente</b> — {now}\n{'─'*28}\n"
        f"{emoji} Profil   : <b>{profile}</b>\n"
        f"👤 Vendeur : <b>{seller}</b>\n"
        f"🎟️ Ticket  : <code>{username}</code>\n"
        f"💰 Montant : <b>{amount:,} FCFA</b>{router_line}\n"
        f"{'─'*28}\n"
        f"📊 Aujourd'hui ({seller}) : <b>{ds} vente(s)</b> · <b>{dr:,} FCFA</b>\n"
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

    def handle_update(self, update):
        slug = self.slug
        if "message" in update:
            msg     = update["message"]
            chat_id = str(msg["chat"]["id"])
            text    = msg.get("text", "").strip()
            if text.startswith("/stats"):
                send_msg(self.bot_token, chat_id, msg_global(slug), markup=kb_main())
            elif text.startswith("/vendeurs"):
                sellers = get_all_sellers(slug)
                send_msg(self.bot_token, chat_id, msg_sellers(sellers), markup=kb_sellers(sellers))
            else:
                send_msg(self.bot_token, chat_id, msg_welcome(), markup=kb_main())

        elif "callback_query" in update:
            cb      = update["callback_query"]
            chat_id = str(cb["message"]["chat"]["id"])
            msg_id  = cb["message"]["message_id"]
            data    = cb.get("data", "")
            _tg(self.bot_token, "answerCallbackQuery", {"callback_query_id": cb["id"]})

            if data == "menu_main":
                edit_msg(self.bot_token, chat_id, msg_id, msg_welcome(), markup=kb_main())
            elif data == "menu_sellers":
                sellers = get_all_sellers(slug)
                edit_msg(self.bot_token, chat_id, msg_id, msg_sellers(sellers), markup=kb_sellers(sellers))
            elif data == "menu_ranking":
                edit_msg(self.bot_token, chat_id, msg_id, msg_ranking(slug), markup=kb_main())
            elif data == "menu_global":
                edit_msg(self.bot_token, chat_id, msg_id, msg_global(slug), markup=kb_main())
            elif data.startswith("seller_"):
                seller = data[7:]
                edit_msg(self.bot_token, chat_id, msg_id,
                         f"👤 <b>{seller}</b>\n\nChoisissez la période :", markup=kb_periods(seller))
            elif data.startswith("stat_"):
                parts = data.split("_", 2)
                if len(parts) == 3:
                    _, seller, period = parts
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
# TRAITEMENT VENTE
# ═══════════════════════════════════════════════

def process_sale(tenant: dict, username, profile, ip, router_display=""):
    """Traite la vente en arrière-plan APRÈS avoir répondu à MikroTik."""
    slug = tenant["slug"]
    if is_already_registered(slug, username):
        return   # Ticket déjà vendu, on ignore silencieusement

    parts  = username.split("-", 1)
    seller = parts[0].capitalize() if parts else username
    amount = tenant["price_map"].get(profile, 0)

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
    if HUB_KEY and request.args.get("key", "") != HUB_KEY:
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
    token     = data.get("token", "").strip()
    identity  = data.get("router", "").strip()
    client_ip = request.headers.get("X-Real-IP") or request.remote_addr or ""

    if not username or not profile:
        return jsonify({"error": "username et profile requis"}), 400

    # Token obligatoire dès qu'il est configuré (toujours le cas pour
    # les tenants créés depuis la v2)
    if tenant.get("router_token") and token != tenant["router_token"]:
        print(f"[SECURITY] {slug}: token invalide depuis {client_ip} — ignoré", flush=True)
        return jsonify({"status": "ok"})

    router_display = identity or tenant.get("router_name") or ""

    threading.Thread(
        target=process_sale,
        args=(tenant, username, profile, client_ip, router_display),
        daemon=True
    ).start()

    return jsonify({"status": "ok"})


@app.route("/t/<slug>/stats")
def tenant_stats(slug):
    tenant, err = _resolve_tenant(slug)
    if err:
        return err
    # Endpoint interne (web app) — protégé par HUB_KEY
    if HUB_KEY and request.args.get("key", "") != HUB_KEY:
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
    if HUB_KEY and request.args.get("key", "") != HUB_KEY:
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
