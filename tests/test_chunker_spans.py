"""chunker spans: gap gluing, separator merging, char-bounded splits (FR-007, FR-007a, FR-008)."""

import pytest

from conftest import _ranges, _src
from foamkb.chunker import chunk_source
from foamkb.config import GLUE_MIN_CHARS


# --- 002 Phase 2: gap gluing and separator merging (FR-007, FR-007a, FR-008) ---
#
# Fixture discipline (tasks.md "Fixture 要求" T009/T010): the gap regions must sum to
# LESS than GLUE_MIN_CHARS before the unit, otherwise the gluing loop never runs and
# the test cannot fail; each separator fixture holds the shape its rule acts on.


def _long(label, width=250):
    """One comment line well over `width` characters: a region that is glue-worthy alone."""
    return f"// {label} " + "x" * width


GLUE_UNDER_THRESHOLD_FILE = (
    "// alpha comment line one\n"      # 1  region 1
    "\n"                               # 2
    "// alpha comment line two\n"      # 3  region 2
    "\n"                               # 4
    "// beta comment line one\n"       # 5  region 3
    "const scalar betaValue = 2.0;\n"  # 6
    "\n"                               # 7
    "void tail()\n"                    # 8
    "{\n"                              # 9
    "    use();\n"                     # 10
    "}\n"                              # 11
)


def test_gap_regions_glue_to_threshold():
    """FR-007: three adjacent regions below the threshold become one block.

    Before gluing this file yields (1,1), (3,3), (5,6): three fragments where the
    source has one stretch of prose. The whole gap is under GLUE_MIN_CHARS, so the
    block must end where the unit starts, not where the threshold was reached.
    """
    src = _src(*GLUE_UNDER_THRESHOLD_FILE.splitlines())
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 6), (8, 11)]
    assert [chunk["kind"] for chunk in chunks] == ["text", "function"]
    assert len(chunks[0]["text"]) < GLUE_MIN_CHARS


GLUE_OVER_THRESHOLD_FILE = (
    _long("alpha") + "\n"
    + "\n"
    + _long("beta") + "\n"
    + "\n"
    + "// gamma tail\n"
)


def test_glue_flushes_at_the_threshold_and_never_splits_a_region():
    """FR-007: gluing stops once the accumulated text reaches the threshold.

    Two 259-char regions cross 400 together, so they form one block and the tail
    region starts a new one: a rule that never flushes would return a single chunk,
    a rule that flushes early would leave three.
    """
    src = _src(*GLUE_OVER_THRESHOLD_FILE.splitlines())
    chunks = chunk_source(src, "struct", 150, 8000)["chunks"]

    assert _ranges(chunks) == [(1, 3), (5, 5)]
    assert len(chunks[0]["text"]) >= GLUE_MIN_CHARS
    assert chunks[0]["text"] == "\n".join((_long("alpha"), "", _long("beta")))


GLUE_UNIT_BREAK_FILE = (
    "// leading note\n"         # 1
    "\n"                         # 2
    "// second leading note\n"  # 3
    "\n\n\n"                    # 4-6
    "void middle()\n"           # 7
    "{\n"                       # 8
    "    m();\n"                # 9
    "}\n"                       # 10
    "\n"                        # 11
    "// trailing note\n"        # 12
)


def test_glue_stops_at_unit():
    """FR-007: a structural unit breaks the run; the gaps on both sides stay separate.

    Two regions above the unit are glued to each other but not to the unit, and the
    region below it starts its own block: gluing that crossed a unit boundary would
    produce one (1,12) block here.
    """
    src = _src(*GLUE_UNIT_BREAK_FILE.splitlines())
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 3), (7, 10), (12, 12)]
    assert [chunk["kind"] for chunk in chunks] == ["text", "function", "text"]
    assert chunks[0]["end_line"] < 7 and chunks[2]["start_line"] > 10


GLUE_INDENTED_FILE = (
    "    // indented gap line one\n"       # 1
    "\n"                                    # 2
    "        // deeper gap line two\n"      # 3
    "\n"                                    # 4
    "const scalar  spaced  =  3.0;   \n"   # 5
    "\n\n\n"                                # 6-8
    "void tail()\n"                         # 9
    "{\n"                                   # 10
    "}\n"                                   # 11
)


def test_chunk_text_is_exact_slice():
    """FR-008: a glued block is still the verbatim slice of consecutive source lines."""
    src = _src(*GLUE_INDENTED_FILE.splitlines())
    glued = chunk_source(src, "struct", 150, 2000)["chunks"][0]

    assert (glued["start_line"], glued["end_line"]) == (1, 5)
    assert glued["text"] == "\n".join(GLUE_INDENTED_FILE.splitlines()[0:5])
    assert glued["text"].count("\n") == 4, "the blank lines between regions must survive"
    assert glued["text"].startswith("    // indented")
    assert glued["text"].endswith("3.0;   "), "trailing whitespace must not be trimmed"
    assert "..." not in glued["text"], "an ellipsis would mean the block is not a slice"


SEP_TAIL_FILE = (
    "const scalar endValue = 1.0;\n"   # 1
    "\n"                                # 2
    "// " + "*" * 74 + "\n"             # 3  a section rule at EOF
    "// " + "-" * 74 + "\n"             # 4
)


def test_trailing_separator_merges_into_previous():
    """FR-007a: a rule-only region at EOF joins the preceding block instead of standing alone."""
    src = _src(*SEP_TAIL_FILE.splitlines())
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 4)]
    assert chunks[0]["kind"] == "text"
    assert chunks[0]["text"].endswith("// " + "-" * 74)


SEP_MIDDLE_FILE = (
    _long("licence body", 420) + "\n"   # 1  over 400 chars on its own: no gluing past it
    "\n"                                 # 2
    "\\*" + "-" * 75 + "*/\n"            # 3  the licence-banner closer, 79 chars
    "\n"                                 # 4
    '#include "fvMesh.H"\n'              # 5
    "\n\n\n"                             # 6-8
    "void after()\n"                     # 9
    "{\n"                                # 10
    "    a();\n"                         # 11
    "}\n"                                # 12
)


def test_interior_separator_merges_into_previous():
    """FR-007a: a rule-only region in the middle joins the PRECEDING block.

    The preceding region exceeds the glue threshold on its own, so gluing cannot
    explain the merge: only the separator rule can. The `#include` region after it
    must stay a separate block, which is what makes "prefer the preceding block"
    observable rather than "absorb in either direction".
    """
    src = _src(*SEP_MIDDLE_FILE.splitlines())
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 3), (5, 5), (9, 12)]
    assert chunks[0]["kind"] == "text"
    assert chunks[0]["text"].split("\n")[-1] == "\\*" + "-" * 75 + "*/"


SEP_HEAD_FILE = (
    "// " + "*" * 74 + "\n"   # 1  a rule with no block before it
    "\n\n\n"                   # 2-4
    "void first()\n"           # 5
    "{\n"                      # 6
    "    f();\n"               # 7
    "}\n"                      # 8
)


def test_separator_without_previous_block_merges_forward():
    """FR-007a: with no preceding block the rule joins the following one.

    A unit follows, so gluing cannot cross the boundary: only the forward branch of
    the separator rule can keep line 1 out of a standalone chunk.
    """
    src = _src(*SEP_HEAD_FILE.splitlines())
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 8)]
    assert chunks[0]["kind"] == "function"
    assert chunks[0]["start_line"] == 1


def test_file_of_only_rules_stays_covered_once():
    """FR-007a/FR-009: with no non-rule neighbour the rules keep one block, not one each."""
    src = _src("// " + "*" * 74, "", "// " + "-" * 74, "")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 3)]
    uncovered = [number for number in range(1, 4) if not any(
        chunk["start_line"] <= number <= chunk["end_line"] for chunk in chunks)]
    assert uncovered == []


def test_rules_absorbed_into_a_unit_never_split_off_alone():
    """FR-007a: the char split must not strand an absorbed rule in a chunk of its own.

    The body fills max_chars to within one rule's width, so greedy packing leaves the
    rule (and the blank line above it) as the whole final chunk — 23 such chunks on the
    corpus until the split started handing lines back to the tail.
    """
    body = [f"    value = compute({index});" for index in range(1, 80)]
    src = _src("void big()", "{", *body, "}", "", "// " + "*" * 74)
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert len(chunks) == 2, _ranges(chunks)
    assert all(len(chunk["text"]) <= 2000 for chunk in chunks)
    assert chunks[-1]["text"].endswith("// " + "*" * 74)
    assert chunks[-1]["text"].startswith("}"), "the rule must share its chunk with code"


@pytest.mark.parametrize("line", [
    "// " + "*" * 74,
    "// " + "-" * 74,
    "/** " + "*" * 20 + " **/",
    "\\*" + "-" * 75 + "*/",
    "    // " + "*" * 74,
])
def test_is_separator_only_accepts_the_section_rule_shapes(line):
    """FR-007a: the four shapes the corpus actually contains (plus surrounding indent)."""
    from foamkb.chunker import is_separator_only

    assert is_separator_only([line], 0, 0) is True


@pytest.mark.parametrize("line", [
    "// a normal comment",
    "const scalar x = 1.0;",
    "// " + "*" * 74 + " trailing words",
    "/*" + "-" * 74 + "*\\",
])
def test_is_separator_only_rejects_lines_with_content(line):
    """FR-007a: the rule spans a whole region, so a line with words is not a rule."""
    from foamkb.chunker import is_separator_only

    assert is_separator_only([line], 0, 0) is False


def test_is_separator_only_requires_every_line_to_be_a_rule():
    """FR-007a: blank lines are allowed inside a span, content anywhere vetoes it."""
    from foamkb.chunker import is_separator_only

    rules = ["// " + "*" * 74, "", "   ", "// " + "-" * 74]

    assert is_separator_only(rules, 0, 3) is True
    assert is_separator_only(rules + ["int x;"], 0, 4) is False
    assert is_separator_only(["", "   "], 0, 1) is False
