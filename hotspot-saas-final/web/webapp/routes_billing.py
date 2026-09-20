"""
routes_billing.py — Abonnements, paiements FedaPay, webhook
"""
from flask import (render_template, request, redirect, url_for,
                   session, flash, jsonify)

import config
from datetime import datetime
from legal_content import TERMS_VERSION
import fedapay
import services
from webapp_core import app
from db import get_db, get_client
from security import login_required


@app.route("/subscribe", methods=["GET", "POST"])
@login_required
def subscribe():
    plan = request.args.get("plan") or request.form.get("plan")
    if plan not in config.PLANS:
        plan = "3m"
    return render_template("subscribe.html", plan=plan,
                           p=config.PLANS[plan], plans=config.PLANS,
                           fedapay_public_key=config.fedapay_public_key())


@app.route("/subscribe/checkout", methods=["POST"])
@login_required
def subscribe_checkout():
    """Crée une transaction FedaPay et redirige vers la page de paiement."""
    client = get_client(session["client_id"])
    plan   = request.form.get("plan", "3m")

    if plan not in config.PLANS:
        flash("Plan invalide.", "error")
        return redirect(url_for("subscribe"))
    p = config.PLANS[plan]
    # Plus de case « demande expresse de démarrage immédiat » à la commande :
    # immediate_consent_at reste donc vide. Consentement non demandé =
    # consentement non enregistré (les lignes déjà remplies sont conservées).
    consent_at = None

    try:
        trans_id, payment_url = fedapay.create_transaction(
            description=f"HotspotPro — Abonnement {p['label']}",
            amount=p["price"],
            callback_url=f"{config.APP_URL}/subscribe/callback",
            customer_name=client["full_name"],
            customer_email=client["email"],
            metadata={"type": "subscription",
                      "client_id": str(client["id"]), "plan": plan},
        )
        conn = get_db()
        conn.execute("""
            INSERT INTO payments (client_id, plan, amount, method, reference, status,
                                  terms_version, immediate_consent_at)
            VALUES (?, ?, ?, 'fedapay', ?, 'pending', ?, ?)
        """, (client["id"], plan, p["price"], trans_id, TERMS_VERSION, consent_at))
        conn.commit()
        conn.close()
        return redirect(payment_url)

    except Exception as e:
        print(f"[BILLING] Erreur création transaction FedaPay : {e}", flush=True)
        flash("Erreur lors de la création du paiement. Veuillez réessayer dans quelques instants.", "error")
        return redirect(url_for("subscribe", plan=plan))


@app.route("/subscribe/callback")
@login_required
def subscribe_callback():
    """FedaPay redirige ici après le paiement (succès ou échec).
    Purement informatif : l'activation réelle passe par le webhook signé."""
    status = request.args.get("status", "")
    if status == "approved":
        flash("Paiement confirmé. Votre abonnement est en cours d'activation automatique.", "success")
        return redirect(url_for("dashboard"))
    elif status == "declined":
        flash("Paiement refusé. Veuillez réessayer.", "error")
        return redirect(url_for("subscribe"))
    elif status == "canceled":
        flash("Paiement annulé.", "warning")
        return redirect(url_for("subscribe"))
    flash("Paiement en cours de traitement…", "info")
    return redirect(url_for("dashboard"))


@app.route("/mikrotik/callback")
@login_required
def mikrotik_callback():
    """FedaPay redirige ici après paiement d'un routeur supplémentaire."""
    status = request.args.get("status", "")
    if status == "approved":
        flash("Paiement reçu. Votre routeur sera activé sous peu.", "success")
    elif status == "declined":
        flash("Paiement refusé. Veuillez réessayer.", "error")
    elif status == "canceled":
        flash("Paiement annulé.", "warning")
    else:
        flash("Paiement en cours de vérification…", "info")
    return redirect(url_for("dashboard"))


@app.route("/payment/pending")
@login_required
def payment_pending():
    """Page de statut après soumission d'un paiement."""
    client = get_client(session["client_id"])
    conn   = get_db()
    payment = conn.execute(
        "SELECT * FROM payments WHERE client_id=? ORDER BY created_at DESC LIMIT 1",
        (client["id"],)
    ).fetchone()
    conn.close()
    return render_template("payment_pending.html",
                           client=client, payment=payment, plans=config.PLANS)


# ═══════════════════════════════════════════════
# WEBHOOK FEDAPAY
# ═══════════════════════════════════════════════

@app.route("/webhook/fedapay", methods=["POST"])
def webhook_fedapay():
    """Reçoit les événements FedaPay et active automatiquement les
    abonnements/routeurs payés.

    Sécurité :
      - signature vérifiée STRICTEMENT (403 sinon) ;
      - le montant payé est comparé au montant attendu du paiement
        enregistré en base — un webhook rejoué ou falsifié ne peut
        rien activer.
    """
    payload   = request.get_data()
    signature = request.headers.get("X-FEDAPAY-SIGNATURE", "")

    if not fedapay.verify_webhook_signature(payload, signature):
        print("[WEBHOOK] Signature invalide — rejeté", flush=True)
        return jsonify({"error": "signature invalide"}), 403

    try:
        evt = fedapay.parse_webhook(payload)
    except Exception as e:
        return jsonify({"error": f"payload invalide : {e}"}), 400

    if evt["name"] != "transaction.approved":
        return jsonify({"status": "ignored"}), 200

    trans_id = evt["trans_id"]
    metadata = evt["metadata"]
    if not trans_id:
        return jsonify({"error": "transaction sans id"}), 400

    if metadata.get("device_id") or metadata.get("type") == "device":
        return _confirm_device_payment(trans_id, evt)
    return _confirm_subscription_payment(trans_id, evt)


def _amount_invalid(evt: dict, expected_amount: int, trans_id: str) -> str | None:
    """Vérifie devise et montant du webhook. Retourne un message d'erreur
    ou None si tout est conforme."""
    if evt.get("currency") and evt["currency"] != "XOF":
        print(f"[WEBHOOK] Devise inattendue : {evt['currency']} (ref {trans_id})", flush=True)
        return "devise invalide"
    if evt.get("amount") is not None and int(evt["amount"]) < expected_amount:
        print(f"[WEBHOOK] Montant insuffisant : {evt['amount']} < {expected_amount} (ref {trans_id})", flush=True)
        return "montant insuffisant"
    return None


def _confirm_subscription_payment(trans_id: str, evt: dict):
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM payments WHERE reference=? AND status='pending'",
        (trans_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "ignored", "reason": "paiement inconnu ou déjà traité"}), 200
    payment = dict(row)

    err = _amount_invalid(evt, payment["amount"], trans_id)
    if err:
        conn.close()
        return jsonify({"error": err}), 400

    try:
        # Claim atomique : si un webhook concurrent a déjà confirmé ce
        # paiement, rowcount vaut 0 et on n'active rien une deuxième fois.
        cur = conn.execute("""
            UPDATE payments
            SET status='confirmed', confirmed_at=datetime('now','localtime')
            WHERE id=? AND status='pending'
        """, (payment["id"],))
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"status": "ignored", "reason": "déjà traité"}), 200

        sub_id, end = services.activate_subscription(conn, payment["client_id"], payment["plan"])
        conn.execute("UPDATE payments SET subscription_id=? WHERE id=?",
                     (sub_id, payment["id"]))
        conn.commit()

        client_row = conn.execute("SELECT * FROM clients WHERE id=?",
                                  (payment["client_id"],)).fetchone()
        conn.close()

        if client_row:
            from emails import email_subscription_activated
            p = config.PLANS[payment["plan"]]
            email_subscription_activated(
                client_row["email"], client_row["full_name"],
                p["label"], payment["amount"], trans_id,
                end.strftime("%d/%m/%Y"))

        return jsonify({"status": "ok", "subscription_id": sub_id}), 200

    except Exception as e:
        conn.close()
        print(f"[WEBHOOK] Erreur activation abonnement : {e}", flush=True)
        return jsonify({"error": str(e)}), 500


def _confirm_device_payment(trans_id: str, evt: dict):
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM mikrotik_payments WHERE reference=? AND status='pending'",
        (trans_id,)
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"status": "ignored", "reason": "paiement routeur inconnu ou déjà traité"}), 200
    payment = dict(row)

    err = _amount_invalid(evt, payment["amount"], trans_id)
    if err:
        conn.close()
        return jsonify({"error": err}), 400

    try:
        cur = conn.execute("""
            UPDATE mikrotik_payments
            SET status='confirmed', confirmed_at=datetime('now','localtime')
            WHERE id=? AND status='pending'
        """, (payment["id"],))
        if cur.rowcount == 0:
            conn.close()
            return jsonify({"status": "ignored", "reason": "déjà traité"}), 200

        device_row = conn.execute("SELECT * FROM mikrotik_devices WHERE id=?",
                                  (payment["device_id"],)).fetchone()
        if device_row:
            services.activate_device(conn, dict(device_row), payment.get("plan") or "1m")

        conn.commit()
        conn.close()
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        conn.close()
        print(f"[WEBHOOK] Erreur activation routeur : {e}", flush=True)
        return jsonify({"error": str(e)}), 500
