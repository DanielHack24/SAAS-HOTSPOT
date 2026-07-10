"""
secretbox.py — Chiffrement des secrets côté moteur SaaS (central.db).

Clé dérivée de SECRET_KEY (variable d'environnement, partagée avec l'app
web et les services root via le fichier .env). Une fuite de la seule base
— sans le .env — ne révèle donc aucun secret.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

_PREFIX = "enc:"
_SALT = b"hotspotpro-core-v1"


def _fernet() -> Fernet:
    key = os.environ.get("SECRET_KEY", "").encode()
    if not key:
        raise RuntimeError("SECRET_KEY absente : chiffrement impossible.")
    dk = hashlib.pbkdf2_hmac("sha256", key, _SALT, 200_000, dklen=32)
    return Fernet(base64.urlsafe_b64encode(dk))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return plaintext
    return _PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt(stored: str) -> str:
    if not stored or not stored.startswith(_PREFIX):
        return stored
    try:
        return _fernet().decrypt(stored[len(_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""
