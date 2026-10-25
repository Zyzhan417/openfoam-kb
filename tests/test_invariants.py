"""Global invariants over real builds: SC-001 slices, SC-002 coverage, SC-003 rev (FR-007/015/017)."""

import json
import sqlite3
from collections import Counter

import pytest

from foamkb.chunker import chunk_source
from foamkb.config import build_config
from foamkb.index import build, open_reader
from foamkb.loader import iter_sources

KEY_FIELDS = ("path", "start_line", "end_line", "symbol", "kind", "unit_id", "text")


def _function(name, body_lines):
    body = "\n".join(body_lines)
    return f"Foam::scalar Foam::{name}::calc()\n{{\n{body}\n}}\n"


LONG_BODY = [f"    const scalar value{i} = {i}.0;" for i in range(1, 201)]
UNITS = (_function("longRun", LONG_BODY) + "\n" + _function("shortRun", ["    return 7.0;"]))
GAPS = ("// leading comment\n\nconst scalar a = 1.0;\nconst scalar b = 2.0;\n\n\n"
        + _function("gapUnit", ["    return a + b;"]) + "\nafter the unit\n")
BROKEN = "".join(f"    const scalar open{i} = {i}.0;\n" for i in range(1, 201))
LONGLINE = "x" * 2500 + "\nshort tail line\n"
FILES = {
    "src/units.C": UNITS,
    "src/gaps.C": GAPS,
    "src/broken.C": BROKEN,
    "src/longline.C": LONGLINE,
    "src/empty.C": "",
}


def _built(corpus, tmp_path, mode):
    """Build one index over the shared corpus and return (config, index path)."""
    root = corpus(FILES)
    index_path = str(tmp_path / f"kb_{mode}.sqlite")
    cfg = build_config(root, index_path, ("*.C",), chunk_mode=mode)
    build(cfg)
    return cfg, index_path


def _rows(index_path):
    """Every stored chunk in insertion order, read straight from sqlite in the test."""
    conn = sqlite3.connect(index_path)
    try:
        return conn.execute(
            "SELECT path, start_line, end_line, symbol, kind, unit_id, text"
            " FROM chunks ORDER BY id").fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_sc001_every_chunk_text_equals_its_source_slice(corpus, tmp_path, mode):
    """SC-001: 100% of chunks, both modes, text == '\\n'.join(lines[start-1:end])."""
    cfg, index_path = _built(corpus, tmp_path, mode)
    lines_by_path = {src.path: src.lines for src in iter_sources(cfg)}
    rows = _rows(index_path)
    reader = open_reader(index_path)
    expected_count = int(reader.meta("chunk_count"))
    assert expected_count > 0
    checked = 0
    for path, start, end, _symbol, _kind, _unit, text in rows:
        lines = lines_by_path[path]
        assert 1 <= start <= end <= len(lines), (path, start, end)
        assert text == "\n".join(lines[start - 1:end]), (path, start, end)
        checked += 1
    assert checked == len(rows) == expected_count
    # Every file with content must be represented; the empty file must not be.
    assert {row[0] for row in rows} == {path for path in lines_by_path if lines_by_path[path]}


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_sc002_lines_covered_once_and_blocks_disjoint(corpus, tmp_path, mode):
    """SC-002: each non-blank line covered exactly once, no overlap, no block across files."""
    cfg, index_path = _built(corpus, tmp_path, mode)
    lines_by_path = {src.path: src.lines for src in iter_sources(cfg)}
    spanned = {}
    for path, start, end, _symbol, _kind, _unit, text in _rows(index_path):
        assert path in lines_by_path
        assert 1 <= start <= end <= len(lines_by_path[path]), (path, start, end)
        assert text == "\n".join(lines_by_path[path][start - 1:end]), (path, start, end)
        spanned.setdefault(path, []).append((start, end))
    assert spanned
    for path, spans in spanned.items():
        spans.sort()
        counts = Counter()
        previous_end = 0
        for start, end in spans:
            assert start > previous_end, (path, start, previous_end)
            previous_end = end
            counts.update(range(start, end + 1))
        for number, line in enumerate(lines_by_path[path], 1):
            if line.strip():
                assert counts[number] == 1, (path, number)
            else:
                assert counts[number] <= 1, (path, number)


def test_sc002_chunking_is_deterministic_and_reacts_to_max_lines(corpus):
    """SC-002: equal input + equal parameters give equal chunks; halving max_lines differs."""
    root = corpus({"src/units.C": UNITS})
    cfg = build_config(root, str(root / "unused.sqlite"), ("*.C",))
    src = next(iter(iter_sources(cfg)))

    def shape(max_lines):
        result = chunk_source(src, "struct", max_lines, 2000)
        return json.dumps([[chunk[key] for key in KEY_FIELDS] for chunk in result["chunks"]])

    assert shape(150) == shape(150)
    assert shape(150) != shape(75)


def test_sc003_rebuilding_same_corpus_keeps_rev_and_chunk_count(corpus, tmp_path):
    """SC-003: rev is derived from the manifest, so a second build cannot look newer."""
    root = corpus(FILES)
    index_path = str(tmp_path / "kb.sqlite")
    cfg = build_config(root, index_path, ("*.C",))
    first = build(cfg)
    first_rev = open_reader(index_path).rev()
    second = build(cfg)
    reader = open_reader(index_path)
    assert len(first_rev) == 8 and first_rev == first.rev
    assert second.rev == first_rev == reader.rev() == first_rev
    assert second.chunk_count == first.chunk_count == int(reader.meta("chunk_count"))


# --- T011: the four invariants, on a corpus that carries the Phase 2 shapes -----
#
# FILES above predates gap gluing: it has no section rules and no multi-region gap,
# so invariants checked only against it cannot see a gluing or merging bug (a dropped
# region, a duplicated line, a chunk that overlaps its neighbour). These fixtures add
# exactly those shapes: a rule at the head of the file, a gap glued across blank
# lines, a rule merged into the unit above it, and a unit long enough to be split.

PHASE2_RULES = (
    "// " + "*" * 74 + "\n"      # 1  rule with no block before it
    "\n\n\n"                      # 2-4
    "// leading note\n"           # 5  glue region, joined to the rule at line 1
    "\n\n\n"                      # 6-8
    "void plainUnit()\n"          # 9
    "{\n"                         # 10
    "    p();\n"                  # 11
    "}\n"                         # 12
    "\n"                          # 13
    "\\*" + "-" * 75 + "*/\n"     # 14 licence-banner closer: merges into the unit above
)
PHASE2_SPLIT = _function("longRun", LONG_BODY) + "\n// " + "*" * 74 + "\n"
PHASE2_TAIL = "void tailUnit()\n{\n    t();\n}\n\n// a final note below the last unit\n"
PHASE2_FILES = {"src/rules.C": PHASE2_RULES, "src/split.C": PHASE2_SPLIT,
                "src/tail.C": PHASE2_TAIL}


def _built_from(corpus, tmp_path, mode, files, tag):
    """Build one index over a named corpus and return (config, index path)."""
    root = corpus(files)
    index_path = str(tmp_path / f"kb_{tag}_{mode}.sqlite")
    cfg = build_config(root, index_path, ("*.C",), chunk_mode=mode)
    build(cfg)
    return cfg, index_path


def test_unit_span_is_one_chunk_or_contiguous_sub_chunks(corpus, tmp_path):
    """FR-010: a unit's head and closing line share a chunk, or its sub-chunks are contiguous."""
    cfg, index_path = _built_from(corpus, tmp_path, "struct", PHASE2_FILES, "p2units")
    lines_by_path = {src.path: src.lines for src in iter_sources(cfg)}
    groups = {}
    for path, start, end, symbol, kind, unit, text in _rows(index_path):
        assert unit is not None or kind == "text", (path, start, end, kind)
        if unit is not None:
            groups.setdefault((path, unit), []).append((start, end, symbol, kind, text))
    assert len(groups) == 3
    for (path, unit), chunks in groups.items():
        chunks.sort()
        assert len({chunk[2] for chunk in chunks}) == 1, (path, unit, "symbol changed")
        assert len({chunk[3] for chunk in chunks}) == 1, (path, unit, "kind changed")
        for previous, following in zip(chunks, chunks[1:]):
            assert following[0] == previous[1] + 1, (path, unit, previous, following)
        joined = "\n".join(chunk[4] for chunk in chunks)
        assert joined == "\n".join(lines_by_path[path][chunks[0][0] - 1:chunks[-1][1]]), \
            (path, unit, "the unit's lines must be exactly what its chunks hold")
    # One unit fits in a single chunk; the other is cut by max_lines (and again by
    # max_chars), which is the branch where contiguity and one unit_id must hold.
    counts = sorted(len(chunks) for chunks in groups.values())
    assert counts[0] == 1 and counts[-1] > 1, counts


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_every_non_blank_line_is_covered_exactly_once(corpus, tmp_path, mode):
    """FR-009: gluing and merging may move lines between chunks, never lose or copy them."""
    cfg, index_path = _built_from(corpus, tmp_path, mode, PHASE2_FILES, "p2cover")
    lines_by_path = {src.path: src.lines for src in iter_sources(cfg)}
    counts = {path: Counter() for path in lines_by_path}
    for path, start, end, _symbol, _kind, _unit, _text in _rows(index_path):
        counts[path].update(range(start, end + 1))
    assert any(counts[path] for path in counts)
    for path, lines in lines_by_path.items():
        for number, line in enumerate(lines, 1):
            if line.strip():
                assert counts[path][number] == 1, (path, number, counts[path][number])
            else:
                assert counts[path][number] <= 1, (path, number)


@pytest.mark.parametrize("mode", ["struct", "fixed"])
def test_adjacent_chunks_do_not_overlap_and_never_span_files(corpus, tmp_path, mode):
    """FR-009: ranges stay disjoint and in order, and each chunk holds its own file's lines."""
    cfg, index_path = _built_from(corpus, tmp_path, mode, PHASE2_FILES, "p2overlap")
    lines_by_path = {src.path: src.lines for src in iter_sources(cfg)}
    spans = {path: [] for path in lines_by_path}
    for path, start, end, _symbol, _kind, _unit, text in _rows(index_path):
        assert path in lines_by_path
        assert 1 <= start <= end <= len(lines_by_path[path]), (path, start, end)
        # A chunk that had swallowed lines of another file could not equal this slice.
        assert text == "\n".join(lines_by_path[path][start - 1:end]), (path, start, end)
        spans[path].append((start, end))
    assert any(spans.values())
    for path, ranges in spans.items():
        ranges.sort()
        for (first_start, first_end), (next_start, _next_end) in zip(ranges, ranges[1:]):
            assert first_end < next_start, (path, first_start, first_end, next_start)


def test_two_builds_of_the_same_input_give_identical_chunk_tuples(corpus, tmp_path):
    """FR-009/SC-011: a rebuild of equal input repeats every chunk tuple, not just the count."""
    root = corpus(PHASE2_FILES)
    first_path, second_path = str(tmp_path / "p2det_a.sqlite"), str(tmp_path / "p2det_b.sqlite")
    build(build_config(root, first_path, ("*.C",)))
    build(build_config(root, second_path, ("*.C",)))

    first_rows, second_rows = _rows(first_path), _rows(second_path)

    assert first_rows and first_rows == second_rows
    assert open_reader(first_path).rev() == open_reader(second_path).rev()
