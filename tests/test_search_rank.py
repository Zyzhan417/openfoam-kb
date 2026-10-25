"""search ranking: two-channel RRF fusion, ordering, ref and preview truncation (FR-020..FR-022)."""

import pytest

from conftest import SEARCH_FILES
from foamkb import index
from foamkb.config import build_config
from foamkb.search import search

RRF_K = 60
ALPHA = "src/alpha/nutWall.C"
MESH_PATH = "src/beta/mesh.C"
TWIN_PATH = "src/omega/twin.C"
LONG = SEARCH_FILES["src/zeta/longFile.C"]


@pytest.fixture
def reader(search_reader):
    """The shared search reader; the tests below name the fixture ``reader``."""
    return search_reader


def _chunk(reader, path, start_line=None):
    """Return the stored chunk dict for one path (unique at the given start line)."""
    found = [chunk for chunk in (reader.get_chunk(cid)
                                 for cid in range(1, int(reader.meta("chunk_count")) + 1))
             if chunk["path"] == path and start_line in (None, chunk["start_line"])]
    assert len(found) == 1, (path, start_line, len(found))
    return found[0]


def test_hit_carries_every_documented_field_and_both_channel_rrf_score(reader):
    hits = search(reader, "calcNut")
    assert len(hits) == 1
    hit = hits[0]
    chunk = _chunk(reader, ALPHA)
    assert hit.path == ALPHA
    assert (hit.start_line, hit.end_line) == (1, 5)
    assert hit.text_start_line == 1
    assert hit.symbol == "Foam::alpha::calcNut"
    assert hit.kind == "function"
    assert hit.unit_id == 1
    assert hit.ref == f"{reader.rev()}:{ALPHA}::1"
    assert hit.truncated is False
    # Rank 1 in the symbol channel and rank 1 in the lexical channel: 1/(60+1) twice.
    assert hit.score == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 1), abs=1e-12)
    assert hit.text == chunk["text"]


def test_score_counts_only_the_channel_that_returned_the_chunk(reader):
    # "alphaOne" is no symbol, so the symbol channel contributes nothing.
    hits = search(reader, "alphaOne")
    assert [hit.path for hit in hits] == [ALPHA]
    assert hits[0].score == pytest.approx(1 / (RRF_K + 1), abs=1e-12)


def test_scores_are_the_hand_computed_rrf_of_the_lexical_ranks(reader):
    hits = search(reader, "scalar", top_k=20)
    assert len(hits) == 8
    expected = [1 / (RRF_K + rank) for rank in range(1, len(hits) + 1)]
    for hit, want in zip(hits, expected):
        assert hit.score == pytest.approx(want, abs=1e-12)


def test_hits_are_sorted_by_score_then_path_then_start_line(reader):
    keys = [(-hit.score, hit.path, hit.start_line) for hit in search(reader, "scalar", top_k=20)]
    assert len(keys) == 8
    assert keys == sorted(keys)


def test_ties_break_by_path(reader, monkeypatch):
    monkeypatch.setattr(reader, "find_symbols",
                        lambda term, limit, path_prefix=None: [_chunk(reader, MESH_PATH)["id"]])
    monkeypatch.setattr(reader, "search_lexical",
                        lambda query, limit, path_prefix=None: [_chunk(reader, ALPHA)["id"]])
    hits = search(reader, "tie")
    assert [hit.path for hit in hits] == [ALPHA, MESH_PATH]
    assert hits[0].score == hits[1].score == pytest.approx(1 / (RRF_K + 1), abs=1e-12)


def test_ties_break_by_start_line_inside_one_path(reader, monkeypatch):
    first, second = _chunk(reader, TWIN_PATH, 1), _chunk(reader, TWIN_PATH, 6)
    monkeypatch.setattr(reader, "find_symbols",
                        lambda term, limit, path_prefix=None: [second["id"]])
    monkeypatch.setattr(reader, "search_lexical",
                        lambda query, limit, path_prefix=None: [first["id"]])
    hits = search(reader, "twin")
    assert [hit.start_line for hit in hits] == [1, 6]
    assert hits[0].score == hits[1].score == pytest.approx(1 / (RRF_K + 1), abs=1e-12)


def test_top_k_cuts_the_ranked_list_without_reordering_it(reader):
    full = search(reader, "scalar", top_k=20)
    cut = search(reader, "scalar", top_k=3)
    assert len(full) == 8, "the corpus must offer more hits than top_k"
    assert len(cut) == 3
    assert [hit.ref for hit in cut] == [hit.ref for hit in full[:3]]


def test_path_prefix_keeps_every_hit_inside_the_prefix(reader):
    hits = search(reader, "scalar", top_k=20, path_prefix="src/alpha")
    assert [hit.path for hit in hits] == [ALPHA]
    assert all(hit.path.startswith("src/alpha/") for hit in hits)
    assert search(reader, "calcNut", path_prefix="src/beta") == []


def test_preview_starts_at_the_line_holding_the_query(reader):
    hit = search(reader, "alphaOne")[0]
    # "alphaOne" first appears on line 3, so the preview opens at that line head.
    assert hit.text.startswith("    const scalar alphaOne = 1.0;")
    assert "Foam::scalar Foam::alpha::calcNut" not in hit.text
    assert (hit.start_line, hit.text_start_line) == (1, 3)
    assert hit.truncated is False


def test_preview_starts_at_the_chunk_head_when_the_literal_is_absent(reader):
    # "sst" matches the camel subword of kOmegaSST, but only "SST" is in the text.
    hits = search(reader, "sst")
    assert [hit.path for hit in hits] == [MESH_PATH]
    assert "sst" not in hits[0].text
    assert hits[0].text.startswith("Foam::tmp Foam::beta::kOmegaSST()")
    assert hits[0].text_start_line == hits[0].start_line == 1


def test_truncation_lands_on_a_line_boundary(reader):
    chunk = _chunk(reader, "src/zeta/longFile.C")
    assert len(chunk["text"]) > 200
    hit = search(reader, "longRun", max_chars=200)[0]
    assert hit.truncated is True
    assert len(hit.text) <= 200
    assert chunk["text"].startswith(hit.text)
    assert not hit.text.endswith("\n")
    assert hit.text_start_line == hit.start_line
    # The last kept line is complete, never a half line.
    assert hit.text.split("\n")[-1] in chunk["text"].split("\n")


def test_a_preview_that_fits_is_not_truncated(reader):
    hit = search(reader, "longRun", max_chars=8000)[0]
    assert hit.truncated is False
    assert hit.text == _chunk(reader, "src/zeta/longFile.C")["text"]


PREVIEW = """Foam::scalar Foam::demo::calcNut()
{
    const scalar otherValue = 0.0;
    const scalar targetValue = 1.0;
}
"""
# One 1895-char line (no line break within the 200-char preview) inside a normal unit.
WIDE = ("Foam::scalar Foam::wide::hugeLine()\n{\n    const int before = 1;\n    targetTail "
        + "z" * 1880 + "\n}\n")


@pytest.fixture
def located(tmp_path, corpus):
    """One reader over the preview corpus plus the verbatim source lines of both files."""
    files = {"src/demo/demo.C": PREVIEW, "src/wide/wide.C": WIDE}
    root = corpus(files)
    cfg = build_config(root, str(tmp_path / "located.sqlite"), ("**/*",))
    index.build(cfg)
    lines = {rel: (root / rel).read_text(encoding="utf-8").split("\n") for rel in files}
    return index.open_reader(cfg.index_path), lines


def test_text_start_line_locates_the_preview_verbatim(located):
    """Constitution I: text must be sliceable from the line numbers shipped with it."""
    reader, lines = located
    hit = search(reader, "targetValue")[0]
    assert (hit.start_line, hit.text_start_line, hit.end_line) == (1, 4, 5)
    assert hit.text.split("\n")[0] == "    const scalar targetValue = 1.0;"
    assert hit.truncated is False
    assert hit.text == "\n".join(lines[hit.path][hit.text_start_line - 1:hit.end_line])


def test_truncated_text_is_a_line_aligned_prefix_of_its_own_window(reader):
    hit = search(reader, "stepA", max_chars=200)[0]
    lines = LONG.split("\n")
    window = "\n".join(lines[hit.text_start_line - 1:hit.end_line])
    assert hit.text_start_line == 3
    assert hit.truncated is True
    assert window.startswith(hit.text)
    kept = hit.text.count("\n") + 1
    assert hit.text == "\n".join(lines[hit.text_start_line - 1:hit.text_start_line - 1 + kept])


def test_a_single_over_long_line_falls_back_to_a_character_cut(located):
    reader, lines = located
    hit = search(reader, "targetTail", max_chars=200)[0]
    window = "\n".join(lines[hit.path][hit.text_start_line - 1:hit.end_line])
    assert hit.truncated is True
    assert len(hit.text) == 200
    assert hit.text_start_line == 4
    assert window.startswith(hit.text)
