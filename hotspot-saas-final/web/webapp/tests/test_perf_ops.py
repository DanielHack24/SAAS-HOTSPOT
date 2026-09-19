"""
test_perf_ops.py — Optimisations de charge : alerte processeur du watchdog,
connexions Telegram persistantes du hub, réglages SQLite.
"""
import http.client
import json
import os
import sys

import pytest

DEPLOY = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "deploy"))
HUB_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "saas", "src"))


# ── Watchdog : alerte processeur ─────────────────────────────────

@pytest.fixture()
def wd(monkeypatch):
    if DEPLOY not in sys.path:
        sys.path.insert(0, DEPLOY)
    import watchdog
    sent = []
    monkeypatch.setattr(watchdog, "telegram", sent.append)
    monkeypatch.setattr(watchdog, "log", lambda m: None)
    monkeypatch.setattr(watchdog, "CPU_ALERT", 85)
    monkeypatch.setattr(watchdog, "STEAL_ALERT", 30)
    watchdog.sent = sent
    return watchdog


def _run(wd, monkeypatch, state, busy, steal):
    monkeypatch.setattr(wd, "cpu_usage", lambda: (busy, steal))
    wd.check_cpu(state)


def test_pic_isole_pas_d_alerte(wd, monkeypatch):
    state = {}
    _run(wd, monkeypatch, state, 99, 0)
    _run(wd, monkeypatch, state, 10, 0)
    assert wd.sent == []


def test_charge_soutenue_une_seule_alerte_puis_retour(wd, monkeypatch):
    state = {}
    for _ in range(5):
        _run(wd, monkeypatch, state, 95, 0)
    assert len(wd.sent) == 1 and "occupé à 95%" in wd.sent[0]
    _run(wd, monkeypatch, state, 20, 0)
    assert len(wd.sent) == 2 and "normale" in wd.sent[1]


def test_processeur_bride_par_aws(wd, monkeypatch):
    state = {}
    for _ in range(3):
        _run(wd, monkeypatch, state, 40, 55)
    assert len(wd.sent) == 1 and "crédits CPU" in wd.sent[0]


def test_cpu_usage_lit_proc_stat(wd, monkeypatch):
    samples = iter([(1000, 500, 0), (2000, 1400, 100)])
    monkeypatch.setattr(wd, "_cpu_times", lambda: next(samples))
    monkeypatch.setattr(wd.time, "sleep", lambda s: None)
    assert wd.cpu_usage() == (90, 10)


# ── Hub : Telegram en keep-alive ─────────────────────────────────

class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body


class FakeConn:
    instances = []

    def __init__(self, host, timeout=None):
        self.host, self.timeout, self.sock = host, timeout, None
        self.requests, self.closed, self.script = [], False, []
        FakeConn.instances.append(self)

    def request(self, method, url, body=None, headers=None):
        self.requests.append(url)
        if self.script:
            exc = self.script.pop(0)
            if exc:
                raise exc

    def getresponse(self):
        return FakeResp(200, json.dumps({"ok": True, "result": []}).encode())

    def close(self):
        self.closed = True


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    import tenant_db
    monkeypatch.setattr(tenant_db, "SAAS_DIR", str(tmp_path))
    monkeypatch.setattr(tenant_db, "CENTRAL_DB", str(tmp_path / "central.db"))
    tenant_db.init_central_db()
    if HUB_SRC not in sys.path:
        sys.path.insert(0, HUB_SRC)
    import tenant_hub
    FakeConn.instances = []
    monkeypatch.setattr(tenant_hub.http.client, "HTTPSConnection", FakeConn)
    tenant_hub._tg_local.conn = None
    yield tenant_hub
    tenant_hub._tg_local.conn = None


def test_connexion_reutilisee_entre_appels(hub):
    for _ in range(5):
        assert hub._tg("TOKEN", "sendMessage", {"chat_id": 1, "text": "x"}) == {"ok": True, "result": []}
    assert len(FakeConn.instances) == 1
    assert len(FakeConn.instances[0].requests) == 5
    assert FakeConn.instances[0].host == "api.telegram.org"


def test_connexion_fermee_par_telegram_rouverte_une_fois(hub):
    hub._tg("TOKEN", "getMe", {})
    FakeConn.instances[0].script = [http.client.RemoteDisconnected("fermée")]
    assert hub._tg("TOKEN", "sendMessage", {"chat_id": 1, "text": "x"})["ok"] is True
    assert len(FakeConn.instances) == 2 and FakeConn.instances[0].closed


def test_delai_depasse_pas_de_doublon(hub):
    hub._tg("TOKEN", "getMe", {})
    FakeConn.instances[0].script = [TimeoutError()]
    assert hub._tg("TOKEN", "sendMessage", {"chat_id": 1, "text": "x"}) == {}
    # la requête n'a PAS été renvoyée (elle a pu être traitée par Telegram)
    assert len(FakeConn.instances) == 1


def test_sans_token_aucun_appel(hub):
    assert hub._tg("", "sendMessage", {}) == {}
    assert FakeConn.instances == []


# ── SQLite ───────────────────────────────────────────────────────

def test_connexions_en_wal_synchronous_normal(tmp_path):
    import dbconn
    conn = dbconn.connect(str(tmp_path / "x.db"))
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA synchronous").fetchone()[0] == 1   # NORMAL
    conn.close()


def test_schema_tickets_verifie_une_seule_fois(tmp_path, monkeypatch):
    import tickets
    calls = []
    real = tickets._ensure_schema_now
    monkeypatch.setattr(tickets, "_ensure_schema_now", lambda p: (calls.append(p), real(p)))
    db = str(tmp_path / "t" / "sales.db")
    for _ in range(3):
        tickets.ensure_schema(db)
    assert len(calls) == 1


def test_temoins_plafonnes(tmp_path, monkeypatch):
    import dbconn
    from collections import OrderedDict
    monkeypatch.setattr(dbconn, "_keepers", OrderedDict())
    monkeypatch.setattr(dbconn, "MAX_KEEPERS", 3)
    for i in range(5):
        p = str(tmp_path / f"b{i}.db")
        dbconn.connect(p).close()
        dbconn.keep_open(p)
    assert len(dbconn._keepers) == 3
    assert os.path.abspath(str(tmp_path / "b4.db")) in dbconn._keepers
    for c, _ in dbconn._keepers.values():
        c.close()


def test_temoin_ignore_fichier_absent(tmp_path, monkeypatch):
    import dbconn
    from collections import OrderedDict
    monkeypatch.setattr(dbconn, "_keepers", OrderedDict())
    dbconn.keep_open(str(tmp_path / "absent.db"))
    assert len(dbconn._keepers) == 0
    assert not (tmp_path / "absent.db").exists()
