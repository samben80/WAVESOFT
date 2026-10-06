"""Lecture, contrôle et écriture des fichiers d'import Wavesoft."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .formats import FormatSpec, RecordSpec, check_value, format_value


@dataclass
class Issue:
    line: int  # n° de ligne dans le fichier (1 = première), 0 si non applicable
    record: str
    column: int | None
    message: str
    level: str = "erreur"  # erreur | attention

    def __str__(self) -> str:
        where = f"ligne {self.line} " if self.line else ""
        col = f", colonne {self.column}" if self.column else ""
        return f"{self.level.upper()} {where}({self.record}{col}) : {self.message}"


@dataclass
class Record:
    code: str
    values: list[str]
    line: int = 0


# --- lecture -------------------------------------------------------------------

def split_records(text: str, sep: str) -> list[Record]:
    """Découpe le texte en enregistrements ; un champ entre guillemets peut contenir
    des sauts de ligne et le séparateur."""
    records: list[Record] = []
    values: list[str] = []
    cur: list[str] = []
    in_quotes = False
    line = start = 1
    i = 0
    while i < len(text):
        ch = text[i]
        if in_quotes:
            if ch == '"':
                in_quotes = False
            else:
                if ch == "\n":
                    line += 1
                cur.append(ch)
        elif ch == '"' and not "".join(cur).strip():
            in_quotes, cur = True, []
        elif ch == sep:
            values.append("".join(cur))
            cur = []
        elif ch in "\r\n":
            if ch == "\r" and text[i + 1 : i + 2] == "\n":
                i += 1
            values.append("".join(cur))
            if any(v.strip() for v in values):
                records.append(Record(values[0].strip(), values, start))
            values, cur = [], []
            line += 1
            start = line
        else:
            cur.append(ch)
        i += 1
    values.append("".join(cur))
    if any(v.strip() for v in values):
        records.append(Record(values[0].strip(), values, start))
    for r in records:
        if len(r.values) > 1 and r.values[-1] == "":  # séparateur final toléré
            r.values.pop()
    return records


def detect_separator(text: str, spec: FormatSpec) -> str:
    first = next((l for l in text.splitlines() if l.strip()), "")
    counts = {s: first.count(s) for s in spec.separateurs}
    return max(counts, key=counts.get) if any(counts.values()) else spec.separateurs[0]


def read_file(path: str | Path, spec: FormatSpec, sep: str | None = None) -> tuple[list[Record], str]:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode(spec.encodage)
    sep = sep or detect_separator(text, spec)
    return split_records(text, sep), sep


# --- contrôle ------------------------------------------------------------------

def _expected(rec: RecordSpec, values: list[str]):
    """Associe chaque valeur à sa colonne (gère les paires répétées de ED/LD)."""
    by_n = {f.n: f for f in rec.champs}
    for idx, value in enumerate(values, start=1):
        n = idx
        if rec.repetition_depuis and idx >= rec.repetition_depuis:
            width = len(rec.champs) - rec.repetition_depuis + 1
            n = rec.repetition_depuis + (idx - rec.repetition_depuis) % width
        yield idx, by_n.get(n), value


def check_records(records: list[Record], spec: FormatSpec) -> list[Issue]:
    issues: list[Issue] = []
    in_doc = False
    previous: str | None = None
    seen_in_doc: set[str] = set()
    for r in records:
        if spec.sans_code:
            r.code = "*"
        rec = spec.enregistrements.get(r.code)
        if rec is None:
            issues.append(Issue(r.line, r.code, 1, f"type d'enregistrement inconnu pour {spec.code}"))
            previous = None
            continue
        if r.code == spec.debut_document:
            in_doc, seen_in_doc = True, set()
        elif rec.niveau != "commande" and spec.debut_document and not in_doc:
            issues.append(Issue(r.line, r.code, None, f"doit suivre une ligne {spec.debut_document}"))
        if rec.niveau == "commande":
            in_doc = False
        if rec.suit and previous not in rec.suit and previous != r.code:
            issues.append(Issue(r.line, r.code, None, f"doit suivre une ligne {' ou '.join(rec.suit)}"))
        if rec.unique and r.code in seen_in_doc:
            issues.append(Issue(r.line, r.code, None, "une seule fois par pièce"))
        seen_in_doc.add(r.code)

        given = set()
        for idx, fspec, value in _expected(rec, r.values):
            if fspec is None:
                if value.strip():
                    issues.append(Issue(r.line, r.code, idx, f"colonne en trop (le format en compte {len(rec.champs)})"))
                continue
            given.add(fspec.n)
            text = value if fspec.type == "texte_long" else value.strip()
            msg = check_value(fspec, text, spec.format_date)
            if msg:
                issues.append(Issue(r.line, r.code, idx, f"{fspec.libelle or fspec.nom} : {msg}"))
            elif fspec.type == "vide" and text:
                issues.append(Issue(r.line, r.code, idx, "colonne réservée (VIDE) : valeur ignorée par Wavesoft", "attention"))
            elif fspec.export_seulement and text:
                issues.append(Issue(r.line, r.code, idx, f"{fspec.libelle} : ignoré en import", "attention"))
        for f in rec.champs:
            if f.obligatoire and f.n not in given:
                issues.append(Issue(r.line, r.code, f.n, f"{f.libelle or f.nom} : obligatoire (colonne absente)"))
        previous = r.code
    return issues


def check_file(path: str | Path, spec: FormatSpec, sep: str | None = None) -> list[Issue]:
    records, sep = read_file(path, spec, sep)
    issues = check_records(records, spec)
    text = Path(path).read_bytes()
    if "”".encode("utf-8") in text or "“".encode("utf-8") in text or b"\x94" in text or b"\x93" in text:
        issues.append(Issue(0, "-", None, "guillemets typographiques “ ” trouvés : utiliser des guillemets droits \"", "attention"))
    return issues


# --- écriture ------------------------------------------------------------------

@dataclass
class Document:
    """Une pièce (ou une commande isolée) : liste ordonnée d'enregistrements."""

    records: list[tuple[str, dict]] = field(default_factory=list)

    def add(self, code: str, **values) -> "Document":
        self.records.append((code, values))
        return self


def build_record(spec: FormatSpec, code: str, values: dict) -> Record:
    rec = spec.enregistrements[code]
    unknown = set(values) - {f.nom for f in rec.champs} - {"paires"}
    if unknown:
        raise KeyError(f"{code} : colonnes inconnues {sorted(unknown)}")
    out: list[str] = []
    fixed = [f for f in rec.champs if not rec.repetition_depuis or f.n < rec.repetition_depuis]
    for f in fixed:
        if f.n == 1 and code != "*":
            out.append(code)
        else:
            out.append(format_value(f, values.get(f.nom), spec.format_date))
    if rec.repetition_depuis:
        rep = [f for f in rec.champs if f.n >= rec.repetition_depuis]
        pairs = values.get("paires") or [tuple(values.get(f.nom) for f in rep)]
        for pair in pairs:
            out += [format_value(f, v, spec.format_date) for f, v in zip(rep, pair)]
    while len(out) > 1 and out[-1] == "":
        out.pop()
    return Record(code, out)


def _encode(text: str, encoding: str) -> tuple[bytes, list[Issue]]:
    try:
        return text.encode(encoding), []
    except UnicodeEncodeError as exc:
        bad = text[exc.start:exc.end]
        line = text.count("\n", 0, exc.start) + 1
        return b"", [Issue(line, "-", None, f"caractère {bad!r} impossible à écrire en {encoding}")]


def _quote(value: str, sep: str, long_text: bool) -> str:
    if long_text or "\n" in value or sep in value:
        return f'"{value}"'
    return value


def write_file(path: str | Path, spec: FormatSpec, documents: list[Document], sep: str = ";",
               check: bool = True, write: bool = True) -> list[Issue]:
    """Écrit le fichier d'import. Renvoie les anomalies ; n'écrit rien s'il y a des erreurs."""
    records = [build_record(spec, code, values) for doc in documents for code, values in doc.records]
    for i, r in enumerate(records, start=1):
        r.line = i
    issues = check_records(records, spec) if check else []
    for r in records:
        rec = spec.enregistrements[r.code]
        for idx, fspec, value in _expected(rec, r.values):
            if fspec and '"' in value:
                issues.append(Issue(r.line, r.code, idx, f"{fspec.libelle} : guillemet interdit dans le texte"))
            elif fspec and fspec.type != "texte_long" and sep in value:
                issues.append(Issue(r.line, r.code, idx, f"{fspec.libelle} : contient le séparateur {sep!r}"))
    if not write or any(i.level == "erreur" for i in issues):
        return issues
    lines = []
    for r in records:
        rec = spec.enregistrements[r.code]
        cells = [
            _quote(v, sep, bool(f and f.type == "texte_long" and v))
            for _, f, v in _expected(rec, r.values)
        ]
        lines.append(sep.join(cells) + sep)
    data, problems = _encode("\r\n".join(lines) + "\r\n", spec.encodage)
    if problems:
        return issues + problems
    Path(path).write_bytes(data)
    return issues
