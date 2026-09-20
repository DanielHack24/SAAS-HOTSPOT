"""
routes_client.py — Espace client : dashboard, configuration, scripts
MikroTik, compte, routeurs supplémentaires
"""
import re, json, secrets, ipaddress

from flask import render_template, request, redirect, url_for, session, flash, jsonify, Response

import config
from datetime import datetime
from legal_content import TERMS_VERSION
import fedapay
import mikrotik_scripts as mks
import services
from webapp_core import app, PROVISIONER_OK
import webapp_core as core
from db import (get_db, get_client, get_active_sub, get_client_devices,
                days_remaining)
from security import login_required, hash_password, verify_password


def current_client():
    return get_client(session["client_id"])


# Couleurs d'avatar proposées (validées côté serveur)
AVATAR_COLORS = ["#12263A", "#C2410C", "#15803D", "#1D4ED8",
                 "#7C3AED", "#B45309", "#0E7490", "#BE185D"]


def is_valid_ip(value: str) -> bool:
    """Validation stricte d'une IPv4 (la regex \\d{1,3} acceptait 999.999.999.999)."""
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


# ═══════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════

@app.route("/dashboard")
@login_required
def dashboard():
    client = current_client()
    sub    = get_active_sub(client["id"])
    days   = days_remaining(sub["end_date"]) if sub else 0

    svc_status = None
    if sub and sub.get("provisioned") and sub.get("slug") and PROVISIONER_OK:
        try:
            svc_status = core.get_service_status(sub["slug"])
        except Exception:
            svc_status = {"api": "unknown", "bot": "unknown"}

    devices = get_client_devices(client["id"], sub["id"]) if sub else []

    conn = get_db()
    mk_payments_pending = conn.execute("""
        SELECT mp.*, md.label, md.ip FROM mikrotik_payments mp
        LEFT JOIN mikrotik_devices md ON mp.device_id = md.id
        WHERE mp.client_id=? AND mp.status='pending'
    """, (client["id"],)).fetchall()
    conn.close()

    return render_template("dashboard.html",
        client=client, sub=sub, days=days,
        plans=config.PLANS, svc_status=svc_status,
        devices=devices, mk_payments_pending=mk_payments_pending)


# ═══════════════════════════════════════════════
# CONFIGURATION / PROVISIONING
# ═══════════════════════════════════════════════

DEFAULT_PROFILES = {
    "1h":  {"validity": "1h",  "price": 100},
    "12h": {"validity": "12h", "price": 500},
    "24h": {"validity": "24h", "price": 1000},
    "3j":  {"validity": "3d",  "price": 2500},
    "7j":  {"validity": "7d",  "price": 5000},
    "30j": {"validity": "30d", "price": 15000},
}


@app.route("/configure", methods=["GET", "POST"])
@login_required
def configure():
    client = current_client()
    sub    = get_active_sub(client["id"])

    if not sub:
        flash("Aucun abonnement actif. Veuillez d'abord souscrire à un plan.", "error")
        return redirect(url_for("dashboard"))

    # Si déjà provisionné et pas de demande de reconfiguration, aller au script
    if sub.get("provisioned") and not request.args.get("reconfigure"):
        return redirect(url_for("mikrotik_script"))

    if request.method == "POST":
        bot_token   = request.form.get("bot_token", "").strip()
        chat_id     = request.form.get("chat_id", "").strip()
        mikrotik_ip = request.form.get("mikrotik_ip", "").strip()
        router_name = request.form.get("router_name", "").strip() or "Routeur principal"

        try:
            raw_profiles = json.loads(request.form.get("profiles_json", "{}"))
            prices = {}
            for k, v in raw_profiles.items():
                if not k.strip():
                    continue
                if isinstance(v, dict):
                    prices[k] = {"validity": str(v.get("validity", "")).strip(),
                                 "price": int(v.get("price", 0))}
                else:
                    prices[k] = {"validity": "", "price": int(v)}
        except Exception:
            prices = {}
        if not prices:
            prices = dict(DEFAULT_PROFILES)
        prices_json = json.dumps(prices)

        errors = []
        if not re.match(r"^\d+:[A-Za-z0-9_\-]{35,}$", bot_token):
            errors.append("Format du token Telegram invalide. Ex: 123456789:AAGQ...")
        if not re.match(r"^-?\d+$", chat_id):
            errors.append("Chat ID invalide (nombre entier requis).")
        if mikrotik_ip and not is_valid_ip(mikrotik_ip):
            errors.append("Adresse IP MikroTik invalide (laisser vide si derrière NAT/pare-feu).")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("configure.html", sub=sub)

        slug = sub.get("slug") or services.make_slug(client["full_name"], str(sub["id"]))
        router_token = sub.get("router_token") or secrets.token_urlsafe(24)

        conn = get_db()
        conn.execute("""
            UPDATE subscriptions
            SET bot_token=?, chat_id=?, mikrotik_ip=?, slug=?, prices=?,
                router_name=?, router_token=?
            WHERE id=?
        """, (bot_token, chat_id, mikrotik_ip, slug, prices_json,
              router_name, router_token, sub["id"]))
        conn.commit()
        conn.close()

        if PROVISIONER_OK:
            try:
                if not core.slug_exists(slug):
                    tenant = core.add_tenant(client["full_name"], slug,
                                             bot_token, chat_id, mikrotik_ip,
                                             router_name=router_name,
                                             router_token=router_token,
                                             prices=prices)
                else:
                    tenant = core.get_tenant(slug)
                    tenant.update({"bot_token": bot_token, "chat_id": chat_id,
                                   "mikrotik_ip": mikrotik_ip,
                                   "router_name": router_name,
                                   "router_token": router_token})
                core.provision_tenant(tenant, config.get_vps_ip(), prices=prices)
                _mark_provisioned(sub["id"])
                flash("Votre système a été déployé avec succès.", "success")
            except Exception as e:
                flash(f"Erreur de déploiement : {e}", "error")
                return render_template("configure.html", sub=sub)
        else:
            _mark_provisioned(sub["id"])
            flash("Configuration enregistrée.", "success")

        return redirect(url_for("mikrotik_script"))

    return render_template("configure.html", sub=sub)


def _mark_provisioned(sub_id: int):
    conn = get_db()
    conn.execute("UPDATE subscriptions SET provisioned=1 WHERE id=?", (sub_id,))
    conn.commit()
    conn.close()


def _reserve_slug(client: dict, sub: dict):
    """Réserve slug + router_token sur l'abonnement SANS provisionner le hub.
    Permet de monter le tunnel WireGuard avant l'étape des profils (pour lire
    les profils réels du routeur). Idempotent."""
    slug  = sub.get("slug")
    token = sub.get("router_token")
    if slug and token:
        return slug, token
    slug  = slug  or services.make_slug(client["full_name"], str(sub["id"]))
    token = token or secrets.token_urlsafe(24)
    conn = get_db()
    conn.execute("UPDATE subscriptions SET slug=?, router_token=? WHERE id=?",
                 (slug, token, sub["id"]))
    conn.commit()
    conn.close()
    return slug, token


def _tunnel_client(slug: str):
    """Retourne (RouterOSRest prêt, module routeros) pour un tenant, ou
    (None, reason) si indisponible. Le tunnel WireGuard doit être provisionné."""
    try:
        import wg_store
        import routeros as ros
    except Exception:
        return None, "unavailable"
    peer = wg_store.get_peer(slug)
    if not peer:
        return None, "no_peer"
    return ros.RouterOSRest(peer["tunnel_ip"], peer["api_user"], peer["api_pass"]), ros


@app.route("/configure/tunnel", methods=["POST"])
@login_required
def configure_tunnel():
    """Prépare le tunnel WireGuard pendant l'onboarding : réserve le slug et
    renvoie le bloc de configuration à coller sur le routeur."""
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub:
        return jsonify({"ready": False, "reason": "no_sub"}), 400
    slug, _ = _reserve_slug(client, sub)
    try:
        import wg_store
        if wg_store.get_server() is None:
            return jsonify({"ready": False, "reason": "wg_not_ready"})
        block = wg_store.provision_peer(slug)["block"]
        return jsonify({"ready": True, "block": block})
    except Exception as e:
        return jsonify({"ready": False, "reason": "unavailable", "error": str(e)})


@app.route("/configure/tunnel/status")
@login_required
def configure_tunnel_status():
    """Ping du routeur à travers le tunnel : le client a-t-il collé le bloc ?"""
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("slug"):
        return jsonify({"online": False, "reason": "no_peer"})
    rest, ros = _tunnel_client(sub["slug"])
    if rest is None:
        return jsonify({"online": False, "reason": ros})
    try:
        rest.ping()
        return jsonify({"online": True})
    except ros.RouterUnreachable:
        return jsonify({"online": False, "reason": "offline"})
    except ros.RouterOSAuthError:
        return jsonify({"online": False, "reason": "auth"})
    except Exception as e:
        return jsonify({"online": False, "reason": "error", "error": str(e)})


@app.route("/configure/router-profiles")
@login_required
def configure_router_profiles():
    """Lit les profils réels du User Manager du routeur, pour pré-remplir
    l'étape des profils avec des noms garantis exacts."""
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("slug"):
        return jsonify({"ok": False, "reason": "no_peer"})
    rest, ros = _tunnel_client(sub["slug"])
    if rest is None:
        return jsonify({"ok": False, "reason": ros})
    try:
        raw = rest.get("/ip/hotspot/user/profile")
    except ros.RouterUnreachable:
        return jsonify({"ok": False, "reason": "offline"})
    except ros.RouterOSAuthError:
        return jsonify({"ok": False, "reason": "auth"})
    except Exception as e:
        return jsonify({"ok": False, "reason": "error", "error": str(e)})
    profiles = []
    for p in (raw or []):
        name = p.get("name")
        # On exclut « default » (profil système d'essai, jamais vendu).
        if name and name != "default":
            profiles.append({"name": name, "validity": ""})
    return jsonify({"ok": True, "profiles": profiles})


# ═══════════════════════════════════════════════
# SCRIPTS MIKROTIK
# ═══════════════════════════════════════════════

def _profiles_from_sub(sub: dict) -> dict:
    """Normalise sub['prices'] : {name: {validity, price}}."""
    profiles = {}
    try:
        raw = json.loads(sub["prices"]) if sub.get("prices") else {}
        for k, v in raw.items():
            if isinstance(v, dict):
                profiles[k] = v
            else:
                profiles[k] = {"validity": "", "price": int(v)}
    except Exception:
        profiles = {}
    return profiles


@app.route("/aide/routeros-7")
@login_required
def routeros_update_guide():
    """Tutoriel : mettre à jour un MikroTik vers RouterOS 7 (prérequis
    à la synchronisation automatique des tickets)."""
    return render_template("tuto_routeros.html", client=current_client())


@app.route("/mikrotik/connexion")
@login_required
def mikrotik_connect():
    """Affiche le bloc WireGuard à coller UNE FOIS pour connecter le
    routeur du client à la plateforme (envoi automatique des tickets)."""
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("provisioned") or not sub.get("slug"):
        flash("Configurez et déployez d'abord votre système.", "info")
        return redirect(url_for("dashboard"))

    block, error = None, None
    try:
        import wg_store
        if wg_store.get_server() is None:
            error = "not_ready"      # serveur WireGuard pas encore installé
        else:
            block = wg_store.provision_peer(sub["slug"])["block"]
    except Exception:
        error = "unavailable"        # moteur SaaS absent (dev local)

    return render_template("connexion.html", client=client, sub=sub,
                           block=block, error=error)


@app.route("/mikrotik")
@login_required
def mikrotik_script():
    client = current_client()
    sub    = get_active_sub(client["id"])

    if not sub:
        flash("Aucun abonnement actif.", "error")
        return redirect(url_for("dashboard"))

    if not sub.get("provisioned") or not sub.get("slug"):
        flash("Configurez d'abord votre système.", "info")
        return redirect(url_for("configure"))

    vps_ip = config.get_vps_ip()
    tenant = {
        "client_name":  client["full_name"],
        "slug":         sub["slug"],
        "router_token": sub.get("router_token", "") or "",
    }
    oneliner   = mks.generate_oneliner(tenant, vps_ip)
    health_url = mks.health_url(vps_ip, sub["slug"])
    profiles   = _profiles_from_sub(sub)

    # Un script On Login complet PAR profil : logique mikhmon (expiration +
    # prix/validité du profil) fusionnée avec la notification de vente. Le
    # client colle chaque script dans le On Login du User Profile correspondant.
    scripts_by_profile = mks.build_profile_scripts(
        profiles, vps_ip, sub["slug"], tenant["router_token"])

    return render_template("mikrotik.html",
                           client=client, sub=sub,
                           oneliner=oneliner,
                           profiles=profiles,
                           scripts_by_profile=scripts_by_profile,
                           limits=config.plan_limits(sub.get("plan")),
                           vps_ip=vps_ip, health_url=health_url)


# ═══════════════════════════════════════════════
# VPN D'ACCÈS AU ROUTEUR (forfaits 8000 / 15000)
# ═══════════════════════════════════════════════

# Fraîcheur d'un handshake WireGuard : au-delà, on considère le pair déconnecté.
# WireGuard renégocie ~toutes les 2 min (keepalive 25 s) ; wg_sync écrit chaque
# minute -> 240 s couvre la latence sans faux « connecté ».
_VPN_FRESH_S = 240


def _vpn_conn_state(slug: str):
    """(routeur_en_ligne, {device_id: en_ligne}, un_appareil_connecté) d'après
    les handshakes persistés par wg_sync. Best-effort."""
    try:
        import time as _t
        import wg_store
        now = _t.time()
        router = wg_store.get_peer(slug)
        r_on = bool(router) and (now - wg_store.last_handshake(router["router_public_key"])) < _VPN_FRESH_S
        devs = {d["id"]: (now - wg_store.last_handshake(d["public_key"])) < _VPN_FRESH_S
                for d in wg_store.list_admin_peers(slug)}
        return r_on, devs, any(devs.values())
    except Exception as e:
        print(f"[VPN] état connexion {e}", flush=True)
        return False, {}, False


@app.route("/mikrotik/vpn")
@login_required
def mikrotik_vpn():
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("provisioned") or not sub.get("slug"):
        flash("Configurez d'abord votre système.", "info")
        return redirect(url_for("dashboard"))

    lim = config.plan_limits(sub.get("plan"))
    if not lim["vpn"]:
        flash("Le VPN d'accès à distance est inclus dans les forfaits 8000 et 15000 FCFA.", "info")
        return redirect(url_for("subscribe") + "?plan=12m")

    status = "unavailable"          # moteur SaaS absent (dev local)
    router_ip = None
    router_online = False
    devices = []
    try:
        import wg_store, vpn_access
        if wg_store.get_server() is None:
            status = "not_ready"    # serveur WireGuard pas encore installé
        else:
            wg_store.provision_admin_peer(sub["slug"])       # au moins un appareil
            status = vpn_access.push_vpn_access(sub["slug"]).get("status", "error")
            router_online, dev_state, _ = _vpn_conn_state(sub["slug"])
            for d in wg_store.list_admin_peers(sub["slug"]):
                cfg = wg_store.get_admin_config(sub["slug"], d["id"])
                if not cfg:
                    continue
                router_ip = cfg["router_ip"]
                devices.append({
                    "id": d["id"],
                    "label": d["label"] or f"Appareil {d['id']}",
                    "tunnel_ip": d["tunnel_ip"],
                    "online": dev_state.get(d["id"], False),
                    "conf": cfg["config"],
                })
    except Exception as e:
        print(f"[VPN] {e}", flush=True)

    return render_template("vpn.html", client=client, sub=sub,
                           status=status, router_ip=router_ip,
                           router_online=router_online, devices=devices,
                           max_devices=lim["max_vpn_devices"])


@app.route("/mikrotik/vpn/add", methods=["POST"])
@login_required
def mikrotik_vpn_add():
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("slug"):
        flash("Aucun abonnement actif.", "error")
        return redirect(url_for("dashboard"))
    lim = config.plan_limits(sub.get("plan"))
    if not lim["vpn"]:
        return redirect(url_for("subscribe"))
    try:
        import wg_store, vpn_access
        if wg_store.count_admin_peers(sub["slug"]) >= lim["max_vpn_devices"]:
            flash(f"Votre forfait autorise {lim['max_vpn_devices']} appareil(s) VPN maximum.", "error")
            return redirect(url_for("mikrotik_vpn"))
        label = (request.form.get("label") or "").strip()[:60]
        wg_store.add_admin_peer(sub["slug"], label or "Nouvel appareil")
        vpn_access.push_vpn_access(sub["slug"])
        flash("Appareil VPN ajouté. Téléchargez sa configuration ci-dessous.", "success")
    except Exception as e:
        print(f"[VPN] add {e}", flush=True)
        flash("Impossible d'ajouter l'appareil pour le moment.", "error")
    return redirect(url_for("mikrotik_vpn"))


@app.route("/mikrotik/vpn/<int:device_id>/delete", methods=["POST"])
@login_required
def mikrotik_vpn_delete(device_id):
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("slug"):
        flash("Aucun abonnement actif.", "error")
        return redirect(url_for("dashboard"))
    try:
        import wg_store, vpn_access
        if wg_store.count_admin_peers(sub["slug"]) <= 1:
            flash("Vous devez conserver au moins un appareil VPN.", "error")
            return redirect(url_for("mikrotik_vpn"))
        wg_store.delete_admin_peer(sub["slug"], device_id)
        vpn_access.push_vpn_access(sub["slug"])
        flash("Appareil VPN supprimé. Son accès sera coupé sous une minute.", "success")
    except Exception as e:
        print(f"[VPN] delete {e}", flush=True)
        flash("Suppression impossible pour le moment.", "error")
    return redirect(url_for("mikrotik_vpn"))


@app.route("/mikrotik/vpn/status")
@login_required
def mikrotik_vpn_status():
    """État de connexion en temps réel (sondé par la page VPN)."""
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("slug"):
        return jsonify({"router": False, "devices": {}, "operator": False})
    r_on, devs, any_on = _vpn_conn_state(sub["slug"])
    return jsonify({"router": r_on, "operator": any_on,
                    "devices": {str(k): v for k, v in devs.items()}})


def _vpn_conf_response(conf: str, name: str):
    resp = Response(conf, mimetype="text/plain")
    resp.headers["Content-Disposition"] = f"attachment; filename={name}"
    return resp


@app.route("/mikrotik/vpn.conf")
@login_required
def mikrotik_vpn_download():
    """Config du premier appareil (compatibilité)."""
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("slug"):
        flash("Aucun abonnement actif.", "error")
        return redirect(url_for("dashboard"))
    if not config.plan_limits(sub.get("plan"))["vpn"]:
        return redirect(url_for("subscribe"))
    try:
        import wg_store
        conf = wg_store.provision_admin_peer(sub["slug"])["config"]
    except Exception:
        flash("Configuration VPN indisponible pour le moment.", "error")
        return redirect(url_for("mikrotik_vpn"))
    return _vpn_conf_response(conf, "hotspotpro-vpn.conf")


@app.route("/mikrotik/vpn/<int:device_id>.conf")
@login_required
def mikrotik_vpn_device_download(device_id):
    """Config .conf d'un appareil VPN précis (multi-appareils)."""
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub or not sub.get("slug"):
        flash("Aucun abonnement actif.", "error")
        return redirect(url_for("dashboard"))
    if not config.plan_limits(sub.get("plan"))["vpn"]:
        return redirect(url_for("subscribe"))
    try:
        import wg_store
        cfg = wg_store.get_admin_config(sub["slug"], device_id)
    except Exception:
        cfg = None
    if not cfg:
        flash("Configuration VPN indisponible pour le moment.", "error")
        return redirect(url_for("mikrotik_vpn"))
    return _vpn_conf_response(cfg["config"], f"hotspotpro-vpn-{device_id}.conf")


@app.route("/mikrotik/script/<int:device_id>")
@login_required
def mikrotik_device_script(device_id):
    client = current_client()
    sub    = get_active_sub(client["id"])
    if not sub:
        flash("Aucun abonnement actif.", "error")
        return redirect(url_for("dashboard"))

    conn   = get_db()
    device = conn.execute(
        "SELECT * FROM mikrotik_devices WHERE id=? AND client_id=?",
        (device_id, client["id"])
    ).fetchone()
    conn.close()

    if not device:
        flash("Routeur introuvable.", "error")
        return redirect(url_for("dashboard"))
    device = dict(device)

    if not device["provisioned"] or not device.get("slug"):
        flash("Ce MikroTik n'est pas encore activé (paiement en attente).", "info")
        return redirect(url_for("dashboard"))

    router_token = ""
    if PROVISIONER_OK:
        t = core.get_tenant(device["slug"])
        router_token = (t or {}).get("router_token", "") or ""

    vps_ip = config.get_vps_ip()
    tenant = {
        "client_name":  f"{client['full_name']} — {device['label']}",
        "slug":         device["slug"],
        "router_token": router_token,
    }
    oneliner   = mks.generate_oneliner(tenant, vps_ip)
    health_url = mks.health_url(vps_ip, device["slug"])
    profiles   = _profiles_from_sub(sub)
    scripts_by_profile = mks.build_profile_scripts(
        profiles, vps_ip, device["slug"], router_token)

    return render_template("mikrotik.html",
                           client=client, sub=sub,
                           oneliner=oneliner,
                           scripts_by_profile=scripts_by_profile,
                           profiles=profiles,
                           vps_ip=vps_ip, health_url=health_url,
                           device=device)


# ═══════════════════════════════════════════════
# ROUTEURS SUPPLÉMENTAIRES
# ═══════════════════════════════════════════════

@app.route("/mikrotik/add", methods=["GET", "POST"])
@login_required
def mikrotik_add():
    client = current_client()
    sub    = get_active_sub(client["id"])

    if not sub:
        flash("Aucun abonnement actif.", "error")
        return redirect(url_for("dashboard"))

    if not sub.get("provisioned"):
        flash("Configurez d'abord votre système principal.", "info")
        return redirect(url_for("configure"))

    if request.method == "POST":
        label = request.form.get("label", "").strip() or "Mon MikroTik"
        ip    = request.form.get("mikrotik_ip", "").strip()
        plan  = request.form.get("plan", "1m").strip()

        if plan not in config.PLANS:
            plan = "1m"
        p = config.PLANS[plan]

        if not is_valid_ip(ip):
            flash("Adresse IP invalide.", "error")
            return render_template("mikrotik_add.html", sub=sub, plans=config.PLANS)
        # Plus de case « demande expresse de démarrage immédiat » : le
        # consentement n'étant pas demandé, il n'est pas enregistré.
        consent_at = None

        conn = get_db()
        cur = conn.execute("""
            INSERT INTO mikrotik_devices (subscription_id, client_id, label, ip, provisioned)
            VALUES (?, ?, ?, ?, 0)
        """, (sub["id"], client["id"], label, ip))
        device_id = cur.lastrowid
        conn.commit()
        conn.close()

        try:
            trans_id, payment_url = fedapay.create_transaction(
                description=f"HotspotPro — Routeur {label} ({p['label']})",
                amount=p["price"],
                callback_url=f"{config.APP_URL}/mikrotik/callback",
                customer_name=client["full_name"],
                customer_email=client["email"],
                metadata={"type": "device", "client_id": str(client["id"]),
                          "device_id": str(device_id), "plan": plan},
            )
            conn = get_db()
            conn.execute("""
                INSERT INTO mikrotik_payments (client_id, device_id, plan, amount, method,
                                               reference, status, terms_version,
                                               immediate_consent_at)
                VALUES (?, ?, ?, ?, 'fedapay', ?, 'pending', ?, ?)
            """, (client["id"], device_id, plan, p["price"], trans_id,
                  TERMS_VERSION, consent_at))
            conn.commit()
            conn.close()
            return redirect(payment_url)

        except Exception as e:
            conn = get_db()
            conn.execute("DELETE FROM mikrotik_devices WHERE id=?", (device_id,))
            conn.commit()
            conn.close()
            flash(f"Erreur lors de la création du paiement : {e}", "error")
            return render_template("mikrotik_add.html", sub=sub, plans=config.PLANS)

    return render_template("mikrotik_add.html", sub=sub, plans=config.PLANS)


@app.route("/mikrotik/delete/<int:device_id>", methods=["POST"])
@login_required
def mikrotik_delete(device_id):
    client = current_client()
    conn   = get_db()
    device = conn.execute(
        "SELECT * FROM mikrotik_devices WHERE id=? AND client_id=?",
        (device_id, client["id"])
    ).fetchone()

    if not device:
        conn.close()
        flash("Routeur introuvable.", "error")
        return redirect(url_for("dashboard"))

    if device["provisioned"] and device["slug"] and PROVISIONER_OK:
        try:
            core.remove_tenant_files(device["slug"])
        except Exception:
            pass

    # Annuler les paiements encore en attente pour ce routeur, sinon ils
    # resteraient affichés « en attente » indéfiniment sur le dashboard
    conn.execute("""
        UPDATE mikrotik_payments SET status='canceled'
        WHERE device_id=? AND status='pending'
    """, (device_id,))
    conn.execute("DELETE FROM mikrotik_devices WHERE id=?", (device_id,))
    conn.commit()
    conn.close()

    flash("Routeur supprimé. Aucun remboursement ne sera effectué.", "warning")
    return redirect(url_for("dashboard"))


# ═══════════════════════════════════════════════
# COMPTE
# ═══════════════════════════════════════════════

@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    client = current_client()
    if request.method == "POST":
        action = request.form.get("action")

        if action == "update_profile":
            name  = request.form.get("full_name", "").strip()
            phone = request.form.get("phone", "").strip()
            color = request.form.get("avatar_color", "").strip()
            if color not in AVATAR_COLORS:
                color = client.get("avatar_color")
            if not name:
                flash("Le nom ne peut pas être vide.", "error")
            else:
                conn = get_db()
                conn.execute("UPDATE clients SET full_name=?, phone=?, avatar_color=? WHERE id=?",
                             (name, phone, color, client["id"]))
                conn.commit()
                conn.close()
                session["full_name"] = name
                session["avatar_color"] = color
                flash("Profil mis à jour.", "success")
                return redirect(url_for("account"))

        elif action == "change_password":
            old_pwd  = request.form.get("old_password", "")
            new_pwd  = request.form.get("new_password", "")
            new_pwd2 = request.form.get("new_password2", "")
            conn = get_db()
            row  = conn.execute("SELECT password_hash FROM clients WHERE id=?",
                                (client["id"],)).fetchone()
            ok, _ = verify_password(row["password_hash"], old_pwd)
            if not ok:
                flash("Mot de passe actuel incorrect.", "error")
            elif len(new_pwd) < 8:
                flash("Le nouveau mot de passe doit faire au moins 8 caractères.", "error")
            elif new_pwd != new_pwd2:
                flash("Les nouveaux mots de passe ne correspondent pas.", "error")
            else:
                conn.execute("UPDATE clients SET password_hash=? WHERE id=?",
                             (hash_password(new_pwd), client["id"]))
                conn.commit()
                flash("Mot de passe changé avec succès.", "success")
            conn.close()
            return redirect(url_for("account"))

    import reviews
    conn = get_db()
    payments = conn.execute(
        "SELECT * FROM payments WHERE client_id=? ORDER BY created_at DESC",
        (client["id"],)
    ).fetchall()
    my_review = reviews.last_submission(conn, client["id"])
    conn.close()

    return render_template("account.html", client=client,
                           payments=payments, plans=config.PLANS,
                           my_review=my_review,
                           avatar_colors=AVATAR_COLORS)


@app.route("/avis", methods=["POST"])
@login_required
def submit_review():
    """Avis client : enregistré en attente, publié par l'administrateur.

    La publication du nom et de l'activité suppose l'accord de l'auteur : la
    case de consentement du formulaire est donc obligatoire."""
    import reviews
    client = current_client()
    quote = (request.form.get("quote") or "").strip()
    name  = (request.form.get("author_name") or "").strip() or client["full_name"]
    role  = (request.form.get("author_role") or "").strip()
    stars = request.form.get("stars", "5")

    if request.form.get("accept_publication") != "1":
        flash("Cochez la case autorisant la publication de votre avis.", "error")
        return redirect(url_for("account"))
    if len(quote) < 20:
        flash("Votre avis doit faire au moins 20 caractères.", "error")
        return redirect(url_for("account"))

    conn = get_db()
    reviews.submit(conn, client["id"], name, role, quote, stars)
    conn.commit()
    conn.close()

    # Alerte à l'administrateur (bot d'alerte, s'il est configuré)
    try:
        services.send_telegram_notify(
            config.admin_bot_token(), config.admin_chat_id(),
            f"💬 <b>Nouvel avis client</b>\n\n{name} ({client['email']})\n"
            f"{'⭐' * max(1, min(5, int(stars or 5)))}\n\n{quote[:300]}\n\n"
            "À afficher ou supprimer dans Administration > Avis.")
    except Exception as e:
        print(f"[AVIS] alerte admin impossible : {e}", flush=True)

    flash("Merci ! Votre avis a été transmis. Il sera publié après validation.",
          "success")
    return redirect(url_for("account"))


# ═══════════════════════════════════════════════
# DONNÉES PERSONNELLES : suppression du compte (loi n° 2019-014, art. 39-48)
# ═══════════════════════════════════════════════
# Le téléchargement en libre-service a été retiré. Le droit d'accès reste dû :
# une demande reçue par e-mail se sert de privacy.export_client_data().

@app.route("/account/delete", methods=["POST"])
@login_required
def account_delete():
    """Suppression définitive du compte par son titulaire : mot de passe et
    confirmation explicite exigés, pour éviter une suppression accidentelle
    ou provoquée à son insu."""
    import privacy
    client = current_client()
    if client.get("is_admin"):
        flash("Un compte administrateur ne peut pas être supprimé ici.", "error")
        return redirect(url_for("account"))
    if request.form.get("confirm_delete") != "1":
        flash("Cochez la case de confirmation pour supprimer votre compte.", "error")
        return redirect(url_for("account"))

    conn = get_db()
    row = conn.execute("SELECT password_hash FROM clients WHERE id=?",
                       (client["id"],)).fetchone()
    ok, _ = verify_password(row["password_hash"], request.form.get("password", ""))
    if not ok:
        conn.close()
        flash("Mot de passe incorrect : compte non supprimé.", "error")
        return redirect(url_for("account"))

    privacy.delete_client_account(conn, client["id"])
    conn.commit()
    conn.close()
    session.clear()
    flash("Votre compte et vos données ont été supprimés. Les paiements "
          "encaissés sont conservés sans vos coordonnées, pour les obligations "
          "comptables.", "success")
    return redirect(url_for("index"))
