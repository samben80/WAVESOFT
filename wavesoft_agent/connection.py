"""Connexion à la base Wavesoft (SQL Server via ODBC).

Paramètres lus dans les variables d'environnement (ou un fichier ``.env``
placé à côté de l'agent), jamais écrits dans le code ni dans le dépôt :

=====================  =====================================================
WAVESOFT_SERVER        ``SERVEUR\\INSTANCE`` ou ``hôte,port``
WAVESOFT_DATABASE      nom de la base de la société Wavesoft
WAVESOFT_USER          compte SQL (laisser vide = authentification Windows)
WAVESOFT_PASSWORD      mot de passe du compte SQL
WAVESOFT_DRIVER        pilote ODBC (défaut : ODBC Driver 18 for SQL Server)
WAVESOFT_TRUST_CERT    ``yes`` si le serveur a un certificat auto-signé
=====================  =====================================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DRIVER = "ODBC Driver 18 for SQL Server"


@dataclass
class ConnectionSettings:
    server: str
    database: str
    user: str | None = None
    password: str | None = None
    driver: str = DEFAULT_DRIVER
    trust_server_certificate: bool = False
    timeout: int = 15

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env", **overrides) -> "ConnectionSettings":
        env = dict(_read_env_file(env_file)) if env_file else {}
        env.update(os.environ)
        values = {
            "server": env.get("WAVESOFT_SERVER", ""),
            "database": env.get("WAVESOFT_DATABASE", ""),
            "user": env.get("WAVESOFT_USER") or None,
            "password": env.get("WAVESOFT_PASSWORD") or None,
            "driver": env.get("WAVESOFT_DRIVER") or DEFAULT_DRIVER,
            "trust_server_certificate": env.get("WAVESOFT_TRUST_CERT", "").lower() in ("1", "yes", "true", "oui"),
        }
        values.update({k: v for k, v in overrides.items() if v not in (None, "")})
        if not values["server"] or not values["database"]:
            raise ValueError("Renseigner WAVESOFT_SERVER et WAVESOFT_DATABASE (ou --server / --database).")
        return cls(**values)

    def connection_string(self, write: bool = False) -> str:
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={self.database}",
        ]
        if not write:
            # Découverte et contrôles ne font que lire : on le dit au serveur (utile sur un groupe AlwaysOn).
            parts.append("ApplicationIntent=ReadOnly")
        parts.append("APP=wavesoft-agent")
        if self.user:
            parts += [f"UID={self.user}", f"PWD={{{(self.password or '').replace('}', '}}')}}}"]
        else:
            parts.append("Trusted_Connection=yes")
        if self.trust_server_certificate:
            parts.append("TrustServerCertificate=yes")
        return ";".join(parts) + ";"


def connect(settings: ConnectionSettings, write: bool = False):
    """Connexion en lecture seule, sauf ``write=True`` réservé à l'intégration
    directe (transaction explicite, validée ou annulée par l'appelant)."""
    try:
        import pyodbc
    except ImportError as exc:  # pragma: no cover - dépend du poste
        raise RuntimeError("Installer pyodbc : pip install pyodbc (et le pilote ODBC SQL Server).") from exc
    return pyodbc.connect(settings.connection_string(write), timeout=settings.timeout,
                          readonly=not write, autocommit=False)


def _read_env_file(path: str | Path):
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            yield key.strip(), value.strip().strip('"').strip("'")
