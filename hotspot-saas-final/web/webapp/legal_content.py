"""
legal_content.py — Contenu des pages légales.

Pages : conditions d'utilisation, confidentialité, cookies, remboursement et
rétractation, mentions légales.

Textes de référence (droit togolais) :
  - loi n° 2019-014 du 29 octobre 2019 relative à la protection des données
    à caractère personnel (autorité : IPDCP) ;
  - loi n° 2017-07 du 22 juin 2017 relative aux transactions électroniques
    (rétractation : art. 44 à 47) ;
  - Acte uniforme OHADA relatif au droit comptable (conservation 10 ans).

RÈGLE : chaque affirmation doit décrire ce que la plateforme fait
RÉELLEMENT. Les durées de conservation sont appliquées par privacy.py (base
web), deploy/backup.sh (sauvegardes) et deploy/install_ops.sh (journaux
système) : toute modification d'un côté doit être reportée de l'autre.

Les champs d'identité vides ne sont pas affichés. Complétez-les dès que
possible (voir IDENTITÉ ci-dessous).
"""

EDITEUR       = "SEKAYA Kossi Rodrigue"
CONTACT_EMAIL = "support@hotpotpro.app"
SITE          = "hotpotpro.app"
UPDATED       = "19 septembre 2026"
# Version des conditions enregistrée avec chaque consentement (inscription,
# paiement). À changer à chaque modification substantielle des textes.
TERMS_VERSION = "2026-09-19"

# ── IDENTITÉ À COMPLÉTER (affichée uniquement si renseignée) ─────
ADRESSE          = ""   # adresse postale de l'éditeur au Togo
TELEPHONE        = ""   # téléphone de contact
RCCM             = ""   # n° d'immatriculation RCCM, si vous êtes immatriculé
NIF              = ""   # numéro d'identification fiscale, si applicable
IPDCP_RECEPISSE  = ""   # n° du récépissé de déclaration délivré par l'IPDCP

HEBERGEUR = ("Amazon Web Services, Inc., 410 Terry Avenue North, Seattle, "
             "WA 98109-5210, États-Unis")


def _identity_lines() -> str:
    lines = [f"<li><strong>Éditeur</strong> : {EDITEUR}, personne physique</li>"]
    for label, value in (("Adresse", ADRESSE), ("Téléphone", TELEPHONE),
                         ("RCCM", RCCM), ("NIF", NIF)):
        if value:
            lines.append(f"<li><strong>{label}</strong> : {value}</li>")
    lines.append(f'<li><strong>E-mail</strong> : <a href="mailto:{CONTACT_EMAIL}">'
                 f"{CONTACT_EMAIL}</a></li>")
    return "<ul>" + "".join(lines) + "</ul>"


def _ipdcp_line() -> str:
    if IPDCP_RECEPISSE:
        return (f"<p>Les traitements décrits ici ont été déclarés à l'IPDCP "
                f"(récépissé n° {IPDCP_RECEPISSE}).</p>")
    return ""


# ═══════════════════════════════════════════════
# CONDITIONS D'UTILISATION
# ═══════════════════════════════════════════════

_CONDITIONS = f"""
<p class="lead">Les présentes conditions régissent l'utilisation de la plateforme
HotspotPro. Vous les acceptez expressément en créant votre compte, puis à chaque
paiement.</p>

<h2>1. Qui sommes-nous</h2>
{_identity_lines()}

<h2>2. Objet du service</h2>
<p>HotspotPro est une plateforme en ligne destinée aux exploitants de points
d'accès Wi-Fi (hotspots) équipés de routeurs MikroTik. Elle permet de générer des
tickets, de les envoyer sur votre routeur, de suivre les ventes par vendeur,
de recevoir des notifications sur Telegram et, selon le forfait, d'accéder à
distance à votre routeur par un tunnel chiffré (VPN).</p>

<h2>3. Accès au service</h2>
<ul>
  <li>Le service s'adresse aux personnes majeures qui exploitent un hotspot, à
  titre professionnel ou individuel.</li>
  <li>Vous fournissez des informations exactes et confirmez votre adresse
  e-mail. Le numéro de téléphone est facultatif.</li>
  <li>Vous gardez votre mot de passe confidentiel. Toute action faite depuis
  votre compte est réputée faite par vous.</li>
  <li>Un compte est personnel. Il ne peut être ni revendu, ni partagé.</li>
</ul>

<h2>4. Forfaits, prix et paiement</h2>
<ul>
  <li>Le service est vendu par forfaits de durée fixe (1, 3, 5 ou 12 mois). Le
  prix, en francs CFA toutes taxes comprises, et le contenu de chaque forfait
  sont affichés avant le paiement.</li>
  <li>Chaque forfait couvre un routeur. Un routeur supplémentaire est facturé
  au prix du forfait choisi pour ce routeur, affiché avant le paiement.</li>
  <li>Le paiement est traité par notre prestataire <strong>FedaPay</strong>
  (Mobile Money ou carte). HotspotPro ne voit ni ne conserve aucune donnée de
  carte ou de compte Mobile Money.</li>
  <li>Un e-mail confirme chaque paiement et l'activation de votre forfait.</li>
  <li>Il n'y a <strong>aucun renouvellement automatique</strong> : sans nouveau
  paiement, le service s'arrête à la date de fin du forfait.</li>
</ul>

<h2>5. Droit de rétractation et remboursement</h2>
<p>Vous disposez d'un délai de rétractation de dix jours ouvrables, dans les
conditions décrites dans notre
<a href="/legal/remboursement">politique de remboursement et de rétractation</a>,
qui fait partie des présentes conditions.</p>

<h2>6. Vos engagements</h2>
<ul>
  <li>Vous utilisez le service dans le respect des lois applicables, notamment
  celles qui encadrent l'activité de fourniture d'accès Wi-Fi au public.</li>
  <li>Un forfait ne peut pas être utilisé sur un routeur qu'il ne couvre pas. La
  plateforme détecte ce partage (voir la politique de confidentialité) et peut
  suspendre le compte en cas d'abus, après vous avoir permis de vous expliquer.</li>
  <li>Toute tentative d'atteinte à la sécurité ou à la disponibilité de la
  plateforme est interdite.</li>
</ul>

<h2>7. Données de vos vendeurs et de votre activité</h2>
<p>Les noms de vos vendeurs, vos tickets et l'historique de vos ventes sont des
données de <strong>votre</strong> activité. Pour ces données, vous êtes le
responsable du traitement et HotspotPro agit comme sous-traitant, au sens de la
loi n° 2019-014. Nous nous engageons à :</p>
<ul>
  <li>ne les traiter que pour vous rendre le service ;</li>
  <li>les protéger (chiffrement des secrets, accès restreint, sauvegardes
  chiffrées) ;</li>
  <li>ne jamais les vendre ni les utiliser à d'autres fins ;</li>
  <li>les effacer à la suppression de votre compte.</li>
</ul>
<p>Il vous appartient d'informer vos vendeurs que leur nom est enregistré pour
le suivi des ventes.</p>

<h2>8. Disponibilité</h2>
<p>Nous faisons le nécessaire pour que le service soit disponible, sans pouvoir
garantir un fonctionnement sans interruption. Des maintenances ou des incidents
extérieurs (réseau, fournisseur d'accès, électricité, hébergeur) peuvent
l'affecter temporairement. Une interruption qui vous empêche durablement
d'utiliser le service ouvre droit au remboursement prévu par notre politique de
remboursement.</p>

<h2>9. Responsabilité</h2>
<p>HotspotPro est un outil de gestion. Vous restez responsable de votre activité,
de votre routeur et de la relation avec vos propres clients. Sauf faute lourde ou
intentionnelle de notre part, notre responsabilité est limitée aux sommes que
vous nous avez versées au cours des douze derniers mois.</p>

<h2>10. Suspension et fin du contrat</h2>
<ul>
  <li>Vous pouvez supprimer votre compte à tout moment depuis la page
  <em>Mon compte</em>. La suppression est immédiate et définitive.</li>
  <li>Nous pouvons suspendre ou fermer un compte en cas de manquement grave aux
  présentes conditions, après vous en avoir informé par e-mail.</li>
</ul>

<h2>11. Modification des conditions</h2>
<p>Ces conditions peuvent évoluer. Les changements importants vous sont
signalés par e-mail avant leur entrée en vigueur. Un forfait déjà payé reste
régi par les conditions acceptées lors de son paiement.</p>

<h2>12. Droit applicable et litiges</h2>
<p>Les présentes conditions sont soumises au droit togolais. En cas de
désaccord, écrivez-nous d'abord : nous cherchons une solution amiable dans les
trente jours. À défaut, le litige est porté devant les juridictions compétentes
du Togo.</p>
"""


# ═══════════════════════════════════════════════
# POLITIQUE DE CONFIDENTIALITÉ
# ═══════════════════════════════════════════════

_CONFIDENTIALITE = f"""
<p class="lead">Cette politique explique quelles données nous collectons, pourquoi,
combien de temps nous les gardons et comment exercer vos droits. Elle applique
la loi togolaise n° 2019-014 du 29 octobre 2019 relative à la protection des
données à caractère personnel. Nous ne collectons que ce qui est nécessaire au
service. Nous n'utilisons ni publicité, ni outil de mesure d'audience, ni
traceur.</p>

<h2>1. Responsable du traitement</h2>
{_identity_lines()}
{_ipdcp_line()}

<h2>2. Données collectées et pourquoi</h2>
<div class="table-wrap"><table>
<thead><tr><th scope="col">Données</th><th scope="col">Finalité</th>
<th scope="col">Base légale</th></tr></thead>
<tbody>
<tr><td>Nom, adresse e-mail, mot de passe (stocké sous forme hachée,
illisible)</td><td>Créer et sécuriser votre compte, vous contacter au sujet du
service</td><td>Exécution du contrat</td></tr>
<tr><td>Téléphone (facultatif)</td><td>Vous joindre en cas de problème sur votre
installation. Ne pas le donner n'a aucune conséquence.</td><td>Votre
consentement</td></tr>
<tr><td>Forfait, dates, montant, référence de transaction FedaPay, date
d'acceptation des conditions</td><td>Activer le service, facturer, prouver
votre accord</td><td>Exécution du contrat, obligation comptable</td></tr>
<tr><td>Configuration : nom et adresse IP du routeur (facultative), jeton et
identifiant de votre bot Telegram, profils et prix, clés du tunnel et
identifiants techniques du routeur (chiffrés)</td><td>Faire fonctionner le
service sur votre routeur</td><td>Exécution du contrat</td></tr>
<tr><td>Adresse IP publique, nom et numéro de série du routeur, à chaque
notification de vente et via le tunnel</td><td>Sécurité, et détection du
partage d'un forfait entre plusieurs routeurs</td><td>Exécution du contrat
(respect des conditions du forfait)</td></tr>
<tr><td>Adresse IP et e-mail saisis lors d'une connexion échouée</td>
<td>Bloquer les tentatives de piratage de mot de passe</td><td>Sécurité du
service (exécution du contrat)</td></tr>
<tr><td>Support Telegram : identifiant, nom et pseudonyme Telegram, messages</td>
<td>Répondre à vos demandes d'assistance</td><td>Exécution du contrat</td></tr>
</tbody></table></div>
<p>Les champs obligatoires sont signalés sur chaque formulaire. Sans eux, nous
ne pouvons pas créer votre compte ou activer le service.</p>
<p>Pour les tickets, les vendeurs et les ventes enregistrés dans votre espace,
c'est vous le responsable du traitement et nous agissons pour votre compte (voir
l'article 7 des conditions d'utilisation). Nous ne recevons aucune donnée
personnelle des utilisateurs finaux de votre hotspot : seul le code du ticket
utilisé nous parvient.</p>

<h2>3. Destinataires</h2>
<p>Vos données ne sont ni vendues, ni louées, ni utilisées pour de la
publicité. Seuls les prestataires suivants en reçoivent, uniquement pour faire
fonctionner le service :</p>
<ul>
  <li><strong>Amazon Web Services</strong> (hébergement de la plateforme,
  États-Unis) : toutes les données du service ;</li>
  <li><strong>FedaPay</strong> (paiements, Bénin) : votre nom, votre e-mail et
  le montant, au moment du paiement ;</li>
  <li><strong>Brevo</strong> (envoi des e-mails, France) : votre nom, votre
  e-mail et le contenu des e-mails du service ;</li>
  <li><strong>Telegram</strong> (notifications et support) : le contenu des
  messages envoyés à votre bot et au bot de support.</li>
</ul>

<h2>4. Transferts hors du Togo</h2>
<p>Ces prestataires traitent les données hors du Togo, notamment aux
États-Unis pour l'hébergement. Ces transferts sont nécessaires pour vous
fournir le service et sont encadrés conformément aux articles 28 à 30 de la loi
n° 2019-014. Les données sont chiffrées pendant leur transport et les secrets
sensibles sont chiffrés au repos.</p>

<h2>5. Durées de conservation</h2>
<div class="table-wrap"><table>
<thead><tr><th scope="col">Données</th><th scope="col">Durée</th></tr></thead>
<tbody>
<tr><td>Compte et configuration</td><td>Tant que le compte existe. Un compte
est supprimé automatiquement trois ans après la fin de son dernier forfait, ou
un an après sa création s'il n'a jamais souscrit de forfait.</td></tr>
<tr><td>Inscription non confirmée</td><td>Quinze minutes (durée de validité du
code envoyé par e-mail)</td></tr>
<tr><td>Lien de réinitialisation du mot de passe</td><td>Une heure</td></tr>
<tr><td>Paiements encaissés</td><td>Dix ans, obligation comptable de l'Acte
uniforme OHADA. Après suppression du compte, ils sont conservés sans votre nom
ni votre e-mail.</td></tr>
<tr><td>Journal des routeurs (adresse IP, numéro de série)</td><td>Douze mois
après le dernier passage</td></tr>
<tr><td>Connexions échouées</td><td>Vingt-quatre heures</td></tr>
<tr><td>Échanges avec le support et notifications envoyées</td><td>Douze
mois</td></tr>
<tr><td>Journaux techniques du serveur</td><td>Trente jours au plus</td></tr>
<tr><td>Sauvegardes chiffrées</td><td>Quatorze jours, puis effacées</td></tr>
</tbody></table></div>

<h2>6. Sécurité</h2>
<p>Mots de passe hachés, secrets chiffrés au repos, échanges protégés par TLS
(HTTPS) et par un tunnel chiffré avec votre routeur, sauvegardes chiffrées,
accès aux serveurs restreint. En cas de violation de données susceptible de
vous affecter, nous vous en informons dans les meilleurs délais.</p>

<h2>7. Vos droits</h2>
<p>Vous disposez des droits d'accès, de copie, de rectification, de mise à jour,
d'opposition pour motif légitime et de suppression de vos données (articles 39
à 50 de la loi n° 2019-014).</p>
<ul>
  <li><strong>En autonomie</strong>, depuis la page <em>Mon compte</em> : modifier
  vos informations, supprimer votre compte.</li>
  <li><strong>Par e-mail</strong>, à <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>,
  pour obtenir une copie de vos données ou pour toute autre demande. Nous
  répondons dans un délai d'un mois au plus.</li>
</ul>
<p>Si vous estimez que vos droits ne sont pas respectés, vous pouvez saisir
l'<strong>Instance de protection des données à caractère personnel
(IPDCP)</strong> du Togo : <a href="https://ipdcp.tg/">ipdcp.tg</a>.</p>

<h2>8. Prospection</h2>
<p>Nous ne vous envoyons aucune prospection commerciale sans votre accord
préalable (article 26 de la loi n° 2019-014). Les e-mails du service
(confirmation, paiement, échéance, sécurité) ne sont pas de la prospection.</p>

<h2>9. Cookies</h2>
<p>Le site n'utilise qu'un cookie strictement nécessaire à la connexion, et
aucun traceur. Voir notre <a href="/legal/cookies">politique relative aux
cookies</a>.</p>

<h2>10. Modification de cette politique</h2>
<p>Toute modification importante vous est signalée par e-mail. La date de
dernière mise à jour figure en haut de cette page.</p>
"""


# ═══════════════════════════════════════════════
# COOKIES
# ═══════════════════════════════════════════════

_COOKIES = """
<p class="lead">HotspotPro ne dépose aucun cookie publicitaire, aucun cookie de
mesure d'audience et aucun traceur tiers. Le site ne charge aucune ressource
externe : les polices de caractères sont servies par nos propres serveurs.</p>

<h2>1. Ce que le site enregistre dans votre navigateur</h2>
<div class="table-wrap"><table>
<thead><tr><th scope="col">Nom</th><th scope="col">Type</th>
<th scope="col">Rôle</th><th scope="col">Durée</th></tr></thead>
<tbody>
<tr><td><code>session</code></td><td>Cookie</td><td>Garder votre connexion
ouverte et protéger les formulaires contre les requêtes falsifiées (CSRF).
Illisible par les scripts de la page, transmis uniquement en HTTPS.</td>
<td>Jusqu'à la fermeture du navigateur ou la déconnexion</td></tr>
<tr><td><code>dash_tab</code></td><td>Stockage local</td><td>Rouvrir le
dernier onglet consulté du tableau de bord. Jamais envoyé à nos
serveurs.</td><td>Jusqu'à ce que vous vidiez les données du site</td></tr>
<tr><td><code>sellerPos</code></td><td>Stockage de session</td><td>Animer le
déplacement des cartes vendeurs après un ajout. Effacé dès la page
suivante.</td><td>Quelques secondes</td></tr>
</tbody></table></div>

<h2>2. Faut-il votre consentement ?</h2>
<p>Non. L'article 36 de la loi n° 2019-014 dispense de consentement ce qui est
strictement nécessaire au service que vous demandez, ce qui est le cas du cookie
de connexion. Les deux éléments de stockage local servent uniquement au confort
d'affichage, ne quittent jamais votre appareil et ne permettent aucun suivi.
C'est pourquoi le site n'affiche pas de bandeau de cookies. Cette page vous
informe, comme la loi l'exige.</p>

<h2>3. Comment vous y opposer</h2>
<p>Vous pouvez bloquer ou effacer les cookies et les données du site dans les
réglages de votre navigateur. Sans le cookie <code>session</code>, vous ne
pourrez pas vous connecter à votre espace. Les autres éléments peuvent être
effacés sans aucune conséquence.</p>

<h2>4. Sites tiers</h2>
<p>Au moment du paiement, vous êtes redirigé vers la page de
<strong>FedaPay</strong>, qui applique sa propre politique de cookies. Les liens
vers Telegram ouvrent l'application ou le site de Telegram, qui applique ses
propres règles.</p>
"""


# ═══════════════════════════════════════════════
# REMBOURSEMENT ET RÉTRACTATION
# ═══════════════════════════════════════════════

_REMBOURSEMENT = f"""
<p class="lead">Cette politique s'applique à tout forfait HotspotPro, y compris
aux routeurs supplémentaires. Elle applique les articles 44 à 47 de la loi
togolaise n° 2017-07 du 22 juin 2017 relative aux transactions
électroniques.</p>

<h2>1. Délai de rétractation</h2>
<p>Vous pouvez vous rétracter pendant <strong>dix jours ouvrables</strong> à
compter du paiement, sans avoir à vous justifier.</p>

<h2>2. Montant remboursé</h2>
<ul>
  <li><strong>Service pas encore déployé</strong> : si votre forfait n'a pas encore
  été déployé sur votre routeur, vous êtes remboursé intégralement.</li>
  <li><strong>Service déjà en cours</strong> : lors du paiement, vous demandez
  expressément que le service commence tout de suite. Si vous vous rétractez
  ensuite dans le délai, vous êtes remboursé au prorata des jours restants du
  forfait. Les jours déjà utilisés ne sont pas remboursés.</li>
</ul>

<h2>3. Interruption de notre fait</h2>
<p>Si le service est indisponible de notre fait pendant plus de sept jours
consécutifs, vous êtes remboursé au prorata des jours perdus, à tout moment du
forfait et même après le délai de rétractation.</p>

<h2>4. Comment demander un remboursement</h2>
<ol>
  <li>Écrivez à <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a> depuis
  l'adresse de votre compte.</li>
  <li>Indiquez la référence du paiement, visible dans l'e-mail de confirmation
  et dans l'historique de <em>Mon compte</em>.</li>
  <li>Nous accusons réception et vous remboursons dans un délai de
  <strong>dix jours</strong>, par le même moyen de paiement lorsque c'est
  possible, sans frais pour vous.</li>
</ol>
<p>Le remboursement met fin au forfait concerné.</p>

<h2>5. Cas non remboursables</h2>
<ul>
  <li>Une demande faite après le délai de rétractation, sauf interruption de
  notre fait (point 3).</li>
  <li>Une suspension justifiée par un manquement grave aux conditions
  d'utilisation, par exemple le partage d'un forfait sur plusieurs routeurs.</li>
</ul>
"""


# ═══════════════════════════════════════════════
# MENTIONS LÉGALES
# ═══════════════════════════════════════════════

_MENTIONS = f"""
<h2>Éditeur du site</h2>
<p>Le site <strong>{SITE}</strong> et le service <strong>HotspotPro</strong>
sont édités par :</p>
{_identity_lines()}

<h2>Directeur de la publication</h2>
<p>{EDITEUR}</p>

<h2>Hébergement</h2>
<p>{HEBERGEUR}.</p>

<h2>Paiements</h2>
<p>Les paiements en ligne sont traités par <strong>FedaPay</strong>,
prestataire de services de paiement. HotspotPro n'a accès à aucune donnée de
carte ni de compte Mobile Money.</p>

<h2>Données personnelles</h2>
<p>Voir notre <a href="/legal/confidentialite">politique de confidentialité</a>
et notre <a href="/legal/cookies">politique relative aux cookies</a>.</p>
{_ipdcp_line()}

<h2>Propriété intellectuelle</h2>
<p>La marque HotspotPro, le site et ses contenus sont protégés. Toute
reproduction sans autorisation est interdite. Les polices Archivo, Public Sans et
JetBrains Mono sont utilisées sous licence SIL Open Font License 1.1. MikroTik et
RouterOS sont des marques de leur propriétaire ; HotspotPro n'est pas affilié à
MikroTik.</p>
"""


PAGES = {
    "conditions": {
        "title": "Conditions d'utilisation",
        "description": "Conditions d'utilisation de la plateforme HotspotPro : "
                       "forfaits, paiement, obligations et responsabilités.",
        "body":  _CONDITIONS,
    },
    "confidentialite": {
        "title": "Politique de confidentialité",
        "description": "Données collectées par HotspotPro, finalités, durées de "
                       "conservation et droits, selon la loi togolaise n° 2019-014.",
        "body":  _CONFIDENTIALITE,
    },
    "cookies": {
        "title": "Politique relative aux cookies",
        "description": "Cookies et stockage local utilisés par HotspotPro : un "
                       "seul cookie nécessaire, aucun traceur.",
        "body":  _COOKIES,
    },
    "remboursement": {
        "title": "Remboursement et rétractation",
        "description": "Délai de rétractation de dix jours ouvrables et "
                       "conditions de remboursement des forfaits HotspotPro.",
        "body":  _REMBOURSEMENT,
    },
    "mentions-legales": {
        "title": "Mentions légales",
        "description": "Éditeur, hébergeur et informations légales du site "
                       "HotspotPro.",
        "body":  _MENTIONS,
    },
}
