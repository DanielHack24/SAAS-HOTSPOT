#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════╗
# ║   HOTSPOT SaaS WEB — Mise à jour rapide (sans réinstaller)      ║
# ║   Usage : sudo bash update_web.sh                               ║
# ╚══════════════════════════════════════════════════════════════════╝
set -e

R="\033[0m"; B="\033[1m"; G="\033[92m"; Y="\033[93m"; C="\033[96m"; RD="\033[91m"
ok()   { echo -e "${G}  ✅  $1${R}"; }
err()  { echo -e "${RD}  ❌  $1${R}"; exit 1; }
info() { echo -e "${C}  ℹ️   $1${R}"; }
warn() { echo -e "${Y}  ⚠️   $1${R}"; }
hr()   { echo -e "\033[2m$(printf '─%.0s' {1..62})\033[0m"; }

[ "$EUID" -ne 0 ] && err "Exécuter en root : sudo bash update_web.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="/opt/hotspot-saas-web"
VENV="$WEB_DIR/venv"
ENV_FILE="$WEB_DIR/.env"

clear
echo -e "${C}${B}"
echo "  ╔════════════════════════════════════════════════════════════╗"
echo "  ║     🔄  HOTSPOT SaaS WEB — Mise à jour                    ║"
echo "  ╚════════════════════════════════════════════════════════════╝"
echo -e "${R}"

[ ! -d "$WEB_DIR" ] && err "Installation introuvable dans $WEB_DIR. Lancez d'abord install_web.sh"

# ── Mise à jour des fichiers ──────────────────────────────────────
hr
info "Copie des nouveaux fichiers…"
cp -r "$SCRIPT_DIR/webapp/templates/"*.html "$WEB_DIR/webapp/templates/"
cp "$SCRIPT_DIR/webapp/"*.py "$WEB_DIR/webapp/"
[ -f "$SCRIPT_DIR/requirements.txt" ] && cp "$SCRIPT_DIR/requirements.txt" "$WEB_DIR/requirements.txt"
if [ -d "$SCRIPT_DIR/webapp/static" ]; then
  cp -r "$SCRIPT_DIR/webapp/static/." "$WEB_DIR/webapp/static/"
fi
chown -R hotspot:hotspot "$WEB_DIR" 2>/dev/null || true
ok "Fichiers copiés"

# ── Mise à jour des dépendances Python ───────────────────────────
hr
info "Mise à jour des dépendances Python…"
"$VENV/bin/pip" install --quiet -r "$WEB_DIR/requirements.txt"
ok "Dépendances à jour"

# ── Mise à jour configuration email/paiement ─────────────────────
hr
echo -e "${B}  ⚙️  Mise à jour de la configuration (optionnel)${R}"
echo -e "  ${C}Appuyez sur Entrée pour garder la valeur actuelle.${R}"
echo

# Lire les valeurs actuelles depuis .env
get_env() { grep "^$1=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo ""; }

CURRENT_BREVO=$(get_env "BREVO_API_KEY")
CURRENT_FROM_EMAIL=$(get_env "FROM_EMAIL")
CURRENT_FROM_NAME=$(get_env "FROM_NAME")
CURRENT_FEDAPAY_SECRET=$(get_env "FEDAPAY_SECRET_KEY")
CURRENT_FEDAPAY_PUBLIC=$(get_env "FEDAPAY_PUBLIC_KEY")
CURRENT_FEDAPAY_WEBHOOK=$(get_env "FEDAPAY_WEBHOOK_KEY")
CURRENT_APP_URL=$(get_env "APP_URL")

# Email Brevo
echo -e "${B}  📧  Email (Brevo)${R}"
MASKED_BREVO="${CURRENT_BREVO:0:12}…"
[ -z "$CURRENT_BREVO" ] && MASKED_BREVO="(non configuré)"
read -rp "  ▶ Clé API Brevo [$MASKED_BREVO] : " NEW_BREVO
NEW_BREVO="${NEW_BREVO:-$CURRENT_BREVO}"

read -rp "  ▶ Email expéditeur [$CURRENT_FROM_EMAIL] : " NEW_FROM_EMAIL
NEW_FROM_EMAIL="${NEW_FROM_EMAIL:-$CURRENT_FROM_EMAIL}"

read -rp "  ▶ Nom expéditeur [$CURRENT_FROM_NAME] : " NEW_FROM_NAME
NEW_FROM_NAME="${NEW_FROM_NAME:-$CURRENT_FROM_NAME}"

echo
# FedaPay
echo -e "${B}  💳  Paiement (FedaPay)${R}"
MASKED_FP="${CURRENT_FEDAPAY_SECRET:0:12}…"
[ -z "$CURRENT_FEDAPAY_SECRET" ] && MASKED_FP="(non configuré)"
read -rp "  ▶ Clé secrète FedaPay [$MASKED_FP] : " NEW_FEDAPAY_SECRET
NEW_FEDAPAY_SECRET="${NEW_FEDAPAY_SECRET:-$CURRENT_FEDAPAY_SECRET}"

read -rp "  ▶ Clé publique FedaPay [${CURRENT_FEDAPAY_PUBLIC:0:12}…] : " NEW_FEDAPAY_PUBLIC
NEW_FEDAPAY_PUBLIC="${NEW_FEDAPAY_PUBLIC:-$CURRENT_FEDAPAY_PUBLIC}"

read -rp "  ▶ Clé webhook FedaPay [${CURRENT_FEDAPAY_WEBHOOK:0:12}…] : " NEW_FEDAPAY_WEBHOOK
NEW_FEDAPAY_WEBHOOK="${NEW_FEDAPAY_WEBHOOK:-$CURRENT_FEDAPAY_WEBHOOK}"

read -rp "  ▶ URL publique du site [$CURRENT_APP_URL] : " NEW_APP_URL
NEW_APP_URL="${NEW_APP_URL:-$CURRENT_APP_URL}"

# Détecter l'environnement FedaPay
if [[ "$NEW_FEDAPAY_SECRET" == *"sandbox"* ]]; then
  NEW_FEDAPAY_ENV="sandbox"
else
  NEW_FEDAPAY_ENV="live"
fi

# ── Mettre à jour le .env sans écraser les autres variables ───────
hr
info "Mise à jour du fichier .env…"

update_env() {
  local key="$1" val="$2"
  if grep -q "^$key=" "$ENV_FILE"; then
    sed -i "s|^$key=.*|$key=$val|" "$ENV_FILE"
  else
    echo "$key=$val" >> "$ENV_FILE"
  fi
}

update_env "BREVO_API_KEY"       "$NEW_BREVO"
update_env "FROM_EMAIL"          "$NEW_FROM_EMAIL"
update_env "FROM_NAME"           "$NEW_FROM_NAME"
update_env "FEDAPAY_SECRET_KEY"  "$NEW_FEDAPAY_SECRET"
update_env "FEDAPAY_PUBLIC_KEY"  "$NEW_FEDAPAY_PUBLIC"
update_env "FEDAPAY_WEBHOOK_KEY" "$NEW_FEDAPAY_WEBHOOK"
update_env "FEDAPAY_ENV"         "$NEW_FEDAPAY_ENV"
update_env "APP_URL"             "$NEW_APP_URL"

ok "Fichier .env mis à jour"

# ── Test Brevo si clé renseignée ─────────────────────────────────
if [ -n "$NEW_BREVO" ]; then
  info "Vérification de la clé Brevo…"
  TEST_RESULT=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "api-key: $NEW_BREVO" \
    -H "Content-Type: application/json" \
    -X GET "https://api.brevo.com/v3/account")
  if [ "$TEST_RESULT" = "200" ]; then
    ok "Clé Brevo valide ✅ — emails activés"
  else
    warn "Clé Brevo invalide (code: $TEST_RESULT) — vérifiez la clé"
  fi
fi

# ── Redémarrage du service ────────────────────────────────────────
hr
info "Redémarrage du service…"
systemctl restart hotspot-web
sleep 2
STATUS=$(systemctl is-active hotspot-web)
if [ "$STATUS" = "active" ]; then
  ok "Service redémarré (statut : $STATUS)"
else
  err "Le service n'a pas démarré. Vérifiez : journalctl -u hotspot-web -n 30"
fi

# ── Résumé ────────────────────────────────────────────────────────
hr
echo -e "${G}${B}  ✅  Mise à jour terminée !${R}"
echo
[ -n "$NEW_BREVO" ] && echo -e "  ${B}Email :${R}    ${G}Brevo actif ✅${R}" || echo -e "  ${B}Email :${R}    ${Y}Non configuré${R}"
[ -n "$NEW_FEDAPAY_SECRET" ] && echo -e "  ${B}Paiement :${R} ${G}FedaPay actif ✅ ($NEW_FEDAPAY_ENV)${R}" || echo -e "  ${B}Paiement :${R} ${Y}Non configuré${R}"
echo -e "  ${B}Logs :${R}     journalctl -u hotspot-web -f"
echo
