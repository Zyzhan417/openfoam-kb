"""Chunker C18: the licence stub the carve strands before a filedoc (FR-007, FR-009, C18).

C17 cuts the Description section out of the span that held it. In an OpenFOAM file that
span is `banner / licence para A / licence para B / Description`: para A is already wider
than the glue floor, so the buffer has flushed by the time para B starts, and para B then
runs into the filedoc boundary and cannot keep accumulating. It was emitted alone — 3,871
licence stubs of ~245 chars on the corpus, a searchable chunk of boilerplate that pre-C17
sat invisibly inside the big filedoc chunk.

C18 merges that stub backwards into the `text` chunk above it, and only into a `text`
chunk: extending a `class`/`function` span would swallow code into a unit (FR-010, SC-016).
"""

import pytest

from conftest import _src
from foamkb.chunker import chunk_source
from foamkb.config import GLUE_MIN_CHARS

BANNER_OPEN = "/*--------------------------------*- C++ -*----------------------------------*\\"
BANNER_CLOSE = "\\*---------------------------------------------------------------------------*/"
LICENCE_TAIL = ("    You should have received a copy of the GNU General Public License",
                "    along with OpenFOAM.  If not, see <http://www.gnu.org/licenses/>.")
LICENCE_PARA = ("License",
                "    This file is part of OpenFOAM.",
                "",
                "    OpenFOAM is free software: you can redistribute it and/or modify it",
                "    under the terms of the GNU General Public License as published by",
                "    the Free Software Foundation, either version 3 of the License, or",
                "    (at your option) any later version that the Foundation publishes.")
PROSE = ("    Solves the Black-Scholes equation to price commodities, with dividends.",
         "    The mesh is read once and the option surface is written at every time step.")
TAIL_HEAD = "You should have received"


def _width(lines, first, last):
    """Characters of the exact inclusive 0-based slice, newlines included (chunker `_chars`)."""
    return sum(len(lines[index]) for index in range(first, last + 1)) + last - first


def _chunks(lines, max_chars=2000):
    return chunk_source(_src(*lines, path="stub.C"), "struct", 150, max_chars, GLUE_MIN_CHARS)["chunks"]


def _one(chunks, kind):
    found = [chunk for chunk in chunks if chunk["kind"] == kind]
    assert len(found) == 1, f"expected one {kind}, got {[(c['kind'], c['start_line']) for c in chunks]}"
    return found[0]


def _stubs(chunks):
    """Chunks that begin at the licence tail — the fragment C18 exists to remove."""
    return [chunk for chunk in chunks
            if chunk["text"].split("\n")[0].strip().startswith(TAIL_HEAD)]


def _assert_covered_once(lines, chunks):
    """FR-009: every non-blank line once, no overlap, ascending and disjoint."""
    covered = {}
    for chunk in chunks:
        for number in range(chunk["start_line"], chunk["end_line"] + 1):
            covered[number] = covered.get(number, 0) + 1
    for number, line in enumerate(lines, 1):
        assert covered.get(number, 0) == (1 if line.strip() else covered.get(number, 0)), number
    for previous, following in zip(chunks, chunks[1:]):
        assert previous["end_line"] < following["start_line"], (previous["end_line"], following["start_line"])


def _licence_file():
    """The corpus shape: banner, licence para A (over the floor), tail, Description."""
    lines = [BANNER_OPEN, *LICENCE_PARA, "", *LICENCE_TAIL, "",
             "Description", *PROSE, "", BANNER_CLOSE, "",
             '#include "price.H"', "", "int main(int argc, char* argv[])", "{", "}"]
    tail = lines.index(LICENCE_TAIL[0])
    assert _width(lines, 0, tail - 2) > GLUE_MIN_CHARS, "para A must flush the glue buffer"
    assert _width(lines, tail, tail + 1) < GLUE_MIN_CHARS, "the tail must be below the floor"
    return lines


def test_the_licence_tail_before_a_filedoc_is_merged_backwards():
    """C18: a below-floor text chunk immediately before a filedoc joins the text above it."""
    lines = _licence_file()
    chunks = _chunks(lines)
    doc = _one(chunks, "filedoc")
    tail = lines.index(LICENCE_TAIL[0]) + 1
    above = [chunk for chunk in chunks if chunk["end_line"] < doc["start_line"]][-1]

    assert _stubs(chunks) == [], "the licence tail must not stand alone as its own chunk"
    assert above["kind"] == "text" and above["end_line"] == tail + 1, (
        f"the tail must be absorbed into the chunk above, got {above['kind']} "
        f"{above['start_line']}-{above['end_line']}")
    assert above["start_line"] == 1, "the merge is backwards, so the chunk starts where it did"
    assert LICENCE_TAIL[0].strip() in above["text"] and LICENCE_PARA[3].strip() in above["text"]
    assert above["text"] == "\n".join(lines[above["start_line"] - 1:above["end_line"]]), (
        "a merged chunk is still a verbatim contiguous slice")
    assert doc["start_line"] == lines.index("Description") + 1, "the filedoc must not move"
    assert not any(marker in doc["text"] for marker in ("GNU General Public", "along with OpenFOAM"))
    _assert_covered_once(lines, chunks)


CLASS_BEFORE = ("class LicenceStub", "{", "public:", "    void read();", "};")
FUNCTION_BEFORE = ("void licenceStubRead()", "{", "    int count = 0;", "}")


@pytest.mark.parametrize("unit, kind", [(CLASS_BEFORE, "class"), (FUNCTION_BEFORE, "function")])
def test_the_merge_never_extends_a_class_or_function_chunk(unit, kind):
    """C18/FR-010: the target must be `text` — a unit span never grows past its own code."""
    lines = [*unit, "", *LICENCE_TAIL, "", BANNER_OPEN, "Description", *PROSE, BANNER_CLOSE]
    chunks = _chunks(lines)
    _one(chunks, "filedoc")
    span = [chunk for chunk in chunks if chunk["kind"] == kind][-1]

    assert span["end_line"] <= len(unit), (
        f"the {kind} span must end inside its own block, got {span['start_line']}-{span['end_line']}")
    assert TAIL_HEAD not in span["text"], "the licence tail must never enter a unit span"
    stubs = _stubs(chunks)
    assert len(stubs) == 1 and stubs[0]["kind"] == "text" and stubs[0]["start_line"] == len(unit) + 2, (
        f"with a unit above it the stub stays its own chunk, got {[(c['kind'], c['start_line']) for c in chunks]}")
    _assert_covered_once(lines, chunks)


def test_no_merge_when_the_combined_width_would_exceed_max_chars():
    """C18: the merged chunk must still fit max_chars, else it would only be cut again."""
    filler = [f"// leading note {index:02d}: prose about the file's scope, its purpose and its collaborators"
              for index in range(1, 24)]
    lines = [*filler, "", *LICENCE_TAIL, "", "Description", *PROSE, "", BANNER_CLOSE]
    tail = len(filler) + 1
    assert _width(lines, 0, tail - 1) < 2000, "the filler must pack into one chunk"
    assert _width(lines, 0, tail + 1) > 2000, "the pair must exceed max_chars, else this proves nothing"
    chunks = _chunks(lines, max_chars=2000)

    stubs = _stubs(chunks)
    assert len(stubs) == 1 and stubs[0]["start_line"] == tail + 1, (
        f"an over-wide merge must be left alone, got {[(c['kind'], c['start_line']) for c in chunks]}")
    assert len(stubs[0]["text"]) < GLUE_MIN_CHARS
    assert chunks[0]["end_line"] == tail - 1, "the wide chunk must not grow past max_chars"
    _assert_covered_once(lines, chunks)


def test_a_narrow_text_tail_before_a_code_unit_stays_its_own_chunk():
    """C18 is scoped to the filedoc boundary — the general absorb was measured and rejected.

    The same 245-char tail in front of a class keeps its own chunk: only the boundary the
    carve created is repaired, so V2's 71,266-chunk rewrite cannot creep back in.
    """
    filler = [f"// leading note {index:02d}: prose about the file's scope, its purpose and its collaborators"
              for index in range(1, 9)]
    lines = [*filler, "", *LICENCE_TAIL, "", "class Tail", "{", "};"]
    tail = len(filler) + 1
    chunks = _chunks(lines)

    assert [chunk["kind"] for chunk in chunks] == ["text", "text", "class"], (
        f"only a filedoc boundary absorbs the stub: {[(c['kind'], c['start_line']) for c in chunks]}")
    assert chunks[1]["start_line"] == tail + 1 and len(_stubs(chunks)) == 1
    assert chunks[0]["end_line"] == tail - 1, "the chunk above must not grow past its own span"
    _assert_covered_once(lines, chunks)


def test_a_filedoc_that_opens_the_file_has_no_text_chunk_to_merge_into():
    """C18: no preceding text chunk means leave it alone; the carve must still be exact."""
    lines = ["Description", *PROSE, "", "class NoHeader", "{", "};"]
    chunks = _chunks(lines)

    assert chunks[0]["kind"] == "filedoc" and chunks[0]["start_line"] == 1, (
        f"the doc opens the file, got {[(c['kind'], c['start_line']) for c in chunks]}")
    assert chunks[0]["end_line"] == 1 + len(PROSE)
    _assert_covered_once(lines, chunks)
