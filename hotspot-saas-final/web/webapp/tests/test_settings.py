"""
test_settings.py — Magasin de configuration modifiable depuis l'admin :
chiffrement des secrets, précédence base > environnement, formulaire,
accès réservé à l'admin.
"""
import config
import settings
from db import get_db
from helpers import create_client_row


# ── Chiffrement des secrets ─────────────────────────────────────

def test_secret_chiffre_en_base_et_dechiffre_a_la_lecture(db):
    conn = get_db()
    settings.set_value(conn, "fedapay_secret_key", "sk_live_supersecret")
    conn.commit()
    raw = conn.execute("SELECT value FROM settings WHERE key='fedapay_secret_key'").fetchone()["value"]
    conn.close()
    # En base : chiffré, jamais en clair
    assert raw.startswith("enc:")
    assert "supersecret" not in raw
    # À la lecture : déchiffré
    assert settings.get("fedapay_secret_key") == "sk_live_supersecret"


def test_valeur_non_secrete_stockee_en_clair(db):
    conn = get_db()
    settings.set_value(conn, "from_email", "hello@exemple.tg")
    conn.commit()
    raw = conn.execute("SELECT value FROM settings WHERE key='from_email'").fetchone()["value"]
    conn.close()
    assert raw == "hello@exemple.tg"
    assert settings.get("from_email") == "hello@exemple.tg"


# ── Précédence base > environnement ─────────────────────────────

def test_repli_sur_env_si_absent_en_base(db):
    saved = config.FROM_NAME
    try:
        config.FROM_NAME = "DefautEnv"
        assert settings.get("from_name") == "DefautEnv"
    finally:
        config.FROM_NAME = saved


def test_base_surcharge_env(db):
    saved = config.FROM_NAME
    try:
        config.FROM_NAME = "DefautEnv"
        conn = get_db()
        settings.set_value(conn, "from_name", "NomAdmin")
        conn.commit(); conn.close()
        assert settings.get("from_name") == "NomAdmin"
    finally:
        config.FROM_NAME = saved


def test_accesseurs_config_refletent_la_base(db):
    conn = get_db()
    settings.set_value(conn, "fedapay_env", "sandbox")
    settings.set_value(conn, "fedapay_secret_key", "sk_sandbox_x")
    conn.commit(); conn.close()
    assert config.fedapay_env() == "sandbox"
    assert config.fedapay_secret_key() == "sk_sandbox_x"


def test_mikrotik_https_bascule(db):
    conn = get_db()
    settings.set_value(conn, "mikrotik_https", "1")
    conn.commit(); conn.close()
    assert config.mikrotik_https() is True
    import mikrotik_scripts as mks
    assert mks.login_url("api.exemple.com", "slug").startswith("https://")


# ── Formulaire admin ────────────────────────────────────────────

def test_secret_vide_ne_ecrase_pas(db):
    conn = get_db()
    settings.set_value(conn, "brevo_api_key", "xkeysib-original")
    conn.commit(); conn.close()
    # L'admin renvoie le formulaire sans retaper le secret
    settings.save_from_form({"brevo_api_key": "", "from_email": "a@b.tg"})
    assert settings.get("brevo_api_key") == "xkeysib-original"
    assert settings.get("from_email") == "a@b.tg"


def test_case_a_cocher_absente_vaut_zero(db):
    conn = get_db()
    settings.set_value(conn, "mikrotik_https", "1")
    conn.commit(); conn.close()
    settings.save_from_form({})   # case décochée = absente
    assert settings.get_bool("mikrotik_https") is False


def test_case_a_cocher_presente_vaut_un(db):
    settings.save_from_form({"watchdog_auto_restart": "on"})
    assert settings.get_bool("watchdog_auto_restart") is True


# ── Sauvegarde distante (SMB / SFTP) ────────────────────────────

def test_mot_de_passe_sauvegarde_chiffre(db):
    conn = get_db()
    settings.set_value(conn, "backup_pass", "TrueNAS-Secret!")
    conn.commit()
    raw = conn.execute("SELECT value FROM settings WHERE key='backup_pass'").fetchone()["value"]
    conn.close()
    assert raw.startswith("enc:")
    assert "TrueNAS-Secret" not in raw
    assert settings.get("backup_pass") == "TrueNAS-Secret!"


def test_config_destination_smb_persistee(db):
    settings.save_from_form({
        "backup_dest_type": "smb", "backup_host": "192.168.1.20",
        "backup_smb_share": "backups", "backup_user": "nasuser",
        "backup_pass": "p@ss", "backup_remote_path": "hotspotpro",
    })
    assert settings.get("backup_dest_type") == "smb"
    assert settings.get("backup_host") == "192.168.1.20"
    assert settings.get("backup_smb_share") == "backups"
    assert settings.get("backup_pass") == "p@ss"


# ── Contrôle d'accès ────────────────────────────────────────────

def _login(client, is_admin: bool):
    cid = create_client_row(email=("a@a.tg" if is_admin else "u@u.tg"))
    if is_admin:
        conn = get_db()
        conn.execute("UPDATE clients SET is_admin=1 WHERE id=?", (cid,))
        conn.commit(); conn.close()
    with client.session_transaction() as s:
        s["client_id"] = cid
        s["is_admin"] = is_admin
    return cid


def test_settings_refuse_non_admin(client):
    _login(client, is_admin=False)
    r = client.get("/admin/settings")
    assert r.status_code in (301, 302)
    assert "/dashboard" in r.headers.get("Location", "")


def test_settings_refuse_anonyme(client):
    r = client.get("/admin/settings")
    assert r.status_code in (301, 302)


def test_settings_page_admin_ok(client):
    _login(client, is_admin=True)
    r = client.get("/admin/settings")
    assert r.status_code == 200
    assert "FedaPay" in r.get_data(as_text=True)


def test_settings_enregistre_via_post(client):
    _login(client, is_admin=True)
    with client.session_transaction() as s:
        csrf = s.setdefault("_csrf", "testtoken")
    r = client.post("/admin/settings", data={
        "action": "save", "csrf_token": csrf,
        "from_email": "boss@exemple.tg", "fedapay_env": "sandbox",
        "fedapay_secret_key": "sk_sandbox_new",
    })
    assert r.status_code in (301, 302)
    assert settings.get("from_email") == "boss@exemple.tg"
    assert settings.get("fedapay_env") == "sandbox"
    assert settings.get("fedapay_secret_key") == "sk_sandbox_new"
