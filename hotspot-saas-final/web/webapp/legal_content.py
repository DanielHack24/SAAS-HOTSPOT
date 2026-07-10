"""
legal_content.py — Contenu des pages legales (CGU, confidentialite, mentions).

Formulation regionale « Afrique de l'Ouest » (pas de pays impose), editeur
particulier. Les valeurs ci-dessous (EDITEUR, CONTACT_EMAIL) sont a ajuster si
besoin. Le contenu decrit fidelement ce que la plateforme fait reellement.
"""

EDITEUR       = "SEKAYA Kossi Rodrigue"
CONTACT_EMAIL = "support@hotpotpro.app"
SITE          = "hotpotpro.app"
UPDATED       = "9 juillet 2026"


_CONDITIONS = f"""
<p class="lead">Les presentes conditions regissent l'utilisation de la plateforme
HotspotPro. En creant un compte ou en utilisant le service, vous les acceptez.</p>

<h2>1. Objet</h2>
<p>HotspotPro est une plateforme en ligne (SaaS) destinee aux operateurs de
points d'acces Wi-Fi (hotspots) utilisant des routeurs MikroTik. Elle permet la
gestion des ventes de tickets, le suivi des vendeurs, la generation de scripts
RouterOS, un acces distant securise au routeur (VPN) et un support par Telegram.</p>

<h2>2. Compte et inscription</h2>
<ul>
  <li>Vous devez fournir des informations exactes (nom, e-mail, telephone) et
  confirmer votre adresse e-mail.</li>
  <li>Vous etes responsable de la confidentialite de votre mot de passe et de
  toute activite realisee depuis votre compte.</li>
  <li>Un compte est personnel. Sa revente ou son partage n'est pas autorise.</li>
</ul>

<h2>3. Abonnements et paiements</h2>
<ul>
  <li>Le service est propose par abonnements de duree fixe (1, 3, 5 ou 12 mois).</li>
  <li>Les paiements sont traites par notre prestataire <strong>FedaPay</strong>
  (Mobile Money, carte). HotspotPro ne stocke aucune donnee bancaire.</li>
  <li>L'abonnement est active des la confirmation du paiement. Sauf disposition
  legale imperative, les sommes versees ne sont pas remboursables une fois le
  service active.</li>
  <li>Chaque abonnement couvre un routeur principal. Les routeurs
  supplementaires font l'objet d'un supplement indique lors de la commande.</li>
  <li>L'abonnement n'est pas reconduit automatiquement : il vous appartient de le
  renouveler avant l'echeance pour eviter toute interruption.</li>
</ul>

<h2>4. Utilisation acceptable</h2>
<ul>
  <li>Vous vous engagez a utiliser le service dans le respect des lois
  applicables et des droits des tiers.</li>
  <li>Le partage ou la copie d'un abonnement sur un routeur non couvert est
  interdit. Des mecanismes de detection (empreinte materielle du routeur)
  peuvent entrainer la suspension en cas d'abus.</li>
  <li>Toute tentative d'atteinte a la securite ou a la disponibilite de la
  plateforme est prohibee.</li>
</ul>

<h2>5. Disponibilite et maintenance</h2>
<p>Nous nous efforcons d'assurer une haute disponibilite du service, sans pouvoir
garantir un fonctionnement ininterrompu. Des operations de maintenance ou des
incidents independants de notre volonte (reseau, fournisseur d'acces, coupures
electriques) peuvent affecter temporairement le service.</p>

<h2>6. Responsabilite</h2>
<p>HotspotPro fournit un outil de gestion. Vous restez seul responsable de votre
activite d'operateur, de la conformite de votre exploitation et de la relation
avec vos propres clients. Dans la limite permise par la loi, notre responsabilite
ne saurait exceder les montants que vous avez verses au cours des douze derniers
mois.</p>

<h2>7. Suspension et resiliation</h2>
<p>Nous pouvons suspendre ou fermer un compte en cas de non-respect des presentes
conditions. Vous pouvez a tout moment cesser d'utiliser le service et demander la
suppression de votre compte.</p>

<h2>8. Modification des conditions</h2>
<p>Ces conditions peuvent evoluer. La version en vigueur est celle publiee sur
cette page. Les changements importants vous seront signales.</p>

<h2>9. Droit applicable</h2>
<p>Le service s'adresse aux operateurs d'Afrique de l'Ouest. Tout differend sera
d'abord recherche a l'amiable. A defaut, il sera soumis aux juridictions
competentes du lieu d'etablissement de l'editeur.</p>

<h2>10. Contact</h2>
<p>Pour toute question : <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>
"""


_CONFIDENTIALITE = f"""
<p class="lead">Cette politique explique quelles donnees nous collectons, pourquoi,
et vos droits. Nous appliquons le principe de minimisation : seules les donnees
utiles au service sont traitees.</p>

<h2>1. Donnees collectees</h2>
<ul>
  <li><strong>Identite et contact</strong> : nom, adresse e-mail, numero de
  telephone.</li>
  <li><strong>Paiement</strong> : gere par FedaPay. Nous conservons la reference
  de transaction et le statut, jamais les numeros de carte.</li>
  <li><strong>Donnees techniques d'exploitation</strong> : identifiant et numero
  de serie du routeur, adresse IP publique de connexion, etat du tunnel VPN.
  Ces donnees servent au fonctionnement et a la detection de partage
  d'abonnement.</li>
  <li><strong>Support</strong> : si vous utilisez le bot Telegram, votre
  identifiant de conversation et le contenu de vos messages de support.</li>
</ul>

<h2>2. Finalites</h2>
<ul>
  <li>Fournir et securiser le service (comptes, abonnements, VPN).</li>
  <li>Traiter les paiements et activer les abonnements.</li>
  <li>Assurer le support et vous envoyer des notifications utiles
  (confirmation, expiration, incident routeur).</li>
  <li>Prevenir la fraude et l'usage abusif.</li>
</ul>

<h2>3. Sous-traitants</h2>
<p>Nous faisons appel a des prestataires strictement pour executer le service :
<strong>FedaPay</strong> (paiements), <strong>Brevo</strong> (envoi d'e-mails),
<strong>Telegram</strong> (support), et un hebergeur d'infrastructure
(<strong>Amazon Web Services</strong>). Nous ne vendons jamais vos donnees.</p>

<h2>4. Conservation</h2>
<p>Vos donnees sont conservees le temps de la relation contractuelle, puis pour
la duree necessaire au respect de nos obligations legales et comptables. La
suppression de votre compte entraine l'effacement de vos donnees associees, sous
reserve des obligations legales de conservation.</p>

<h2>5. Securite</h2>
<p>Les mots de passe sont stockes sous forme hachee (non reversible). Les secrets
sensibles sont chiffres au repos. Les echanges avec la plateforme sont proteges
par TLS (HTTPS).</p>

<h2>6. Vos droits</h2>
<p>Vous pouvez demander l'acces, la rectification ou la suppression de vos
donnees. La reinitialisation du mot de passe et la suppression de compte sont
accessibles directement ; pour toute autre demande, ecrivez a
<a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>

<h2>7. Cookies</h2>
<p>Le site utilise uniquement un cookie de session strictement necessaire a
l'authentification. Aucun cookie publicitaire ou de suivi tiers n'est depose.</p>

<h2>8. Contact</h2>
<p>Questions relatives a vos donnees : <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>
"""


_MENTIONS = f"""
<h2>Editeur</h2>
<p>Le service <strong>HotspotPro</strong> est edite par <strong>{EDITEUR}</strong>,
operateur individuel, exercant en Afrique de l'Ouest.</p>
<p>Contact : <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>

<h2>Directeur de la publication</h2>
<p>{EDITEUR}</p>

<h2>Hebergement</h2>
<p>La plateforme est hebergee sur l'infrastructure <strong>Amazon Web Services
(AWS)</strong>.</p>

<h2>Paiements</h2>
<p>Les paiements en ligne sont traites par <strong>FedaPay</strong>, prestataire
de services de paiement. HotspotPro n'a acces a aucune donnee bancaire.</p>

<h2>Propriete intellectuelle</h2>
<p>La marque HotspotPro, le site et ses contenus sont proteges. Toute
reproduction non autorisee est interdite.</p>

<h2>Contact</h2>
<p>Pour toute question relative au site : <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a>.</p>
"""


PAGES = {
    "conditions": {
        "title": "Conditions d'utilisation",
        "body":  _CONDITIONS,
    },
    "confidentialite": {
        "title": "Politique de confidentialite",
        "body":  _CONFIDENTIALITE,
    },
    "mentions-legales": {
        "title": "Mentions legales",
        "body":  _MENTIONS,
    },
}
