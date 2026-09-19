"""
test_security_fixes.py — Correctifs de l'audit de sécurité :
jeton routeur en en-tête, refus des tenants sans jeton, droits minimaux du
compte plateforme sur le routeur, filtrage du trafic entre pairs WireGuard,
validation de la destination de sauvegarde.
"""
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(__file__)
HUB_SRC = os.path.abspath(os.path.join(HERE, "..", "..", "..", "saas", "src"))
DEPLOY = os.path.abspath(os.path.join(HERE, "..", "..", "..", "deploy"))


# ── Jeton du routeur ─────────────────────────────────────────────

def test_script_on_login_jeton_en_entete_pas_dans_url():
    import mikrotik_scripts as mks
    tok = "AbC-123_xyz"
    for script in (mks.generate_oneliner({"slug": "s1", "router_token": tok}, "1.2.3.4"),
                   mks.mikhmon_onlogin(100, "1d", "http://1.2.3.4/t/s1/login", tok)):
        assert f'http-header-field="X-Router-Token: {tok}"' in script
        assert "token=" not in script


@pytest.fixture()
def hub(tmp_path, monkeypatch):
    import tenant_db
    monkeypatch.setattr(tenant_db, "SAAS_DIR", str(tmp_path))
    monkeypatch.setattr(tenant_db, "CENTRAL_DB", str(tmp_path / "central.db"))
    tenant_db.init_central_db()
    if HUB_SRC not in sys.path:
        sys.path.insert(0, HUB_SRC)
    import tenant_hub
    tenants = {
        "avec": {"slug": "avec", "active": True, "router_token": "bon", "chat_id": "",
                 "bot_token": "", "router_name": "", "price_map": {}},
        "legacy": {"slug": "legacy", "active": True, "router_token": "", "chat_id": "",
                   "bot_token": "", "router_name": "", "price_map": {}},
    }

    class Reg:
        def get(self, s):
            return tenants.get(s)

    submitted = []

    class Exe:
        def submit(self, fn, *a):
            submitted.append(a)

    monkeypatch.setattr(tenant_hub, "registry", Reg())
    monkeypatch.setattr(tenant_hub, "_rate", {})
    monkeypatch.setattr(tenant_hub, "_sale_executor", Exe())
    monkeypatch.setattr(tenant_hub.access_log, "record", lambda *a, **k: None)
    tenant_hub.submitted = submitted
    return tenant_hub


def test_hub_accepte_jeton_en_entete(hub):
    c = hub.app.test_client()
    c.get("/t/avec/login?username=U1", headers={"X-Router-Token": "bon"})
    assert len(hub.submitted) == 1


def test_hub_accepte_encore_ancien_jeton_en_url(hub):
    c = hub.app.test_client()
    c.get("/t/avec/login?username=U1&token=bon")
    assert len(hub.submitted) == 1


def test_hub_refuse_mauvais_jeton_en_entete(hub):
    c = hub.app.test_client()
    c.get("/t/avec/login?username=U1", headers={"X-Router-Token": "faux"})
    assert hub.submitted == []


def test_hub_refuse_tenant_sans_jeton(hub):
    c = hub.app.test_client()
    c.get("/t/legacy/login?username=U1")
    c.get("/t/legacy/login?username=U2&token=nimporte")
    assert hub.submitted == []


# ── Droits du compte plateforme sur le routeur ───────────────────

def test_compte_routeur_sans_droits_sensibles():
    import wireguard
    droits = wireguard.API_POLICY.split(",")
    for interdit in ("winbox", "password", "sensitive", "policy"):
        assert interdit not in droits


def test_groupe_mis_a_jour_sur_routeur_existant():
    import wireguard
    block = wireguard.render_client_block(
        tunnel_ip="10.66.0.7", router_private_key="K", server_public_key="S",
        vps_endpoint="198.51.100.10", api_user="hotspotpro", api_pass="Abc23xyz")
    assert (f"/user/group/set [find name=hotspotpro] policy={wireguard.API_POLICY}"
            in block)
    assert "winbox" not in block and "sensitive" not in block


def test_script_temporaire_politique_restreinte():
    import routeros
    import wireguard
    created = []

    class R(routeros.RouterOSRest):
        def __init__(self):
            pass

        def get(self, path):
            return []

        def create(self, path, data):
            created.append(data)

        def _req(self, *a, **k):
            return None

        def delete(self, path):
            pass

    R().run_script("/log info x")
    assert created[0]["policy"] == routeros.SCRIPT_POLICY
    # le script ne peut pas demander plus de droits que le compte n'en a
    assert set(routeros.SCRIPT_POLICY.split(",")) <= set(wireguard.API_POLICY.split(","))


# ── Filtrage du trafic entre pairs WireGuard ─────────────────────

@pytest.fixture()
def wgs():
    if DEPLOY not in sys.path:
        sys.path.insert(0, DEPLOY)
    import wg_sync
    return wg_sync


def test_regles_chaque_vpn_vers_son_routeur_seulement(wgs):
    rules = wgs.forward_rules(
        {"a": "10.66.0.2", "b": "10.66.0.5"},
        [{"slug": "a", "tunnel_ip": "10.66.0.3"}, {"slug": "orphelin", "tunnel_ip": "10.66.0.9"}])
    assert rules == [
        ["-s", "10.66.0.3/32", "-d", "10.66.0.2/32", "-j", "ACCEPT"],
        ["-s", "10.66.0.2/32", "-d", "10.66.0.3/32", "-j", "ACCEPT"],
        ["-j", "DROP"],
    ]


class FakeIpt:
    """Simule iptables : chaînes, règle ACCEPT historique, sortie -S."""

    def __init__(self):
        self.chains = {}
        self.forward = ["-i wg-hotspotpro -o wg-hotspotpro -j ACCEPT"]

    def __call__(self, *args):
        a = list(args)
        ok = subprocess.CompletedProcess(args, 0, "", "")
        ko = subprocess.CompletedProcess(args, 1, "", "")
        if a[:2] == ["-n", "-L"]:
            return ok if a[2] in self.chains else ko
        if a[0] == "-N":
            self.chains[a[1]] = []
            return ok
        if a[0] in ("-C", "-D") and a[1] == "FORWARD":
            spec = " ".join(a[2:])
            if a[0] == "-C":
                return ok if spec in self.forward else ko
            self.forward.remove(spec)
            return ok
        if a[0] == "-I" and a[1] == "FORWARD":
            self.forward.insert(0, " ".join(a[3:]))
            return ok
        if a[0] == "-S":
            out = "\n".join([f"-N {a[1]}"] +
                            [f"-A {a[1]} " + " ".join(r) for r in self.chains[a[1]]])
            return subprocess.CompletedProcess(args, 0, out, "")
        if a[0] == "-F":
            self.chains[a[1]] = []
            return ok
        if a[0] == "-A":
            self.chains[a[1]].append(a[2:])
            return ok
        return ok


def test_sync_remplace_accept_global_par_chaine_filtree(wgs):
    ipt = FakeIpt()
    rules = wgs.forward_rules({"a": "10.66.0.2"}, [{"slug": "a", "tunnel_ip": "10.66.0.3"}])
    assert wgs.sync_forward(rules, run=ipt) is True
    assert ipt.forward == ["-i wg-hotspotpro -o wg-hotspotpro -j HOTSPOTPRO-WG"]
    assert ipt.chains["HOTSPOTPRO-WG"][-1] == ["-j", "DROP"]
    # Second passage identique : aucune réécriture (donc aucune coupure)
    assert wgs.sync_forward(rules, run=ipt) is False


# ── Destination de sauvegarde ────────────────────────────────────

@pytest.mark.parametrize("host,ok", [
    ("nas.local", True), ("192.168.1.10", True),
    ("nas',user='x", False), ("h,pass=x", False), ("h:1", False),
])
def test_destination_sauvegarde_caracteres_interdits(client, monkeypatch, host, ok):
    import config
    import routes_admin
    vals = {"backup_dest_type": "sftp", "backup_host": host, "backup_user": "u",
            "backup_pass": "p", "backup_remote_path": "bk", "backup_sftp_port": "22"}
    monkeypatch.setattr(config, "_setting", lambda k: vals.get(k, ""))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "obscur", ""))
    dest, err = routes_admin._rclone_backup_dest()
    assert (dest is not None) == ok
    if not ok:
        assert "interdit" in err
