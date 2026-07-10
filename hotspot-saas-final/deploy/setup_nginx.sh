#!/bin/bash
# ══════════════════════════════════════════════════════════════
# setup_nginx.sh — Reverse proxy nginx de HotspotPro (source unique).
#
# (Re)génère le vhost nginx qui redirige le trafic public vers les
# services internes :
#   /t/…        -> hub multi-tenant   (127.0.0.1:8010)  ← On-Login MikroTik
#   /static/…   -> fichiers statiques (servis par nginx)
#   /…          -> interface web       (127.0.0.1:5000)  ← gunicorn
#
# Gestion du port :
#   • Sans certificat  : sert tout en HTTP sur le port 80.
#   • Avec certificat  : le port 80 redirige (301) vers 443, et le 443
#                        sert l'app en HTTPS.
#
# Idempotent : re-lançable à volonté pour réparer / régénérer la config.
#   sudo bash setup_nginx.sh [domaine]
# Sans argument, le domaine est lu depuis APP_URL (/opt/hotspot-saas-web/.env)
# ou demandé.
# ══════════════════════════════════════════════════════════════
set -e

R='\033[0m'; B='\033[1m'; G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; RED='\033[0;31m'
info()  { echo -e "  ${Y}>${R} $1"; }
ok()    { echo -e "  ${G}OK${R} $1"; }
warn()  { echo -e "  ${Y}!${R} $1"; }
error() { echo -e "  ${RED}ERREUR :${R} $1"; exit 1; }
sep()   { echo -e "\n${B}==========================================${R}"; }

[ "$EUID" -ne 0 ] && error "Lancez ce script en root : sudo bash setup_nginx.sh"

WEB_DIR="/opt/hotspot-saas-web"
WEB_ENV="$WEB_DIR/.env"
STATIC_DIR="$WEB_DIR/webapp/static"
WEB_PORT=5000
HUB_PORT=8010
SITE="/etc/nginx/sites-available/hotspotpro"
LINK="/etc/nginx/sites-enabled/hotspotpro"

sep
echo -e "  ${B}HotspotPro — Configuration du reverse proxy nginx${R}"
sep

# ── nginx présent ? ──
if ! command -v nginx > /dev/null 2>&1; then
  info "Installation de nginx…"
  apt-get update -qq && apt-get install -y -qq nginx
  ok "nginx installé"
fi

# ── Domaine : argument, sinon APP_URL du .env, sinon question ──
DOMAIN="${1:-}"
if [ -z "$DOMAIN" ] && [ -f "$WEB_ENV" ]; then
  APP_URL=$(grep '^APP_URL=' "$WEB_ENV" | cut -d'=' -f2- || true)
  DOMAIN=$(echo "$APP_URL" | sed -E 's#^https?://##; s#/.*$##')
fi
if [ -z "$DOMAIN" ]; then
  read -rp "  > Nom de domaine (vide = HTTP seul, sans domaine) : " DOMAIN
fi

# Une IP ou un vide => pas de HTTPS possible (server_name générique)
if [ -z "$DOMAIN" ] || [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  SERVER_NAME="_"
  DOMAIN=""
else
  SERVER_NAME="$DOMAIN"
fi

CERT="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
KEY="/etc/letsencrypt/live/$DOMAIN/privkey.pem"

# ── Bloc de proxy commun (réutilisé en HTTP et en HTTPS) ──
IFS= read -r -d '' PROXY_BLOCK << PROXYEOF || true
    client_max_body_size 2M;

    # API tenants (hub multi-tenant) — appelée par les routeurs MikroTik
    location ^~ /t/ {
        proxy_pass         http://127.0.0.1:$HUB_PORT;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30s;
    }

    # Fichiers statiques servis directement par nginx
    location /static/ {
        alias $STATIC_DIR/;
        expires 7d;
        add_header Cache-Control "public";
    }

    # Interface web (gunicorn)
    location / {
        proxy_pass         http://127.0.0.1:$WEB_PORT;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
    }
PROXYEOF

# ── Bloc /t/ seul (API tenant) — réutilisé sur le port 80 en mode HTTPS ──
# Les routeurs MikroTik appellent l'API en HTTP par IP : on garde /t/ joignable
# sur le port 80 même en HTTPS (RouterOS gère mal la vérification TLS), le reste
# étant redirigé vers HTTPS. L'endpoint est protégé par le token du routeur.
IFS= read -r -d '' T_BLOCK << TEOF || true
    location ^~ /t/ {
        proxy_pass         http://127.0.0.1:$HUB_PORT;
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 30s;
    }
TEOF

# ── Génération du vhost ──
if [ -n "$DOMAIN" ] && [ -f "$CERT" ]; then
  # Certificat présent : 80 -> redirection HTTPS, 443 -> app
  info "Certificat trouvé pour $DOMAIN — configuration HTTPS (80 redirige vers 443)…"
  mkdir -p /var/www/html
  cat > "$SITE" << CONFEOF
# Port 80 : API tenant (MikroTik) + challenge ACME, le reste redirigé en HTTPS
server {
    listen 80 default_server;
    server_name _;

    location /.well-known/acme-challenge/ { root /var/www/html; }

$T_BLOCK

    location / { return 301 https://$DOMAIN\$request_uri; }
}

# Port 443 : l'application en HTTPS
server {
    listen 443 ssl;
    http2 on;
    server_name $DOMAIN;

    ssl_certificate     $CERT;
    ssl_certificate_key $KEY;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

$PROXY_BLOCK
}
CONFEOF
  MODE="HTTPS (redirection 80 -> 443 active)"
else
  # Pas de certificat : tout en HTTP sur le port 80
  if [ -n "$DOMAIN" ]; then
    warn "Aucun certificat pour $DOMAIN — configuration HTTP (port 80) en attendant certbot."
  else
    info "Mode HTTP (port 80), sans domaine."
  fi
  cat > "$SITE" << CONFEOF
server {
    listen 80;
    server_name $SERVER_NAME;

$PROXY_BLOCK
}
CONFEOF
  MODE="HTTP (port 80)"
fi

# ── Activation ──
ln -sf "$SITE" "$LINK"
rm -f /etc/nginx/sites-enabled/default

if nginx -t 2>/dev/null; then
  systemctl reload nginx 2>/dev/null || systemctl restart nginx
  ok "Reverse proxy appliqué — mode : $MODE"
else
  nginx -t   # ré-affiche l'erreur exacte
  error "Config nginx invalide — rien n'a été rechargé."
fi

# ── Proposition certbot si domaine sans certificat ──
if [ -n "$DOMAIN" ] && [ ! -f "$CERT" ]; then
  sep
  echo -e "  ${C}Pour activer le HTTPS (et la redirection 80 -> 443) :${R}"
  echo -e "    ${B}sudo certbot --nginx -d $DOMAIN --redirect${R}"
  echo -e "  puis relancez ce script : ${B}sudo bash setup_nginx.sh $DOMAIN${R}"
  if command -v certbot > /dev/null 2>&1; then
    read -rp "  > Lancer certbot maintenant ? [o/N] : " RUN_CB
    if [[ "$RUN_CB" =~ ^[oO]$ ]]; then
      certbot --nginx -d "$DOMAIN" --redirect \
        --non-interactive --agree-tos --register-unsafely-without-email \
        && exec bash "$0" "$DOMAIN"   # régénère proprement avec le certificat
    fi
  fi
fi

sep
echo -e "  ${B}Routes proxifiées :${R}"
echo -e "    /t/…      -> hub    (127.0.0.1:$HUB_PORT)   [On-Login MikroTik]"
echo -e "    /static/… -> $STATIC_DIR"
echo -e "    /…        -> web    (127.0.0.1:$WEB_PORT)   [gunicorn]"
echo -e "  ${B}Tester :${R}  curl -I http://127.0.0.1/   ·   nginx -t"
sep
