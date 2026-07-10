"""
test_tickets.py — Vendeurs, génération de tickets et attribution des ventes
(module partagé saas/core/tickets.py).
"""
import sqlite3

import pytest

import tickets


@pytest.fixture()
def dbp(tmp_path):
    """Chemin d'une base de vente de tenant jetable, schéma prêt."""
    path = str(tmp_path / "sales.db")
    tickets.ensure_schema(path)
    # La table sales est créée par le hub ; on la recrée ici pour les stats.
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT, seller TEXT, username TEXT,
        profile TEXT, amount INTEGER DEFAULT 0, comment TEXT DEFAULT '',
        ip TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime')))""")
    conn.commit(); conn.close()
    return path


# ── Vendeurs ────────────────────────────────────────────────────

def test_creation_vendeur(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    assert isinstance(sid, int)
    names = [s["name"] for s in tickets.list_sellers(dbp)]
    assert "daniel" in names


def test_vendeur_nom_vide_refuse(dbp):
    with pytest.raises(ValueError):
        tickets.create_seller(dbp, "   ")


def test_vendeur_doublon_refuse(dbp):
    tickets.create_seller(dbp, "ali")
    with pytest.raises(ValueError):
        tickets.create_seller(dbp, "ali")


def test_nom_reserve_refuse(dbp):
    with pytest.raises(ValueError):
        tickets.create_seller(dbp, "Non attribué")


# ── Génération ──────────────────────────────────────────────────

def test_generation_lot_codes_uniques(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    res = tickets.generate_batch(dbp, sid, "1h", qty=50, code_len=5,
                                 charset="alnum", pw_mode="same")
    codes = [t["username"] for t in res["tickets"]]
    assert len(codes) == 50
    assert len(set(codes)) == 50                 # tous uniques
    # username = password en mode 'same'
    assert all(t["username"] == t["password"] for t in res["tickets"])
    # bonne longueur et jeu de caractères
    assert all(len(c) == 5 for c in codes)
    assert all(ch in tickets.CHARSETS["alnum"] for c in codes for ch in c)


def test_generation_unicite_sur_deux_lots(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    a = tickets.generate_batch(dbp, sid, "1h", qty=100, code_len=4, charset="alnum")
    b = tickets.generate_batch(dbp, sid, "1h", qty=100, code_len=4, charset="alnum")
    codes = {t["username"] for t in a["tickets"]} | {t["username"] for t in b["tickets"]}
    assert len(codes) == 200                     # aucun chevauchement


def test_mode_mot_de_passe_separe(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    res = tickets.generate_batch(dbp, sid, "1h", qty=10, pw_mode="separate")
    assert all(t["username"] != t["password"] for t in res["tickets"])


def test_generation_trop_dense_refusee(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    # 4 caractères en majuscules seules (24 lettres) : 24^4 ~ 331k ; demander
    # trop par rapport à l'espace n'est pas le cas ici, mais un code_len hors
    # bornes doit être refusé.
    with pytest.raises(ValueError):
        tickets.generate_batch(dbp, sid, "1h", qty=10, code_len=3)


def test_inventaire_par_vendeur(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    tickets.generate_batch(dbp, sid, "1h", qty=20, code_len=5)
    s = [x for x in tickets.list_sellers(dbp) if x["name"] == "daniel"][0]
    assert s["generated"] == 20
    assert s["stock"] == 20
    assert s["sold"] == 0


# ── Attribution au login ────────────────────────────────────────

def test_attribution_ticket_connu(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    res = tickets.generate_batch(dbp, sid, "1h", qty=5, code_len=5)
    code = res["tickets"][0]["username"]

    out = tickets.resolve_sale(dbp, code, "1h")
    assert out["seller"] == "daniel"
    assert out["record"] is True
    assert out["profile"] == "1h"


def test_reconnexion_ne_recompte_pas(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    res = tickets.generate_batch(dbp, sid, "1h", qty=5, code_len=5)
    code = res["tickets"][0]["username"]

    first  = tickets.resolve_sale(dbp, code, "1h")
    second = tickets.resolve_sale(dbp, code, "1h")
    assert first["record"] is True
    assert second["record"] is False          # déjà vendu
    assert second["seller"] == "daniel"


def test_code_inconnu_va_en_non_attribue(dbp):
    out = tickets.resolve_sale(dbp, "zzzz9", "24h")
    assert out["seller"] == "Non attribué"
    assert out["record"] is True
    # Reconnexion du même code inconnu : pas de recomptage
    again = tickets.resolve_sale(dbp, "zzzz9", "24h")
    assert again["record"] is False


def test_vente_met_a_jour_inventaire(dbp):
    sid = tickets.create_seller(dbp, "daniel")
    res = tickets.generate_batch(dbp, sid, "1h", qty=4, code_len=5)
    tickets.resolve_sale(dbp, res["tickets"][0]["username"], "1h")
    tickets.resolve_sale(dbp, res["tickets"][1]["username"], "1h")
    s = [x for x in tickets.list_sellers(dbp) if x["name"] == "daniel"][0]
    assert s["sold"] == 2
    assert s["stock"] == 2


def test_suppression_vendeur_retire_le_stock(dbp):
    sid = tickets.create_seller(dbp, "temp")
    tickets.generate_batch(dbp, sid, "1h", qty=10, code_len=5)
    tickets.delete_seller(dbp, sid)
    assert all(s["name"] != "temp" for s in tickets.list_sellers(dbp))
