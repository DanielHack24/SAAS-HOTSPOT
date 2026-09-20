"""
reviews.py — Avis clients affichés sur la page d'accueil.

Parcours : le client écrit son avis depuis « Mon compte » -> l'avis arrive en
attente -> l'administrateur l'affiche (il rejoint alors le carrousel de la
page d'accueil) ou le supprime.

Un avis n'est publié qu'avec l'accord explicite de son auteur (publication de
son nom et de son activité) : voir le formulaire dans account.html.

TEXTES DE DÉMONSTRATION : la liste DEMO_TESTIMONIALS ci-dessous n'est PAS
composée d'avis réels de clients. Elle est conservée à la demande du
propriétaire du site. Présenter des avis fictifs comme authentiques est une
pratique commerciale trompeuse : pour n'afficher que de vrais avis, videz
cette liste (DEMO_TESTIMONIALS = []) — le reste continue de fonctionner.
"""
from datetime import datetime

MAX_QUOTE = 400
MAX_NAME = 60
MAX_ROLE = 80

# Palette d'avatars, choisie par l'identifiant de l'avis (rendu stable).
COLORS = ["#C2410C", "#1D4ED8", "#15803D", "#7C3AED", "#0E7490",
          "#B45309", "#12263A", "#BE185D"]

DEMO_TESTIMONIALS = [
    ("Avant, je comptais mes ventes à la main. Maintenant mon Telegram me dit tout en temps réel. J'ai gagné deux heures par jour.", "Koami A.", "Opérateur hotspot — Lomé", "#C2410C", 5),
    ("Le script MikroTik était prêt en cinq minutes. J'ai juste copié-collé et ça fonctionnait. Les stats arrivent automatiquement.", "Yao M.", "Gérant cybercafé — Tsévié", "#1D4ED8", 5),
    ("J'avais peur que ce soit compliqué. Le support m'a aidé et tout était opérationnel le soir même.", "Akossiwa D.", "Revendeuse tickets — Kpalimé", "#15803D", 5),
    ("Le paiement par Mobile Money est vraiment pratique. Pas besoin de carte bancaire. Confirmation en moins d'une heure.", "Biossey K.", "Opérateur hotspot — Sokodé", "#7C3AED", 4),
    ("Je surveille mes ventes depuis mon téléphone, même quand je ne suis pas sur place. C'est exactement ce qu'il me fallait.", "Edem T.", "Multi-sites hotspot — Atakpamé", "#0E7490", 5),
    ("J'ai recommandé HotspotPro à quatre collègues. Le rapport qualité-prix est imbattable comparé aux autres solutions.", "Sena A.", "Opérateur hotspot — Lomé, Bè", "#B45309", 5),
    ("Telegram me montre qui a vendu quoi, quand et combien. Fini les disputes avec mes vendeurs à la fin du mois.", "Dodzi K.", "Gérant multi-vendeurs — Agbodrafo", "#12263A", 5),
    ("Installation en dix minutes chrono. Je pensais que ça prendrait des heures. Franchement convaincant.", "Mawuli H.", "Cybercafé — Aného", "#BE185D", 5),
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def submit(conn, client_id: int, author_name: str, author_role: str,
           quote: str, stars: int) -> int:
    """Enregistre un avis en attente de validation. Retourne son identifiant."""
    stars = max(1, min(5, int(stars or 5)))
    cur = conn.execute("""
        INSERT INTO testimonials (client_id, author_name, author_role, quote,
                                  stars, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (client_id, author_name.strip()[:MAX_NAME], author_role.strip()[:MAX_ROLE],
          quote.strip()[:MAX_QUOTE], stars, _now()))
    return cur.lastrowid


def last_submission(conn, client_id: int):
    row = conn.execute("""SELECT * FROM testimonials WHERE client_id=?
                          ORDER BY id DESC LIMIT 1""", (client_id,)).fetchone()
    return dict(row) if row else None


def listing(conn, status: str = None) -> list[dict]:
    if status:
        rows = conn.execute("""SELECT t.*, c.email FROM testimonials t
                               LEFT JOIN clients c ON c.id = t.client_id
                               WHERE t.status=? ORDER BY t.id DESC""", (status,))
    else:
        rows = conn.execute("""SELECT t.*, c.email FROM testimonials t
                               LEFT JOIN clients c ON c.id = t.client_id
                               ORDER BY t.id DESC""")
    return [dict(r) for r in rows.fetchall()]


def pending_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM testimonials WHERE status='pending'"
                        ).fetchone()[0]


def set_status(conn, tid: int, status: str) -> bool:
    """« published » l'ajoute au carrousel, « pending » l'en retire."""
    if status not in ("published", "pending"):
        return False
    cur = conn.execute("UPDATE testimonials SET status=?, published_at=? WHERE id=?",
                       (status, _now() if status == "published" else None, tid))
    return cur.rowcount > 0


def remove(conn, tid: int) -> bool:
    return conn.execute("DELETE FROM testimonials WHERE id=?", (tid,)).rowcount > 0


def for_landing(conn) -> list[tuple]:
    """Avis affichés : les avis publiés (les plus récents d'abord), suivis des
    textes de démonstration s'il en reste dans DEMO_TESTIMONIALS."""
    rows = conn.execute("""SELECT id, author_name, author_role, quote, stars
                           FROM testimonials WHERE status='published'
                           ORDER BY published_at DESC, id DESC""").fetchall()
    published = [(r["quote"], r["author_name"], r["author_role"],
                  COLORS[r["id"] % len(COLORS)], r["stars"]) for r in rows]
    return published + DEMO_TESTIMONIALS
