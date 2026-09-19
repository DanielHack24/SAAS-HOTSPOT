"""
routes_auth.py — Connexion, inscription, déconnexion, mot de passe oublié
"""
import re, sqlite3, secrets, hashlib
from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, session, flash

import config
from legal_content import TERMS_VERSION
from webapp_core import app
from db import get_db
from security import (hash_password, verify_password,
                      rate_limited, record_attempt, clear_attempts)
from emails import email_welcome, email_password_reset, email_verification_code


@app.route("/login", methods=["GET", "POST"])
def login():
    if "client_id" in session:
        return redirect(url_for("admin_dashboard" if session.get("is_admin") else "dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pwd   = request.form.get("password", "")
        ip = request.remote_addr
        rl_key = f"login:{ip}:{email}"
        # Trois compteurs : couple IP+compte (usage normal), compte seul
        # (attaque d'un compte depuis beaucoup d'IP) et IP seule (essai
        # de nombreux comptes depuis une même IP).
        rl_limits = [(rl_key, 8, 600),
                     (f"login-email:{email}", 20, 900),
                     (f"login-ip:{ip}", 30, 600)]

        if any(rate_limited(k, m, w) for k, m, w in rl_limits):
            flash("Trop de tentatives. Réessayez dans quelques minutes.", "error")
            return render_template("login.html")

        conn = get_db()
        row  = conn.execute("SELECT * FROM clients WHERE email=?", (email,)).fetchone()

        ok, needs_rehash = verify_password(row["password_hash"], pwd) if row else (False, False)
        if ok:
            if needs_rehash:
                # Migration transparente des anciens hash SHA-256 vers PBKDF2
                conn.execute("UPDATE clients SET password_hash=? WHERE id=?",
                             (hash_password(pwd), row["id"]))
                conn.commit()
            conn.close()
            clear_attempts(rl_key)
            clear_attempts(f"login-email:{email}")
            session["client_id"] = row["id"]
            session["is_admin"]  = bool(row["is_admin"])
            session["full_name"] = row["full_name"]
            try:
                session["avatar_color"] = row["avatar_color"]
            except (IndexError, KeyError):
                session["avatar_color"] = None
            return redirect(url_for("admin_dashboard" if row["is_admin"] else "dashboard"))

        conn.close()
        for k, _, _ in rl_limits:
            record_attempt(k)
        flash("Email ou mot de passe incorrect.", "error")

    return render_template("login.html")


# ── Vérification d'e-mail à l'inscription ───────────────────────
# Quand Brevo est configuré, le compte n'est créé qu'après saisie d'un code
# à 6 chiffres envoyé par e-mail (anti-faux comptes / adresses invalides).
VERIF_TTL_MIN     = 15
MAX_CODE_ATTEMPTS = 5


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _gen_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _create_account(conn, email, name, phone, pwd_hash,
                    terms_version=None, terms_accepted_at=None) -> int:
    conn.execute(
        "INSERT INTO clients (email, password_hash, full_name, phone, "
        "terms_version, terms_accepted_at) VALUES (?,?,?,?,?,?)",
        (email, pwd_hash, name, phone or None, terms_version, terms_accepted_at))
    conn.commit()
    return conn.execute("SELECT id FROM clients WHERE email=?", (email,)).fetchone()["id"]


def _login_client(cid: int, name: str):
    session["client_id"]   = cid
    session["is_admin"]    = False
    session["full_name"]   = name
    session["avatar_color"] = None


@app.route("/register", methods=["GET", "POST"])
def register():
    if "client_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name  = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        pwd   = request.form.get("password", "")
        pwd2  = request.form.get("password2", "")
        plan_chosen = request.form.get("plan", "").strip()
        plan = plan_chosen if plan_chosen in config.PLANS else ""

        if not all([name, email, pwd]):
            flash("Tous les champs sont requis.", "error")
        elif pwd != pwd2:
            flash("Les mots de passe ne correspondent pas.", "error")
        elif len(pwd) < 8:
            flash("Le mot de passe doit faire au moins 8 caractères.", "error")
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash("Email invalide.", "error")
        elif request.form.get("accept_terms") != "1":
            # Consentement exprès (loi n° 2019-014, art. 14 et 35).
            flash("Vous devez accepter les conditions d'utilisation et la politique de confidentialité pour créer un compte.", "error")
        else:
            terms_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = get_db()
            if conn.execute("SELECT 1 FROM clients WHERE email=?", (email,)).fetchone():
                conn.close()
                flash("Cet email est déjà utilisé.", "error")
                return render_template("register.html", plans=config.PLANS)

            # Vérification par e-mail si Brevo est configuré
            if config.brevo_api_key():
                code = _gen_code()
                expires = (datetime.now() + timedelta(minutes=VERIF_TTL_MIN)
                           ).strftime("%Y-%m-%d %H:%M:%S")
                conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
                conn.execute("""INSERT INTO pending_registrations
                                  (email, full_name, phone, password_hash, plan,
                                   code_hash, expires_at, terms_version,
                                   terms_accepted_at)
                                VALUES (?,?,?,?,?,?,?,?,?)""",
                             (email, name, phone or None, hash_password(pwd), plan,
                              _hash_code(code), expires, TERMS_VERSION, terms_at))
                conn.commit()
                conn.close()
                session["pending_email"] = email
                email_verification_code(email, name, code)
                flash("Un code de vérification vient de vous être envoyé par e-mail.", "info")
                return redirect(url_for("register_verify"))

            # Pas d'e-mail configuré (dev/local) : création directe
            try:
                cid = _create_account(conn, email, name, phone, hash_password(pwd),
                                      TERMS_VERSION, terms_at)
                conn.close()
            except sqlite3.IntegrityError:
                conn.close()
                flash("Cet email est déjà utilisé.", "error")
                return render_template("register.html", plans=config.PLANS)
            _login_client(cid, name)
            email_welcome(email, name)
            flash("Compte créé ! Bienvenue sur HotspotPro.", "success")
            if plan:
                return redirect(url_for("subscribe", plan=plan))
            return redirect(url_for("dashboard"))

    return render_template("register.html", plans=config.PLANS)


@app.route("/register/verify", methods=["GET", "POST"])
def register_verify():
    if "client_id" in session:
        return redirect(url_for("dashboard"))
    email = session.get("pending_email", "")
    if not email:
        return redirect(url_for("register"))

    if request.method == "POST":
        rl_key = f"verify:{request.remote_addr}:{email}"
        if rate_limited(rl_key, max_attempts=10, window_s=3600):
            flash("Trop de tentatives. Réessayez plus tard.", "error")
            return render_template("register_verify.html", email=email)

        code = request.form.get("code", "").strip()
        conn = get_db()
        row  = conn.execute("SELECT * FROM pending_registrations WHERE email=?",
                            (email,)).fetchone()
        if not row:
            conn.close()
            session.pop("pending_email", None)
            flash("Session expirée. Recommencez votre inscription.", "error")
            return redirect(url_for("register"))

        expired = (datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
                   < datetime.now())
        if expired or row["attempts"] >= MAX_CODE_ATTEMPTS:
            conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
            conn.commit(); conn.close()
            session.pop("pending_email", None)
            flash("Code expiré ou trop de tentatives. Recommencez votre inscription.", "error")
            return redirect(url_for("register"))

        if not secrets.compare_digest(row["code_hash"], _hash_code(code)):
            conn.execute("UPDATE pending_registrations SET attempts=attempts+1 WHERE email=?",
                         (email,))
            conn.commit(); conn.close()
            record_attempt(rl_key)
            flash("Code incorrect. Vérifiez votre e-mail et réessayez.", "error")
            return render_template("register_verify.html", email=email)

        name, phone, pwd_hash, plan = (row["full_name"], row["phone"],
                                       row["password_hash"], row["plan"])
        try:
            cid = _create_account(conn, email, name, phone, pwd_hash,
                                  row["terms_version"], row["terms_accepted_at"])
        except sqlite3.IntegrityError:
            conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
            conn.commit(); conn.close()
            session.pop("pending_email", None)
            flash("Cet email est déjà utilisé.", "error")
            return redirect(url_for("login"))
        conn.execute("DELETE FROM pending_registrations WHERE email=?", (email,))
        conn.commit(); conn.close()
        session.pop("pending_email", None)
        clear_attempts(rl_key)
        _login_client(cid, name)
        email_welcome(email, name)
        flash("E-mail vérifié, compte créé ! Bienvenue sur HotspotPro.", "success")
        if plan in config.PLANS:
            return redirect(url_for("subscribe", plan=plan))
        return redirect(url_for("dashboard"))

    return render_template("register_verify.html", email=email)


@app.route("/register/resend", methods=["POST"])
def register_resend():
    email = session.get("pending_email", "")
    if not email:
        return redirect(url_for("register"))
    rl_key = f"resend:{request.remote_addr}:{email}"
    if rate_limited(rl_key, max_attempts=3, window_s=900):
        flash("Trop de renvois. Patientez quelques minutes.", "error")
        return redirect(url_for("register_verify"))
    record_attempt(rl_key)

    conn = get_db()
    row  = conn.execute("SELECT * FROM pending_registrations WHERE email=?", (email,)).fetchone()
    if not row:
        conn.close()
        session.pop("pending_email", None)
        return redirect(url_for("register"))
    code = _gen_code()
    expires = (datetime.now() + timedelta(minutes=VERIF_TTL_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""UPDATE pending_registrations
                    SET code_hash=?, expires_at=?, attempts=0 WHERE email=?""",
                 (_hash_code(code), expires, email))
    conn.commit()
    name = row["full_name"]
    conn.close()
    email_verification_code(email, name, code)
    flash("Nouveau code envoyé.", "info")
    return redirect(url_for("register_verify"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ═══════════════════════════════════════════════
# MOT DE PASSE OUBLIÉ
# ═══════════════════════════════════════════════
# Seul le SHA-256 du jeton est stocké en base : un dump de la base ne
# permet pas de réinitialiser des comptes. Jeton valable 1 h, usage unique.

RESET_TOKEN_TTL_H = 1


def _hash_reset_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if "client_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email  = request.form.get("email", "").strip().lower()
        rl_key = f"forgot:{request.remote_addr}"

        if rate_limited(rl_key, max_attempts=5, window_s=3600):
            flash("Trop de demandes. Réessayez dans une heure.", "error")
            return render_template("forgot_password.html")
        record_attempt(rl_key)

        if email:
            conn = get_db()
            row  = conn.execute("SELECT * FROM clients WHERE email=?", (email,)).fetchone()
            if row:
                token   = secrets.token_urlsafe(32)
                expires = datetime.now() + timedelta(hours=RESET_TOKEN_TTL_H)
                conn.execute("""
                    UPDATE clients SET reset_token_hash=?, reset_token_expires=? WHERE id=?
                """, (_hash_reset_token(token),
                      expires.strftime("%Y-%m-%d %H:%M:%S"), row["id"]))
                conn.commit()
                reset_url = f"{config.APP_URL}{url_for('reset_password', token=token)}"
                email_password_reset(row["email"], row["full_name"], reset_url)
            conn.close()

        # Message identique que le compte existe ou non (anti-énumération)
        flash("Si un compte existe avec cet email, un lien de réinitialisation "
              "vient de lui être envoyé (valable 1 heure).", "success")
        return redirect(url_for("login"))

    return render_template("forgot_password.html")


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    conn = get_db()
    row  = conn.execute(
        "SELECT * FROM clients WHERE reset_token_hash=?",
        (_hash_reset_token(token),)
    ).fetchone()

    valid = bool(row and row["reset_token_expires"]
                 and datetime.strptime(row["reset_token_expires"], "%Y-%m-%d %H:%M:%S") > datetime.now())
    if not valid:
        conn.close()
        flash("Lien de réinitialisation invalide ou expiré. Refaites une demande.", "error")
        return redirect(url_for("forgot_password"))

    if request.method == "POST":
        pwd  = request.form.get("password", "")
        pwd2 = request.form.get("password2", "")
        if len(pwd) < 8:
            flash("Le mot de passe doit faire au moins 8 caractères.", "error")
        elif pwd != pwd2:
            flash("Les mots de passe ne correspondent pas.", "error")
        else:
            conn.execute("""
                UPDATE clients
                SET password_hash=?, reset_token_hash=NULL, reset_token_expires=NULL
                WHERE id=?
            """, (hash_password(pwd), row["id"]))
            conn.commit()
            conn.close()
            flash("Mot de passe réinitialisé ! Vous pouvez vous connecter.", "success")
            return redirect(url_for("login"))

    conn.close()
    return render_template("reset_password.html", token=token)
