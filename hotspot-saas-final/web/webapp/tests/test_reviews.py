"""
test_reviews.py — Avis clients : dépôt par le client, validation par
l'administrateur, affichage sur la page d'accueil.
"""
import reviews
from db import get_db
from helpers import create_client_row, fetch_all, fetch_one


def _login(client, cid, admin=False):
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["is_admin"] = admin
        s["_csrf"] = "tok"


def _admin(client):
    cid = create_client_row(email="patron@test.tg", name="Patron")
    conn = get_db()
    conn.execute("UPDATE clients SET is_admin=1 WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    _login(client, cid, admin=True)
    return cid


AVIS = "Les notifications Telegram me font gagner du temps chaque jour."


def _post_review(client, quote=AVIS, consent="1", role="Opérateur — Lomé"):
    return client.post("/avis", data={"csrf_token": "tok", "quote": quote,
                                      "author_name": "Koffi A.",
                                      "author_role": role, "stars": "4",
                                      "accept_publication": consent})


# ── Dépôt par le client ──────────────────────────────────────────

def test_avis_enregistre_en_attente(client):
    cid = create_client_row(email="client@test.tg")
    _login(client, cid)
    _post_review(client)
    row = fetch_one("SELECT * FROM testimonials")
    assert row["status"] == "pending"
    assert row["author_name"] == "Koffi A." and row["stars"] == 4
    assert row["client_id"] == cid


def test_avis_refuse_sans_accord_de_publication(client):
    cid = create_client_row(email="sansaccord@test.tg")
    _login(client, cid)
    _post_review(client, consent="")
    assert fetch_all("SELECT * FROM testimonials") == []


def test_avis_trop_court_refuse(client):
    cid = create_client_row(email="court@test.tg")
    _login(client, cid)
    _post_review(client, quote="Super")
    assert fetch_all("SELECT * FROM testimonials") == []


def test_avis_exige_une_connexion(client):
    # Sans session : rejet CSRF (400) ou redirection vers la connexion.
    r = _post_review(client)
    assert r.status_code in (302, 303, 400)
    assert fetch_all("SELECT * FROM testimonials") == []


# ── Validation par l'administrateur ──────────────────────────────

def test_avis_en_attente_absent_de_la_page_d_accueil(client):
    cid = create_client_row(email="attente@test.tg")
    _login(client, cid)
    _post_review(client)
    assert AVIS.encode() not in client.get("/").data


def test_publication_ajoute_l_avis_au_carrousel(client):
    cid = create_client_row(email="publie@test.tg")
    _login(client, cid)
    _post_review(client)
    tid = fetch_one("SELECT * FROM testimonials")["id"]

    _admin(client)
    client.post(f"/admin/avis/{tid}/publish", data={"csrf_token": "tok"})

    assert fetch_one("SELECT * FROM testimonials")["status"] == "published"
    page = client.get("/").get_data(as_text=True)
    assert AVIS in page
    # Les témoignages déjà en place restent affichés
    assert reviews.DEMO_TESTIMONIALS[0][1] in page


def test_retrait_enleve_l_avis_du_site(client):
    cid = create_client_row(email="retire@test.tg")
    _login(client, cid)
    _post_review(client)
    tid = fetch_one("SELECT * FROM testimonials")["id"]
    _admin(client)
    client.post(f"/admin/avis/{tid}/publish", data={"csrf_token": "tok"})
    client.post(f"/admin/avis/{tid}/unpublish", data={"csrf_token": "tok"})
    assert AVIS.encode() not in client.get("/").data


def test_suppression_par_admin(client):
    cid = create_client_row(email="efface@test.tg")
    _login(client, cid)
    _post_review(client)
    tid = fetch_one("SELECT * FROM testimonials")["id"]
    _admin(client)
    client.post(f"/admin/avis/{tid}/delete", data={"csrf_token": "tok"})
    assert fetch_all("SELECT * FROM testimonials") == []


def test_page_des_avis_reservee_a_l_admin(client):
    cid = create_client_row(email="curieux@test.tg")
    _login(client, cid)
    assert client.get("/admin/avis").status_code in (301, 302)


def test_client_ne_peut_pas_publier_son_propre_avis(client):
    cid = create_client_row(email="malin@test.tg")
    _login(client, cid)
    _post_review(client)
    tid = fetch_one("SELECT * FROM testimonials")["id"]
    client.post(f"/admin/avis/{tid}/publish", data={"csrf_token": "tok"})
    assert fetch_one("SELECT * FROM testimonials")["status"] == "pending"


# ── Suppression de compte ────────────────────────────────────────

def test_avis_supprime_avec_le_compte(client):
    import privacy
    cid = create_client_row(email="adieu@test.tg")
    _login(client, cid)
    _post_review(client)
    conn = get_db()
    privacy.delete_client_account(conn, cid)
    conn.commit()
    conn.close()
    assert fetch_all("SELECT * FROM testimonials") == []
