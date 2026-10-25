"""Reader.file_doc: which files carry a doc and the licence rejection (FR-012, FR-014)."""

import sqlite3

import pytest

from foamkb import index
from foamkb.config import build_config

NUT = "Foam::scalar Foam::nutUSpaldingWallFunction::calcNut()\n{\n    return 1.0;\n}\n"


DOC_BANNER = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
              "Description\n"
              + 8 * "    Wall function for nut; blends laminar and turbulent behaviour.\n"
              + "\\*---------------------------------------------------------------------------*/\n")
# A licence head in the same block comment: OpenFOAM's shape, and the reason the doc is
# the Description-onward tail rather than the whole chunk (C14). The block runs past the
# 400-char glue floor, so it is a chunk of its own instead of gluing onto the #include.
LICENSED_BANNER = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    "License\n"
    "    This file is part of OpenFOAM.\n"
    "    Distributed under the GNU General Public License, version 3 or later.\n"
    "Class\n"
    "    Foam::nutWallFunction\n"
    "Description\n"
    "    Wall function for nut, blending laminar and turbulent behaviour near walls.\n"
    "    The blending is continuous in y+ and needs no switching between regimes.\n"
    "    It is provided WITHOUT ANY WARRANTY of any kind, express or implied.\n"
    "\\*---------------------------------------------------------------------------*/\n")
DOC_FILES = {
    "src/wall/nutWallFunction.H": DOC_BANNER
    + '\n#include "nutWallFunction.H"\n\nclass nutWallFunction\n{\n};\n',
    "src/licensed/nutWallFunction.H": LICENSED_BANNER
    + '\n#include "nutWallFunction.H"\n\nclass nutWallFunction\n{\n};\n',
    "src/plain/plain.C": NUT,
}


@pytest.fixture
def doc_reader(tmp_path, corpus):
    """A reader over a corpus where exactly one file carries a filedoc block."""
    root = corpus(DOC_FILES)
    cfg = build_config(root, str(tmp_path / "docs.sqlite"), ("**/*",))
    index.build(cfg)
    return index.open_reader(cfg.index_path)


def test_reader_file_doc(doc_reader):
    """FR-014: `search` gets a doc only through this method, never through its own SQL.

    C14/T017a supersedes the chunk-shaped return: the doc is the Description-onward tail
    of the filedoc chunk, so the dict is {start_line, end_line, text} and starts at the
    Description line — not at the chunk head (which holds the banner rule here).
    """
    doc = doc_reader.file_doc("src/wall/nutWallFunction.H")

    assert doc is not None
    assert set(doc) == {"start_line", "end_line", "text"}
    assert doc["start_line"] == 2, "the doc starts on the Description line (line 2 here)"
    lines = DOC_FILES["src/wall/nutWallFunction.H"].split("\n")
    assert doc["text"] == "\n".join(lines[doc["start_line"] - 1:doc["end_line"]])
    assert doc["text"].split("\n")[0] == "Description"


def test_file_doc_starts_at_description_line(doc_reader):
    """C14/T017a: the boundary is the Description line, not the whole filedoc chunk.

    Under the whole-chunk rule this doc would start at line 1 and carry the banner rule
    `/*----... C++ ...----*\\` — the licence-adjacent noise the doc must never contain.
    """
    doc = doc_reader.file_doc("src/wall/nutWallFunction.H")

    assert doc is not None
    assert doc["start_line"] == 2, f"doc must start at the Description line, got {doc['start_line']}"
    assert "C++" not in doc["text"], "the banner rule above Description must not travel with the doc"
    # C15: the boundary is the closing `*/` of the banner. Here the banner IS the chunk, so
    # the two rules coincide (both end at line 11) — the glued-tail shape is covered below.
    assert doc["end_line"] == 11, "the doc ends at the banner's closing '*/' (C15)"
    assert doc["text"].split("\n")[-1].rstrip().endswith("*/"), "line 11 closes the block comment"
    lines = DOC_FILES["src/wall/nutWallFunction.H"].split("\n")
    assert doc["text"] == "\n".join(lines[1:11]), "the text must be the verbatim source slice"


def test_file_doc_rejects_licensed_slice(tmp_path, corpus):
    """FR-012 / C14: a licence marker INSIDE the Description-onward slice still rejects.

    Two layers, both asserted: the build must not mark such a file at all, and the reader
    must still refuse the slice if an index written by the pre-C14 rule hands it one.
    """
    root = corpus(DOC_FILES)
    cfg = build_config(root, str(tmp_path / "licensed.sqlite"), ("**/*",))
    index.build(cfg)
    path = "src/licensed/nutWallFunction.H"
    with sqlite3.connect(cfg.index_path) as conn:
        marked = conn.execute("SELECT COUNT(*) FROM chunks WHERE path = ? AND kind = 'filedoc'",
                              (path,)).fetchone()[0]
        assert marked == 0, "the build must not mark a licensed Description slice"
        # Simulate the pre-C14 index: same chunk, marked as a field doc after the fact.
        chunk_id = conn.execute("SELECT id FROM chunks WHERE path = ? AND text LIKE '%Description%'"
                                " ORDER BY start_line LIMIT 1", (path,)).fetchone()[0]
        conn.execute("UPDATE chunks SET kind = 'filedoc' WHERE id = ?", (chunk_id,))

    doc = index.open_reader(cfg.index_path).file_doc(path)
    assert doc is None, "a stored filedoc whose slice is licensed must never be returned"


def test_reader_file_doc_absent(doc_reader):
    """No filedoc on this file, and no such file at all: both are None, never a guess."""
    assert doc_reader.file_doc("src/plain/plain.C") is None
    assert doc_reader.file_doc("src/ghost/missing.H") is None
