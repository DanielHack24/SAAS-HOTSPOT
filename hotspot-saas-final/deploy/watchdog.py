#!/usr/bin/env python3
"""
watchdog.py — Supervision HotspotPro (app web + hub tenants + disque)

Lancé toutes les 2 minutes par un timer systemd (voir install_ops.sh).
Pour chaque service : sonde HTTP locale ; si elle échoue, tente UN
redémarrage systemd puis re-vérifie. Alerte Telegram uniquement aux
transitions (panne -> alerte unique, retour -> message de rétablissement),
jamais de spam à chaque passage.

Config (EnvironmentFile /etc/hotspotpro/ops.env) :
  ADMIN_BOT_TOKEN / ADMIN_CHAT_ID  bot Telegram d'alerte (BotFather)
  WATCHDOG_AUTO_RESTART=1          tenter systemctl restart avant d'alerter
  WATCHDOG_DISK_ALERT=90           seuil d'alerte disque (%)

Stdlib uniquement : aucun paquet à installer.
"""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request

CHECKS = [
    # (nom, url de sonde, unité systemd)
    ("Application web", "http://127.0.0.1:5000/healthz", "hotspot-web"),
    ("Hub tenants",     "http://127.0.0.1:8010/health",  "hotspot-tenant-hub"),
]

STATE_FILE   = "/var/lib/hotspotpro/watchdog_state.json"
HOSTNAME     = socket.gethostname()


def _cfg(key: str, env: str, default: str = "") -> str:
    """Valeur réglée depuis la page admin (base) si disponible, sinon
    variable d'environnement (ops.env), sinon défaut."""
    webapp = os.environ.get("HOTSPOT_WEBAPP_DIR", "/opt/hotspot-saas-web/webapp")
    if webapp not in sys.path:
        sys.path.insert(0, webapp)
    try:
        import settings
        val = settings.get(key)
        if val not in (None, ""):
            return str(val)
    except Exception:
        pass
    return os.environ.get(env, default)


BOT_TOKEN    = _cfg("admin_bot_token", "ADMIN_BOT_TOKEN").strip()
CHAT_ID      = _cfg("admin_chat_id", "ADMIN_CHAT_ID").strip()
AUTO_RESTART = _cfg("watchdog_auto_restart", "WATCHDOG_AUTO_RESTART", "1").strip().lower() not in ("0", "false", "no", "off", "")
DISK_ALERT   = int(_cfg("watchdog_disk_alert", "WATCHDOG_DISK_ALERT", "90") or 90)


def log(msg: str):
    print(msg, flush=True)   # capté par journald (SyslogIdentifier)


def telegram(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        log(f"(telegram non configure) {text}")
        return
    try:
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=data, method="POST")
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        log(f"Envoi Telegram impossible : {e}")


def probe(url: str, timeout: int = 6) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def restart_unit(unit: str) -> bool:
    try:
        subprocess.run(["systemctl", "restart", unit],
                       check=True, capture_output=True, timeout=60)
        return True
    except Exception as e:
        log(f"systemctl restart {unit} a echoue : {e}")
        return False


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def check_services(state: dict):
    for name, url, unit in CHECKS:
        key = f"down:{unit}"
        was_down = state.get(key, False)

        up = probe(url)
        if not up and AUTO_RESTART:
            log(f"{name} ne repond pas — tentative de redemarrage de {unit}")
            if restart_unit(unit):
                time.sleep(8)
                up = probe(url)

        if up and was_down:
            state[key] = False
            telegram(f"HotspotPro [{HOSTNAME}] : {name} est RETABLI.")
            log(f"{name} : retabli")
        elif not up and not was_down:
            state[key] = True
            telegram(
                f"ALERTE HotspotPro [{HOSTNAME}] : {name} est EN PANNE "
                f"(sonde {url} sans reponse, redemarrage automatique "
                f"{'tente sans succes' if AUTO_RESTART else 'desactive'}). "
                f"Diagnostic : journalctl -u {unit} -n 50"
            )
            log(f"{name} : EN PANNE")
        elif not up:
            log(f"{name} : toujours en panne (alerte deja envoyee)")


def check_disk(state: dict):
    usage = shutil.disk_usage("/")
    pct = round(usage.used / usage.total * 100)
    today = time.strftime("%Y-%m-%d")
    if pct >= DISK_ALERT and state.get("disk_alert_date") != today:
        state["disk_alert_date"] = today
        free_gb = usage.free / (1024 ** 3)
        telegram(
            f"ALERTE HotspotPro [{HOSTNAME}] : disque rempli a {pct}% "
            f"({free_gb:.1f} Go libres). Nettoyer les logs ou agrandir le disque "
            f"avant que les bases de donnees ne soient bloquees."
        )
        log(f"Disque : {pct}% (alerte envoyee)")


def main():
    state = load_state()
    check_services(state)
    check_disk(state)
    save_state(state)


if __name__ == "__main__":
    main()
