"""Découverte du schéma d'une base Wavesoft sur SQL Server.

Toutes les requêtes sont en **lecture seule** et ne lisent que les vues
système (``sys.*``). Droits minimum conseillés pour le compte de l'agent :
``db_datareader`` + ``VIEW DEFINITION`` (sans ce dernier, le code des vues et
procédures sort vide mais le reste du catalogue est complet).

Chaque requête commence par un commentaire ``-- q:<nom>`` : cela les rend
repérables dans les traces SQL Server et permet de les simuler dans les tests.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from .catalog import OBJECT_KINDS, Catalog, Column, DbObject, ForeignKey, Key, Parameter

_TYPES = "('" + "','".join(OBJECT_KINDS) + "')"

QUERIES: dict[str, str] = {
    "server": """-- q:server
SELECT @@SERVERNAME AS server_name, DB_NAME() AS database_name, @@VERSION AS version,
       CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS nvarchar(128)) AS collation,
       (SELECT compatibility_level FROM sys.databases WHERE name = DB_NAME()) AS compatibility_level
""",
    "objects": f"""-- q:objects
SELECT o.object_id, s.name AS schema_name, o.name, RTRIM(o.type) AS type,
       CONVERT(varchar(19), o.create_date, 126) AS created,
       CONVERT(varchar(19), o.modify_date, 126) AS modified,
       CAST(ep.value AS nvarchar(4000)) AS description
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.extended_properties ep
       ON ep.class = 1 AND ep.major_id = o.object_id AND ep.minor_id = 0 AND ep.name = 'MS_Description'
WHERE o.is_ms_shipped = 0 AND RTRIM(o.type) IN {_TYPES}
ORDER BY s.name, o.name
""",
    "columns": """-- q:columns
SELECT c.object_id, c.column_id, c.name, TYPE_NAME(c.user_type_id) AS type_name,
       c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity, c.is_computed,
       dc.definition AS default_definition, cc.definition AS computed_definition,
       c.collation_name, CAST(ep.value AS nvarchar(4000)) AS description
FROM sys.columns c
JOIN sys.objects o ON o.object_id = c.object_id
LEFT JOIN sys.default_constraints dc ON dc.object_id = c.default_object_id
LEFT JOIN sys.computed_columns cc ON cc.object_id = c.object_id AND cc.column_id = c.column_id
LEFT JOIN sys.extended_properties ep
       ON ep.class = 1 AND ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
WHERE o.is_ms_shipped = 0 AND RTRIM(o.type) IN ('U', 'V', 'IF', 'TF', 'FT')
ORDER BY c.object_id, c.column_id
""",
    "indexes": """-- q:indexes
SELECT i.object_id, i.name, i.is_primary_key, i.is_unique, i.type_desc, c.name AS column_name
FROM sys.indexes i
JOIN sys.objects o ON o.object_id = i.object_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE o.is_ms_shipped = 0 AND i.name IS NOT NULL AND ic.is_included_column = 0
ORDER BY i.object_id, i.index_id, ic.key_ordinal
""",
    "foreign_keys": """-- q:foreign_keys
SELECT fk.parent_object_id AS object_id, fk.name, pc.name AS column_name,
       rs.name AS ref_schema, ro.name AS ref_table, rc.name AS ref_column,
       fk.delete_referential_action_desc AS on_delete,
       fk.update_referential_action_desc AS on_update, fk.is_disabled
FROM sys.foreign_keys fk
JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.columns pc ON pc.object_id = fkc.parent_object_id AND pc.column_id = fkc.parent_column_id
JOIN sys.objects ro ON ro.object_id = fkc.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
JOIN sys.columns rc ON rc.object_id = fkc.referenced_object_id AND rc.column_id = fkc.referenced_column_id
ORDER BY fk.parent_object_id, fk.name, fkc.constraint_column_id
""",
    "checks": """-- q:checks
SELECT cc.parent_object_id AS object_id, cc.name, cc.definition
FROM sys.check_constraints cc
ORDER BY cc.parent_object_id, cc.name
""",
    "parameters": """-- q:parameters
SELECT p.object_id, p.parameter_id, p.name, TYPE_NAME(p.user_type_id) AS type_name,
       p.max_length, p.precision, p.scale, p.is_output, p.has_default_value
FROM sys.parameters p
JOIN sys.objects o ON o.object_id = p.object_id
WHERE o.is_ms_shipped = 0
ORDER BY p.object_id, p.parameter_id
""",
    "definitions": """-- q:definitions
SELECT m.object_id, m.definition
FROM sys.sql_modules m
JOIN sys.objects o ON o.object_id = m.object_id
WHERE o.is_ms_shipped = 0
""",
    "triggers": """-- q:triggers
SELECT t.object_id, ps.name + '.' + po.name AS parent, t.is_disabled, te.type_desc AS event
FROM sys.triggers t
JOIN sys.objects po ON po.object_id = t.parent_id
JOIN sys.schemas ps ON ps.schema_id = po.schema_id
LEFT JOIN sys.trigger_events te ON te.object_id = t.object_id
WHERE t.parent_class = 1 AND t.is_ms_shipped = 0
ORDER BY t.object_id
""",
    "row_counts": """-- q:row_counts
SELECT p.object_id, SUM(p.rows) AS row_count
FROM sys.partitions p
JOIN sys.objects o ON o.object_id = p.object_id
WHERE o.type = 'U' AND o.is_ms_shipped = 0 AND p.index_id IN (0, 1)
GROUP BY p.object_id
""",
    "synonyms": """-- q:synonyms
SELECT object_id, base_object_name FROM sys.synonyms
""",
    "dependencies": """-- q:dependencies
SELECT DISTINCT d.referencing_id AS object_id,
       COALESCE(rs.name, d.referenced_schema_name, 'dbo') + '.' + d.referenced_entity_name AS referenced
FROM sys.sql_expression_dependencies d
LEFT JOIN sys.objects ro ON ro.object_id = d.referenced_id
LEFT JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
WHERE d.referenced_server_name IS NULL AND d.referenced_database_name IS NULL
""",
}

# Requêtes dont l'échec (droits insuffisants, version ancienne) ne bloque pas
# la découverte : on continue avec un catalogue moins riche et on le signale.
OPTIONAL = {"definitions", "row_counts", "dependencies", "checks", "synonyms"}


def _fetch(conn: Any, name: str) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(QUERIES[name])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        cur.close()


def _int(v: Any) -> int | None:
    return None if v is None else int(v)


def discover(conn: Any, warnings: list[str] | None = None) -> Catalog:
    """Lit le catalogue complet de la base pointée par ``conn`` (connexion DB-API)."""
    warnings = warnings if warnings is not None else []

    def fetch(name: str) -> list[dict]:
        try:
            return _fetch(conn, name)
        except Exception as exc:  # noqa: BLE001 - le pilote lève ses propres types
            if name not in OPTIONAL:
                raise
            warnings.append(f"{name} : non lu ({exc})")
            return []

    info = (fetch("server") or [{}])[0]
    catalog = Catalog(
        server=info.get("server_name") or "",
        database=info.get("database_name") or "",
        extracted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        server_version=((info.get("version") or "").splitlines() or [None])[0],
        collation=info.get("collation"),
        compatibility_level=_int(info.get("compatibility_level")),
    )

    by_id: dict[int, DbObject] = {}
    for r in fetch("objects"):
        code = r["type"].strip()
        obj = DbObject(
            schema=r["schema_name"],
            name=r["name"],
            type_code=code,
            kind=OBJECT_KINDS.get(code, code),
            object_id=int(r["object_id"]),
            created=r.get("created"),
            modified=r.get("modified"),
            description=r.get("description"),
        )
        by_id[obj.object_id] = obj
        catalog.objects.append(obj)

    for r in fetch("columns"):
        obj = by_id.get(r["object_id"])
        if obj:
            obj.columns.append(
                Column(
                    name=r["name"],
                    position=int(r["column_id"]),
                    data_type=r["type_name"],
                    max_length=_int(r["max_length"]),
                    precision=_int(r["precision"]),
                    scale=_int(r["scale"]),
                    nullable=bool(r["is_nullable"]),
                    identity=bool(r["is_identity"]),
                    computed=bool(r["is_computed"]),
                    default=r.get("default_definition"),
                    computed_definition=r.get("computed_definition"),
                    collation=r.get("collation_name"),
                    description=r.get("description"),
                )
            )

    _group_keys(by_id, fetch("indexes"))
    _group_foreign_keys(by_id, fetch("foreign_keys"))

    for r in fetch("checks"):
        if obj := by_id.get(r["object_id"]):
            obj.checks[r["name"]] = r["definition"]

    for r in fetch("parameters"):
        obj = by_id.get(r["object_id"])
        if obj and r["name"]:  # parameter_id 0 sans nom = valeur de retour d'une fonction
            obj.parameters.append(
                Parameter(
                    name=r["name"],
                    position=int(r["parameter_id"]),
                    data_type=r["type_name"],
                    max_length=_int(r["max_length"]),
                    precision=_int(r["precision"]),
                    scale=_int(r["scale"]),
                    output=bool(r["is_output"]),
                    has_default=bool(r["has_default_value"]),
                )
            )

    definitions = fetch("definitions")
    for r in definitions:
        if obj := by_id.get(r["object_id"]):
            obj.definition = r["definition"]
    if not definitions and any(o.kind in ("view", "procedure") for o in catalog.objects):
        warnings.append("Code des vues/procédures non visible : accorder VIEW DEFINITION au compte.")

    for r in fetch("triggers"):
        if obj := by_id.get(r["object_id"]):
            obj.parent = r["parent"]
            obj.disabled = bool(r["is_disabled"])
            if r.get("event") and r["event"] not in obj.trigger_events:
                obj.trigger_events.append(r["event"])

    for r in fetch("row_counts"):
        if obj := by_id.get(r["object_id"]):
            obj.row_count = int(r["row_count"])

    for r in fetch("synonyms"):
        if obj := by_id.get(r["object_id"]):
            obj.base_object = r["base_object_name"]

    for r in fetch("dependencies"):
        obj = by_id.get(r["object_id"])
        if obj and r["referenced"] not in obj.references:
            obj.references.append(r["referenced"])

    return catalog


def _group_keys(by_id: dict[int, DbObject], rows: Iterable[dict]) -> None:
    current: dict[tuple[int, str], Key] = {}
    for r in rows:
        obj = by_id.get(r["object_id"])
        if obj is None:
            continue
        k = (r["object_id"], r["name"])
        if k not in current:
            kind = "primary_key" if r["is_primary_key"] else "unique" if r["is_unique"] else "index"
            current[k] = Key(r["name"], kind, [], clustered=r["type_desc"] == "CLUSTERED")
            obj.keys.append(current[k])
        current[k].columns.append(r["column_name"])


def _group_foreign_keys(by_id: dict[int, DbObject], rows: Iterable[dict]) -> None:
    current: dict[tuple[int, str], ForeignKey] = {}
    for r in rows:
        obj = by_id.get(r["object_id"])
        if obj is None:
            continue
        k = (r["object_id"], r["name"])
        if k not in current:
            current[k] = ForeignKey(
                name=r["name"],
                columns=[],
                ref_schema=r["ref_schema"],
                ref_table=r["ref_table"],
                ref_columns=[],
                on_delete=r["on_delete"],
                on_update=r["on_update"],
                disabled=bool(r["is_disabled"]),
            )
            obj.foreign_keys.append(current[k])
        current[k].columns.append(r["column_name"])
        current[k].ref_columns.append(r["ref_column"])


def group_by_prefix(catalog: Catalog, kinds: tuple[str, ...] = ("table", "view")) -> dict[str, list[str]]:
    """Regroupe les objets par préfixe de nom (avant le premier ``_``).

    Utile sur une base ERP pour voir d'un coup d'œil les domaines fonctionnels
    (articles, tiers, pièces commerciales…) quand les tables suivent une
    convention de préfixes.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for o in catalog.objects:
        if o.kind in kinds:
            prefix = o.name.split("_", 1)[0].upper() if "_" in o.name else "(sans préfixe)"
            groups[prefix].append(o.full_name)
    return dict(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])))
