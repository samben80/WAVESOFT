"""Intégration directe de données client dans la base Wavesoft depuis un classeur Excel.

Ordre imposé : familles, articles, clients, fournisseurs, produits, pièces de vente,
pièces d'achat (voir ``integration.json``). Pour chaque ligne :

- l'identifiant est demandé à Wavesoft par ``ws_sp_GetIdTable`` (FTC005), jamais calculé ;
- la ligne de la table paramétrable ``<TABLE>_P`` est créée avec le même identifiant ;
- les codes (famille, client, article…) sont traduits en identifiants, en cherchant
  d'abord dans la base puis dans ce qui vient d'être créé ;
- une fiche dont le code existe déjà est laissée telle quelle (relance possible).

Tout passe dans une seule transaction. Par défaut c'est une simulation : tout est
exécuté (contraintes et triggers Wavesoft compris) puis annulé. Rien n'est gardé
sans ``executer=True``.

Une insertion directe ne passe pas par les traitements de l'ERP : pour les pièces,
totaux, TVA, échéances, mouvements de stock et comptabilisation ne sont pas
recalculés. À valider sur une copie du dossier avant toute exécution réelle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .catalog import Catalog, Column, DbObject

DATA_ROW = 3
PIECE = "PIECE"
CONFIG_DEFAUT = Path(__file__).with_name("integration.json")


# --- configuration -------------------------------------------------------------

@dataclass
class Reference:
    colonne: str  # colonne cible (identifiant), ex. FAMID
    feuille_colonne: str  # colonne du classeur qui porte le code, ex. FAMILLE
    etape: str  # étape qui définit la table de la référence
    defaut: str | None = None


@dataclass
class Etape:
    nom: str
    feuille: str
    libelle: str
    table: str
    id: str
    code: str | None = None
    table_p: str | None = None
    constantes: dict = field(default_factory=dict)
    references: list[Reference] = field(default_factory=list)
    parent: dict | None = None  # {"etape": "ventes", "colonne": "PCVID"}
    nature: str | None = None  # "vente" | "achat"
    nature_colonne: str | None = None
    a_confirmer: bool = False

    @property
    def piece(self) -> bool:
        return bool(self.nature)


@dataclass
class Config:
    etapes: list[Etape]
    natures: dict

    def etape(self, nom: str) -> Etape:
        return next(e for e in self.etapes if e.nom == nom)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        data = json.loads(Path(path or CONFIG_DEFAUT).read_text(encoding="utf-8"))
        etapes = []
        for e in data["etapes"]:
            e = dict(e)
            e["references"] = [Reference(**r) for r in e.get("references", [])]
            etapes.append(Etape(**e))
        return cls(etapes, data.get("natures", {}))


# --- anomalies -----------------------------------------------------------------

@dataclass
class Anomalie:
    feuille: str
    ligne: int  # ligne Excel, 0 si la remarque porte sur la feuille
    message: str
    niveau: str = "erreur"  # erreur | attention

    def __str__(self) -> str:
        where = f"feuille {self.feuille}" + (f" ligne {self.ligne}" if self.ligne else "")
        return f"{self.niveau.upper()} {where} : {self.message}"


def verifier_config(config: Config, catalog: Catalog) -> list[Anomalie]:
    """Compare la configuration au catalogue réel du dossier."""
    out: list[Anomalie] = []
    for e in config.etapes:
        out += verifier_etape(e, catalog)
    nat = config.natures
    if nat and catalog.get(nat.get("table", "")) is None:
        out.append(Anomalie("natures", 0, f"table des natures {nat.get('table')} absente de la base"))
    return out


def verifier_etape(e: Etape, catalog: Catalog) -> list[Anomalie]:
    out: list[Anomalie] = []
    table = catalog.get(e.table)
    if table is None or table.kind != "table":
        return [Anomalie(e.feuille, 0, f"table {e.table} absente de la base")]
    cols = [e.id, e.code, e.nature_colonne, *e.constantes, *(r.colonne for r in e.references),
            (e.parent or {}).get("colonne")]
    for c in filter(None, cols):
        if table.column(c) is None:
            out.append(Anomalie(e.feuille, 0, f"colonne {e.table}.{c} absente de la base"))
    if e.table_p and catalog.get(e.table_p) is None:
        out.append(Anomalie(e.feuille, 0, f"table paramétrable {e.table_p} absente (ne sera pas alimentée)",
                            "attention"))
    return out


# --- colonnes du classeur ------------------------------------------------------

def colonnes_saisies(e: Etape, table: DbObject) -> list[Column]:
    """Colonnes de la table que l'utilisateur renseigne directement."""
    auto = {e.id.lower(), *(c.lower() for c in e.constantes), *(r.colonne.lower() for r in e.references)}
    if e.parent:
        auto.add(e.parent["colonne"].lower())
    if e.nature_colonne:
        auto.add(e.nature_colonne.lower())
    return [c for c in table.columns if c.name.lower() not in auto and not c.identity and not c.computed]


def entetes(e: Etape, table: DbObject) -> list[str]:
    cols = [PIECE] if e.piece or e.parent else []
    cols += [r.feuille_colonne for r in e.references]
    # les colonnes obligatoires d'abord, puis le reste dans l'ordre de la table
    saisies = colonnes_saisies(e, table)
    cols += [c.name for c in saisies if c.required] + [c.name for c in saisies if not c.required]
    return cols


def _decrire(c: Column) -> str:
    parts = [c.description or "", c.sql_type, "OBLIGATOIRE" if c.required else ""]
    return " · ".join(p for p in parts if p)


def ecrire_modele(config: Config, catalog: Catalog, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    info = wb.active
    info.title = "Lisez-moi"
    for line in [
        "Intégration directe dans la base Wavesoft, dans l'ordre des feuilles.",
        "Ligne 1 : noms des colonnes Wavesoft (ne pas modifier). Ligne 2 : description. Données à partir de la ligne 3.",
        "Les codes de référence (FAMILLE, CLIENT, ARTICLE…) sont les codes Wavesoft, pas les identifiants.",
        "FAMILLE vide : la famille DEFAULT est utilisée.",
        "PIECE : code libre qui relie les lignes d'une pièce à son entête.",
        "La nature des pièces (ex. CDECLI, FACCLI) est demandée au lancement.",
        "Les feuilles laissées vides sont ignorées.",
    ]:
        info.append([line])
    info.column_dimensions["A"].width = 110
    for e in config.etapes:
        table = catalog.get(e.table)
        if table is None:
            continue
        ws = wb.create_sheet(e.feuille[:31])
        hdr = entetes(e, table)
        ws.append(hdr)
        descs = []
        for h in hdr:
            col = table.column(h)
            if col is not None:
                descs.append(_decrire(col))
            elif h == PIECE:
                descs.append("code libre de la pièce · OBLIGATOIRE")
            else:
                ref = next(r for r in e.references if r.feuille_colonne == h)
                descs.append(f"code {catalog_ref_label(config, ref)}" + (
                    f" · vide = {ref.defaut}" if ref.defaut else " · OBLIGATOIRE"))
        ws.append(descs)
        for i, h in enumerate(hdr, start=1):
            cell = ws.cell(row=2, column=i)
            if "OBLIGATOIRE" in str(cell.value):
                cell.font = Font(color="C00000")
            ws.cell(row=1, column=i).font = Font(bold=True)
            col = table.column(h)
            if col is None or _famille(col) == "texte":
                for r in range(DATA_ROW, DATA_ROW + 500):
                    ws.cell(row=r, column=i).number_format = "@"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def catalog_ref_label(config: Config, ref: Reference) -> str:
    return config.etape(ref.etape).libelle.lower()


# --- conversion des valeurs ----------------------------------------------------

def _famille(c: Column) -> str:
    t = (c.base_type or c.data_type).lower()
    if c.data_type.upper() == "BOOL":
        return "bool"
    if t in ("char", "varchar", "nchar", "nvarchar", "text", "ntext"):
        return "texte"
    if t in ("int", "bigint", "smallint", "tinyint"):
        return "entier"
    if t in ("decimal", "numeric", "money", "smallmoney", "float", "real"):
        return "nombre"
    if t in ("date", "datetime", "datetime2", "smalldatetime"):
        return "date"
    if t == "bit":
        return "bit"
    return "autre"


def _longueur(c: Column) -> int | None:
    if c.max_length in (None, -1):
        return None
    t = (c.base_type or c.data_type).lower()
    return c.max_length // 2 if t in ("nchar", "nvarchar") else c.max_length


def convertir(c: Column, value):
    """Valeur Excel → valeur SQL ; lève ValueError avec un message lisible."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    fam = _famille(c)
    if fam == "texte":
        if isinstance(value, float) and value.is_integer():
            value = int(value)  # code saisi comme nombre dans Excel
        if isinstance(value, (date, datetime)):
            value = value.strftime("%d/%m/%Y")
        text = str(value).strip()
        n = _longueur(c)
        if n and len(text) > n:
            raise ValueError(f"{len(text)} caractères, maximum {n}")
        return text
    if fam == "bool":
        if isinstance(value, bool):
            return "O" if value else "N"
        text = str(value).strip().upper()
        if text in ("O", "OUI", "1", "VRAI", "TRUE"):
            return "O"
        if text in ("N", "NON", "0", "FAUX", "FALSE"):
            return "N"
        raise ValueError("O ou N attendu")
    if fam == "bit":
        return 1 if str(value).strip().upper() in ("1", "O", "OUI", "TRUE", "VRAI") else 0
    if fam == "entier":
        try:
            d = Decimal(str(value).strip().replace(",", "."))
        except InvalidOperation:
            raise ValueError("nombre entier attendu") from None
        if d != d.to_integral_value():
            raise ValueError("nombre entier attendu")
        return int(d)
    if fam == "nombre":
        try:
            d = Decimal(str(value).strip().replace(" ", "").replace(",", "."))
        except InvalidOperation:
            raise ValueError("nombre attendu") from None
        if c.precision and c.scale is not None and (c.base_type or c.data_type).lower() in ("decimal", "numeric"):
            d = d.quantize(Decimal(1).scaleb(-c.scale))
            if len(d.as_tuple().digits) > c.precision:
                raise ValueError(f"trop grand pour {c.sql_type}")
        return d
    if fam == "date":
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M"):
            try:
                return datetime.strptime(str(value).strip(), fmt)
            except ValueError:
                pass
        raise ValueError("date attendue (jj/mm/aaaa)")
    return value


def _code(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


# --- plan --------------------------------------------------------------------

@dataclass
class Ligne:
    ligne: int  # ligne Excel
    valeurs: dict  # colonne cible -> valeur SQL
    codes: dict  # colonne de référence -> code saisi
    piece: str | None = None
    code: str | None = None


@dataclass
class Plan:
    etapes: list[tuple[Etape, DbObject, list[Ligne]]]
    anomalies: list[Anomalie]
    natures: dict  # "vente"/"achat" -> code nature

    @property
    def erreurs(self) -> list[Anomalie]:
        return [a for a in self.anomalies if a.niveau == "erreur"]


def _lire_feuille(wb, nom: str) -> tuple[list[str], list[tuple[int, list]]]:
    if nom[:31] not in wb.sheetnames:
        return [], []
    ws = wb[nom[:31]]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return [], []
    hdr = [str(h).strip() if h is not None else "" for h in rows[0]]
    data = []
    for i, row in enumerate(rows[DATA_ROW - 1:], start=DATA_ROW):
        if any(v is not None and str(v).strip() for v in row):
            data.append((i, list(row) + [None] * (len(hdr) - len(row))))
    return hdr, data


def preparer(config: Config, catalog: Catalog, classeur: str | Path, natures: dict | None = None) -> Plan:
    """Lit et contrôle tout le classeur sans toucher à la base."""
    from openpyxl import load_workbook

    wb = load_workbook(classeur, data_only=True)
    natures = {k: v for k, v in (natures or {}).items() if v}
    anomalies: list[Anomalie] = []
    plan: list[tuple[Etape, DbObject, list[Ligne]]] = []
    pieces_connues: dict[str, set[str]] = {}
    for e in config.etapes:
        hdr, data = _lire_feuille(wb, e.feuille)
        if not data:
            continue
        verif = verifier_etape(e, catalog)
        anomalies += verif
        table = catalog.get(e.table)
        if any(a.niveau == "erreur" for a in verif):
            continue
        cols = {c.name.lower(): c for c in colonnes_saisies(e, table)}
        refs = {r.feuille_colonne.lower(): r for r in e.references}
        for h in hdr:
            if h and h.lower() not in cols and h.lower() not in refs and h != PIECE:
                anomalies.append(Anomalie(e.feuille, 0, f"colonne {h} inconnue dans {e.table}"))
        if e.piece and e.nature not in natures:
            anomalies.append(Anomalie(e.feuille, 0, f"nature de pièce de {e.nature} non choisie"))
        if e.piece:
            anomalies.append(Anomalie(e.feuille, 0, "insertion directe : totaux, TVA, échéances, stock et "
                                      "comptabilisation ne sont pas recalculés par Wavesoft", "attention"))
        fournies = {h.lower() for h in hdr}
        manquantes = [c.name for c in cols.values() if c.required and c.name.lower() not in fournies]
        if manquantes:
            anomalies.append(Anomalie(e.feuille, 0, "colonnes obligatoires absentes : " + ", ".join(manquantes)))
        lignes: list[Ligne] = []
        vus: set[str] = set()
        for n, row in data:
            ligne = Ligne(n, dict(e.constantes), {})
            for h, v in zip(hdr, row):
                key = h.lower()
                if h == PIECE:
                    ligne.piece = _code(v)
                elif key in refs:
                    ligne.codes[refs[key].colonne] = _code(v) or refs[key].defaut
                elif key in cols:
                    c = cols[key]
                    try:
                        val = convertir(c, v)
                    except ValueError as exc:
                        anomalies.append(Anomalie(e.feuille, n, f"{c.name} : {exc}"))
                        continue
                    if val is None and c.required:
                        anomalies.append(Anomalie(e.feuille, n, f"{c.name} : obligatoire"))
                    if val is not None:
                        ligne.valeurs[c.name] = val
            for r in e.references:
                if not ligne.codes.get(r.colonne):
                    anomalies.append(Anomalie(e.feuille, n, f"{r.feuille_colonne} : obligatoire"))
            if e.nature_colonne and e.nature in natures:
                ligne.valeurs[e.nature_colonne] = natures[e.nature]
            if e.code:
                ligne.code = _code(ligne.valeurs.get(e.code))
                if ligne.code and ligne.code.upper() in vus:
                    anomalies.append(Anomalie(e.feuille, n, f"{e.code} {ligne.code} en double dans la feuille"))
                if ligne.code:
                    vus.add(ligne.code.upper())
            if e.piece or e.parent:
                if not ligne.piece:
                    anomalies.append(Anomalie(e.feuille, n, f"{PIECE} : obligatoire"))
            if e.piece and ligne.piece:
                if ligne.piece in pieces_connues.setdefault(e.nom, set()):
                    anomalies.append(Anomalie(e.feuille, n, f"PIECE {ligne.piece} : deux entêtes"))
                pieces_connues[e.nom].add(ligne.piece)
            if e.parent and ligne.piece and ligne.piece not in pieces_connues.get(e.parent["etape"], set()):
                parent = config.etape(e.parent["etape"])
                anomalies.append(Anomalie(e.feuille, n, f"PIECE {ligne.piece} sans entête dans la feuille {parent.feuille}"))
            lignes.append(ligne)
        plan.append((e, table, lignes))
    return Plan(plan, anomalies, natures)


# --- exécution ---------------------------------------------------------------

def _q(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


class Base:
    """Accès à la base pendant l'intégration (une seule transaction)."""

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def chercher(self, table: str, id_col: str, code_col: str, code: str, filtres: dict) -> int | None:
        where = " AND ".join([f"{_q(code_col)} = ?"] + [f"{_q(k)} = ?" for k in filtres])
        self.cur.execute(f"SELECT {_q(id_col)} FROM dbo.{_q(table)} WHERE {where}", code, *filtres.values())
        row = self.cur.fetchone()
        return row[0] if row else None

    def nouvel_id(self, table: str) -> int:
        self.cur.execute("SET NOCOUNT ON; DECLARE @id bigint; EXEC @id = dbo.ws_sp_GetIdTable ?; SELECT @id", table)
        row = self.cur.fetchone()
        if row is None or row[0] is None:
            raise RuntimeError(f"ws_sp_GetIdTable n'a pas rendu d'identifiant pour {table}")
        return int(row[0])

    def inserer(self, table: str, valeurs: dict) -> None:
        cols = ", ".join(_q(c) for c in valeurs)
        marks = ", ".join("?" for _ in valeurs)
        self.cur.execute(f"INSERT INTO dbo.{_q(table)} ({cols}) VALUES ({marks})", *valeurs.values())

    def identity(self) -> int:
        self.cur.execute("SELECT CAST(SCOPE_IDENTITY() AS bigint)")
        return int(self.cur.fetchone()[0])


@dataclass
class Bilan:
    crees: dict = field(default_factory=dict)  # étape -> nombre
    existants: dict = field(default_factory=dict)
    anomalies: list[Anomalie] = field(default_factory=list)
    execute: bool = False


def integrer(plan: Plan, config: Config, catalog: Catalog, conn, executer: bool = False) -> Bilan:
    """Écrit le plan dans la base, dans une transaction ; annule tout sauf si ``executer``."""
    if plan.erreurs:
        raise ValueError("le classeur contient des erreurs : rien n'est écrit")
    base = Base(conn)
    bilan = Bilan(anomalies=list(plan.anomalies))
    crees: dict[tuple[str, str], int] = {}  # (étape, code) -> id
    pieces: dict[tuple[str, str], int | None] = {}  # (étape, PIECE) -> id ; None = pièce non créée
    try:
        for e, table, lignes in plan.etapes:
            id_col = table.column(e.id)
            table_p = catalog.get(e.table_p) if e.table_p else None
            for ligne in lignes:
                valeurs = dict(ligne.valeurs)
                erreur = False
                for r in e.references:
                    cible = config.etape(r.etape)
                    code = ligne.codes[r.colonne]
                    ref_id = crees.get((cible.nom, code.upper())) or base.chercher(
                        cible.table, cible.id, cible.code, code, cible.constantes)
                    if ref_id is None:
                        bilan.anomalies.append(Anomalie(e.feuille, ligne.ligne,
                                                        f"{r.feuille_colonne} {code} introuvable dans Wavesoft"))
                        erreur = True
                    valeurs[r.colonne] = ref_id
                if e.parent:
                    parent_id = pieces.get((e.parent["etape"], ligne.piece))
                    if parent_id is None:
                        continue  # entête en erreur ou déjà présente : ses lignes ne sont pas ajoutées
                    valeurs[e.parent["colonne"]] = parent_id
                if erreur:
                    if ligne.piece and e.piece:
                        pieces[(e.nom, ligne.piece)] = None
                    continue
                if ligne.code and not e.parent:
                    existant = base.chercher(e.table, e.id, e.code, ligne.code, e.constantes)
                    if existant is not None:
                        bilan.existants[e.nom] = bilan.existants.get(e.nom, 0) + 1
                        bilan.anomalies.append(Anomalie(e.feuille, ligne.ligne,
                                                        f"{e.code} {ligne.code} existe déjà : laissé tel quel", "attention"))
                        crees[(e.nom, ligne.code.upper())] = existant
                        if ligne.piece and e.piece:
                            pieces[(e.nom, ligne.piece)] = None  # ne jamais compléter une pièce existante
                        continue
                if id_col is not None and id_col.identity:
                    base.inserer(e.table, valeurs)
                    new_id = base.identity()
                else:
                    new_id = base.nouvel_id(e.table)
                    base.inserer(e.table, {e.id: new_id, **valeurs})
                if table_p is not None and table_p.column(e.id) is not None:
                    base.inserer(table_p.name, {e.id: new_id})
                bilan.crees[e.nom] = bilan.crees.get(e.nom, 0) + 1
                if ligne.code:
                    crees[(e.nom, ligne.code.upper())] = new_id
                if ligne.piece and e.piece:
                    pieces[(e.nom, ligne.piece)] = new_id
        if any(a.niveau == "erreur" for a in bilan.anomalies):
            conn.rollback()
        elif executer:
            conn.commit()
            bilan.execute = True
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    return bilan


def natures_disponibles(config: Config, conn) -> list[tuple[str, str]]:
    nat = config.natures
    cur = conn.cursor()
    cur.execute(f"SELECT {_q(nat['code'])}, {_q(nat.get('libelle') or nat['code'])} "
                f"FROM dbo.{_q(nat['table'])} ORDER BY 1")
    return [(str(r[0]).strip(), str(r[1] or "").strip()) for r in cur.fetchall()]
