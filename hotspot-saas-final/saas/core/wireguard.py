"""
wireguard.py — Tunnel sécurisé plateforme <-> routeurs clients (option B).

Chaque tenant reçoit un pair WireGuard : le routeur se connecte à notre
VPS (il sort vers nous, donc marche derrière NAT / IP changeante). Une
fois le tunnel monté, la plateforme peut POUSSER les tickets dans le
User Manager du routeur via l'API RouterOS, à travers le tunnel.

Ce module est pur (aucune base, aucun accès réseau) : il génère les clés,
alloue les adresses de tunnel et rend le bloc de configuration que le
client colle UNE SEULE FOIS. Le stockage (central.db) et l'application
côté serveur (wg set) sont gérés ailleurs.

Sécurité intégrée au bloc généré :
  - compte API dédié (mot de passe aléatoire, distinct de l'admin) ;
  - service API accessible UNIQUEMENT depuis l'IP tunnel du serveur ;
  - pare-feu : sur le tunnel, seule notre IP atteint l'API, tout le reste
    est bloqué ;
  - chaque routeur sur sa propre /32 (pas de routeur-à-routeur).
"""
import base64
import secrets

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption)

# ── Réseau du tunnel ───────────────────────────────────────────
WG_IFACE          = "wg-hotspotpro"
WG_SUBNET         = "10.66.0.0/16"
SERVER_TUNNEL_IP  = "10.66.0.1"          # la plateforme, côté tunnel
TUNNEL_NET_PREFIX = "10.66"              # 10.66.x.y
WG_UDP_PORT       = 51820                # port UDP public du serveur WG
ROUTER_LISTEN     = 13231                # port d'écoute WG côté routeur
API_SVC           = "www"               # REST API RouterOS (HTTP, dans le tunnel chiffré)
API_PORT          = 80
API_GROUP         = "hotspotpro"
API_USER          = "hotspotpro"


# ═══════════════════════════════════════════════
# CLÉS
# ═══════════════════════════════════════════════

def gen_keypair() -> tuple[str, str]:
    """Retourne (clé_privée_b64, clé_publique_b64) au format WireGuard."""
    priv = X25519PrivateKey.generate()
    priv_b64 = base64.b64encode(
        priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return priv_b64, pub_b64


def public_from_private(priv_b64: str) -> str:
    raw = base64.b64decode(priv_b64)
    priv = X25519PrivateKey.from_private_bytes(raw)
    return base64.b64encode(
        priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def gen_api_password(length: int = 24) -> str:
    """Mot de passe API sans caractères susceptibles de casser un script
    RouterOS (pas de guillemets, $, \\, espaces)."""
    alphabet = ("ABCDEFGHJKLMNPQRSTUVWXYZ"
                "abcdefghijkmnpqrstuvwxyz23456789")
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ═══════════════════════════════════════════════
# ADRESSAGE
# ═══════════════════════════════════════════════

def next_tunnel_ip(used: set[str]) -> str:
    """Prochaine IP de tunnel libre (10.66.0.2 .. 10.66.255.254),
    en évitant .1 (serveur) et les .0/.255 de chaque bloc."""
    used = set(used or [])
    for third in range(0, 256):
        for fourth in range(1, 255):
            ip = f"{TUNNEL_NET_PREFIX}.{third}.{fourth}"
            if ip in (SERVER_TUNNEL_IP,) or ip in used:
                continue
            return ip
    raise RuntimeError("Plus d'adresses de tunnel disponibles.")


# ═══════════════════════════════════════════════
# GÉNÉRATION DES CONFIGS
# ═══════════════════════════════════════════════

def render_client_block(*, tunnel_ip: str, router_private_key: str,
                        server_public_key: str, vps_endpoint: str,
                        api_user: str, api_pass: str,
                        wg_port: int = WG_UDP_PORT) -> str:
    """Bloc RouterOS 7 à coller sur le routeur du client.

    Monte le tunnel, crée le compte API dédié et verrouille l'accès par
    pare-feu (seule notre IP de tunnel atteint l'API).

    Idempotent : chaque opération est conditionnée à l'absence de l'objet,
    donc re-coller le bloc (ou le coller sur un routeur déjà partiellement
    configuré) ne produit aucune erreur « already exists ». Le compte API
    est mis à jour s'il existe déjà, pour garantir le bon mot de passe."""
    FW_API  = "HotspotPro API"
    FW_DROP = "HotspotPro: bloque le reste du tunnel"

    def guard(find: str, cmd: str) -> str:
        return f':if ([{find}]="") do={{{cmd}}}'

    lines = [
        "# ===== HotspotPro — connexion securisee =====",
        "# RouterOS 7 requis. Re-coller ce bloc est sans risque (idempotent).",
        guard(
            f"/interface/wireguard find name={WG_IFACE}",
            f'/interface/wireguard/add name={WG_IFACE} '
            f'private-key="{router_private_key}" listen-port={ROUTER_LISTEN}',
        ),
        guard(
            f"/interface/wireguard/peers find interface={WG_IFACE}",
            f'/interface/wireguard/peers/add interface={WG_IFACE} '
            f'public-key="{server_public_key}" endpoint-address={vps_endpoint} '
            f'endpoint-port={wg_port} allowed-address={SERVER_TUNNEL_IP}/32 '
            f'persistent-keepalive=25s',
        ),
        guard(
            f"/ip/address find interface={WG_IFACE}",
            f'/ip/address/add interface={WG_IFACE} address={tunnel_ip}/16',
        ),
        guard(
            f"/user/group find name={API_GROUP}",
            f'/user/group/add name={API_GROUP} '
            f'policy=api,rest-api,read,write,test,winbox,web,password,sensitive',
        ),
        # Compte API : créé s'il manque, sinon mot de passe/groupe remis à jour.
        f':if ([/user find name={api_user}]="") '
        f'do={{/user/add name={api_user} password="{api_pass}" group={API_GROUP} '
        f'comment="HotspotPro (ne pas supprimer)"}} '
        f'else={{/user/set [find name={api_user}] password="{api_pass}" group={API_GROUP}}}',
        # /ip/service/set est déjà idempotent par nature.
        f'/ip/service/set {API_SVC} address={SERVER_TUNNEL_IP}/32 disabled=no',
        guard(
            f'/ip/firewall/filter find comment="{FW_API}"',
            f'/ip/firewall/filter/add chain=input in-interface={WG_IFACE} '
            f'src-address={SERVER_TUNNEL_IP} protocol=tcp dst-port={API_PORT} '
            f'action=accept place-before=0 comment="{FW_API}"',
        ),
        guard(
            f'/ip/firewall/filter find comment="{FW_DROP}"',
            f'/ip/firewall/filter/add chain=input in-interface={WG_IFACE} '
            f'action=drop comment="{FW_DROP}"',
        ),
    ]
    return "\n".join(lines) + "\n"


def render_admin_config(*, admin_private_key: str, admin_ip: str,
                        server_public_key: str, vps_endpoint: str,
                        router_ip: str, wg_port: int = WG_UDP_PORT) -> str:
    """Fichier .conf WireGuard pour le POSTE de l'opérateur (PC/téléphone).

    Lui donne accès à SON routeur (router_ip) à travers le serveur : le poste
    monte un tunnel vers le VPS et ne route QUE l'IP du routeur (AllowedIPs en
    /32), rien d'autre du trafic Internet du poste ne passe par le VPN."""
    return (
        "[Interface]\n"
        f"PrivateKey = {admin_private_key}\n"
        f"Address = {admin_ip}/32\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_public_key}\n"
        f"Endpoint = {vps_endpoint}:{wg_port}\n"
        f"AllowedIPs = {router_ip}/32\n"
        "PersistentKeepalive = 25\n"
    )


def server_peer_args(router_public_key: str, tunnel_ip: str) -> list[str]:
    """Arguments `wg set` pour enregistrer le routeur côté serveur, sans
    redémarrer l'interface. Pas d'endpoint : le routeur initie le tunnel."""
    return ["set", WG_IFACE, "peer", router_public_key,
            "allowed-ips", f"{tunnel_ip}/32", "persistent-keepalive", "25"]
