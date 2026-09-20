"""
emails.py — Envoi d'emails transactionnels via l'API Brevo
(gratuit jusqu'à 300 emails/jour). Silencieux si BREVO_API_KEY absente.

Un envoi qui échoue ne doit jamais casser une inscription ou un paiement :
send_email() n'échoue donc jamais, mais journalise TOUJOURS la raison
exacte renvoyée par Brevo (journalctl -u hotspot-web). Sans cette trace, un
expéditeur non validé chez Brevo se traduit par « aucun mail n'arrive »
sans le moindre indice.
"""
import json
import logging
import urllib.error
import urllib.request

import config

log = logging.getLogger("hotspotpro.emails")

BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def try_send(to_email: str, to_name: str, subject: str, html_body: str) -> str:
    """Envoie un email HTML. Retourne "" si envoyé, sinon la raison de l'échec."""
    api_key = config.brevo_api_key()
    if not api_key:
        return ("Aucune clé API Brevo enregistrée "
                "(page Configuration, section Email).")
    sender = config.from_email()
    if not sender:
        return ("Aucun email expéditeur enregistré "
                "(page Configuration, section Email).")
    payload = json.dumps({
        "sender":      {"name": config.from_name() or "HotspotPro", "email": sender},
        "to":          [{"email": to_email, "name": to_name}],
        "subject":     subject,
        "htmlContent": html_body,
    }).encode("utf-8")
    req = urllib.request.Request(
        BREVO_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "api-key":      api_key,
            "Accept":       "application/json",
        },
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return ""
    except urllib.error.HTTPError as exc:
        # Brevo détaille la cause dans le corps de la réponse : expéditeur non
        # validé, clé révoquée, quota du jour atteint…
        try:
            body = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            body = ""
        return f"Brevo a refusé l'envoi (HTTP {exc.code}) : {body or exc.reason}"
    except Exception as exc:
        return f"Brevo injoignable : {type(exc).__name__} {exc}"


def send_email(to_email: str, to_name: str, subject: str, html_body: str) -> str:
    """Envoie un email HTML via l'API Brevo. Ne lève jamais d'exception.

    Retourne "" si l'email est parti, sinon la raison de l'échec (déjà
    journalisée) pour que l'appelant puisse en informer l'utilisateur.
    """
    err = try_send(to_email, to_name, subject, html_body)
    if err:
        log.error("Email non envoye a %s (%s) : %s", to_email, subject, err)
    return err


def _layout(inner: str) -> str:
    """Gabarit commun (en-tête sombre + pied de page)."""
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body style="margin:0;padding:0;background:#f4f1eb;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:2.5rem 1rem">
<table width="540" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.07)">
  <tr><td style="background:#0a0a12;padding:2rem 2.5rem">
    <p style="font-family:'Times New Roman',Georgia,serif;font-size:1.3rem;font-weight:700;font-style:italic;color:#fff;margin:0">Hotspot<span style="color:#ff4d1c">Pro</span></p>
  </td></tr>
  <tr><td style="padding:2.2rem 2.5rem">
{inner}
  </td></tr>
  <tr><td style="background:#f8f6f0;padding:1.1rem 2.5rem;text-align:center">
    <p style="font-size:.75rem;color:#6b6b7a;margin:0">© HotspotPro · Lomé, Togo</p>
  </td></tr>
</table>
</td></tr></table></body></html>"""


def email_welcome(to_email: str, to_name: str):
    first = to_name.split()[0] if to_name.split() else to_name
    inner = f"""
    <h1 style="font-family:'Times New Roman',Georgia,serif;font-size:1.6rem;color:#0a0a12;margin:0 0 .7rem;letter-spacing:-.02em">Bienvenue, {first}</h1>
    <p style="color:#6b6b7a;font-size:.92rem;line-height:1.65;margin:0 0 1.5rem">Votre compte HotspotPro a été créé avec succès. Vous pouvez dès maintenant choisir un abonnement et démarrer votre système de comptabilité hotspot.</p>
    <table cellpadding="0" cellspacing="0"><tr><td>
      <a href="{config.APP_URL}/subscribe" style="background:#ff4d1c;color:#fff;text-decoration:none;padding:.75rem 1.8rem;border-radius:8px;font-weight:bold;font-size:.92rem;display:inline-block">Choisir mon abonnement →</a>
    </td></tr></table>
    <hr style="border:none;border-top:1px solid rgba(10,10,18,.1);margin:1.8rem 0">
    <p style="font-size:.8rem;color:#6b6b7a;line-height:1.6;margin:0">Paiement sécurisé via <strong>FedaPay</strong> — Mobile Money, carte. Activation instantanée. Des questions ? Répondez à cet email.</p>"""
    send_email(to_email, to_name, "Bienvenue sur HotspotPro", _layout(inner))


def email_subscription_activated(to_email: str, to_name: str, plan_label: str,
                                 amount: int, reference: str, end_date: str):
    first = to_name.split()[0] if to_name.split() else to_name
    inner = f"""
    <h1 style="font-family:'Times New Roman',Georgia,serif;font-size:1.5rem;color:#0a0a12;margin:0 0 .6rem;letter-spacing:-.02em">Abonnement activé, {first}</h1>
    <p style="color:#6b6b7a;font-size:.92rem;line-height:1.65;margin:0 0 1.5rem">Votre paiement a été confirmé et votre abonnement est actif jusqu'au <strong>{end_date}</strong>.</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f6f0;border-radius:10px;margin-bottom:1.5rem">
      <tr><td style="padding:1.2rem 1.4rem">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="font-size:.78rem;color:#6b6b7a;padding-bottom:.3rem">Plan</td><td style="font-size:.88rem;font-weight:bold;color:#0a0a12;text-align:right">{plan_label}</td></tr>
          <tr><td style="font-size:.78rem;color:#6b6b7a;padding-bottom:.3rem">Montant</td><td style="font-size:.88rem;font-weight:bold;color:#0a0a12;text-align:right">{amount:,} FCFA</td></tr>
          <tr><td style="font-size:.78rem;color:#6b6b7a">Référence</td><td style="font-size:.82rem;font-weight:bold;color:#ff4d1c;text-align:right;word-break:break-all">{reference}</td></tr>
        </table>
      </td></tr>
    </table>
    <p style="font-size:.82rem;color:#6b6b7a;line-height:1.5;margin:0">Connectez-vous à votre espace client pour configurer votre système si ce n'est pas déjà fait.</p>"""
    send_email(to_email, to_name, f"Abonnement {plan_label} activé", _layout(inner))


def email_verification_code(to_email: str, to_name: str, code: str) -> str:
    """Code à 6 chiffres pour valider l'adresse avant création du compte.

    Retourne "" si envoyé, sinon la raison de l'échec : sans le code, le
    visiteur ne peut pas terminer son inscription, il faut le lui dire.
    """
    first = to_name.split()[0] if to_name.split() else to_name
    inner = f"""
    <h1 style="font-family:'Times New Roman',Georgia,serif;font-size:1.5rem;color:#0a0a12;margin:0 0 .6rem;letter-spacing:-.02em">Confirmez votre e-mail, {first}</h1>
    <p style="color:#6b6b7a;font-size:.92rem;line-height:1.65;margin:0 0 1.4rem">Voici votre code de vérification. Saisissez-le sur la page d'inscription pour créer votre compte HotspotPro. Ce code expire dans <strong>15 minutes</strong>.</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:1.4rem"><tr><td align="center">
      <div style="display:inline-block;background:#f8f6f0;border:1px solid rgba(10,10,18,.1);border-radius:12px;padding:1rem 1.8rem;font-family:'Courier New',monospace;font-size:2rem;font-weight:bold;letter-spacing:.5rem;color:#0a0a12">{code}</div>
    </td></tr></table>
    <p style="font-size:.8rem;color:#6b6b7a;line-height:1.6;margin:0">Si vous n'êtes pas à l'origine de cette inscription, ignorez simplement cet e-mail.</p>"""
    return send_email(to_email, to_name,
                      f"Votre code de vérification : {code}", _layout(inner))


def email_password_reset(to_email: str, to_name: str, reset_url: str):
    first = to_name.split()[0] if to_name.split() else to_name
    inner = f"""
    <h1 style="font-family:'Times New Roman',Georgia,serif;font-size:1.5rem;color:#0a0a12;margin:0 0 .6rem;letter-spacing:-.02em">Réinitialisation du mot de passe</h1>
    <p style="color:#6b6b7a;font-size:.92rem;line-height:1.65;margin:0 0 1.5rem">Bonjour {first}, vous avez demandé à réinitialiser votre mot de passe HotspotPro. Cliquez sur le bouton ci-dessous — ce lien est valable <strong>1 heure</strong>.</p>
    <table cellpadding="0" cellspacing="0"><tr><td>
      <a href="{reset_url}" style="background:#ff4d1c;color:#fff;text-decoration:none;padding:.75rem 1.8rem;border-radius:8px;font-weight:bold;font-size:.92rem;display:inline-block">Choisir un nouveau mot de passe →</a>
    </td></tr></table>
    <hr style="border:none;border-top:1px solid rgba(10,10,18,.1);margin:1.8rem 0">
    <p style="font-size:.8rem;color:#6b6b7a;line-height:1.6;margin:0">Si vous n'êtes pas à l'origine de cette demande, ignorez simplement cet email : votre mot de passe actuel reste inchangé.</p>"""
    send_email(to_email, to_name, "Réinitialisation de votre mot de passe", _layout(inner))


def email_support_ticket(to_email: str, ticket: dict):
    """Alerte admin : un operateur a ouvert un ticket de support via le bot."""
    msg = (ticket.get("message") or "").replace("<", "&lt;").replace(">", "&gt;")
    msg_html = msg.replace("\n", "<br>")
    who = ticket.get("who") or "operateur"
    acc = ticket.get("account") or {}
    acc_html = ""
    if acc:
        etat = "actif" if acc.get("active") else "inactif / aucun"
        fiche = f"{config.APP_URL}/admin/client/{acc.get('id')}"
        acc_html = (
            f"<p style=\"font-size:.78rem;color:#6b6b7a;margin:1rem 0 .3rem\">Compte HotspotPro</p>"
            f"<p style=\"font-size:.9rem;color:#0a0a12;margin:0 0 .2rem\"><b>{acc.get('full_name','')}</b> "
            f"({acc.get('email','')})</p>"
            f"<p style=\"font-size:.82rem;color:#6b6b7a;margin:0 0 .6rem\">Forfait : {acc.get('plan') or 'aucun'} ({etat})</p>"
            f"<p style=\"margin:0\"><a href=\"{fiche}\" style=\"font-size:.82rem;color:#ff4d1c;font-weight:bold;text-decoration:none\">Ouvrir la fiche client &rarr;</a></p>")
    inner = f"""
    <div style="width:52px;height:52px;background:rgba(255,77,28,.12);border:2px solid rgba(255,77,28,.3);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.4rem;margin-bottom:1.2rem">SOS</div>
    <h1 style="font-family:'Times New Roman',Georgia,serif;font-size:1.5rem;color:#0a0a12;margin:0 0 .6rem;letter-spacing:-.02em">Nouveau ticket de support #{ticket.get('id')}</h1>
    <p style="color:#6b6b7a;font-size:.9rem;line-height:1.6;margin:0 0 1.2rem">Un operateur a contacte le support via le bot Telegram.</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f6f0;border-radius:10px;margin-bottom:1.4rem">
      <tr><td style="padding:1.2rem 1.4rem">
        <p style="font-size:.78rem;color:#6b6b7a;margin:0 0 .3rem">De</p>
        <p style="font-size:.92rem;font-weight:bold;color:#0a0a12;margin:0 0 1rem">{who} <span style="font-weight:normal;color:#6b6b7a">(chat {ticket.get('chat_id')})</span></p>
        <p style="font-size:.78rem;color:#6b6b7a;margin:0 0 .3rem">Message</p>
        <p style="font-size:.92rem;color:#0a0a12;margin:0;line-height:1.55">{msg_html}</p>
        {acc_html}
      </td></tr>
    </table>
    <p style="font-size:.82rem;color:#6b6b7a;line-height:1.55;margin:0">Repondez directement dans Telegram (en repondant au message du ticket) : votre reponse est relayee a l'operateur.</p>"""
    send_email(to_email, "Admin HotspotPro",
               f"[Support #{ticket.get('id')}] {who}", _layout(inner))


def email_payment_received(to_email: str, to_name: str, plan_label: str,
                           amount: int, reference: str, method: str):
    first = to_name.split()[0] if to_name.split() else to_name
    inner = f"""
    <div style="width:52px;height:52px;background:rgba(245,166,35,.15);border:2px solid rgba(245,166,35,.3);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1.4rem;margin-bottom:1.2rem">⏳</div>
    <h1 style="font-family:'Times New Roman',Georgia,serif;font-size:1.5rem;color:#0a0a12;margin:0 0 .6rem;letter-spacing:-.02em">Paiement reçu, {first} !</h1>
    <p style="color:#6b6b7a;font-size:.92rem;line-height:1.65;margin:0 0 1.5rem">Votre demande de paiement a bien été enregistrée. Notre équipe la vérifie et activera votre abonnement dès confirmation, généralement en moins d'1h en heures ouvrables.</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f6f0;border-radius:10px;margin-bottom:1.5rem">
      <tr><td style="padding:1.2rem 1.4rem">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr><td style="font-size:.78rem;color:#6b6b7a;padding-bottom:.3rem">Plan</td><td style="font-size:.88rem;font-weight:bold;color:#0a0a12;text-align:right">{plan_label}</td></tr>
          <tr><td style="font-size:.78rem;color:#6b6b7a;padding-bottom:.3rem">Montant</td><td style="font-size:.88rem;font-weight:bold;color:#0a0a12;text-align:right">{amount:,} FCFA</td></tr>
          <tr><td style="font-size:.78rem;color:#6b6b7a;padding-bottom:.3rem">Opérateur</td><td style="font-size:.88rem;font-weight:bold;color:#0a0a12;text-align:right">{method.upper()}</td></tr>
          <tr><td style="font-size:.78rem;color:#6b6b7a">Référence</td><td style="font-size:.82rem;font-weight:bold;color:#ff4d1c;text-align:right;word-break:break-all">{reference}</td></tr>
        </table>
      </td></tr>
    </table>
    <p style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:.9rem 1.1rem;font-size:.82rem;color:#92400e;line-height:1.5;margin:0"><strong>Transaction FedaPay.</strong> L'activation de votre abonnement est automatique après confirmation.</p>"""
    send_email(to_email, to_name, f"Paiement en cours de vérification — {plan_label}", _layout(inner))
