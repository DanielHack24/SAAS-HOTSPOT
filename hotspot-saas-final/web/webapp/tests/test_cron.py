"""
test_cron.py — Endpoint /cron/check_expiry (expiration des abonnements)
et sonde /healthz.
"""
from datetime import datetime, timedelta

import config
from db import get_db
from helpers import create_client_row, fetch_one


def _create_sub(cid: int, end_date: str, active=1, provisioned=0) -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO subscriptions (client_id, plan, start_date, end_date,
                                   active, provisioned)
        VALUES (?, '1m', '2026-01-01 00:00:00', ?, ?, ?)
    """, (cid, end_date, active, provisioned))
    conn.commit()
    sub_id = cur.lastrowid
    conn.close()
    return sub_id


def test_cron_sans_cle_refuse(client):
    assert client.get("/cron/check_expiry").status_code == 403


def test_cron_mauvaise_cle_refuse(client):
    r = client.get("/cron/check_expiry",
                   headers={"X-Cron-Key": "mauvaise-cle"})
    assert r.status_code == 403


def test_cron_refuse_tout_si_cle_non_configuree(client):
    saved = config.CRON_KEY
    try:
        config.CRON_KEY = ""
        # Même une clé vide envoyée ne doit pas matcher une config vide
        r = client.get("/cron/check_expiry", headers={"X-Cron-Key": ""})
        assert r.status_code == 403
    finally:
        config.CRON_KEY = saved


def test_cron_expire_les_abonnements_depasses(client):
    cid = create_client_row()
    past = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    sub_id = _create_sub(cid, past, active=1)

    r = client.get("/cron/check_expiry",
                   headers={"X-Cron-Key": config.CRON_KEY})
    assert r.status_code == 200
    data = r.get_json()
    assert any(e["id"] == sub_id for e in data["expired"])
    assert fetch_one("SELECT active FROM subscriptions WHERE id=?",
                     (sub_id,))["active"] == 0


def test_cron_laisse_les_abonnements_valides(client):
    cid = create_client_row()
    future = (datetime.now() + timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    sub_id = _create_sub(cid, future, active=1)

    r = client.get("/cron/check_expiry",
                   headers={"X-Cron-Key": config.CRON_KEY})
    assert r.status_code == 200
    assert fetch_one("SELECT active FROM subscriptions WHERE id=?",
                     (sub_id,))["active"] == 1


def test_cron_signale_les_expirations_proches(client):
    cid = create_client_row()
    soon = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    sub_id = _create_sub(cid, soon, active=1)

    r = client.get("/cron/check_expiry",
                   headers={"X-Cron-Key": config.CRON_KEY})
    data = r.get_json()
    assert any(e["id"] == sub_id for e in data["expiring"])
    # Signalé mais PAS désactivé
    assert fetch_one("SELECT active FROM subscriptions WHERE id=?",
                     (sub_id,))["active"] == 1


def test_cron_expire_les_routeurs_supplementaires(client):
    cid = create_client_row()
    future = (datetime.now() + timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
    sub_id = _create_sub(cid, future, active=1)
    past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cur = conn.execute("""
        INSERT INTO mikrotik_devices (subscription_id, client_id, label, ip,
                                      slug, provisioned, active, end_date)
        VALUES (?, ?, 'Boutique 2', '192.168.88.2', 'dev-slug', 1, 1, ?)
    """, (sub_id, cid, past))
    device_id = cur.lastrowid
    conn.commit()
    conn.close()

    r = client.get("/cron/check_expiry",
                   headers={"X-Cron-Key": config.CRON_KEY})
    data = r.get_json()
    assert any(d["id"] == device_id for d in data["expired_devices"])
    assert fetch_one("SELECT active FROM mikrotik_devices WHERE id=?",
                     (device_id,))["active"] == 0
    # L'abonnement principal, lui, reste actif
    assert fetch_one("SELECT active FROM subscriptions WHERE id=?",
                     (sub_id,))["active"] == 1


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"
