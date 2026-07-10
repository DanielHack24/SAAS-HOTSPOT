"""
test_hotspot_sync.py — Poussée des tickets vers le Hotspot local du routeur
(/ip hotspot user), via tunnel, avec un faux routeur injecté. La création se
fait EN MASSE par script exécuté sur le routeur (run_script). Vérifie
l'idempotence, la tolérance aux pannes et le contenu du script.
"""
import re

import pytest

import tickets
import wg_store
import hotspot_sync
from routeros import RouterUnreachable, RouterOSError, RouterOSAuthError


SLUG = "client-a"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "clef-test-hssync-0123456789")
    monkeypatch.setattr(tickets, "SAAS_DIR", str(tmp_path))
    monkeypatch.setattr(wg_store, "CENTRAL_DB", str(tmp_path / "central.db"))
    wg_store.set_server("203.0.113.9")
    wg_store.provision_peer(SLUG)            # tunnel prêt
    dbp = tickets.sales_db_path(SLUG)
    sid = tickets.create_seller(dbp, "Daniel")
    tickets.generate_batch(dbp, sid, "1heure", qty=6, code_len=5, validity="1h")
    return dbp


class FakeRouter:
    """Simule un routeur Hotspot local. Les users sont créés en masse via
    run_script (le doublon est avalé par `on-error={}` côté routeur)."""
    def __init__(self, fail=None):
        self.calls = []           # créations de profils
        self.scripts = []         # sources de script exécutées
        self.fail = fail          # 'offline' | 'auth' | None
        self.users = set()
        self.profiles = set()

    def factory(self, host, user, password):
        self.host = host
        return self

    def ping(self):
        if self.fail == "offline":
            raise RouterUnreachable("timeout")
        if self.fail == "auth":
            raise RouterOSAuthError("401")
        return True

    def get(self, path):
        if self.fail == "offline":
            raise RouterUnreachable("timeout")
        if path.endswith("/user/profile"):
            return [{"name": n} for n in self.profiles]
        return []

    def create(self, path, data):
        if self.fail == "offline":
            raise RouterUnreachable("timeout")
        if self.fail == "auth":
            raise RouterOSAuthError("401")
        self.calls.append((path, data))
        if path.endswith("/user/profile"):
            if data["name"] in self.profiles:
                raise RouterOSError("failure: profile already exists")
            self.profiles.add(data["name"])
        return {}

    def run_script(self, source, name="hotspotpro_bulk"):
        if self.fail == "offline":
            raise RouterUnreachable("timeout")
        if self.fail == "auth":
            raise RouterOSAuthError("401")
        self.scripts.append(source)
        # on-error={} => un doublon n'échoue pas ; le set dédoublonne
        for m in re.finditer(r'name="([^"]+)"', source):
            self.users.add(m.group(1))
        return {}


def test_push_cree_les_utilisateurs_hotspot(env):
    dbp = env
    fake = FakeRouter()
    res = hotspot_sync.push_pending(SLUG, client_factory=fake.factory)
    assert res["status"] == "ok"
    assert res["pushed"] == 6
    assert res["pending"] == 0
    # les 6 tickets sont créés en un seul script, avec profil + durée
    assert len(fake.users) == 6
    assert len(fake.scripts) == 1
    assert 'profile="1heure"' in fake.scripts[0]
    assert 'limit-uptime="1h"' in fake.scripts[0]
    # atteint bien l'IP de tunnel du routeur
    assert fake.host == "10.66.0.2"
    assert tickets.pending_push(dbp) == []
    assert tickets.push_counts(dbp)["pending"] == 0


def test_profil_hotspot_cree_si_absent(env):
    fake = FakeRouter()
    hotspot_sync.push_pending(SLUG, client_factory=fake.factory)
    prof_calls = [c for c in fake.calls if c[0] == "/ip/hotspot/user/profile"]
    assert prof_calls and prof_calls[0][1]["name"] == "1heure"


def test_push_idempotent(env):
    fake = FakeRouter()
    hotspot_sync.push_pending(SLUG, client_factory=fake.factory)
    fake2 = FakeRouter()
    res = hotspot_sync.push_pending(SLUG, client_factory=fake2.factory)
    assert res["status"] == "ok"
    assert res["pushed"] == 0          # déjà synchronisés
    assert fake2.scripts == []


def test_doublon_ignore(env):
    """Un utilisateur déjà présent sur le routeur (« already have user »)
    ne doit pas faire échouer la poussée (on-error côté routeur)."""
    dbp = env
    fake = FakeRouter()
    fake.users.add(list({t["username"] for t in tickets.pending_push(dbp)})[0])
    res = hotspot_sync.push_pending(SLUG, client_factory=fake.factory)
    assert res["status"] == "ok"
    assert res["pushed"] == 6           # le paquet entier est marqué poussé


def test_routeur_hors_ligne_garde_les_tickets(env):
    dbp = env
    fake = FakeRouter(fail="offline")
    res = hotspot_sync.push_pending(SLUG, client_factory=fake.factory)
    assert res["status"] == "offline"
    assert res["pushed"] == 0
    assert len(tickets.pending_push(dbp)) == 6


def test_identifiants_refuses(env):
    dbp = env
    fake = FakeRouter(fail="auth")
    res = hotspot_sync.push_pending(SLUG, client_factory=fake.factory)
    assert res["status"] == "auth"
    assert len(tickets.pending_push(dbp)) == 6


def test_sans_tunnel(env, monkeypatch):
    monkeypatch.setattr(wg_store, "get_peer", lambda slug: None)
    res = hotspot_sync.push_pending(SLUG, client_factory=FakeRouter().factory)
    assert res["status"] == "no_tunnel"
    assert res["pending"] == 6
