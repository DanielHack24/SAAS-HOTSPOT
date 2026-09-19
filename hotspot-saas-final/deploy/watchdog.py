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
  WATCHDOG_CPU_ALERT=85            seuil d'alerte processeur occupé (%)
  WATCHDOG_STEAL_ALERT=30          seuil d'alerte processeur « volé » (%)

Stdlib uniquement : aucun paquet à installer.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
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
CPU_ALERT    = int(_cfg("watchdog_cpu_alert", "WATCHDOG_CPU_ALERT", "85") or 85)
STEAL_ALERT  = int(_cfg("watchdog_steal_alert", "WATCHDOG_STEAL_ALERT", "30") or 30)
CPU_SAMPLE_S = 5    # durée de la mesure processeur
CPU_STREAK   = 3    # nb de passages consécutifs (≈ 6 min) avant alerte


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


def _cpu_times():
    """Ligne « cpu » de /proc/stat -> (total, occupé, volé)."""
    with open("/proc/stat") as f:
        vals = [int(v) for v in f.readline().split()[1:]]
    vals += [0] * (8 - len(vals))
    user, nice, system, idle, iowait, irq, softirq, steal = vals[:8]
    total = sum(vals[:8])
    return total, total - idle - iowait, steal


def cpu_usage(sample_s: float = CPU_SAMPLE_S):
    """(occupé %, volé %) mesurés sur `sample_s` secondes.

    « Volé » (steal) = temps que l'hyperviseur refuse à la machine. Sur une
    instance AWS à crédits (t2/t3) il grimpe quand les crédits processeur
    sont épuisés : le serveur est alors bridé et tout ralentit."""
    t1, b1, s1 = _cpu_times()
    time.sleep(sample_s)
    t2, b2, s2 = _cpu_times()
    dt = max(t2 - t1, 1)
    return round((b2 - b1) * 100 / dt), round((s2 - s1) * 100 / dt)


def check_cpu(state: dict):
    try:
        busy, steal = cpu_usage()
    except (OSError, ValueError):
        return   # pas de /proc/stat (hors Linux) : rien à surveiller
    high = busy >= CPU_ALERT or steal >= STEAL_ALERT
    streak = state.get("cpu_streak", 0) + 1 if high else 0
    state["cpu_streak"] = streak
    alerted = state.get("cpu_alerted", False)

    if streak >= CPU_STREAK and not alerted:
        state["cpu_alerted"] = True
        cause = (f"processeur bridé par l'hébergeur ({steal}% volé : crédits CPU "
                 f"probablement épuisés)" if steal >= STEAL_ALERT
                 else f"processeur occupé à {busy}%")
        telegram(
            f"ALERTE HotspotPro [{HOSTNAME}] : {cause} depuis plusieurs minutes. "
            f"Le site et les notifications de vente vont ralentir. "
            f"Solution durable : passer sur une instance plus puissante ou sans "
            f"crédits limités. Diagnostic : top, journalctl -u hotspot-web -n 50"
        )
        log(f"CPU : occupé {busy}% / volé {steal}% (alerte envoyée)")
    elif not high and alerted:
        state["cpu_alerted"] = False
        telegram(f"HotspotPro [{HOSTNAME}] : charge processeur revenue à la normale "
                 f"({busy}% occupé).")
        log(f"CPU : retour à la normale ({busy}%)")
    elif high:
        log(f"CPU : occupé {busy}% / volé {steal}% (série {streak}/{CPU_STREAK})")


def main():
    state = load_state()
    check_services(state)
    check_disk(state)
    check_cpu(state)
    save_state(state)


if __name__ == "__main__":
    main()
