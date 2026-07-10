"""
test_support.py — Bot de support Telegram : base de connaissances, menus,
tickets et boucle de reponse admin -> operateur.
"""
import pytest

import config
import support_kb
import support_bot
from db import get_db


# ── Base de connaissances ───────────────────────────────────────

def test_kb_match_vpn():
    hits = support_kb.match("mon vpn est deconnecte depuis ce matin")
    assert hits and hits[0]["id"] == "vpn_offline"


def test_kb_match_paiement():
    hits = support_kb.match("j'ai paye mais mon abonnement n'est pas active")
    assert hits and hits[0]["id"] == "payment_not_active"


def test_kb_match_vide():
    assert support_kb.match("azerty qwerty zzz") == []


# ── Transport factice ───────────────────────────────────────────

@pytest.fixture()
def calls(monkeypatch):
    sent = []
    counter = {"n": 100}

    def transport(method, payload):
        sent.append((method, payload))
        counter["n"] += 1
        return {"ok": True, "result": {"message_id": counter["n"]}}

    support_bot.set_transport(transport)
    monkeypatch.setattr(config, "support_chat_id", lambda: "999")
    monkeypatch.setattr(config, "support_bot_token", lambda: "T")
    yield sent
    support_bot.set_transport(None)


def _methods(calls):
    return [m for m, _ in calls]


# ── Menus ───────────────────────────────────────────────────────

def test_start_affiche_menu(client, calls):
    support_bot.handle_update({"message": {"chat": {"id": 7}, "text": "/start",
                                           "from": {"id": 7, "first_name": "Koffi"}}})
    assert "sendMessage" in _methods(calls)
    m, p = calls[-1]
    kb = p["reply_markup"]["inline_keyboard"]
    assert any("other" == b["callback_data"] for row in kb for b in row)


def test_callback_kb_montre_solution(client, calls):
    support_bot.handle_update({"callback_query": {
        "id": "cb1", "data": "kb:vpn_offline",
        "message": {"chat": {"id": 7}, "message_id": 50}}})
    assert "editMessageText" in _methods(calls)
    edited = [p for m, p in calls if m == "editMessageText"][0]
    assert "VPN" in edited["text"] or "vpn" in edited["text"].lower()


# ── Tickets ─────────────────────────────────────────────────────

def test_other_puis_message_cree_ticket(client, calls, monkeypatch):
    recorded = {}
    import emails
    monkeypatch.setattr(emails, "email_support_ticket",
                        lambda to, t: recorded.update(t))

    # 1) clic "Autre probleme" -> etat en attente
    support_bot.handle_update({"callback_query": {
        "id": "cb", "data": "other",
        "message": {"chat": {"id": 42}, "message_id": 1}}})
    # 2) l'operateur decrit son probleme
    support_bot.handle_update({"message": {
        "chat": {"id": 42}, "text": "Mon routeur ne veut pas demarrer",
        "from": {"id": 42, "username": "koffi", "first_name": "Koffi"}}})

    row = get_db().execute("SELECT * FROM support_tickets").fetchone()
    assert row is not None
    assert row["chat_id"] == "42"
    assert row["status"] == "open"
    # alerte admin Telegram envoyee (vers chat 999) + e-mail
    assert any(p.get("chat_id") == "999" for m, p in calls if m == "sendMessage")
    assert recorded.get("id") == row["id"]


def test_texte_libre_propose_solution(client, calls):
    support_bot.handle_update({"message": {
        "chat": {"id": 8}, "text": "je ne recois pas le mail de verification",
        "from": {"id": 8}}})
    sent = [p for m, p in calls if m == "sendMessage"][-1]
    assert "e-mail" in sent["text"].lower() or "spam" in sent["text"].lower()


# ── Boucle de reponse admin -> operateur ────────────────────────

def test_admin_reply_relaye_a_operateur(client, calls):
    # Cree un ticket dont admin_msg_id = id du message admin renvoye
    tid = support_bot.create_ticket(42, "koffi", "Koffi", "probleme X")
    admin_msg_id = get_db().execute(
        "SELECT admin_msg_id FROM support_tickets WHERE id=?", (tid,)).fetchone()["admin_msg_id"]

    calls.clear()
    support_bot.handle_update({"message": {
        "chat": {"id": 999}, "text": "Redemarrez le routeur puis reessayez.",
        "from": {"id": 999},
        "reply_to_message": {"message_id": int(admin_msg_id)}}})

    # message relaye vers l'operateur (chat 42)
    assert any(p.get("chat_id") == 42 or p.get("chat_id") == "42"
               for m, p in calls if m == "sendMessage")
    status = get_db().execute(
        "SELECT status FROM support_tickets WHERE id=?", (tid,)).fetchone()["status"]
    assert status == "answered"


# ── Liaison de compte ───────────────────────────────────────────

def test_start_avec_token_lie_le_compte(client, calls):
    from helpers import create_client_row
    cid = create_client_row(email="op@a.tg", name="Operateur Un")
    token = support_bot.create_link_token(cid)

    support_bot.handle_update({"message": {
        "chat": {"id": 55}, "text": "/start " + token,
        "from": {"id": 55, "first_name": "Op"}}})

    lc = support_bot.linked_client(55)
    assert lc and lc["id"] == cid
    # message de confirmation contient le nom du compte
    sent = [p for m, p in calls if m == "sendMessage"][-1]
    assert "Operateur Un" in sent["text"]


def test_token_usage_unique(client, calls):
    from helpers import create_client_row
    cid = create_client_row(email="op2@a.tg", name="Op Deux")
    token = support_bot.create_link_token(cid)
    assert support_bot._consume_link_token(token) == cid
    assert support_bot._consume_link_token(token) is None   # deja consomme


def test_ticket_rattache_au_compte_lie(client, calls, monkeypatch):
    from helpers import create_client_row
    import emails
    monkeypatch.setattr(emails, "email_support_ticket", lambda to, t: None)
    cid = create_client_row(email="op3@a.tg", name="Op Trois")
    support_bot.link_chat(77, cid)

    tid = support_bot.create_ticket(77, "op3", "Op Trois", "ça ne marche pas")

    row = get_db().execute("SELECT client_id FROM support_tickets WHERE id=?",
                           (tid,)).fetchone()
    assert row["client_id"] == cid
    # l'alerte admin mentionne le compte
    admin_msg = [p for m, p in calls if m == "sendMessage"
                 and p.get("chat_id") == "999"][0]
    assert "Op Trois" in admin_msg["text"]


# ── Diagnostic en direct ────────────────────────────────────────

def test_report_text_routeur_hors_ligne():
    import support_diag
    health = {"name": "Op", "routers": [{
        "slug": "op-x", "router_name": "Salon", "plan": "12m", "days_left": 40,
        "end_date": "2026-09-01", "provisioned": True,
        "router_online": False, "last_seen": 1000, "vpn_devices": []}]}
    txt = support_diag.report_text(health)
    assert "HORS LIGNE" in txt and "Salon" in txt


def test_diag_callback_pour_compte_lie(client, calls, monkeypatch):
    from helpers import create_client_row
    import support_diag
    cid = create_client_row(email="d@a.tg", name="Diag Op")
    support_bot.link_chat(70, cid)
    monkeypatch.setattr(support_diag, "account_health", lambda c: {
        "name": "Diag Op", "routers": [{
            "slug": "s", "router_name": "R1", "plan": "12m", "days_left": 10,
            "end_date": "2026-08-01", "provisioned": True,
            "router_online": True, "last_seen": __import__("time").time(),
            "vpn_devices": []}]})
    support_bot.handle_update({"callback_query": {
        "id": "c", "data": "diag", "message": {"chat": {"id": 70}, "message_id": 3}}})
    sent = [p for m, p in calls if m == "sendMessage"][-1]
    assert "Diagnostic" in sent["text"]


def test_menu_montre_diagnostic_si_lie(client, calls):
    from helpers import create_client_row
    cid = create_client_row(email="m@a.tg", name="Lie")
    support_bot.link_chat(71, cid)
    kb = support_bot._menu_keyboard(71)
    assert any(b["callback_data"] == "diag" for row in kb for b in row)
    # non lie : pas de bouton diagnostic
    kb2 = support_bot._menu_keyboard(72)
    assert not any(b["callback_data"] == "diag" for row in kb2 for b in row)


# ── Commandes admin & anti-abus ─────────────────────────────────

def test_admin_commande_stats(client, calls):
    support_bot.handle_update({"message": {
        "chat": {"id": 999}, "text": "/stats", "from": {"id": 999}}})
    sent = [p for m, p in calls if m == "sendMessage"][-1]
    assert "Statistiques support" in sent["text"]


def test_rate_limit_tickets(client, calls, monkeypatch):
    import emails
    monkeypatch.setattr(emails, "email_support_ticket", lambda to, t: None)
    conn = get_db()
    for i in range(5):
        conn.execute("""INSERT INTO support_tickets (chat_id, message, status)
                        VALUES ('88','x','open')""")
    conn.commit(); conn.close()
    support_bot._set_state(88, "await_free")
    support_bot.handle_update({"message": {
        "chat": {"id": 88}, "text": "encore un probleme", "from": {"id": 88}}})
    n = get_db().execute("SELECT COUNT(*) c FROM support_tickets WHERE chat_id='88'").fetchone()["c"]
    assert n == 5   # 6e refuse


# ── Alertes proactives ──────────────────────────────────────────

def test_notify_routeur_hors_ligne(client, calls, monkeypatch):
    import support_notify, support_diag
    from helpers import create_client_row
    cid = create_client_row(email="n@a.tg", name="Notif Op")
    support_bot.link_chat(60, cid)
    monkeypatch.setattr(support_diag, "account_health", lambda c: {
        "name": "Notif Op", "routers": [{
            "slug": "s1", "router_name": "Boutique", "plan": "12m",
            "days_left": 40, "end_date": "2026-09-01", "provisioned": True,
            "router_online": False, "last_seen": 1000, "vpn_devices": []}]})
    n1 = support_notify.run(dry=False)
    assert n1 == 1
    msg = [p for m, p in calls if m == "sendMessage"][-1]
    assert "hors ligne" in msg["text"].lower() and "Boutique" in msg["text"]
    # 2e passage : pas de doublon (etat 'down' memorise)
    n2 = support_notify.run(dry=False)
    assert n2 == 0


# ── Verification du webhook ─────────────────────────────────────

def test_verify_webhook(monkeypatch):
    monkeypatch.setattr(config, "support_webhook_secret", lambda: "SEC")
    assert support_bot.verify_webhook("SEC", "SEC") is True
    assert support_bot.verify_webhook("SEC", "bad") is False
    assert support_bot.verify_webhook("bad", "SEC") is False
