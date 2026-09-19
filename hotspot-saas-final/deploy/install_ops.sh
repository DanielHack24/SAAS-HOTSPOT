#!/bin/bash
# ══════════════════════════════════════════════════════════════
# install_ops.sh — Exploitation HotspotPro : sauvegardes, monitoring,
# durcissement (ufw, fail2ban) et HTTPS optionnel (certbot).
#
# À lancer APRÈS install_web.sh et install_saas.sh :
#   cd deploy && sudo bash install_ops.sh
# ══════════════════════════════════════════════════════════════
set -e

R='\033[0m'; B='\033[1m'; G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; RED='\033[0;31m'
info()  { echo -e "  ${Y}>${R} $1"; }
ok()    { echo -e "  ${G}OK${R} $1"; }
warn()  { echo -e "  ${Y}!${R} $1"; }
error() { echo -e "  ${RED}ERREUR :${R} $1"; exit 1; }
sep()   { echo -e "\n${B}==========================================${R}"; }

[ "$EUID" -ne 0 ] && error "Lancez ce script en root : sudo bash install_ops.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="/opt/hotspot-saas-web"
SAAS_DIR="/opt/hotspot-saas"
OPS_DIR="/etc/hotspotpro"
OPS_ENV="$OPS_DIR/ops.env"

sep
echo -e "  ${B}HotspotPro — Exploitation (sauvegardes, monitoring, sécurité)${R}"
sep

[ -d "$WEB_DIR" ] || warn "$WEB_DIR introuvable — lancez d'abord install_web.sh"

# ══════════════════════════════════════════════
# 1) BOT TELEGRAM D'ALERTE ADMIN
# ══════════════════════════════════════════════
sep
echo -e "  ${B}1/5 — Alertes Telegram (panne, sauvegarde, disque)${R}"
echo
echo -e "  ${C}Créez un bot dédié aux alertes via @BotFather sur Telegram,${R}"
echo -e "  ${C}puis envoyez-lui un message et récupérez votre chat_id via${R}"
echo -e "  ${C}https://api.telegram.org/bot<TOKEN>/getUpdates${R}"
echo

EXISTING_BOT=""; EXISTING_CHAT=""
if [ -f "$OPS_ENV" ]; then
  EXISTING_BOT=$(grep '^ADMIN_BOT_TOKEN=' "$OPS_ENV" | cut -d'=' -f2- || true)
  EXISTING_CHAT=$(grep '^ADMIN_CHAT_ID=' "$OPS_ENV" | cut -d'=' -f2- || true)
fi

# Phrase secrète de chiffrement des sauvegardes : conservée si elle existe
# (sinon les anciennes archives deviendraient illisibles), générée sinon.
EXISTING_PASSPHRASE=""
[ -f "$OPS_ENV" ] && EXISTING_PASSPHRASE=$(grep '^BACKUP_PASSPHRASE=' "$OPS_ENV" | cut -d'=' -f2- || true)
NEW_PASSPHRASE=0
if [ -n "$EXISTING_PASSPHRASE" ]; then
  BACKUP_PASSPHRASE="$EXISTING_PASSPHRASE"
else
  BACKUP_PASSPHRASE=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  NEW_PASSPHRASE=1
fi

read -rp "  > Token du bot d'alerte [${EXISTING_BOT:-vide = alertes désactivées}] : " ADMIN_BOT_TOKEN
ADMIN_BOT_TOKEN="${ADMIN_BOT_TOKEN:-$EXISTING_BOT}"
read -rp "  > Chat ID admin [${EXISTING_CHAT:-vide}] : " ADMIN_CHAT_ID
ADMIN_CHAT_ID="${ADMIN_CHAT_ID:-$EXISTING_CHAT}"

echo
echo -e "  ${C}La destination des sauvegardes (SMB/TrueNAS, SFTP, cloud) et ses"
echo -e "  identifiants se configurent ensuite dans ${G}Administration > Configuration${C}.${R}"

mkdir -p "$OPS_DIR" /var/lib/hotspotpro
cat > "$OPS_ENV" << ENVEOF
# Amorçage exploitation HotspotPro (repli avant configuration via la page admin)
ADMIN_BOT_TOKEN=$ADMIN_BOT_TOKEN
ADMIN_CHAT_ID=$ADMIN_CHAT_ID
BACKUP_KEEP_DAYS=14
BACKUP_DEST_TYPE=none
WATCHDOG_AUTO_RESTART=1
WATCHDOG_DISK_ALERT=90
WATCHDOG_CPU_ALERT=85
WATCHDOG_STEAL_ALERT=30
# Chiffrement des sauvegardes (à conserver AUSSI hors du serveur)
BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE
ENVEOF
chmod 600 "$OPS_ENV"
ok "Configuration écrite : $OPS_ENV"

if [ "$NEW_PASSPHRASE" = "1" ]; then
  sep
  echo -e "  ${Y}PHRASE SECRÈTE DES SAUVEGARDES (nouvelle) :${R}"
  echo
  echo -e "      ${B}$BACKUP_PASSPHRASE${R}"
  echo
  echo -e "  ${Y}Copiez-la MAINTENANT dans un gestionnaire de mots de passe.${R}"
  echo -e "  ${Y}Sans elle, les sauvegardes chiffrées sont IRRÉCUPÉRABLES si le${R}"
  echo -e "  ${Y}serveur est perdu. Elle n'est jamais envoyée avec les sauvegardes.${R}"
  sep
  read -rp "  > Appuyez sur Entrée une fois la phrase secrète sauvegardée… " _
fi

if [ -n "$ADMIN_BOT_TOKEN" ] && [ -n "$ADMIN_CHAT_ID" ]; then
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 \
    "https://api.telegram.org/bot${ADMIN_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${ADMIN_CHAT_ID}" \
    -d "text=HotspotPro : alertes de supervision activees sur $(hostname).")
  if [ "$HTTP" = "200" ]; then
    ok "Message de test Telegram envoyé"
  else
    warn "Envoi Telegram échoué (HTTP $HTTP) — vérifiez token et chat_id"
  fi
else
  warn "Alertes Telegram non configurées (le watchdog loguera seulement dans journald)"
fi

# ══════════════════════════════════════════════
# 2) SAUVEGARDES NOCTURNES
# ══════════════════════════════════════════════
sep
echo -e "  ${B}2/5 — Sauvegardes automatiques${R}"
apt-get install -y -qq sqlite3 curl gnupg > /dev/null

# rclone : requis pour la copie distante SMB/SFTP/cloud. La version des
# dépôts Debian est parfois trop ancienne pour le backend SMB ; on installe
# la dernière depuis le script officiel si rclone est absent.
if ! command -v rclone > /dev/null 2>&1; then
  info "Installation de rclone (copie distante SMB / SFTP / cloud)…"
  curl -s https://rclone.org/install.sh | bash > /dev/null 2>&1 \
    && ok "rclone installé ($(rclone version 2>/dev/null | head -1))" \
    || warn "Installation de rclone échouée — la copie distante sera indisponible jusqu'à 'curl https://rclone.org/install.sh | sudo bash'"
else
  ok "rclone déjà présent ($(rclone version 2>/dev/null | head -1))"
fi

install -m 755 "$SCRIPT_DIR/opsconfig.py" /usr/local/bin/hotspotpro-opsconfig.py
install -m 700 "$SCRIPT_DIR/backup.sh" /usr/local/bin/hotspotpro-backup
( crontab -l 2>/dev/null | grep -v 'hotspotpro-backup'; \
  echo "30 3 * * * /usr/local/bin/hotspotpro-backup" ) | crontab -
ok "Sauvegarde chaque nuit à 03h30 -> /var/backups/hotspotpro (rotation 14 jours)"

info "Première sauvegarde de test…"
if /usr/local/bin/hotspotpro-backup; then
  ok "Sauvegarde de test réussie : $(ls -1t /var/backups/hotspotpro/hotspotpro_*.tar.gz 2>/dev/null | head -1)"
else
  warn "La sauvegarde de test a échoué — vérifiez journalctl -t hotspotpro-backup"
fi
warn "Copie hors serveur : à activer dans Administration > Configuration > Sauvegardes"
warn "(SMB/TrueNAS, SFTP ou cloud). Sans elle, une panne disque du VPS emporte les sauvegardes."

# ══════════════════════════════════════════════
# 3) WATCHDOG (supervision toutes les 2 minutes)
# ══════════════════════════════════════════════
sep
echo -e "  ${B}3/5 — Watchdog des services${R}"
install -m 755 "$SCRIPT_DIR/watchdog.py" /usr/local/bin/hotspotpro-watchdog.py

# Interpréteur du venv (embarque cryptography pour déchiffrer les secrets
# réglés depuis la page admin) ; repli sur le python système.
WD_PY="$WEB_DIR/venv/bin/python3"
[ -x "$WD_PY" ] || WD_PY="/usr/bin/python3"

cat > /etc/systemd/system/hotspotpro-watchdog.service << SVCEOF
[Unit]
Description=HotspotPro watchdog (supervision web + hub + disque)

[Service]
Type=oneshot
Environment=HOTSPOT_WEBAPP_DIR=$WEB_DIR/webapp
EnvironmentFile=-/etc/hotspotpro/ops.env
EnvironmentFile=-$WEB_DIR/.env
ExecStart=$WD_PY /usr/local/bin/hotspotpro-watchdog.py
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hotspotpro-watchdog
SVCEOF

cat > /etc/systemd/system/hotspotpro-watchdog.timer << 'TMREOF'
[Unit]
Description=Lance le watchdog HotspotPro toutes les 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
TMREOF

systemctl daemon-reload
systemctl enable --now hotspotpro-watchdog.timer
ok "Watchdog actif (toutes les 2 min) : redémarre les services tombés et alerte par Telegram"
info "Complément externe conseillé : compte gratuit UptimeRobot qui surveille http://VOTRE_IP/healthz"

# ══════════════════════════════════════════════
# 4) PARE-FEU + FAIL2BAN
# ══════════════════════════════════════════════
sep
echo -e "  ${B}4/5 — Pare-feu (ufw) et fail2ban${R}"
apt-get install -y -qq ufw fail2ban > /dev/null

ufw allow OpenSSH > /dev/null
ufw allow 80/tcp  > /dev/null
ufw allow 443/tcp > /dev/null
ufw --force enable > /dev/null
ok "ufw actif : seuls SSH, 80 et 443 sont ouverts (les ports 5000/8010 restent internes)"

cat > /etc/fail2ban/jail.local << 'F2BEOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5

[sshd]
enabled = true
F2BEOF
systemctl enable --now fail2ban > /dev/null 2>&1
systemctl restart fail2ban
ok "fail2ban actif : bannit 1h les IP qui brutalisent SSH"

# ══════════════════════════════════════════════
# 5) HTTPS (optionnel — nécessite un nom de domaine)
# ══════════════════════════════════════════════
sep
echo -e "  ${B}5/5 — HTTPS avec Let's Encrypt (optionnel)${R}"
echo
echo -e "  ${C}Nécessite un domaine dont l'enregistrement A pointe déjà vers ce VPS.${R}"
read -rp "  > Nom de domaine (ex: hotspotpro.tg) [vide = passer] : " DOMAIN

if [ -n "$DOMAIN" ]; then
  apt-get install -y -qq certbot python3-certbot-nginx > /dev/null
  NGINX_CONF="/etc/nginx/sites-available/hotspotpro"
  if [ -f "$NGINX_CONF" ]; then
    sed -i "s|server_name _;|server_name $DOMAIN;|" "$NGINX_CONF"
    nginx -t && systemctl reload nginx
  fi
  if certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
    ok "Certificat installé, renouvellement automatique activé"
    WEB_ENV="$WEB_DIR/.env"
    if [ -f "$WEB_ENV" ]; then
      sed -i "s|^APP_URL=.*|APP_URL=https://$DOMAIN|" "$WEB_ENV"
      sed -i "s|^VPS_PUBLIC_IP=.*|VPS_PUBLIC_IP=$DOMAIN|" "$WEB_ENV"
      grep -q '^MIKROTIK_HTTPS=' "$WEB_ENV" \
        && sed -i "s|^MIKROTIK_HTTPS=.*|MIKROTIK_HTTPS=1|" "$WEB_ENV" \
        || echo "MIKROTIK_HTTPS=1" >> "$WEB_ENV"
      echo "$DOMAIN" > "$SAAS_DIR/vps_ip.txt" 2>/dev/null || true
      systemctl restart hotspot-web 2>/dev/null || true
      ok "APP_URL, VPS_PUBLIC_IP et MIKROTIK_HTTPS mis à jour — scripts MikroTik désormais en https://$DOMAIN"
      warn "Les clients EXISTANTS doivent recopier leur script MikroTik depuis leur dashboard."
    fi
  else
    warn "certbot a échoué — vérifiez que le DNS pointe bien vers ce VPS puis relancez :"
    warn "certbot --nginx -d $DOMAIN"
  fi
else
  warn "HTTPS ignoré. Relancez ce script quand vous aurez un domaine."
fi

# ══════════════════════════════════════════════
# RÉSUMÉ
# ══════════════════════════════════════════════
sep
echo -e "  ${G}${B}Exploitation configurée.${R}"
echo
echo -e "  ${B}Sauvegardes :${R}   /var/backups/hotspotpro (03h30, rotation 14 j)"
echo -e "                  restaurer : tar -xzf hotspotpro_<date>.tar.gz puis remettre les .db"
echo -e "  ${B}Watchdog :${R}      systemctl list-timers hotspotpro-watchdog.timer"
echo -e "                  logs : journalctl -t hotspotpro-watchdog -f"
echo -e "  ${B}Pare-feu :${R}      ufw status"
echo -e "  ${B}Fail2ban :${R}      fail2ban-client status sshd"
echo -e "  ${B}Config ops :${R}    $OPS_ENV (valeurs de demarrage)"
echo
echo -e "  ${Y}Token d'alerte, destination des sauvegardes et seuils sont"
echo -e "  modifiables a chaud depuis ${G}Administration > Configuration${Y} ;"
echo -e "  ces valeurs priment sur le fichier ci-dessus, sans redemarrage.${R}"
sep
