"""
routes_admin.py — Tableau de bord administrateur
"""
from flask import render_template, request, redirect, url_for, session, flash

import config
import fedapay
import services
from webapp_core import app, PROVISIONER_OK
import webapp_core as core
from db import get_db
from security import login_required, admin_required, hash_password

try:
    import access_log            # module partagé (saas/core), sur le sys.path
    ACCESS_LOG_OK = True
except Exception as _e:          # pragma: no cover
    ACCESS_LOG_OK = False
    print(f"[ADMIN] access_log indisponible: {_e}", flush=True)


@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    conn     = get_db()
    clients  = conn.execute("SELECT * FROM clients ORDER BY created_at DESC").fetchall()
    payments = conn.execute("""
        SELECT p.*, c.full_name, c.email
        FROM payments p JOIN clients c ON p.client_id=c.id
        ORDER BY p.created_at DESC
    """).fetchall()
    subs = conn.execute("""
        SELECT s.*, c.full_name, c.email
        FROM subscriptions s JOIN clients c ON s.client_id=c.id
        ORDER BY s.created_at DESC
    """).fetchall()
    mk_payments = conn.execute("""
        SELECT mp.*, c.full_name, c.email, md.label as device_label, md.ip as device_ip
        FROM mikrotik_payments mp
        JOIN clients c ON mp.client_id=c.id
        LEFT JOIN mikrotik_devices md ON mp.device_id=md.id
        ORDER BY mp.created_at DESC
    """).fetchall()
    conn.close()

    share_alerts = 0
    if ACCESS_LOG_OK:
        try:
            share_alerts = len(access_log.sharing_report(window_hours=48, min_devices=2))
        except Exception:
            share_alerts = 0

    stats = {
        "total_clients":  len([c for c in clients if not c["is_admin"]]),
        "total_payments": len(payments),
        "pending":        sum(1 for p in payments if p["status"] == "pending"),
        "confirmed":      sum(1 for p in payments if p["status"] == "confirmed"),
        "revenue":        sum(p["amount"] for p in payments if p["status"] == "confirmed"),
        "active_subs":    sum(1 for s in subs if s["active"]),
        "mk_pending":     sum(1 for p in mk_payments if p["status"] == "pending"),
        "share_alerts":   share_alerts,
    }

    # Répartition des ventes par formule (paiements confirmés) pour le donut.
    _colors = {"1m": "#2563EB", "3m": "#0D9488", "5m": "#D97706", "12m": "#7C3AED"}
    _counts = {k: 0 for k in config.PLANS}
    for p in payments:
        if p["status"] == "confirmed" and p["plan"] in _counts:
            _counts[p["plan"]] += 1
    plan_stats = [{
        "key":   k,
        "label": config.PLANS[k]["label"],
        "price": config.PLANS[k]["price"],
        "count": _counts[k],
        "color": _colors.get(k, "#64748B"),
    } for k in config.PLANS]
    plan_total = sum(_counts.values())

    return render_template("admin.html",
        clients=clients, payments=payments, subs=subs,
        stats=stats, plans=config.PLANS, mk_payments=mk_payments,
        plan_stats=plan_stats, plan_total=plan_total)


@app.route("/admin/clear_payments", methods=["POST"])
@login_required
@admin_required
def admin_clear_payments():
    """Vide l'historique des paiements (abonnements) en conservant les
    paiements « en attente » qui restent à traiter."""
    conn = get_db()
    cur = conn.execute("DELETE FROM payments WHERE status != 'pending'")
    conn.commit()
    conn.close()
    flash(f"Historique des paiements vidé ({cur.rowcount} ligne(s) supprimée(s)). "
          "Les paiements en attente ont été conservés.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/clear_mikrotik_payments", methods=["POST"])
@login_required
@admin_required
def admin_clear_mikrotik_payments():
    """Vide l'historique des paiements de routeurs supplémentaires (garde les
    paiements en attente)."""
    conn = get_db()
    cur = conn.execute("DELETE FROM mikrotik_payments WHERE status != 'pending'")
    conn.commit()
    conn.close()
    flash(f"Historique des routeurs supplémentaires vidé ({cur.rowcount} ligne(s)). "
          "Les paiements en attente ont été conservés.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/clear_subscriptions", methods=["POST"])
@login_required
@admin_required
def admin_clear_subscriptions():
    """Vide l'historique des abonnements en supprimant uniquement les
    abonnements inactifs (expirés ou coupés). Les abonnements actifs, qui
    pilotent le service en cours, sont conservés."""
    conn = get_db()
    cur = conn.execute("DELETE FROM subscriptions WHERE active=0")
    conn.commit()
    conn.close()
    flash(f"Historique des abonnements vidé ({cur.rowcount} abonnement(s) inactif(s) "
          "supprimé(s)). Les abonnements actifs ont été conservés.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/access")
@login_required
@admin_required
def admin_access():
    """Journal d'accès des routeurs + détection de partage d'abonnement.
    Un slug vu depuis plusieurs IP publiques = script/tunnel probablement
    recopié sur un 2e routeur non payé."""
    window = request.args.get("window", "48", type=int) or 48

    # slug -> client (abonnements + routeurs supplémentaires)
    conn = get_db()
    owners = {}
    for r in conn.execute("""
        SELECT s.slug AS slug, c.full_name AS name, c.email AS email,
               s.router_name AS label
        FROM subscriptions s JOIN clients c ON s.client_id=c.id
        WHERE s.slug IS NOT NULL AND s.slug != ''
    """).fetchall():
        owners[r["slug"]] = {"name": r["name"], "email": r["email"], "label": r["label"]}
    for r in conn.execute("""
        SELECT md.slug AS slug, c.full_name AS name, c.email AS email,
               md.label AS label
        FROM mikrotik_devices md JOIN clients c ON md.client_id=c.id
        WHERE md.slug IS NOT NULL AND md.slug != ''
    """).fetchall():
        owners.setdefault(r["slug"], {"name": r["name"], "email": r["email"],
                                      "label": r["label"]})
    conn.close()

    alerts, logs = [], []
    if ACCESS_LOG_OK:
        try:
            alerts = access_log.sharing_report(window_hours=window, min_devices=2)
            logs   = access_log.recent(limit=400)
        except Exception as e:
            flash(f"Journal d'accès indisponible : {e}", "error")

    for a in alerts:
        a["owner"] = owners.get(a["slug"])
    for l in logs:
        l["owner"] = owners.get(l["slug"])

    return render_template("admin_access.html",
                           alerts=alerts, logs=logs, window=window,
                           access_ok=ACCESS_LOG_OK)


def _reject_if_not_paid(payment: dict) -> str | None:
    """Interroge FedaPay (source de vérité) avant toute activation manuelle.

    Empêche l'activation d'un paiement simplement « pending » (client qui a
    ouvert la page FedaPay puis abandonné) : sans ce garde-fou, un clic admin
    par erreur offrirait un accès gratuit. Retourne un message d'erreur si
    l'activation doit être refusée, sinon None. Un paiement sans référence
    FedaPay (saisi manuellement) reste confirmable à la main."""
    ref = payment.get("reference")
    if not ref:
        return None
    try:
        info = fedapay.transaction_status(ref)
    except Exception as e:
        print(f"[ADMIN] vérif FedaPay échouée ({ref}): {e}", flush=True)
        return "Impossible de vérifier ce paiement auprès de FedaPay. Réessayez dans un instant."
    if info["status"] != "approved":
        return (f"FedaPay indique « {info['status'] or 'non payé'} » : "
                "ce paiement n'a pas été réglé. Activation refusée.")
    if info["amount"] is not None and info["amount"] < int(payment["amount"]):
        return (f"Montant réglé insuffisant selon FedaPay "
                f"({info['amount']} < {payment['amount']} FCFA). Activation refusée.")
    return None


@app.route("/admin/confirm_payment/<int:pid>", methods=["POST"])
@login_required
@admin_required
def confirm_payment(pid):
    conn = get_db()
    row  = conn.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()

    if not row or row["status"] != "pending":
        conn.close()
        flash("Paiement introuvable ou déjà traité.", "error")
        return redirect(url_for("admin_dashboard"))
    payment = dict(row)

    # Vérification FedaPay obligatoire avant activation manuelle.
    err = _reject_if_not_paid(payment)
    if err:
        conn.close()
        flash(err, "error")
        return redirect(url_for("admin_dashboard"))

    # Claim atomique : protège contre un double-clic ou un webhook simultané
    cur = conn.execute("""
        UPDATE payments
        SET status='confirmed', confirmed_at=datetime('now','localtime')
        WHERE id=? AND status='pending'
    """, (pid,))
    if cur.rowcount == 0:
        conn.close()
        flash("Paiement déjà traité.", "error")
        return redirect(url_for("admin_dashboard"))

    sub_id, end = services.activate_subscription(conn, payment["client_id"], payment["plan"])
    conn.execute("UPDATE payments SET subscription_id=? WHERE id=?", (sub_id, pid))
    conn.commit()
    conn.close()

    flash(f"Paiement confirmé. Abonnement actif jusqu'au {end.strftime('%d/%m/%Y')}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/confirm_mikrotik_payment/<int:pid>", methods=["POST"])
@login_required
@admin_required
def confirm_mikrotik_payment(pid):
    conn = get_db()
    row  = conn.execute("SELECT * FROM mikrotik_payments WHERE id=?", (pid,)).fetchone()

    if not row or row["status"] != "pending":
        conn.close()
        flash("Paiement introuvable ou déjà traité.", "error")
        return redirect(url_for("admin_dashboard"))
    payment = dict(row)

    # Vérification FedaPay obligatoire avant activation manuelle.
    err = _reject_if_not_paid(payment)
    if err:
        conn.close()
        flash(err, "error")
        return redirect(url_for("admin_dashboard"))

    cur = conn.execute("""
        UPDATE mikrotik_payments
        SET status='confirmed', confirmed_at=datetime('now','localtime')
        WHERE id=? AND status='pending'
    """, (pid,))
    if cur.rowcount == 0:
        conn.close()
        flash("Paiement déjà traité.", "error")
        return redirect(url_for("admin_dashboard"))

    device_row = conn.execute("SELECT * FROM mikrotik_devices WHERE id=?",
                              (payment["device_id"],)).fetchone()
    if device_row:
        services.activate_device(conn, dict(device_row), payment["plan"] or "1m")

    conn.commit()
    conn.close()

    flash("MikroTik activé avec succès.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/toggle_sub/<int:sid>", methods=["POST"])
@login_required
@admin_required
def toggle_sub(sid):
    conn = get_db()
    sub  = conn.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    if sub:
        new_state = 0 if sub["active"] else 1
        conn.execute("UPDATE subscriptions SET active=? WHERE id=?", (new_state, sid))
        conn.commit()
        if sub["slug"] and PROVISIONER_OK:
            try:
                if new_state == 0:
                    core.stop_tenant(sub["slug"])
                else:
                    core.start_tenant(sub["slug"])
            except Exception:
                pass
    conn.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/client/<int:cid>")
@login_required
@admin_required
def admin_client(cid):
    """Fiche complète d'un opérateur : abonnements, routeurs, appareils VPN
    (avec état de connexion), paiements, terminaux vus (journal d'accès)."""
    conn = get_db()
    row = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        flash("Client introuvable.", "error")
        return redirect(url_for("admin_dashboard"))
    client   = dict(row)
    subs     = [dict(r) for r in conn.execute(
        "SELECT * FROM subscriptions WHERE client_id=? ORDER BY id DESC", (cid,)).fetchall()]
    payments = [dict(r) for r in conn.execute(
        "SELECT * FROM payments WHERE client_id=? ORDER BY created_at DESC", (cid,)).fetchall()]
    devices  = [dict(r) for r in conn.execute(
        "SELECT * FROM mikrotik_devices WHERE client_id=? ORDER BY id", (cid,)).fetchall()]
    conn.close()

    slugs = [s["slug"] for s in subs if s.get("slug")]
    slugs += [d["slug"] for d in devices if d.get("slug")]
    slugs = list(dict.fromkeys(slugs))          # unique, ordre stable

    vpn_devices, terminals, svc = {}, {}, {}
    try:
        import time as _t
        import wg_store
        now = _t.time()
        for slug in slugs:
            vpn_devices[slug] = [
                {**d, "online": (now - wg_store.last_handshake(d["public_key"])) < 240}
                for d in wg_store.list_admin_peers(slug)]
    except Exception as e:
        print(f"[ADMIN] vpn info: {e}", flush=True)
    if ACCESS_LOG_OK:
        for slug in slugs:
            try:
                terminals[slug] = access_log.recent(slug=slug, limit=100)
            except Exception:
                terminals[slug] = []
    if PROVISIONER_OK:
        for slug in slugs:
            try:
                svc[slug] = core.get_service_status(slug)
            except Exception:
                svc[slug] = None

    return render_template("admin_client.html", client=client, subs=subs,
                           payments=payments, devices=devices, slugs=slugs,
                           vpn_devices=vpn_devices, terminals=terminals,
                           svc=svc, plans=config.PLANS)


@app.route("/admin/client/<int:cid>/delete", methods=["POST"])
@login_required
@admin_required
def admin_client_delete(cid):
    """Supprime un opérateur ET toutes ses données (abonnements, paiements,
    routeurs, appareils VPN, tenant provisionné). Irréversible."""
    conn = get_db()
    row = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        flash("Client introuvable.", "error")
        return redirect(url_for("admin_dashboard"))
    if row["is_admin"]:
        conn.close()
        flash("Impossible de supprimer un compte administrateur ici.", "error")
        return redirect(url_for("admin_dashboard"))
    client = dict(row)
    email  = client["email"]

    slugs = [r["slug"] for r in conn.execute(
        "SELECT slug FROM subscriptions WHERE client_id=? AND slug IS NOT NULL AND slug!=''",
        (cid,)).fetchall()]
    slugs += [r["slug"] for r in conn.execute(
        "SELECT slug FROM mikrotik_devices WHERE client_id=? AND slug IS NOT NULL AND slug!=''",
        (cid,)).fetchall()]
    slugs = list(dict.fromkeys(slugs))

    # 1) Démontage des tenants (bot, fichiers, registre central) + WireGuard
    for slug in slugs:
        if PROVISIONER_OK:
            for fn in ("stop_tenant", "remove_tenant_files", "delete_tenant"):
                try:
                    getattr(core, fn)(slug)
                except Exception as e:
                    print(f"[ADMIN] delete {slug} {fn}: {e}", flush=True)
        try:
            import wg_store
            wg_store.delete_all_for_slug(slug)
        except Exception as e:
            print(f"[ADMIN] wg cleanup {slug}: {e}", flush=True)

    # 2) Nettoyage base web (enfants -> parent)
    conn.execute("DELETE FROM notifications WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM mikrotik_payments WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM mikrotik_devices WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM payments WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM subscriptions WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
    conn.execute("DELETE FROM clients WHERE id=?", (cid,))
    conn.commit()
    conn.close()

    flash(f"Compte « {client['full_name']} » et toutes ses données ont été supprimés.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/account", methods=["GET", "POST"])
@login_required
@admin_required
def admin_account():
    from routes_client import AVATAR_COLORS
    conn = get_db()
    row  = conn.execute("SELECT * FROM clients WHERE id=?",
                        (session["client_id"],)).fetchone()
    admin = dict(row) if row else {}

    if request.method == "POST" and request.form.get("action") == "update_profile":
        name  = request.form.get("full_name", "").strip()
        color = request.form.get("avatar_color", "").strip()
        if color not in AVATAR_COLORS:
            color = admin.get("avatar_color")
        if not name:
            flash("Le nom ne peut pas être vide.", "error")
        else:
            conn.execute("UPDATE clients SET full_name=?, avatar_color=? WHERE id=?",
                         (name, color, session["client_id"]))
            conn.commit()
            session["full_name"] = name
            session["avatar_color"] = color
            admin["full_name"] = name
            admin["avatar_color"] = color
            flash("Profil administrateur mis à jour.", "success")
        conn.close()
        return redirect(url_for("admin_account"))

    conn.close()
    return render_template("admin_account.html", admin=admin,
                           avatar_colors=AVATAR_COLORS)


@app.route("/admin/settings", methods=["GET", "POST"])
@login_required
@admin_required
def admin_settings():
    import settings as app_settings

    if request.method == "POST":
        action = request.form.get("action", "save")
        if action == "test_telegram":
            _test_telegram_alert()
            return redirect(url_for("admin_settings"))
        if action == "test_brevo":
            _test_brevo_key()
            return redirect(url_for("admin_settings"))
        if action == "test_backup":
            _test_backup_remote()
            return redirect(url_for("admin_settings"))
        n = app_settings.save_from_form(request.form)
        flash(f"Configuration enregistrée ({n} champ(s)). "
              "Les scripts de sauvegarde et le watchdog s'appuieront sur ces valeurs.",
              "success")
        return redirect(url_for("admin_settings"))

    return render_template("admin_settings.html",
                           groups=app_settings.view_model())


def _test_telegram_alert():
    """Envoie un message de test avec les valeurs ACTUELLEMENT enregistrées."""
    import urllib.parse, urllib.request
    token   = config._setting("admin_bot_token")
    chat_id = config._setting("admin_chat_id")
    if not token or not chat_id:
        flash("Renseignez et enregistrez d'abord le token du bot et le chat ID.", "error")
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": "HotspotPro : message de test depuis la page de configuration. "
                    "Les alertes fonctionnent."}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status == 200
        flash("Message Telegram envoyé — vérifiez votre conversation."
              if ok else "Telegram a refusé l'envoi (token ou chat ID invalide).",
              "success" if ok else "error")
    except Exception:
        flash("Envoi Telegram impossible (token ou chat ID invalide).", "error")


def _test_brevo_key():
    import urllib.request
    key = config._setting("brevo_api_key")
    if not key:
        flash("Renseignez et enregistrez d'abord la clé API Brevo.", "error")
        return
    try:
        req = urllib.request.Request("https://api.brevo.com/v3/account",
                                     headers={"api-key": key, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ok = r.status == 200
        flash("Clé Brevo valide — emails opérationnels." if ok
              else "Clé Brevo invalide.", "success" if ok else "error")
    except Exception:
        flash("Clé Brevo invalide ou service injoignable.", "error")


def _rclone_backup_dest():
    """Construit la destination rclone à partir des réglages admin.
    Retourne (dest, message_erreur) — dest None si non configuré."""
    import shlex
    dtype = config._setting("backup_dest_type") or "none"
    if dtype == "none":
        return None, "Aucune copie distante configurée (type « Locale uniquement »)."
    if dtype == "rclone":
        remote = config._setting("backup_rclone_remote")
        return (remote, None) if remote else (None, "Remote rclone non renseigné.")

    host = config._setting("backup_host")
    user = config._setting("backup_user")
    pwd  = config._setting("backup_pass")
    path = config._setting("backup_remote_path") or "hotspotpro"
    if not host or not user:
        return None, "Hôte et nom d'utilisateur requis."
    try:
        import subprocess
        ob = subprocess.run(["rclone", "obscure", pwd], capture_output=True,
                            text=True, timeout=10).stdout.strip()
    except FileNotFoundError:
        return None, "rclone n'est pas installé sur le serveur."
    except Exception:
        return None, "Impossible de préparer les identifiants (rclone obscure)."

    if dtype == "smb":
        share = config._setting("backup_smb_share")
        if not share:
            return None, "Nom du partage SMB requis."
        return f":smb,host='{host}',user='{user}',pass='{ob}':{share}/{path}", None
    if dtype == "sftp":
        port = config._setting("backup_sftp_port") or "22"
        return f":sftp,host='{host}',user='{user}',pass='{ob}',port='{port}':{path}", None
    return None, f"Type de destination inconnu : {dtype}"


def _test_backup_remote():
    """Vérifie la connexion distante en créant le dossier cible (mkdir)."""
    import subprocess
    dest, err = _rclone_backup_dest()
    if err:
        flash(err, "error")
        return
    try:
        r = subprocess.run(["rclone", "mkdir", dest], capture_output=True,
                           text=True, timeout=30)
        if r.returncode == 0:
            flash("Connexion au serveur de sauvegarde réussie — dossier cible accessible en écriture.", "success")
        else:
            detail = (r.stderr or "").strip().splitlines()
            msg = detail[-1] if detail else "échec inconnu"
            flash(f"Connexion au serveur de sauvegarde échouée : {msg}", "error")
    except FileNotFoundError:
        flash("rclone n'est pas installé sur le serveur (copie distante indisponible).", "error")
    except subprocess.TimeoutExpired:
        flash("Le serveur de sauvegarde ne répond pas (délai dépassé). Vérifiez l'hôte, le port et le réseau.", "error")
    except Exception as e:
        flash(f"Test impossible : {e}", "error")


@app.route("/admin/change_password", methods=["POST"])
@login_required
@admin_required
def admin_change_password():
    new_pwd  = request.form.get("new_password", "")
    new_pwd2 = request.form.get("new_password2", "")
    if len(new_pwd) < 8:
        flash("Mot de passe trop court (min 8 caractères).", "error")
    elif new_pwd != new_pwd2:
        flash("Les mots de passe ne correspondent pas.", "error")
    else:
        conn = get_db()
        conn.execute("UPDATE clients SET password_hash=? WHERE id=?",
                     (hash_password(new_pwd), session["client_id"]))
        conn.commit()
        conn.close()
        flash("Mot de passe administrateur changé.", "success")
    return redirect(url_for("admin_account"))
