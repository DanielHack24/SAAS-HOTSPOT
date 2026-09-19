"""
security.py — Mots de passe, CSRF, rate limiting, décorateurs d'accès
"""
import time, hashlib, secrets, threading, sqlite3
from functools import wraps

from flask import session, redirect, url_for, request, abort
from markupsafe import Markup
from werkzeug.security import generate_password_hash, check_password_hash


# ═══════════════════════════════════════════════
# MOTS DE PASSE
# ═══════════════════════════════════════════════
# Historique : les anciens comptes étaient stockés en SHA-256 non salé
# (64 caractères hexadécimaux). On les vérifie encore, mais chaque
# connexion réussie migre le hash vers le format Werkzeug (PBKDF2 salé).

def hash_password(pwd: str) -> str:
    return generate_password_hash(pwd)


def _is_legacy_hash(stored: str) -> bool:
    return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)


def verify_password(stored: str, pwd: str) -> tuple[bool, bool]:
    """Retourne (mot_de_passe_valide, hash_a_migrer)."""
    if not stored:
        return False, False
    if _is_legacy_hash(stored):
        ok = secrets.compare_digest(stored, hashlib.sha256(pwd.encode()).hexdigest())
        return ok, ok   # valide → à re-hasher en PBKDF2
    try:
        return check_password_hash(stored, pwd), False
    except Exception:
        return False, False


# ═══════════════════════════════════════════════
# DÉCORATEURS D'ACCÈS
# ═══════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "client_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


# ═══════════════════════════════════════════════
# CSRF
# ═══════════════════════════════════════════════
# Jeton stocké en session, exigé sur toutes les requêtes POST sauf
# les endpoints exemptés (webhooks signés, appels machine-à-machine).

CSRF_EXEMPT_ENDPOINTS = {"webhook_fedapay", "telegram_webhook"}


def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


def csrf_field() -> Markup:
    return Markup(f'<input type="hidden" name="csrf_token" value="{csrf_token()}">')


def validate_csrf():
    """Hook before_request : rejette les POST sans jeton CSRF valide."""
    if request.method != "POST":
        return
    if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    good = session.get("_csrf", "")
    if not good or not sent or not secrets.compare_digest(sent, good):
        abort(400, description="Jeton CSRF manquant ou invalide.")


# ═══════════════════════════════════════════════
# RATE LIMITING (partagé entre workers, en base)
# ═══════════════════════════════════════════════
# Les tentatives sont stockées dans la base web (table rate_attempts) : les
# workers gunicorn partagent donc le même compteur (en mémoire, chaque worker
# avait le sien et la limite effective était multipliée). Repli en mémoire
# si la base est indisponible, pour ne jamais bloquer une connexion.

RL_RETENTION_S = 86400   # purge des tentatives de plus de 24 h

_attempts: dict[str, list[float]] = {}
_attempts_lock = threading.Lock()


def _rl_conn() -> sqlite3.Connection:
    from db import get_db   # import tardif : db importe déjà security
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS rate_attempts (
                        key TEXT NOT NULL, ts REAL NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_attempts ON rate_attempts(key, ts)")
    return conn


def rate_limited(key: str, max_attempts: int = 8, window_s: int = 600) -> bool:
    """True si `key` a dépassé `max_attempts` sur les `window_s` dernières
    secondes. Appeler après chaque tentative échouée via record_attempt."""
    now = time.time()
    try:
        conn = _rl_conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM rate_attempts WHERE key=? AND ts>?",
                             (key, now - window_s)).fetchone()[0]
        finally:
            conn.close()
        return n >= max_attempts
    except sqlite3.Error:
        with _attempts_lock:
            stamps = [t for t in _attempts.get(key, []) if now - t < window_s]
            _attempts[key] = stamps
            return len(stamps) >= max_attempts


def record_attempt(key: str):
    now = time.time()
    try:
        conn = _rl_conn()
        try:
            conn.execute("INSERT INTO rate_attempts (key, ts) VALUES (?, ?)", (key, now))
            conn.execute("DELETE FROM rate_attempts WHERE ts<?", (now - RL_RETENTION_S,))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        with _attempts_lock:
            _attempts.setdefault(key, []).append(now)


def clear_attempts(key: str):
    try:
        conn = _rl_conn()
        try:
            conn.execute("DELETE FROM rate_attempts WHERE key=?", (key,))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    with _attempts_lock:
        _attempts.pop(key, None)
