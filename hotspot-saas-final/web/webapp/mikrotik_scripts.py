"""
mikrotik_scripts.py — Génération des scripts RouterOS (source unique)

Depuis la v2, l'URL d'API est servie par le hub multi-tenant derrière
nginx : http(s)://<HOTE_VPS>/t/<slug>/login (port 80/443, aucun port
exotique à ouvrir côté pare-feu).

Compatibilité dates : RouterOS 7.10+ renvoie les dates au format ISO
(2026-07-06) alors que les versions antérieures utilisent jan/06/2026.
Les scripts générés détectent le format à l'exécution et gèrent les deux.
"""
import config


def _scheme() -> str:
    return "https" if config.mikrotik_https() else "http"


def login_url(vps_host: str, slug: str) -> str:
    return f"{_scheme()}://{vps_host}/t/{slug}/login"


def health_url(vps_host: str, slug: str) -> str:
    return f"{_scheme()}://{vps_host}/t/{slug}/health"


def _oneliner_body(url: str, token: str, validity: str = "") -> str:
    """Corps du script On Login : signale simplement la vente à l'API.

    L'expiration est gérée nativement par le User Manager (validité du
    profil, démarrage à la 1re connexion) et la création des utilisateurs
    se fait automatiquement via le tunnel — le script On Login ne sert donc
    plus qu'à envoyer la notification de vente en temps réel. Le hub retrouve
    le vendeur et le profil du ticket dans sa base à partir de l'identifiant."""
    token_param = f"&token={token}" if token else ""
    fetch_mode  = "https" if url.startswith("https://") else "http"
    return (
        f':local identity [/system identity get name]; '
        f':local serial ""; :do {{:set serial [/system routerboard get serial-number]}} on-error={{}}; '
        f'/tool fetch url=("{url}?username=" . $user . "&router=" . $identity . '
        f'"&serial=" . $serial . "{token_param}") '
        f'mode={fetch_mode} keep-result=no dst-path="/tmp/hs.tmp"'
    )


def generate_oneliner(tenant: dict, vps_host: str, validity: str = "30d") -> str:
    """Script On Login en une seule ligne, prêt à coller dans le profil
    hotspot. `tenant` : slug, router_token (optionnels : client_name)."""
    url = login_url(vps_host, tenant["slug"])
    return _oneliner_body(url, tenant.get("router_token", ""), validity or "30d")


# ═══════════════════════════════════════════════
# SCRIPTS ON LOGIN PAR PROFIL (mikhmon + notification fusionnés)
# ═══════════════════════════════════════════════
# Modèle mikhmon standard : expiration gérée par commentaire + planificateur,
# avec le prix et la validité encodés dans la 1re ligne (mikhmon les relit).
# Paramétré par __PRICE__ et __VALIDITY__ ; la notification de vente HotspotPro
# est fusionnée à la fin (sur une nouvelle ligne, sans :local en conflit —
# le nom du routeur est lu en ligne).
_MIKHMON_ONLOGIN = (
    ':put (",rem,__PRICE__,__VALIDITY__,__PRICE__,,Disable,"); '
    '{:local comment [ /ip hotspot user get [/ip hotspot user find where name="$user"] comment]; '
    ':local ucode [:pic $comment 0 2]; '
    ':if ($ucode = "vc" or $ucode = "up" or $comment = "") do={ '
    ':local date [ /system clock get date ];:local year [ :pick $date 7 11 ];'
    ':local month [ :pick $date 0 3 ]; '
    '/sys sch add name="$user" disable=no start-date=$date interval="__VALIDITY__"; :delay 5s; '
    ':local exp [ /sys sch get [ /sys sch find where name="$user" ] next-run]; '
    ':local getxp [len $exp]; '
    ':if ($getxp = 15) do={ :local d [:pic $exp 0 6]; :local t [:pic $exp 7 16]; '
    ':local s ("/"); :local exp ("$d$s$year $t"); '
    '/ip hotspot user set comment="$exp" [find where name="$user"];}; '
    ':if ($getxp = 8) do={ /ip hotspot user set comment="$date $exp" [find where name="$user"];}; '
    ':if ($getxp > 15) do={ /ip hotspot user set comment="$exp" [find where name="$user"];};'
    ':delay 5s; /sys sch remove [find where name="$user"]}}'
)


def _notify_fetch(url: str, token: str) -> str:
    """Ligne(s) /tool fetch de notification à ajouter à un script existant.
    Le hostname est lu en ligne ; le numéro de série (empreinte matérielle
    stable pour la détection de partage) est résolu à part, avec garde pour les
    modèles sans routerboard (CHR/x86)."""
    token_param = f"&token={token}" if token else ""
    fetch_mode  = "https" if url.startswith("https://") else "http"
    return (
        f':local serial ""; :do {{:set serial [/system routerboard get serial-number]}} on-error={{}}; '
        f'/tool fetch url=("{url}?username=" . $user . '
        f'"&router=" . [/system identity get name] . "&serial=" . $serial . "{token_param}") '
        f'mode={fetch_mode} keep-result=no dst-path="/tmp/hs.tmp"'
    )


def mikhmon_onlogin(price, validity: str, url: str, token: str) -> str:
    """Script On Login complet pour UN profil : expiration mikhmon (prix +
    validité du profil) + notification de vente HotspotPro. À coller dans le
    champ On Login du User Profile correspondant.

    Sans validité, seule la notification est renvoyée (l'expiration mikhmon a
    besoin d'un intervalle)."""
    validity = (validity or "").strip()
    try:
        price_s = str(int(price))
    except (TypeError, ValueError):
        price_s = str(price or 0)
    notify = _notify_fetch(url, token)
    if not validity:
        return notify
    body = _MIKHMON_ONLOGIN.replace("__PRICE__", price_s).replace("__VALIDITY__", validity)
    return body + "\n" + notify


def build_profile_scripts(profiles: dict, vps_host: str, slug: str, token: str) -> dict:
    """`profiles` = {nom: {validity, price}} -> {nom: script On Login complet}.
    Un script par profil, prêt à coller dans le On Login du User Profile."""
    url = login_url(vps_host, slug)
    out = {}
    for name, p in (profiles or {}).items():
        if isinstance(p, dict):
            price, validity = p.get("price", 0), p.get("validity", "")
        else:
            price, validity = p, ""
        out[name] = mikhmon_onlogin(price, validity, url, token)
    return out


def generate_full_script(tenant: dict, vps_host: str, validity: str = "30d") -> str:
    """Version commentée multi-lignes pour System > Scripts."""
    url  = login_url(vps_host, tenant["slug"])
    name = tenant.get("client_name") or tenant.get("name", "Client")
    header = (
        f"# ══════════════════════════════════════════\n"
        f"# Script HotspotPro — {name}\n"
        f"# URL API : {url}\n"
        f"# Coller dans : System > Scripts > Add\n"
        f"# Puis dans chaque profil : IP > Hotspot > Server Profiles > Scripting > On Login\n"
        f"# ══════════════════════════════════════════\n\n"
    )
    return header + _oneliner_body(url, tenant.get("router_token", ""), validity or "30d") + "\n"
