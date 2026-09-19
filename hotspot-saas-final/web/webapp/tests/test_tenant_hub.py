"""
test_tenant_hub.py — Hub multi-tenant (saas/src/tenant_hub.py) :
accès au bot réservé au propriétaire, rate limiting qui ne pénalise plus
le tenant sur jeton invalide, boutons vendeurs courts et non ambigus.
"""
import os
import sys

import pytest

HUB_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "..", "saas", "src"))


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    import tenant_db
    # L'import du hub lit central.db : on le pointe vers une base jetable.
    monkeypatch.setattr(tenant_db, "SAAS_DIR", str(tmp_path))
    monkeypatch.setattr(tenant_db, "CENTRAL_DB", str(tmp_path / "central.db"))
    tenant_db.init_central_db()
    if HUB_SRC not in sys.path:
        sys.path.insert(0, HUB_SRC)
    import tenant_hub

    tenant = {"slug": "client-a", "active": True, "chat_id": "1111",
              "bot_token": "", "router_token": "bon-jeton",
              "router_name": "", "price_map": {}}

    class FakeRegistry:
        def get(self, slug):
            return tenant if slug == "client-a" else None

        def all(self):
            return [tenant]

        def refresh(self):
            pass

    monkeypatch.setattr(tenant_hub, "registry", FakeRegistry())
    monkeypatch.setattr(tenant_hub, "_rate", {})

    logged, submitted, sent = [], [], []
    monkeypatch.setattr(tenant_hub.access_log, "record",
                        lambda slug, ip, ident, ok, device_id="": logged.append(ok))

    class FakeExecutor:
        def submit(self, fn, *args):
            submitted.append(args)

    monkeypatch.setattr(tenant_hub, "_sale_executor", FakeExecutor())
    monkeypatch.setattr(tenant_hub, "send_msg",
                        lambda tok, chat, text, markup=None: sent.append(("send", chat, text, markup)))
    monkeypatch.setattr(tenant_hub, "edit_msg",
                        lambda tok, chat, mid, text, markup=None: sent.append(("edit", chat, text, markup)))
    monkeypatch.setattr(tenant_hub, "_tg", lambda *a, **k: {})

    tenant_hub.app.config["TESTING"] = True
    tenant_hub.logged, tenant_hub.submitted, tenant_hub.sent = logged, submitted, sent
    return tenant_hub


# ── Rate limiting ────────────────────────────────────────────────

def test_flood_jeton_invalide_ne_bloque_pas_les_vraies_ventes(hub):
    c = hub.app.test_client()
    for _ in range(hub.RATE_MAX + 50):
        c.post("/t/client-a/login", data={"username": "X", "token": "faux"})
    assert hub.submitted == []

    r = c.post("/t/client-a/login", data={"username": "VRAI1", "token": "bon-jeton"})
    assert r.status_code == 200
    assert len(hub.submitted) == 1
    assert hub.submitted[0][1] == "VRAI1"


def test_flood_jeton_invalide_journalisation_bornee(hub):
    c = hub.app.test_client()
    for _ in range(hub.RATE_BAD_MAX + 20):
        c.post("/t/client-a/login", data={"username": "X", "token": "faux"})
    assert len(hub.logged) == hub.RATE_BAD_MAX
    assert not any(hub.logged)


def test_rate_limit_slug_sur_ventes_authentifiees(hub):
    c = hub.app.test_client()
    for i in range(hub.RATE_MAX + 5):
        c.post("/t/client-a/login", data={"username": f"T{i}", "token": "bon-jeton"})
    assert len(hub.submitted) == hub.RATE_MAX


# ── Bot réservé au propriétaire ──────────────────────────────────

def _msg(chat_id, user_id, text):
    return {"message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": text}}


def test_bot_refuse_un_inconnu(hub, monkeypatch):
    monkeypatch.setattr(hub, "msg_global", lambda slug: "CHIFFRE-AFFAIRES")
    w = hub.BotWorker("tok", "client-a")
    w.handle_update(_msg(9999, 9999, hub.BTN_GLOBAL))
    assert len(hub.sent) == 1
    assert "CHIFFRE-AFFAIRES" not in hub.sent[0][2]
    assert "Bot privé" in hub.sent[0][2]


def test_bot_repond_au_proprietaire(hub, monkeypatch):
    monkeypatch.setattr(hub, "msg_global", lambda slug: "CHIFFRE-AFFAIRES")
    w = hub.BotWorker("tok", "client-a")
    w.handle_update(_msg(1111, 1111, hub.BTN_GLOBAL))
    assert hub.sent[-1][2] == "CHIFFRE-AFFAIRES"


def test_bot_groupe_du_proprietaire_autorise(hub, monkeypatch):
    # chat_id configuré = un groupe ; un membre y écrit
    hub.registry.get("client-a")["chat_id"] = "-100500"
    monkeypatch.setattr(hub, "msg_global", lambda slug: "CHIFFRE-AFFAIRES")
    w = hub.BotWorker("tok", "client-a")
    w.handle_update(_msg(-100500, 4242, hub.BTN_GLOBAL))
    assert hub.sent[-1][2] == "CHIFFRE-AFFAIRES"


def test_callback_inconnu_refuse(hub, monkeypatch):
    monkeypatch.setattr(hub, "get_all_sellers", lambda slug: ["Ama"])
    w = hub.BotWorker("tok", "client-a")
    w.handle_update({"callback_query": {
        "id": "cb1", "from": {"id": 9999}, "data": "menu_sellers",
        "message": {"chat": {"id": 9999}, "message_id": 5}}})
    assert hub.sent == []


# ── Boutons vendeurs ─────────────────────────────────────────────

def test_callback_data_court_meme_nom_long(hub):
    long_name = "Vendeur_au_nom_vraiment_tres_long_" * 4
    kb = hub.kb_sellers([long_name])["inline_keyboard"]
    kp = hub.kb_periods(long_name)["inline_keyboard"]
    datas = [b["callback_data"] for row in kb + kp for b in row]
    assert all(len(d.encode("utf-8")) <= 64 for d in datas)


def test_nom_avec_tiret_bas_retrouve(hub, monkeypatch):
    monkeypatch.setattr(hub, "get_all_sellers", lambda slug: ["jean_paul", "Ama"])
    key = hub.seller_key("jean_paul")
    assert hub.parse_callback("client-a", f"s:{key}") == ("seller", "jean_paul", None)
    assert hub.parse_callback("client-a", f"p:{key}:month") == ("stat", "jean_paul", "month")
    assert hub.parse_callback("client-a", "p:inconnu:month") is None
    assert hub.parse_callback("client-a", f"p:{key}:hack") is None


def test_ancien_format_toujours_compris(hub):
    assert hub.parse_callback("client-a", "stat_jean_paul_week") == ("stat", "jean_paul", "week")
    assert hub.parse_callback("client-a", "seller_Ama") == ("seller", "Ama", None)


def test_drill_down_vendeur_proprietaire(hub, monkeypatch):
    monkeypatch.setattr(hub, "get_all_sellers", lambda slug: ["jean_paul"])
    monkeypatch.setattr(hub, "msg_stats", lambda slug, s, p: f"STATS {s} {p}")
    w = hub.BotWorker("tok", "client-a")
    key = hub.seller_key("jean_paul")
    w.handle_update({"callback_query": {
        "id": "cb1", "from": {"id": 1111}, "data": f"p:{key}:today",
        "message": {"chat": {"id": 1111}, "message_id": 5}}})
    assert hub.sent[-1][2] == "STATS jean_paul today"
