"""
test_wg_store.py — Stockage chiffré des pairs WireGuard (saas/core).
"""
import os
import sqlite3

import pytest

import secretbox
import wg_store
import wireguard as wg


@pytest.fixture()
def central(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "clef-de-test-wg-0123456789")
    monkeypatch.setattr(wg_store, "CENTRAL_DB", str(tmp_path / "central.db"))
    wg_store.ensure_schema()
    return wg_store.CENTRAL_DB


# ── Secretbox ───────────────────────────────────────────────────

def test_secretbox_roundtrip(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "abc")
    enc = secretbox.encrypt("motdepasse")
    assert enc.startswith("enc:")
    assert "motdepasse" not in enc
    assert secretbox.decrypt(enc) == "motdepasse"


# ── Serveur ─────────────────────────────────────────────────────

def test_set_et_get_server(central):
    wg_store.set_server("203.0.113.5")
    srv = wg_store.get_server()
    assert srv["endpoint"] == "203.0.113.5"
    assert srv["udp_port"] == wg.WG_UDP_PORT
    assert len(srv["public_key"]) == 44
    assert len(srv["private_key"]) == 44          # déchiffré


def test_cle_serveur_chiffree_en_base(central):
    wg_store.set_server("203.0.113.5")
    conn = sqlite3.connect(central)
    raw = conn.execute("SELECT private_key FROM wg_server WHERE id=1").fetchone()[0]
    conn.close()
    assert raw.startswith("enc:")               # jamais en clair


# ── Pairs ───────────────────────────────────────────────────────

def test_provision_peer_sans_serveur_echoue(central):
    with pytest.raises(RuntimeError):
        wg_store.provision_peer("client-a")


def test_provision_peer(central):
    wg_store.set_server("nas.example.com")
    peer = wg_store.provision_peer("client-a")
    assert peer["tunnel_ip"] == "10.66.0.2"
    assert "interface/wireguard/add" in peer["block"]
    assert "nas.example.com" in peer["block"]


def test_provision_peer_idempotent(central):
    wg_store.set_server("nas.example.com")
    a = wg_store.provision_peer("client-a")
    b = wg_store.provision_peer("client-a")
    assert a["tunnel_ip"] == b["tunnel_ip"]       # même IP, pas de doublon
    assert a["router_public_key"] == b["router_public_key"]


def test_deux_tenants_ips_distinctes(central):
    wg_store.set_server("nas.example.com")
    a = wg_store.provision_peer("client-a")
    b = wg_store.provision_peer("client-b")
    assert a["tunnel_ip"] != b["tunnel_ip"]


def test_secrets_peer_chiffres_en_base(central):
    wg_store.set_server("nas.example.com")
    wg_store.provision_peer("client-a")
    conn = sqlite3.connect(central)
    row = conn.execute("SELECT router_private_key, api_pass FROM wg_peers WHERE slug='client-a'").fetchone()
    conn.close()
    assert row[0].startswith("enc:") and row[1].startswith("enc:")
    # déchiffrement correct via get_peer
    peer = wg_store.get_peer("client-a")
    assert len(peer["router_private_key"]) == 44
    assert len(peer["api_pass"]) == 24


def test_all_peers_public_sans_secrets(central):
    wg_store.set_server("nas.example.com")
    wg_store.provision_peer("client-a")
    pubs = wg_store.all_peers_public()
    assert len(pubs) == 1
    assert set(pubs[0].keys()) == {"slug", "tunnel_ip", "router_public_key"}


# ── Pairs opérateur (VPN) ───────────────────────────────────────

def test_provision_admin_peer(central):
    wg_store.set_server("vpn.example.com")
    wg_store.provision_peer("client-a")
    a = wg_store.provision_admin_peer("client-a")
    assert a["router_ip"] == "10.66.0.2"
    assert a["tunnel_ip"] != a["router_ip"]
    assert "AllowedIPs = 10.66.0.2/32" in a["config"]
    assert "vpn.example.com:51820" in a["config"]


def test_provision_admin_peer_idempotent(central):
    wg_store.set_server("vpn.example.com")
    wg_store.provision_peer("client-a")
    a = wg_store.provision_admin_peer("client-a")
    b = wg_store.provision_admin_peer("client-a")
    assert a["tunnel_ip"] == b["tunnel_ip"]


def test_admin_peer_sans_routeur_echoue(central):
    wg_store.set_server("vpn.example.com")
    with pytest.raises(RuntimeError):
        wg_store.provision_admin_peer("client-sans-routeur")


def test_admin_peer_secret_chiffre(central):
    wg_store.set_server("vpn.example.com")
    wg_store.provision_peer("client-a")
    wg_store.provision_admin_peer("client-a")
    conn = sqlite3.connect(central)
    raw = conn.execute("SELECT private_key FROM wg_admin_peers WHERE slug='client-a'").fetchone()[0]
    conn.close()
    assert raw.startswith("enc:")
    pub = wg_store.all_admin_peers_public()
    assert set(pub[0].keys()) == {"slug", "tunnel_ip", "public_key"}


# ── Multi-appareils (forfait 12 mois) ───────────────────────────

def test_add_admin_peer_multi_ips_distinctes(central):
    wg_store.set_server("vpn.example.com")
    wg_store.provision_peer("client-a")
    d1 = wg_store.provision_admin_peer("client-a")        # Appareil 1
    d2 = wg_store.add_admin_peer("client-a", "PC bureau")
    d3 = wg_store.add_admin_peer("client-a", "Téléphone")
    ips = {d1["tunnel_ip"], d2["tunnel_ip"], d3["tunnel_ip"]}
    assert len(ips) == 3                                  # trois IP distinctes
    assert wg_store.count_admin_peers("client-a") == 3
    labels = {p["label"] for p in wg_store.list_admin_peers("client-a")}
    assert {"Appareil 1", "PC bureau", "Téléphone"} == labels


def test_get_admin_config_par_appareil(central):
    wg_store.set_server("vpn.example.com")
    wg_store.provision_peer("client-a")
    wg_store.provision_admin_peer("client-a")
    d2 = wg_store.add_admin_peer("client-a", "Téléphone")
    cfg = wg_store.get_admin_config("client-a", d2["id"])
    assert cfg["tunnel_ip"] == d2["tunnel_ip"]
    assert f"Address = {d2['tunnel_ip']}/32" in cfg["config"]
    assert "AllowedIPs = 10.66.0.2/32" in cfg["config"]   # route vers le routeur


def test_delete_admin_peer_libere(central):
    wg_store.set_server("vpn.example.com")
    wg_store.provision_peer("client-a")
    wg_store.provision_admin_peer("client-a")
    d2 = wg_store.add_admin_peer("client-a", "Téléphone")
    assert wg_store.delete_admin_peer("client-a", d2["id"]) is True
    assert wg_store.count_admin_peers("client-a") == 1
    assert wg_store.get_admin_config("client-a", d2["id"]) is None


def test_all_admin_peers_public_couvre_tous(central):
    wg_store.set_server("vpn.example.com")
    wg_store.provision_peer("client-a")
    wg_store.provision_admin_peer("client-a")
    wg_store.add_admin_peer("client-a", "Téléphone")
    pub = wg_store.all_admin_peers_public()
    assert len(pub) == 2                                  # wg_sync voit les 2
    assert all(set(p.keys()) == {"slug", "tunnel_ip", "public_key"} for p in pub)
