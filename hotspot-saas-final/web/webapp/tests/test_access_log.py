"""
test_access_log.py — Journal d'accès routeur + détection de partage (saas/core).
"""
import pytest

import access_log


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(access_log, "CENTRAL_DB", str(tmp_path / "central.db"))
    access_log.ensure_schema()
    return access_log.CENTRAL_DB


def test_record_puis_recent(store):
    access_log.record("client-a", "41.2.3.4", "MikroTik-Salon")
    rows = access_log.recent()
    assert len(rows) == 1
    assert rows[0]["slug"] == "client-a"
    assert rows[0]["source_ip"] == "41.2.3.4"
    assert rows[0]["router_id"] == "MikroTik-Salon"
    assert rows[0]["hits"] == 1


def test_meme_ip_incremente_hits_sans_doublon(store):
    for _ in range(3):
        access_log.record("client-a", "41.2.3.4")
    rows = access_log.recent(slug="client-a")
    assert len(rows) == 1               # une seule ligne par (slug, ip)
    assert rows[0]["hits"] == 3


def test_slug_vide_ou_ip_vide_ignore(store):
    access_log.record("", "41.2.3.4")
    access_log.record("client-a", "")
    assert access_log.recent() == []


def test_partage_detecte_deux_appareils(store):
    access_log.record("client-a", "41.2.3.4",  device_id="SN-AAA")
    access_log.record("client-a", "197.9.9.9", device_id="SN-BBB")  # 2e routeur
    access_log.record("client-b", "80.1.1.1",  device_id="SN-CCC")  # 1 seul appareil
    alerts = access_log.sharing_report(window_hours=48, min_devices=2)
    slugs = {a["slug"] for a in alerts}
    assert "client-a" in slugs
    assert "client-b" not in slugs
    a = next(a for a in alerts if a["slug"] == "client-a")
    assert a["device_count"] == 2
    assert "SN-AAA" in a["devices"] and "SN-BBB" in a["devices"]


def test_meme_appareil_ip_change_pas_alerte(store):
    # Le cas des faux positifs : UN seul routeur dont l'IP WAN change (mobile).
    access_log.record("client-a", "41.2.3.4",  device_id="SN-AAA")
    access_log.record("client-a", "197.9.9.9", device_id="SN-AAA")
    assert access_log.sharing_report(window_hours=48, min_devices=2) == []


def test_ip_privee_loopback_ignoree(store):
    # nginx sans X-Real-IP (127.0.0.1), tunnel (10.66.0.1) et LAN : jamais
    # des routeurs distincts -> ne doivent pas être journalisés.
    access_log.record("client-a", "127.0.0.1")
    access_log.record("client-a", "10.66.0.1", via="wg-tunnel")
    access_log.record("client-a", "192.168.88.1")
    assert access_log.recent(slug="client-a") == []


def test_ip_serveur_vps_ignoree(store, monkeypatch):
    monkeypatch.setenv("VPS_PUBLIC_IP", "107.21.168.36")
    access_log.record("client-a", "107.21.168.36")   # boucle sur le VPS
    access_log.record("client-a", "41.2.3.4")        # vrai routeur
    rows = access_log.recent(slug="client-a")
    assert len(rows) == 1
    assert rows[0]["source_ip"] == "41.2.3.4"


def test_ip_publique_seule_avec_interne_pas_alerte(store):
    # Une seule IP publique + du bruit interne ne doit pas lever d'alerte.
    access_log.record("client-a", "41.2.3.4")
    access_log.record("client-a", "127.0.0.1")
    access_log.record("client-a", "10.66.0.1", via="wg-tunnel")
    assert access_log.sharing_report(window_hours=48, min_devices=2) == []


def test_via_tunnel_enregistre_canal(store):
    access_log.record("client-a", "41.2.3.4", via="wg-tunnel")
    row = access_log.recent(slug="client-a")[0]
    assert row["last_via"] == "wg-tunnel"


def test_meme_ip_onlogin_puis_tunnel_fusionne_et_garde_identite(store):
    access_log.record("client-a", "41.2.3.4", router_id="MikroTik-Salon")
    access_log.record("client-a", "41.2.3.4", router_id="", via="wg-tunnel")
    rows = access_log.recent(slug="client-a")
    assert len(rows) == 1                       # même IP => une seule ligne
    assert rows[0]["router_id"] == "MikroTik-Salon"   # identité non écrasée
    assert rows[0]["last_via"] == "wg-tunnel"
    assert rows[0]["hits"] == 2


def test_tunnel_et_onlogin_meme_routeur_pas_alerte(store):
    # On-login (avec empreinte) + endpoint tunnel (sans empreinte) du MÊME
    # routeur : deux IP, un seul appareil -> aucune alerte.
    access_log.record("client-a", "41.2.3.4", device_id="SN-AAA", via="on-login")
    access_log.record("client-a", "197.9.9.9", via="wg-tunnel")
    assert access_log.sharing_report(window_hours=48, min_devices=2) == []


def test_token_ko_incremente_bad_token(store):
    access_log.record("client-a", "41.2.3.4", token_ok=False)
    access_log.record("client-a", "41.2.3.4", token_ok=True)
    row = access_log.recent(slug="client-a")[0]
    assert row["bad_token"] == 1
    assert row["hits"] == 2


def test_une_seule_ip_pas_alerte(store):
    access_log.record("client-a", "41.2.3.4")
    access_log.record("client-a", "41.2.3.4")
    assert access_log.sharing_report(window_hours=48, min_devices=2) == []


# ── Page admin ──────────────────────────────────────────────────

from db import get_db                                    # noqa: E402
from helpers import create_client_row                    # noqa: E402


def _login_admin(client):
    cid = create_client_row(email="admin@a.tg")
    conn = get_db()
    conn.execute("UPDATE clients SET is_admin=1 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["is_admin"] = True


def test_page_acces_refuse_non_admin(client):
    r = client.get("/admin/access")
    assert r.status_code in (301, 302)


def test_page_acces_affiche_partage(client, tmp_path, monkeypatch):
    monkeypatch.setattr(access_log, "CENTRAL_DB", str(tmp_path / "central.db"))
    access_log.ensure_schema()
    access_log.record("client-a", "41.2.3.4",  device_id="SN-1")
    access_log.record("client-a", "197.9.9.9", device_id="SN-2")
    _login_admin(client)
    r = client.get("/admin/access")
    assert r.status_code == 200
    assert b"client-a" in r.data
    assert b"SN-1" in r.data and b"SN-2" in r.data
