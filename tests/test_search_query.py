"""search query handling: tokenisation, FTS escaping, argument validation (FR-020..FR-022)."""

import pytest

from foamkb import index
from foamkb.config import build_config
from foamkb.errors import IndexUnavailable, InternalError, InvalidArgument
from foamkb.search import search

IDENTIFIERS = {
    "calcNut": "src/alpha/nutWall.C",
    "kOmegaSST": "src/beta/mesh.C",
    "deltaField": "src/delta/deltaField.C",
    "epsPatch": "src/epsilon/epsPatch.C",
    "gammaTurb": "tests/unit/unitMesh.C",
}

RRF_K = 60
ALPHA = "src/alpha/nutWall.C"


@pytest.fixture
def reader(search_reader):
    """The shared search reader; the tests below name the fixture ``reader``."""
    return search_reader


def _expect_invalid(call):
    """Assert one call fails with the documented argument error."""
    with pytest.raises(InvalidArgument) as info:
        call()
    assert type(info.value) is InvalidArgument
    assert info.value.code == "INVALID_ARGUMENT"


def test_a_reader_index_error_is_rethrown_as_the_same_instance(reader, monkeypatch):
    failure = IndexUnavailable("index is unreadable: simulated")

    def boom(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(reader, "find_symbols", boom)
    with pytest.raises(IndexUnavailable) as caught:
        search(reader, "calcNut")
    assert caught.value is failure


def test_an_unexpected_reader_failure_becomes_internal_error(reader, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(reader, "find_symbols", boom)
    with pytest.raises(InternalError) as caught:
        search(reader, "calcNut")
    assert type(caught.value) is InternalError
    assert caught.value.code == "INTERNAL_ERROR"


def test_no_hit_returns_an_empty_list_and_raises_nothing(reader):
    assert search(reader, "definitelyAbsentTerm") == []
    assert search(reader, "definitelyAbsentTerm", path_prefix="src") == []


@pytest.mark.parametrize("name, path", sorted(IDENTIFIERS.items()))
def test_each_seeded_identifier_reaches_its_file(reader, name, path):
    hits = search(reader, name, top_k=3)
    assert hits[0].path == path
    assert path in [hit.path for hit in hits[:3]]


def test_channels_are_asked_for_10_symbols_and_50_lexical_hits(reader, monkeypatch):
    calls = []
    real_symbols, real_lexical = reader.find_symbols, reader.search_lexical

    def spy_symbols(term, limit, path_prefix=None):
        calls.append(("symbols", term, limit, path_prefix))
        return real_symbols(term, limit, path_prefix)

    def spy_lexical(query, limit, path_prefix=None):
        calls.append(("lexical", query, limit, path_prefix))
        return real_lexical(query, limit, path_prefix)

    monkeypatch.setattr(reader, "find_symbols", spy_symbols)
    monkeypatch.setattr(reader, "search_lexical", spy_lexical)
    search(reader, "  calcNut \t", path_prefix="src")
    assert calls == [
        ("symbols", "calcNut", 10, "src"),
        # Channel B receives the same tokens, each quoted as an FTS5 phrase.
        ("lexical", '"calcNut"', 50, "src"),
    ]


def test_a_multi_word_query_falls_back_to_single_token_symbol_lookups(reader, monkeypatch):
    terms, real = [], reader.find_symbols

    def spy(term, limit, path_prefix=None):
        terms.append(term)
        return real(term, limit, path_prefix)

    monkeypatch.setattr(reader, "find_symbols", spy)
    hits = search(reader, "nutUSpalding calcNut")
    # The whole query is tried first, then its tokens one by one, first non-empty wins.
    assert terms == ["nutUSpalding calcNut", "nutUSpalding", "calcNut"]
    assert [hit.path for hit in hits] == [ALPHA]
    assert hits[0].score == pytest.approx(1 / (RRF_K + 1), abs=1e-12)


@pytest.mark.parametrize("query", ["", "   ", "\t\n ", "a" * 513, "a" + " " * 700 + "b"])
def test_query_must_be_non_empty_and_at_most_512_characters(reader, query):
    # The limit counts len(query.strip()), so padding cannot smuggle a long query past it.
    _expect_invalid(lambda: search(reader, query))


@pytest.mark.parametrize("query", [None, 5, b"calcNut", ["calcNut"]])
def test_query_must_be_a_string(reader, query):
    _expect_invalid(lambda: search(reader, query))


@pytest.mark.parametrize("top_k", [0, 21, -1, "5", 2.0, None, True])
def test_top_k_must_be_an_integer_within_1_and_20(reader, top_k):
    _expect_invalid(lambda: search(reader, "calcNut", top_k=top_k))


@pytest.mark.parametrize("max_chars", [199, 8001, "1500", 0, None])
def test_max_chars_must_be_within_200_and_8000(reader, max_chars):
    _expect_invalid(lambda: search(reader, "calcNut", max_chars=max_chars))


@pytest.mark.parametrize("prefix", ["/src", "src/../etc", "src/", "", "src/..", "../src"])
def test_path_prefix_must_be_a_relative_directory(reader, prefix):
    _expect_invalid(lambda: search(reader, "calcNut", path_prefix=prefix))


OPERATOR_CORPUS = {
    "src/ops/guard.C": """struct opsGuard {
    // clamp wall-2.0 down to nut-2.0 before use
    scalar guardValue = 1.0;
};
""",
}

OPERATOR_QUERIES = ["-calcNut", '"calcNut', "calcNut OR", "alpha*"]


@pytest.fixture
def operator_reader(tmp_path, corpus):
    root = corpus(OPERATOR_CORPUS)
    cfg = build_config(root, str(tmp_path / "ops.sqlite"), ("**/*",))
    index.build(cfg)
    return index.open_reader(cfg.index_path)


@pytest.mark.parametrize("query", OPERATOR_QUERIES)
def test_fts_syntax_in_a_query_never_surfaces_as_index_unavailable(reader, query):
    # Handed to FTS5 raw these inputs raise sqlite errors, which Reader wraps as
    # IndexUnavailable and would send the caller off to rebuild a healthy index.
    hits = search(reader, query)
    assert isinstance(hits, list)


@pytest.mark.parametrize("query", ["-calcNut", '"calcNut', "alpha*"])
def test_fts_operator_queries_still_find_the_literal_term(reader, query):
    assert ALPHA in [hit.path for hit in search(reader, query)]


def test_preview_locates_the_unescaped_query_on_the_line_that_holds_it(operator_reader):
    hits = search(operator_reader, "wall-2.0")
    assert [hit.path for hit in hits] == ["src/ops/guard.C"]
    hit = hits[0]
    assert hit.start_line == 1
    # The literal sits on line 2 of the chunk: the preview must open there, not at
    # the chunk head, so escaping must never reach _preview.
    assert hit.text_start_line == 2
    assert hit.text.startswith("    // clamp wall-2.0 down to nut-2.0 before use")
    assert "struct opsGuard" not in hit.text
    assert hit.truncated is False
