"""
settings.py — Configuration modifiable à chaud depuis la page admin.

Principe : une table `settings` (clé -> valeur) surcharge les variables
d'environnement. Si une clé n'a pas de valeur en base, on retombe sur la
valeur d'environnement (chargée dans config.py au démarrage). L'admin peut
donc tout régler depuis l'interface sans toucher au fichier .env ni
redémarrer les services.

Secrets au repos : les champs sensibles (clés FedaPay, Brevo, token du bot
d'alerte) sont chiffrés en base avec Fernet. La clé de chiffrement est
dérivée de SECRET_KEY (qui reste, elle, uniquement dans .env) : une fuite
de la seule base — sans le .env — ne révèle aucun secret.

Ce module est aussi utilisé hors application Flask (watchdog, backup) via
opsconfig.py : il ne dépend donc que de config.py et db.py.
"""
import base64
import hashlib
import threading

import config
from db import get_db

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_OK = True
except Exception:                       # pragma: no cover
    _CRYPTO_OK = False
    InvalidToken = Exception

_ENC_PREFIX = "enc:"
_SALT = b"hotspotpro-settings-v1"
_fernet = None
_fernet_lock = threading.Lock()


# ═══════════════════════════════════════════════
# REGISTRE DES PARAMÈTRES
# ═══════════════════════════════════════════════
# Chaque champ : clé, libellé, groupe, type d'input, secret (chiffré +
# masqué dans l'UI), env_attr (attribut de config servant de valeur par
# défaut). `options` pour les select.

FIELDS = [
    # ── Paiement FedaPay ──
    {"key": "fedapay_env", "label": "Environnement", "group": "fedapay",
     "type": "select", "options": [("live", "Production (live)"),
                                    ("sandbox", "Test (sandbox)")],
     "secret": False, "env_attr": "FEDAPAY_ENV",
     "help": "« live » pour encaisser réellement, « sandbox » pour tester."},
    {"key": "fedapay_public_key", "label": "Clé publique", "group": "fedapay",
     "type": "text", "secret": False, "env_attr": "FEDAPAY_PUBLIC_KEY",
     "help": "pk_live_… ou pk_sandbox_…"},
    {"key": "fedapay_secret_key", "label": "Clé secrète", "group": "fedapay",
     "type": "password", "secret": True, "env_attr": "FEDAPAY_SECRET_KEY",
     "help": "sk_live_… ou sk_sandbox_… — jamais affichée."},
    {"key": "fedapay_webhook_key", "label": "Clé webhook", "group": "fedapay",
     "type": "password", "secret": True, "env_attr": "FEDAPAY_WEBHOOK_KEY",
     "help": "wh_… — indispensable à l'activation automatique des paiements."},

    # ── Email Brevo ──
    {"key": "brevo_api_key", "label": "Clé API Brevo", "group": "brevo",
     "type": "password", "secret": True, "env_attr": "BREVO_API_KEY",
     "help": "xkeysib-… Laisser vide désactive les emails."},
    {"key": "from_email", "label": "Email expéditeur", "group": "brevo",
     "type": "text", "secret": False, "env_attr": "FROM_EMAIL",
     "help": "Adresse affichée comme expéditeur (ex: noreply@mondomaine.com)."},
    {"key": "from_name", "label": "Nom expéditeur", "group": "brevo",
     "type": "text", "secret": False, "env_attr": "FROM_NAME",
     "help": "Nom affiché dans la boîte du destinataire."},

    # ── Réseau / scripts MikroTik ──
    {"key": "vps_public_ip", "label": "Domaine ou IP publique", "group": "network",
     "type": "text", "secret": False, "env_attr": "VPS_PUBLIC_IP",
     "help": "Hôte inséré dans les scripts MikroTik (ex: api.mondomaine.com). "
             "Un domaine évite de tout reconfigurer si l'IP change."},
    {"key": "mikrotik_https", "label": "Scripts en HTTPS", "group": "network",
     "type": "bool", "secret": False, "env_attr": "MIKROTIK_HTTPS",
     "help": "À activer une fois un certificat TLS en place (token chiffré en transit)."},

    # ── Alertes & supervision ──
    {"key": "admin_bot_token", "label": "Token du bot d'alerte", "group": "ops",
     "type": "password", "secret": True, "env_attr": "ADMIN_BOT_TOKEN",
     "help": "Bot Telegram (via @BotFather) qui vous alerte des pannes et échecs de sauvegarde."},
    {"key": "admin_chat_id", "label": "Chat ID admin", "group": "ops",
     "type": "text", "secret": False, "env_attr": "ADMIN_CHAT_ID",
     "help": "Votre identifiant de conversation Telegram (destinataire des alertes)."},
    # ── Bot de support Telegram ──
    {"key": "support_bot_token", "label": "Token du bot de support", "group": "support",
     "type": "password", "secret": True, "env_attr": "SUPPORT_BOT_TOKEN",
     "help": "Bot Telegram dedie au support des operateurs (via @BotFather). "
             "Une fois renseigne, activez le webhook depuis la page Support."},
    {"key": "support_bot_username", "label": "Nom d'utilisateur du bot (sans @)", "group": "support",
     "type": "text", "secret": False, "env_attr": "SUPPORT_BOT_USERNAME",
     "help": "Ex: hotspotprosupport_bot. Sert au bouton \"Lier mon compte\" "
             "affiche aux operateurs (lien t.me/<nom>)."},
    {"key": "support_chat_id", "label": "Chat ID des alertes support", "group": "support",
     "type": "text", "secret": False, "env_attr": "SUPPORT_CHAT_ID",
     "help": "Votre chat Telegram : les nouveaux tickets y arrivent. Repondez "
             "au message d'un ticket pour repondre a l'operateur."},
    {"key": "support_alert_email", "label": "E-mail des alertes support", "group": "support",
     "type": "text", "secret": False, "env_attr": "SUPPORT_ALERT_EMAIL",
     "help": "Adresse qui recoit un e-mail a chaque nouveau ticket. Vide = "
             "e-mail du compte administrateur."},

    {"key": "watchdog_disk_alert", "label": "Seuil d'alerte disque (%)", "group": "ops",
     "type": "number", "secret": False, "env_attr": "WATCHDOG_DISK_ALERT",
     "help": "Alerte Telegram quand le disque dépasse ce pourcentage."},
    {"key": "watchdog_cpu_alert", "label": "Seuil d'alerte processeur (%)", "group": "ops",
     "type": "number", "secret": False, "env_attr": "WATCHDOG_CPU_ALERT",
     "help": "Alerte Telegram quand le processeur reste occupé au-delà de ce "
             "pourcentage pendant environ 6 minutes."},
    {"key": "watchdog_steal_alert", "label": "Seuil d'alerte processeur bridé (%)", "group": "ops",
     "type": "number", "secret": False, "env_attr": "WATCHDOG_STEAL_ALERT",
     "help": "Part du processeur retenue par l'hébergeur (crédits CPU AWS "
             "épuisés). Au-delà, alerte : le serveur est bridé."},
    {"key": "watchdog_auto_restart", "label": "Redémarrage auto des services", "group": "ops",
     "type": "bool", "secret": False, "env_attr": "WATCHDOG_AUTO_RESTART",
     "help": "Le watchdog tente de relancer un service tombé avant d'alerter."},

    # ── Sauvegardes (locale + copie distante authentifiée) ──
    {"key": "backup_keep_days", "label": "Rétention locale (jours)", "group": "backup",
     "type": "number", "secret": False, "env_attr": "BACKUP_KEEP_DAYS",
     "help": "Nombre de jours de sauvegardes conservées sur le VPS."},
    {"key": "backup_dest_type", "label": "Copie distante", "group": "backup",
     "type": "select", "secret": False, "env_attr": "BACKUP_DEST_TYPE",
     "options": [("none", "Locale uniquement (aucune copie distante)"),
                 ("sftp", "SFTP / SSH — recommandé à distance"),
                 ("smb",  "Partage SMB / TrueNAS (réseau local ou VPN)"),
                 ("rclone", "Remote rclone déjà configuré (avancé)")],
     "help": "Où envoyer une copie hors du VPS. Le SMB brut ne doit PAS traverser "
             "Internet : à distance, préférez le SFTP, ou un SMB via VPN."},
    {"key": "backup_host", "label": "Hôte du serveur (IP ou nom)", "group": "backup",
     "type": "text", "secret": False, "env_attr": "BACKUP_HOST",
     "help": "Adresse du NAS / TrueNAS (ex: 192.168.1.20 ou nas.mondomaine.com). SMB et SFTP."},
    {"key": "backup_smb_share", "label": "Nom du partage (SMB)", "group": "backup",
     "type": "text", "secret": False, "env_attr": "BACKUP_SMB_SHARE",
     "help": "Nom du partage SMB sur le NAS (ex: backups). Uniquement pour SMB."},
    {"key": "backup_sftp_port", "label": "Port SFTP", "group": "backup",
     "type": "number", "secret": False, "env_attr": "BACKUP_SFTP_PORT",
     "help": "Port SSH/SFTP du serveur (par défaut 22). Uniquement pour SFTP."},
    {"key": "backup_remote_path", "label": "Dossier distant", "group": "backup",
     "type": "text", "secret": False, "env_attr": "BACKUP_REMOTE_PATH",
     "help": "Sous-dossier de destination (ex: hotspotpro). Créé au besoin."},
    {"key": "backup_user", "label": "Nom d'utilisateur", "group": "backup",
     "type": "text", "secret": False, "env_attr": "BACKUP_USER",
     "help": "Compte du NAS ayant le droit d'écriture sur le partage/dossier."},
    {"key": "backup_pass", "label": "Mot de passe", "group": "backup",
     "type": "password", "secret": True, "env_attr": "BACKUP_PASS",
     "help": "Mot de passe du compte — chiffré en base, jamais réaffiché."},
    {"key": "backup_rclone_remote", "label": "Remote rclone (avancé)", "group": "backup",
     "type": "text", "secret": False, "env_attr": "BACKUP_RCLONE_REMOTE",
     "help": "Si « Copie distante » = rclone : nom d'un remote configuré via rclone config "
             "(ex: b2:hotspotpro-backups, gdrive:backups)."},
]

FIELDS_BY_KEY = {f["key"]: f for f in FIELDS}

GROUPS = [
    ("fedapay", "Paiement (FedaPay)"),
    ("brevo",   "Email (Brevo)"),
    ("network", "Réseau & scripts MikroTik"),
    ("support", "Bot de support Telegram"),
    ("ops",     "Alertes & supervision"),
    ("backup",  "Sauvegardes"),
]

_TRUE = ("1", "true", "yes", "on", "oui")


# ═══════════════════════════════════════════════
# CHIFFREMENT
# ═══════════════════════════════════════════════

def _get_fernet():
    global _fernet
    if not _CRYPTO_OK:
        return None
    if _fernet is None:
        with _fernet_lock:
            if _fernet is None:
                dk = hashlib.pbkdf2_hmac("sha256", config.SECRET_KEY.encode(),
                                         _SALT, 200_000, dklen=32)
                _fernet = Fernet(base64.urlsafe_b64encode(dk))
    return _fernet


def _encrypt(plaintext: str) -> str:
    f = _get_fernet()
    if not f or plaintext == "":
        return plaintext
    return _ENC_PREFIX + f.encrypt(plaintext.encode()).decode()


def _decrypt(stored: str) -> str:
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    f = _get_fernet()
    if not f:
        return ""
    try:
        return f.decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""


# ═══════════════════════════════════════════════
# LECTURE / ÉCRITURE
# ═══════════════════════════════════════════════

def _default(key: str) -> str:
    field = FIELDS_BY_KEY.get(key)
    if not field:
        return ""
    return str(getattr(config, field["env_attr"], "") or "")


def _raw_db_value(key: str):
    """Valeur brute stockée en base (chiffrée si secret), ou None."""
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
    except Exception:
        return None
    if row is None:
        return None
    return row["value"]


def get(key: str) -> str:
    """Valeur effective (base si définie, sinon environnement), déchiffrée."""
    raw = _raw_db_value(key)
    if raw is None or raw == "":
        return _default(key)
    return _decrypt(raw)


def get_bool(key: str) -> bool:
    return str(get(key)).strip().lower() in _TRUE


def is_set_in_db(key: str) -> bool:
    """Vrai si la clé a une valeur explicitement enregistrée en base."""
    raw = _raw_db_value(key)
    return raw is not None and raw != ""


def set_value(conn, key: str, value: str):
    field = FIELDS_BY_KEY.get(key)
    stored = _encrypt(value) if (field and field["secret"]) else value
    conn.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                       updated_at=excluded.updated_at
    """, (key, stored))


def save_from_form(form) -> int:
    """Enregistre les champs postés depuis la page admin.

    Règle pour les secrets : un champ laissé vide n'écrase PAS la valeur
    existante (on ne renvoie jamais le secret au navigateur). Les cases à
    cocher absentes valent 0.
    """
    conn = get_db()
    changed = 0
    for field in FIELDS:
        key = field["key"]
        if field["type"] == "bool":
            value = "1" if form.get(key) else "0"
        else:
            if key not in form:
                continue
            value = form.get(key, "").strip()
            if field["secret"] and value == "":
                continue   # inchangé
        set_value(conn, key, value)
        changed += 1
    conn.commit()
    conn.close()
    return changed


def view_model() -> list[dict]:
    """Données prêtes pour le template : par groupe, secrets masqués."""
    out = []
    for gkey, glabel in GROUPS:
        fields = []
        for f in FIELDS:
            if f["group"] != gkey:
                continue
            item = dict(f)
            if f["secret"]:
                item["value"] = ""
                item["configured"] = is_set_in_db(f["key"]) or bool(_default(f["key"]))
            else:
                item["value"] = get(f["key"])
                item["configured"] = None
            fields.append(item)
        out.append({"key": gkey, "label": glabel, "fields": fields})
    return out
