"""
routes_sellers.py — Espace client : vendeurs et génération de tickets.

Les tickets sont pré-générés et rattachés à un vendeur (plus de préfixe
« vendeur- » dans le username). La logique vit dans le module partagé
`tickets` (saas/core) ; ces routes ne font qu'exposer l'interface.
"""
import threading

from flask import (render_template, request, redirect, url_for,
                   session, flash, Response)

import config
from webapp_core import app
from db import get_active_sub
from security import login_required
from routes_client import current_client, _profiles_from_sub


def _limits(sub) -> dict:
    """Limites du forfait courant (tickets/génération, vendeurs, VPN)."""
    return config.plan_limits((sub or {}).get("plan"))


def _seller_count(dbp) -> int:
    """Nombre de vendeurs réels (hors « Non attribué » automatique)."""
    return sum(1 for s in tickets.list_sellers(dbp) if s["name"] != tickets.UNASSIGNED)

try:
    import tickets
    import hotspot_sync
    TICKETS_OK = True
except Exception:                       # moteur SaaS absent (dev local)
    TICKETS_OK = False


def _async_sync(slug: str):
    """Pousse les tickets vers le routeur EN ARRIÈRE-PLAN, sans bloquer la
    requête web.

    Un gros lot (des centaines de PUT vers le routeur via le tunnel) ne doit
    JAMAIS s'exécuter dans la requête : sinon gunicorn tue le worker au bout de
    30 s (timeout) -> 500 Internal Server Error, et pendant ce temps les workers
    bloqués rendent tout le site indisponible pour les autres opérateurs.

    On lance donc la synchro dans un thread daemon et on répond tout de suite.
    Le cron /cron/sync_tickets (toutes les 5 min) reprend ce qui n'a pas été
    poussé (push_pending est idempotent et repartable)."""
    if not TICKETS_OK:
        return

    def _run():
        try:
            hotspot_sync.push_pending(slug)
        except Exception as e:
            print(f"[SYNC] push_pending({slug}) échec arrière-plan : {e}", flush=True)

    threading.Thread(target=_run, name=f"push-{slug}", daemon=True).start()


def _active_tenant():
    """Retourne (db_path, sub) si le client a un système déployé, sinon
    (None, sub) — l'appelant redirige avec un message adapté."""
    sub = get_active_sub(session["client_id"])
    if not TICKETS_OK or not sub or not sub.get("provisioned") or not sub.get("slug"):
        return None, sub
    return tickets.sales_db_path(sub["slug"]), sub


def _guard():
    """Renvoie une réponse de redirection si les vendeurs sont indisponibles."""
    dbp, sub = _active_tenant()
    if dbp:
        return dbp, sub, None
    if not sub:
        flash("Aucun abonnement actif.", "error")
        return None, None, redirect(url_for("dashboard"))
    flash("Configurez et déployez d'abord votre système.", "info")
    return None, None, redirect(url_for("dashboard"))


# ═══════════════════════════════════════════════
# VENDEURS
# ═══════════════════════════════════════════════

@app.route("/vendeurs")
@login_required
def sellers():
    dbp, sub, redir = _guard()
    if redir:
        return redir
    lim = _limits(sub)
    return render_template("sellers_list.html",
                           client=current_client(), sub=sub,
                           sellers=tickets.list_sellers(dbp),
                           sync=tickets.push_counts(dbp),
                           unassigned=tickets.UNASSIGNED,
                           limits=lim, seller_count=_seller_count(dbp))


@app.route("/vendeurs/create", methods=["POST"])
@login_required
def seller_create():
    dbp, sub, redir = _guard()
    if redir:
        return redir
    lim = _limits(sub)
    if _seller_count(dbp) >= lim["max_sellers"]:
        flash(f"Limite de votre forfait atteinte : {lim['max_sellers']} vendeurs maximum. "
              "Passez à un forfait supérieur pour en ajouter davantage.", "error")
        return redirect(url_for("sellers"))
    try:
        new_id = tickets.create_seller(dbp, request.form.get("name", ""))
        flash("Vendeur ajouté.", "success")
        return redirect(url_for("sellers", new=new_id))
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("sellers"))


@app.route("/vendeurs/<int:sid>/rename", methods=["POST"])
@login_required
def seller_rename(sid):
    dbp, sub, redir = _guard()
    if redir:
        return redir
    try:
        tickets.rename_seller(dbp, sid, request.form.get("name", ""))
        flash("Vendeur renommé.", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("seller_detail", sid=sid))


@app.route("/vendeurs/<int:sid>/delete", methods=["POST"])
@login_required
def seller_delete(sid):
    dbp, sub, redir = _guard()
    if redir:
        return redir
    try:
        tickets.delete_seller(dbp, sid)
        flash("Vendeur supprimé (ses tickets en stock ont été retirés).", "success")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("sellers"))


@app.route("/vendeurs/<int:sid>")
@login_required
def seller_detail(sid):
    dbp, sub, redir = _guard()
    if redir:
        return redir
    seller = tickets.get_seller(dbp, sid)
    if not seller:
        flash("Vendeur introuvable.", "error")
        return redirect(url_for("sellers"))
    summary = next((s for s in tickets.list_sellers(dbp) if s["id"] == sid), seller)
    return render_template("seller_detail.html",
                           client=current_client(), sub=sub,
                           seller=summary,
                           profiles=_profiles_from_sub(sub),
                           batches=tickets.list_batches(dbp, sid),
                           charsets=tickets.CHARSET_LABELS,
                           pw_modes=tickets.PW_MODES,
                           unassigned=tickets.UNASSIGNED,
                           limits=_limits(sub))


# ═══════════════════════════════════════════════
# GÉNÉRATION
# ═══════════════════════════════════════════════

@app.route("/vendeurs/<int:sid>/generate", methods=["POST"])
@login_required
def seller_generate(sid):
    dbp, sub, redir = _guard()
    if redir:
        return redir
    try:
        qty      = int(request.form.get("qty", "0"))
        code_len = int(request.form.get("code_len", "5"))
    except ValueError:
        flash("Quantité ou longueur invalide.", "error")
        return redirect(url_for("seller_detail", sid=sid))

    profile = request.form.get("profile", "").strip()
    charset = request.form.get("charset", "alnum")
    pw_mode = request.form.get("pw_mode", "same")

    # Le profil doit exister dans la configuration du client
    profiles = _profiles_from_sub(sub)
    if profile not in profiles:
        flash("Profil inconnu — configurez vos profils d'abord.", "error")
        return redirect(url_for("seller_detail", sid=sid))

    # Plafond de tickets par génération selon le forfait
    lim = _limits(sub)
    if qty > lim["max_tickets"]:
        flash(f"Votre forfait limite à {lim['max_tickets']} tickets par génération. "
              "Passez à un forfait supérieur pour en générer plus.", "error")
        return redirect(url_for("seller_detail", sid=sid))

    validity = (profiles[profile].get("validity") or "").strip()

    try:
        res = tickets.generate_batch(dbp, sid, profile, qty,
                                     code_len=code_len, charset=charset,
                                     pw_mode=pw_mode, validity=validity)
        flash(f"{len(res['tickets'])} tickets générés. "
              "Leur création sur le routeur se fait en arrière-plan "
              "(actualisez le lot dans un instant pour suivre l'avancement).", "success")
        _async_sync(sub["slug"])          # push routeur NON bloquant (arrière-plan)
        return redirect(url_for("batch_view", sid=sid, bid=res["batch_id"]))
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("seller_detail", sid=sid))


@app.route("/vendeurs/synchroniser", methods=["POST"])
@login_required
def sellers_sync():
    dbp, sub, redir = _guard()
    if redir:
        return redir
    _async_sync(sub["slug"])
    flash("Synchronisation lancée en arrière-plan. Actualisez dans un instant "
          "pour voir l'avancement.", "info")
    return redirect(request.referrer or url_for("sellers"))


def _batch_or_redirect(dbp, sid, bid):
    data = tickets.get_batch(dbp, bid)
    if not data or data["batch"]["seller_id"] != sid:
        return None, redirect(url_for("seller_detail", sid=sid))
    return data, None


def _script_lines(batch, batch_tickets) -> str:
    """Lignes RouterOS créant les tickets en Hotspot local (méthode de secours,
    si le tunnel n'est pas disponible), à l'identique de mikhmon : crée le
    profil hotspot s'il manque, puis chaque utilisateur (profil + durée
    `limit-uptime` + mot de passe). Idempotent (re-import sans erreur)."""
    profile  = batch["profile"]
    validity = (batch.get("validity") or "").strip()
    uptime   = f' limit-uptime={validity}' if validity else ""
    lines = [
        f':if ([/ip hotspot user profile find name="{profile}"]="") '
        f'do={{/ip hotspot user profile add name="{profile}"}}'
    ]
    for t in batch_tickets:
        lines.append(
            f':if ([/ip hotspot user find name="{t["username"]}"]="") do={{'
            f'/ip hotspot user add name="{t["username"]}" '
            f'password="{t["password"]}" profile="{profile}"{uptime} '
            f'comment="HotspotPro"}}'
        )
    return "\n".join(lines)


@app.route("/vendeurs/<int:sid>/lot/<int:bid>")
@login_required
def batch_view(sid, bid):
    dbp, sub, redir = _guard()
    if redir:
        return redir
    data, redir = _batch_or_redirect(dbp, sid, bid)
    if redir:
        return redir
    profiles = _profiles_from_sub(sub)
    pmeta = profiles.get(data["batch"]["profile"], {})
    return render_template("batch_view.html",
                           client=current_client(), sub=sub,
                           sid=sid, batch=data["batch"],
                           seller_name=data["seller_name"],
                           tickets=data["tickets"],
                           script=_script_lines(data["batch"], data["tickets"]),
                           pmeta=pmeta)


@app.route("/vendeurs/<int:sid>/lot/<int:bid>/imprimer")
@login_required
def batch_print(sid, bid):
    dbp, sub, redir = _guard()
    if redir:
        return redir
    data, redir = _batch_or_redirect(dbp, sid, bid)
    if redir:
        return redir
    profiles = _profiles_from_sub(sub)
    pmeta = profiles.get(data["batch"]["profile"], {})
    size = request.args.get("size", "big")
    if size not in ("small", "big"):
        size = "big"
    return render_template("cards_print.html",
                           client=current_client(),
                           batch=data["batch"],
                           seller_name=data["seller_name"],
                           tickets=data["tickets"],
                           pmeta=pmeta, size=size,
                           pw_same=(data["batch"]["pw_mode"] == "same"))


@app.route("/vendeurs/<int:sid>/lot/<int:bid>/rsc")
@login_required
def batch_rsc(sid, bid):
    dbp, sub, redir = _guard()
    if redir:
        return redir
    data, redir = _batch_or_redirect(dbp, sid, bid)
    if redir:
        return redir
    header = (
        "# HotspotPro — creation des utilisateurs hotspot\n"
        f"# Vendeur : {data['seller_name']} | Profil : {data['batch']['profile']} "
        f"| {len(data['tickets'])} tickets\n"
        "# Importer sur le MikroTik : /import file-name=hotspotpro_tickets.rsc\n\n"
    )
    body = _script_lines(data["batch"], data["tickets"]) + "\n"
    return Response(header + body, mimetype="text/plain",
                    headers={"Content-Disposition":
                             f'attachment; filename="hotspotpro_lot{bid}.rsc"'})
