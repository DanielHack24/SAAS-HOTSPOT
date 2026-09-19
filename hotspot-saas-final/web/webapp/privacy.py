"""
privacy.py — Données personnelles : export, suppression de compte, purge.

Loi togolaise n° 2019-014 relative à la protection des données à caractère
personnel :
  - art. 16 et 89 : les données ne sont pas conservées au-delà de la durée
    nécessaire (sanction pénale sinon) -> run_retention() applique, chaque
    jour, les durées annoncées dans la politique de confidentialité ;
  - art. 39-40 : droit d'accès et copie des données -> export_client_data() ;
  - art. 46-48 : droit de suppression -> delete_client_account().

Acte uniforme OHADA relatif au droit comptable, art. 24 : les pièces
justificatives sont conservées dix ans. Un paiement encaissé (statut
« confirmed ») est donc gardé dix ans, rattaché à un compte ANONYMISÉ, même
après la suppression du compte. Les tentatives de paiement non abouties n'ont
aucune valeur comptable et sont effacées.

Toute modification des durées ci-dessous doit être reportée dans
legal_content.py (politique de confidentialité), et inversement.
"""
from datetime import datetime

import webapp_core as core
from webapp_core import PROVISIONER_OK

# ── Durées de conservation (en jours) ────────────────────────────
ACCOUNT_AFTER_LAST_SUB_DAYS = 3 * 365   # compte sans abonnement depuis 3 ans
ACCOUNT_NEVER_SUBSCRIBED_DAYS = 365      # compte jamais abonné : 1 an
ACCOUNTING_DAYS            = 10 * 365   # paiements encaissés (OHADA)
SUPPORT_DAYS               = 365        # tickets de support
SUPPORT_SESSION_DAYS       = 30         # état de conversation du bot support
NOTIFICATIONS_DAYS         = 365        # historique des notifications envoyées
ROUTER_ACCESS_DAYS         = 365        # journal IP / empreinte des routeurs

DELETED_EMAIL = "supprime-{cid}@compte-supprime.invalid"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ago(days: int) -> str:
    """Borne SQL « maintenant moins N jours », au format des dates stockées."""
    from datetime import timedelta
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _client_slugs(conn, cid: int) -> list[str]:
    slugs = [r["slug"] for r in conn.execute(
        "SELECT slug FROM subscriptions WHERE client_id=? AND slug IS NOT NULL AND slug!=''",
        (cid,)).fetchall()]
    slugs += [r["slug"] for r in conn.execute(
        "SELECT slug FROM mikrotik_devices WHERE client_id=? AND slug IS NOT NULL AND slug!=''",
        (cid,)).fetchall()]
    return list(dict.fromkeys(slugs))


def _teardown_tenant(slug: str) -> None:
    """Démonte le tenant (bot, base de ventes, registre central), ses pairs
    WireGuard et son journal d'accès. Best-effort : chaque étape est isolée."""
    if PROVISIONER_OK:
        for fn in ("stop_tenant", "remove_tenant_files", "delete_tenant"):
            try:
                getattr(core, fn)(slug)
            except Exception as e:
                print(f"[PRIVACY] {slug} {fn}: {e}", flush=True)
    for mod, fn in (("wg_store", "delete_all_for_slug"), ("access_log", "forget_slug")):
        try:
            getattr(__import__(mod), fn)(slug)
        except Exception as e:
            print(f"[PRIVACY] {slug} {mod}.{fn}: {e}", flush=True)


# ═══════════════════════════════════════════════
# SUPPRESSION DE COMPTE
# ═══════════════════════════════════════════════

def delete_client_account(conn, cid: int) -> dict:
    """Supprime un compte client et toutes ses données, sauf les paiements
    encaissés (obligation comptable de 10 ans), conservés sur un compte
    anonymisé. N'engage pas la transaction : l'appelant fait conn.commit().

    Retourne {"deleted": bool, "anonymized": bool, "kept_payments": int}."""
    row = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not row:
        return {"deleted": False, "anonymized": False, "kept_payments": 0}
    email = row["email"]

    for slug in _client_slugs(conn, cid):
        _teardown_tenant(slug)

    # Tentatives de paiement non abouties : aucune valeur comptable.
    conn.execute("DELETE FROM payments WHERE client_id=? AND status!='confirmed'", (cid,))
    conn.execute("DELETE FROM mikrotik_payments WHERE client_id=? AND status!='confirmed'", (cid,))

    conn.execute("DELETE FROM notifications WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM mikrotik_devices WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM subscriptions WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
    conn.execute("DELETE FROM support_tickets WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM support_accounts WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM support_link_tokens WHERE client_id=?", (cid,))
    conn.execute("DELETE FROM support_notify_state WHERE client_id=?", (cid,))

    kept = (conn.execute("SELECT COUNT(*) FROM payments WHERE client_id=?", (cid,)).fetchone()[0]
            + conn.execute("SELECT COUNT(*) FROM mikrotik_payments WHERE client_id=?",
                           (cid,)).fetchone()[0])
    if kept == 0:
        conn.execute("DELETE FROM clients WHERE id=?", (cid,))
        return {"deleted": True, "anonymized": False, "kept_payments": 0}

    # Paiements encaissés à garder : le compte devient une coquille sans
    # aucune donnée personnelle (connexion impossible, e-mail libéré).
    conn.execute("""
        UPDATE clients SET email=?, full_name='Compte supprimé', phone=NULL,
               password_hash='!', avatar_color=NULL, reset_token_hash=NULL,
               reset_token_expires=NULL, is_admin=0, deleted_at=?
        WHERE id=?""", (DELETED_EMAIL.format(cid=cid), _now(), cid))
    return {"deleted": True, "anonymized": True, "kept_payments": kept}


# ═══════════════════════════════════════════════
# EXPORT (droit d'accès)
# ═══════════════════════════════════════════════

def _mask(secret: str | None) -> str | None:
    if not secret:
        return secret
    return "…" + secret[-4:] if len(secret) > 8 else "…"


def export_client_data(conn, cid: int) -> dict:
    """Copie des données personnelles d'un client, dans une forme lisible.
    Les secrets techniques (jetons) sont masqués : ils ne sont pas des
    informations sur la personne et leur fuite exposerait son compte."""
    c = conn.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not c:
        return {}
    rows = lambda sql: [dict(r) for r in conn.execute(sql, (cid,)).fetchall()]  # noqa: E731
    subs = rows("SELECT * FROM subscriptions WHERE client_id=?")
    for s in subs:
        s["bot_token"] = _mask(s.get("bot_token"))
        s["router_token"] = _mask(s.get("router_token"))
    return {
        "genere_le": _now(),
        "responsable_du_traitement": "HotspotPro",
        "compte": {k: c[k] for k in ("email", "full_name", "phone", "created_at",
                                      "avatar_color", "terms_version",
                                      "terms_accepted_at") if k in c.keys()},
        "abonnements": subs,
        "paiements": rows("SELECT plan, amount, method, reference, status, created_at, "
                          "confirmed_at, terms_version, immediate_consent_at "
                          "FROM payments WHERE client_id=?"),
        "routeurs_supplementaires": rows("SELECT label, ip, provisioned, active, end_date, "
                                         "created_at FROM mikrotik_devices WHERE client_id=?"),
        "paiements_routeurs": rows("SELECT plan, amount, method, reference, status, created_at, "
                                   "confirmed_at, terms_version, immediate_consent_at "
                                   "FROM mikrotik_payments WHERE client_id=?"),
        "notifications": rows("SELECT type, message, sent_at FROM notifications WHERE client_id=?"),
        "support": rows("SELECT category, message, status, created_at, answered_at "
                        "FROM support_tickets WHERE client_id=?"),
        "note": ("Les données de vos ventes, vendeurs et tickets sont consultables "
                 "et exportables depuis votre espace (pages Vendeurs et lots)."),
    }


# ═══════════════════════════════════════════════
# PURGE QUOTIDIENNE (durées de conservation)
# ═══════════════════════════════════════════════

def run_retention(conn) -> dict:
    """Applique les durées de conservation. Appelée chaque jour par le cron
    /cron/check_expiry. Engage la transaction elle-même."""
    done = {}

    def run(key, sql, args=()):
        done[key] = conn.execute(sql, args).rowcount

    now = _now()
    run("inscriptions_expirees",
        "DELETE FROM pending_registrations WHERE expires_at < ?", (now,))
    run("jetons_reinit_expires",
        "UPDATE clients SET reset_token_hash=NULL, reset_token_expires=NULL "
        "WHERE reset_token_expires IS NOT NULL AND reset_token_expires < ?", (now,))
    run("jetons_liaison_expires",
        "DELETE FROM support_link_tokens WHERE expires_at < ?", (now,))
    run("tickets_support",
        "DELETE FROM support_tickets WHERE created_at < ?", (_ago(SUPPORT_DAYS),))
    run("sessions_support",
        "DELETE FROM support_sessions WHERE updated_at < ?", (_ago(SUPPORT_SESSION_DAYS),))
    run("notifications",
        "DELETE FROM notifications WHERE sent_at < ?", (_ago(NOTIFICATIONS_DAYS),))
    run("tentatives_connexion",
        "DELETE FROM rate_attempts WHERE ts < ?",
        (datetime.now().timestamp() - 86400,))

    # Comptes inactifs : aucun abonnement ni routeur actif, et fin du dernier
    # forfait (ou création, si jamais abonné) plus ancienne que la limite.
    stale = conn.execute("""
        SELECT c.id,
               (SELECT MAX(end_date) FROM subscriptions s WHERE s.client_id=c.id) AS last_sub,
               (SELECT MAX(end_date) FROM mikrotik_devices d WHERE d.client_id=c.id) AS last_dev,
               c.created_at
        FROM clients c
        WHERE c.is_admin=0 AND c.deleted_at IS NULL
          AND NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.client_id=c.id AND s.active=1)
          AND NOT EXISTS (SELECT 1 FROM mikrotik_devices d WHERE d.client_id=c.id AND d.active=1)
    """).fetchall()
    removed = 0
    for r in stale:
        last = max([d for d in (r["last_sub"], r["last_dev"]) if d] or [""])
        if last:
            expired = last < _ago(ACCOUNT_AFTER_LAST_SUB_DAYS)
        else:
            expired = (r["created_at"] or now) < _ago(ACCOUNT_NEVER_SUBSCRIBED_DAYS)
        if expired:
            delete_client_account(conn, r["id"])
            removed += 1
    done["comptes_inactifs"] = removed

    # Pièces comptables au-delà de 10 ans, puis coquilles devenues vides.
    limit = _ago(ACCOUNTING_DAYS)
    run("paiements_10_ans",
        "DELETE FROM payments WHERE COALESCE(confirmed_at, created_at) < ?", (limit,))
    run("paiements_routeurs_10_ans",
        "DELETE FROM mikrotik_payments WHERE COALESCE(confirmed_at, created_at) < ?", (limit,))
    run("comptes_supprimes_vides", """
        DELETE FROM clients WHERE deleted_at IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.client_id=clients.id)
          AND NOT EXISTS (SELECT 1 FROM mikrotik_payments m WHERE m.client_id=clients.id)""")
    conn.commit()

    try:
        import access_log
        done["journal_routeurs"] = access_log.prune(days=ROUTER_ACCESS_DAYS)
    except Exception as e:
        print(f"[PRIVACY] purge journal routeurs : {e}", flush=True)
    return done
