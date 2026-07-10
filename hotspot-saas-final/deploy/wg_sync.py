#!/usr/bin/env python3
"""
wg_sync.py — Applique les pairs WireGuard des tenants sur l'interface.

Source de vérité : la table wg_peers de central.db (gérée par l'app web).
Ce service (root) lit les informations NON secrètes (clé publique + IP de
tunnel) et les applique via `wg set`, sans redémarrer l'interface. Il
retire aussi les pairs présents sur l'interface mais absents de la base.

Aucun secret n'est manipulé ici (ni clé privée, ni mot de passe API) :
pas besoin de SECRET_KEY. Lancé au démarrage puis toutes les minutes par
un timer systemd.
"""
import os
import subprocess
import sys

WEBAPP_CORE = os.environ.get("HOTSPOT_SAAS_CORE", "/opt/hotspot-saas/core")
IFACE = "wg-hotspotpro"

sys.path.insert(0, WEBAPP_CORE)


def _wg(*args) -> str:
    return subprocess.run(["wg", *args], capture_output=True, text=True,
                          timeout=30).stdout.strip()


def current_peers() -> set[str]:
    """Clés publiques actuellement configurées sur l'interface."""
    out = _wg("show", IFACE, "peers")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _endpoint_ip(endpoint: str) -> str:
    """Extrait l'IP d'un endpoint WireGuard 'IP:port' (IPv4 ou [IPv6]:port)."""
    endpoint = (endpoint or "").strip()
    if not endpoint or endpoint == "(none)":
        return ""
    if endpoint.startswith("["):                    # [IPv6]:port
        return endpoint[1:endpoint.find("]")] if "]" in endpoint else ""
    return endpoint.rsplit(":", 1)[0]               # IPv4:port


def peer_endpoints() -> dict:
    """pubkey -> IP publique source, pour les pairs ayant un handshake récent.

    `wg show <iface> dump` : 1re ligne = interface ; puis par pair :
    pubkey, psk, endpoint, allowed-ips, latest-handshake(unix), rx, tx, keepalive.
    On ne retient que les endpoints avec un handshake < 10 min (IP WAN réelle
    du routeur vue par le serveur ; sinon endpoint périmé)."""
    import time
    out = _wg("show", IFACE, "dump")
    fresh = {}
    lines = out.splitlines()
    for line in lines[1:]:                           # saute la ligne interface
        cols = line.split("\t")
        if len(cols) < 5:
            continue
        pub, endpoint, handshake = cols[0], cols[2], cols[4]
        try:
            hs = int(handshake)
        except ValueError:
            hs = 0
        if hs and (time.time() - hs) < 600:
            ip = _endpoint_ip(endpoint)
            if ip:
                fresh[pub] = ip
    return fresh


def peer_handshakes() -> dict:
    """pubkey -> epoch unix du dernier handshake, pour TOUS les pairs (0 si
    jamais). Persisté dans central.db pour que l'app web affiche l'état de
    connexion (routeur en ligne, poste opérateur connecté)."""
    out = _wg("show", IFACE, "dump")
    res = {}
    for line in out.splitlines()[1:]:                # saute la ligne interface
        cols = line.split("\t")
        if len(cols) < 5:
            continue
        try:
            res[cols[0]] = int(cols[4])
        except ValueError:
            res[cols[0]] = 0
    return res


def main() -> int:
    try:
        import wg_store
    except Exception as e:
        print(f"[wg-sync] import wg_store impossible : {e}", flush=True)
        return 1

    try:
        peers = wg_store.all_peers_public()
    except Exception as e:
        print(f"[wg-sync] lecture central.db impossible : {e}", flush=True)
        return 1

    desired    = {p["router_public_key"]: p["tunnel_ip"] for p in peers}
    slug_bykey = {p["router_public_key"]: p["slug"] for p in peers}

    # Pairs opérateur (VPN d'accès au routeur) : appliqués aussi sur
    # l'interface, mais PAS journalisés (leur IP publique diffère de celle du
    # routeur -> ne doit pas déclencher de fausse alerte de partage).
    try:
        for p in wg_store.all_admin_peers_public():
            desired[p["public_key"]] = p["tunnel_ip"]
    except Exception as e:
        print(f"[wg-sync] pairs opérateur ignorés : {e}", flush=True)

    try:
        existing = current_peers()
    except Exception as e:
        print(f"[wg-sync] interface {IFACE} injoignable : {e}", flush=True)
        return 1

    added = 0
    for pub, ip in desired.items():
        subprocess.run(["wg", "set", IFACE, "peer", pub,
                        "allowed-ips", f"{ip}/32",
                        "persistent-keepalive", "25"],
                       capture_output=True, text=True, timeout=30)
        if pub not in existing:
            added += 1

    removed = 0
    for pub in existing - set(desired):
        subprocess.run(["wg", "set", IFACE, "peer", pub, "remove"],
                       capture_output=True, text=True, timeout=30)
        removed += 1

    # Persiste la config courante (survie au redémarrage)
    subprocess.run(["wg-quick", "save", IFACE],
                   capture_output=True, text=True, timeout=30)

    # Journal d'accès : remonte l'IP publique (endpoint) de chaque pair actif.
    # Corrèle avec l'IP vue à l'On-Login pour la détection de partage.
    logged = 0
    try:
        import access_log
        for pub, ip in peer_endpoints().items():
            slug = slug_bykey.get(pub)
            if slug:
                access_log.record(slug, ip, router_id="", via="wg-tunnel")
                logged += 1
    except Exception as e:
        print(f"[wg-sync] journal d'accès indisponible : {e}", flush=True)

    # État de connexion : persiste le dernier handshake de chaque pair pour
    # que l'app web puisse allumer les indicateurs routeur/opérateur.
    try:
        wg_store.record_handshakes(peer_handshakes())
    except Exception as e:
        print(f"[wg-sync] persistance des handshakes ignorée : {e}", flush=True)

    print(f"[wg-sync] {len(desired)} pair(s) — {added} ajouté(s), {removed} retiré(s), "
          f"{logged} endpoint(s) journalisé(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
