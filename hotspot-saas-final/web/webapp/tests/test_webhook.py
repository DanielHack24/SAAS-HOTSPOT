"""
test_webhook.py — Circuit complet du webhook FedaPay (intégration)

Vérifie ce qui protège l'argent : signature stricte, montant/devise
contrôlés, activation atomique (un replay ne crée jamais deux
abonnements), routeurs supplémentaires.
"""
import time
from datetime import datetime

from db import get_db
from helpers import (sign, webhook_payload, post_webhook,
                     create_client_row, create_pending_payment,
                     fetch_one, fetch_all)


# ── Rejets de sécurité ──────────────────────────────────────────

def test_webhook_sans_signature_rejete(client):
    r = post_webhook(client, webhook_payload())
    assert r.status_code == 403


def test_webhook_signature_invalide_rejete(client):
    r = post_webhook(client, webhook_payload(),
                     signature=f"t={int(time.time())},s={'0' * 64}")
    assert r.status_code == 403


def test_webhook_signature_expiree_rejete(client):
    payload = webhook_payload()
    r = post_webhook(client, payload, signature=sign(payload, ts=int(time.time()) - 3600))
    assert r.status_code == 403


def test_webhook_montant_insuffisant_refuse(client):
    cid = create_client_row()
    create_pending_payment(cid, plan="3m", amount=5000, reference="tx1")
    payload = webhook_payload(trans_id="tx1", amount=100)
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.status_code == 400
    # Le paiement reste en attente : rien n'a été activé
    assert fetch_one("SELECT status FROM payments WHERE reference='tx1'")["status"] == "pending"
    assert fetch_all("SELECT * FROM subscriptions") == []


def test_webhook_mauvaise_devise_refuse(client):
    cid = create_client_row()
    create_pending_payment(cid, plan="3m", amount=5000, reference="tx2")
    payload = webhook_payload(trans_id="tx2", amount=5000, currency="USD")
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.status_code == 400
    assert fetch_one("SELECT status FROM payments WHERE reference='tx2'")["status"] == "pending"


def test_webhook_transaction_inconnue_ignoree(client):
    payload = webhook_payload(trans_id="inexistant")
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.status_code == 200
    assert r.get_json()["status"] == "ignored"


def test_webhook_evenement_non_approved_ignore(client):
    cid = create_client_row()
    create_pending_payment(cid, reference="tx3")
    payload = webhook_payload(trans_id="tx3", name="transaction.declined")
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.status_code == 200
    assert r.get_json()["status"] == "ignored"
    assert fetch_one("SELECT status FROM payments WHERE reference='tx3'")["status"] == "pending"


# ── Activation nominale ─────────────────────────────────────────

def test_webhook_valide_active_abonnement(client):
    cid = create_client_row()
    create_pending_payment(cid, plan="3m", amount=5000, reference="tx10")

    payload = webhook_payload(trans_id="tx10", amount=5000)
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"

    payment = fetch_one("SELECT * FROM payments WHERE reference='tx10'")
    assert payment["status"] == "confirmed"
    assert payment["confirmed_at"]

    subs = fetch_all("SELECT * FROM subscriptions WHERE client_id=?", (cid,))
    assert len(subs) == 1
    sub = subs[0]
    assert sub["active"] == 1
    assert sub["plan"] == "3m"
    assert payment["subscription_id"] == sub["id"]

    # Durée : 3 mois calendaires (88 à 93 jours selon les mois)
    end = datetime.strptime(sub["end_date"], "%Y-%m-%d %H:%M:%S")
    assert 88 <= (end - datetime.now()).days <= 93


def test_webhook_montant_superieur_accepte(client):
    # Le client a payé plus que demandé (pourboire, arrondi) : on active
    cid = create_client_row()
    create_pending_payment(cid, plan="1m", amount=2000, reference="tx11")
    payload = webhook_payload(trans_id="tx11", amount=2500)
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_webhook_rejoue_n_active_qu_une_fois(client):
    """Un replay du même webhook (ou deux livraisons concurrentes) ne doit
    jamais créer un deuxième abonnement."""
    cid = create_client_row()
    create_pending_payment(cid, plan="3m", amount=5000, reference="tx20")
    payload = webhook_payload(trans_id="tx20", amount=5000)

    r1 = post_webhook(client, payload, signature=sign(payload))
    r2 = post_webhook(client, payload, signature=sign(payload))

    assert r1.get_json()["status"] == "ok"
    assert r2.get_json()["status"] == "ignored"
    assert len(fetch_all("SELECT * FROM subscriptions WHERE client_id=?", (cid,))) == 1
    assert len(fetch_all("SELECT * FROM payments WHERE status='confirmed'")) == 1


def test_renouvellement_reutilise_la_config(client):
    """Renouveler doit garder slug/bot/token du client et désactiver
    l'ancien abonnement."""
    cid = create_client_row()
    conn = get_db()
    conn.execute("""
        INSERT INTO subscriptions
            (client_id, plan, start_date, end_date, active, slug, bot_token,
             chat_id, mikrotik_ip, provisioned, router_token)
        VALUES (?, '1m', '2026-06-01 00:00:00', '2026-07-01 00:00:00', 1,
                'client-abc', '', '', '192.168.88.1', 1, 'tok-secret')
    """, (cid,))
    conn.commit()
    conn.close()
    create_pending_payment(cid, plan="12m", amount=15000, reference="tx30")

    payload = webhook_payload(trans_id="tx30", amount=15000)
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.get_json()["status"] == "ok"

    subs = fetch_all(
        "SELECT * FROM subscriptions WHERE client_id=? ORDER BY id", (cid,))
    assert len(subs) == 2
    old, new = subs
    assert old["active"] == 0
    assert new["active"] == 1
    assert new["slug"] == "client-abc"
    assert new["router_token"] == "tok-secret"
    assert new["provisioned"] == 1


# ── Routeurs supplémentaires ────────────────────────────────────

def _create_device(cid: int) -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO subscriptions (client_id, plan, start_date, end_date, active)
        VALUES (?, '3m', '2026-06-01 00:00:00', '2026-09-01 00:00:00', 1)
    """, (cid,))
    sub_id = cur.lastrowid
    cur = conn.execute("""
        INSERT INTO mikrotik_devices (subscription_id, client_id, label, ip)
        VALUES (?, ?, 'Boutique 2', '192.168.88.2')
    """, (sub_id, cid))
    device_id = cur.lastrowid
    conn.commit()
    conn.close()
    return device_id


def test_webhook_paiement_routeur_active_le_device(client):
    cid = create_client_row()
    device_id = _create_device(cid)
    conn = get_db()
    conn.execute("""
        INSERT INTO mikrotik_payments (client_id, device_id, plan, amount,
                                       method, reference, status)
        VALUES (?, ?, '1m', 2000, 'fedapay', 'txdev1', 'pending')
    """, (cid, device_id))
    conn.commit()
    conn.close()

    payload = webhook_payload(trans_id="txdev1", amount=2000,
                              metadata={"type": "device", "device_id": str(device_id)})
    r = post_webhook(client, payload, signature=sign(payload))
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"

    device = fetch_one("SELECT * FROM mikrotik_devices WHERE id=?", (device_id,))
    assert device["provisioned"] == 1
    assert device["active"] == 1
    assert device["slug"]
    assert device["end_date"]
    assert fetch_one("SELECT status FROM mikrotik_payments WHERE reference='txdev1'")["status"] == "confirmed"


def test_webhook_paiement_routeur_rejoue_ignore(client):
    cid = create_client_row()
    device_id = _create_device(cid)
    conn = get_db()
    conn.execute("""
        INSERT INTO mikrotik_payments (client_id, device_id, plan, amount,
                                       method, reference, status)
        VALUES (?, ?, '1m', 2000, 'fedapay', 'txdev2', 'pending')
    """, (cid, device_id))
    conn.commit()
    conn.close()

    payload = webhook_payload(trans_id="txdev2", amount=2000,
                              metadata={"type": "device", "device_id": str(device_id)})
    r1 = post_webhook(client, payload, signature=sign(payload))
    r2 = post_webhook(client, payload, signature=sign(payload))
    assert r1.get_json()["status"] == "ok"
    assert r2.get_json()["status"] == "ignored"
