"""search doc: a symbol hit carries its file's Description, whole and verbatim (FR-013, C19)."""

import sqlite3

import pytest

from foamkb import index
from foamkb.config import build_config
from foamkb.search import search

PATH = "src/wall/nutWallFunction.H"
# OpenFOAM's shape: a rule, `Description`, prose, the closing rule of the same block comment.
BANNER = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
          "Description\n"
          "    Wall function for nut, blending laminar and turbulent behaviour near walls.\n"
          "    The blending stays continuous in y+ and switches nothing at run time.\n"
          "    It is used by the fixedValue patch fields of the nut field.\n"
          "\\*---------------------------------------------------------------------------*/")
# After the class: a code line plus prose that names the same identifier, so ONE query
# returns a class hit and a text hit from the very file that owns the doc (T018 fixture).
# The prose alone would be comment-only, and a comment-only chunk never enters FTS.
NOTES = ("// Notes for nutWallBlendFunction: the wall function blends laminar and turbulent\n"
         "// terms without a switching criterion, so the gradient stays well defined.\n") * 8
DOC_FILE = (BANNER + '\n#include "nutWallFunction.H"\n\n'
            "class nutWallBlendFunction\n{\npublic:\n    scalar nut() const;\n};\n\n"
            "scalar nutWallBlendFunctionScale = 1.0;\n" + NOTES)
# A description far wider than max_chars: the whole point of C17/C19 is that it arrives whole.
LONG_PROSE = "".join(f"    Line {index}: the blending stays continuous and no switch is needed.\n"
                     for index in range(60))
# The class body is wide enough that the hit's own preview really is truncated at
# max_chars=200, so the test can show the cut landing on `text` and not on `doc`.
MEMBERS = "".join(f"    scalar member{index}() const;\n" for index in range(20))
LONG_FILE = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
             "Description\n" + LONG_PROSE
             + "\\*---------------------------------------------------------------------------*/"
             + '\n#include "longDesc.H"\n\nclass longDescClass\n{\npublic:\n'
             + MEMBERS + "};\n")


@pytest.fixture
def doc_index(tmp_path, corpus):
    """A reader over the doc corpus, plus its index path so a test can read the stored row."""
    root = corpus({PATH: DOC_FILE, "src/long/longDesc.H": LONG_FILE})
    cfg = build_config(root, str(tmp_path / "docs.sqlite"), ("**/*",))
    index.build(cfg)
    return index.open_reader(cfg.index_path), cfg.index_path


def _filedoc_row(index_path, path):
    """The stored filedoc row: the doc must be exactly this, field for field (FR-013)."""
    keys = ("start_line", "end_line", "text")
    with sqlite3.connect(index_path) as conn:
        rows = conn.execute("SELECT " + ", ".join(keys) + " FROM chunks"
                            " WHERE path = ? AND kind = 'filedoc'", (path,)).fetchall()
    assert len(rows) == 1, f"the fixture must store one filedoc for {path}, got {len(rows)}"
    return dict(zip(keys, rows[0]))


def _first(hits, kind):
    """The first hit of a kind, or a failure that shows what the search did return."""
    found = [hit for hit in hits if hit.kind == kind]
    assert found, f"expected a {kind} hit, got {[(h.kind, h.path) for h in hits]}"
    return found[0]


def test_hit_carries_doc_for_class(doc_index):
    """FR-013: a class hit on a file with a filedoc chunk carries that doc."""
    reader, _ = doc_index
    hits = search(reader, "nutWallBlendFunction", top_k=10)
    hit = _first(hits, "class")

    assert hit.path == PATH
    assert hit.doc is not None, "a class hit whose file has a filedoc must carry the doc"
    assert set(hit.doc) == {"start_line", "end_line", "text"}
    assert hit.doc["text"].startswith("Description"), (
        "the doc is the Description section itself, not the banner above it")
    assert "Wall function for nut" in hit.doc["text"]


def test_hit_doc_null_for_text_kind(doc_index):
    """FR-013: doc is null for text hits — even when that very file owns a filedoc."""
    reader, _ = doc_index
    hits = search(reader, "nutWallBlendFunction", top_k=10)
    hit = _first(hits, "text")

    assert hit.path == PATH, "the text hit must come from the file that owns the doc"
    assert hit.symbol is None
    assert hit.doc is None, "a text hit must not carry a doc, though its file has one"


def test_hit_doc_null_for_filedoc_kind(doc_index):
    """FR-013: a filedoc hit already IS the description as its text; doc stays null."""
    reader, _ = doc_index
    hits = search(reader, "blending", top_k=10)
    hit = _first([h for h in hits if h.path == PATH], "filedoc")

    assert hit.text.startswith("    Wall function for nut"), (
        "the hit's own text must be the description prose")
    assert hit.text_start_line == hit.start_line + 1, (
        "the preview opened on the line holding the query, right after `Description`")
    assert hit.doc is None, "attaching the doc again would duplicate the text"


def test_doc_is_the_whole_description_never_truncated(doc_index):
    """C19: no max_chars cut and no `truncated` key — the doc is the section, whole.

    max_chars=200 is the preview floor: the hit's own text is cut at 200 chars, and the
    doc must still arrive complete. A truncating implementation fails either assertion.
    """
    reader, _ = doc_index
    hit = _first(search(reader, "longDescClass", max_chars=200, top_k=10), "class")

    assert hit.truncated is True, "the fixture must trigger preview truncation, else this proves nothing"
    assert hit.doc is not None
    assert "truncated" not in hit.doc, "C19: the doc carries no truncation flag"
    assert len(hit.doc["text"]) > 2000, f"the doc must exceed max_chars, got {len(hit.doc['text'])}"
    lines = LONG_FILE.split("\n")
    window = lines[hit.doc["start_line"] - 1:hit.doc["end_line"]]
    assert hit.doc["text"] == "\n".join(window), "the doc must be the verbatim source slice"
    assert hit.doc["text"].rstrip().endswith("*/"), "the section ends at the closing rule"


def test_doc_matches_the_filedoc_chunk_exactly(doc_index):
    """FR-013: doc.text is the chunk's text byte for byte, and the span is the chunk's span."""
    reader, index_path = doc_index
    hit = _first(search(reader, "nutWallBlendFunction", top_k=10), "class")
    row = _filedoc_row(index_path, PATH)

    assert hit.doc == row, "doc must be the stored filedoc row, field for field"
    lines = DOC_FILE.split("\n")
    assert hit.doc["text"] == "\n".join(lines[row["start_line"] - 1:row["end_line"]]), (
        "the doc text must be the verbatim source slice of its own span")
