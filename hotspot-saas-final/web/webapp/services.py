"""
services.py — Logique métier partagée (activation d'abonnements et de
routeurs). Utilisée à la fois par le webhook FedaPay et par la
confirmation manuelle admin, pour garantir un comportement identique.
"""
import re, json, secrets
import urllib.request, urllib.parse
from datetime import datetime, timedelta

import config
import webapp_core as core


def send_telegram_notify(bot_token: str, chat_id: str, message: str) -> bool:
    """Envoie une notification Telegram à un client. Jamais bloquant."""
    if not bot_token or not chat_id:
        return False
    try:
        url  = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": message,
                                       "parse_mode": "HTML"}).encode()
        req  = urllib.request.Request(url, data=data, method="POST")
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def make_slug(full_name: str, suffix: str) -> str:
    base = re.sub(r"[^a-z0-9]", "-", full_name.lower())
    base = re.sub(r"-+", "-", base).strip("-") or "client"
    return f"{base}-{suffix}"


def activate_subscription(conn, client_id: int, plan: str) -> tuple[int, datetime]:
    """Désactive les abonnements actifs du client et crée le nouveau,
    en réutilisant la config (slug, bot, prix) s'il s'agit d'un
    renouvellement. Retourne (subscription_id, date_fin).

    `conn` : connexion sqlite ouverte — le COMMIT reste à la charge de
    l'appelant pour garder l'opération atomique avec la mise à jour du
    paiement."""
    months = config.PLANS[plan]["months"]
    start  = datetime.now()
    end    = start + timedelta(days=30 * months)

    old_row = conn.execute("""
        SELECT * FROM subscriptions
        WHERE client_id=? AND provisioned=1
        ORDER BY id DESC LIMIT 1
    """, (client_id,)).fetchone()
    old_sub = dict(old_row) if old_row else None

    conn.execute("UPDATE subscriptions SET active=0 WHERE client_id=? AND active=1",
                 (client_id,))

    if old_sub:
        cur = conn.execute("""
            INSERT INTO subscriptions
                (client_id, plan, start_date, end_date, active,
                 slug, bot_token, chat_id, mikrotik_ip, provisioned,
                 prices, router_name, router_token)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, 1, ?, ?, ?)
        """, (client_id, plan,
              start.strftime("%Y-%m-%d %H:%M:%S"),
              end.strftime("%Y-%m-%d %H:%M:%S"),
              old_sub["slug"], old_sub["bot_token"],
              old_sub["chat_id"], old_sub["mikrotik_ip"],
              old_sub.get("prices"),
              old_sub.get("router_name") or "Routeur principal",
              old_sub.get("router_token")))
        if old_sub["slug"] and core.PROVISIONER_OK:
            try:
                core.start_tenant(old_sub["slug"])
            except Exception:
                pass
    else:
        cur = conn.execute("""
            INSERT INTO subscriptions (client_id, plan, start_date, end_date, active)
            VALUES (?, ?, ?, ?, 1)
        """, (client_id, plan,
              start.strftime("%Y-%m-%d %H:%M:%S"),
              end.strftime("%Y-%m-%d %H:%M:%S")))

    sub_id = cur.lastrowid

    # Notification Telegram si le client a déjà un bot configuré
    if old_sub and old_sub.get("bot_token") and old_sub.get("chat_id"):
        msg = (
            "✅ <b>Abonnement activé !</b>\n\n"
            f"📦 Plan : <b>{config.PLANS[plan]['label']}</b>\n"
            f"📅 Valable jusqu'au : <b>{end.strftime('%d/%m/%Y')}</b>\n\n"
            "Votre système hotspot est actif. Bonne vente ! 🚀"
        )
        send_telegram_notify(old_sub["bot_token"], old_sub["chat_id"], msg)

    return sub_id, end


def activate_device(conn, device: dict) -> str | None:
    """Provisionne un routeur MikroTik supplémentaire déjà payé.
    Retourne le slug créé (ou None si le device est introuvable)."""
    client_row = conn.execute("SELECT * FROM clients WHERE id=?",
                              (device["client_id"],)).fetchone()
    if not client_row:
        return None
    client = dict(client_row)

    slug = device.get("slug") or make_slug(client["full_name"], f"dev{device['id']}")
    conn.execute("UPDATE mikrotik_devices SET provisioned=1, slug=? WHERE id=?",
                 (slug, device["id"]))

    if core.PROVISIONER_OK:
        sub_row = conn.execute("SELECT * FROM subscriptions WHERE id=?",
                               (device["subscription_id"],)).fetchone()
        sub = dict(sub_row) if sub_row else {}
        try:
            if not core.slug_exists(slug):
                prices = None
                try:
                    prices = json.loads(sub["prices"]) if sub.get("prices") else None
                except Exception:
                    pass
                tenant = core.add_tenant(
                    f"{client['full_name']} (dev{device['id']})", slug,
                    sub.get("bot_token", ""), sub.get("chat_id", ""),
                    device["ip"],
                    router_name=device.get("label", ""),
                    router_token=secrets.token_urlsafe(24),
                    prices=prices,
                )
            else:
                tenant = core.get_tenant(slug)
            core.provision_tenant(tenant, config.get_vps_ip())
        except Exception as e:
            print(f"[PROVISION] Échec provisioning device {slug}: {e}", flush=True)

    return slug
