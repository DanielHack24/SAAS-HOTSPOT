"""test_legal.py — Pages legales publiques (CGU, confidentialite, mentions)."""


def test_conditions_ok(client):
    r = client.get("/legal/conditions")
    assert r.status_code == 200
    assert "Conditions".encode() in r.data


def test_confidentialite_ok(client):
    r = client.get("/legal/confidentialite")
    assert r.status_code == 200
    assert b"confidentialite" in r.data.lower() or b"donnees" in r.data.lower()


def test_mentions_ok(client):
    r = client.get("/legal/mentions-legales")
    assert r.status_code == 200
    assert b"Editeur" in r.data or b"diteur" in r.data


def test_slug_inconnu_404(client):
    assert client.get("/legal/nimportequoi").status_code == 404
