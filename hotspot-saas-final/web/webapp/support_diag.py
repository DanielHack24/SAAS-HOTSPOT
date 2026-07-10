"""
support_diag.py — Diagnostic en direct d'un compte opérateur.

Lit l'état reel (abonnement en base web + liveness WireGuard via wg_store) et
produit un instantane de sante. Sert au bot ("Mon diagnostic") et aux alertes
proactives. Best-effort : ne leve jamais, degrade proprement si wg_store est
indisponible (dev local).
"""
import time

_FRESH_S = 240   # handshake plus recent que ça = pair en ligne (cf. wg_sync)


def _router_handshake(slug: str):
    """Epoch du dernier handshake du routeur, 0 si jamais, None si indispo."""
    try:
        import wg_store
        peer = wg_store.get_peer(slug)
        if not peer:
            return None
        return wg_store.last_handshake(peer["router_public_key"])
    except Exception:
        return None


def _vpn_devices(slug: str, now: float):
    try:
        import wg_store
        out = []
        for d in wg_store.list_admin_peers(slug):
            hs = wg_store.last_handshake(d["public_key"])
            out.append({"label": d.get("label") or f"Appareil {d['id']}",
                        "online": bool(hs) and (now - hs) < _FRESH_S})
        return out
    except Exception:
        return []


def human_ago(epoch) -> str:
    if not epoch:
        return "aucun contact"
    d = int(time.time() - epoch)
    if d < 0:
        d = 0
    if d < 60:
        return f"il y a {d} s"
    if d < 3600:
        return f"il y a {d // 60} min"
    if d < 86400:
        return f"il y a {d // 3600} h"
    return f"il y a {d // 86400} j"


def account_health(client_id: int):
    """Instantane : {name, routers:[{slug, router_name, plan, days_left,
    end_date, provisioned, router_online, last_seen, vpn_devices}]}."""
    from db import get_db, days_remaining
    conn = get_db()
    client = conn.execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    subs = conn.execute("""SELECT * FROM subscriptions
                           WHERE client_id=? AND active=1
                           ORDER BY end_date DESC""", (client_id,)).fetchall()
    conn.close()
    if not client:
        return None

    now = time.time()
    routers = []
    for s in subs:
        s = dict(s)
        slug = s.get("slug")
        entry = {
            "slug": slug,
            "router_name": s.get("router_name") or "Routeur principal",
            "plan": s.get("plan"),
            "days_left": days_remaining(s["end_date"]) if s.get("end_date") else None,
            "end_date": (s.get("end_date") or "")[:10],
            "provisioned": bool(s.get("provisioned")),
            "router_online": None,
            "last_seen": None,
            "vpn_devices": [],
        }
        if slug:
            hs = _router_handshake(slug)
            if hs is not None:
                entry["last_seen"] = hs
                entry["router_online"] = bool(hs) and (now - hs) < _FRESH_S
            entry["vpn_devices"] = _vpn_devices(slug, now)
        routers.append(entry)
    return {"name": client["full_name"], "routers": routers}


def report_text(health: dict) -> str:
    """Rapport HTML Telegram lisible d'un instantane de sante."""
    if not health or not health.get("routers"):
        return ("<b>Diagnostic</b>\n\nAucun abonnement actif trouve pour votre "
                "compte. Rendez-vous dans votre espace client pour vous abonner "
                "ou configurer votre routeur.")
    lines = ["<b>Diagnostic en direct</b>"]
    for r in health["routers"]:
        lines.append("")
        lines.append(f"<b>{_esc(r['router_name'])}</b>")
        # Abonnement
        dl = r["days_left"]
        if dl is None:
            lines.append("• Abonnement : actif")
        elif dl <= 0:
            lines.append(f"• Abonnement : <b>expire aujourd'hui</b> ({r['end_date']})")
        elif dl <= 3:
            lines.append(f"• Abonnement : actif, <b>expire dans {dl} j</b> ({r['end_date']})")
        else:
            lines.append(f"• Abonnement : actif, {dl} j restants ({r['end_date']})")
        # Routeur
        if not r["provisioned"]:
            lines.append("• Routeur : pas encore configure")
        elif r["router_online"] is None:
            lines.append("• Routeur : etat indisponible")
        elif r["router_online"]:
            lines.append(f"• Routeur : <b>EN LIGNE</b> (contact {human_ago(r['last_seen'])})")
        else:
            lines.append(f"• Routeur : <b>HORS LIGNE</b> (dernier contact {human_ago(r['last_seen'])})")
        # VPN
        if r["vpn_devices"]:
            on = sum(1 for d in r["vpn_devices"] if d["online"])
            lines.append(f"• VPN : {on}/{len(r['vpn_devices'])} appareil(s) connecte(s)")
    lines.append("")
    lines.append("<i>Donnees en temps reel. Si le routeur est hors ligne, verifiez "
                 "son alimentation et sa connexion Internet.</i>")
    return "\n".join(lines)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
