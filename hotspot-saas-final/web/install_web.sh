#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║      HOTSPOT SaaS WEB — Installation de l'interface web         ║
# ╚══════════════════════════════════════════════════════════════════╝
set -e

R="\033[0m"; B="\033[1m"; G="\033[92m"; Y="\033[93m"; C="\033[96m"; RD="\033[91m"
ok()   { echo -e "${G}  ✅  $1${R}"; }
err()  { echo -e "${RD}  ❌  $1${R}"; exit 1; }
info() { echo -e "${C}  ℹ️   $1${R}"; }
warn() { echo -e "${Y}  ⚠️   $1${R}"; }
hr()   { echo -e "\033[2m$(printf '─%.0s' {1..62})\033[0m"; }

[ "$EUID" -ne 0 ] && err "Exécuter en root : sudo bash install_web.sh"

clear
echo -e "${C}${B}"
echo "  ╔════════════════════════════════════════════════════════════╗"
echo "  ║     🌐  HOTSPOT SaaS WEB — Installation                   ║"
echo "  ╚════════════════════════════════════════════════════════════╝"
echo -e "${R}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="/opt/hotspot-saas-web"
SAAS_DIR="/opt/hotspot-saas"
VENV="$WEB_DIR/venv"
APP_USER="hotspot"

# ── IP publique du VPS ────────────────────────────────────────────
hr
info "Détection de l'IP publique du VPS…"
AUTO_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "")
if [ -n "$AUTO_IP" ]; then
  echo -e "  IP détectée automatiquement : ${G}${B}$AUTO_IP${R}"
  read -rp "  ▶ Confirmer ou entrer une autre IP [$AUTO_IP] : " ENTERED_IP
  VPS_PUBLIC_IP="${ENTERED_IP:-$AUTO_IP}"
else
  read -rp "  ▶ Entrez l'IP publique de ce VPS : " VPS_PUBLIC_IP
fi
ok "IP VPS configurée : $VPS_PUBLIC_IP"

# ── Dépendances système ───────────────────────────────────────────
hr
info "Installation des paquets système…"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx curl sqlite3
ok "Paquets installés"

# ── Utilisateur applicatif (non-root) ─────────────────────────────
if ! id "$APP_USER" &>/dev/null; then
  useradd --system --home-dir "$WEB_DIR" --shell /usr/sbin/nologin "$APP_USER"
  ok "Utilisateur système '$APP_USER' créé"
else
  info "Utilisateur '$APP_USER' déjà présent"
fi

# ── Dossiers ─────────────────────────────────────────────────────
info "Création de l'arborescence web…"
mkdir -p "$WEB_DIR/webapp/templates" "$WEB_DIR/webapp/static" "$SAAS_DIR"

# ── Copie de l'application ────────────────────────────────────────
cp -r "$SCRIPT_DIR/webapp/"* "$WEB_DIR/webapp/"
cp "$SCRIPT_DIR/requirements.txt" "$WEB_DIR/requirements.txt"
ok "Fichiers copiés → $WEB_DIR"

# ── Virtualenv + dépendances ──────────────────────────────────────
hr
info "Création de l'environnement Python…"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$WEB_DIR/requirements.txt"
ok "Environnement virtuel prêt"

# ── Configuration générale ────────────────────────────────────────
hr
echo -e "${B}  ⚙️  Configuration générale${R}"
echo
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
CRON_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
HUB_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")

read -rp "  ▶ Votre nom (affiché sur la page de paiement) : " OWNER_NAME_INPUT
OWNER_NAME_INPUT="${OWNER_NAME_INPUT:-HotspotPro}"

read -rp "  ▶ URL publique du site (ex: https://hotspotpro.hopto.org) : " APP_URL_INPUT
APP_URL_INPUT="${APP_URL_INPUT:-http://$VPS_PUBLIC_IP}"

# ── Compte administrateur ─────────────────────────────────────────
hr
echo -e "${B}  🔐  Compte administrateur${R}"
echo
read -rp "  ▶ Email admin [admin@hotspotpro.tg] : " ADMIN_EMAIL_INPUT
ADMIN_EMAIL_INPUT="${ADMIN_EMAIL_INPUT:-admin@hotspotpro.tg}"
read -rsp "  ▶ Mot de passe admin (vide = généré aléatoirement) : " ADMIN_PASSWORD_INPUT
echo
if [ -z "$ADMIN_PASSWORD_INPUT" ]; then
  ADMIN_PASSWORD_INPUT=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
  ADMIN_PWD_GENERATED=1
fi

# ── Configuration Email Brevo ────────────────────────────────────
hr
echo -e "${B}  📧  Configuration Email (Brevo)${R}"
echo
echo -e "  ${Y}Brevo est gratuit jusqu'à 300 emails/jour.${R}"
echo -e "  ${C}Créez un compte sur https://brevo.com puis copiez votre clé API.${R}"
echo
read -rp "  ▶ Clé API Brevo (xkeysib-...) [laisser vide pour ignorer] : " BREVO_KEY_INPUT
read -rp "  ▶ Email expéditeur (ex: noreply@mondomaine.com) : " FROM_EMAIL_INPUT
FROM_EMAIL_INPUT="${FROM_EMAIL_INPUT:-noreply@hotspotpro.tg}"
read -rp "  ▶ Nom expéditeur [HotspotPro] : " FROM_NAME_INPUT
FROM_NAME_INPUT="${FROM_NAME_INPUT:-HotspotPro}"

if [ -n "$BREVO_KEY_INPUT" ]; then
  ok "Brevo configuré — emails activés"
else
  warn "Clé Brevo vide — les emails seront désactivés (configurables plus tard)"
fi

# ── Configuration FedaPay ─────────────────────────────────────────
hr
echo -e "${B}  💳  Configuration Paiement (FedaPay)${R}"
echo
echo -e "  ${C}Créez un compte sur https://fedapay.com pour accepter les paiements en ligne.${R}"
echo -e "  ${Y}Les clés ne sont JAMAIS stockées dans le code — uniquement dans $WEB_DIR/.env${R}"
echo
read -rp "  ▶ Clé secrète FedaPay (sk_live_... ou sk_sandbox_...) : " FEDAPAY_SECRET_INPUT
read -rp "  ▶ Clé publique FedaPay (pk_live_... ou pk_sandbox_...) : " FEDAPAY_PUBLIC_INPUT
read -rp "  ▶ Clé webhook FedaPay (wh_live_...) [REQUIS pour l'activation auto] : " FEDAPAY_WEBHOOK_INPUT

if [[ "$FEDAPAY_SECRET_INPUT" == *"sandbox"* ]]; then
  FEDAPAY_ENV_INPUT="sandbox"
else
  FEDAPAY_ENV_INPUT="live"
fi

if [ -n "$FEDAPAY_SECRET_INPUT" ]; then
  ok "FedaPay configuré (environnement : $FEDAPAY_ENV_INPUT)"
  [ -z "$FEDAPAY_WEBHOOK_INPUT" ] && warn "Sans clé webhook, AUCUN paiement ne sera activé automatiquement !"
else
  warn "FedaPay non configuré — paiement manuel uniquement"
fi

# ── Écriture du .env ──────────────────────────────────────────────
hr
info "Génération du fichier de configuration…"

cat > "$WEB_DIR/.env" << ENVEOF
# ── Général ──────────────────────────────────────────────────────
HOTSPOT_SAAS_DIR=$SAAS_DIR
SECRET_KEY=$SECRET_KEY
CRON_KEY=$CRON_KEY
HUB_KEY=$HUB_KEY
VPS_PUBLIC_IP=$VPS_PUBLIC_IP
APP_URL=$APP_URL_INPUT
OWNER_NAME=$OWNER_NAME_INPUT

# ── Admin initial (utilisé uniquement si aucun admin n'existe) ────
ADMIN_EMAIL=$ADMIN_EMAIL_INPUT
ADMIN_PASSWORD=$ADMIN_PASSWORD_INPUT

# ── Email (Brevo) ─────────────────────────────────────────────────
BREVO_API_KEY=$BREVO_KEY_INPUT
FROM_EMAIL=$FROM_EMAIL_INPUT
FROM_NAME=$FROM_NAME_INPUT

# ── Paiement (FedaPay) ────────────────────────────────────────────
FEDAPAY_SECRET_KEY=$FEDAPAY_SECRET_INPUT
FEDAPAY_PUBLIC_KEY=$FEDAPAY_PUBLIC_INPUT
FEDAPAY_WEBHOOK_KEY=$FEDAPAY_WEBHOOK_INPUT
FEDAPAY_ENV=$FEDAPAY_ENV_INPUT
ENVEOF

chmod 600 "$WEB_DIR/.env"
ok "Fichier .env créé : $WEB_DIR/.env (permissions 600)"

# ── Init base de données ──────────────────────────────────────────
hr
info "Initialisation de la base de données web…"
cd "$WEB_DIR/webapp"
set -a; source "$WEB_DIR/.env"; set +a
"$VENV/bin/python3" -c "from db import init_web_db; init_web_db()"
ok "Base de données initialisée"

# ── Permissions ───────────────────────────────────────────────────
chown -R "$APP_USER:$APP_USER" "$WEB_DIR" "$SAAS_DIR"
ok "Propriétaire : $APP_USER (les services ne tournent plus en root)"

# ── Service systemd ───────────────────────────────────────────────
hr
info "Création du service systemd…"
cat > /etc/systemd/system/hotspot-web.service << SVCEOF
[Unit]
Description=HotspotPro Web Application
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$WEB_DIR/webapp
EnvironmentFile=$WEB_DIR/.env
Environment=PYTHONPATH=$SAAS_DIR/core
ExecStart=$VENV/bin/gunicorn -w 2 -b 127.0.0.1:5000 app:app
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hotspot-web
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=$WEB_DIR $SAAS_DIR

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable --now hotspot-web
ok "Service hotspot-web démarré"

# ── Nginx ─────────────────────────────────────────────────────────
hr
info "Configuration Nginx…"
cat > /etc/nginx/sites-available/hotspotpro << 'NGINXEOF'
server {
    listen 80;
    server_name _;
    client_max_body_size 2M;

    # API tenants (hub multi-tenant) — utilisée par les MikroTik
    location ^~ /t/ {
        proxy_pass         http://127.0.0.1:8010;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 30s;
    }

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 60s;
    }

    location /static/ {
        alias /opt/hotspot-saas-web/webapp/static/;
        expires 7d;
        add_header Cache-Control "public";
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/hotspotpro /etc/nginx/sites-enabled/hotspotpro
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
ok "Nginx configuré (interface web + API tenants sur /t/)"

# ── Cron expiration ───────────────────────────────────────────────
hr
info "Ajout du cron de vérification des expirations (6h chaque jour)…"
( crontab -l 2>/dev/null | grep -v 'check_expiry'; \
  echo "0 6 * * * curl -s \"http://localhost/cron/check_expiry?key=${CRON_KEY}\" > /dev/null 2>&1" \
) | crontab -
ok "Cron configuré"

# ── Test email si Brevo configuré ────────────────────────────────
if [ -n "$BREVO_KEY_INPUT" ]; then
  hr
  info "Test de l'envoi email Brevo…"
  TEST_RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "api-key: $BREVO_KEY_INPUT" \
    -H "Content-Type: application/json" \
    -X GET "https://api.brevo.com/v3/account")
  if [ "$TEST_RESULT" = "200" ]; then
    ok "Connexion Brevo vérifiée ✅"
  else
    warn "Clé Brevo invalide (code HTTP: $TEST_RESULT) — emails désactivés"
  fi
fi

# ── Résumé ────────────────────────────────────────────────────────
hr
echo -e "${G}${B}"
echo "  ✅  Installation terminée avec succès !"
echo -e "${R}"
echo -e "  ${B}Interface web :${R}   ${G}http://${VPS_PUBLIC_IP}${R}"
echo -e "  ${B}Admin :${R}           $ADMIN_EMAIL_INPUT"
if [ -n "$ADMIN_PWD_GENERATED" ]; then
  echo -e "  ${B}Mot de passe :${R}    ${Y}$ADMIN_PASSWORD_INPUT${R}  ${RD}(notez-le, affiché une seule fois)${R}"
fi
echo -e "  ${B}Config :${R}          $WEB_DIR/.env"
echo -e "  ${B}Logs :${R}            journalctl -u hotspot-web -f"
echo
echo -e "  ${B}Webhook FedaPay :${R} configurez ${G}${APP_URL_INPUT}/webhook/fedapay${R}"
echo -e "                    dans votre dashboard FedaPay (événement transaction.approved)"
echo
warn "Étape suivante : cd ../saas && sudo bash install_saas.sh"
hr
