#!/bin/bash
# ══════════════════════════════════════════════════════════════
# install_saas.sh — Moteur SaaS HotspotPro (v2 : hub multi-tenant)
# Installe le core (tenant_db, provisioner) et le hub (tenant_hub)
# Migre automatiquement les anciennes installations (1 service/client)
# Compatible : Debian/Ubuntu
# ══════════════════════════════════════════════════════════════
set -e

# ── Couleurs ──
R='\033[0m'; B='\033[1m'; G='\033[0;32m'; Y='\033[1;33m'; RED='\033[0;31m'
info()  { echo -e "  ${Y}▶${R} $1"; }
ok()    { echo -e "  ${G}✓${R} $1"; }
error() { echo -e "  ${RED}✗ ERREUR :${R} $1"; exit 1; }
sep()   { echo -e "\n${B}══════════════════════════════════════════${R}"; }

sep
echo -e "  ${B}HotspotPro — Installation du moteur SaaS (hub v2)${R}"
sep

# ── Vérifications ──
[ "$EUID" -ne 0 ] && error "Lancez ce script en root : sudo bash install_saas.sh"

# Saisie des réglages : valeurs du fichier hotspotpro.conf si présentes,
# sinon questions (voir deploy/lib_ask.sh).
source "$(dirname "${BASH_SOURCE[0]}")/../deploy/lib_ask.sh"

SAAS_DIR="/opt/hotspot-saas"
WEB_DIR="/opt/hotspot-saas-web"
WEB_ENV="$WEB_DIR/.env"
VENV="$SAAS_DIR/venv"
CORE_DIR="$SAAS_DIR/core"
SRC_DIR="$SAAS_DIR/src"
APP_USER="hotspot"
HUB_PORT=8010

# ── Détection de l'IP publique ──
sep
info "Détection de l'IP publique du VPS…"
AUTO_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "")

if [ -n "$AUTO_IP" ]; then
    echo -e "  IP détectée automatiquement : ${G}$AUTO_IP${R}"
    ask VPS_PUBLIC_IP "Confirmer cette IP ou en saisir une autre" "$AUTO_IP"
else
    ask VPS_PUBLIC_IP "Entrez l'IP publique de ce VPS" ""
    [ -z "$VPS_PUBLIC_IP" ] && error "IP publique requise."
fi
ok "IP VPS : $VPS_PUBLIC_IP"

# ── Utilisateur applicatif ──
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --home-dir "$SAAS_DIR" --shell /usr/sbin/nologin "$APP_USER"
    ok "Utilisateur système '$APP_USER' créé"
fi

# ── Création des dossiers ──
sep
info "Création de la structure de dossiers…"
mkdir -p "$SAAS_DIR"/{core,src,tenants}
ok "Dossiers créés : $SAAS_DIR"

# ── Copie des fichiers core ──
info "Installation des fichiers du moteur…"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/core/tenant_db.py" ]; then
    # Copie TOUT le core : le hub (tickets, access_log) et le service web
    # (hotspot_sync, wg_store, secretbox, routeros, wireguard) en dépendent
    # via PYTHONPATH=$CORE_DIR. Ne jamais cherry-picker : un module oublié
    # fait crasher le service au démarrage (ModuleNotFoundError).
    cp "$SCRIPT_DIR/core/"*.py            "$CORE_DIR/"
    cp "$SCRIPT_DIR/src/tenant_hub.py"    "$SRC_DIR/"
    cp "$SCRIPT_DIR/requirements.txt"     "$SAAS_DIR/"
    ok "Fichiers Python copiés (core complet : $(ls "$SCRIPT_DIR/core/"*.py | wc -l) modules)"
else
    error "Fichiers core introuvables. Lancez ce script depuis le dossier saas/"
fi

# ── Virtualenv Python ──
sep
info "Création de l'environnement Python…"
if [ ! -f "$VENV/bin/python3" ]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install -q --disable-pip-version-check -r "$SAAS_DIR/requirements.txt"
ok "Dépendances installées"

# ── Clé du hub (partagée avec l'interface web) ──
sep
info "Configuration du hub…"
HUB_KEY=""
if [ -f "$WEB_ENV" ] && grep -q "^HUB_KEY=" "$WEB_ENV"; then
    HUB_KEY=$(grep "^HUB_KEY=" "$WEB_ENV" | cut -d'=' -f2-)
fi
if [ -z "$HUB_KEY" ]; then
    HUB_KEY=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    if [ -f "$WEB_ENV" ]; then
        echo "HUB_KEY=$HUB_KEY" >> "$WEB_ENV"
    fi
fi

cat > "$SAAS_DIR/.env" << ENVEOF
HOTSPOT_SAAS_DIR=$SAAS_DIR
TENANT_HUB_PORT=$HUB_PORT
HUB_KEY=$HUB_KEY
ENVEOF
chmod 600 "$SAAS_DIR/.env"
ok "Configuration hub écrite : $SAAS_DIR/.env"

# ── Mise à jour du .env web ──
if [ -f "$WEB_ENV" ]; then
    if grep -q "^VPS_PUBLIC_IP=" "$WEB_ENV"; then
        sed -i "s|^VPS_PUBLIC_IP=.*|VPS_PUBLIC_IP=$VPS_PUBLIC_IP|" "$WEB_ENV"
    else
        echo "VPS_PUBLIC_IP=$VPS_PUBLIC_IP" >> "$WEB_ENV"
    fi
    grep -q "^HOTSPOT_SAAS_DIR=" "$WEB_ENV" || echo "HOTSPOT_SAAS_DIR=$SAAS_DIR" >> "$WEB_ENV"
    ok ".env web mis à jour : $WEB_ENV"
fi
echo "$VPS_PUBLIC_IP" > "$SAAS_DIR/vps_ip.txt"

# ── Migration : suppression des anciens services par client ──
sep
info "Migration des anciennes installations (1 service par client)…"
LEGACY_UNITS=$(systemctl list-unit-files 'hotspot-tenant-*' --no-legend 2>/dev/null \
               | awk '{print $1}' | grep -v '^hotspot-tenant-hub' || true)
if [ -n "$LEGACY_UNITS" ]; then
    for UNIT in $LEGACY_UNITS; do
        systemctl stop "$UNIT" 2>/dev/null || true
        systemctl disable "$UNIT" 2>/dev/null || true
        rm -f "/etc/systemd/system/$UNIT"
        info "Ancien service retiré : $UNIT"
    done
    systemctl daemon-reload
    ok "Anciens services supprimés (les données de vente sont CONSERVÉES)"
    echo -e "  ${Y}⚠ Les clients existants doivent recopier leur script MikroTik"
    echo -e "    depuis leur dashboard (nouvelle URL /t/<slug>/login).${R}"
else
    ok "Aucun ancien service à migrer"
fi

# ── Import des tenants existants dans la base centrale ──
sep
info "Synchronisation des abonnements provisionnés vers la base centrale…"
WEB_DB="$WEB_DIR/webapp/hotspotpro.db"
if [ -f "$WEB_DB" ]; then
    HOTSPOT_SAAS_DIR="$SAAS_DIR" "$VENV/bin/python3" - << 'PYEOF'
import os, sys, sqlite3, json, secrets

SAAS_DIR = os.environ["HOTSPOT_SAAS_DIR"]
sys.path.insert(0, os.path.join(SAAS_DIR, "core"))
from tenant_db import init_central_db, slug_exists, add_tenant, update_tenant

init_central_db()
web_db = "/opt/hotspot-saas-web/webapp/hotspotpro.db"
conn = sqlite3.connect(web_db)
conn.row_factory = sqlite3.Row

subs = conn.execute("""
    SELECT s.*, c.full_name FROM subscriptions s
    JOIN clients c ON s.client_id = c.id
    WHERE s.provisioned=1 AND s.slug IS NOT NULL AND s.slug != ''
""").fetchall()

count = 0
for row in subs:
    sub = dict(row)
    slug = sub["slug"]
    prices = None
    try:
        prices = json.loads(sub["prices"]) if sub.get("prices") else None
    except Exception:
        pass
    token = sub.get("router_token")
    if not token:
        token = secrets.token_urlsafe(24)
        conn.execute("UPDATE subscriptions SET router_token=? WHERE id=?", (token, sub["id"]))
        conn.commit()
    fields = dict(bot_token=sub.get("bot_token", ""), chat_id=sub.get("chat_id", ""),
                  mikrotik_ip=sub.get("mikrotik_ip", ""),
                  router_name=sub.get("router_name") or "Routeur principal",
                  router_token=token, prices=prices)
    if slug_exists(slug):
        update_tenant(slug, active=1, **fields)
        print(f"  → Tenant mis à jour : {slug}")
    else:
        add_tenant(sub["full_name"], slug, fields["bot_token"], fields["chat_id"],
                   fields["mikrotik_ip"], router_name=fields["router_name"],
                   router_token=token, prices=prices)
        print(f"  → Tenant créé : {slug}")
    count += 1

conn.close()
print(f"  {count} tenant(s) synchronisé(s)")
PYEOF
    ok "Base centrale synchronisée"
else
    info "Base web non trouvée — les tenants seront créés à la configuration client"
fi

# ── Permissions ──
chown -R "$APP_USER:$APP_USER" "$SAAS_DIR"

# ── Service systemd du hub ──
sep
info "Création du service hotspot-tenant-hub…"
cat > /etc/systemd/system/hotspot-tenant-hub.service << SVCEOF
[Unit]
Description=HotspotPro Tenant Hub (multi-tenant)
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$SRC_DIR
EnvironmentFile=$SAAS_DIR/.env
ExecStart=$VENV/bin/gunicorn -w 1 --threads 16 -b 127.0.0.1:$HUB_PORT tenant_hub:app
# Bases SQLite gardées ouvertes (dbconn.keep_open) : marge de descripteurs
LimitNOFILE=65536
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=hotspot-tenant-hub
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=$SAAS_DIR

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable --now hotspot-tenant-hub
ok "Service hotspot-tenant-hub démarré (port $HUB_PORT)"

# ── Nginx : s'assurer que /t/ est proxifié vers le hub ──
NGINX_CONF="/etc/nginx/sites-available/hotspotpro"
if [ -f "$NGINX_CONF" ] && ! grep -q "location \^~ /t/" "$NGINX_CONF"; then
    info "Ajout de la route /t/ dans nginx…"
    sed -i "0,/location \/ {/s||location ^~ /t/ {\n        proxy_pass         http://127.0.0.1:$HUB_PORT;\n        proxy_set_header   Host \$host;\n        proxy_set_header   X-Real-IP \$remote_addr;\n        proxy_read_timeout 30s;\n    }\n\n    location / {|" "$NGINX_CONF"
    nginx -t && systemctl reload nginx
    ok "Nginx mis à jour"
fi

# ── Redémarrage du service web (pour charger le provisioner) ──
if systemctl list-unit-files hotspot-web.service &>/dev/null; then
    WEB_SERVICE="/etc/systemd/system/hotspot-web.service"
    if [ -f "$WEB_SERVICE" ] && ! grep -q "PYTHONPATH" "$WEB_SERVICE"; then
        sed -i "/^\[Service\]/a Environment=PYTHONPATH=$CORE_DIR" "$WEB_SERVICE"
        systemctl daemon-reload
    fi
    systemctl restart hotspot-web 2>/dev/null || true
    ok "Service hotspot-web redémarré"
fi

# ── Résumé ──
sep
echo ""
echo -e "  ${G}${B}✅ Moteur SaaS v2 installé avec succès !${R}"
echo ""
echo -e "  ${B}Architecture :${R}"
echo -e "    Hub multi-tenant : ${G}hotspot-tenant-hub${R} (port $HUB_PORT, un seul processus)"
echo -e "    API MikroTik     : ${G}http://$VPS_PUBLIC_IP/t/<slug>/login${R} (port 80 via nginx)"
echo -e "    Données          : ${G}$SAAS_DIR/tenants/<slug>/sales.db${R}"
echo ""
echo -e "  ${B}Vérifier :${R}"
echo -e "    ${Y}systemctl status hotspot-tenant-hub${R}"
echo -e "    ${Y}curl http://127.0.0.1:$HUB_PORT/health${R}"
echo ""
sep
