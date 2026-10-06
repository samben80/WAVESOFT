"""Formats de fichiers d'import Wavesoft (fichiers texte multi-enregistrements).

Les fichiers d'import Wavesoft (pièces de vente, d'achat, divers, EDI…) sont
des fichiers texte où chaque ligne commence par un **type d'enregistrement**
(``E`` entête, ``AF`` adresse de facturation, ``LA`` ligne article…) suivi de
colonnes séparées par ``;`` ou une tabulation.

Une *définition de format* (fichier JSON) décrit, pour un flux, chaque type
d'enregistrement et ses colonnes : position, obligatoire ou non, type, longueur.
Les définitions sont la traduction des fiches techniques de l'éditeur ; elles ne
sont pas livrées avec le code (voir ``specs/README.md``).

Schéma d'une définition ::

    {
      "code": "FTC002", "titre": "Pièces de vente", "version": "42",
      "separateurs": [";", "\\t"], "format_date": "dd/mm/yyyy", "encodage": "cp1252",
      "documents": {"debut": "E"},          # enregistrement qui ouvre un document
      "enregistrements": {
        "E":  {"libelle": "Entête", "niveau": "entete", "champs": [ ... ]},
        "LC": {"libelle": "Ligne commentaire", "comme": "LT"},    # même format que LT
        "LD": {"libelle": "Champs paramétrables", "niveau": "ligne",
               "suit": ["LA", "LO"], "repetition_depuis": 2, "champs": [ ... ]}
      }
    }

Chaque champ : ``{"n": 3, "nom": "date_effet", "libelle": "Date d'effet",
"obligatoire": true, "type": "date"}`` avec ``type`` parmi :

=============  ==============================================================
``texte``      chaîne, ``max`` = longueur maximale
``texte_long`` chaîne entre guillemets, sauts de ligne permis (notes)
``decimal``    nombre, ``decimales`` = nombre de décimales écrites
``entier``     nombre entier
``date``       date au ``format_date`` du fichier (ou ``format`` du champ)
``heure``      ``hh:mm``
``on``         ``O`` ou ``N``
``liste``      une des ``valeurs``
``vide``       colonne réservée, toujours vide
``libre``      valeur quelconque
=============  ==============================================================
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELD_TYPES = {"texte", "texte_long", "decimal", "entier", "date", "heure", "on", "liste", "vide", "libre"}


@dataclass
class FieldSpec:
    n: int
    nom: str
    libelle: str = ""
    obligatoire: bool = False
    type: str = "texte"
    max: int | None = None
    decimales: int | None = None
    valeurs: list[str] | None = None
    format: str | None = None
    note: str | None = None
    export_seulement: bool = False


@dataclass
class RecordSpec:
    code: str
    libelle: str = ""
    niveau: str = "entete"  # entete | ligne | commande
    champs: list[FieldSpec] = field(default_factory=list)
    suit: list[str] | None = None  # doit suivre un de ces enregistrements (ex. LD après LA)
    repetition_depuis: int | None = None  # colonnes répétées par paires à partir de ce n°
    unique: bool = False  # au plus une fois par document
    note: str | None = None

    def field(self, nom: str) -> FieldSpec | None:
        return next((f for f in self.champs if f.nom == nom), None)


@dataclass
class FormatSpec:
    code: str
    titre: str
    enregistrements: dict[str, RecordSpec]
    version: str | None = None
    separateurs: list[str] = field(default_factory=lambda: [";", "\t"])
    format_date: str = "dd/mm/yyyy"
    encodage: str = "cp1252"
    debut_document: str | None = "E"
    note: str | None = None

    @property
    def sans_code(self) -> bool:
        """Format à un seul enregistrement sans type en 1re colonne (formats FTC004)."""
        return list(self.enregistrements) == ["*"]

    @classmethod
    def from_dict(cls, data: dict) -> "FormatSpec":
        raw = data["enregistrements"]
        records: dict[str, RecordSpec] = {}
        for code, r in raw.items():
            base = raw[r["comme"]] if "comme" in r else r
            records[code] = RecordSpec(
                code=code,
                libelle=r.get("libelle", base.get("libelle", "")),
                niveau=r.get("niveau", base.get("niveau", "entete")),
                champs=[_field(dict(f, valeurs=[code]) if f.get("n") == 1 and "comme" in r else f, code)
                        for f in base.get("champs", [])],
                suit=r.get("suit", base.get("suit")),
                repetition_depuis=r.get("repetition_depuis", base.get("repetition_depuis")),
                unique=r.get("unique", base.get("unique", False)),
                note=r.get("note"),
            )
        for rec in records.values():
            _check_record(rec)
        return cls(
            code=data["code"],
            titre=data.get("titre", data["code"]),
            version=data.get("version"),
            enregistrements=records,
            separateurs=data.get("separateurs", [";", "\t"]),
            format_date=data.get("format_date", "dd/mm/yyyy"),
            encodage=data.get("encodage", "cp1252"),
            debut_document=(data.get("documents") or {}).get("debut", "E"),
            note=data.get("note"),
        )

    @classmethod
    def load(cls, path: str | Path) -> "FormatSpec":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _field(f: dict, code: str) -> FieldSpec:
    spec = FieldSpec(**f)
    # La 1re colonne est le type d'enregistrement (sauf format sans code, noté "*").
    if code != "*" and spec.n == 1 and spec.type == "texte" and not spec.valeurs:
        spec.type, spec.valeurs, spec.obligatoire = "liste", [code], True
    return spec


def _check_record(rec: RecordSpec) -> None:
    nums = [f.n for f in rec.champs]
    if nums != sorted(set(nums)):
        raise ValueError(f"{rec.code} : numéros de colonnes non croissants ou en double {nums}")
    for f in rec.champs:
        if f.type not in FIELD_TYPES:
            raise ValueError(f"{rec.code}.{f.nom} : type inconnu {f.type!r}")


def load_specs(folder: str | Path) -> dict[str, FormatSpec]:
    """Charge toutes les définitions ``*.json`` d'un dossier, indexées par code."""
    specs = {}
    for path in sorted(Path(folder).glob("*.json")):
        spec = FormatSpec.load(path)
        specs[spec.code] = spec
    return dict(sorted(specs.items()))


# --- conversion et contrôle des valeurs ---------------------------------------

_PY_DATE = {"dd/mm/yyyy": "%d/%m/%Y", "ddmmyyyy": "%d%m%Y", "yyyymmdd": "%Y%m%d"}
_EMPTY = (None, "")


def format_value(spec: FieldSpec, value, date_format: str = "dd/mm/yyyy") -> str:
    """Convertit une valeur Python (ou texte) dans l'écriture attendue par Wavesoft."""
    if value in _EMPTY or (isinstance(value, float) and value != value):  # None, "", NaN
        return ""
    t = spec.type
    if t == "date" and isinstance(value, (date, datetime)):
        return value.strftime(_PY_DATE[spec.format or date_format])
    if t == "heure" and isinstance(value, datetime):
        return value.strftime("%H:%M")
    if t == "on" and isinstance(value, bool):
        return "O" if value else "N"
    if t == "decimal" and isinstance(value, (int, float, Decimal)):
        q = Decimal(str(value))
        return f"{q:.{spec.decimales}f}" if spec.decimales is not None else format(q.normalize(), "f")
    if t == "entier" and isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float) and value.is_integer() and t in ("texte", "liste", "libre"):
        value = int(value)  # nombre saisi dans Excel pour un code (ex. 2 au lieu de "2")
    text = str(value)
    if t in ("texte", "texte_long", "liste", "on"):
        text = text.strip()
    return text


def check_value(spec: FieldSpec, text: str, date_format: str = "dd/mm/yyyy") -> str | None:
    """Renvoie un message d'erreur si ``text`` (déjà formaté) ne respecte pas la colonne."""
    if text == "":
        return "obligatoire" if spec.obligatoire else None
    t = spec.type
    if t in ("texte", "texte_long") and spec.max and len(text) > spec.max:
        return f"{len(text)} caractères, maximum {spec.max}"
    if t == "decimal":
        try:
            Decimal(text)
        except InvalidOperation:
            return f"nombre attendu (point décimal), reçu {text!r}"
        if "," in text:
            return "le séparateur décimal doit être un point"
    if t == "entier" and not re.fullmatch(r"-?\d+", text):
        return f"entier attendu, reçu {text!r}"
    if t == "date":
        fmt = spec.format or date_format
        try:
            datetime.strptime(text, _PY_DATE[fmt])
        except ValueError:
            return f"date au format {fmt} attendue, reçu {text!r}"
    if t == "heure" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text):
        return f"heure hh:mm attendue, reçu {text!r}"
    if t == "on" and text not in ("O", "N"):
        return f"O ou N attendu, reçu {text!r}"
    if t == "liste" and spec.valeurs and text not in spec.valeurs:
        return f"valeur parmi {', '.join(spec.valeurs)} attendue, reçu {text!r}"
    if t != "texte_long" and ("\n" in text or "\r" in text):
        return "saut de ligne interdit dans cette colonne"
    return None
