#!/bin/bash
# ══════════════════════════════════════════════════════════════
# install.sh — Orchestrateur de déploiement HotspotPro.
#
# Lance les installeurs dans le BON ordre, d'un seul coup :
#   1) web/install_web.sh        Interface web + reverse proxy nginx
#   2) saas/install_saas.sh      Moteur SaaS (hub multi-tenant)
#   3) deploy/install_ops.sh     Sauvegardes, watchdog, ufw/fail2ban, HTTPS
#   4) deploy/install_wireguard.sh   Serveur WireGuard (tunnel routeurs)
#
# L'ordre ops AVANT wireguard est volontaire : install_ops active ufw, et
# install_wireguard ajoute alors sa règle ufw 51820/udp sur un pare-feu actif.
#
# Chaque installeur reste interactif (il pose ses propres questions).
#
# Usage :
#   sudo bash install.sh                 # tout, dans l'ordre
#   sudo bash install.sh web saas         # seulement ces étapes
#   sudo bash install.sh ops              # relancer une étape
#   Étapes valides : web  saas  ops  wireguard
# ══════════════════════════════════════════════════════════════
set -e

R='\033[0m'; B='\033[1m'; G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; RED='\033[0;31m'
info()  { echo -e "  ${Y}>${R} $1"; }
ok()    { echo -e "  ${G}OK${R} $1"; }
warn()  { echo -e "  ${Y}!${R} $1"; }
error() { echo -e "  ${RED}ERREUR :${R} $1"; exit 1; }
sep()   { echo -e "\n${B}══════════════════════════════════════════════════════════════${R}"; }

[ "$EUID" -ne 0 ] && error "Lancez en root : sudo bash install.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Table des étapes : mot-clé | dossier | script | libellé ──
STEP_KEYS=(web saas ops wireguard)
declare -A STEP_DIR=(   [web]="web" [saas]="saas" [ops]="deploy" [wireguard]="deploy" )
declare -A STEP_SH=(    [web]="install_web.sh" [saas]="install_saas.sh" [ops]="install_ops.sh" [wireguard]="install_wireguard.sh" )
declare -A STEP_LABEL=( [web]="Interface web + nginx" [saas]="Moteur SaaS (hub)" [ops]="Exploitation (sauvegardes, ufw, HTTPS)" [wireguard]="Serveur WireGuard" )

# ── Sélection des étapes (arguments, sinon toutes) ──
if [ "$#" -gt 0 ]; then
  SELECTED=()
  for arg in "$@"; do
    case "$arg" in
      web|saas|ops|wireguard) SELECTED+=("$arg") ;;
      *) error "Étape inconnue : '$arg' (valides : web saas ops wireguard)" ;;
    esac
  done
else
  SELECTED=("${STEP_KEYS[@]}")
fi

# ── Vérification préalable : les scripts existent ──
for key in "${SELECTED[@]}"; do
  path="$ROOT/${STEP_DIR[$key]}/${STEP_SH[$key]}"
  [ -f "$path" ] || error "Introuvable : $path (repo incomplet ?)"
done

# ── Écran d'accueil ──
clear
echo -e "${C}${B}"
echo "  ╔════════════════════════════════════════════════════════════╗"
echo "  ║          HotspotPro — Déploiement complet                  ║"
echo "  ╚════════════════════════════════════════════════════════════╝"
echo -e "${R}"
echo -e "  Étapes qui vont être lancées, dans l'ordre :"
i=1
for key in "${SELECTED[@]}"; do
  echo -e "    ${B}$i)${R} ${STEP_DIR[$key]}/${STEP_SH[$key]}  ${C}— ${STEP_LABEL[$key]}${R}"
  i=$((i+1))
done

sep
echo -e "  ${Y}${B}Avant de continuer, vérifiez (côté AWS, manuel) :${R}"
echo -e "    • Elastic IP allouée et associée à l'instance"
echo -e "    • Security Group : entrées ${B}80/tcp${R}, ${B}443/tcp${R}, ${B}51820/udp${R} ouvertes"
echo -e "    • DNS : l'enregistrement A du domaine pointe déjà vers l'Elastic IP"
echo -e "      (nécessaire pour le HTTPS de l'étape ops)"
sep
read -rp "  ▶ Tout est prêt ? Démarrer le déploiement ? [o/N] : " GO
[[ "$GO" =~ ^[oO]$ ]] || { echo "  Annulé."; exit 0; }

# ── Exécution séquentielle ──
STEP_NUM=1
TOTAL="${#SELECTED[@]}"
for key in "${SELECTED[@]}"; do
  dir="$ROOT/${STEP_DIR[$key]}"
  script="${STEP_SH[$key]}"
  sep
  echo -e "  ${B}[Étape $STEP_NUM/$TOTAL] ${STEP_LABEL[$key]}${R}"
  echo -e "  ${C}$dir/$script${R}"
  sep
  ( cd "$dir" && bash "$script" ) || error "Échec de l'étape '$key' ($script). Corrigez puis relancez : sudo bash install.sh $key"
  ok "Étape '$key' terminée"
  STEP_NUM=$((STEP_NUM+1))
done

# ── Filet de sécurité : port WireGuard ouvert dans ufw ──
if printf '%s\n' "${SELECTED[@]}" | grep -q '^wireguard$'; then
  if command -v ufw > /dev/null 2>&1; then
    ufw allow 51820/udp > /dev/null 2>&1 || true
    ok "ufw : port 51820/udp confirmé ouvert (WireGuard)"
  fi
fi

# ── Résumé final ──
sep
echo -e "  ${G}${B}Déploiement terminé.${R}"
echo
echo -e "  ${B}Vérifications :${R}"
echo -e "    systemctl status hotspot-web hotspot-tenant-hub --no-pager"
echo -e "    journalctl -u hotspot-tenant-hub -n 30 --no-pager   ${C}(aucun ModuleNotFoundError)${R}"
echo -e "    curl http://127.0.0.1:8010/health"
echo -e "    sudo wg show wg-hotspotpro"
echo
echo -e "  ${B}À faire ensuite :${R}"
echo -e "    • FedaPay : webhook -> https://VOTRE-DOMAINE/webhook/fedapay (transaction.approved)"
echo -e "    • Révoquer les anciennes clés FedaPay live exposées"
sep
