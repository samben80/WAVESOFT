"""Ligne de commande de l'agent intégrateur Wavesoft.

Exemples ::

    python -m wavesoft_agent discover --out sortie/            # lit la base, écrit catalog.json/.md/.xlsx
    python -m wavesoft_agent summary sortie/catalog.json       # comptes par type et domaines
    python -m wavesoft_agent search sortie/catalog.json ARTICLE  # cherche un nom d'objet ou de colonne
    python -m wavesoft_agent describe sortie/catalog.json dbo.ARTICLES
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .catalog import Catalog
from .export import KIND_LABELS, _object_md, to_excel, to_markdown


def cmd_discover(args) -> int:
    from .connection import ConnectionSettings, connect
    from .discovery import discover

    settings = ConnectionSettings.from_env(
        env_file=args.env, server=args.server, database=args.database, user=args.user, driver=args.driver
    )
    warnings: list[str] = []
    with connect(settings) as conn:
        catalog = discover(conn, warnings)
    out = Path(args.out)
    catalog.save(out / "catalog.json")
    (out / "catalog.md").write_text(to_markdown(catalog), encoding="utf-8")
    to_excel(catalog, out / "catalog.xlsx")
    print(f"{catalog.database} : " + ", ".join(f"{n} {KIND_LABELS.get(k, k).lower()}" for k, n in catalog.counts().items()))
    print(f"Écrit dans {out.resolve()} (catalog.json, catalog.md, catalog.xlsx)")
    for w in warnings:
        print(f"Attention : {w}", file=sys.stderr)
    return 0


def cmd_summary(args) -> int:
    from .discovery import group_by_prefix

    catalog = Catalog.load(args.catalog)
    print(f"Base {catalog.database} sur {catalog.server}, extraite le {catalog.extracted_at}")
    for kind, n in catalog.counts().items():
        print(f"  {KIND_LABELS.get(kind, kind):<22}{n:>6}")
    print("\nDomaines (préfixes de tables et vues) :")
    for prefix, names in list(group_by_prefix(catalog).items())[: args.top]:
        print(f"  {prefix:<22}{len(names):>6}")
    return 0


def cmd_search(args) -> int:
    catalog = Catalog.load(args.catalog)
    rx = re.compile(args.pattern, re.IGNORECASE)
    found = 0
    for o in catalog.objects:
        if rx.search(o.name):
            print(f"{o.kind:<10} {o.full_name}")
            found += 1
        for c in o.columns:
            if rx.search(c.name):
                print(f"{'colonne':<10} {o.full_name}.{c.name} ({c.sql_type})")
                found += 1
        if args.code and o.definition and rx.search(o.definition):
            print(f"{'code':<10} {o.full_name} ({o.kind})")
            found += 1
    if not found:
        print("Aucun résultat.")
    return 0 if found else 1


def cmd_describe(args) -> int:
    catalog = Catalog.load(args.catalog)
    obj = catalog.get(args.name)
    if obj is None:
        print(f"Objet introuvable (ou nom ambigu) : {args.name}", file=sys.stderr)
        return 1
    print("\n".join(_object_md(obj)))
    users = catalog.referenced_by(obj.full_name)
    if users:
        print("Utilisé par : " + ", ".join(f"{u.full_name} ({u.kind})" for u in users))
    if args.code and obj.definition:
        print("\n" + obj.definition)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wavesoft_agent", description="Agent intégrateur Wavesoft")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="lire tous les objets de la base et écrire le catalogue")
    d.add_argument("--out", default="sortie", help="dossier de sortie (défaut : sortie)")
    d.add_argument("--env", default=".env", help="fichier de paramètres de connexion (défaut : .env)")
    d.add_argument("--server")
    d.add_argument("--database")
    d.add_argument("--user", help="compte SQL (sinon authentification Windows)")
    d.add_argument("--driver")
    d.set_defaults(func=cmd_discover)

    s = sub.add_parser("summary", help="résumé d'un catalogue")
    s.add_argument("catalog")
    s.add_argument("--top", type=int, default=30)
    s.set_defaults(func=cmd_summary)

    f = sub.add_parser("search", help="chercher un objet ou une colonne (expression régulière)")
    f.add_argument("catalog")
    f.add_argument("pattern")
    f.add_argument("--code", action="store_true", help="chercher aussi dans le code SQL")
    f.set_defaults(func=cmd_search)

    x = sub.add_parser("describe", help="détail d'un objet")
    x.add_argument("catalog")
    x.add_argument("name")
    x.add_argument("--code", action="store_true", help="afficher le code SQL")
    x.set_defaults(func=cmd_describe)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
