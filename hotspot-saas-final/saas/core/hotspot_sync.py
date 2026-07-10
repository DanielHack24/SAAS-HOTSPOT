"""
hotspot_sync.py — Pousse les tickets dans le Hotspot local du routeur
(/ip hotspot user), à travers le tunnel WireGuard, exactement comme mikhmon.

Les clients gèrent leurs tickets en utilisateurs Hotspot locaux (et non dans
User Manager) : un ticket = un `/ip hotspot user` rattaché à un profil hotspot
(1heure, 24heure, 3jours…) avec une durée `limit-uptime`.

Flux validé sur RouterOS 7.21 :
  1. Profil : /ip/hotspot/user/profile (créé s'il manque, sinon réutilisé tel
     quel — on ne touche jamais aux profils existants du client).
  2. Ticket : PUT /ip/hotspot/user {name, password, profile, limit-uptime}.

Idempotent (« already have user »/« already exists » ignoré), tolérant aux
pannes : routeur éteint => les tickets restent « à pousser » et sont repris.
"""
import tickets
import wg_store
from routeros import (RouterOSRest, RouterUnreachable, RouterOSError,
                      RouterOSAuthError, is_already_exists)

HS_USER    = "/ip/hotspot/user"
HS_PROFILE = "/ip/hotspot/user/profile"
COMMENT    = "HotspotPro"


def _ensure_profiles(client, names: set):
    """Crée les profils hotspot manquants (nom seul, réglages par défaut).
    Les profils déjà présents (créés par l'opérateur/mikhmon) sont laissés
    intacts."""
    try:
        existing = {p.get("name") for p in client.get(HS_PROFILE)}
    except RouterOSError:
        existing = set()
    for name in names:
        if not name or name in existing:
            continue
        try:
            client.create(HS_PROFILE, {"name": name})
        except RouterOSError as e:
            if not is_already_exists(e):
                raise


CHUNK = 300                # tickets par script (≈30 Ko de source, testé OK)


def _esc(v) -> str:
    """Échappe une valeur pour une chaîne de script RouterOS."""
    return str(v if v is not None else "").replace("\\", "\\\\").replace('"', '\\"')


def _add_line(t: dict) -> str:
    """Ligne de script créant un utilisateur hotspot, idempotente : le doublon
    (« already have user ») est avalé côté routeur par `on-error={}`."""
    parts = [
        f'name="{_esc(t["username"])}"',
        f'password="{_esc(t.get("password", ""))}"',
        f'profile="{_esc(t["profile"])}"',
    ]
    validity = (t.get("validity") or "").strip()
    if validity:
        parts.append(f'limit-uptime="{_esc(validity)}"')
    parts.append(f'comment="{COMMENT}"')
    return ":do {/ip hotspot user add " + " ".join(parts) + "} on-error={}"


def push_pending(slug: str, client_factory=RouterOSRest) -> dict:
    """Pousse tous les tickets en attente d'un tenant dans le Hotspot local.

    status : 'ok' | 'no_tunnel' | 'offline' | 'auth' | 'partial'
    """
    dbp  = tickets.sales_db_path(slug)
    peer = wg_store.get_peer(slug)
    if not peer:
        return {"status": "no_tunnel", "pushed": 0,
                "pending": len(tickets.pending_push(dbp))}

    pend = tickets.pending_push(dbp)
    if not pend:
        return {"status": "ok", "pushed": 0, "pending": 0}

    client = client_factory(peer["tunnel_ip"], peer["api_user"], peer["api_pass"])
    try:
        # Connexion + authentification (une fois)
        try:
            client.ping()
        except RouterUnreachable as e:
            return {"status": "offline", "pushed": 0, "pending": len(pend), "error": str(e)}
        except RouterOSAuthError as e:
            return {"status": "auth", "pushed": 0, "pending": len(pend), "error": str(e)}

        # Profils nécessaires
        needed = {t["profile"] for t in pend if t.get("profile")}
        try:
            _ensure_profiles(client, needed)
        except RouterUnreachable as e:
            return {"status": "offline", "pushed": 0, "pending": len(pend), "error": str(e)}
        except RouterOSAuthError as e:
            return {"status": "auth", "pushed": 0, "pending": len(pend), "error": str(e)}
        except RouterOSError:
            pass   # un profil problématique ne doit pas bloquer tout le lot

        # Création EN MASSE par script exécuté localement sur le routeur : un
        # seul envoi par paquet de CHUNK tickets (≈25× plus rapide que 1 appel
        # REST par ticket). Chaque paquet réussi est marqué « poussé » aussitôt
        # -> la progression persiste même si le thread est interrompu (le cron
        # reprend le reste ; les doublons sont avalés côté routeur).
        done, failed = 0, 0
        for i in range(0, len(pend), CHUNK):
            batch = pend[i:i + CHUNK]
            src = ";".join(_add_line(t) for t in batch)
            try:
                client.run_script(src)
            except RouterUnreachable:
                break                      # routeur parti : le reste attend
            except RouterOSAuthError as e:
                return {"status": "auth", "pushed": done,
                        "pending": len(pend) - done, "error": str(e)}
            except RouterOSError:
                failed += len(batch)       # paquet en échec : on continue
                continue
            tickets.mark_pushed(dbp, [t["username"] for t in batch])
            done += len(batch)

        pending = len(pend) - done
        status = "ok" if pending == 0 and failed == 0 else "partial" if done else "offline"
        return {"status": status, "pushed": done, "pending": pending, "failed": failed}
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            closer()
