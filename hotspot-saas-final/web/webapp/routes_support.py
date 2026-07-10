"""
routes_support.py — Webhook du bot de support Telegram + gestion admin.

- POST /telegram/webhook/<secret> : reception des updates Telegram (exempt
  CSRF, protege par le secret d'URL + l'en-tete secret Telegram).
- /admin/support : liste des tickets + activation/desactivation du bot.
"""
from flask import (request, redirect, url_for, render_template, flash, abort,
                   session)

import config
import support_bot
from webapp_core import app
from db import get_db
from security import login_required, admin_required


@app.route("/telegram/webhook/<secret>", methods=["POST"])
def telegram_webhook(secret):
    header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not support_bot.verify_webhook(secret, header):
        abort(403)
    update = request.get_json(silent=True) or {}
    support_bot.handle_update(update)
    return "", 200


@app.route("/support")
@login_required
def client_support():
    """Page opérateur : lier son compte au bot et ouvrir le support Telegram."""
    if session.get("is_admin"):
        return redirect(url_for("admin_support"))
    username = config.support_bot_username() or ""
    configured = bool(config.support_bot_token()) and bool(username)
    deep_link = ""
    if configured:
        token = support_bot.create_link_token(session["client_id"])
        deep_link = f"https://t.me/{username}?start={token}"
    return render_template("support.html", bot_username=username,
                           deep_link=deep_link, configured=configured)


@app.route("/admin/support")
@login_required
@admin_required
def admin_support():
    conn = get_db()
    tickets = [dict(r) for r in conn.execute("""
        SELECT t.*, c.full_name AS client_name, c.email AS client_email
        FROM support_tickets t LEFT JOIN clients c ON t.client_id = c.id
        ORDER BY t.id DESC LIMIT 300""").fetchall()]
    conn.close()
    open_count = sum(1 for t in tickets if t["status"] == "open")
    configured = bool(config.support_bot_token()) and bool(config.support_chat_id())
    return render_template("admin_support.html", tickets=tickets,
                           open_count=open_count, configured=configured)


@app.route("/admin/support/activate", methods=["POST"])
@login_required
@admin_required
def admin_support_activate():
    if not config.support_bot_token():
        flash("Renseignez d'abord le token du bot de support dans la configuration.", "error")
        return redirect(url_for("admin_settings"))
    base = (config.APP_URL or "").strip()
    if not base.startswith("https://"):
        flash("Le webhook Telegram exige une URL HTTPS publique (APP_URL).", "error")
        return redirect(url_for("admin_support"))
    res = support_bot.set_webhook(base)
    if res.get("ok"):
        flash("Bot de support active : le webhook Telegram est en place.", "success")
    else:
        flash(f"Echec de l'activation du bot : {res.get('description', 'reponse Telegram invalide')}.", "error")
    return redirect(url_for("admin_support"))


@app.route("/admin/support/deactivate", methods=["POST"])
@login_required
@admin_required
def admin_support_deactivate():
    res = support_bot.delete_webhook()
    if res.get("ok"):
        flash("Bot de support desactive (webhook supprime).", "success")
    else:
        flash("Impossible de supprimer le webhook Telegram.", "error")
    return redirect(url_for("admin_support"))
