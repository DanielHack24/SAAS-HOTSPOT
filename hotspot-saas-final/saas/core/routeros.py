"""
routeros.py — Petit client REST RouterOS (v7) via le tunnel WireGuard.

On atteint le routeur à son IP de tunnel, sur le service web restreint à
notre seule IP (le trafic est déjà chiffré par WireGuard). Authentification
HTTP Basic avec le compte API dédié.

Volontairement minimal (get/post) : la logique métier (User Manager) vit
dans um_sync.py pour rester facile à ajuster selon la config du routeur.
"""
import requests

# Droits des scripts temporaires exécutés sur le routeur (tickets, règles
# VPN) : sous-ensemble strict du groupe du compte plateforme (wireguard.py).
SCRIPT_POLICY = "read,write,test"


class RouterUnreachable(Exception):
    """Le routeur ne répond pas (tunnel down, routeur éteint)."""


class RouterOSError(Exception):
    """Le routeur a répondu par une erreur (requête invalide, doublon…)."""


class RouterOSAuthError(RouterOSError):
    """Identifiants API refusés (401/403)."""


class RouterOSRest:
    def __init__(self, host: str, user: str, password: str,
                 port: int = 80, timeout: int = 8):
        self.base = f"http://{host}:{port}/rest"
        self.timeout = timeout
        # Session persistante : garde la connexion TCP ouverte (keep-alive)
        # entre les requêtes. Un gros lot (des centaines de tickets) réutilise
        # le même socket au lieu de rouvrir une connexion à chaque PUT — 2 à
        # 3× plus rapide sur un tunnel à latence élevée.
        self.session = requests.Session()
        self.session.auth = (user, password)

    def _req(self, method: str, path: str, data: dict | None = None):
        url = self.base + path
        try:
            r = self.session.request(method, url, json=data, timeout=self.timeout)
        except requests.exceptions.RequestException as e:
            raise RouterUnreachable(str(e))
        if r.status_code == 401:
            raise RouterOSAuthError("Authentification refusée (401).")
        if r.status_code == 403:
            raise RouterOSAuthError("Accès refusé (403).")
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except Exception:
                detail = r.text[:200]
            raise RouterOSError(detail or f"HTTP {r.status_code}")
        return r.json() if r.text else {}

    def get(self, path: str):
        return self._req("GET", path)

    def create(self, path: str, data: dict):
        """Ajoute un élément (sémantique REST RouterOS : PUT = add)."""
        return self._req("PUT", path, data)

    def update(self, path_with_id: str, data: dict):
        return self._req("PATCH", path_with_id, data)

    def delete(self, path_with_id: str):
        return self._req("DELETE", path_with_id)

    def ping(self) -> bool:
        """Vérifie que le routeur répond et que l'authentification passe."""
        self.get("/system/resource")
        return True

    def run_script(self, source: str, name: str = "hotspotpro_bulk"):
        """Crée un script temporaire, l'exécute sur le routeur puis le supprime.

        Exécuter les commandes LOCALEMENT sur le routeur (un seul envoi de la
        source) est ~25× plus rapide que de faire un appel REST par commande :
        plus d'aller-retour réseau par ticket. Idempotent : retire d'abord un
        éventuel script homonyme resté d'un run interrompu."""
        for sc in self.get("/system/script"):
            if sc.get("name") == name:
                self.delete("/system/script/" + sc[".id"])
        # Politique explicite, limitée aux droits du compte plateforme : sans
        # elle, RouterOS attribue au script des droits plus larges que ceux
        # du compte, et son exécution serait refusée.
        self.create("/system/script", {"name": name, "source": source,
                                       "policy": SCRIPT_POLICY})
        try:
            self._req("POST", "/system/script/run", {"number": name})
        finally:
            for sc in self.get("/system/script"):
                if sc.get("name") == name:
                    self.delete("/system/script/" + sc[".id"])

    def close(self):
        """Ferme la session (libère le socket keep-alive)."""
        try:
            self.session.close()
        except Exception:
            pass


def is_already_exists(err: Exception) -> bool:
    """Vrai si l'erreur signale un doublon (idempotence). Couvre le User
    Manager (« already exists ») et le Hotspot local (« already have user
    with this name »)."""
    msg = str(err).lower()
    return "already exists" in msg or "already have" in msg
