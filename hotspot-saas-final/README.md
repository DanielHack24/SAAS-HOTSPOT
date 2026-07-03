# HotspotPro — Pack Complet (Web + Moteur SaaS v2)

Plateforme SaaS de comptabilité pour hotspots MikroTik : les clients
s'abonnent en ligne (FedaPay), configurent leur bot Telegram, et
reçoivent chaque vente de ticket en temps réel.

## Architecture v2

**Un seul hub multi-tenant** remplace l'ancien modèle « 1 processus + 1
port par client » : RAM constante, plus de limite de ports, tarifs
rechargés à chaud, provisioning instantané (INSERT + reload).

```
hotspot-saas-final/
├── web/                          → Interface web (dashboard, paiements…)
│   ├── install_web.sh            → Installe nginx, gunicorn, l'interface
│   ├── update_web.sh             → Met à jour l'interface web
│   ├── requirements.txt
│   └── webapp/
│       ├── app.py                → Point d'entrée (gunicorn app:app)
│       ├── webapp_core.py        → Instance Flask + pont moteur SaaS
│       ├── config.py             → Config (tout vient du .env)
│       ├── db.py                 → Base web + helpers
│       ├── security.py           → Mots de passe (PBKDF2), CSRF, rate limit
│       ├── emails.py             → Emails transactionnels (Brevo)
│       ├── fedapay.py            → Client FedaPay + vérif signature webhook
│       ├── mikrotik_scripts.py   → Génération scripts RouterOS
│       ├── services.py           → Activation abonnements/routeurs
│       ├── routes_*.py           → Routes (auth, client, billing, admin, api)
│       └── templates/            → Pages HTML
│
├── saas/                         → Moteur SaaS
│   ├── install_saas.sh           → Installe le hub (APRÈS install_web.sh)
│   │                               + migre les anciennes installations
│   ├── requirements.txt
│   ├── core/
│   │   ├── tenant_db.py          → Base centrale (tenants, tokens, tarifs)
│   │   └── provisioner.py        → Provisioning (DB + reload hub)
│   └── src/
│       └── tenant_hub.py         → Hub multi-tenant (ventes + bots Telegram)
│
└── README.md
```

## Flux des ventes

```
MikroTik ──(On Login)──▶ http://IP/t/<slug>/login?username=…&token=…
                              │ nginx (port 80)
                              ▼
                    hotspot-tenant-hub (127.0.0.1:8010)
                              │
                    tenants/<slug>/sales.db + notif Telegram
```

Chaque tenant a un **token secret** dans son script : sans token
valide, la vente est ignorée.

## Installation (ordre important)

```bash
# Étape 1 — Interface web
cd web/
sudo bash install_web.sh

# Étape 2 — Moteur SaaS (hub)
cd ../saas/
sudo bash install_saas.sh
```

L'installeur demande : IP du VPS, compte admin, clés Brevo et FedaPay.
**Aucun secret n'est stocké dans le code** — tout va dans
`/opt/hotspot-saas-web/.env` (permissions 600).

### Webhook FedaPay (obligatoire pour l'activation automatique)

Dans le dashboard FedaPay, créez un webhook pointant vers :
`https://votre-domaine/webhook/fedapay` (événement `transaction.approved`)
et renseignez la clé `wh_live_…` dans le `.env`. La signature est
vérifiée strictement : sans clé webhook valide, aucune activation.

## Après installation

```bash
# Services (2 seulement, non-root)
systemctl status hotspot-web
systemctl status hotspot-tenant-hub

# Santé du hub
curl http://127.0.0.1:8010/health

# Santé d'un tenant
curl http://localhost/t/<slug>/health

# Logs
journalctl -u hotspot-web -f
journalctl -u hotspot-tenant-hub -f
```

## Migration depuis la v1 (1 service par client)

`install_saas.sh` s'occupe de tout :
1. arrête et supprime les anciens services `hotspot-tenant-<slug>` ;
2. importe les abonnements provisionnés dans la base centrale
   (les `sales.db` existantes sont **conservées telles quelles**) ;
3. démarre le hub.

⚠️ **Seule action manuelle** : chaque client doit recopier son script
On Login depuis son dashboard (l'URL passe de `http://IP:800X/login` à
`http://IP/t/<slug>/login` avec un token de sécurité).

## Sécurité

- Mots de passe : PBKDF2 salé (migration automatique des anciens hash à la connexion)
- CSRF sur tous les formulaires
- Rate limiting sur la connexion
- Webhook FedaPay : signature vérifiée strictement + contrôle du montant payé
- Services systemd non-root (`User=hotspot`, `ProtectSystem=full`)
- Ventes MikroTik authentifiées par token par routeur
