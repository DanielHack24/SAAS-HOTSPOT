"""
test_wireguard.py — Génération des clés, adressage et bloc client WireGuard
(module partagé saas/core/wireguard.py).
"""
import base64

import wireguard as wg


# ── Clés ────────────────────────────────────────────────────────

def test_keypair_format_wireguard():
    priv, pub = wg.gen_keypair()
    assert len(priv) == 44 and len(pub) == 44         # 32 octets en base64
    assert len(base64.b64decode(priv)) == 32
    assert len(base64.b64decode(pub)) == 32


def test_keypairs_uniques():
    a = wg.gen_keypair()
    b = wg.gen_keypair()
    assert a[0] != b[0] and a[1] != b[1]


def test_public_from_private_deterministe():
    priv, pub = wg.gen_keypair()
    assert wg.public_from_private(priv) == pub


def test_api_password_sans_caracteres_dangereux():
    p = wg.gen_api_password()
    assert len(p) == 24
    assert not any(c in p for c in '"$\\ \'`')


# ── Adressage ───────────────────────────────────────────────────

def test_premiere_ip_evite_le_serveur():
    ip = wg.next_tunnel_ip(set())
    assert ip == "10.66.0.2"
    assert ip != wg.SERVER_TUNNEL_IP


def test_ip_suivante_saute_les_utilisees():
    used = {"10.66.0.2", "10.66.0.3"}
    assert wg.next_tunnel_ip(used) == "10.66.0.4"


# ── Bloc client ─────────────────────────────────────────────────

def test_bloc_client_contient_les_elements_cles():
    priv, _ = wg.gen_keypair()
    _, server_pub = wg.gen_keypair()
    block = wg.render_client_block(
        tunnel_ip="10.66.0.7", router_private_key=priv,
        server_public_key=server_pub, vps_endpoint="198.51.100.10",
        api_user="hotspotpro", api_pass="Abc23xyz")

    assert "interface/wireguard/add" in block
    assert priv in block
    assert server_pub in block
    assert "198.51.100.10" in block
    assert "endpoint-port=51820" in block
    assert "10.66.0.7/16" in block
    # verrouillage sécurité
    assert "/user/add name=hotspotpro" in block
    assert 'password="Abc23xyz"' in block
    assert f"src-address={wg.SERVER_TUNNEL_IP}" in block
    assert "action=drop" in block


def test_bloc_client_idempotent():
    """Re-coller le bloc ne doit rien casser : chaque opération est gardée
    par un test d'existence, et le compte API est mis à jour s'il existe."""
    priv, _ = wg.gen_keypair()
    _, server_pub = wg.gen_keypair()
    block = wg.render_client_block(
        tunnel_ip="10.66.0.7", router_private_key=priv,
        server_public_key=server_pub, vps_endpoint="nas.example.com",
        api_user="hotspotpro", api_pass="Abc23xyz")
    # Gardes d'idempotence présentes
    assert ':if ([' in block and 'do={' in block
    # Le compte API existant est mis à jour (branche else), pas seulement créé
    assert 'else={/user/set' in block


def test_bloc_client_une_seule_operation_par_ligne():
    priv, _ = wg.gen_keypair()
    _, server_pub = wg.gen_keypair()
    block = wg.render_client_block(
        tunnel_ip="10.66.0.7", router_private_key=priv,
        server_public_key=server_pub, vps_endpoint="nas.example.com",
        api_user="hotspotpro", api_pass="Abc23xyz")
    cmds = [l for l in block.splitlines() if l.strip() and not l.startswith("#")]
    # interface + peer + address + group + user + service + 2 firewall = 8
    assert len(cmds) == 8


def test_server_peer_args():
    _, pub = wg.gen_keypair()
    args = wg.server_peer_args(pub, "10.66.0.7")
    assert args[0] == "set"
    assert pub in args
    assert "10.66.0.7/32" in args
