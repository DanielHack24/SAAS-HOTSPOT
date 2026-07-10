"""
helpers.py — Fabriques de données et signature webhook pour les tests
"""
import hashlib
import hmac
import json
import time

import config
from db import get_db


def sign(payload: bytes, ts: int | None = None) -> str:
    """Signature X-FEDAPAY-SIGNATURE valide (format t=...,s=...)."""
    if ts is None:
        ts = int(time.time())
    mac = hmac.new(config.FEDAPAY_WEBHOOK_KEY.encode(),
                   f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},s={mac}"


def webhook_payload(trans_id="12345", amount=5000, currency="XOF",
                    name="transaction.approved", metadata=None) -> bytes:
    return json.dumps({
        "name": name,
        "entity": {
            "id": trans_id,
            "amount": amount,
            "currency": {"iso": currency},
            "metadata": metadata or {"type": "subscription"},
        },
    }).encode()


def post_webhook(client, payload: bytes, signature: str | None = None):
    headers = {}
    if signature is not None:
        headers["X-FEDAPAY-SIGNATURE"] = signature
    return client.post("/webhook/fedapay", data=payload, headers=headers,
                       content_type="application/json")


def create_client_row(email="client@test.tg", name="Client Test") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO clients (email, password_hash, full_name) VALUES (?,?,?)",
        (email, "x", name))
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def create_pending_payment(client_id: int, plan="3m", amount=5000,
                           reference="12345") -> int:
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO payments (client_id, plan, amount, method, reference, status)
        VALUES (?,?,?,'fedapay',?,'pending')
    """, (client_id, plan, amount, reference))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def fetch_one(sql: str, args=()):
    conn = get_db()
    row = conn.execute(sql, args).fetchone()
    conn.close()
    return dict(row) if row else None


def fetch_all(sql: str, args=()):
    conn = get_db()
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]
