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

# Scripts MikroTik : passer à "1" quand un domaine + certificat TLS
# (Let's Encrypt) sont en place sur le VPS — les URLs générées dans les
# scripts RouterOS passeront alors en https (token chiffré en transit).
MIKROTIK_HTTPS = os.environ.get("MIKROTIK_HTTPS", "").strip().lower() in ("1", "true", "yes", "on")


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

# ── Tarifs et limites des abonnements ──────────────────────────
# max_tickets : plafond de tickets par génération (par lot)
# max_sellers : nombre maximum de vendeurs
# vpn         : accès VPN WireGuard vers son routeur (config à télécharger)
# max_vpn_devices : nombre de postes opérateur (configs VPN) simultanés.
#   0 = pas de VPN ; 1 = un seul appareil ; >1 = multi-appareils (12 mois).
PLANS = {
    "1m":  {"label": "1 mois",  "months": 1,  "price": 2000,
            "max_tickets": 200,  "max_sellers": 20, "vpn": False, "max_vpn_devices": 0},
    "3m":  {"label": "3 mois",  "months": 3,  "price": 5000,
            "max_tickets": 500,  "max_sellers": 30, "vpn": False, "max_vpn_devices": 0},
    "5m":  {"label": "5 mois",  "months": 5,  "price": 8000,
            "max_tickets": 700,  "max_sellers": 40, "vpn": True,  "max_vpn_devices": 1},
    "12m": {"label": "12 mois", "months": 12, "price": 15000,
            "max_tickets": 1000, "max_sellers": 50, "vpn": True,  "max_vpn_devices": 5},
}
MIKROTIK_EXTRA_PRICE = 2000   # FCFA par routeur supplémentaire


def plan_limits(plan_key: str) -> dict:
    """Limites d'un plan (repli sur le plan de base si inconnu)."""
    p = PLANS.get(plan_key) or PLANS["1m"]
    vpn = p.get("vpn", False)
    return {"max_tickets": p.get("max_tickets", 200),
            "max_sellers": p.get("max_sellers", 20),
            "vpn":         vpn,
            "max_vpn_devices": p.get("max_vpn_devices", 1 if vpn else 0)}

# ── FedaPay ────────────────────────────────────────────────────
FEDAPAY_SECRET_KEY  = os.environ.get("FEDAPAY_SECRET_KEY", "").strip()
FEDAPAY_PUBLIC_KEY  = os.environ.get("FEDAPAY_PUBLIC_KEY", "").strip()
FEDAPAY_WEBHOOK_KEY = os.environ.get("FEDAPAY_WEBHOOK_KEY", "").strip()
FEDAPAY_ENV         = os.environ.get("FEDAPAY_ENV", "live").strip()

# ── Email (Brevo) ──────────────────────────────────────────────
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip()
FROM_EMAIL    = os.environ.get("FROM_EMAIL", "noreply@hotspotpro.tg").strip()
FROM_NAME     = os.environ.get("FROM_NAME", "HotspotPro").strip()

# ── Exploitation : alertes & sauvegardes (valeurs par défaut env) ──
# Surchargeables à chaud depuis la page admin (module settings.py). Ces
# constantes ne servent que de valeur de repli si rien n'est enregistré
# en base.
ADMIN_BOT_TOKEN       = os.environ.get("ADMIN_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID         = os.environ.get("ADMIN_CHAT_ID", "").strip()
WATCHDOG_DISK_ALERT   = os.environ.get("WATCHDOG_DISK_ALERT", "90").strip()
WATCHDOG_AUTO_RESTART = os.environ.get("WATCHDOG_AUTO_RESTART", "1").strip()

# Sauvegardes : rétention locale + copie distante authentifiée (SMB/SFTP/rclone)
BACKUP_KEEP_DAYS      = os.environ.get("BACKUP_KEEP_DAYS", "14").strip()
BACKUP_DEST_TYPE      = os.environ.get("BACKUP_DEST_TYPE", "none").strip()
BACKUP_HOST           = os.environ.get("BACKUP_HOST", "").strip()
BACKUP_SMB_SHARE      = os.environ.get("BACKUP_SMB_SHARE", "").strip()
BACKUP_SFTP_PORT      = os.environ.get("BACKUP_SFTP_PORT", "22").strip()
BACKUP_REMOTE_PATH    = os.environ.get("BACKUP_REMOTE_PATH", "hotspotpro").strip()
BACKUP_USER           = os.environ.get("BACKUP_USER", "").strip()
BACKUP_PASS           = os.environ.get("BACKUP_PASS", "")
BACKUP_RCLONE_REMOTE  = os.environ.get("BACKUP_RCLONE_REMOTE", "").strip()

# ── Cron / interne ─────────────────────────────────────────────
# Pas de valeur par défaut : si CRON_KEY est absente, l'endpoint
# /cron/check_expiry refuse toutes les requêtes.
CRON_KEY = os.environ.get("CRON_KEY", "").strip()

# ── Admin initial (bootstrap uniquement) ──────────────────────
# Utilisées seulement à la création de la base si aucun admin n'existe.
ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


# ── Accès aux réglages modifiables à chaud (magasin settings.py) ──
# Import paresseux : config.py est importé partout et ne doit pas dépendre
# de settings.py au chargement (settings importe config).

def _setting(key: str) -> str:
    try:
        import settings
        return settings.get(key)
    except Exception:
        return ""


def fedapay_secret_key() -> str:  return _setting("fedapay_secret_key")
def fedapay_public_key() -> str:  return _setting("fedapay_public_key")
def fedapay_webhook_key() -> str: return _setting("fedapay_webhook_key")
def fedapay_env() -> str:         return _setting("fedapay_env") or "live"
def brevo_api_key() -> str:       return _setting("brevo_api_key")
def from_email() -> str:          return _setting("from_email")
def from_name() -> str:           return _setting("from_name")


# ── Bot de support Telegram ──
def support_bot_token() -> str:      return _setting("support_bot_token")
def support_bot_username() -> str:   return _setting("support_bot_username")
def support_chat_id() -> str:        return _setting("support_chat_id")
def support_alert_email() -> str:    return _setting("support_alert_email")
def support_webhook_secret() -> str: return _setting("support_webhook_secret")


def mikrotik_https() -> bool:
    return str(_setting("mikrotik_https")).strip().lower() in ("1", "true", "yes", "on", "oui")


def get_vps_ip() -> str:
    ip = _setting("vps_public_ip")
    if ip:
        return ip
    if os.path.exists(VPS_IP_FILE):
        with open(VPS_IP_FILE) as f:
            ip = f.read().strip()
        if ip:
            return ip
    return "VOTRE_IP_VPS"
