"""
routes_admin.py — Tableau de bord administrateur
"""
from flask import render_template, request, redirect, url_for, session, flash

import config
import services
from webapp_core import app, PROVISIONER_OK
import webapp_core as core
from db import get_db
from security import login_required, admin_required, hash_password


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

    stats = {
        "total_clients":  len([c for c in clients if not c["is_admin"]]),
        "total_payments": len(payments),
        "pending":        sum(1 for p in payments if p["status"] == "pending"),
        "confirmed":      sum(1 for p in payments if p["status"] == "confirmed"),
        "revenue":        sum(p["amount"] for p in payments if p["status"] == "confirmed"),
        "active_subs":    sum(1 for s in subs if s["active"]),
        "mk_pending":     sum(1 for p in mk_payments if p["status"] == "pending"),
    }

    return render_template("admin.html",
        clients=clients, payments=payments, subs=subs,
        stats=stats, plans=config.PLANS, mk_payments=mk_payments)


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

    sub_id, end = services.activate_subscription(conn, payment["client_id"], payment["plan"])
    conn.execute("""
        UPDATE payments
        SET status='confirmed', confirmed_at=datetime('now','localtime'), subscription_id=?
        WHERE id=?
    """, (sub_id, pid))
    conn.commit()
    conn.close()

    flash(f"✅ Paiement confirmé. Abonnement actif jusqu'au {end.strftime('%d/%m/%Y')}.", "success")
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

    device_row = conn.execute("SELECT * FROM mikrotik_devices WHERE id=?",
                              (payment["device_id"],)).fetchone()
    if device_row:
        services.activate_device(conn, dict(device_row))

    conn.execute("""
        UPDATE mikrotik_payments
        SET status='confirmed', confirmed_at=datetime('now','localtime')
        WHERE id=?
    """, (pid,))
    conn.commit()
    conn.close()

    flash("✅ MikroTik activé avec succès.", "success")
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
        flash("✅ Mot de passe administrateur changé.", "success")
    return redirect(url_for("admin_dashboard"))
