"""
test_admin_client.py — Fiche opérateur admin + suppression de compte.
"""
from db import get_db
from helpers import (create_client_row, create_pending_payment,
                     fetch_one, fetch_all)


def _login_admin(client):
    cid = create_client_row(email="admin@a.tg", name="Admin")
    conn = get_db()
    conn.execute("UPDATE clients SET is_admin=1 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["is_admin"]  = True
        s["_csrf"]     = "tok"
    return cid


def _make_operator():
    cid = create_client_row(email="op@a.tg", name="Op Test")
    conn = get_db()
    conn.execute("""INSERT INTO subscriptions
                      (client_id, plan, start_date, end_date, active)
                    VALUES (?, '3m', '2026-06-01 00:00:00', '2026-09-01 00:00:00', 1)""",
                 (cid,))
    conn.commit(); conn.close()
    create_pending_payment(cid, plan="3m", amount=5000, reference="txop")
    return cid


def test_fiche_accessible_admin(client):
    cid = _make_operator()
    _login_admin(client)
    r = client.get(f"/admin/client/{cid}")
    assert r.status_code == 200
    assert b"Op Test" in r.data


def test_fiche_refuse_non_admin(client):
    cid = _make_operator()
    r = client.get(f"/admin/client/{cid}")
    assert r.status_code in (301, 302)


def test_suppression_efface_tout(client):
    cid = _make_operator()
    _login_admin(client)
    r = client.post(f"/admin/client/{cid}/delete", data={"csrf_token": "tok"})
    assert r.status_code in (302, 303)
    assert fetch_all("SELECT * FROM clients WHERE id=?", (cid,)) == []
    assert fetch_all("SELECT * FROM subscriptions WHERE client_id=?", (cid,)) == []
    assert fetch_all("SELECT * FROM payments WHERE client_id=?", (cid,)) == []


def test_suppression_refuse_un_admin(client):
    admin_cid = _login_admin(client)
    client.post(f"/admin/client/{admin_cid}/delete", data={"csrf_token": "tok"})
    assert fetch_one("SELECT * FROM clients WHERE id=?", (admin_cid,)) is not None
