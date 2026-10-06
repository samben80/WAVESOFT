"""Ligne de commande de l'agent intégrateur Wavesoft.

Exemples ::

    python -m wavesoft_agent discover --out sortie/            # lit la base, écrit catalog.json/.md/.xlsx
    python -m wavesoft_agent summary sortie/catalog.json       # comptes par type et domaines
    python -m wavesoft_agent search sortie/catalog.json ARTICLE  # cherche un nom d'objet ou de colonne
    python -m wavesoft_agent describe sortie/catalog.json dbo.ARTICLES
    python -m wavesoft_agent formats                            # formats d'import connus
    python -m wavesoft_agent template FTC002                    # modèle Excel à remplir
    python -m wavesoft_agent build FTC002 modele_FTC002.xlsx    # fichier d'import contrôlé
    python -m wavesoft_agent check-file FTC002 import.txt       # contrôle d'un fichier existant
    python -m wavesoft_agent automate FTC002 import.txt         # script SQL des tâches Automate, à relire
    python -m wavesoft_agent automate-etat --erreurs            # état des tâches dans WSAUTOMATE (lecture)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .catalog import Catalog
from .export import KIND_LABELS, _object_md, to_excel, to_markdown


def _connect(args):
    from .connection import ConnectionSettings, connect

    return connect(ConnectionSettings.from_env(
        env_file=args.env, server=args.server, database=args.database, user=args.user, driver=args.driver
    ))


def cmd_discover(args) -> int:
    from .discovery import discover

    warnings: list[str] = []
    with _connect(args) as conn:
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
    specific = [o for o in catalog.objects if o.specific]
    print(f"  {'dont spécifiques EXT_':<22}{len(specific):>6}")
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


def _specs(args):
    from .formats import load_specs

    folder = Path(args.specs or os.environ.get("WAVESOFT_SPECS") or Path(__file__).parent / "specs" / "formats")
    specs = load_specs(folder) if folder.is_dir() else {}
    if not specs:
        raise ValueError(f"Aucune définition de format dans {folder} (option --specs ou WAVESOFT_SPECS).")
    return specs


def _spec(args):
    specs = _specs(args)
    code = args.format.upper()
    if code not in specs:
        raise ValueError(f"Format {args.format} inconnu. Disponibles : {', '.join(specs)}")
    return specs[code]


def _report(issues) -> int:
    for i in issues:
        print(i)
    errors = sum(i.level == "erreur" for i in issues)
    warnings = len(issues) - errors
    print(f"{errors} erreur(s), {warnings} avertissement(s).")
    return 1 if errors else 0


def cmd_formats(args) -> int:
    for code, spec in _specs(args).items():
        recs = ", ".join(c for c in spec.enregistrements if c != "*")
        print(f"{code:<24} {spec.titre}" + (f"  [{recs}]" if recs else ""))
    return 0


def cmd_check_file(args) -> int:
    from .importfile import check_file

    return _report(check_file(args.fichier, _spec(args), _sep(args.sep)))


def cmd_template(args) -> int:
    from .excel_io import write_template

    spec = _spec(args)
    out = args.out or f"modele_{spec.code}.xlsx"
    records = [r.strip().upper() for r in args.records.split(",")] if args.records else None
    print(f"Modèle écrit : {write_template(spec, out, records)}")
    return 0


def cmd_build(args) -> int:
    from .excel_io import build_file

    spec = _spec(args)
    out = args.out or Path(args.classeur).with_suffix(".txt").name
    code = _report(build_file(spec, args.classeur, out, _sep(args.sep) or ";"))
    if code == 0:
        print(f"Fichier d'import écrit : {Path(out).resolve()} ({spec.encodage})")
    else:
        print("Aucun fichier écrit : corriger le classeur puis relancer.")
    return code


def cmd_automate(args) -> int:
    from .automate import ENTITES, decouper, entite_pour, script_sql
    from .importfile import check_file

    spec = _spec(args)
    entite = entite_pour(spec, args.entite)
    sep = _sep(args.sep)
    if _report(check_file(args.fichier, spec, sep)):
        print("Aucun script écrit : corriger le fichier puis relancer.")
        return 1
    taches, sep = decouper(args.fichier, spec, sep)
    if not taches:
        raise ValueError(f"{args.fichier} ne contient aucun enregistrement")
    out = Path(args.out or Path(args.fichier).with_suffix(".automate.sql").name)
    sql = script_sql(taches, entite, sep, args.profil, args.tcp, Path(args.fichier).name)
    out.write_bytes(sql.encode("utf-8-sig"))
    print(f"Script écrit : {out.resolve()}")
    print(f"{len(taches)} tâche(s) pour l'entité {entite} ({ENTITES[entite]}). "
          "Rien n'a été envoyé : relire le script puis l'exécuter sur le dossier Wavesoft.")
    return 0


def cmd_automate_etat(args) -> int:
    from .automate import ETATS, lire_etat

    with _connect(args) as conn:
        rows = lire_etat(conn, args.ids, args.erreurs)
    for r in rows:
        etat = ETATS.get(r["TRSETAT"], r["TRSETAT"])
        objet = r["TRSCODEOBJET"] or r["TRSIDOBJET"] or ""
        print(f"{r['TRSID']:>8}  entité {r['TRSENTITE']:<4} {etat:<10} {objet}  {r['TRSERREUR'] or ''}".rstrip())
    if not rows:
        print("Aucune tâche.")
    return 1 if any(r["TRSETAT"] == "E" for r in rows) else 0


def _sep(value):
    return {"tab": "\t", "tabulation": "\t", "pv": ";", ";": ";"}.get(value, value) if value else None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wavesoft_agent", description="Agent intégrateur Wavesoft")
    sub = p.add_subparsers(dest="command", required=True)

    def connection_args(parser):
        parser.add_argument("--env", default=".env", help="fichier de paramètres de connexion (défaut : .env)")
        parser.add_argument("--server")
        parser.add_argument("--database")
        parser.add_argument("--user", help="compte SQL (sinon authentification Windows)")
        parser.add_argument("--driver")

    d = sub.add_parser("discover", help="lire tous les objets de la base et écrire le catalogue")
    d.add_argument("--out", default="sortie", help="dossier de sortie (défaut : sortie)")
    connection_args(d)
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
    specs_help = "dossier des définitions de formats (défaut : WAVESOFT_SPECS ou wavesoft_agent/specs/formats)"
    fm = sub.add_parser("formats", help="lister les formats d'import disponibles")
    fm.add_argument("--specs", help=specs_help)
    fm.set_defaults(func=cmd_formats)

    cf = sub.add_parser("check-file", help="contrôler un fichier d'import existant")
    cf.add_argument("format", help="code du format, ex. FTC002")
    cf.add_argument("fichier")
    cf.add_argument("--sep", help="séparateur : ; ou tab (détecté sinon)")
    cf.add_argument("--specs", help=specs_help)
    cf.set_defaults(func=cmd_check_file)

    tp = sub.add_parser("template", help="créer le modèle Excel à remplir pour un format")
    tp.add_argument("format")
    tp.add_argument("--out")
    tp.add_argument("--records", help="enregistrements à inclure, ex. E,AF,AL,LA")
    tp.add_argument("--specs", help=specs_help)
    tp.set_defaults(func=cmd_template)

    bd = sub.add_parser("build", help="générer le fichier d'import à partir du modèle Excel rempli")
    bd.add_argument("format")
    bd.add_argument("classeur")
    bd.add_argument("--out")
    bd.add_argument("--sep", help="séparateur : ; (défaut) ou tab")
    bd.add_argument("--specs", help=specs_help)
    bd.set_defaults(func=cmd_build)

    au = sub.add_parser("automate", help="script SQL qui confie un fichier d'import à l'Automate (à relire, rien n'est envoyé)")
    au.add_argument("format")
    au.add_argument("fichier")
    au.add_argument("--entite", type=int, help="TRSENTITE (déduit du format sinon, ex. 99 pour FTC002)")
    au.add_argument("--profil", default="", help="code du profil d'I/E (inutile pour les pièces)")
    au.add_argument("--tcp", action="store_true", help="réserver les tâches à un automate en mode serveur TCP")
    au.add_argument("--out", help="script SQL (défaut : <fichier>.automate.sql)")
    au.add_argument("--sep", help="séparateur : ; ou tab (détecté sinon)")
    au.add_argument("--specs", help=specs_help)
    au.set_defaults(func=cmd_automate)

    ae = sub.add_parser("automate-etat", help="état des tâches de l'Automate (lecture seule)")
    ae.add_argument("--ids", type=int, nargs="+", help="TRSID à suivre (défaut : tâches non terminées)")
    ae.add_argument("--erreurs", action="store_true", help="seulement les tâches en erreur")
    connection_args(ae)
    ae.set_defaults(func=cmd_automate_etat)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
