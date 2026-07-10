"""
provisioner.py — Provisioning des tenants HotspotPro (v2)

Depuis la v2, tous les tenants sont servis par un seul processus
(tenant_hub.py, service systemd `hotspot-tenant-hub`). Provisionner
un client ne crée plus de service systemd ni de processus dédié :
c'est un simple enregistrement en base centrale suivi d'un POST
/reload au hub.

Conséquences :
  - plus besoin de tourner en root
  - activation/suspension instantanées (flag `active` en base)
  - changement de tarifs pris en compte à chaud
"""
import os, json
import urllib.request

from tenant_db import (update_tenant, set_tenant_active, delete_tenant,
                       get_tenant, get_last_activity as _get_last_activity)

SAAS_DIR    = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")
TENANTS_DIR = os.path.join(SAAS_DIR, "tenants")
HUB_PORT    = int(os.environ.get("TENANT_HUB_PORT", "8010"))
HUB_KEY     = os.environ.get("HUB_KEY", "")
HUB_URL     = f"http://127.0.0.1:{HUB_PORT}"


# ═══════════════════════════════════════════════
# COMMUNICATION AVEC LE HUB
# ═══════════════════════════════════════════════

def _hub_request(path: str, method: str = "GET", timeout: int = 5) -> dict:
    # Clé en header : une query string finirait dans les logs d'accès
    headers = {"X-Hub-Key": HUB_KEY} if HUB_KEY else {}
    req = urllib.request.Request(f"{HUB_URL}{path}", method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def reload_hub() -> bool:
    """Demande au hub de recharger la liste des tenants.
    Non bloquant en cas d'échec : le hub se resynchronise seul
    toutes les 60 secondes."""
    try:
        _hub_request("/reload", method="POST")
        return True
    except Exception as e:
        print(f"[PROVISIONER] Hub injoignable ({e}) — resync auto sous 60s", flush=True)
        return False


# ═══════════════════════════════════════════════
# CYCLE DE VIE
# ═══════════════════════════════════════════════

def provision_tenant(tenant: dict, vps_ip: str = "", src_dir: str = None,
                     prices: dict = None):
    """Active un tenant : met à jour sa config en base centrale puis
    notifie le hub. `tenant` doit contenir au minimum `slug`."""
    slug = tenant["slug"]
    os.makedirs(os.path.join(TENANTS_DIR, slug), exist_ok=True)

    updates = {"active": 1}
    for key in ("bot_token", "chat_id", "mikrotik_ip", "router_name", "router_token"):
        if tenant.get(key) is not None:
            updates[key] = tenant[key]
    if prices:
        updates["prices"] = prices
    update_tenant(slug, **updates)
    reload_hub()


def stop_tenant(slug: str):
    """Suspend un tenant (le hub cesse d'accepter ses ventes)."""
    set_tenant_active(slug, False)
    reload_hub()


def start_tenant(slug: str):
    """Réactive un tenant."""
    set_tenant_active(slug, True)
    reload_hub()


def restart_tenant(slug: str):
    """Recharge la config du tenant (équivalent d'un redémarrage)."""
    reload_hub()


def remove_tenant_files(slug: str):
    """Retire un tenant : suppression en base centrale, données de vente
    archivées dans tenants/_archived/ (jamais détruites)."""
    delete_tenant(slug)
    tenant_dir = os.path.join(TENANTS_DIR, slug)
    if os.path.isdir(tenant_dir):
        import shutil, time
        archive_root = os.path.join(TENANTS_DIR, "_archived")
        os.makedirs(archive_root, exist_ok=True)
        dest = os.path.join(archive_root, f"{slug}-{int(time.time())}")
        shutil.move(tenant_dir, dest)
    reload_hub()


def get_service_status(slug: str) -> dict:
    """Retourne le statut du tenant vu par le hub."""
    try:
        data   = _hub_request(f"/t/{slug}/health")
        status = "active" if data.get("status") == "ok" else "stopped"
    except Exception:
        status = "stopped"
    return {"api": status, "bot": status}


def get_hub_status() -> dict:
    """Statut global du hub (nombre de tenants/bots chargés)."""
    try:
        return _hub_request("/health")
    except Exception as e:
        return {"status": "down", "error": str(e)}


def get_tenant_stats(slug: str) -> dict:
    """Stats du jour d'un tenant, via le hub."""
    return _hub_request(f"/t/{slug}/stats")


def get_tenant_week_stats(slug: str) -> dict:
    """Stats des 7 derniers jours d'un tenant, via le hub."""
    return _hub_request(f"/t/{slug}/stats/week")


def get_last_activity(slug: str) -> dict:
    """Dernière vente enregistrée pour un tenant."""
    return _get_last_activity(slug)
