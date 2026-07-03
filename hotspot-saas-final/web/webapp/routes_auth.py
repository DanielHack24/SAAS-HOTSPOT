"""
routes_auth.py — Connexion, inscription, déconnexion
"""
import re, sqlite3

from flask import render_template, request, redirect, url_for, session, flash

import config
from webapp_core import app
from db import get_db
from security import (hash_password, verify_password,
                      rate_limited, record_attempt, clear_attempts)
from emails import email_welcome


@app.route("/login", methods=["GET", "POST"])
def login():
    if "client_id" in session:
        return redirect(url_for("admin_dashboard" if session.get("is_admin") else "dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pwd   = request.form.get("password", "")
        rl_key = f"login:{request.remote_addr}:{email}"

        if rate_limited(rl_key):
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
            session["client_id"] = row["id"]
            session["is_admin"]  = bool(row["is_admin"])
            session["full_name"] = row["full_name"]
            return redirect(url_for("admin_dashboard" if row["is_admin"] else "dashboard"))

        conn.close()
        record_attempt(rl_key)
        flash("Email ou mot de passe incorrect.", "error")

    return render_template("login.html")


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

        if not all([name, email, pwd]):
            flash("Tous les champs sont requis.", "error")
        elif pwd != pwd2:
            flash("Les mots de passe ne correspondent pas.", "error")
        elif len(pwd) < 8:
            flash("Le mot de passe doit faire au moins 8 caractères.", "error")
        elif not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            flash("Email invalide.", "error")
        else:
            try:
                conn = get_db()
                conn.execute(
                    "INSERT INTO clients (email, password_hash, full_name, phone) VALUES (?,?,?,?)",
                    (email, hash_password(pwd), name, phone)
                )
                conn.commit()
                row = conn.execute("SELECT * FROM clients WHERE email=?", (email,)).fetchone()
                conn.close()
                session["client_id"] = row["id"]
                session["is_admin"]  = False
                session["full_name"] = row["full_name"]
                email_welcome(email, name)
                flash("Compte créé ! Bienvenue sur HotspotPro.", "success")
                if plan_chosen in config.PLANS:
                    return redirect(url_for("subscribe", plan=plan_chosen))
                return redirect(url_for("dashboard"))
            except sqlite3.IntegrityError:
                flash("Cet email est déjà utilisé.", "error")

    return render_template("register.html", plans=config.PLANS)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
