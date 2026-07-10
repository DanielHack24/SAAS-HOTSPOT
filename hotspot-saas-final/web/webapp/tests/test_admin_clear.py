"""
test_admin_clear.py — Boutons « Vider l'historique » du tableau de bord admin.

Vider un historique ne doit jamais supprimer ce qui est encore actionnable
(paiements en attente) ni couper un service en cours (abonnements actifs).
"""
from db import get_db
from helpers import (create_client_row, create_pending_payment,
                     fetch_all)


def _login_admin(client):
    cid = create_client_row(email="admin@a.tg", name="Admin")
    conn = get_db()
    conn.execute("UPDATE clients SET is_admin=1 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["is_admin"] = True
        s["_csrf"] = "tok"


def _confirm_payment(client_id, plan="3m", amount=5000, ref="txok"):
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO payments (client_id, plan, amount, method, reference, status)
        VALUES (?,?,?,'fedapay',?,'confirmed')
    """, (client_id, plan, amount, ref))
    conn.commit(); pid = cur.lastrowid; conn.close()
    return pid


def _sub(client_id, active):
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO subscriptions (client_id, plan, start_date, end_date, active)
        VALUES (?, '3m', datetime('now'), datetime('now','+90 days'), ?)
    """, (client_id, active))
    conn.commit(); sid = cur.lastrowid; conn.close()
    return sid


def test_vider_paiements_garde_les_en_attente(client):
    cid = create_client_row(email="c@a.tg")
    pend = create_pending_payment(cid, reference="pend1")
    done = _confirm_payment(cid, ref="done1")
    _login_admin(client)

    client.post("/admin/clear_payments", data={"csrf_token": "tok"})

    rows = fetch_all("SELECT id, status FROM payments")
    ids = {r["id"] for r in rows}
    assert pend in ids          # en attente conservé
    assert done not in ids      # confirmé purgé


def test_vider_abonnements_garde_les_actifs(client):
    cid = create_client_row(email="c2@a.tg")
    actif = _sub(cid, 1)
    inactif = _sub(cid, 0)
    _login_admin(client)

    client.post("/admin/clear_subscriptions", data={"csrf_token": "tok"})

    ids = {r["id"] for r in fetch_all("SELECT id FROM subscriptions")}
    assert actif in ids
    assert inactif not in ids


def test_vider_refuse_si_non_admin(client):
    cid = create_client_row(email="c3@a.tg")
    create_pending_payment(cid, reference="p3")
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["_csrf"] = "tok"
    r = client.post("/admin/clear_payments", data={"csrf_token": "tok"})
    assert r.status_code in (301, 302, 403)
