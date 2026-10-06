"""Modèle en mémoire du catalogue d'une base Wavesoft (SQL Server).

Le catalogue est produit par :mod:`wavesoft_agent.discovery` et sérialisé en
JSON pour que les étapes suivantes (lecture des fiches techniques, mapping,
génération des fichiers d'intégration) travaillent hors connexion.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Types d'objets SQL Server (sys.objects.type) -> libellé lisible.
OBJECT_KINDS = {
    "U": "table",
    "V": "view",
    "P": "procedure",
    "PC": "procedure",  # CLR
    "X": "procedure",  # procédure étendue
    "FN": "function",
    "IF": "function",
    "TF": "function",
    "FS": "function",  # CLR
    "FT": "function",  # CLR
    "TR": "trigger",
    "SN": "synonym",
    "SO": "sequence",
}


@dataclass
class Column:
    name: str
    position: int
    data_type: str
    base_type: str | None = None  # type système sous un type utilisateur Wavesoft (ID, BOOL, MNTCPT…)
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool = True
    identity: bool = False
    computed: bool = False
    default: str | None = None
    computed_definition: str | None = None
    collation: str | None = None
    description: str | None = None

    @property
    def sql_type(self) -> str:
        """Type tel qu'on l'écrirait dans un CREATE TABLE (ex. ``nvarchar(50)``).

        Pour un type utilisateur, renvoie ``BOOL (char(1))``."""
        if self.base_type:
            return f"{self.data_type} ({Column(self.name, 0, self.base_type, None, self.max_length, self.precision, self.scale).sql_type})"
        t = self.data_type.lower()
        if t in ("varchar", "char", "varbinary", "binary"):
            return f"{t}({'max' if self.max_length == -1 else self.max_length})"
        if t in ("nvarchar", "nchar"):
            if self.max_length == -1:
                return f"{t}(max)"
            return f"{t}({(self.max_length or 0) // 2})"
        if t in ("decimal", "numeric"):
            return f"{t}({self.precision},{self.scale})"
        if t in ("datetime2", "time", "datetimeoffset") and self.scale is not None:
            return f"{t}({self.scale})"
        return t

    @property
    def required(self) -> bool:
        """Doit être fourni par un fichier d'intégration (pas de valeur auto)."""
        return not (self.nullable or self.identity or self.computed or self.default)


@dataclass
class Key:
    name: str
    kind: str  # "primary_key" | "unique" | "index"
    columns: list[str]
    clustered: bool = False


@dataclass
class ForeignKey:
    name: str
    columns: list[str]
    ref_schema: str
    ref_table: str
    ref_columns: list[str]
    on_delete: str = "NO_ACTION"
    on_update: str = "NO_ACTION"
    disabled: bool = False


@dataclass
class Parameter:
    name: str
    position: int
    data_type: str
    base_type: str | None = None  # type système sous un type utilisateur Wavesoft (ID, BOOL, MNTCPT…)
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    output: bool = False
    has_default: bool = False


@dataclass
class DbObject:
    schema: str
    name: str
    type_code: str
    kind: str
    object_id: int | None = None
    created: str | None = None
    modified: str | None = None
    description: str | None = None
    definition: str | None = None
    columns: list[Column] = field(default_factory=list)
    keys: list[Key] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    checks: dict[str, str] = field(default_factory=dict)
    parameters: list[Parameter] = field(default_factory=list)
    row_count: int | None = None
    parent: str | None = None  # table d'un trigger
    trigger_events: list[str] = field(default_factory=list)
    disabled: bool = False
    base_object: str | None = None  # cible d'un synonyme
    references: list[str] = field(default_factory=list)  # objets utilisés

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def specific(self) -> bool:
        """Objet ajouté hors standard Wavesoft (convention FTC010 : préfixes EXT_ / V_EXT_)."""
        return self.name.upper().startswith(("EXT_", "V_EXT_"))

    @property
    def primary_key(self) -> Key | None:
        return next((k for k in self.keys if k.kind == "primary_key"), None)

    def column(self, name: str) -> Column | None:
        low = name.lower()
        return next((c for c in self.columns if c.name.lower() == low), None)

    def required_columns(self) -> list[Column]:
        return [c for c in self.columns if c.required]


@dataclass
class Catalog:
    server: str
    database: str
    extracted_at: str
    server_version: str | None = None
    collation: str | None = None
    compatibility_level: int | None = None
    objects: list[DbObject] = field(default_factory=list)

    # --- accès ------------------------------------------------------------
    def get(self, name: str) -> DbObject | None:
        """Retrouve un objet par ``schema.nom`` ou ``nom`` (insensible à la casse)."""
        low = name.lower().replace("[", "").replace("]", "")
        for o in self.objects:
            if o.full_name.lower() == low:
                return o
        matches = [o for o in self.objects if o.name.lower() == low]
        return matches[0] if len(matches) == 1 else None

    def of_kind(self, kind: str) -> list[DbObject]:
        return [o for o in self.objects if o.kind == kind]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.objects:
            out[o.kind] = out.get(o.kind, 0) + 1
        return dict(sorted(out.items()))

    def referenced_by(self, name: str) -> list[DbObject]:
        """Objets (vues, procédures, triggers…) qui utilisent ``name``."""
        target = self.get(name)
        if target is None:
            return []
        full = target.full_name.lower()
        return [o for o in self.objects if full in (r.lower() for r in o.references)]

    # --- sérialisation ----------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @classmethod
    def from_dict(cls, data: dict) -> "Catalog":
        objects = []
        for o in data.get("objects", []):
            o = dict(o)
            o["columns"] = [Column(**c) for c in o.get("columns", [])]
            o["keys"] = [Key(**k) for k in o.get("keys", [])]
            o["foreign_keys"] = [ForeignKey(**f) for f in o.get("foreign_keys", [])]
            o["parameters"] = [Parameter(**p) for p in o.get("parameters", [])]
            objects.append(DbObject(**o))
        data = {k: v for k, v in data.items() if k != "objects"}
        return cls(objects=objects, **data)

    @classmethod
    def load(cls, path: str | Path) -> "Catalog":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
