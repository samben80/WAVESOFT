"""Tests de la préparation des tâches Automate (WSAUTOMATE)."""

import json

import pytest

from wavesoft_agent.automate import decouper, entite_pour, lire_etat, script_sql
from wavesoft_agent.cli import main
from wavesoft_agent.formats import FormatSpec

from .test_wavesoft_formats import SPEC

FICHIER = ('E;CDECLI;05/10/2026;0002;\r\nNO;"Livrer\r\nle matin";\r\nLA;ART1;1;\r\n'
           "E;CDECLI;06/10/2026;0003;\r\nLA;L'ART;2;\r\nCLO;CC1;\r\n")


@pytest.fixture
def spec():
    return FormatSpec.from_dict(SPEC)


@pytest.fixture
def fichier(tmp_path):
    p = tmp_path / "import.txt"
    p.write_bytes(FICHIER.encode("cp1252"))
    return p


def test_one_task_per_document_and_command(spec, fichier):
    taches, sep = decouper(fichier, spec)
    assert sep == ";"
    assert [t.premiere_ligne for t in taches] == [1, 5, 7]
    assert taches[0].contenu == 'E;CDECLI;05/10/2026;0002;\r\nNO;"Livrer\r\nle matin";\r\nLA;ART1;1;\r\n'
    assert taches[2].contenu == "CLO;CC1;\r\n"


def test_format_without_document_header_stays_whole(tmp_path):
    spec = FormatSpec.from_dict({"code": "L", "titre": "L", "documents": {"debut": None}, "enregistrements": {
        "*": {"niveau": "ligne", "champs": [{"n": 1, "nom": "article"}, {"n": 2, "nom": "quantite"}]}}})
    p = tmp_path / "l.txt"
    p.write_bytes(b"A;1\r\nB;2\r\n")
    taches, _ = decouper(p, spec)
    assert len(taches) == 1 and taches[0].contenu == "A;1\r\nB;2\r\n"


def test_script_sql(spec, fichier):
    taches, sep = decouper(fichier, spec)
    sql = script_sql(taches, 99, sep, source="import.txt")
    assert sql.count("EXEC @id = dbo.ws_sp_add_tache_automate") == 3
    assert "@TRSENTITE = 99," in sql and "@TRSSEPARATEUR = 'V'," in sql and "@TRSISTCP = 'N';" in sql
    assert "@TRSFILE = N'E;CDECLI;06/10/2026;0003;\r\nLA;L''ART;2;\r\n'," in sql  # apostrophe doublée
    assert sql.index("BEGIN TRANSACTION") < sql.index("EXEC") < sql.index("COMMIT TRANSACTION")
    assert "@TRSISTCP = 'O';" in script_sql(taches, 99, "\t", tcp=True)
    with pytest.raises(ValueError):
        script_sql(taches, 99, "|")


def test_entite(spec):
    assert entite_pour(FormatSpec.from_dict({**SPEC, "code": "FTC002"})) == 99
    assert entite_pour(spec, 4) == 4
    with pytest.raises(ValueError, match="--entite"):
        entite_pour(spec)
    with pytest.raises(ValueError, match="inconnue"):
        entite_pour(spec, 3)


class Cursor:
    description = [("TRSID",), ("TRSETAT",)]

    def execute(self, sql, *params):
        self.sql, self.params = sql, params

    def fetchall(self):
        return [(7, "E")]


class Conn:
    def __init__(self):
        self.cur = Cursor()

    def cursor(self):
        return self.cur


def test_lire_etat_is_a_select():
    conn = Conn()
    assert lire_etat(conn, [7, 8]) == [{"TRSID": 7, "TRSETAT": "E"}]
    assert conn.cur.sql.lstrip().upper().startswith("SELECT") and conn.cur.params == (7, 8)
    lire_etat(conn, erreurs=True)
    assert "TRSETAT = 'E'" in conn.cur.sql


def test_cli_automate(tmp_path, fichier, capsys):
    specs = tmp_path / "formats"
    specs.mkdir()
    (specs / "TEST.json").write_text(json.dumps(SPEC), encoding="utf-8")
    out = tmp_path / "taches.sql"
    assert main(["automate", "TEST", str(fichier), "--specs", str(specs)]) == 2  # entité inconnue pour TEST
    assert main(["automate", "TEST", str(fichier), "--entite", "99", "--out", str(out), "--specs", str(specs)]) == 0
    assert "3 tâche(s)" in capsys.readouterr().out
    assert out.read_text(encoding="utf-8-sig").count("ws_sp_add_tache_automate") == 3

    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"LA;ART;1\r\n")
    assert main(["automate", "TEST", str(bad), "--entite", "99", "--out", str(tmp_path / "b.sql"),
                 "--specs", str(specs)]) == 1
    assert not (tmp_path / "b.sql").exists()
