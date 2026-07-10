"""
test_configure_tunnel.py — Endpoints d'onboarding (tunnel WireGuard + lecture
des profils du routeur). On teste les chemins de repli, sans routeur ni
serveur WireGuard disponibles : les endpoints doivent dégrader proprement
(jamais planter) et réserver le slug.
"""
from db import get_db
from helpers import create_client_row, fetch_one


def _login(client, cid):
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["_csrf"] = "tok"


def _create_sub(cid, provisioned=0, slug=None):
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO subscriptions (client_id, plan, start_date, end_date,
                                   active, provisioned, slug)
        VALUES (?, '1m', '2026-01-01 00:00:00', '2027-01-01 00:00:00', 1, ?, ?)
    """, (cid, provisioned, slug))
    conn.commit()
    sid = cur.lastrowid
    conn.close()
    return sid


def test_tunnel_reserve_le_slug(client):
    cid = create_client_row()
    sid = _create_sub(cid)
    _login(client, cid)
    r = client.post("/configure/tunnel", headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200
    d = r.get_json()
    # Selon que le serveur WireGuard central est initialisé ou non, `ready`
    # peut varier ; l'invariant garanti est la réservation du slug + token.
    if d["ready"]:
        assert d.get("block")
    else:
        assert d["reason"] in ("wg_not_ready", "unavailable")
    row = fetch_one("SELECT slug, router_token FROM subscriptions WHERE id=?", (sid,))
    assert row["slug"]
    assert row["router_token"]


def test_tunnel_sans_csrf_refuse(client):
    cid = create_client_row()
    _create_sub(cid)
    _login(client, cid)
    r = client.post("/configure/tunnel")   # pas d'en-tête X-CSRF-Token
    assert r.status_code == 400


def test_tunnel_sans_abonnement(client):
    cid = create_client_row()
    _login(client, cid)
    r = client.post("/configure/tunnel", headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400
    assert r.get_json()["ready"] is False


def test_tunnel_status_sans_peer(client):
    cid = create_client_row()
    _create_sub(cid, slug="demo-status")
    _login(client, cid)
    r = client.get("/configure/tunnel/status")
    assert r.status_code == 200
    d = r.get_json()
    assert d["online"] is False
    assert d["reason"] in ("no_peer", "unavailable", "offline")


def test_router_profiles_sans_peer(client):
    cid = create_client_row()
    _create_sub(cid, slug="demo-profiles")
    _login(client, cid)
    r = client.get("/configure/router-profiles")
    assert r.status_code == 200
    assert r.get_json()["ok"] is False


def test_endpoints_exigent_login(client):
    # Non authentifié : redirection vers la connexion (302), pas d'exception
    assert client.get("/configure/tunnel/status").status_code in (302, 401)
    assert client.get("/configure/router-profiles").status_code in (302, 401)
