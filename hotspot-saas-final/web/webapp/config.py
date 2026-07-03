"""
config.py — Configuration centralisée HotspotPro

TOUS les secrets viennent des variables d'environnement (fichier .env
chargé par systemd). AUCUNE clé ne doit jamais être écrite en dur ici.
"""
import os, secrets

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SAAS_DIR = os.environ.get("HOTSPOT_SAAS_DIR", "/opt/hotspot-saas")

WEB_DB      = os.path.join(BASE_DIR, "hotspotpro.db")
VPS_IP_FILE = os.path.join(SAAS_DIR, "vps_ip.txt")

VPS_PUBLIC_IP = os.environ.get("VPS_PUBLIC_IP", "").strip()
APP_URL       = os.environ.get("APP_URL", "").strip().rstrip("/")


def _load_secret_key() -> str:
    """SECRET_KEY depuis l'env, sinon générée UNE FOIS et persistée sur
    disque — indispensable pour que les workers gunicorn partagent la
    même clé (sinon les sessions sautent aléatoirement)."""
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    path = os.path.join(BASE_DIR, ".secret_key")
    try:
        if os.path.exists(path):
            with open(path) as f:
                stored = f.read().strip()
            if stored:
                return stored
        key = secrets.token_hex(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        return key
    except OSError:
        # Disque en lecture seule : clé éphémère (sessions perdues au restart)
        return secrets.token_hex(32)


SECRET_KEY = _load_secret_key()

# ── Tarifs des abonnements ─────────────────────────────────────
PLANS = {
    "1m":  {"label": "1 mois",  "months": 1,  "price": 2000},
    "3m":  {"label": "3 mois",  "months": 3,  "price": 5000},
    "5m":  {"label": "5 mois",  "months": 5,  "price": 8000},
    "12m": {"label": "12 mois", "months": 12, "price": 15000},
}
MIKROTIK_EXTRA_PRICE = 2000   # FCFA par routeur supplémentaire

# ── FedaPay ────────────────────────────────────────────────────
FEDAPAY_SECRET_KEY  = os.environ.get("FEDAPAY_SECRET_KEY", "").strip()
FEDAPAY_PUBLIC_KEY  = os.environ.get("FEDAPAY_PUBLIC_KEY", "").strip()
FEDAPAY_WEBHOOK_KEY = os.environ.get("FEDAPAY_WEBHOOK_KEY", "").strip()
FEDAPAY_ENV         = os.environ.get("FEDAPAY_ENV", "live").strip()

# ── Email (Brevo) ──────────────────────────────────────────────
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
FROM_EMAIL    = os.environ.get("FROM_EMAIL", "noreply@hotspotpro.tg").strip()
FROM_NAME     = os.environ.get("FROM_NAME", "HotspotPro").strip()

# ── Cron / interne ─────────────────────────────────────────────
# Pas de valeur par défaut : si CRON_KEY est absente, l'endpoint
# /cron/check_expiry refuse toutes les requêtes.
CRON_KEY = os.environ.get("CRON_KEY", "").strip()

# ── Admin initial (bootstrap uniquement) ──────────────────────
# Utilisées seulement à la création de la base si aucun admin n'existe.
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def get_vps_ip() -> str:
    if VPS_PUBLIC_IP:
        return VPS_PUBLIC_IP
    if os.path.exists(VPS_IP_FILE):
        with open(VPS_IP_FILE) as f:
            ip = f.read().strip()
        if ip:
            return ip
    return "VOTRE_IP_VPS"
