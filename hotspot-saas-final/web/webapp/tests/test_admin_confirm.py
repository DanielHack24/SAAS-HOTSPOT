"""
test_admin_confirm.py — Confirmation manuelle d'un paiement côté admin.

Protège l'argent : un admin ne doit jamais pouvoir activer un abonnement
que FedaPay n'a pas réellement approuvé (un client qui ouvre la page de
paiement puis abandonne laisse un paiement 'pending' ; le confirmer par
erreur offrirait un accès gratuit).
"""
import fedapay
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
        s["is_admin"] = True
        s["_csrf"] = "tok"


def _confirm(client, pid):
    return client.post(f"/admin/confirm_payment/{pid}",
                       data={"csrf_token": "tok"})


def _fake_status(monkeypatch, **kw):
    monkeypatch.setattr(fedapay, "transaction_status", lambda ref: kw)


def test_confirm_refuse_si_pending(client, monkeypatch):
    cid = create_client_row(email="c@a.tg")
    pid = create_pending_payment(cid, plan="3m", amount=5000, reference="txp1")
    _login_admin(client)
    _fake_status(monkeypatch, status="pending", amount=5000, currency="XOF")

    _confirm(client, pid)

    assert fetch_one("SELECT status FROM payments WHERE id=?", (pid,))["status"] == "pending"
    assert fetch_all("SELECT * FROM subscriptions WHERE client_id=?", (cid,)) == []


def test_confirm_refuse_si_montant_insuffisant(client, monkeypatch):
    cid = create_client_row(email="c2@a.tg")
    pid = create_pending_payment(cid, plan="3m", amount=5000, reference="txp2")
    _login_admin(client)
    _fake_status(monkeypatch, status="approved", amount=100, currency="XOF")

    _confirm(client, pid)

    assert fetch_one("SELECT status FROM payments WHERE id=?", (pid,))["status"] == "pending"
    assert fetch_all("SELECT * FROM subscriptions WHERE client_id=?", (cid,)) == []


def test_confirm_refuse_si_fedapay_injoignable(client, monkeypatch):
    cid = create_client_row(email="c3@a.tg")
    pid = create_pending_payment(cid, plan="3m", amount=5000, reference="txp3")
    _login_admin(client)

    def boom(ref):
        raise RuntimeError("réseau indisponible")
    monkeypatch.setattr(fedapay, "transaction_status", boom)

    _confirm(client, pid)

    assert fetch_one("SELECT status FROM payments WHERE id=?", (pid,))["status"] == "pending"


def test_confirm_active_si_approuve(client, monkeypatch):
    cid = create_client_row(email="c4@a.tg")
    pid = create_pending_payment(cid, plan="3m", amount=5000, reference="txp4")
    _login_admin(client)
    _fake_status(monkeypatch, status="approved", amount=5000, currency="XOF")

    _confirm(client, pid)

    assert fetch_one("SELECT status FROM payments WHERE id=?", (pid,))["status"] == "confirmed"
    subs = fetch_all("SELECT * FROM subscriptions WHERE client_id=?", (cid,))
    assert len(subs) == 1
    assert subs[0]["active"] == 1
