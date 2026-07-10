"""
support_notify.py — Alertes proactives aux operateurs via le bot de support.

Lance periodiquement (timer systemd). Pour chaque compte lie a Telegram :
  - Routeur hors ligne : le tunnel WireGuard ne repond plus depuis > DOWN_S
    alors que l'abonnement est actif et le systeme configure -> alerte, puis
    message de retour a la normale quand il revient.
  - Abonnement bientot expire : 3 jours et 1 jour avant l'echeance.

Anti-spam : une table d'etat (support_notify_state) memorise ce qui a deja ete
notifie ; on n'envoie qu'aux transitions. `--dry-run` affiche sans envoyer.

Usage : python support_notify.py [--dry-run]
Env requis : SECRET_KEY (dechiffrement du token bot), PYTHONPATH vers saas/core.
"""
import sys
import time

import support_bot
import support_diag
from db import get_db

DOWN_S = 900          # 15 min sans handshake = hors ligne (marge anti-flapping)
EXPIRY_DAYS = (3, 1)  # seuils d'alerte avant expiration


def _get_state(client_id, kind):
    conn = get_db()
    row = conn.execute("""SELECT state FROM support_notify_state
                          WHERE client_id=? AND kind=?""",
                       (client_id, kind)).fetchone()
    conn.close()
    return row["state"] if row else None


def _set_state(client_id, kind, state):
    conn = get_db()
    conn.execute("""INSERT INTO support_notify_state (client_id, kind, state, updated_at)
                    VALUES (?, ?, ?, datetime('now','localtime'))
                    ON CONFLICT(client_id, kind) DO UPDATE SET
                        state=excluded.state, updated_at=excluded.updated_at""",
                 (client_id, kind, state))
    conn.commit(); conn.close()


def _linked_accounts():
    """[(chat_id, client_id)] des comptes operateurs lies a Telegram."""
    conn = get_db()
    rows = conn.execute("SELECT chat_id, client_id FROM support_accounts").fetchall()
    conn.close()
    return [(r["chat_id"], r["client_id"]) for r in rows]


def _emit(dry, chat_id, text):
    if dry:
        print(f"[DRY] -> chat {chat_id}: {text.splitlines()[0]}")
    else:
        support_bot._send(chat_id, text)


def run(dry=False) -> int:
    sent = 0
    now = time.time()
    for chat_id, client_id in _linked_accounts():
        health = None
        try:
            health = support_diag.account_health(client_id)
        except Exception as e:
            print(f"[NOTIFY] health {client_id}: {e}", flush=True)
        if not health:
            continue

        for r in health["routers"]:
            slug = r.get("slug")
            if not slug:
                continue

            # ── Liveness routeur ──
            if r.get("provisioned") and r.get("last_seen"):
                kind = f"router:{slug}"
                down = (now - r["last_seen"]) > DOWN_S
                prev = _get_state(client_id, kind)
                if down and prev != "down":
                    _emit(dry, chat_id,
                          f"<b>Alerte : routeur hors ligne</b>\n\n"
                          f"Votre routeur <b>{support_diag._esc(r['router_name'])}</b> "
                          f"ne repond plus (dernier contact "
                          f"{support_diag.human_ago(r['last_seen'])}). Vos ventes sont "
                          f"interrompues tant qu'il est hors ligne.\n\n"
                          f"Verifiez son alimentation et sa connexion Internet. "
                          f"Il se reconnecte automatiquement des le retour du reseau.")
                    _set_state(client_id, kind, "down"); sent += 1
                elif not down and prev == "down":
                    _emit(dry, chat_id,
                          f"<b>Routeur de nouveau en ligne</b>\n\n"
                          f"Votre routeur <b>{support_diag._esc(r['router_name'])}</b> "
                          f"a retrouve la connexion. Tout est rentre dans l'ordre.")
                    _set_state(client_id, kind, "up"); sent += 1

            # ── Expiration abonnement ──
            dl = r.get("days_left")
            if dl is not None:
                if dl > max(EXPIRY_DAYS):
                    # renouvele : on rearme les alertes d'expiration
                    for d in EXPIRY_DAYS:
                        if _get_state(client_id, f"exp:{d}") == "sent":
                            _set_state(client_id, f"exp:{d}", "")
                else:
                    for d in EXPIRY_DAYS:
                        if dl == d and _get_state(client_id, f"exp:{d}") != "sent":
                            _emit(dry, chat_id,
                                  f"<b>Abonnement bientot expire</b>\n\n"
                                  f"Votre abonnement ({r.get('plan') or 'actif'}) expire "
                                  f"dans <b>{d} jour(s)</b> ({r.get('end_date')}). "
                                  f"Renouvelez-le depuis votre espace client pour eviter "
                                  f"toute interruption de service.")
                            _set_state(client_id, f"exp:{d}", "sent"); sent += 1
    return sent


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    n = run(dry=dry)
    print(f"[NOTIFY] {'(dry-run) ' if dry else ''}{n} notification(s).")
