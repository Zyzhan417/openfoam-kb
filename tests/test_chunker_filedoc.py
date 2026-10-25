"""Chunker filedoc: the Description section as its own never-split chunk (FR-011..FR-013a, C17).

C17 supersedes the read-side trim that `reader.doc_slice` performed: the seven tests of
tests/test_reader_doc_slice.py asserted that rule (branch ① ends at `*/`, branch ② before a
directive, `#` examples survive, `Description:` accepted) against a function that no longer
exists, so that file was deleted rather than rewritten — the same behaviours are asserted
here against the chunk the build now produces, which is the only place they can still be
wrong. AGENTS C10: deletion of a test is otherwise forbidden; this is the one place the
spec (C17) moved the behaviour out from under it.

The licence rejection of FR-012 survives on both sides: `filedoc.carve` refuses the section
here, and `Reader.file_doc` refuses a row written by an older rule (tests/test_reader_doc.py).
"""

from collections import Counter

import pytest

from conftest import _src
from foamkb.chunker import chunk_source
from foamkb.config import FILEDOC_MAX_CHARS, GLUE_MIN_CHARS

# The shape 2,890 corpus files have: the licence block closes with `\*---*/`, then the
# preprocessor tail follows. The section is lines 2-6; the glued tail (7-8) is the part
# that used to travel with the doc.
BANNER = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
          "Description\n"
          "    Wall function for nut, blending laminar and turbulent behaviour near walls.\n"
          "    The blending is continuous in y+ and needs no switching between regimes.\n"
          "    It is valid for both low and high Reynolds numbers without damping.\n"
          "\\*---------------------------------------------------------------------------*/\n"
          "#ifndef nutWallFunction_H\n"
          "#define nutWallFunction_H\n"
          "\n"
          '#include "nutWallFunction.H"\n'
          "\n"
          "class nutWallFunction\n"
          "{\n"
          "};\n")
BANNER_LINES = BANNER.split("\n")


def _chunks(text, max_chars=2000, glue=GLUE_MIN_CHARS, path="case.H"):
    """Every chunk the structural mode produces for one file's text."""
    return chunk_source(_src(*text.split("\n"), path=path), "struct", 150, max_chars, glue)["chunks"]


def _filedocs(chunks):
    return [chunk for chunk in chunks if chunk["kind"] == "filedoc"]


def _one_filedoc(chunks, what=""):
    docs = _filedocs(chunks)
    assert len(docs) == 1, f"{what}: expected exactly one filedoc, got {[(c['kind'], c['start_line']) for c in chunks]}"
    return docs[0]


def _assert_covered_once(lines, chunks):
    """FR-009: every non-blank line once, no overlap, ranges ascending and disjoint."""
    spans = sorted((chunk["start_line"], chunk["end_line"]) for chunk in chunks)
    counts = Counter()
    for start, end in spans:
        assert 1 <= start <= end <= len(lines), (start, end, len(lines))
        counts.update(range(start, end + 1))
    for previous, following in zip(spans, spans[1:]):
        assert previous[1] < following[0], (previous, following)
    for number, line in enumerate(lines, 1):
        if line.strip():
            assert counts[number] == 1, (number, counts[number], line)
        else:
            assert counts[number] <= 1, (number, counts[number])


def test_filedoc_is_one_chunk_from_description_line_to_block_close():
    """FR-011 ①/FR-012: the section is its own chunk, verbatim, from Description to `*/`."""
    chunks = _chunks(BANNER)
    assert chunks, "the fixture must produce chunks, else this proves nothing"
    doc = _one_filedoc(chunks, "banner")

    assert (doc["start_line"], doc["end_line"]) == (2, 6), (
        f"the section runs from the Description line to the banner's close, got "
        f"{doc['start_line']}-{doc['end_line']}")
    assert doc["symbol"] is None and doc["unit_id"] is None, "a filedoc carries no symbol"
    assert doc["text"] == "\n".join(BANNER_LINES[1:6]), "the text must be the verbatim slice"
    assert doc["text"].split("\n")[0] == "Description"
    assert doc["text"].split("\n")[-1].rstrip().endswith("*/")
    assert "#ifndef" not in doc["text"] and "#define" not in doc["text"], (
        "the preprocessor tail must never travel with the doc")
    _assert_covered_once(BANNER_LINES, chunks)


def test_filedoc_is_not_cut_by_max_chars():
    """FR-017/C17: the section is exempt from max_chars — this is the bug C17 fixes.

    Under the old rules the 400-char glue and max_chars both cut the section, which is how
    multiValveEngine.H reached the reader with 4 of its 194 lines. Here max_chars is 200 and
    the section is wider than that: it must still be ONE chunk.
    """
    chunks = _chunks(BANNER, max_chars=200)
    doc = _one_filedoc(chunks, "banner")

    assert (doc["start_line"], doc["end_line"]) == (2, 6)
    assert len(doc["text"]) > 200, "the fixture must exceed max_chars, else this proves nothing"
    for chunk in chunks:
        if chunk["kind"] != "filedoc":
            assert len(chunk["text"]) <= 200, (
                f"only the filedoc is exempt from max_chars, got {chunk['kind']} at "
                f"{chunk['start_line']} with {len(chunk['text'])} chars")
    _assert_covered_once(BANNER_LINES, chunks)


def test_filedoc_is_not_glued_to_its_neighbours():
    """FR-011/C17: the section never joins the prose below it, even when glue would.

    Section + trailing note pass the glue floor as a pair, so without the exclusion they
    would be ONE chunk and the doc would carry the neighbour's text.
    """
    prose = ["    Boundary condition that couples to an external application over files.",
             "    Values are exchanged as plain text at every time step, in both directions.",
             "    The interface closes when the other application writes its own stop file."]
    note = "// a gap note below the banner, long enough that glue would join the two together"
    text = "\n".join(["/*--------------------------------*- C++ -*----------------------------------*\\",
                      "Description", *prose,
                      "\\*---------------------------------------------------------------------------*/",
                      "", note, "", "const scalar coupledScale = 1.0;", ""])
    lines = text.split("\n")
    end = next(index for index, line in enumerate(lines) if line.rstrip().endswith("*/"))
    width = sum(len(lines[index]) for index in range(0, end + 1)) + end
    assert width + len(note) >= GLUE_MIN_CHARS, (
        f"the pair must pass the glue floor, else this proves nothing: {width + len(note)}")

    doc = _one_filedoc(_chunks(text), "glue")

    assert doc["text"] == "\n".join(lines[1:end + 1]), "the doc must be the section and nothing else"
    assert note not in doc["text"], "the gap note must stay in its own chunk"
    _assert_covered_once(lines, _chunks(text))


DIRECTIVES = ("#ifndef", "#define", "#include", "#ifdef", "#endif", "#else", "#elif")


@pytest.mark.parametrize("directive", DIRECTIVES)
def test_filedoc_without_block_close_stops_before_directive(directive):
    """FR-011 ②/C16: with no `*/` the section ends before a preprocessor directive.

    The set is the seven directives and nothing else, indented or not; one fixture per
    entry keeps the failure readable when the set is narrowed by accident.
    """
    text = ("Description\n"
            "    Prose line one.\n"
            "    Prose line two.\n"
            f"    {directive} SOMETHING\n"
            "    tail that is not prose\n"
            "\n"
            "class Foo\n"
            "{\n"
            "};")
    chunks = _chunks(text)
    doc = _one_filedoc(chunks, directive)

    assert (doc["start_line"], doc["end_line"]) == (1, 3), (
        f"{directive} must end the section, got {doc['start_line']}-{doc['end_line']}")
    assert doc["text"] == "Description\n    Prose line one.\n    Prose line two."
    _assert_covered_once(text.split("\n"), chunks)


def test_filedoc_keeps_hash_example_lines():
    """FR-011 ②/C16: `# Patch: <name>` is prose, not a directive — the section runs past it.

    Cutting on any `#` line threw away the example block: on the corpus it cost
    inputModeEntry.H 479 of its 640 characters.
    """
    text = ("Description\n"
            "    This boundary condition provides an interface to an external application.\n"
            "    Values are transferred as plain text files, where OpenFOAM data is written:\n"
            "\n"
            "    \\verbatim\n"
            "        # Patch: <patch name>\n"
            "        <magSf1> <value1> <surfaceNormalGradient1>\n"
            "    \\endverbatim\n"
            "    The whole interface is read at every time step and written back on write.\n"
            "\n"
            '#include "externalCoupledMixedFvPatchField.H"\n'
            "\n"
            "class externalCoupledMixed\n"
            "{\n"
            "};")
    doc = _one_filedoc(_chunks(text), "hash")

    assert doc["end_line"] == 9, f"an example '#' line must not end the section, got {doc['end_line']}"
    assert "# Patch: <patch name>" in doc["text"]
    assert "<magSf1> <value1> <surfaceNormalGradient1>" in doc["text"]


def test_block_close_wins_over_the_section_rule_cut():
    """FR-011: branch ① is tried first — a `*/` ends the section even after a rule line.

    The rule sits inside the block comment, so it is prose; branch ② would stop at line 4
    and drop the prose after it.
    """
    text = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
            "Description\n"
            "    Prose that continues past a section rule.\n"
            "    Still prose.\n"
            "    // * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //\n"
            "    More prose after the rule, inside the same comment.\n"
            "\\*---------------------------------------------------------------------------*/\n"
            "#ifndef x_H\n"
            "#define x_H\n"
            "\n"
            '#include "x.H"\n'
            "\n"
            "class x\n"
            "{\n"
            "};")
    doc = _one_filedoc(_chunks(text), "verbatim")

    assert doc["end_line"] == 7, f"the doc must end at the '*/' line, got {doc['end_line']}"
    assert "More prose after the rule, inside the same comment." in doc["text"], (
        "branch ① must not be shortened by branch ②'s rule test")


def test_filedoc_accepts_the_description_colon_variant():
    """FR-011: `Description:` is a legal variant (levelSet.H, cutTriTet.H) — 2 corpus files."""
    text = BANNER.replace("Description\n", "Description:\n", 1)
    doc = _one_filedoc(_chunks(text), "colon")

    assert doc["text"].split("\n")[0] == "Description:"
    assert (doc["start_line"], doc["end_line"]) == (2, 6)


def test_no_filedoc_when_the_section_carries_a_licence_marker():
    """FR-012: a licensed section must produce NO filedoc, and must not lose its lines."""
    text = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
            "Description\n"
            "    Wall function for nut, blending laminar and turbulent behaviour.\n"
            "    It is provided WITHOUT ANY WARRANTY of any kind, express or implied.\n"
            "\\*---------------------------------------------------------------------------*/\n"
            "\n"
            '#include "nutWallFunction.H"\n'
            "\n"
            "class nutWallFunction\n"
            "{\n"
            "};")
    chunks = _chunks(text)

    assert chunks, "the fixture must produce chunks, else this proves nothing"
    assert _filedocs(chunks) == [], "a licence marker inside the section forbids the filedoc"
    _assert_covered_once(text.split("\n"), chunks)


def test_no_filedoc_when_the_section_starts_inside_a_class_chunk():
    """FR-011: a symbol-carrying span keeps its lines — carving there deletes the symbol.

    No `#include` anywhere, so the "before the first include" test is vacuous; the banner is
    absorbed into the class unit by FR-004, and the Description therefore sits inside a
    `class` span. 466 corpus files have this shape.
    """
    text = ("/*---------------------------------------------------------------------------*\\\n"
            "Class\n"
            "    Foam::wallFunction\n"
            "\n"
            "Description\n"
            "    Wall function for nut, blending laminar and turbulent behaviour.\n"
            "\n"
            "\\*---------------------------------------------------------------------------*/\n"
            "\n"
            "class wallFunction\n"
            "{\n"
            "public:\n"
            "    scalar nut() const;\n"
            "};")
    chunks = _chunks(text)

    assert [chunk["kind"] for chunk in chunks] == ["class"], (
        f"the symbol span must stay a class, got {[(c['kind'], c['symbol']) for c in chunks]}")
    assert chunks[0]["symbol"] == "wallFunction", "the symbol must survive the carve"
    _assert_covered_once(text.split("\n"), chunks)


def test_section_over_the_limit_is_one_chunk_and_warns():
    """FR-017/C17: a section wider than FILEDOC_MAX_CHARS warns and stays whole anyway.

    Splitting it would defeat the purpose of the kind, so the limit only reports.
    """
    prose = "".join("    " + "x" * 70 + "\n" for _ in range(130))
    text = ("/*--------------------------------*- C++ -*----------------------------------*\\\n"
            "Description\n" + prose
            + "\\*---------------------------------------------------------------------------*/\n"
            "\n"
            '#include "wide.H"\n'
            "\n"
            "class wide\n"
            "{\n"
            "};")
    src = _src(*text.split("\n"), path="wide.H")
    result = chunk_source(src, "struct", 150, 2000)
    doc = _one_filedoc(result["chunks"], "wide")

    assert (doc["start_line"], doc["end_line"]) == (2, 133), (
        f"the whole section must be one chunk, got {doc['start_line']}-{doc['end_line']}")
    assert len(doc["text"]) > FILEDOC_MAX_CHARS, "the fixture must exceed the limit, else this proves nothing"
    reasons = [warning["reason"] for warning in result["warnings"]]
    assert "filedoc_too_wide" in reasons, f"an over-limit section must warn, got {reasons}"
    _assert_covered_once(text.split("\n"), result["chunks"])


def test_section_crossing_a_chunk_boundary_keeps_every_line_covered_once():
    """FR-009: the section is taken OUT of its span, not copied — coverage stays exact.

    This is the shape that used to split 1,394 sections: prose above the banner, a section
    wider than max_chars, code below. Adding the filedoc without splitting the host span
    would cover those lines twice; dropping them would leave the file short.
    """
    lead = "".join(f"// leading note {index} about the file's purpose and its scope\n"
                   for index in range(1, 21))
    prose = "".join(f"    Description line {index}: laminar and turbulent blending near walls.\n"
                    for index in range(1, 21))
    text = (lead
            + "/*--------------------------------*- C++ -*----------------------------------*\\\n"
            + "Description\n" + prose
            + "\\*---------------------------------------------------------------------------*/\n"
            + "\n"
            + '#include "wall.H"\n'
            + "\n"
            + "class wall\n{\n};\n")
    chunks = _chunks(text, max_chars=200)
    doc = _one_filedoc(chunks, "crossing")
    lines = text.split("\n")
    start = lines.index("Description") + 1
    end = next(index for index, line in enumerate(lines) if line.rstrip().endswith("*/")) + 1
    assert (doc["start_line"], doc["end_line"]) == (start, end), (
        f"the section must be one chunk despite max_chars, got {doc['start_line']}-{doc['end_line']}")
    assert len(doc["text"]) > 200, "the fixture must exceed max_chars, else this proves nothing"
    _assert_covered_once(text.split("\n"), chunks)
