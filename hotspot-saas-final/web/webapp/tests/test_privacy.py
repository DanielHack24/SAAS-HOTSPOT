"""
test_privacy.py — Conformité données personnelles :
consentement prouvé, copie des données, suppression de compte, durées de
conservation (loi togolaise n° 2019-014 ; OHADA pour les pièces comptables).
"""
from datetime import datetime, timedelta

from markupsafe import escape

import config
import privacy
import routes_auth
from db import get_db
from helpers import create_client_row, fetch_all, fetch_one
from legal_content import PAGES, TERMS_VERSION
from security import hash_password


def _csrf(client):
    with client.session_transaction() as s:
        s["_csrf"] = "tok"


def _login(client, cid):
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["is_admin"] = False
        s["_csrf"] = "tok"


def _ago(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _account(email="titulaire@test.tg", pwd="MotDePasse1"):
    conn = get_db()
    cur = conn.execute("INSERT INTO clients (email, password_hash, full_name) VALUES (?,?,?)",
                       (email, hash_password(pwd), "Titulaire Test"))
    cid = cur.lastrowid
    conn.commit()
    conn.close()
    return cid


# ── Pages légales ────────────────────────────────────────────────

def test_les_cinq_pages_legales_repondent(client):
    for slug in ("conditions", "confidentialite", "cookies", "remboursement",
                 "mentions-legales"):
        r = client.get(f"/legal/{slug}")
        assert r.status_code == 200, slug
        # Le gabarit échappe le HTML (apostrophes comprises)
        assert escape(PAGES[slug]["title"]) in r.get_data(as_text=True)


def test_pages_legales_sans_ressource_externe(client):
    """Aucune requête vers un tiers (polices, scripts) : sinon l'adresse IP
    du visiteur partirait à l'étranger sans information ni base légale."""
    for slug in PAGES:
        html = client.get(f"/legal/{slug}").get_data(as_text=True)
        for tiers in ("fonts.googleapis.com", "fonts.gstatic.com",
                      "googletagmanager", "google-analytics"):
            assert tiers not in html, (slug, tiers)


def test_robots_sitemap_llms(client):
    robots = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /admin" in robots and "Sitemap:" in robots
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "/legal/confidentialite" in sitemap
    llms = client.get("/llms.txt").get_data(as_text=True)
    assert "HotspotPro" in llms and "/legal/cookies" in llms


# ── Consentement ─────────────────────────────────────────────────

def test_inscription_refusee_sans_consentement(client, monkeypatch):
    monkeypatch.setattr(config, "brevo_api_key", lambda: "")
    monkeypatch.setattr(routes_auth, "email_welcome", lambda *a, **k: None)
    _csrf(client)
    data = {"csrf_token": "tok", "full_name": "Sans Accord",
            "email": "refus@test.tg", "password": "motdepasse1",
            "password2": "motdepasse1"}
    client.post("/register", data=data)
    assert fetch_all("SELECT * FROM clients WHERE email='refus@test.tg'") == []


def test_consentement_enregistre_avec_version_et_date(client, monkeypatch):
    monkeypatch.setattr(config, "brevo_api_key", lambda: "")
    monkeypatch.setattr(routes_auth, "email_welcome", lambda *a, **k: None)
    _csrf(client)
    client.post("/register", data={"csrf_token": "tok", "full_name": "Avec Accord",
                                   "email": "ok@test.tg", "password": "motdepasse1",
                                   "password2": "motdepasse1", "accept_terms": "1"})
    row = fetch_one("SELECT * FROM clients WHERE email='ok@test.tg'")
    assert row["terms_version"] == TERMS_VERSION
    assert row["terms_accepted_at"]


def test_paiement_refuse_sans_demande_expresse(client):
    cid = _account()
    _login(client, cid)
    r = client.post("/subscribe/checkout", data={"csrf_token": "tok", "plan": "3m"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    assert fetch_all("SELECT * FROM payments WHERE client_id=?", (cid,)) == []


# ── Droit d'accès : copie des données ────────────────────────────

def test_export_contient_les_donnees_et_masque_les_secrets(client):
    cid = _account(email="export@test.tg")
    conn = get_db()
    conn.execute("""INSERT INTO subscriptions (client_id, plan, start_date, end_date,
                                               active, bot_token)
                    VALUES (?, '3m', ?, ?, 1, 'SECRET-TOKEN-1234')""",
                 (cid, _ago(10), _ago(-80)))
    conn.commit()
    conn.close()
    _login(client, cid)
    r = client.get("/account/export")
    assert r.status_code == 200
    data = r.get_json()
    assert data["compte"]["email"] == "export@test.tg"
    assert data["abonnements"][0]["bot_token"] == "…1234"
    assert "SECRET-TOKEN-1234" not in r.get_data(as_text=True)


# ── Droit de suppression ─────────────────────────────────────────

def test_suppression_exige_le_bon_mot_de_passe(client):
    cid = _account(email="garde@test.tg")
    _login(client, cid)
    client.post("/account/delete", data={"csrf_token": "tok", "password": "faux",
                                         "confirm_delete": "1"})
    assert fetch_one("SELECT * FROM clients WHERE id=?", (cid,)) is not None


def test_suppression_efface_le_compte(client):
    cid = _account(email="parti@test.tg")
    conn = get_db()
    conn.execute("""INSERT INTO subscriptions (client_id, plan, start_date, end_date, active)
                    VALUES (?, '1m', ?, ?, 1)""", (cid, _ago(5), _ago(-25)))
    conn.commit()
    conn.close()
    _login(client, cid)
    client.post("/account/delete", data={"csrf_token": "tok",
                                         "password": "MotDePasse1",
                                         "confirm_delete": "1"})
    assert fetch_one("SELECT * FROM clients WHERE id=?", (cid,)) is None
    assert fetch_all("SELECT * FROM subscriptions WHERE client_id=?", (cid,)) == []


def test_suppression_conserve_les_paiements_encaisses_sans_identite(client):
    cid = _account(email="facture@test.tg")
    conn = get_db()
    conn.execute("""INSERT INTO payments (client_id, plan, amount, method, reference, status)
                    VALUES (?, '3m', 5000, 'fedapay', 'tx-ok', 'confirmed')""", (cid,))
    conn.execute("""INSERT INTO payments (client_id, plan, amount, method, reference, status)
                    VALUES (?, '3m', 5000, 'fedapay', 'tx-abandonne', 'pending')""", (cid,))
    conn.commit()
    conn.close()
    _login(client, cid)
    client.post("/account/delete", data={"csrf_token": "tok",
                                         "password": "MotDePasse1",
                                         "confirm_delete": "1"})
    row = fetch_one("SELECT * FROM clients WHERE id=?", (cid,))
    assert row is not None and row["deleted_at"]
    assert "facture@test.tg" not in row["email"]
    assert row["full_name"] == "Compte supprimé"
    assert row["phone"] is None
    refs = [p["reference"] for p in fetch_all("SELECT * FROM payments WHERE client_id=?", (cid,))]
    assert refs == ["tx-ok"]          # la tentative abandonnée est effacée


# ── Durées de conservation ───────────────────────────────────────

def test_purge_des_donnees_expirees(client):
    cid = create_client_row(email="actif@test.tg")
    conn = get_db()
    conn.execute("""INSERT INTO subscriptions (client_id, plan, start_date, end_date, active)
                    VALUES (?, '12m', ?, ?, 1)""", (cid, _ago(5), _ago(-300)))
    conn.execute("""INSERT INTO pending_registrations
                      (email, full_name, password_hash, code_hash, expires_at)
                    VALUES ('vieux@test.tg', 'X', 'h', 'c', ?)""", (_ago(1),))
    conn.execute("INSERT INTO notifications (client_id, type, message, sent_at) VALUES (?,?,?,?)",
                 (cid, "info", "ancienne", _ago(400)))
    conn.execute("INSERT INTO notifications (client_id, type, message, sent_at) VALUES (?,?,?,?)",
                 (cid, "info", "recente", _ago(10)))
    conn.execute("""INSERT INTO support_tickets (chat_id, message, created_at)
                    VALUES ('1', 'vieux ticket', ?)""", (_ago(400),))
    conn.commit()

    res = privacy.run_retention(conn)
    conn.close()

    assert res["inscriptions_expirees"] == 1
    assert fetch_all("SELECT * FROM pending_registrations") == []
    msgs = [n["message"] for n in fetch_all("SELECT * FROM notifications")]
    assert msgs == ["recente"]
    assert fetch_all("SELECT * FROM support_tickets") == []
    # Le compte actif n'est pas touché
    assert fetch_one("SELECT * FROM clients WHERE id=?", (cid,)) is not None


def test_purge_supprime_les_comptes_inactifs_mais_garde_les_recents(client):
    vieux = create_client_row(email="vieux@test.tg")
    recent = create_client_row(email="recent@test.tg")
    conn = get_db()
    # Forfait terminé il y a plus de trois ans -> compte à supprimer
    conn.execute("""INSERT INTO subscriptions (client_id, plan, start_date, end_date, active)
                    VALUES (?, '1m', ?, ?, 0)""", (vieux, _ago(1200), _ago(1100)))
    # Forfait terminé il y a un an -> compte conservé
    conn.execute("""INSERT INTO subscriptions (client_id, plan, start_date, end_date, active)
                    VALUES (?, '1m', ?, ?, 0)""", (recent, _ago(400), _ago(370)))
    conn.commit()
    privacy.run_retention(conn)
    conn.close()

    assert fetch_one("SELECT * FROM clients WHERE id=?", (vieux,)) is None
    assert fetch_one("SELECT * FROM clients WHERE id=?", (recent,)) is not None


def test_purge_conserve_un_compte_jamais_abonne_recent(client):
    cid = create_client_row(email="nouveau@test.tg")
    conn = get_db()
    conn.execute("UPDATE clients SET created_at=? WHERE id=?", (_ago(30), cid))
    conn.commit()
    privacy.run_retention(conn)
    conn.close()
    assert fetch_one("SELECT * FROM clients WHERE id=?", (cid,)) is not None


def test_durees_annoncees_et_appliquees_concordent():
    """La politique de confidentialité annonce des durées : le code doit
    appliquer exactement les mêmes."""
    texte = PAGES["confidentialite"]["body"]
    assert privacy.ACCOUNTING_DAYS == 10 * 365 and "Dix ans" in texte
    assert privacy.ROUTER_ACCESS_DAYS == 365 and "Douze mois" in texte
    assert privacy.ACCOUNT_AFTER_LAST_SUB_DAYS == 3 * 365 and "trois ans" in texte
    assert privacy.ACCOUNT_NEVER_SUBSCRIBED_DAYS == 365 and "un an après sa création" in texte
    assert privacy.SUPPORT_DAYS == 365
