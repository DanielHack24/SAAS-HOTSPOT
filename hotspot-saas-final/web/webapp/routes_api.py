"""
routes_api.py — API JSON du dashboard + cron d'expiration
"""
import json
from datetime import datetime

from flask import request, session, jsonify

import config
import services
from webapp_core import app, PROVISIONER_OK
import webapp_core as core
from db import get_db, get_active_sub, days_remaining
from security import login_required


@app.route("/api/stats/today")
@login_required
def api_stats_today():
    """Stats du jour du tenant (via le hub)."""
    sub = get_active_sub(session["client_id"])
    empty = {"total_sales": 0, "total_revenue": 0, "sellers": []}

    if not sub or not sub.get("provisioned") or not sub.get("slug"):
        return jsonify({"error": "non déployé", **empty})
    if not PROVISIONER_OK:
        return jsonify({"error": "moteur indisponible", **empty})
    try:
        return jsonify(core.get_tenant_stats(sub["slug"]))
    except Exception as e:
        return jsonify({"error": str(e), **empty})


@app.route("/api/stats/week")
@login_required
def api_stats_week():
    """Stats des 7 derniers jours (via le hub)."""
    sub = get_active_sub(session["client_id"])
    if not sub or not sub.get("provisioned") or not sub.get("slug") or not PROVISIONER_OK:
        return jsonify({"days": []})
    try:
        return jsonify(core.get_tenant_week_stats(sub["slug"]))
    except Exception as e:
        return jsonify({"days": [], "error": str(e)})


@app.route("/api/last-activity")
@login_required
def api_last_activity():
    """Horodatage de la dernière vente reçue pour ce client."""
    sub = get_active_sub(session["client_id"])
    if not sub or not sub.get("provisioned") or not sub.get("slug") or not PROVISIONER_OK:
        return jsonify({"last_seen": None, "seller": None})
    try:
        data = core.get_last_activity(sub["slug"])
        return jsonify({"last_seen": data.get("last_seen"), "seller": data.get("seller")})
    except Exception:
        return jsonify({"last_seen": None, "seller": None})


@app.route("/api/profiles")
@login_required
def api_profiles():
    """Profils hotspot configurés par le client avec leurs prix."""
    sub = get_active_sub(session["client_id"])
    if not sub or not sub.get("provisioned"):
        return jsonify({"profiles": [], "error": "Système non déployé"})

    profiles = []
    try:
        raw = json.loads(sub["prices"]) if sub.get("prices") else {}
        for name, v in raw.items():
            price = v.get("price", 0) if isinstance(v, dict) else int(v)
            profiles.append({"name": name, "price": price})
    except Exception:
        profiles = []

    return jsonify({"profiles": profiles,
                    "router_name": sub.get("router_name") or "Routeur principal"})


# ═══════════════════════════════════════════════
# CRON — Vérification des expirations
# ═══════════════════════════════════════════════

@app.route("/cron/check_expiry")
def cron_check_expiry():
    key = request.args.get("key", "")
    # CRON_KEY obligatoire : pas de valeur par défaut acceptée
    if not config.CRON_KEY or key != config.CRON_KEY:
        return jsonify({"error": "unauthorized"}), 403

    conn = get_db()
    subs = conn.execute("""
        SELECT s.*, c.full_name, c.email
        FROM subscriptions s JOIN clients c ON s.client_id=c.id
        WHERE s.active=1
    """).fetchall()

    expired  = []
    expiring = []

    for row in subs:
        sub = dict(row)
        d   = days_remaining(sub["end_date"])

        if d <= 0:
            conn.execute("UPDATE subscriptions SET active=0 WHERE id=?", (sub["id"],))
            if sub["slug"] and PROVISIONER_OK:
                try:
                    core.stop_tenant(sub["slug"])
                except Exception:
                    pass
            if sub["bot_token"] and sub["chat_id"]:
                services.send_telegram_notify(sub["bot_token"], sub["chat_id"], (
                    "⚠️ <b>Votre abonnement HotspotPro a expiré.</b>\n\n"
                    "Votre système de comptabilité a été suspendu.\n"
                    "Renouvelez votre abonnement sur le site pour le réactiver.\n\n"
                    "👉 Connectez-vous à votre espace client."
                ))
            expired.append({"id": sub["id"], "name": sub["full_name"]})

        elif d <= 3:
            if sub["bot_token"] and sub["chat_id"]:
                services.send_telegram_notify(sub["bot_token"], sub["chat_id"], (
                    f"⏰ <b>Votre abonnement expire dans {d} jour(s).</b>\n\n"
                    f"Renouvelez maintenant pour ne pas interrompre votre système.\n"
                    f"👉 Connectez-vous à votre espace HotspotPro."
                ))
            expiring.append({"id": sub["id"], "name": sub["full_name"], "days": d})

    conn.commit()
    conn.close()

    return jsonify({
        "expired":    expired,
        "expiring":   expiring,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
