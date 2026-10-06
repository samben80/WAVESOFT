"""Préparation des tâches de l'Automate de transferts Wavesoft (table WSAUTOMATE).

L'agent ne modifie jamais la base : il produit un script SQL à relire, qui appelle
la procédure éditeur ws_sp_add_tache_automate (FTC005) une fois par objet, comme le
demande le guide Automate (« 2 commandes à importer, 2 enregistrements »).
La lecture de l'état des tâches (TRSETAT, TRSERREUR) se fait en lecture seule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .formats import FormatSpec
from .importfile import read_file

# Guide Automate de transferts V26.00.01, « Liste des valeurs pour TRSENTITE ».
ENTITES = {
    1: "écritures comptables", 2: "comptes", 4: "clients", 5: "fournisseurs", 6: "commerciaux",
    7: "familles d'articles", 8: "articles", 9: "produits", 10: "tarifs articles", 11: "nomenclatures",
    13: "prospects", 14: "actions", 15: "tarifs particuliers", 16: "familles de produits",
    18: "tarifs produits", 19: "affaires", 23: "profils TVA articles", 24: "traitement de pièces de vente",
    25: "profils TVA produits", 27: "modèles analytiques", 28: "traitement de pièces d'achat",
    99: "pièces de vente", 111: "écritures non indexées", 112: "écritures avec analytique",
    113: "écritures non indexées avec analytique", 121: "contacts clients", 122: "contacts fournisseurs",
    123: "contacts prospects", 171: "adresses de livraison clients", 172: "adresses de livraison prospects",
    199: "pièces d'achat", 299: "pièces de stock", 400: "import de pièces EDI",
}

ENTITE_PAR_FORMAT = {
    "FTC002": 99, "FTC002-TRAITEMENT": 24, "FTC007": 199, "FTC007-TRAITEMENT": 28,
    "FTC011": 299, "FTC022-CDE": 400,
}

SEPARATEURS = {";": "V", "\t": "C"}


@dataclass
class Tache:
    contenu: str  # texte exact du fichier pour un objet
    premiere_ligne: int
    resume: str  # première ligne, pour le commentaire du script


def entite_pour(spec: FormatSpec, entite: int | None = None) -> int:
    if entite is None:
        entite = ENTITE_PAR_FORMAT.get(spec.code.upper())
    if entite is None:
        raise ValueError(f"pas d'entité Automate connue pour {spec.code} : préciser --entite")
    if entite not in ENTITES:
        raise ValueError(f"entité Automate inconnue : {entite}")
    return entite


def decouper(path: str | Path, spec: FormatSpec, sep: str | None = None) -> tuple[list[Tache], str]:
    """Découpe un fichier d'import en une tâche par objet : une pièce (de sa ligne
    d'entête à la suivante) ou une commande isolée (CLO, SUP…). Un format sans entête
    de pièce reste en une seule tâche."""
    records, sep = read_file(path, spec, sep)
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode(spec.encodage)
    lines = text.splitlines(keepends=True)

    starts: list[int] = []
    for r in records:
        rec = spec.enregistrements.get("*" if spec.sans_code else r.code)
        nouvelle = not starts or (spec.debut_document and (
            r.code == spec.debut_document or (rec is not None and rec.niveau == "commande")))
        if nouvelle:
            starts.append(r.line)
    taches = []
    for i, start in enumerate(starts):
        end = starts[i + 1] - 1 if i + 1 < len(starts) else len(lines)
        contenu = "".join(lines[start - 1:end])
        taches.append(Tache(contenu, start, lines[start - 1].rstrip("\r\n")))
    return taches, sep


def _sql_text(value: str) -> str:
    return "N'" + value.replace("'", "''") + "'"


def script_sql(taches: list[Tache], entite: int, sep: str, profil: str = "", tcp: bool = False,
               source: str = "") -> str:
    """Script T-SQL qui ajoute les tâches dans WSAUTOMATE, tout ou rien, puis liste
    les TRSID créés pour suivre leur état."""
    if sep not in SEPARATEURS:
        raise ValueError("séparateur non géré par l'Automate (; ou tabulation)")
    out = [
        f"-- Tâches Automate Wavesoft générées par wavesoft_agent{f' depuis {source}' if source else ''}",
        f"-- Entité {entite} ({ENTITES[entite]}), {len(taches)} tâche(s), une par objet.",
        "-- À RELIRE avant exécution : ce script écrit dans la table WSAUTOMATE du dossier.",
        "-- L'Automate traite les tâches en état I puis renseigne TRSETAT (C, T ou E) et TRSERREUR.",
        f"-- @TRSISTCP = '{'O' if tcp else 'N'}' : "
        + ("tâches réservées à un automate en mode serveur TCP." if tcp
           else "tâches traitées par l'automate en mode normal (la valeur par défaut 'O' les réserve au mode TCP)."),
        "SET XACT_ABORT ON;",
        "SET NOCOUNT ON;",
        "DECLARE @taches TABLE (ordre int, TRSID bigint);",
        "DECLARE @id bigint;",
        "BEGIN TRANSACTION;",
    ]
    for n, t in enumerate(taches, start=1):
        out += [
            "",
            f"-- {n}/{len(taches)} : ligne {t.premiere_ligne} : {t.resume[:80].replace(chr(10), ' ')}",
            "EXEC @id = dbo.ws_sp_add_tache_automate",
            f"    @TRSENTITE = {entite},",
            f"    @TRSPROFIL = {_sql_text(profil)},",
            f"    @TRSFILE = {_sql_text(t.contenu)},",
            f"    @TRSSEPARATEUR = '{SEPARATEURS[sep]}',",
            f"    @TRSISTCP = '{'O' if tcp else 'N'}';",
            f"INSERT INTO @taches VALUES ({n}, @id);",
        ]
    out += [
        "",
        "COMMIT TRANSACTION;",
        "SELECT t.ordre, a.TRSID, a.TRSETAT, a.TRSERREUR",
        "FROM @taches t JOIN dbo.WSAUTOMATE a ON a.TRSID = t.TRSID ORDER BY t.ordre;",
        "",
    ]
    return "\r\n".join(out)


ETAT_SQL = """SELECT TRSID, TRSENTITE, TRSPROFIL, TRSETAT, TRSIDOBJET, TRSCODEOBJET, TRSERREUR
FROM dbo.WSAUTOMATE
WHERE {where}
ORDER BY TRSID"""

ETATS = {"I": "à faire", "C": "en cours", "T": "terminé", "E": "en erreur"}


def lire_etat(conn, ids: list[int] | None = None, erreurs: bool = False) -> list[dict]:
    """Lit l'état des tâches (lecture seule) : par TRSID, ou toutes celles en erreur."""
    if ids:
        where, params = f"TRSID IN ({', '.join('?' for _ in ids)})", list(ids)
    elif erreurs:
        where, params = "TRSETAT = 'E'", []
    else:
        where, params = "TRSETAT IN ('I', 'C', 'E')", []
    cur = conn.cursor()
    cur.execute(ETAT_SQL.format(where=where), *params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
