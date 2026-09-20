#!/bin/bash
# ══════════════════════════════════════════════════════════════
# install.sh — Installation complète de HotspotPro sur un serveur neuf.
#
# Sur un VPS Ubuntu fraîchement créé :
#
#   sudo apt update && sudo apt install -y git
#   git clone https://github.com/DanielHack24/SAAS-HOTSPOT.git
#   cd SAAS-HOTSPOT
#   sudo bash install.sh
#
# Ce script ne fait que déléguer au véritable installateur, qui enchaîne
# dans l'ordre : interface web + nginx, moteur SaaS, exploitation
# (sauvegardes, pare-feu, HTTPS) puis serveur WireGuard.
#
# Pour ne répondre à AUCUNE question, préparez d'abord vos réglages :
#   cp hotspot-saas-final/hotspotpro.conf.example hotspot-saas-final/hotspotpro.conf
#   nano hotspot-saas-final/hotspotpro.conf
#   sudo bash install.sh --auto
#
# Les arguments sont transmis tels quels (--auto, web, saas, ops, wireguard).
# ══════════════════════════════════════════════════════════════
set -e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$ROOT/hotspot-saas-final/install.sh"

if [ "$EUID" -ne 0 ]; then
  echo "  Lancez en root : sudo bash install.sh" >&2
  exit 1
fi

if [ ! -f "$TARGET" ]; then
  echo "  Introuvable : $TARGET (dépôt incomplet ?)" >&2
  exit 1
fi

exec bash "$TARGET" "$@"
