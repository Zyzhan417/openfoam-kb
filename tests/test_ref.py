"""Ref tests: <rev>:<path>::<start_line> round-trip and strict NotFound discipline (FR-023)."""

import pytest

from foamkb import index
from foamkb.config import build_config
from foamkb.errors import NotFound
from foamkb.search import make_ref, resolve_ref, search

NUT = """Foam::scalar Foam::alpha::calcNut()
{
    const scalar alphaOne = 1.0;
    return alphaOne;
}
"""
TWIN = """Foam::scalar Foam::omega::firstTwin()
{
    return firstValue;
}

Foam::scalar Foam::omega::secondTwin()
{
    return secondValue;
}
"""

CORPUS = {"src/alpha/nutWall.C": NUT, "src/omega/twin.C": TWIN}
TWIN_PATH = "src/omega/twin.C"
NUT_PATH = "src/alpha/nutWall.C"


@pytest.fixture
def reader(tmp_path, corpus):
    root = corpus(CORPUS)
    cfg = build_config(root, str(tmp_path / "kb.sqlite"), ("**/*",))
    index.build(cfg)
    return index.open_reader(cfg.index_path)


def _chunk(reader, path, start_line):
    found = [chunk for chunk in (reader.get_chunk(cid)
                                 for cid in range(1, int(reader.meta("chunk_count")) + 1))
             if chunk["path"] == path and chunk["start_line"] == start_line]
    assert len(found) == 1, (path, start_line)
    return found[0]


def _expect_not_found(call, needle):
    with pytest.raises(NotFound) as info:
        call()
    assert type(info.value) is NotFound
    assert info.value.code == "NOT_FOUND"
    assert needle in str(info.value)


def test_make_ref_is_rev_path_and_start_line(reader):
    ref = make_ref(reader, _chunk(reader, TWIN_PATH, 6))
    assert ref == f"{reader.rev()}:{TWIN_PATH}::6"
    assert ref.count("::") == 1


def test_resolve_ref_returns_the_chunk_the_ref_was_made_from(reader):
    chunk = _chunk(reader, TWIN_PATH, 6)
    assert resolve_ref(reader, make_ref(reader, chunk)) == chunk


def test_a_ref_taken_from_a_hit_round_trips_to_the_same_chunk(reader):
    hit = search(reader, "firstTwin")[0]
    chunk = resolve_ref(reader, hit.ref)
    assert (chunk["path"], chunk["start_line"]) == (hit.path, hit.start_line)
    assert chunk == _chunk(reader, TWIN_PATH, 1)


def test_resolve_ref_rejects_a_stale_rev_without_returning_a_neighbour(reader):
    start = _chunk(reader, TWIN_PATH, 6)["start_line"]
    stale = f"deadbeef:{TWIN_PATH}::{start}"
    _expect_not_found(lambda: resolve_ref(reader, stale), "deadbeef")
    # Only the rev differed, yet the same path/line resolves under the current rev.
    assert resolve_ref(reader, f"{reader.rev()}:{TWIN_PATH}::{start}")["start_line"] == start


@pytest.mark.parametrize("start_line", [2, 5, 7, 999])
def test_resolve_ref_rejects_a_line_that_starts_no_chunk(reader, start_line):
    _expect_not_found(lambda: resolve_ref(reader, f"{reader.rev()}:{TWIN_PATH}::{start_line}"),
                      TWIN_PATH)


def test_resolve_ref_rejects_an_unknown_path(reader):
    _expect_not_found(lambda: resolve_ref(reader, f"{reader.rev()}:src/ghost/missing.C::1"),
                      "src/ghost/missing.C")


@pytest.mark.parametrize("ref", [
    "", "abc", "rev-only", "::1", "src/omega/twin.C::1",
    f"{{rev}}:{TWIN_PATH}", f"{{rev}}:{TWIN_PATH}::0", f"{{rev}}:{TWIN_PATH}::-6",
    f"{{rev}}:{TWIN_PATH}::six", f"{{rev}}::{TWIN_PATH}::1", "{rev}:::1",
])
def test_resolve_ref_rejects_malformed_refs(reader, ref):
    ref = ref.replace("{rev}", reader.rev())
    with pytest.raises(NotFound) as info:
        resolve_ref(reader, ref)
    assert type(info.value) is NotFound
    assert info.value.code == "NOT_FOUND"
    assert ref in str(info.value)


def test_resolve_ref_rejects_a_non_string_ref(reader):
    _expect_not_found(lambda: resolve_ref(reader, None), "")
