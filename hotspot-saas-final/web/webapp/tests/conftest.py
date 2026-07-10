"""
conftest.py — Fixtures pytest HotspotPro

La config est patchée AVANT l'import de l'app : base SQLite temporaire,
clé webhook de test, pas de clé Brevo (les emails sont silencieux) ni de
bot Telegram (aucun appel réseau pendant les tests).
"""
import os
import sys
import tempfile

import pytest

WEBAPP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, WEBAPP_DIR)

# Moteur SaaS partagé (module tickets, tenant_db) pour les tests d'attribution
SAAS_CORE = os.path.abspath(os.path.join(WEBAPP_DIR, "..", "..", "saas", "core"))
if os.path.isdir(SAAS_CORE):
    sys.path.insert(0, SAAS_CORE)

import config  # noqa: E402

# Base jetable pour l'init exécutée à l'import de app.py ;
# chaque test reçoit ensuite sa propre base via la fixture `db`.
config.WEB_DB = os.path.join(tempfile.gettempdir(), "hotspotpro_test_bootstrap.db")
config.FEDAPAY_WEBHOOK_KEY = "whsec_test"
config.BREVO_API_KEY = ""
config.CRON_KEY = "cron_test_key"
config.ADMIN_EMAIL = "admin@test.local"
config.ADMIN_PASSWORD = "admin-test-pwd"

from app import app as flask_app          # noqa: E402
from db import init_web_db                # noqa: E402


@pytest.fixture()
def db(tmp_path):
    """Base web neuve et isolée pour chaque test."""
    config.WEB_DB = str(tmp_path / "web.db")
    init_web_db()
    yield config.WEB_DB


@pytest.fixture()
def client(db):
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c
