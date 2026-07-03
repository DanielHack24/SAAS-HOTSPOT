"""
webapp_core.py — Instance Flask partagée + pont vers le moteur SaaS

Les modules de routes importent `app` d'ici (et non de app.py) pour
éviter les imports circulaires. app.py reste le point d'entrée gunicorn.
"""
import os, sys

from flask import Flask

import config

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if config.APP_URL.startswith("https://"):
    app.config["SESSION_COOKIE_SECURE"] = True

# ── Pont vers le moteur SaaS (absent en dev local) ─────────────
sys.path.insert(0, os.path.join(config.SAAS_DIR, "core"))
try:
    from tenant_db import (init_central_db, add_tenant, get_tenant,        # noqa: F401
                           get_all_tenants, delete_tenant, slug_exists)    # noqa: F401
    from provisioner import (provision_tenant, stop_tenant, start_tenant,  # noqa: F401
                             restart_tenant, remove_tenant_files,          # noqa: F401
                             get_service_status, get_tenant_stats,         # noqa: F401
                             get_tenant_week_stats, get_last_activity,     # noqa: F401
                             get_hub_status, reload_hub)                   # noqa: F401
    PROVISIONER_OK = True
except ImportError:
    PROVISIONER_OK = False
    init_central_db = add_tenant = get_tenant = get_all_tenants = None
    delete_tenant = slug_exists = None
    provision_tenant = stop_tenant = start_tenant = restart_tenant = None
    remove_tenant_files = get_service_status = get_tenant_stats = None
    get_tenant_week_stats = get_last_activity = get_hub_status = reload_hub = None
