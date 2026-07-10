"""
test_sellers_routes.py — Pages vendeurs (interface web) de bout en bout :
accès, création, génération, vue de lot, export .rsc.
"""
import json

import pytest

import tickets
from db import get_db
from helpers import create_client_row


@pytest.fixture()
def logged_client(client, tmp_path, monkeypatch):
    """Client connecté avec un abonnement provisionné + tenant en tmp."""
    monkeypatch.setattr(tickets, "SAAS_DIR", str(tmp_path))
    cid = create_client_row(email="op@op.tg", name="Operateur")
    prices = json.dumps({"1h": {"price": 100, "validity": "1h"},
                         "24h": {"price": 500, "validity": "24h"}})
    conn = get_db()
    conn.execute("""
        INSERT INTO subscriptions (client_id, plan, start_date, end_date, active,
                                   slug, provisioned, prices)
        VALUES (?, '3m', '2026-01-01 00:00:00', '2027-01-01 00:00:00', 1,
                'op-abc', 1, ?)
    """, (cid, prices))
    conn.commit(); conn.close()
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["_csrf"] = "tok"
    return client, cid


def test_page_vendeurs_ok(logged_client):
    c, _ = logged_client
    r = c.get("/vendeurs")
    assert r.status_code == 200
    assert "Vendeurs" in r.get_data(as_text=True)


def test_creation_vendeur_via_web(logged_client):
    c, _ = logged_client
    r = c.post("/vendeurs/create", data={"name": "Daniel", "csrf_token": "tok"})
    assert r.status_code in (301, 302)
    page = c.get("/vendeurs").get_data(as_text=True)
    assert "Daniel" in page


def test_generation_et_lot_via_web(logged_client):
    c, _ = logged_client
    c.post("/vendeurs/create", data={"name": "Daniel", "csrf_token": "tok"})
    # récupère l'id du vendeur créé
    import tickets as tk
    dbp = tk.sales_db_path("op-abc")
    sid = [s for s in tk.list_sellers(dbp) if s["name"] == "Daniel"][0]["id"]

    r = c.post(f"/vendeurs/{sid}/generate", data={
        "profile": "1h", "qty": "10", "code_len": "5",
        "charset": "alnum", "pw_mode": "same", "csrf_token": "tok"})
    assert r.status_code in (301, 302)
    assert f"/vendeurs/{sid}/lot/" in r.headers["Location"]

    # 10 tickets bien créés
    inv = [s for s in tk.list_sellers(dbp) if s["id"] == sid][0]
    assert inv["generated"] == 10

    # vue du lot + export .rsc
    bid = tk.list_batches(dbp, sid)[0]["id"]
    assert c.get(f"/vendeurs/{sid}/lot/{bid}").status_code == 200
    assert c.get(f"/vendeurs/{sid}/lot/{bid}/imprimer").status_code == 200
    rsc = c.get(f"/vendeurs/{sid}/lot/{bid}/rsc")
    assert rsc.status_code == 200
    assert "/ip hotspot user add" in rsc.get_data(as_text=True)


def test_profil_inconnu_refuse(logged_client):
    c, _ = logged_client
    c.post("/vendeurs/create", data={"name": "Ali", "csrf_token": "tok"})
    import tickets as tk
    dbp = tk.sales_db_path("op-abc")
    sid = [s for s in tk.list_sellers(dbp) if s["name"] == "Ali"][0]["id"]
    r = c.post(f"/vendeurs/{sid}/generate", data={
        "profile": "ZZZ", "qty": "5", "code_len": "5",
        "charset": "alnum", "pw_mode": "same", "csrf_token": "tok"})
    assert r.status_code in (301, 302)
    assert tk.list_batches(dbp, sid) == []   # rien généré


def test_vendeurs_sans_deploiement_redirige(client):
    cid = create_client_row(email="nodev@x.tg")
    with client.session_transaction() as s:
        s["client_id"] = cid
    r = client.get("/vendeurs")
    assert r.status_code in (301, 302)   # redirigé vers le dashboard


def test_plafond_tickets_forfait(logged_client):
    """Le plan 3m limite à 500 tickets/génération : 600 est refusé."""
    c, _ = logged_client
    c.post("/vendeurs/create", data={"name": "Max", "csrf_token": "tok"})
    import tickets as tk
    dbp = tk.sales_db_path("op-abc")
    sid = [s for s in tk.list_sellers(dbp) if s["name"] == "Max"][0]["id"]
    r = c.post(f"/vendeurs/{sid}/generate", data={
        "profile": "1h", "qty": "600", "code_len": "6",
        "charset": "alnum", "pw_mode": "same", "csrf_token": "tok"})
    assert r.status_code in (301, 302)
    assert tk.list_batches(dbp, sid) == []   # rien généré au-delà du plafond


def test_plafond_vendeurs_forfait(logged_client, monkeypatch):
    """Limite de vendeurs du forfait : au-delà, l'ajout est bloqué."""
    import config
    monkeypatch.setattr(config, "plan_limits",
                        lambda p: {"max_tickets": 500, "max_sellers": 1, "vpn": False})
    c, _ = logged_client
    c.post("/vendeurs/create", data={"name": "A", "csrf_token": "tok"})
    c.post("/vendeurs/create", data={"name": "B", "csrf_token": "tok"})
    import tickets as tk
    dbp = tk.sales_db_path("op-abc")
    names = [s["name"] for s in tk.list_sellers(dbp) if s["name"] != tk.UNASSIGNED]
    assert "A" in names and "B" not in names
