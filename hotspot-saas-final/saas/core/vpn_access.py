"""
vpn_access.py — Ouvre l'accès VPN de l'opérateur à SON routeur.

Le pair opérateur (poste du client) obtient une IP de tunnel. Pour qu'il
atteigne son routeur, il faut, CÔTÉ ROUTEUR :
  1. router vers le sous-réseau du tunnel (sinon les réponses ne repartent
     pas vers l'opérateur) ;
  2. accepter l'IP de l'opérateur au pare-feu (le bloc de connexion pose une
     règle « drop » sur tout le reste du tunnel).

On pousse ces deux réglages via l'API RouterOS, à travers le tunnel existant,
en réutilisant l'exécution d'un script (run_script) — même mécanisme validé
que la création en masse des tickets. Idempotent et best-effort : si le
routeur est hors ligne, on renvoie un statut et on réessaiera plus tard.
"""
import wg_store
from wireguard import WG_IFACE, WG_SUBNET
from routeros import RouterOSRest


def push_vpn_access(slug: str, client_factory=RouterOSRest) -> dict:
    """Ouvre l'accès pour TOUS les postes opérateur du tenant (multi-appareils).

    On reconstruit les règles à chaque appel : on retire les anciennes règles
    « HotspotPro VPN … » puis on ré-ajoute une règle accept par appareil
    courant. Ainsi un appareil supprimé perd son accès et un appareil ajouté
    l'obtient, sans accumulation de règles périmées.

    status : 'ok' | 'no_peer' | 'offline' | 'error'."""
    peer  = wg_store.get_peer(slug)
    peers = wg_store.list_admin_peers(slug)
    if not peer or not peers:
        return {"status": "no_peer"}

    lines = [
        # 1) le routeur route tout le sous-réseau tunnel (idempotent)
        f"/interface/wireguard/peers set [find interface={WG_IFACE}] "
        f"allowed-address={WG_SUBNET}",
        # 2) purge des règles opérateur existantes (reconstruction propre)
        r'/ip/firewall/filter remove [find comment~"HotspotPro VPN "]',
    ]
    # 3) une règle accept par appareil, avant la règle drop du tunnel
    for d in peers:
        op_ip = d["tunnel_ip"]
        lines.append(
            f"/ip/firewall/filter add chain=input in-interface={WG_IFACE} "
            f'src-address={op_ip} action=accept place-before=0 '
            f'comment="HotspotPro VPN {op_ip}"')
    src = "\n".join(lines)

    client = client_factory(peer["tunnel_ip"], peer["api_user"], peer["api_pass"])
    try:
        try:
            client.ping()
        except Exception as e:                       # routeur injoignable
            return {"status": "offline", "error": str(e)}
        try:
            client.run_script(src, name="hotspotpro_vpn")
            return {"status": "ok"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()
