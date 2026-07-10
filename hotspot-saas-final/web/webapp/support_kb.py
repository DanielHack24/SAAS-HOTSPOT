"""
support_kb.py — Base de connaissances du bot de support Telegram.

Chaque entrée : un bouton (label) dans le menu, des mots-clés pour la
reconnaissance du texte libre, et une solution rédigée (HTML Telegram :
<b> gras, retours à la ligne simples). Rien de spécifique à un opérateur :
c'est une aide générique, éditable sans redéploiement lourd.
"""
import unicodedata


def _norm(s: str) -> str:
    """Minuscule sans accents, pour une correspondance robuste."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


KB = [
    {
        "id": "vpn_offline",
        "label": "Routeur hors ligne / VPN deconnecte",
        "keywords": ["hors ligne", "offline", "vpn", "deconnect", "tunnel",
                     "connexion perdue", "perte de connexion", "ne se connecte",
                     "routeur eteint", "plus de connexion"],
        "answer": (
            "<b>Routeur hors ligne ou VPN deconnecte</b>\n\n"
            "1. Verifiez que le routeur est allume et a bien Internet "
            "(un autre appareil sur le meme reseau navigue-t-il ?).\n"
            "2. Le tunnel se reconnecte tout seul : patientez 1 a 2 minutes, "
            "l'indicateur repasse au vert des que la liaison est retablie.\n"
            "3. Si l'IP publique de votre FAI a change (reseau mobile), "
            "c'est normal : la connexion repart seule, rien a reconfigurer.\n"
            "4. Si rien ne revient apres 5 minutes, verifiez que le script de "
            "connexion (WireGuard) n'a pas ete supprime du routeur, puis "
            "redemarrez le routeur.\n\n"
            "Vous suivez l'etat en direct depuis votre espace client, page VPN."
        ),
    },
    {
        "id": "no_tickets",
        "label": "Les tickets ne se creent pas / pas d'impression",
        "keywords": ["ticket", "impression", "imprime", "utilisateur", "code",
                     "ne se cree", "creation", "pas de code", "vente"],
        "answer": (
            "<b>Les tickets / utilisateurs ne se creent pas</b>\n\n"
            "1. Verifiez d'abord que le routeur est bien connecte (voir "
            "\"Routeur hors ligne\"). Sans tunnel, la creation automatique "
            "ne peut pas fonctionner.\n"
            "2. Assurez-vous que le script <b>On Login</b> est colle dans "
            "CHAQUE profil hotspot (IP > Hotspot > Server Profiles > "
            "Scripting > On Login).\n"
            "3. Verifiez que les profils sont bien configures dans votre "
            "espace client (prix et validite renseignes).\n"
            "4. Faites une vente test : le ticket doit apparaitre dans votre "
            "tableau de bord en quelques secondes."
        ),
    },
    {
        "id": "payment_not_active",
        "label": "Paye mais abonnement pas active",
        "keywords": ["paye", "paiement", "pas active", "active", "fedapay",
                     "abonnement", "mobile money", "argent", "confirme"],
        "answer": (
            "<b>Paiement effectue mais abonnement inactif</b>\n\n"
            "L'activation apres un paiement FedaPay est <b>automatique</b>.\n"
            "1. Attendez 2 a 5 minutes puis rechargez votre espace client.\n"
            "2. Verifiez que le paiement Mobile Money / carte a bien ete "
            "confirme (SMS de votre operateur).\n"
            "3. Toujours inactif au bout de 10 minutes ? Cliquez sur "
            "\"Parler a un humain\" et indiquez votre <b>reference FedaPay</b> "
            "(commence par trx_...) : nous verifions et activons manuellement."
        ),
    },
    {
        "id": "forgot_password",
        "label": "Mot de passe oublie",
        "keywords": ["mot de passe", "mdp", "oublie", "connexion", "connecter",
                     "acces compte", "password", "reinitialiser"],
        "answer": (
            "<b>Mot de passe oublie</b>\n\n"
            "1. Sur la page de connexion, cliquez sur "
            "\"Mot de passe oublie\".\n"
            "2. Saisissez l'e-mail de votre compte.\n"
            "3. Vous recevez un lien de reinitialisation par e-mail "
            "(valable 1 heure). Verifiez aussi vos spams.\n"
            "4. Choisissez un nouveau mot de passe et reconnectez-vous."
        ),
    },
    {
        "id": "no_email",
        "label": "Je ne recois pas les e-mails",
        "keywords": ["email", "e-mail", "mail", "code de verification",
                     "pas recu", "recois pas", "spam", "verification"],
        "answer": (
            "<b>E-mail (code ou lien) non recu</b>\n\n"
            "1. Regardez dans les courriers indesirables / spams.\n"
            "2. Verifiez que l'adresse saisie est correcte, sans faute.\n"
            "3. Patientez 2 minutes puis utilisez \"Renvoyer le code\".\n"
            "4. Certaines boites (surtout pro) filtrent fort : essayez avec "
            "une adresse Gmail.\n"
            "Toujours rien ? Cliquez sur \"Parler a un humain\"."
        ),
    },
    {
        "id": "configure_router",
        "label": "Configurer un nouveau routeur MikroTik",
        "keywords": ["configurer", "configuration", "nouveau routeur",
                     "installer", "mikrotik", "script", "on login", "ajouter routeur"],
        "answer": (
            "<b>Configurer un routeur MikroTik</b>\n\n"
            "1. Espace client > <b>Configurer</b> : suivez le guide etape "
            "par etape.\n"
            "2. Collez le script de connexion fourni dans le routeur "
            "(System > Scripts).\n"
            "3. Collez le script <b>On Login</b> de chaque profil dans "
            "IP > Hotspot > Server Profiles > Scripting > On Login.\n"
            "4. Le routeur apparait \"en ligne\" dans votre espace des que "
            "le tunnel est etabli."
        ),
    },
    {
        "id": "vpn_add_device",
        "label": "Ajouter un 2e appareil au VPN",
        "keywords": ["deuxieme", "2e appareil", "autre pc", "second pc",
                     "multi appareil", "ajouter appareil", "plusieurs pc", "vpn pc"],
        "answer": (
            "<b>Ajouter un appareil au VPN</b>\n\n"
            "Le VPN multi-appareils est inclus dans l'abonnement <b>12 mois</b> "
            "(jusqu'a 5 appareils).\n"
            "1. Espace client > page <b>VPN</b> > \"Ajouter un appareil\".\n"
            "2. Telechargez le fichier de configuration genere.\n"
            "3. Importez-le dans l'application WireGuard du nouveau PC.\n"
            "Chaque appareil a sa propre cle : ils peuvent se connecter en "
            "meme temps sans se couper."
        ),
    },
    {
        "id": "change_plan",
        "label": "Changer ou renouveler mon abonnement",
        "keywords": ["changer", "renouveler", "renouvellement", "upgrade",
                     "mettre a jour", "forfait", "plan", "surclasser"],
        "answer": (
            "<b>Changer / renouveler un abonnement</b>\n\n"
            "1. Espace client > page <b>Abonnement</b>.\n"
            "2. Choisissez la formule voulue (1, 3, 5 ou 12 mois).\n"
            "3. Payez via FedaPay (Mobile Money ou carte).\n"
            "4. L'activation est immediate apres confirmation du paiement."
        ),
    },
    {
        "id": "false_sharing",
        "label": "Fausse alerte de partage d'abonnement",
        "keywords": ["partage", "alerte", "faux positif", "plusieurs ip",
                     "empreinte", "serie", "detection"],
        "answer": (
            "<b>Fausse alerte de partage</b>\n\n"
            "La detection se base sur l'<b>empreinte de l'appareil</b> "
            "(numero de serie du routeur), plus sur l'IP. Une IP qui change "
            "ne declenche donc plus d'alerte.\n"
            "Si votre routeur n'envoie pas encore sa serie, recollez le "
            "script <b>On Login</b> a jour (genere dans votre espace client) : "
            "il transmet desormais l'empreinte materielle."
        ),
    },
]

KB_BY_ID = {e["id"]: e for e in KB}


def match(text: str, limit: int = 3) -> list:
    """Entrees KB les plus pertinentes pour un texte libre (score > 0).

    Score = nombre de mots-cles presents. Tri par score decroissant. Sert a
    proposer une reponse avant d'ouvrir un ticket humain.
    """
    n = _norm(text)
    if not n:
        return []
    scored = []
    for e in KB:
        score = sum(1 for kw in e["keywords"] if _norm(kw) in n)
        if score:
            scored.append((score, e))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [e for _, e in scored[:limit]]
