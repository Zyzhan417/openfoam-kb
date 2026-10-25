"""chunker unit identification: what opens a unit and what its symbol is (FR-001..FR-006)."""

import pytest

from conftest import _assert_non_overlapping, _src
from foamkb.chunker import chunk_source


# --- 002 Phase 1: unit identification (FR-001..FR-006) ------------------------
#
# Fixtures below are built for mutation verification: each one contains the exact
# shape the rule exists for (tasks.md "Fixture 要求"), so breaking the rule turns
# the corresponding test red instead of letting it pass on unrelated structure.

def _units(chunks, kind):
    return [(chunk["start_line"], chunk["end_line"], chunk["symbol"])
            for chunk in chunks if chunk["kind"] == kind]


NAMESPACE_FILE = (
    "// namespace wrapper\n"      # 1
    "namespace Foam\n"            # 2
    "{\n"                          # 3
    "\n"                           # 4
    "void alpha()\n"               # 5
    "{\n"                          # 6
    "    a();\n"                   # 7
    "}\n"                          # 8
    "\n"                           # 9
    "void beta()\n"                # 10
    "{\n"                          # 11
    "    b();\n"                   # 12
    "}\n"                          # 13
    "\n"                           # 14
    "} // namespace Foam\n"        # 15
)


def test_bare_brace_is_not_a_unit_head():
    """FR-001: `namespace Foam` followed by a lone `{` must not swallow the body.

    The bare `{` used to be a unit head, so the whole namespace body became one
    symbol-less text chunk and the two functions inside were unreachable. The fixture
    must keep the namespace form and two functions in the body: without them the bare
    brace rule is never exercised and this test cannot fail.
    """
    chunks = chunk_source(_src(*NAMESPACE_FILE.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "function") == [(5, 8, "alpha"), (10, 13, "beta")]
    brace_block = [chunk for chunk in chunks if chunk["start_line"] <= 3 <= chunk["end_line"]]
    assert len(brace_block) == 1, f"line 3 must belong to exactly one chunk, got {brace_block}"
    assert brace_block[0]["kind"] == "text", \
        f"the bare brace line must stay text, got kind={brace_block[0]['kind']!r}"
    _assert_non_overlapping(chunks)


BASE_LIST_FILE = (
    "class Mesh\n"                  # 1
    ":\n"                           # 2
    "    public Base<Mesh>\n"       # 3
    "{\n"                           # 4
    "public:\n"                     # 5
    "    int n_;\n"                 # 6
    "};\n"                          # 7
)


def test_class_head_opens_unit():
    """FR-002: `class Name` on its own line opens a unit when a `{` follows.

    The base list runs over two lines, so the `{` is on line 4: a rule that only
    accepts heads already ending in `{` cannot see this declaration at all.
    """
    chunks = chunk_source(_src(*BASE_LIST_FILE.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "class") == [(1, 7, "Mesh")]


FORWARD_FILE = (
    "class Forward;\n"              # 1
    "\n"                            # 2
    "void f()\n"                    # 3
    "{\n"                           # 4
    "    g();\n"                    # 5
    "}\n"                           # 6
)

# `class Split\n;` is the same forward declaration with the terminator on the next
# line, so the head line itself does not end in ';' — only the 60-line lookahead sees it.
FORWARD_SPLIT_FILE = (
    "class Split\n"                 # 1
    ";\n"                           # 2
    "\n"                            # 3
    "void f()\n"                    # 4
    "{\n"                           # 5
    "}\n"                           # 6
)

# A preprocessor line before the brace means the body is conditional: not this unit.
FORWARD_HASH_FILE = (
    "class Guarded\n"               # 1
    "#ifdef TRACE\n"                # 2
    "{\n"                           # 3
    "};\n"                          # 4
    "#endif\n"                      # 5
)


@pytest.mark.parametrize("text, function", [
    (FORWARD_FILE, (3, 6, "f")),
    (FORWARD_SPLIT_FILE, (4, 6, "f")),
    (FORWARD_HASH_FILE, None),
])
def test_forward_declaration_is_not_a_unit(text, function):
    """FR-002: a `;`-terminated or `#`-interrupted declaration is not a unit.

    Each fixture fails a different part of the rule: the first via the head line's own
    terminator, the second only via the lookahead (the head line ends with the class
    name), the third only via the `#` veto. A later function exists in the first two
    so that a lookahead which ignores the terminator attaches it to the class instead
    of being invisible for an unrelated reason.
    """
    chunks = chunk_source(_src(*text.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "class") == []
    assert _units(chunks, "function") == ([] if function is None else [function])


SINGLE_LINE_NAMESPACE_FILE = (
    "namespace Foam {\n"            # 1
    "\n"                            # 2
    "void inner()\n"                # 3
    "{\n"                           # 4
    "    g();\n"                    # 5
    "}\n"                           # 6
    "\n"                            # 7
    "} // namespace Foam\n"         # 8
)


def test_namespace_head_is_not_a_unit():
    """FR-002/D8: `namespace Foam {` on one line is not a unit head either.

    This is the second namespace shape: it ends in `{`, so the old brace rule already
    accepted it, and the whole body disappeared into one symbol-less chunk.
    """
    chunks = chunk_source(_src(*SINGLE_LINE_NAMESPACE_FILE.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "function") == [(3, 6, "inner")]
    assert [chunk for chunk in chunks if chunk["start_line"] == 1][0]["kind"] == "text"


TEMPLATE_FILE = (
    "template<class Type>\n"        # 1
    "class Carrier\n"               # 2
    "{\n"                           # 3
    "public:\n"                     # 4
    "    Type v_;\n"                # 5
    "};\n"                          # 6
)

TEMPLATE_BLANK_FILE = (
    "template<class Type>\n"        # 1
    "\n"                            # 2
    "class Carrier\n"               # 3
    "{\n"                           # 4
    "public:\n"                     # 5
    "    Type v_;\n"                # 6
    "};\n"                          # 7
)


@pytest.mark.parametrize("text, end_line", [(TEMPLATE_FILE, 6), (TEMPLATE_BLANK_FILE, 7)])
def test_template_line_joins_unit_head(text, end_line):
    """FR-003: the unit starts at the `template<...>` line, also across one blank line.

    Asserting only `symbol == Carrier` would pass with the head left on the class
    line, so the start_line is the assertion that matters.
    """
    chunks = chunk_source(_src(*text.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "class") == [(1, end_line, "Carrier")]


BANNER_FILE = (
    "/*---------------------------------------------------------------------------*\\\n"  # 1
    "|  Class Foo Declaration                                                    |\n"    # 2
    "\\*---------------------------------------------------------------------------*/\n"   # 3
    "\n"                                                                                 # 4
    "class Foo\n"                                                                        # 5
    "{\n"                                                                                # 6
    "public:\n"                                                                          # 7
    "    int n_;\n"                                                                      # 8
    "};\n"                                                                               # 9
)


def test_block_comment_banner_joins_unit():
    """FR-004: a `/* */` banner above the head is part of the class unit.

    Line 2 carries no comment prefix at all (`|  Class Foo Declaration |` is a line
    inside an open block comment). Judging comments by the first character of each
    line marks it as code and the banner is never absorbed.
    """
    chunks = chunk_source(_src(*BANNER_FILE.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "class") == [(1, 9, "Foo")]
    assert "Class Foo Declaration" in chunks[0]["text"]
    assert chunks[0]["text"] == "\n".join(BANNER_FILE.splitlines())


SPLIT_BANNER_FILE = (
    "/*---------------------------------------------------------------------------*\\\n"  # 1
    "\n"                                                                                 # 2
    "  Class Foo Declaration\n"                                                          # 3
    "\n"                                                                                 # 4
    "\\*---------------------------------------------------------------------------*/\n"   # 5
    "\n"                                                                                 # 6
    "class Foo\n"                                                                        # 7
    "{\n"                                                                                # 8
    "};\n"                                                                               # 9
)


def test_comment_opens_in_previous_span():
    """FR-004: the mask is whole-file, so a block opened earlier closes here.

    The block opens on line 1 and the blank lines split it into three gap spans.
    Line 5 (`\\*---*/`) only reads as a comment because the mask knows the block is
    still open; a per-line judge sees a stray backslash line and stops absorbing,
    which leaves the `Class Foo Declaration` line outside the unit.
    """
    chunks = chunk_source(_src(*SPLIT_BANNER_FILE.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "class") == [(1, 9, "Foo")]
    assert "Class Foo Declaration" in chunks[0]["text"]


LICENSE_FILE = (
    "/*---------------------------------------------------------------------------*\\\n"  # 1
    "|  This file is part of OpenFOAM, distributed under the GNU licence.         |\n"    # 2
    "\\*---------------------------------------------------------------------------*/\n"   # 3
    "\n"                                                                                 # 4
    "\n"                                                                                 # 5
    "\n"                                                                                 # 6
    "class Foo\n"                                                                        # 7
    "{\n"                                                                                # 8
    "};\n"                                                                               # 9
)


def test_license_block_not_absorbed():
    """FR-004: three blank lines (> BANNER_MAX_BLANK) keep the banner out of the unit."""
    chunks = chunk_source(_src(*LICENSE_FILE.splitlines()), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "class") == [(7, 9, "Foo")]


def test_class_symbol_is_class_name():
    """FR-005: the symbol is the name after the keyword, template args removed.

    A class declaration has no `(`, so the 001 rule (last identifier before `(`)
    returned nothing or something unrelated for both shapes below.
    """
    plain = chunk_source(_src("class Mesh", "{", "};"), "struct", 150, 2000)["chunks"]
    templated = chunk_source(_src("class Mesh<Type>", "{", "};"), "struct", 150, 2000)["chunks"]

    assert _units(plain, "class") == [(1, 3, "Mesh")]
    assert _units(templated, "class") == [(1, 3, "Mesh")]


@pytest.mark.parametrize("head, expected", [
    ("class Foam::CarrierField<Type>::Functor", "Functor"),
    ("class Foam::dictionary", "dictionary"),
    ("struct Foam::meshTools::boundBox", "boundBox"),
])
def test_scoped_class_symbol_takes_last_segment(head, expected):
    """FR-005/D9: a qualified name keeps only its last `::` segment (50 such heads).

    Taking the first segment yields the namespace name `Foam`, which is what SC-013
    forbids: 50 class units would claim to be the namespace.
    """
    chunks = chunk_source(_src(head, "{", "};"), "struct", 150, 2000)["chunks"]

    assert _units(chunks, "class") == [(1, 3, expected)]


CONTROL_FLOW_FILE = (
    "for (label i = 0; i < n; i++)\n"   # 1
    "{\n"                               # 2
    "    total += x[i];\n"              # 3
    "}\n"                               # 4
    "\n"                                # 5
    "void realFunction()\n"             # 6
    "{\n"                               # 7
    "    y();\n"                        # 8
    "}\n"                               # 9
)


def test_control_keyword_is_not_a_symbol():
    """FR-006: `for (...)` + `{` is not a symbol, and kind must not be `function`.

    A real function follows it: without one, a rule that stops extracting symbols
    anywhere would also look "fixed" (tasks.md fixture requirement for T008).
    """
    chunks = chunk_source(_src(*CONTROL_FLOW_FILE.splitlines()), "struct", 150, 2000)["chunks"]
    loop = [chunk for chunk in chunks if chunk["start_line"] == 1][0]

    assert loop["symbol"] is None, f"a control-flow keyword must not be a symbol, got {loop['symbol']!r}"
    assert loop["kind"] == "text", f"kind must not be function for a keyword head, got {loop['kind']!r}"
    assert _units(chunks, "function") == [(6, 9, "realFunction")]


# Every macro the OpenFOAM source actually `#define`s with a forAll* prefix. Listed
# explicitly (not derived at test time) so a corpus swap cannot silently shrink the set.
FOR_ALL_MACROS = (
    "forAll", "forAllIter", "forAllConstIter", "forAllReverse",
    "forAllStarEdges", "forAllStarFaces",
)


def test_openfoam_loop_macros_are_not_symbols():
    """FR-006: OpenFOAM's forAll* loop macros expand to `for` and must not become symbols.

    Corpus evidence: before this rule, `forAll` produced 3,726 symbol rows and
    `forAllConstIter` 227, so queries for those names matched thousands of unrelated
    blocks through the exact-symbol channel. Same defect class as FR-006's keywords.
    """
    for macro in FOR_ALL_MACROS:
        source = _src(f"{macro}(celli, mesh.cells())", "{", "    use(celli);", "}", "",
                      "void realFunction()", "{", "    y();", "}")
        chunks = chunk_source(source, "struct", 150, 2000)["chunks"]
        head = [chunk for chunk in chunks if chunk["start_line"] == 1][0]

        assert head["symbol"] is None, f"{macro} must not be a symbol, got {head['symbol']!r}"
        assert head["kind"] == "text", f"{macro}: kind must not be function, got {head['kind']!r}"
        assert _units(chunks, "function") == [(6, 9, "realFunction")], \
            f"{macro}: the real function after it must still be extracted"
