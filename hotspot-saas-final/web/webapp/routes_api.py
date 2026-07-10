"""
routes_api.py — API JSON du dashboard + cron d'expiration
"""
import json
import secrets
from datetime import datetime

from flask import request, session, jsonify

import config
import services
from webapp_core import app, PROVISIONER_OK
import webapp_core as core
from db import get_db, get_active_sub, days_remaining, is_expired
from security import login_required


def _cron_key_ok() -> bool:
    """Clé cron acceptée UNIQUEMENT en en-tête X-Cron-Key (jamais en query
    string : elle finirait dans les logs nginx). Comparaison a temps constant.
    Sans CRON_KEY configuree, tout est refuse."""
    if not config.CRON_KEY:
        return False
    sent = request.headers.get("X-Cron-Key", "")
    return bool(sent) and secrets.compare_digest(sent, config.CRON_KEY)


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
# CRON — Rattrapage de synchronisation des tickets
# ═══════════════════════════════════════════════

@app.route("/cron/sync_tickets")
def cron_sync_tickets():
    """Repousse vers les routeurs les tickets restés en attente (routeur
    éteint au moment de la génération). À appeler périodiquement."""
    if not _cron_key_ok():
        return jsonify({"error": "unauthorized"}), 403

    try:
        import hotspot_sync
    except Exception:
        return jsonify({"error": "moteur indisponible"}), 200

    conn = get_db()
    subs = conn.execute("""
        SELECT DISTINCT slug FROM subscriptions
        WHERE active=1 AND provisioned=1 AND slug IS NOT NULL AND slug<>''
    """).fetchall()
    conn.close()

    results = []
    for row in subs:
        slug = row["slug"]
        try:
            res = hotspot_sync.push_pending(slug)
            if res.get("pushed") or res.get("pending"):
                results.append({"slug": slug, **res})
        except Exception as e:
            results.append({"slug": slug, "status": "error", "error": str(e)})

    return jsonify({"synced": results,
                    "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})


# ═══════════════════════════════════════════════
# CRON — Vérification des expirations
# ═══════════════════════════════════════════════

@app.route("/cron/check_expiry")
def cron_check_expiry():
    # Clé exigée en en-tête X-Cron-Key uniquement (une query string ?key=
    # finirait dans les logs nginx). CRON_KEY obligatoire : pas de défaut.
    if not _cron_key_ok():
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

        if is_expired(sub["end_date"]):
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

        elif (d := days_remaining(sub["end_date"])) <= 3:
            if sub["bot_token"] and sub["chat_id"]:
                services.send_telegram_notify(sub["bot_token"], sub["chat_id"], (
                    f"⏰ <b>Votre abonnement expire dans {d} jour(s).</b>\n\n"
                    f"Renouvelez maintenant pour ne pas interrompre votre système.\n"
                    f"👉 Connectez-vous à votre espace HotspotPro."
                ))
            expiring.append({"id": sub["id"], "name": sub["full_name"], "days": d})

    # Routeurs supplémentaires : payés pour une durée fixe, ils doivent
    # être suspendus à leur propre échéance (indépendamment de
    # l'abonnement principal).
    devices = conn.execute("""
        SELECT md.*, s.bot_token, s.chat_id
        FROM mikrotik_devices md
        LEFT JOIN subscriptions s ON md.subscription_id = s.id
        WHERE md.provisioned=1 AND md.active=1 AND md.end_date IS NOT NULL
    """).fetchall()

    expired_devices = []
    for row in devices:
        device = dict(row)
        if not is_expired(device["end_date"]):
            continue
        conn.execute("UPDATE mikrotik_devices SET active=0 WHERE id=?", (device["id"],))
        if device["slug"] and PROVISIONER_OK:
            try:
                core.stop_tenant(device["slug"])
            except Exception:
                pass
        if device.get("bot_token") and device.get("chat_id"):
            services.send_telegram_notify(device["bot_token"], device["chat_id"], (
                f"⚠️ <b>Le forfait du routeur « {device['label']} » a expiré.</b>\n\n"
                "Ce routeur a été suspendu. Renouvelez-le depuis votre espace client."
            ))
        expired_devices.append({"id": device["id"], "label": device["label"]})

    conn.commit()
    conn.close()

    return jsonify({
        "expired":         expired,
        "expiring":        expiring,
        "expired_devices": expired_devices,
        "checked_at":      datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
