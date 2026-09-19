"""
test_register_verify.py — Inscription avec vérification e-mail (Brevo actif).

Le compte ne doit exister dans `clients` qu'APRÈS saisie du bon code : une
adresse non confirmée ne crée jamais de compte utilisable.
"""
import config
import routes_auth
from helpers import fetch_one, fetch_all


def _csrf(client):
    with client.session_transaction() as s:
        s["_csrf"] = "tok"


def _form(**kw):
    base = {"csrf_token": "tok", "full_name": "Jean Test",
            "email": "jean@test.tg", "phone": "90000000",
            "password": "motdepasse1", "password2": "motdepasse1", "plan": "3m",
            "accept_terms": "1"}
    base.update(kw)
    return base


def _brevo_on(monkeypatch, code="123456"):
    monkeypatch.setattr(config, "brevo_api_key", lambda: "key")
    monkeypatch.setattr(routes_auth, "_gen_code", lambda: code)
    monkeypatch.setattr(routes_auth, "email_verification_code", lambda *a, **k: None)
    monkeypatch.setattr(routes_auth, "email_welcome", lambda *a, **k: None)


def test_inscription_envoie_code_sans_creer_compte(client, monkeypatch):
    sent = {}
    _brevo_on(monkeypatch)
    monkeypatch.setattr(routes_auth, "email_verification_code",
                        lambda email, name, code: sent.update(email=email, code=code))
    _csrf(client)
    r = client.post("/register", data=_form(), follow_redirects=False)
    assert r.status_code in (302, 303)
    assert "/register/verify" in r.headers["Location"]
    # aucun compte, mais une inscription en attente
    assert fetch_all("SELECT * FROM clients WHERE email='jean@test.tg'") == []
    assert fetch_one("SELECT * FROM pending_registrations WHERE email='jean@test.tg'")
    assert sent["code"] == "123456"


def test_code_correct_cree_le_compte(client, monkeypatch):
    _brevo_on(monkeypatch)
    _csrf(client)
    client.post("/register", data=_form())
    r = client.post("/register/verify",
                    data={"csrf_token": "tok", "code": "123456"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)
    assert fetch_one("SELECT * FROM clients WHERE email='jean@test.tg'") is not None
    assert fetch_all("SELECT * FROM pending_registrations WHERE email='jean@test.tg'") == []


def test_code_incorrect_ne_cree_pas_le_compte(client, monkeypatch):
    _brevo_on(monkeypatch)
    _csrf(client)
    client.post("/register", data=_form())
    client.post("/register/verify", data={"csrf_token": "tok", "code": "000000"})
    assert fetch_all("SELECT * FROM clients WHERE email='jean@test.tg'") == []
    assert fetch_one("SELECT attempts FROM pending_registrations WHERE email='jean@test.tg'")["attempts"] == 1


def test_sans_brevo_creation_directe(client, monkeypatch):
    # Brevo non configuré (dev/local) : le compte est créé immédiatement.
    monkeypatch.setattr(config, "brevo_api_key", lambda: "")
    monkeypatch.setattr(routes_auth, "email_welcome", lambda *a, **k: None)
    _csrf(client)
    r = client.post("/register", data=_form(email="direct@test.tg"), follow_redirects=False)
    assert r.status_code in (302, 303)
    assert fetch_one("SELECT * FROM clients WHERE email='direct@test.tg'") is not None
