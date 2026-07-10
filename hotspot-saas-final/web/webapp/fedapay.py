"""
fedapay.py — Client FedaPay (création de transactions, vérification webhook)
"""
import hmac, hashlib, json, time

import requests

import config


class FedaPayError(Exception):
    pass


def _base_url() -> str:
    return ("https://api.fedapay.com" if config.fedapay_env() == "live"
            else "https://sandbox-api.fedapay.com")


def _headers() -> dict:
    secret = config.fedapay_secret_key()
    if not secret:
        raise FedaPayError("Clé secrète FedaPay non configurée.")
    return {
        "Authorization": f"Bearer {secret}",
        "Content-Type":  "application/json",
    }


def create_transaction(description: str, amount: int, callback_url: str,
                       customer_name: str, customer_email: str,
                       metadata: dict) -> tuple[str, str]:
    """Crée une transaction et son token de paiement.
    Retourne (transaction_id, payment_url)."""
    parts     = customer_name.split()
    firstname = parts[0] if parts else customer_name
    lastname  = " ".join(parts[1:]) if len(parts) > 1 else firstname

    payload = {
        "description":  description,
        "amount":       amount,
        "currency":     {"iso": "XOF"},
        "callback_url": callback_url,
        "customer": {
            "email":     customer_email,
            "firstname": firstname,
            "lastname":  lastname,
        },
        "metadata": metadata,
    }
    r = requests.post(f"{_base_url()}/v1/transactions",
                      headers=_headers(), json=payload, timeout=15)
    r.raise_for_status()
    trans_data = r.json()
    # FedaPay peut retourner "v1/transaction" ou "transaction"
    trans_obj = (trans_data.get("v1/transaction")
                 or trans_data.get("transaction")
                 or next(iter(trans_data.values())))
    trans_id = str(trans_obj["id"])

    r2 = requests.post(f"{_base_url()}/v1/transactions/{trans_id}/token",
                       headers=_headers(), timeout=15)
    r2.raise_for_status()
    token_data  = r2.json()
    payment_url = token_data.get("url") or token_data.get("payment_url")
    if not payment_url:
        raise FedaPayError(f"URL de paiement absente de la réponse : {list(token_data.keys())}")
    return trans_id, payment_url


def get_transaction(trans_id: str) -> dict:
    """Relit une transaction côté FedaPay (source de vérité)."""
    r = requests.get(f"{_base_url()}/v1/transactions/{trans_id}",
                     headers=_headers(), timeout=15)
    r.raise_for_status()
    data = r.json()
    return (data.get("v1/transaction") or data.get("transaction")
            or next(iter(data.values())))


def transaction_status(trans_id: str) -> dict:
    """État réel d'une transaction FedaPay (source de vérité), pour valider
    une confirmation manuelle avant d'activer quoi que ce soit.

    Retourne {'status': str, 'amount': int|None, 'currency': str}. `status`
    vaut 'approved' quand le paiement a réellement abouti ; toute autre valeur
    ('pending', 'canceled', 'declined', ...) signifie que rien n'a été payé.
    Lève une exception si FedaPay est injoignable (l'appelant doit alors
    refuser, jamais activer par défaut)."""
    t   = get_transaction(trans_id)
    cur = t.get("currency") or {}
    amount = t.get("amount")
    return {
        "status":   (t.get("status") or "").lower(),
        "amount":   int(amount) if amount is not None else None,
        "currency": (cur.get("iso") or "").upper() if isinstance(cur, dict)
                    else str(cur).upper(),
    }


# ═══════════════════════════════════════════════
# WEBHOOK
# ═══════════════════════════════════════════════

SIGNATURE_TOLERANCE_S = 300   # 5 minutes


def verify_webhook_signature(payload: bytes, header: str) -> bool:
    """Vérifie la signature X-FEDAPAY-SIGNATURE. STRICT : toute signature
    invalide doit entraîner un rejet 403 de la requête.

    Format officiel (comme Stripe) : "t=<timestamp>,s=<hmac>" où le HMAC
    SHA-256 est calculé sur "<timestamp>.<payload>". Le timestamp est
    obligatoire : un HMAC du payload seul serait rejouable indéfiniment.
    """
    key = config.fedapay_webhook_key()
    if not key or not header:
        return False

    parts = dict(
        item.split("=", 1) for item in header.split(",") if "=" in item
    )
    if "t" not in parts or "s" not in parts:
        return False
    try:
        ts = int(parts["t"])
    except ValueError:
        return False
    if abs(time.time() - ts) > SIGNATURE_TOLERANCE_S:
        return False
    signed = f"{parts['t']}.".encode() + payload
    expected = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["s"])


def parse_webhook(payload: bytes) -> dict:
    """Décode l'événement webhook : {name, transaction, trans_id, metadata}."""
    data        = json.loads(payload)
    transaction = data.get("data", {}).get("object", {}) or data.get("entity", {})
    currency    = transaction.get("currency") or {}
    return {
        "name":        data.get("name", ""),
        "transaction": transaction,
        "trans_id":    str(transaction.get("id", "")),
        "metadata":    transaction.get("metadata") or {},
        "amount":      transaction.get("amount"),
        "currency":    (currency.get("iso") or "").upper() if isinstance(currency, dict) else str(currency).upper(),
    }
