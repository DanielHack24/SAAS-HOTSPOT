"""
test_cron_sync.py — Endpoint /cron/sync_tickets : routeurs traités en
parallèle, budget de temps borné, pas de double synchro d'un même tenant.
"""
import threading
import time

import pytest

import hotspot_sync
import routes_api
from db import get_db
from helpers import create_client_row

HDR = {"X-Cron-Key": "cron_test_key"}


def _sub(cid: int, slug: str):
    conn = get_db()
    conn.execute("""
        INSERT INTO subscriptions (client_id, plan, start_date, end_date,
                                   active, provisioned, slug)
        VALUES (?, '1m', '2026-01-01 00:00:00', '2099-01-01 00:00:00', 1, 1, ?)
    """, (cid, slug))
    conn.commit()
    conn.close()


@pytest.fixture()
def clean_state(monkeypatch):
    monkeypatch.setattr(routes_api, "_sync_running", set())


def test_sync_sans_cle_refuse(client):
    assert client.get("/cron/sync_tickets").status_code == 403


def test_routeurs_hors_ligne_traites_en_parallele(client, clean_state, monkeypatch):
    cid = create_client_row()
    for i in range(6):
        _sub(cid, f"t{i}")

    def slow_offline(slug):
        time.sleep(0.5)          # simule le timeout d'un routeur éteint
        return {"status": "offline", "pushed": 0, "pending": 3}

    monkeypatch.setattr(hotspot_sync, "push_pending", slow_offline)
    t0 = time.monotonic()
    r = client.get("/cron/sync_tickets", headers=HDR)
    elapsed = time.monotonic() - t0

    assert r.status_code == 200
    data = r.get_json()
    assert len(data["synced"]) == 6
    assert elapsed < 2.0         # en série : 3 s
    assert routes_api._sync_running == set()


def test_budget_depasse_repond_quand_meme(client, clean_state, monkeypatch):
    cid = create_client_row()
    _sub(cid, "lent")
    release = threading.Event()

    def blocked(slug):
        release.wait(5)
        return {"status": "ok", "pushed": 1, "pending": 0}

    monkeypatch.setattr(hotspot_sync, "push_pending", blocked)
    monkeypatch.setattr(routes_api, "SYNC_BUDGET_S", 0.2)
    try:
        data = client.get("/cron/sync_tickets", headers=HDR).get_json()
        assert data["still_running"] == 1
        # Un second cron pendant que le premier tourne ne relance pas ce slug
        data2 = client.get("/cron/sync_tickets", headers=HDR).get_json()
        assert data2["skipped"] == ["lent"]
    finally:
        release.set()


def test_erreur_d_un_tenant_n_arrete_pas_les_autres(client, clean_state, monkeypatch):
    cid = create_client_row()
    _sub(cid, "casse")
    _sub(cid, "sain")

    def push(slug):
        if slug == "casse":
            raise RuntimeError("boom")
        return {"status": "ok", "pushed": 2, "pending": 0}

    monkeypatch.setattr(hotspot_sync, "push_pending", push)
    data = client.get("/cron/sync_tickets", headers=HDR).get_json()
    by_slug = {r["slug"]: r for r in data["synced"]}
    assert by_slug["casse"]["status"] == "error"
    assert by_slug["sain"]["pushed"] == 2
