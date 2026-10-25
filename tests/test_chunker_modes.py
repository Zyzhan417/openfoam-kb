"""chunker modes: struct path, fixed windows, fallback, argument errors, coverage (T016-T018)."""

from collections import Counter

import pytest

from conftest import _assert_non_overlapping, _ranges, _src
from foamkb.chunker import chunk_source
from foamkb.errors import InvalidArgument


# --- T016: struct normal path -------------------------------------------------

def test_struct_takes_unindented_paren_head_when_next_line_is_bare_brace():
    src = _src("int main(int argc, char **argv)", "{", "  return 0;", "}")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 4)]
    assert chunks[0]["symbol"] == "main"
    assert chunks[0]["kind"] == "function"
    assert chunks[0]["unit_id"] == 1


def test_struct_takes_unindented_line_ending_in_open_brace_as_class():
    src = _src("class Mesh {", "public:", "  int n_;", "};")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 4)]
    assert chunks[0]["kind"] == "class"
    # 002 FR-005 replaced 001 FR-012: the symbol of a class unit is the class name.
    # This assertion used to require None, which encoded the very defect (a class
    # declaration has no '(' so the old rule could never name it) — not a loosening.
    assert chunks[0]["symbol"] == "Mesh"


def test_symbol_is_the_last_identifier_before_the_first_open_paren():
    src = _src("void Foam::fvMesh::update()", "{", "}")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert chunks[0]["symbol"] == "Foam::fvMesh::update"
    assert chunks[0]["kind"] == "function"


def test_include_and_define_lines_neither_count_nor_break_balancing():
    src = _src("void f()", "{", '#include "x.H"', "#define X(a) { a }", "  int y = 0;", "}")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 6)]
    assert chunks[0]["symbol"] == "f"


def test_define_head_followed_by_bare_brace_is_a_macro_unit():
    src = _src("#define BEGIN_FOO(x) \\", "{", "  a = x;", "}")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 4)]
    assert chunks[0]["kind"] == "macro"
    assert chunks[0]["symbol"] == "BEGIN_FOO"


def test_unit_over_max_lines_splits_into_sub_chunks_sharing_unit_id_and_symbol():
    body = [f"  x{i} = {i};" for i in range(24)]
    src = _src("void big()", "{", *body, "}")
    chunks = chunk_source(src, "struct", 10, 8000)["chunks"]

    assert len(chunks) == 3
    assert [chunk["end_line"] - chunk["start_line"] + 1 for chunk in chunks] == [9, 9, 9]
    assert {chunk["unit_id"] for chunk in chunks} == {1}
    assert {chunk["symbol"] for chunk in chunks} == {"big"}
    assert _ranges(chunks) == [(1, 9), (10, 18), (19, 27)]


def test_struct_chunks_are_ascending_and_cover_gaps_as_text():
    # Three blank lines between the header and the unit: 002 FR-004 absorbs a comment
    # block that is within 2 blank lines of the head, so a closer header would (rightly)
    # stop being a gap. This fixture keeps the gap shape the test is about.
    src = _src("// header", "", "", "", "void f()", "{", "  a();", "}", "", "// trailer")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 1), (5, 8), (10, 10)]
    _assert_non_overlapping(chunks)
    assert [chunk["kind"] for chunk in chunks] == ["text", "function", "text"]
    assert chunks[0]["unit_id"] is None


def test_chunk_text_is_the_verbatim_join_of_its_line_range():
    src = _src("void f()", "{", "  int a = 1;", "", "}")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert chunks[0]["text"] == "void f()\n{\n  int a = 1;\n\n}"
    for chunk in chunks:
        start, end = chunk["start_line"], chunk["end_line"]
        assert chunk["text"] == "\n".join(src.lines[start - 1:end])


def test_chunk_text_keeps_leading_indent_and_trailing_whitespace():
    """A gap span can start indented and end with trailing spaces.

    Without this case, ``.strip()`` on the joined text still passes the verbatim
    test above (the fixture there begins and ends on unindented, unpadded lines),
    so the trim that Constitution I forbids would go unnoticed.
    """
    src = _src("int f()", "{", "}", "    loose();   ")
    tail = chunk_source(src, "struct", 150, 2000)["chunks"][-1]

    assert tail["text"].startswith("    loose()")
    assert tail["text"].endswith("loose();   ")
    assert tail["text"] == "\n".join(src.lines[tail["start_line"] - 1:tail["end_line"]])


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_blank_only_file_produces_no_chunks(mode):
    src = _src("   ", "\t", "", "  \t ", "")

    assert chunk_source(src, mode, 150, 2000)["chunks"] == []


# --- T017: fixed path and struct fallback -------------------------------------

def test_fixed_mode_windows_120_lines_from_line_one_without_overlap():
    src = _src(*[f"l{i}" for i in range(1, 251)])
    chunks = chunk_source(src, "fixed", 150, 8000)["chunks"]

    assert _ranges(chunks) == [(1, 120), (121, 240), (241, 250)]
    _assert_non_overlapping(chunks)
    assert {chunk["kind"] for chunk in chunks} == {"lines"}
    assert {chunk["symbol"] for chunk in chunks} == {None}
    assert {chunk["unit_id"] for chunk in chunks} == {None}


@pytest.mark.parametrize("line_count, expected", [(0, 0), (1, 1), (119, 1),
                                                 (120, 1), (121, 2), (240, 2), (241, 3)])
def test_fixed_block_count_follows_line_count(line_count, expected):
    src = _src(*[f"l{i}" for i in range(line_count)])

    assert len(chunk_source(src, "fixed", 150, 8000)["chunks"]) == expected


def test_unbalanced_braces_fall_back_to_fixed_for_the_whole_file():
    body = [f"  a{i} = {i};" for i in range(125)]
    src = _src("void broken()", "{", *body)
    result = chunk_source(src, "struct", 150, 8000)
    chunks = result["chunks"]

    assert _ranges(chunks) == [(1, 120), (121, 127)]
    assert {chunk["kind"] for chunk in chunks} == {"lines"}
    assert {chunk["symbol"] for chunk in chunks} == {None}
    assert {chunk["unit_id"] for chunk in chunks} == {None}
    fallback = [w for w in result["warnings"] if w["reason"] == "struct_fallback"]
    assert len(fallback) == 1
    assert fallback[0]["path"] == "case.C"
    assert fallback[0]["chunks"] == 2


# --- T018: argument errors, long lines, missing symbols, ranges ---------------

def test_unknown_mode_raises_invalid_argument():
    with pytest.raises(InvalidArgument) as exc:
        chunk_source(_src("void f()"), "chars", 150, 2000)

    assert type(exc.value) is InvalidArgument
    assert exc.value.code == "INVALID_ARGUMENT"


def test_max_lines_zero_raises_invalid_argument():
    with pytest.raises(InvalidArgument) as exc:
        chunk_source(_src("void f()"), "struct", 0, 2000)

    assert type(exc.value) is InvalidArgument
    assert exc.value.code == "INVALID_ARGUMENT"


def test_negative_max_lines_raises_invalid_argument():
    with pytest.raises(InvalidArgument) as exc:
        chunk_source(_src("void f()"), "fixed", -3, 2000)

    assert type(exc.value) is InvalidArgument
    assert exc.value.code == "INVALID_ARGUMENT"


def test_max_chars_below_lower_bound_raises_invalid_argument():
    with pytest.raises(InvalidArgument) as exc:
        chunk_source(_src("void f()"), "struct", 150, 199)

    assert type(exc.value) is InvalidArgument
    assert exc.value.code == "INVALID_ARGUMENT"


def test_max_chars_above_upper_bound_raises_invalid_argument():
    with pytest.raises(InvalidArgument) as exc:
        chunk_source(_src("void f()"), "fixed", 150, 8001)

    assert type(exc.value) is InvalidArgument
    assert exc.value.code == "INVALID_ARGUMENT"


def test_line_longer_than_max_chars_is_one_chunk_with_a_warning():
    long_line = "x" * 300
    src = _src("short", long_line, "short2")
    result = chunk_source(src, "fixed", 150, 200)
    chunks = result["chunks"]

    assert _ranges(chunks) == [(1, 1), (2, 2), (3, 3)]
    assert chunks[1]["text"] == long_line
    assert len(result["warnings"]) == 1
    assert result["warnings"][0]["reason"] == "line_too_long"
    assert result["warnings"][0]["line"] == 2
    assert result["warnings"][0]["chars"] == 300


def test_unit_without_parsable_symbol_reports_none_instead_of_a_fake_symbol():
    src = _src("{", "  a();", "}")
    chunks = chunk_source(src, "struct", 150, 2000)["chunks"]

    assert _ranges(chunks) == [(1, 3)]
    assert chunks[0]["symbol"] is None


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_every_chunk_range_is_positive_and_within_the_file(mode):
    src = _src(*[f"line {i}" for i in range(1, 41)])
    chunks = chunk_source(src, mode, 5, 200)["chunks"]

    assert chunks
    for chunk in chunks:
        assert 1 <= chunk["start_line"] <= chunk["end_line"] <= len(src.lines)


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_non_blank_lines_are_covered_exactly_once(mode):
    src = _src("void f() {", "  a();", "}", "", "// tail", "   ", "int x = 0;")
    chunks = chunk_source(src, mode, 150, 2000)["chunks"]
    covered = Counter()

    for chunk in chunks:
        for line_no in range(chunk["start_line"], chunk["end_line"] + 1):
            if src.lines[line_no - 1].strip():
                covered[line_no] += 1

    expected = {i + 1 for i, line in enumerate(src.lines) if line.strip()}
    assert set(covered) == expected
    assert set(covered.values()) == {1}


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_same_input_and_parameters_are_deterministic_across_two_runs(mode):
    body = [f"  x{i} = {i};" for i in range(30)]
    src = _src("void f()", "{", *body, "}", "// tail")

    first = chunk_source(src, mode, 7, 300)
    second = chunk_source(src, mode, 7, 300)

    assert first == second
    assert len(first["chunks"]) > 1
