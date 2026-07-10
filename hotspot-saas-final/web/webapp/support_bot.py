"""
support_bot.py — Bot de support Telegram (webhook).

Logique du bot : menu de la base de connaissances, reconnaissance du texte
libre, ouverture de tickets et boucle de reponse bidirectionnelle avec
l'admin (l'admin repond en repondant au message du ticket dans son chat
Telegram ; le bot relaie a l'operateur).

Aucune dependance a Flask : testable en injectant un faux transport via
`set_transport`. Ne leve jamais d'exception vers l'appelant (webhook).
"""
import json
import secrets
import urllib.request

import config
import support_kb
from db import get_db

API = "https://api.telegram.org/bot{token}/{method}"

# Transport injectable (tests). Par defaut : appel HTTP reel.
_transport = None


def set_transport(fn):
    """Remplace l'appel reseau (tests). fn(method, payload) -> dict."""
    global _transport
    _transport = fn


# ── Reglages ────────────────────────────────────────────────────

def bot_token() -> str:      return config.support_bot_token()
def admin_chat_id() -> str:  return str(config.support_chat_id() or "").strip()


# ── Liaison compte HotspotPro <-> chat Telegram ─────────────────

def create_link_token(client_id) -> str:
    """Jeton a usage unique (30 min) pour lier un compte via un lien profond
    t.me/<bot>?start=<token>."""
    token = secrets.token_urlsafe(18)
    conn = get_db()
    conn.execute("""INSERT INTO support_link_tokens (token, client_id, expires_at)
                    VALUES (?, ?, datetime('now','localtime','+30 minutes'))""",
                 (token, client_id))
    conn.commit(); conn.close()
    return token


def _consume_link_token(token):
    conn = get_db()
    row = conn.execute("""SELECT client_id FROM support_link_tokens
                          WHERE token=? AND expires_at > datetime('now','localtime')""",
                       (token,)).fetchone()
    if row:
        conn.execute("DELETE FROM support_link_tokens WHERE token=?", (token,))
        conn.commit()
    conn.close()
    return row["client_id"] if row else None


def link_chat(chat_id, client_id):
    conn = get_db()
    conn.execute("""INSERT INTO support_accounts (chat_id, client_id, linked_at)
                    VALUES (?, ?, datetime('now','localtime'))
                    ON CONFLICT(chat_id) DO UPDATE SET client_id=excluded.client_id,
                                                       linked_at=excluded.linked_at""",
                 (str(chat_id), client_id))
    conn.commit(); conn.close()


def linked_client(chat_id):
    """Compte HotspotPro lie a ce chat (nom, e-mail, forfait actif) ou None."""
    conn = get_db()
    row = conn.execute("""SELECT c.id, c.full_name, c.email
                          FROM support_accounts sa JOIN clients c ON sa.client_id=c.id
                          WHERE sa.chat_id=?""", (str(chat_id),)).fetchone()
    if not row:
        conn.close()
        return None
    info = dict(row)
    sub = conn.execute("""SELECT plan, active FROM subscriptions
                          WHERE client_id=? ORDER BY active DESC, id DESC LIMIT 1""",
                       (info["id"],)).fetchone()
    conn.close()
    info["plan"] = sub["plan"] if sub else None
    info["active"] = bool(sub["active"]) if sub else False
    return info


def webhook_secret() -> str:
    """Secret d'URL du webhook, genere et persiste une fois."""
    s = config.support_webhook_secret()
    if s:
        return s
    s = secrets.token_urlsafe(24)
    try:
        import settings
        conn = get_db()
        settings.set_value(conn, "support_webhook_secret", s)
        conn.commit(); conn.close()
    except Exception:
        pass
    return s


# ── Transport Telegram ──────────────────────────────────────────

def _api(method: str, payload: dict) -> dict:
    """Appelle l'API Telegram. Retourne le JSON, ou {} en cas d'echec.
    Ne leve jamais."""
    if _transport is not None:
        try:
            return _transport(method, payload) or {}
        except Exception:
            return {}
    token = bot_token()
    if not token:
        return {}
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            API.format(token=token, method=method),
            data=data, headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except Exception:
        return {}


def _send(chat_id, text, keyboard=None) -> dict:
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
         "disable_web_page_preview": True}
    if keyboard is not None:
        p["reply_markup"] = {"inline_keyboard": keyboard}
    return _api("sendMessage", p)


def _edit(chat_id, message_id, text, keyboard=None) -> dict:
    p = {"chat_id": chat_id, "message_id": message_id, "text": text,
         "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard is not None:
        p["reply_markup"] = {"inline_keyboard": keyboard}
    return _api("editMessageText", p)


def _answer_cb(cb_id, text=""):
    _api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})


# ── Menus ───────────────────────────────────────────────────────

WELCOME = (
    "<b>Support HotspotPro</b>\n\n"
    "Bonjour ! Choisissez le sujet qui correspond a votre probleme pour "
    "obtenir une solution immediate. Si rien ne correspond, choisissez "
    "\"Autre probleme\" pour ecrire a un conseiller."
)


def _menu_keyboard(chat_id=None):
    rows = []
    # Raccourci diagnostic en direct pour les comptes lies.
    if chat_id is not None and linked_client(chat_id):
        rows.append([{"text": "Mon diagnostic en direct",
                      "callback_data": "diag"}])
    rows += [[{"text": e["label"], "callback_data": "kb:" + e["id"]}]
             for e in support_kb.KB]
    rows.append([{"text": "Autre probleme (ecrire au support)",
                  "callback_data": "other"}])
    return rows


def _answer_keyboard(kb_id):
    return [
        [{"text": "C'est resolu, merci", "callback_data": "ack:" + kb_id}],
        [{"text": "Parler a un humain", "callback_data": "human:" + kb_id}],
        [{"text": "Retour au menu", "callback_data": "menu"}],
    ]


def _back_keyboard():
    return [[{"text": "Retour au menu", "callback_data": "menu"}]]


# ── Etat conversationnel (FSM minimal, persiste en base) ─────────

def _set_state(chat_id, state):
    conn = get_db()
    conn.execute("""
        INSERT INTO support_sessions (chat_id, state, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(chat_id) DO UPDATE SET state=excluded.state,
                                           updated_at=excluded.updated_at
    """, (str(chat_id), state))
    conn.commit(); conn.close()


def _get_state(chat_id):
    conn = get_db()
    row = conn.execute("SELECT state FROM support_sessions WHERE chat_id=?",
                       (str(chat_id),)).fetchone()
    conn.close()
    return row["state"] if row else None


def _clear_state(chat_id):
    conn = get_db()
    conn.execute("DELETE FROM support_sessions WHERE chat_id=?", (str(chat_id),))
    conn.commit(); conn.close()


# ── Tickets + alertes ───────────────────────────────────────────

def _admin_email() -> str:
    """Destinataire des alertes : reglage dedie, sinon 1er compte admin."""
    em = (config.support_alert_email() or "").strip()
    if em:
        return em
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT email FROM clients WHERE is_admin=1 ORDER BY id LIMIT 1").fetchone()
        conn.close()
        return row["email"] if row else ""
    except Exception:
        return ""


def create_ticket(chat_id, tg_username, tg_name, message, category="autre"):
    """Enregistre un ticket, alerte l'admin (Telegram + e-mail). Retourne l'id.

    Si le chat est lie a un compte HotspotPro, le ticket est rattache a ce
    compte et les alertes incluent le contexte (nom, e-mail, forfait, lien
    vers la fiche admin) pour un support plus rapide."""
    lc = linked_client(chat_id)
    client_id = lc["id"] if lc else None

    conn = get_db()
    cur = conn.execute("""
        INSERT INTO support_tickets (chat_id, tg_username, tg_name, category,
                                     message, status, client_id)
        VALUES (?, ?, ?, ?, ?, 'open', ?)
    """, (str(chat_id), tg_username or "", tg_name or "", category, message, client_id))
    conn.commit()
    tid = cur.lastrowid
    conn.close()

    who = ("@" + tg_username) if tg_username else (tg_name or "operateur")

    ctx = ""
    if lc:
        etat = "actif" if lc["active"] else "inactif/aucun"
        ctx = (f"Compte : <b>{_escape(lc['full_name'])}</b> ({_escape(lc['email'])})\n"
               f"Forfait : {lc['plan'] or 'aucun'} ({etat})\n"
               f"Fiche : {config.APP_URL}/admin/client/{lc['id']}\n")

    admin = admin_chat_id()
    if admin:
        head = (f"<b>Nouveau ticket #{tid}</b>\n"
                f"De : {who} (chat {chat_id})\n"
                f"{ctx}\n"
                f"{_escape(message)}\n\n"
                f"<i>Repondez a ce message pour repondre a l'operateur.</i>")
        res = _send(admin, head)
        msg_id = (res.get("result") or {}).get("message_id")
        if msg_id:
            conn = get_db()
            conn.execute("UPDATE support_tickets SET admin_msg_id=? WHERE id=?",
                         (str(msg_id), tid))
            conn.commit(); conn.close()

    try:
        import emails
        to = _admin_email()
        if to:
            emails.email_support_ticket(to, {
                "id": tid, "who": who, "chat_id": chat_id,
                "message": message, "category": category, "account": lc})
    except Exception:
        pass
    return tid


def _escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Traitement des updates ──────────────────────────────────────

def handle_update(update: dict):
    """Point d'entree du webhook. Ne leve jamais."""
    try:
        if "callback_query" in update:
            _handle_callback(update["callback_query"])
        elif "message" in update:
            _handle_message(update["message"])
    except Exception as e:            # pragma: no cover
        print(f"[SUPPORT] handle_update: {e}", flush=True)


def _diag_report(chat_id) -> str:
    """Rapport de diagnostic en direct pour le compte lie a ce chat."""
    lc = linked_client(chat_id)
    if not lc:
        return ("Pour un diagnostic en direct, liez d'abord votre compte : "
                "espace client > Support > \"Ouvrir le support et lier mon compte\".")
    try:
        import support_diag
        return support_diag.report_text(support_diag.account_health(lc["id"]))
    except Exception:
        return "Diagnostic momentanement indisponible. Reessayez dans un instant."


def _live_prefix(chat_id, kb_id) -> str:
    """Petit encart d'etat reel devant certaines fiches (routeur/tickets)."""
    if kb_id not in ("vpn_offline", "no_tickets"):
        return ""
    lc = linked_client(chat_id)
    if not lc:
        return ""
    try:
        import support_diag
        h = support_diag.account_health(lc["id"])
        r = (h["routers"][0] if h and h["routers"] else None)
        if not r or r["router_online"] is None:
            return ""
        if r["router_online"]:
            return (f"Etat actuel : votre routeur <b>{support_diag._esc(r['router_name'])}</b> "
                    f"est <b>EN LIGNE</b> (contact {support_diag.human_ago(r['last_seen'])}).\n\n")
        return (f"Etat actuel : votre routeur <b>{support_diag._esc(r['router_name'])}</b> "
                f"est <b>HORS LIGNE</b> (dernier contact {support_diag.human_ago(r['last_seen'])}).\n\n")
    except Exception:
        return ""


def _tickets_last_hour(chat_id) -> int:
    conn = get_db()
    n = conn.execute("""SELECT COUNT(*) c FROM support_tickets
                        WHERE chat_id=? AND created_at > datetime('now','localtime','-1 hour')""",
                     (str(chat_id),)).fetchone()["c"]
    conn.close()
    return n


def _handle_callback(cb: dict):
    data = cb.get("data", "")
    msg = cb.get("message", {}) or {}
    chat_id = (msg.get("chat", {}) or {}).get("id")
    message_id = msg.get("message_id")
    cb_id = cb.get("id")

    if data == "menu":
        _edit(chat_id, message_id, WELCOME, _menu_keyboard(chat_id))
        _answer_cb(cb_id)
    elif data == "diag":
        _answer_cb(cb_id, "Analyse en cours...")
        _send(chat_id, _diag_report(chat_id), _back_keyboard())
    elif data.startswith("kb:"):
        e = support_kb.KB_BY_ID.get(data[3:])
        if e:
            text = _live_prefix(chat_id, e["id"]) + e["answer"]
            _edit(chat_id, message_id, text, _answer_keyboard(e["id"]))
        _answer_cb(cb_id)
    elif data.startswith("ack:"):
        _edit(chat_id, message_id,
              "Parfait, ravi d'avoir pu aider. Revenez quand vous voulez.",
              _back_keyboard())
        _answer_cb(cb_id, "Merci !")
    elif data == "other" or data.startswith("human:"):
        _set_state(chat_id, "await_free")
        _send(chat_id,
              "Decrivez votre probleme en un seul message. Indiquez si "
              "possible l'e-mail de votre compte HotspotPro et, pour un "
              "paiement, la reference FedaPay. Un conseiller vous repondra ici.")
        _answer_cb(cb_id)
    else:
        _answer_cb(cb_id)


def _handle_message(msg: dict):
    chat = msg.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    frm = msg.get("from", {}) or {}
    username = frm.get("username")
    name = " ".join(x for x in [frm.get("first_name"), frm.get("last_name")] if x)

    # 1) Reponse de l'admin a un ticket (relais vers l'operateur)
    if str(chat_id) == admin_chat_id() and msg.get("reply_to_message"):
        if _relay_admin_reply(msg):
            return

    if not text:
        return

    # 1bis) Commandes d'administration (depuis le chat admin uniquement)
    if str(chat_id) == admin_chat_id() and text.startswith("/"):
        if text.startswith("/tickets"):
            _send(chat_id, _admin_tickets_text()); return
        if text.startswith("/stats"):
            _send(chat_id, _admin_stats_text()); return

    # 2) Commandes / demarrage (avec liaison de compte via lien profond)
    if text.startswith("/start") or text.startswith("/help") or text.startswith("/menu"):
        _clear_state(chat_id)
        parts = text.split(maxsplit=1)
        if text.startswith("/start") and len(parts) > 1 and parts[1].strip():
            cid = _consume_link_token(parts[1].strip())
            if cid:
                link_chat(chat_id, cid)
                lc = linked_client(chat_id)
                nom = _escape(lc["full_name"]) if lc else "votre compte"
                _send(chat_id,
                      f"Compte lie : <b>{nom}</b>. Vos demandes seront reconnues "
                      "automatiquement. Comment pouvons-nous vous aider ?",
                      _menu_keyboard(chat_id))
                return
            _send(chat_id,
                  "Ce lien de connexion est invalide ou expire. Vous pouvez "
                  "en generer un nouveau depuis votre espace client, page Support.")
        _send(chat_id, WELCOME, _menu_keyboard(chat_id))
        return

    # 3) En attente d'une description -> ouverture de ticket
    if _get_state(chat_id) == "await_free":
        if _tickets_last_hour(chat_id) >= 5:
            _clear_state(chat_id)
            _send(chat_id,
                  "Vous avez ouvert plusieurs demandes recemment. Un conseiller "
                  "va vous repondre. Merci de patienter avant d'en envoyer une "
                  "nouvelle.", _back_keyboard())
            return
        _clear_state(chat_id)
        tid = create_ticket(chat_id, username, name, text)
        _send(chat_id,
              f"Votre demande a bien ete transmise (ticket #{tid}). "
              "Un conseiller vous repondra ici meme. Merci !",
              _back_keyboard())
        return

    # 4) Texte libre spontane -> tentative de reponse par mots-cles
    hits = support_kb.match(text)
    if hits:
        e = hits[0]
        _send(chat_id,
              "Voici ce qui correspond le mieux a votre message :\n\n"
              + e["answer"], _answer_keyboard(e["id"]))
    else:
        _send(chat_id,
              "Je n'ai pas trouve de reponse automatique. Choisissez un sujet "
              "ci-dessous, ou \"Autre probleme\" pour ecrire a un conseiller.",
              _menu_keyboard(chat_id))


def _admin_tickets_text() -> str:
    conn = get_db()
    rows = conn.execute("""SELECT id, tg_username, tg_name, message, created_at
                           FROM support_tickets WHERE status='open'
                           ORDER BY id DESC LIMIT 15""").fetchall()
    conn.close()
    if not rows:
        return "Aucun ticket ouvert. Tout est traite."
    out = [f"<b>Tickets ouverts ({len(rows)})</b>"]
    for r in rows:
        who = ("@" + r["tg_username"]) if r["tg_username"] else (r["tg_name"] or "operateur")
        snippet = _escape((r["message"] or "")[:80])
        out.append(f"\n#{r['id']} · {who} · {r['created_at'][:16]}\n{snippet}")
    out.append("\n\n<i>Pour repondre : ouvrez le message du ticket concerne et "
               "repondez-y (Reply).</i>")
    return "\n".join(out)


def _admin_stats_text() -> str:
    conn = get_db()
    total   = conn.execute("SELECT COUNT(*) c FROM support_tickets").fetchone()["c"]
    opened  = conn.execute("SELECT COUNT(*) c FROM support_tickets WHERE status='open'").fetchone()["c"]
    answered= conn.execute("SELECT COUNT(*) c FROM support_tickets WHERE status='answered'").fetchone()["c"]
    today   = conn.execute("""SELECT COUNT(*) c FROM support_tickets
                              WHERE created_at > datetime('now','localtime','start of day')""").fetchone()["c"]
    linked  = conn.execute("SELECT COUNT(*) c FROM support_accounts").fetchone()["c"]
    conn.close()
    return (f"<b>Statistiques support</b>\n\n"
            f"• Tickets aujourd'hui : {today}\n"
            f"• Ouverts : {opened}\n"
            f"• Repondus : {answered}\n"
            f"• Total : {total}\n"
            f"• Comptes lies : {linked}")


def _relay_admin_reply(msg: dict) -> bool:
    """L'admin repond a un message de ticket : on relaie a l'operateur."""
    reply_to = msg.get("reply_to_message", {}) or {}
    src_id = reply_to.get("message_id")
    text = (msg.get("text") or "").strip()
    if not src_id or not text:
        return False
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM support_tickets WHERE admin_msg_id=?",
        (str(src_id),)).fetchone()
    if not row:
        conn.close()
        return False
    ticket = dict(row)
    conn.execute("""UPDATE support_tickets
                    SET status='answered', answered_at=datetime('now','localtime')
                    WHERE id=?""", (ticket["id"],))
    conn.commit(); conn.close()
    _send(ticket["chat_id"],
          "<b>Reponse du support HotspotPro</b>\n\n" + _escape(text),
          _back_keyboard())
    _send(msg["chat"]["id"], f"Reponse envoyee a l'operateur (ticket #{ticket['id']}).")
    return True


# ── Configuration du webhook ────────────────────────────────────

def set_webhook(base_url: str) -> dict:
    """Enregistre le webhook aupres de Telegram. base_url = https://domaine.
    Le meme secret sert dans l'URL et dans l'en-tete de verification."""
    secret = webhook_secret()
    url = base_url.rstrip("/") + "/telegram/webhook/" + secret
    return _api("setWebhook", {
        "url": url,
        "secret_token": secret,
        "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    })


def delete_webhook() -> dict:
    return _api("deleteWebhook", {"drop_pending_updates": False})


def verify_webhook(path_secret: str, header_secret: str) -> bool:
    """Valide une requete entrante : le secret d'URL ET l'en-tete Telegram
    doivent correspondre au secret enregistre."""
    good = webhook_secret()
    if not good:
        return False
    ok_path = secrets.compare_digest(str(path_secret or ""), good)
    ok_head = secrets.compare_digest(str(header_secret or ""), good)
    return ok_path and ok_head
