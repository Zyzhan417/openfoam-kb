"""index filedoc: the file-level Description block, its licence guard and kind guard (FR-011..FR-012)."""

import sqlite3

from conftest import _cfg
from foamkb import index


# --- filedoc: the file-level Description block (FR-011, FR-012) --------------

def _banner(marker, lines=8):
    """A comment block over 400 characters, so gluing keeps it in one chunk.

    The shape is OpenFOAM's: a `\\*-...-*\\` rule, then `Description`, then indented prose.
    """
    body = "".join(f"    {marker} line {index}: blending laminar and turbulent behaviour.\n"
                   for index in range(lines))
    return ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
            "Description\n" + body
            + "\\*---------------------------------------------------------------------------*/\n")


def _chunks_at(index_path, path):
    """One file's chunks in line order, as the columns this section asserts on."""
    keys = ("start_line", "end_line", "kind", "symbol", "text")
    with sqlite3.connect(index_path) as conn:
        rows = conn.execute(
            "SELECT " + ", ".join(keys) + " FROM chunks WHERE path = ? ORDER BY start_line",
            (path,)).fetchall()
    return [dict(zip(keys, row)) for row in rows]


def _filedocs(index_path, path):
    return [chunk for chunk in _chunks_at(index_path, path) if chunk["kind"] == "filedoc"]


DOC_FILE = (_banner("generic wall function")
            + '\n#include "nutWallFunction.H"\n\nclass nutWallFunction\n{\npublic:\n'
              "    scalar nut() const;\n};\n")
# Same block, but only after the first #include: not a file-level description (FR-011).
LATE_FILE = '#include "nutWallFunction.H"\n\n' + _banner("generic wall function")
MULTI_FILE = (_banner("the general case") + "\n" + _banner("the specialised case")
              + '\n#include "x.H"\n\nclass multi\n{\n};\n')
# C14: the licence and the Description live inside ONE block comment in OpenFOAM, so the
# stored chunk starts with licence text and its tail carries the Description. That is the
# shape of 2,890 real files, so this file MUST keep its filedoc — the licence test below
# applies to the Description-onward slice, not to the whole chunk.
LICENSED_FILE = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    "  =========                 |\n"
    "  \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox\n"
    "   \\\\    /   O peration     |\n"
    "-------------------------------------------------------------------------------\n"
    "License\n"
    "    This file is part of OpenFOAM.\n"
    "    OpenFOAM is free software: you can redistribute it and/or modify it\n"
    "    under the terms of the GNU General Public License as published by\n"
    "    the Free Software Foundation, either version 3 of the License, or\n"
    "    (at your option) any later version.\n"
    "Description\n"
    "    Wall function for nut, blending laminar and turbulent behaviour.\n"
    "\\*---------------------------------------------------------------------------*/\n"
    '\n#include "nutWallFunction.H"\n\nclass nutWallFunction\n{\n};\n')
# The licence marker sits INSIDE the Description-onward slice: this is the only shape the
# amended FR-012 may still reject (C14). The head is clean, and the comment block runs past
# the 400-char glue floor so it is a chunk of its own — otherwise it would glue onto the
# #include line and never become a candidate at all.
LICENSED_SLICE_FILE = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    "Class\n"
    "    Foam::nutWallFunction\n"
    "Description\n"
    "    Wall function for nut, blending laminar and turbulent behaviour near walls.\n"
    "    The blending is continuous in y+ and needs no switching between regimes.\n"
    "    It is provided WITHOUT ANY WARRANTY of any kind, express or implied.\n"
    "    See also Foam::nutWallFunctionFvPatchScalarField for the patch side.\n"
    "SourceFiles\n"
    "    nutWallFunctionFvPatchScalarField.C\n"
    "\\*---------------------------------------------------------------------------*/\n"
    '\n#include "nutWallFunction.H"\n\nclass nutWallFunction\n{\n};\n')


def test_filedoc_picked_before_first_include(tmp_path, corpus):
    """FR-011: the Description block before the first #include becomes the file's filedoc."""
    cfg = _cfg(tmp_path, corpus, {"src/wall/nut.H": DOC_FILE, "src/late/late.H": LATE_FILE})
    index.build(cfg)

    docs = _filedocs(cfg.index_path, "src/wall/nut.H")
    assert len(docs) == 1, f"expected exactly one filedoc, got {[(c['kind']) for c in _chunks_at(cfg.index_path, 'src/wall/nut.H')]}"
    doc = docs[0]
    assert doc["symbol"] is None
    # C17 supersedes start_line == 1: FR-011 makes the chunk begin on the Description line
    # itself (the banner rule above it is no longer glued to the doc), so the chunk's first
    # line is line 2. The old expectation pinned the whole-chunk shape, not the requirement.
    assert doc["start_line"] == 2
    assert "Description" in doc["text"]
    # FR-008: the filedoc text is still the exact slice of that line range.
    lines = DOC_FILE.split("\n")
    assert doc["text"] == "\n".join(lines[doc["start_line"] - 1:doc["end_line"]])
    # A Description that first appears after the #include is not a file-level description.
    assert _filedocs(cfg.index_path, "src/late/late.H") == []


def test_multiple_description_takes_lowest_line(tmp_path, corpus):
    """FR-011 / D6: several candidates on one file → the smallest start_line wins."""
    cfg = _cfg(tmp_path, corpus, {"src/multi/multi.H": MULTI_FILE})
    index.build(cfg)

    chunks = _chunks_at(cfg.index_path, "src/multi/multi.H")
    docs = [chunk for chunk in chunks if chunk["kind"] == "filedoc"]
    assert len(docs) == 1, "one file must never produce two filedoc chunks (SC-014)"
    assert docs[0]["start_line"] == 2, "C17: the chunk starts on the Description line"
    assert "the general case" in docs[0]["text"]
    later = [chunk for chunk in chunks if "the specialised case" in chunk["text"]]
    assert later, "the second candidate must exist in the fixture, else this proves nothing"
    assert all(chunk["kind"] == "text" for chunk in later)


def test_filedoc_rejected_when_license_present(tmp_path, corpus):
    """FR-012 as amended by C14: the rejectable unit is the Description-onward slice."""
    cfg = _cfg(tmp_path, corpus, {"src/clean/nut.H": DOC_FILE,
                                  "src/licensed/nut.H": LICENSED_SLICE_FILE})
    index.build(cfg)

    assert len(_filedocs(cfg.index_path, "src/clean/nut.H")) == 1, (
        "the control file must get a filedoc, else the rejection below proves nothing")
    assert _filedocs(cfg.index_path, "src/licensed/nut.H") == []


def test_licensed_prefix_keeps_clean_description(tmp_path, corpus):
    """C14/C17: a licensed HEAD above the Description does not cost the file its doc.

    Every .H in the corpus is this shape (licence, then Description, inside one block
    comment). Testing the whole chunk — the old FR-012 — rejected all 8,911 files and
    yielded 0 docs. C17 moved the boundary into the build, so the stored chunk IS the
    Description-onward section: it must not hold the licence at all, and the doc must
    start on the Description line. The pre-C17 assertion here ("the stored chunk still
    holds the licence") pinned the glued-chunk shape that C17 removed.
    """
    cfg = _cfg(tmp_path, corpus, {"src/licensed/nut.H": LICENSED_FILE})
    index.build(cfg)

    docs = _filedocs(cfg.index_path, "src/licensed/nut.H")
    assert len(docs) == 1, "a licensed head with a clean Description tail must keep its filedoc"
    assert "GNU General Public" not in docs[0]["text"], (
        "the chunk is the section itself now, so the licence must be outside it")
    assert docs[0]["text"].split("\n")[0] == "Description"
    doc = index.open_reader(cfg.index_path).file_doc("src/licensed/nut.H")
    assert doc is not None, "the section is clean, so the doc must exist"
    assert doc["start_line"] == 12, "the doc must begin on the Description line"
    assert "GNU" not in doc["text"], "no licence text may travel in the doc"


# A filedoc whose body is pure prose: every line is a comment, so the code-character
# gate sees 11 code characters ("Description") and would leave it out of chunks_fts.
FTS_FILE = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
            "Description\n"
            + "".join(f"*    Wall function for nut, line {index}: laminar/turbulent blending.\n"
                      for index in range(8))
            + "\\*---------------------------------------------------------------------------*/\n"
            '\n#include "nutWallFunction.H"\n')


def test_filedoc_enters_fts(tmp_path, corpus):
    """FR-013a: a filedoc is searchable on its own, even when it is nothing but comments."""
    cfg = _cfg(tmp_path, corpus, {"src/wall/nut.H": FTS_FILE})
    index.build(cfg)

    assert len(_filedocs(cfg.index_path, "src/wall/nut.H")) == 1
    with sqlite3.connect(cfg.index_path) as conn:
        chunk_id = conn.execute("SELECT id FROM chunks WHERE path = ? AND kind = 'filedoc'",
                                ("src/wall/nut.H",)).fetchone()[0]
        hits = conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE rowid = ?",
                            (chunk_id,)).fetchone()[0]
    assert hits == 1, "the filedoc row must be in chunks_fts, not only in chunks"

    reader = index.open_reader(cfg.index_path)
    assert chunk_id in reader.search_lexical('"laminar"', 10), (
        "the lexical channel must be able to hit the filedoc on its own")


# --- C15 guards: candidate kind, and the three meta keys FR-016 requires ------

# No `#include` anywhere in this file, so the "before the first include" test is vacuously
# true for all of its chunks. 466 corpus files are like this, and 65 class/function chunks
# sat one Description line away from being relabelled filedoc — losing their symbol. The
# banner is absorbed into the class unit by FR-004, which is how a symbol chunk comes to
# hold a bare `Description` line at all.
SYMBOL_ONLY_FILE = (
    "/*---------------------------------------------------------------------------*\\\n"
    "Class\n"
    "    Foam::wallFunction\n"
    "\n"
    "Description\n"
    "    Wall function for nut, blending laminar and turbulent behaviour near walls.\n"
    "    It is used by the fixedValue patch fields of the nut field.\n"
    "\n"
    "\\*---------------------------------------------------------------------------*/\n"
    "\n"
    "class wallFunction\n"
    "{\n"
    "public:\n"
    "    scalar nut() const;\n"
    "};\n")
# The control: with no #include, a prose Description on a text chunk still makes a doc.
NO_INCLUDE_DOC = (
    "Description\n"
    "    Wall function for nut, blending laminar and turbulent behaviour near walls.\n"
    "    The blending is continuous in y+ and needs no switching between regimes.\n"
    "    It is valid for both low and high Reynolds numbers without damping.\n"
    "\n"
    "class wallFunction\n"
    "{\n"
    "};\n")


def test_filedoc_never_comes_from_a_class_or_function_chunk(tmp_path, corpus):
    """FR-011 guard (C15): a candidate that carries a symbol keeps it.

    `_mark_filedoc` clears symbol and sets kind='filedoc'. On a file with no #include the
    include test admitted *every* chunk, so this class chunk would have lost
    symbol=wallFunction (measured corpus exposure: 65 chunks). The control file pins how
    narrow the guard is: a text Description in a file with no #include is still a doc.
    """
    cfg = _cfg(tmp_path, corpus, {"src/sym/wallFunction.H": SYMBOL_ONLY_FILE,
                                  "src/plain/wallFunction.H": NO_INCLUDE_DOC})
    index.build(cfg)

    chunks = _chunks_at(cfg.index_path, "src/sym/wallFunction.H")
    assert [chunk["kind"] for chunk in chunks] == ["class"], (
        f"the symbol chunk must stay a class, got {[(c['kind'], c['symbol']) for c in chunks]}")
    assert chunks[0]["symbol"] == "wallFunction", "the symbol must survive the filedoc pass"
    assert _filedocs(cfg.index_path, "src/sym/wallFunction.H") == [], (
        "a class chunk must never be rewritten as a filedoc")
    assert len(_filedocs(cfg.index_path, "src/plain/wallFunction.H")) == 1, (
        "the guard must not disable the no-#include text case")
