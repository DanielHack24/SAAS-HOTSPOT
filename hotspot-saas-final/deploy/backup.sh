#!/bin/bash
# ══════════════════════════════════════════════════════════════
# backup.sh — Sauvegarde HotspotPro (bases SQLite + configuration)
#
# Copie cohérente via `sqlite3 .backup` (sans arrêt de service,
# compatible WAL), archive tar.gz horodatée, rotation locale,
# envoi distant optionnel via rclone, alerte Telegram en cas d'échec.
#
# Installé par install_ops.sh dans /usr/local/bin/hotspotpro-backup
# et lancé chaque nuit par cron. Config : /etc/hotspotpro/ops.env
#
# CHIFFREMENT : si BACKUP_PASSPHRASE est défini dans ops.env (install_ops.sh
# le génère), l'archive est chiffrée (GPG, AES-256) avant d'être gardée ou
# envoyée. Sans phrase secrète, la copie DISTANTE est refusée : l'archive
# contient la clé maîtresse qui déchiffre tous les secrets de la plateforme.
# La phrase secrète n'est jamais incluse dans l'archive : conservez-la hors
# du serveur (gestionnaire de mots de passe), sinon l'archive est perdue.
#
# Restauration :
#   gpg --decrypt hotspotpro_AAAAMMJJ_HHMMSS.tar.gz.gpg > sauvegarde.tar.gz
#   tar -xzf sauvegarde.tar.gz -C /tmp/restauration
# ══════════════════════════════════════════════════════════════
set -euo pipefail

WEB_DIR="${WEB_DIR:-/opt/hotspot-saas-web}"
SAAS_DIR="${SAAS_DIR:-/opt/hotspot-saas}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/hotspotpro}"
OPS_ENV="/etc/hotspotpro/ops.env"
OPSCONFIG="/usr/local/bin/hotspotpro-opsconfig.py"
export HOTSPOT_WEBAPP_DIR="$WEB_DIR/webapp"

# 1) Repli : variables d'exploitation depuis ops.env (bootstrap)
[ -f "$OPS_ENV" ] && { set -a; source "$OPS_ENV"; set +a; }

# 2) SECRET_KEY de l'app (indispensable pour déchiffrer les secrets en base)
if [ -f "$WEB_DIR/.env" ]; then
  set -a; source "$WEB_DIR/.env"; set +a
fi

# 3) Source de vérité : la configuration réglée depuis la page admin.
#    opsconfig lit la base (valeurs déchiffrées) ; si une valeur y est
#    définie, elle prime sur ops.env.
# Interpréteur : le venv de l'app embarque cryptography (déchiffrement des
# secrets) ; le python système ne l'a pas forcément.
PY="/opt/hotspot-saas-web/venv/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3 || true)"

if [ -n "$PY" ] && [ -f "$OPSCONFIG" ]; then
  _db_get() { "$PY" "$OPSCONFIG" get "$1" 2>/dev/null; }
  V=$(_db_get admin_bot_token);       [ -n "$V" ] && ADMIN_BOT_TOKEN="$V"
  V=$(_db_get admin_chat_id);         [ -n "$V" ] && ADMIN_CHAT_ID="$V"
  V=$(_db_get backup_keep_days);      [ -n "$V" ] && BACKUP_KEEP_DAYS="$V"
  V=$(_db_get backup_dest_type);      [ -n "$V" ] && BACKUP_DEST_TYPE="$V"
  V=$(_db_get backup_host);           [ -n "$V" ] && BACKUP_HOST="$V"
  V=$(_db_get backup_smb_share);      [ -n "$V" ] && BACKUP_SMB_SHARE="$V"
  V=$(_db_get backup_sftp_port);      [ -n "$V" ] && BACKUP_SFTP_PORT="$V"
  V=$(_db_get backup_remote_path);    [ -n "$V" ] && BACKUP_REMOTE_PATH="$V"
  V=$(_db_get backup_user);           [ -n "$V" ] && BACKUP_USER="$V"
  V=$(_db_get backup_pass);           [ -n "$V" ] && BACKUP_PASS="$V"
  V=$(_db_get backup_rclone_remote);  [ -n "$V" ] && BACKUP_RCLONE_REMOTE="$V"
fi

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

STAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE="$BACKUP_DIR/hotspotpro_$STAMP.tar.gz"
WORK=$(mktemp -d "$BACKUP_DIR/tmp.XXXXXX" 2>/dev/null || mktemp -d)

telegram_alert() {
  # Ne bloque jamais la sauvegarde si Telegram est injoignable
  [ -n "${ADMIN_BOT_TOKEN:-}" ] && [ -n "${ADMIN_CHAT_ID:-}" ] || return 0
  curl -s --max-time 10 "https://api.telegram.org/bot${ADMIN_BOT_TOKEN}/sendMessage" \
       -d "chat_id=${ADMIN_CHAT_ID}" -d "text=$1" > /dev/null 2>&1 || true
}

on_error() {
  logger -t hotspotpro-backup "ECHEC de la sauvegarde (ligne $1)"
  telegram_alert "ALERTE HotspotPro : la sauvegarde nocturne a ECHOUE sur $(hostname) (ligne $1). Verifier : journalctl -t hotspotpro-backup"
  rm -rf "$WORK"
  exit 1
}
trap 'on_error $LINENO' ERR

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# ── Copie cohérente d'une base SQLite (ignore les bases absentes) ──
sq_backup() {
  local src="$1" dst="$2"
  [ -f "$src" ] || return 0
  mkdir -p "$(dirname "$dst")"
  sqlite3 "$src" ".backup '$dst'"
}

sq_backup "$WEB_DIR/webapp/hotspotpro.db" "$WORK/hotspotpro.db"
sq_backup "$SAAS_DIR/central.db"          "$WORK/central.db"

# Bases de ventes de chaque tenant
if [ -d "$SAAS_DIR/tenants" ]; then
  for tdir in "$SAAS_DIR/tenants"/*/; do
    [ -d "$tdir" ] || continue
    slug=$(basename "$tdir")
    sq_backup "$tdir/sales.db" "$WORK/tenants/$slug/sales.db"
  done
fi

# Configuration (contient les secrets : l'archive est en 600, root only)
[ -f "$WEB_DIR/.env" ]  && cp "$WEB_DIR/.env"  "$WORK/web.env"
[ -f "$SAAS_DIR/.env" ] && cp "$SAAS_DIR/.env" "$WORK/saas.env"
[ -f "$WEB_DIR/webapp/.secret_key" ] && cp "$WEB_DIR/webapp/.secret_key" "$WORK/web.secret_key"

# ── Archive (+ chiffrement) + rotation ──
tar -czf "$ARCHIVE" -C "$WORK" .
chmod 600 "$ARCHIVE"
rm -rf "$WORK"

ENCRYPTED=0
if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
  # Trousseau GPG jetable : ne touche pas à celui de root. La phrase secrète
  # passe par un descripteur de fichier, jamais en argument (visible par ps).
  GH=$(mktemp -d)
  gpg --homedir "$GH" --batch --yes --quiet --pinentry-mode loopback \
      --symmetric --cipher-algo AES256 --passphrase-fd 3 \
      -o "$ARCHIVE.gpg" "$ARCHIVE" 3<<< "$BACKUP_PASSPHRASE"
  gpgconf --homedir "$GH" --kill all > /dev/null 2>&1 || true
  rm -rf "$GH"
  chmod 600 "$ARCHIVE.gpg"
  rm -f "$ARCHIVE"
  ARCHIVE="$ARCHIVE.gpg"
  ENCRYPTED=1
fi
find "$BACKUP_DIR" -maxdepth 1 -name 'hotspotpro_*.tar.gz*' -mtime +"$KEEP_DAYS" -delete

SIZE=$(du -h "$ARCHIVE" | cut -f1)
logger -t hotspotpro-backup "Sauvegarde OK : $ARCHIVE ($SIZE)"

# ── Copie hors serveur (SMB / SFTP / remote rclone) ──
# La destination et les identifiants viennent de la page admin (ou d'ops.env
# en repli). rclone gère nativement SMB et SFTP ; le mot de passe est
# "obscurci" à la volée (rclone n'accepte pas de mot de passe en clair dans
# une chaîne de connexion), jamais écrit sur disque.
DEST_TYPE="${BACKUP_DEST_TYPE:-none}"
REMOTE_PATH="${BACKUP_REMOTE_PATH:-hotspotpro}"

remote_copy() {
  [ "$DEST_TYPE" = "none" ] && return 0

  if [ "$ENCRYPTED" != "1" ]; then
    logger -t hotspotpro-backup "Copie distante REFUSEE : archive non chiffree (BACKUP_PASSPHRASE absent de $OPS_ENV)"
    telegram_alert "ALERTE HotspotPro : copie distante des sauvegardes BLOQUEE sur $(hostname) : aucune phrase secrete de chiffrement (BACKUP_PASSPHRASE). Relancer install_ops.sh pour en generer une. La sauvegarde reste sur le VPS."
    return 1
  fi

  # Champs de connexion : aucun caractère capable de modifier la chaîne de
  # connexion rclone (guillemet, virgule, deux-points…).
  local f
  for f in "${BACKUP_HOST:-}" "${BACKUP_USER:-}" "${BACKUP_SMB_SHARE:-}" "$REMOTE_PATH" "${BACKUP_SFTP_PORT:-22}"; do
    if [[ -n "$f" && ! "$f" =~ ^[A-Za-z0-9._@/\ -]+$ ]]; then
      logger -t hotspotpro-backup "Copie distante refusee : caractere interdit dans la configuration"
      telegram_alert "ALERTE HotspotPro : copie distante refusee, la configuration de sauvegarde contient un caractere interdit. Corriger dans Administration > Configuration."
      return 1
    fi
  done

  if ! command -v rclone > /dev/null 2>&1; then
    logger -t hotspotpro-backup "Copie distante demandee ($DEST_TYPE) mais rclone est absent"
    telegram_alert "HotspotPro : copie distante impossible, rclone n'est pas installe. La sauvegarde reste sur le VPS. Relancer install_ops.sh."
    return 1
  fi

  local dest
  case "$DEST_TYPE" in
    rclone)
      [ -n "${BACKUP_RCLONE_REMOTE:-}" ] || { logger -t hotspotpro-backup "type rclone sans remote defini"; return 1; }
      dest="$BACKUP_RCLONE_REMOTE"
      ;;
    smb)
      local ob; ob=$(rclone obscure "$BACKUP_PASS")
      dest=":smb,host='${BACKUP_HOST}',user='${BACKUP_USER}',pass='${ob}':${BACKUP_SMB_SHARE}/${REMOTE_PATH}"
      ;;
    sftp)
      local ob; ob=$(rclone obscure "$BACKUP_PASS")
      dest=":sftp,host='${BACKUP_HOST}',user='${BACKUP_USER}',pass='${ob}',port='${BACKUP_SFTP_PORT:-22}':${REMOTE_PATH}"
      ;;
    *)
      logger -t hotspotpro-backup "type de destination inconnu: $DEST_TYPE"; return 1 ;;
  esac

  if rclone copy "$ARCHIVE" "$dest" --quiet --low-level-retries 2 --retries 2; then
    logger -t hotspotpro-backup "Copie distante OK ($DEST_TYPE -> ${BACKUP_HOST:-$BACKUP_RCLONE_REMOTE})"
    # Meme duree de conservation qu'en local : sans cela les copies distantes
    # s'accumuleraient sans limite, contrairement a la politique annoncee.
    rclone delete "$dest" --min-age "${KEEP_DAYS}d" --include "hotspotpro_*.tar.gz*"       --quiet --low-level-retries 1 --retries 1       || logger -t hotspotpro-backup "Purge distante (> ${KEEP_DAYS} j) incomplete"
  else
    logger -t hotspotpro-backup "ECHEC copie distante ($DEST_TYPE -> ${BACKUP_HOST:-$BACKUP_RCLONE_REMOTE})"
    telegram_alert "ALERTE HotspotPro : la sauvegarde locale a reussi mais la COPIE DISTANTE ($DEST_TYPE) a echoue sur $(hostname). Verifier l'hote, les identifiants et le reseau."
    return 1
  fi
}

remote_copy || true
