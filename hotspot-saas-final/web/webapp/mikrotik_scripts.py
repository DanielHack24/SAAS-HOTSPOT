"""
mikrotik_scripts.py — Génération des scripts RouterOS (source unique)

Depuis la v2, l'URL d'API est servie par le hub multi-tenant derrière
nginx : http://<IP_VPS>/t/<slug>/login (port 80, aucun port exotique à
ouvrir côté pare-feu).
"""


def login_url(vps_host: str, slug: str) -> str:
    return f"http://{vps_host}/t/{slug}/login"


def _oneliner_body(url: str, token: str, validity: str) -> str:
    """Corps du script On Login : gestion de l'expiration du ticket
    (commentaire + schedule) puis notification de la vente à l'API."""
    token_param = f"&token={token}" if token else ""
    return (
        f':put (",rem,1500,{validity},1500,,Disable,Disable,"); '
        f':local mode "X"; '
        f':local identity [/system identity get name]; '
        f'{{:local date [ /system clock get date ];'
        f':local year [ :pick $date 7 11 ];'
        f':local month [ :pick $date 0 3 ];'
        f':local comment [ /ip hotspot user get [/ip hotspot user find where name="$user"] comment]; '
        f':local ucode [:pic $comment 0 2]; '
        f':if ($ucode = "vc" or $ucode = "up" or $comment = "") do={{  '
        f'/sys sch add name="$user" disable=no start-date=$date interval="{validity}"; '
        f':delay 2s; '
        f':local exp [ /sys sch get [ /sys sch find where name="$user" ] next-run]; '
        f':local getxp [len $exp]; '
        f':if ($getxp = 15) do={{ :local d [:pic $exp 0 6]; :local t [:pic $exp 7 16]; :local s ("/"); :local exp ("$d$s$year $t"); /ip hotspot user set comment="$exp $mode" [find where name="$user"];}}; '
        f':if ($getxp = 8) do={{ /ip hotspot user set comment="$date $exp $mode" [find where name="$user"];}}; '
        f':if ($getxp > 15) do={{ /ip hotspot user set comment="$exp $mode" [find where name="$user"];}}; '
        f'/sys sch remove [find where name="$user"]}}}}; '
        f':local hsprof [/ip hotspot user get [find name=$user] profile]; '
        f'/tool fetch url=("{url}?username=" . $user . "&profile=" . $hsprof . "&router=" . $identity . "{token_param}") mode=http keep-result=no dst-path="/tmp/hs.tmp"'
    )


def generate_oneliner(tenant: dict, vps_host: str, validity: str = "30d") -> str:
    """Script On Login en une seule ligne, prêt à coller dans le profil
    hotspot. `tenant` : slug, router_token (optionnels : client_name)."""
    url = login_url(vps_host, tenant["slug"])
    return _oneliner_body(url, tenant.get("router_token", ""), validity or "30d")


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
