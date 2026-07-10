"""
test_services.py — Arithmétique des dates et signature webhook (unitaires)
"""
import time
from datetime import datetime, timedelta

import config
import fedapay
from db import is_expired, days_remaining
from services import add_months
from helpers import sign


# ── add_months : mois calendaires réels ─────────────────────────

def test_add_months_simple():
    assert add_months(datetime(2026, 3, 15), 1) == datetime(2026, 4, 15)


def test_add_months_31_janvier_donne_fin_fevrier():
    # 2026 n'est pas bissextile
    assert add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)


def test_add_months_annee_bissextile():
    assert add_months(datetime(2028, 1, 31), 1) == datetime(2028, 2, 29)


def test_add_months_12_mois_egale_un_an():
    assert add_months(datetime(2026, 7, 6), 12) == datetime(2027, 7, 6)


def test_add_months_changement_annee():
    assert add_months(datetime(2026, 11, 30), 3) == datetime(2027, 2, 28)


# ── is_expired / days_remaining ─────────────────────────────────

def test_is_expired_date_passee():
    past = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert is_expired(past) is True


def test_is_expired_date_future():
    future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    assert is_expired(future) is False


def test_is_expired_expire_ce_soir_reste_actif():
    # Expire dans 2 heures : days_remaining dirait 0, mais pas expiré
    tonight = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    assert is_expired(tonight) is False
    assert days_remaining(tonight) == 0


def test_is_expired_format_date_seule():
    assert is_expired("2020-01-01") is True
    assert is_expired("") is False


def test_days_remaining_jamais_negatif():
    past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    assert days_remaining(past) == 0


# ── Signature webhook FedaPay ───────────────────────────────────

PAYLOAD = b'{"name":"transaction.approved"}'


def test_signature_valide():
    assert fedapay.verify_webhook_signature(PAYLOAD, sign(PAYLOAD)) is True


def test_signature_falsifiee():
    ts = int(time.time())
    assert fedapay.verify_webhook_signature(
        PAYLOAD, f"t={ts},s={'0' * 64}") is False


def test_signature_payload_modifie():
    header = sign(PAYLOAD)
    assert fedapay.verify_webhook_signature(b'{"name":"autre"}', header) is False


def test_signature_timestamp_trop_vieux_rejoue():
    old = int(time.time()) - fedapay.SIGNATURE_TOLERANCE_S - 60
    assert fedapay.verify_webhook_signature(PAYLOAD, sign(PAYLOAD, ts=old)) is False


def test_signature_header_vide_ou_malforme():
    assert fedapay.verify_webhook_signature(PAYLOAD, "") is False
    assert fedapay.verify_webhook_signature(PAYLOAD, "s=abc") is False
    assert fedapay.verify_webhook_signature(PAYLOAD, "t=abc,s=def") is False


def test_signature_refusee_sans_cle_configuree():
    saved = config.FEDAPAY_WEBHOOK_KEY
    try:
        config.FEDAPAY_WEBHOOK_KEY = ""
        assert fedapay.verify_webhook_signature(PAYLOAD, sign(PAYLOAD)) is False
    finally:
        config.FEDAPAY_WEBHOOK_KEY = saved
