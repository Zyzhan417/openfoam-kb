"""004 FR-002/FR-004: the symbol channel's per-token fallback only retries identifier shapes.

Every corpus below is built so the block under test is reachable **only** through the
symbol channel: its ``symbol`` carries the word, its body carries neither query token.
That precondition is asserted inside each test against the lexical channel directly, so a
fixture that drifts back to "the word is also in the text" fails loudly instead of
silently passing an implementation that never used the symbol channel at all (the 002
accident this file exists to prevent).
"""

import pytest

from foamkb import index
from foamkb.config import build_config
from foamkb.search import make_ref, search

SYMBOL_LIMIT = 10
LEXICAL_LIMIT = 50
RRF_K = 60


@pytest.fixture
def build_reader(tmp_path, corpus):
    """Build an index over `files` with the shared `corpus` fixture and open a reader."""

    def _build(files):
        root = corpus(files)
        cfg = build_config(root, str(tmp_path / "kb.sqlite"), ("**/*",))
        index.build(cfg)
        return index.open_reader(cfg.index_path)

    return _build


def _function(head, body_lines):
    """A source file the chunker reads as one function unit with `head`'s symbol."""
    lines = "".join(f"    {line}\n" for line in body_lines)
    return f"{head}\n{{\n{lines}}}\n"


def _phrases(query):
    """The FTS5 expression `search._match_expression` builds, for precondition checks."""
    return " ".join('"' + token.replace('"', '""') + '"' for token in query.split())


def _chunk_id(reader, path):
    """The single chunk id stored for `path`."""
    found = [cid for cid in range(1, int(reader.meta("chunk_count")) + 1)
             if (reader.get_chunk(cid) or {})["path"] == path]
    assert len(found) == 1, (path, found)
    return found[0]


def _fillers(folder, names, prose, head=""):
    """Blocks whose body holds the query words, so the lexical channel has a ranked list."""
    return {f"{folder}/{name}.C":
            _function(f"Foam::tmp Foam::alpha::{head}{name}()",
                      [f"// {prose} for the {name} case",
                       "const scalar valueOne = 1.0;", "return valueOne;"])
            for name in names}


# --- US1: a prose query must not be hijacked by a symbol of the same word ----------

PROSE_QUERY = "incompressible turbulence"
PROSE_TARGET = "src/alpha/prose.C"


def _prose_corpus():
    """Target symbol is `incompressible`; its body holds neither `incompressible` nor `turbulence`."""
    files = {PROSE_TARGET:
             _function("Foam::scalar Foam::alpha::incompressible()",
                       ["const scalar densityScale = 1.0;", "return densityScale;"])}
    files.update(_fillers("src/beta", ("kOmega", "kEpsilon", "Smagorinsky", "LaunderSharma"),
                          "incompressible turbulence closure"))
    return files


def test_prose_query_does_not_use_the_symbol_channel(build_reader):
    """SC-007 / FR-003: an all-lowercase token never triggers the per-token fallback.

    The target block is the trap: a corpus symbol literally named `incompressible`, the
    same as a word in the query. Under the old "retry every token" loop it entered the
    results at symbol rank 1; now it must not appear at all.
    """
    reader = build_reader(_prose_corpus())
    target = _chunk_id(reader, PROSE_TARGET)
    # Preconditions: the block is real, and only the symbol table can reach it here.
    assert reader.find_symbols(PROSE_QUERY, SYMBOL_LIMIT) == [], \
        "the fixture fails to model 'whole query misses the symbol table'"
    assert reader.find_symbols("incompressible", SYMBOL_LIMIT) == [target], \
        "the fixture must expose the target through the token `incompressible`"
    assert target not in reader.search_lexical(_phrases(PROSE_QUERY), LEXICAL_LIMIT), \
        "the target's body must not match lexically, else this test proves nothing"

    hits = search(reader, PROSE_QUERY, top_k=20)
    assert hits, "the lexical channel must still answer the prose query"
    assert PROSE_TARGET not in [hit.path for hit in hits], \
        "a prose token must not pull in a same-named symbol"


# --- US2: a mixed query must keep using the symbol channel ------------------------

MIXED_QUERY = "fvMesh topology"
MIXED_TARGET = "src/beta/mesh.C"


def _mixed_corpus():
    """Target symbol is `fvMesh`; its body holds neither `fvMesh` nor `topology`."""
    files = {MIXED_TARGET:
             _function("Foam::fvMesh::fvMesh()",
                       ["const scalar cellCount = 4.0;", "return cellCount;"])}
    files.update(_fillers("src/alpha", ("polyMesh", "objectRegistry", "dictionary", "IOobject"),
                          "fvMesh topology walk", head=""))
    return files


def test_mixed_query_still_uses_the_symbol_channel(build_reader):
    """SC-008 / FR-007: an identifier token must still fall back to the symbol table.

    Together with the prose test above this pins both directions: only `fvMesh`-style
    tokens retry, and they do retry. Without this test, an implementation that drops the
    fallback entirely would pass the prose test.

    The target is asserted through its *score*, not its position: a symbol-only hit earns
    exactly the channel's rank-1 term, whereas a top-ranked lexical filler earns the same
    number and the two are then ordered by path. Position would be a tie-break artefact;
    the score says which channel supplied the block.
    """
    reader = build_reader(_mixed_corpus())
    target = _chunk_id(reader, MIXED_TARGET)
    assert reader.find_symbols(MIXED_QUERY, SYMBOL_LIMIT) == []
    assert reader.find_symbols("fvMesh", SYMBOL_LIMIT) == [target]
    assert target not in reader.search_lexical(_phrases(MIXED_QUERY), LEXICAL_LIMIT), \
        "the target's body must not match lexically, else this test proves nothing"

    hits = search(reader, MIXED_QUERY, top_k=20)
    paths = [hit.path for hit in hits]
    assert MIXED_TARGET in paths, "the identifier token must still reach the symbol table"
    assert hits[paths.index(MIXED_TARGET)].score == pytest.approx(1 / (RRF_K + 1), abs=1e-12), \
        "a symbol-only hit must carry the symbol channel's rank-1 term"


# --- T004 edge cases, each asserted on its own ------------------------------------

TIME_QUERY = "Time controlDict"
TIME_TARGET = "src/alpha/time.C"


def _time_corpus():
    """Decision D's regression: `Time` is a real class name and must stay a candidate."""
    files = {TIME_TARGET:
             _function("Foam::Time::Time()",
                       ["const scalar deltaT = 0.5;", "return deltaT;"])}
    files.update(_fillers("src/beta", ("timeControl", "dictionary", "regIOobject", "objectRegistry"),
                          "Time controlDict reading", head=""))
    return files


def test_capitalised_class_token_still_falls_back(build_reader):
    """D: a capital anywhere counts, first letter included.

    The narrower "capital after the first character" rule was measured and rejected: it
    took `Time controlDict` from rank 1 to out of the top 20, because `Time` is the symbol
    that carries this query. This case is the guard against reinstating it.
    """
    reader = build_reader(_time_corpus())
    target = _chunk_id(reader, TIME_TARGET)
    assert reader.find_symbols(TIME_QUERY, SYMBOL_LIMIT) == []
    assert reader.find_symbols("Time", SYMBOL_LIMIT) == [target]
    assert target not in reader.search_lexical(_phrases(TIME_QUERY), LEXICAL_LIMIT)

    paths = [hit.path for hit in search(reader, TIME_QUERY, top_k=20)]
    assert paths[:1] == [TIME_TARGET], f"`Time` must still resolve through the symbol channel, got {paths}"
    assert search(reader, TIME_QUERY, top_k=20)[0].score == pytest.approx(1 / (RRF_K + 1), abs=1e-12), \
        "`Time` must arrive as the symbol channel's rank-1 term"


SOLE_QUERY = "kOmegaSST"
SOLE_TARGET = "src/beta/omega.C"


def _sole_corpus():
    files = {SOLE_TARGET:
             _function("Foam::tmp Foam::beta::kOmegaSST()",
                       ["const scalar betaTwo = 2.0;", "return betaTwo;"])}
    files.update(_fillers("src/alpha", ("kOmega", "kEpsilon", "turbulenceModel"),
                          "kOmegaSST coefficient", head=""))
    return files


def test_single_token_query_is_answered_by_the_whole_string_path_alone(build_reader, monkeypatch):
    """Edge case ②: one token means the whole-string path already covers it — no retry.

    Asserts both the call trace (exactly one symbol lookup, with the whole query) and the
    result set (exactly what the two channels return for that same string), so an
    implementation that retried the single token again, or retried nothing, is caught.
    """
    reader = build_reader(_sole_corpus())
    seen, real = [], reader.find_symbols

    def record(term, limit, path_prefix=None):
        seen.append(term)
        return real(term, limit, path_prefix)

    monkeypatch.setattr(reader, "find_symbols", record)
    hits = search(reader, SOLE_QUERY, top_k=20)
    assert seen == [SOLE_QUERY], f"a one-token query must be looked up once, as given; got {seen}"
    expected = {make_ref(reader, reader.get_chunk(cid)) for cid in
                real(SOLE_QUERY, SYMBOL_LIMIT) + reader.search_lexical(_phrases(SOLE_QUERY), LEXICAL_LIMIT)}
    assert {hit.ref for hit in hits} == expected, "the result set must come from both channels, unchanged"


PREFIX_QUERY = "fvMesh topology"
PREFIX_INSIDE = "src/beta/mesh.C"
PREFIX_OUTSIDE = "src/alpha/mesh.C"


def _prefix_corpus():
    """Two blocks share the symbol leaf `fvMesh`; only one is inside the prefix."""
    files = {
        PREFIX_OUTSIDE: _function("Foam::fvMesh::fvMesh()",
                                  ["const scalar faceCount = 9.0;", "return faceCount;"]),
        PREFIX_INSIDE: _function("Foam::tmp Foam::beta::fvMesh()",
                                 ["const scalar cellCount = 4.0;", "return cellCount;"]),
    }
    files.update(_fillers("src/beta", ("polyMesh", "objectRegistry", "dictionary"),
                          "fvMesh topology walk", head=""))
    return files


def test_path_prefix_is_pushed_into_the_fallback_query(build_reader):
    """FR-004: the per-token retry carries `path_prefix` too, not just the whole string.

    Both blocks answer the token `fvMesh`. If the retry dropped the prefix, the block
    outside `src/beta` would come back with it.
    """
    reader = build_reader(_prefix_corpus())
    inside, outside = _chunk_id(reader, PREFIX_INSIDE), _chunk_id(reader, PREFIX_OUTSIDE)
    assert set(reader.find_symbols("fvMesh", SYMBOL_LIMIT)) == {inside, outside}
    assert outside not in reader.search_lexical(_phrases(PREFIX_QUERY), LEXICAL_LIMIT), \
        "the outside block must not enter through the lexical channel"

    paths = [hit.path for hit in search(reader, PREFIX_QUERY, top_k=20, path_prefix="src/beta")]
    assert PREFIX_INSIDE in paths, "the prefixed fallback must still find the in-prefix symbol"
    assert PREFIX_OUTSIDE not in paths, "the retry dropped path_prefix"
    assert all(path.startswith("src/beta/") for path in paths), f"hits escaped the prefix: {paths}"
