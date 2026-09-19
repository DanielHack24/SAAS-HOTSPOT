"""
test_login_ratelimit.py — Anti-bruteforce de la connexion : compteur partagé
entre workers (en base) et limites par compte et par IP.
"""
import security
from db import get_db
from security import hash_password

TOO_MANY = "Trop de tentatives"


def _account(email="cible@test.tg", pwd="BonMotDePasse1"):
    conn = get_db()
    conn.execute("INSERT INTO clients (email, password_hash, full_name) VALUES (?,?,?)",
                 (email, hash_password(pwd), "Cible"))
    conn.commit()
    conn.close()


def _login(client, email, pwd, ip):
    with client.session_transaction() as s:
        s["_csrf"] = "tok"
    return client.post("/login", data={"email": email, "password": pwd, "csrf_token": "tok"},
                       environ_base={"REMOTE_ADDR": ip})


def test_compteur_stocke_en_base_partage_entre_workers(client):
    for _ in range(3):
        security.record_attempt("login:1.1.1.1:x@test.tg")
    # Un autre worker n'a rien en mémoire : il doit voir le compteur en base
    security._attempts.clear()
    assert security.rate_limited("login:1.1.1.1:x@test.tg", max_attempts=3)
    security.clear_attempts("login:1.1.1.1:x@test.tg")
    assert not security.rate_limited("login:1.1.1.1:x@test.tg", max_attempts=3)


def test_un_compte_attaque_depuis_plusieurs_ip_est_bloque(client):
    _account()
    for i in range(20):                       # 20 échecs, 1 par IP différente
        _login(client, "cible@test.tg", "faux", f"10.0.0.{i}")
    r = _login(client, "cible@test.tg", "BonMotDePasse1", "10.0.1.1")
    assert TOO_MANY in r.get_data(as_text=True)


def test_une_ip_qui_essaie_beaucoup_de_comptes_est_bloquee(client):
    _account("autre@test.tg")
    for i in range(30):
        _login(client, f"inconnu{i}@test.tg", "faux", "10.9.9.9")
    r = _login(client, "autre@test.tg", "BonMotDePasse1", "10.9.9.9")
    assert TOO_MANY in r.get_data(as_text=True)


def test_quelques_echecs_puis_succes(client):
    _account()
    for _ in range(3):
        _login(client, "cible@test.tg", "faux", "10.1.1.1")
    r = _login(client, "cible@test.tg", "BonMotDePasse1", "10.1.1.1")
    assert r.status_code == 302
