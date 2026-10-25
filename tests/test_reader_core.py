"""Reader API: rev/meta/get_chunk, symbol and lexical lookup, chunk_id_at, ownership (FR-019, FR-020)."""

import ast
import inspect

import pytest

from foamkb import index
from foamkb.config import build_config


NUT = "Foam::scalar Foam::nutUSpaldingWallFunction::calcNut()\n{\n    return 1.0;\n}\n"
MESH = "Foam::tmp<Foam::fvMesh> Foam::fvMesh::kOmegaSST() const\n{\n    return tmp;\n}\n"
UNIT = "Foam::scalar Foam::meshTests::calcNut()\n{\n    return 2.0;\n}\n"
SYMBOL_ONLY = "Foam::scalar Foam::targetFn()\n{\n    return 1.0;\n}\n"
TEXT_ONLY = "Foam::scalar Foam::plainFn()\n{\n    return targetFn;\n}\n"

CORPUS = {
    "src/transportModels/viscosity/nutWallFunction.C": NUT,
    "src/finiteVolume/fvMesh/fvMesh.C": MESH,
    "tests/unit/meshTests.C": UNIT,
    "src/a/symbolOnly.C": SYMBOL_ONLY,
    "src/b/textOnly.C": TEXT_ONLY,
}


@pytest.fixture
def reader(tmp_path, corpus):
    root = corpus(CORPUS)
    cfg = build_config(root, str(tmp_path / "kb.sqlite"), ("**/*",))
    report = index.build(cfg)
    return index.open_reader(cfg.index_path), report


def _path(reader, chunk_id):
    return reader.get_chunk(chunk_id)["path"]


def test_rev_and_meta_match_build_report(reader):
    reader, report = reader
    assert reader.rev() == report.rev == reader.meta("rev")
    assert int(reader.meta("chunk_count")) == report.chunk_count == 5
    assert int(reader.meta("corpus_files")) == report.corpus_files == 5
    assert int(reader.meta("corpus_lines")) == report.corpus_lines == 20
    assert reader.meta("chunk_mode") == "struct"
    assert reader.meta("max_lines") == "150"
    assert reader.meta("absent_key") is None


def test_get_chunk_returns_the_verbatim_slice(reader):
    reader, _ = reader
    ids = reader.find_symbols("calcNut", 10, "src/transportModels")
    assert len(ids) == 1
    chunk = reader.get_chunk(ids[0])
    assert (chunk["path"], chunk["start_line"], chunk["end_line"]) == (
        "src/transportModels/viscosity/nutWallFunction.C", 1, 4)
    assert chunk["text"] == "\n".join(NUT.split("\n")[:4])
    assert chunk["symbol"] == "Foam::nutUSpaldingWallFunction::calcNut"
    assert chunk["kind"] == "function"
    assert reader.get_chunk(9999) is None


def test_find_symbols_matches_leaf_and_full_exactly(reader):
    reader, _ = reader
    assert len(reader.find_symbols("calcNut", 10, None)) == 2
    full = reader.find_symbols("Foam::nutUSpaldingWallFunction::calcNut", 10, None)
    assert len(full) == 1
    assert _path(reader, full[0]) == "src/transportModels/viscosity/nutWallFunction.C"
    assert reader.find_symbols("calc", 10, None) == []
    assert reader.find_symbols("nutUSpaldingWallFunction", 10, None) == []


def test_find_symbols_pushes_path_prefix_into_the_query(reader):
    reader, _ = reader
    scoped = reader.find_symbols("calcNut", 1, "tests/unit")
    assert len(scoped) == 1
    assert _path(reader, scoped[0]) == "tests/unit/meshTests.C"
    assert reader.find_symbols("calcNut", 10, "src/finiteVolume") == []


def test_search_lexical_ranks_by_bm25_weights(reader):
    reader, _ = reader
    hits = reader.search_lexical("targetFn", 10, None)
    assert len(hits) == 2
    assert _path(reader, hits[0]) == "src/a/symbolOnly.C"
    assert {_path(reader, hit) for hit in hits} == {
        "src/a/symbolOnly.C", "src/b/textOnly.C"}


def test_search_lexical_matches_split_subwords(reader):
    reader, _ = reader
    hits = reader.search_lexical("omega", 5, None)
    assert len(hits) == 1
    assert _path(reader, hits[0]) == "src/finiteVolume/fvMesh/fvMesh.C"


def test_search_lexical_pushes_path_prefix_and_allows_empty_results(reader):
    reader, _ = reader
    scoped = reader.search_lexical("return", 1, "tests/unit")
    assert len(scoped) == 1
    assert _path(reader, scoped[0]) == "tests/unit/meshTests.C"
    assert reader.search_lexical("calcNut", 10, "no/such/dir") == []
    assert reader.search_lexical("definitelyAbsentTerm", 10, None) == []


def test_chunk_id_at_returns_the_id_of_the_chunk_starting_there(reader):
    reader, _ = reader
    (chunk_id,) = reader.find_symbols("calcNut", 10, "src/transportModels")
    chunk = reader.get_chunk(chunk_id)
    assert reader.chunk_id_at(chunk["path"], chunk["start_line"]) == chunk_id
    assert isinstance(reader.chunk_id_at(chunk["path"], chunk["start_line"]), int)


def test_chunk_id_at_returns_none_when_no_chunk_starts_at_that_line(reader):
    reader, _ = reader
    path = "src/transportModels/viscosity/nutWallFunction.C"
    # Line 3 lies inside the chunk but starts nothing, so no neighbour is returned.
    assert reader.chunk_id_at(path, 3) is None
    assert reader.chunk_id_at("src/ghost/missing.C", 1) is None


def test_reader_is_owned_by_the_reader_module_and_reexported_by_index():
    """T013/FR-014: the read side is a module of its own; the dependency runs one way.

    `index` re-exports the read side so `index.open_reader(...)` keeps working, but the
    objects must be the same ones — a second definition in `index` would silently split
    the boundary `search` is supposed to depend on.
    """
    from foamkb import reader

    assert index.Reader is reader.Reader
    assert index.open_reader is reader.open_reader
    assert index.SCHEMA_VERSION == reader.SCHEMA_VERSION

    # The one-way dependency must be proved by the import GRAPH, not by substring
    # matching: `import foamkb.index` or a lazy import inside a function is still a
    # reverse dependency, and a mere comment mentioning ".index" is not one.
    # Mutation check: adding `import foamkb.index` at module level must turn this red.
    tree = ast.parse(inspect.getsource(reader))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Three shapes carry a reverse dependency:
            #   from .index import X      -> module="index", level=1
            #   from . import index       -> module=None,    level=1, name in aliases
            #   from foamkb import index  -> module="foamkb",          name in aliases
            if node.module:
                imported.add("." * node.level + node.module)
            imported.update(alias.name for alias in node.names)
    reverses = {name for name in imported
                if name == "index" or name.endswith(".index") or name == ".index"}
    assert not reverses, (
        f"reader must not depend on index (one-way boundary, FR-014); found {sorted(reverses)}")
