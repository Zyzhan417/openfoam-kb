"""CLI `kind` field (FR-013b): every hit carries it, and it is the chunk's real kind.

The point of the field is that a caller can tell documentation from code without guessing
from `doc` being null. So these tests do not merely check that the key exists: one proves a
Description hit is `filedoc`, the other pins every kind to the row stored in the index.
"""

import json

import pytest

from foamkb import cli, index

RAS_PATH = "src/turbulence/RASModel.H"
PLAIN_PATH = "src/turbulence/blendWall.C"
# OpenFOAM's shape: a rule, `Description`, prose, the closing rule of the same block comment.
BANNER = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
          "Description\n"
          "    Specialisation for RAS turbulence models. The base class holds the\n"
          "    fields every derived model shares and selects nothing by itself.\n"
          "\\*---------------------------------------------------------------------------*/")
# After the class: a code line plus prose naming the same identifier, so one query returns
# a class hit and a text hit from the file that owns the doc (the multi-kind witness).
NOTES = ("// Notes for RASModel: the blend keeps laminar and turbulent terms continuous\n"
         "// at the wall, so no switching criterion is needed in the derived models.\n") * 6
RAS_FILE = (BANNER + '\n#include "RASModel.H"\n\n'
            "class RASModel\n{\npublic:\n    virtual ~RASModel();\n};\n\n"
            "scalar RASModelBlendScale = 1.0;\n" + NOTES)
# No Description here at all: a plain unit whose only kind can be `function`.
PLAIN_FILE = ("// Plain turbulence helper with no Description section at all.\n"
              "\n"
              "Foam::scalar Foam::turbulence::blendWall()\n{\n"
              "    return 1.0;\n}\n")
# One query per witnessed kind: filedoc, class + text, function.
QUERIES = ("Specialisation for RAS", "RASModel", "blendWall")


@pytest.fixture
def ras_index(corpus, tmp_path, capsys):
    """A built index over the Description file and the plain code file, plus its path."""
    root = corpus({RAS_PATH: RAS_FILE, PLAIN_PATH: PLAIN_FILE})
    index_path = str(tmp_path / "kind.sqlite")
    assert cli.main(["build", "--corpus", str(root), "--index", index_path,
                     "--include", "**/*"]) == 0
    capsys.readouterr()
    return index.open_reader(index_path), index_path


def _hits(capsys, index_path, query):
    """The stdout hit list for one CLI search, JSON-parsed, with a clean-exit guard."""
    code = cli.main(["search", "--index", index_path, "--query", query, "--top-k", "5"])
    out, err = capsys.readouterr()
    assert code == 0 and err == "", f"search {query!r} must succeed, stderr={err!r}"
    return json.loads(out)["hits"]


def test_kind_is_a_filedoc_for_a_description_hit(ras_index, capsys):
    """T020a/FR-013b: `Specialisation for RAS` lands on the Description, and kind says so."""
    _, index_path = ras_index
    hits = _hits(capsys, index_path, "Specialisation for RAS")
    assert hits, "the fixture's Description must be searchable"
    top = hits[0]

    assert top["kind"] == "filedoc", f"a Description hit must say filedoc, got {top['kind']!r}"
    assert top["path"] == RAS_PATH and top["symbol"] is None
    assert "Specialisation for RAS" in top["text"], "the hit text is the Description prose"
    # The same index answers a symbol query with a code kind: kind is not one constant.
    code = _hits(capsys, index_path, "RASModel")[0]
    assert code["kind"] == "class" and code["doc"] is not None
    assert code["kind"] != top["kind"], "code and documentation must not share a kind"


def test_kind_matches_the_stored_chunk_for_every_kind(ras_index, capsys):
    """T020a/FR-013b: each hit's kind is the row's kind, across every kind the fixture has."""
    reader, index_path = ras_index
    seen = {}
    for query in QUERIES:
        for hit in _hits(capsys, index_path, query):
            chunk_id = reader.chunk_id_at(hit["path"], hit["start_line"])
            assert chunk_id is not None, f"no stored chunk at {hit['path']}::{hit['start_line']}"
            stored = reader.get_chunk(chunk_id)["kind"]
            assert hit["kind"] == stored, (
                f"hit kind {hit['kind']!r} != stored kind {stored!r} at "
                f"{hit['path']}::{hit['start_line']} (query {query!r})")
            seen.setdefault(hit["kind"], set()).add(hit["path"])

    assert {"filedoc", "class", "text", "function"} <= set(seen), (
        f"the fixture must witness four kinds, saw {sorted(seen)}")
