#!/usr/bin/env python3
"""
opsconfig.py — Pont entre la configuration admin (base) et les scripts
d'exploitation (backup.sh en bash, watchdog.py).

L'admin règle le token du bot d'alerte, la destination rclone, la
rétention, etc. depuis la page /admin/settings ; ces valeurs vivent dans
la base web (chiffrées pour les secrets). Ce script les lit et les imprime
en clair pour que les scripts root puissent les consommer, sans dupliquer
la configuration dans un fichier séparé.

Usage :
    opsconfig.py get <clé>          -> imprime la valeur (vide si absente)
    opsconfig.py env                -> imprime KEY=VALUE pour toutes les
                                       clés d'exploitation (à sourcer)

Repli : si la base n'est pas lisible (cryptography absent, DB manquante),
retombe sur les variables d'environnement déjà chargées (ops.env).
"""
import os
import sys

WEBAPP_DIR = os.environ.get("HOTSPOT_WEBAPP_DIR", "/opt/hotspot-saas-web/webapp")
sys.path.insert(0, WEBAPP_DIR)

OPS_KEYS = [
    "admin_bot_token", "admin_chat_id",
    "watchdog_disk_alert", "watchdog_auto_restart",
    "watchdog_cpu_alert", "watchdog_steal_alert",
    "backup_keep_days", "backup_dest_type", "backup_host", "backup_smb_share",
    "backup_sftp_port", "backup_remote_path", "backup_user", "backup_pass",
    "backup_rclone_remote",
]

# Nom de la variable d'environnement de repli pour chaque clé
ENV_FALLBACK = {
    "admin_bot_token":       "ADMIN_BOT_TOKEN",
    "admin_chat_id":         "ADMIN_CHAT_ID",
    "watchdog_disk_alert":   "WATCHDOG_DISK_ALERT",
    "watchdog_auto_restart": "WATCHDOG_AUTO_RESTART",
    "watchdog_cpu_alert":    "WATCHDOG_CPU_ALERT",
    "watchdog_steal_alert":  "WATCHDOG_STEAL_ALERT",
    "backup_keep_days":      "BACKUP_KEEP_DAYS",
    "backup_dest_type":      "BACKUP_DEST_TYPE",
    "backup_host":           "BACKUP_HOST",
    "backup_smb_share":      "BACKUP_SMB_SHARE",
    "backup_sftp_port":      "BACKUP_SFTP_PORT",
    "backup_remote_path":    "BACKUP_REMOTE_PATH",
    "backup_user":           "BACKUP_USER",
    "backup_pass":           "BACKUP_PASS",
    "backup_rclone_remote":  "BACKUP_RCLONE_REMOTE",
}


def _value(key: str) -> str:
    try:
        import settings
        val = settings.get(key)
        if val not in (None, ""):
            return str(val)
    except Exception:
        pass
    return os.environ.get(ENV_FALLBACK.get(key, key.upper()), "")


def main(argv):
    if len(argv) >= 2 and argv[1] == "get" and len(argv) >= 3:
        sys.stdout.write(_value(argv[2]))
        return 0
    if len(argv) >= 2 and argv[1] == "env":
        for k in OPS_KEYS:
            v = _value(k).replace("\n", " ")
            sys.stdout.write(f"{ENV_FALLBACK[k]}={v}\n")
        return 0
    sys.stderr.write("usage: opsconfig.py get <cle> | env\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
