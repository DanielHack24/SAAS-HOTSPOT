#!/bin/bash
# ══════════════════════════════════════════════════════════════
# install_wireguard.sh — Serveur WireGuard HotspotPro (option B).
#
# Met en place le tunnel plateforme <-> routeurs clients : chaque routeur
# se connecte au VPS, ce qui permet de POUSSER les tickets dans son User
# Manager. À lancer APRÈS install_web.sh et install_saas.sh :
#   cd deploy && sudo bash install_wireguard.sh
# ══════════════════════════════════════════════════════════════
set -e

R='\033[0m'; B='\033[1m'; G='\033[0;32m'; Y='\033[1;33m'; C='\033[0;36m'; RED='\033[0;31m'
info()  { echo -e "  ${Y}>${R} $1"; }
ok()    { echo -e "  ${G}OK${R} $1"; }
warn()  { echo -e "  ${Y}!${R} $1"; }
error() { echo -e "  ${RED}ERREUR :${R} $1"; exit 1; }
sep()   { echo -e "\n${B}==========================================${R}"; }

[ "$EUID" -ne 0 ] && error "Lancez en root : sudo bash install_wireguard.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="/opt/hotspot-saas-web"
SAAS_DIR="/opt/hotspot-saas"
CORE_DIR="$SAAS_DIR/core"
VENV_PY="$WEB_DIR/venv/bin/python3"
IFACE="wg-hotspotpro"
WG_CONF="/etc/wireguard/$IFACE.conf"
WG_PORT=51820
SERVER_TUNNEL_IP="10.66.0.1/16"

[ -x "$VENV_PY" ] || error "venv web introuvable ($VENV_PY). Lancez d'abord install_web.sh."

sep
echo -e "  ${B}HotspotPro — Serveur WireGuard (tunnel vers les routeurs)${R}"
sep

# ── Environnement (SECRET_KEY pour chiffrer la clé serveur en base) ──
set -a
[ -f "$WEB_DIR/.env" ]  && source "$WEB_DIR/.env"
[ -f "$SAAS_DIR/.env" ] && source "$SAAS_DIR/.env"
set +a
export HOTSPOT_SAAS_DIR="$SAAS_DIR"
[ -n "${SECRET_KEY:-}" ] || error "SECRET_KEY absente de $WEB_DIR/.env"

# ── Endpoint public (IP ou domaine) ──
info "Détection de l'adresse publique…"
AUTO_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "")
DEFAULT_EP="${VPS_PUBLIC_IP:-$AUTO_IP}"
ask ENDPOINT "Adresse publique du VPS (IP ou domaine)" "$DEFAULT_EP"
[ -n "$ENDPOINT" ] || error "Adresse publique requise."
ok "Endpoint : $ENDPOINT:$WG_PORT (UDP)"

# ── Paquets ──
info "Installation de wireguard…"
apt-get update -qq
apt-get install -y -qq wireguard wireguard-tools > /dev/null
ok "wireguard installé"

# ── Clé serveur : générée et stockée (chiffrée) dans central.db ──
info "Initialisation de la paire serveur…"
read SRV_PRIV SRV_PUB <<EOF
$("$VENV_PY" -c "import sys,os; sys.path.insert(0, os.environ['HOTSPOT_SAAS_DIR']+'/core'); import wg_store; s=wg_store.set_server('$ENDPOINT', $WG_PORT); print(s['private_key'], s['public_key'])")
EOF
[ -n "$SRV_PRIV" ] && [ -n "$SRV_PUB" ] || error "Génération de la clé serveur échouée."
ok "Clé serveur prête (publique : ${SRV_PUB:0:12}…)"

# ── Interface WireGuard (les pairs sont ajoutés dynamiquement par wg_sync) ──
info "Écriture de $WG_CONF…"
umask 077
mkdir -p /etc/wireguard
cat > "$WG_CONF" << CONFEOF
[Interface]
Address = $SERVER_TUNNEL_IP
ListenPort = $WG_PORT
PrivateKey = $SRV_PRIV
# Forwarding entre pairs du tunnel, FILTRÉ : la chaîne HOTSPOTPRO-WG (remplie
# par hotspotpro-wg-sync) n'autorise que chaque appareil VPN <-> son routeur.
PostUp = sysctl -w net.ipv4.ip_forward=1; iptables -N HOTSPOTPRO-WG 2>/dev/null || true; iptables -C FORWARD -i $IFACE -o $IFACE -j HOTSPOTPRO-WG 2>/dev/null || iptables -I FORWARD 1 -i $IFACE -o $IFACE -j HOTSPOTPRO-WG
PostDown = iptables -D FORWARD -i $IFACE -o $IFACE -j HOTSPOTPRO-WG 2>/dev/null || true
# Les pairs (routeurs clients) sont gérés par hotspotpro-wg-sync
CONFEOF
chmod 600 "$WG_CONF"

systemctl enable --now "wg-quick@$IFACE" > /dev/null 2>&1 || systemctl restart "wg-quick@$IFACE"
ok "Interface $IFACE active ($SERVER_TUNNEL_IP)"

# ── Pare-feu : ouvrir le port WireGuard (UDP) ──
if command -v ufw > /dev/null 2>&1; then
  ufw allow "$WG_PORT/udp" > /dev/null 2>&1 || true
  ok "ufw : port $WG_PORT/udp ouvert"
fi

# ── Service de synchronisation des pairs (root) ──
info "Installation du service de synchronisation des pairs…"
install -m 755 "$SCRIPT_DIR/wg_sync.py" /usr/local/bin/hotspotpro-wg-sync.py

cat > /etc/systemd/system/hotspotpro-wg-sync.service << SVCEOF
[Unit]
Description=HotspotPro — synchronisation des pairs WireGuard
After=wg-quick@$IFACE.service
Wants=wg-quick@$IFACE.service

[Service]
Type=oneshot
Environment=HOTSPOT_SAAS_DIR=$SAAS_DIR
Environment=HOTSPOT_SAAS_CORE=$CORE_DIR
ExecStart=$VENV_PY /usr/local/bin/hotspotpro-wg-sync.py
SyslogIdentifier=hotspotpro-wg-sync
SVCEOF

cat > /etc/systemd/system/hotspotpro-wg-sync.timer << 'TMREOF'
[Unit]
Description=Synchronise les pairs WireGuard toutes les minutes

[Timer]
OnBootSec=30
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
TMREOF

systemctl daemon-reload
systemctl enable --now hotspotpro-wg-sync.timer
systemctl start hotspotpro-wg-sync.service || true
ok "Synchronisation active (toutes les minutes)"

sep
echo -e "  ${G}${B}Serveur WireGuard prêt.${R}"
echo
echo -e "  ${B}Interface :${R}   $IFACE ($SERVER_TUNNEL_IP), port $WG_PORT/udp"
echo -e "  ${B}Endpoint :${R}    $ENDPOINT:$WG_PORT"
echo -e "  ${B}Pairs :${R}       gérés depuis l'espace client (page « Connecter votre routeur »)"
echo -e "  ${B}Diagnostic :${R}  wg show $IFACE   ·   journalctl -t hotspotpro-wg-sync -f"
sep
