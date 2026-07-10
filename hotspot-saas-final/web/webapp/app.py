#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HotspotPro SaaS — Point d'entrée de l'application web

Le code est découpé en modules :
  config.py            configuration (env), tarifs
  db.py                base web + helpers métier
  security.py          mots de passe, CSRF, rate limiting
  emails.py            emails transactionnels (Brevo)
  fedapay.py           client FedaPay + vérification webhook
  mikrotik_scripts.py  génération des scripts RouterOS
  services.py          activation abonnements/routeurs (webhook + admin)
  routes_*.py          les routes Flask
  webapp_core.py       instance Flask + pont vers le moteur SaaS

Lancement : gunicorn app:app  (ou python3 app.py en dev)
"""
from flask import render_template, session, url_for

import config
from webapp_core import app, PROVISIONER_OK, init_central_db
from db import init_web_db
from security import validate_csrf, csrf_field, csrf_token

# ── Routes (l'import suffit : chaque module s'enregistre sur `app`) ──
import routes_auth     # noqa: F401, E402
import routes_client   # noqa: F401, E402
import routes_sellers  # noqa: F401, E402
import routes_billing  # noqa: F401, E402
import routes_admin    # noqa: F401, E402
import routes_api      # noqa: F401, E402
import routes_support  # noqa: F401, E402


# ═══════════════════════════════════════════════
# ACCUEIL / ERREURS / HOOKS
# ═══════════════════════════════════════════════

@app.route("/")
def index():
    # Modèle 3D rotatif du routeur : affiché si static/3d/router.glb existe,
    # sinon la landing retombe sur l'image PNG animée.
    import os
    glb = os.path.join(app.static_folder or "static", "3d", "router.glb")
    return render_template("landing.html", plans=config.PLANS,
                           has_router_3d=os.path.exists(glb),
                           support_bot_username=config.support_bot_username())


@app.route("/legal/<slug>")
def legal_page(slug):
    import legal_content
    page = legal_content.PAGES.get(slug)
    if not page:
        return render_template("404.html"), 404
    return render_template("legal.html", page=page,
                           updated=legal_content.UPDATED,
                           contact=legal_content.CONTACT_EMAIL)


@app.route("/healthz")
def healthz():
    """Sonde de supervision (watchdog, UptimeRobot) : vérifie que
    l'application répond ET que la base est accessible."""
    from db import get_db
    try:
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}, 500


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("404.html", error=True), 500


@app.before_request
def _csrf_protect():
    return validate_csrf()


@app.context_processor
def _inject_helpers():
    def logo_url():
        if "client_id" not in session:
            return url_for("index")
        if session.get("is_admin"):
            return url_for("admin_dashboard")
        return url_for("dashboard")
    return dict(logo_url=logo_url, csrf_field=csrf_field, csrf_token=csrf_token)


# ═══════════════════════════════════════════════
# INIT AU DÉMARRAGE (gunicorn + direct)
# ═══════════════════════════════════════════════

try:
    init_web_db()
    if PROVISIONER_OK:
        try:
            init_central_db()
        except Exception:
            pass
    print("[HotspotPro] Base de donnees initialisee", flush=True)
except Exception as e:
    print(f"[HotspotPro] Erreur init DB : {e}", flush=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
