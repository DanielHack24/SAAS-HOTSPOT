"""
routes_client.py — Espace client : dashboard, configuration, scripts
MikroTik, compte, routeurs supplémentaires
"""
import re, json, secrets

from flask import render_template, request, redirect, url_for, session, flash

import config
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
        if mikrotik_ip and not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", mikrotik_ip):
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
                flash("🎉 Votre système a été déployé avec succès !", "success")
            except Exception as e:
                flash(f"Erreur de déploiement : {e}", "error")
                return render_template("configure.html", sub=sub)
        else:
            _mark_provisioned(sub["id"])
            flash("✅ Configuration enregistrée.", "success")

        return redirect(url_for("mikrotik_script"))

    return render_template("configure.html", sub=sub)


def _mark_provisioned(sub_id: int):
    conn = get_db()
    conn.execute("UPDATE subscriptions SET provisioned=1 WHERE id=?", (sub_id,))
    conn.commit()
    conn.close()


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
    health_url = f"http://{vps_ip}/t/{sub['slug']}/health"

    profiles = _profiles_from_sub(sub)
    scripts_by_profile = {
        name: mks.generate_oneliner(tenant, vps_ip, data.get("validity") or "30d")
        for name, data in profiles.items()
    }

    return render_template("mikrotik.html",
                           client=client, sub=sub,
                           oneliner=oneliner,
                           profiles=profiles,
                           scripts_by_profile=scripts_by_profile,
                           vps_ip=vps_ip, health_url=health_url)


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
    script     = mks.generate_full_script(tenant, vps_ip)
    oneliner   = mks.generate_oneliner(tenant, vps_ip)
    health_url = f"http://{vps_ip}/t/{device['slug']}/health"

    return render_template("mikrotik.html",
                           client=client, sub=sub,
                           script=script, oneliner=oneliner,
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

        if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
            flash("Adresse IP invalide.", "error")
            return render_template("mikrotik_add.html", sub=sub, plans=config.PLANS)

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
                INSERT INTO mikrotik_payments (client_id, device_id, plan, amount, method, reference, status)
                VALUES (?, ?, ?, ?, 'fedapay', ?, 'pending')
            """, (client["id"], device_id, plan, p["price"], trans_id))
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

    conn.execute("DELETE FROM mikrotik_devices WHERE id=?", (device_id,))
    conn.commit()
    conn.close()

    flash("🗑️ Routeur supprimé. Aucun remboursement ne sera effectué.", "warning")
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
            if not name:
                flash("Le nom ne peut pas être vide.", "error")
            else:
                conn = get_db()
                conn.execute("UPDATE clients SET full_name=?, phone=? WHERE id=?",
                             (name, phone, client["id"]))
                conn.commit()
                conn.close()
                session["full_name"] = name
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

    conn = get_db()
    payments = conn.execute(
        "SELECT * FROM payments WHERE client_id=? ORDER BY created_at DESC",
        (client["id"],)
    ).fetchall()
    conn.close()

    return render_template("account.html", client=client,
                           payments=payments, plans=config.PLANS)
