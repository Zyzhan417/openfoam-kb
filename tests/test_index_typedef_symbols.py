"""005 T005: alias rows through a real build, and the alias backfill through `search`.

A synthetic corpus (tmp_path + the `corpus` fixture) built by the real `index.build`, so
every assertion is about the index a user would actually get, not about a helper's return.

T005⑥ — measured comment count, on the build corpus of `/opt/openfoam13` rev 2cf4316a
(the four build globs, 8,911 files): of **214** alias-shaped column-0 lines, **0** sit
inside a `/* ... */` block. Measured with an independent block-comment state machine (not
`foamkb.lines.comment_mask`, AGENTS D15), so a defect in the product's comment rule cannot
hide itself. 0 means the line-anchored extractor needs no comment criterion: no follow-up
to T001 was required.
"""

import sqlite3

import pytest

from conftest import _cfg
from foamkb import index
from foamkb.errors import IndexUnavailable
from foamkb.search import search

# volFieldsFwd.H's shape: three aliases in one chunk, none of them the chunk's head, plus a
# class body whose member aliases must stay out. The class also gives a `class` symbol row.
FWD = "src/fields/volFieldsFwd.H"
FWD_TEXT = (
    "template<class Type>\n"
    "class VolField;\n"
    "\n"
    "typedef VolInternalField<scalar> volLabelField;\n"
    "typedef VolField<vector> volVectorField;\n"
    "using volSymmTensorField = VolField<symmTensor>;\n"
    "\n"
    "class volFieldMarker\n"
    "{\n"
    "public:\n"
    "    typedef VolField<scalar> alphaField;\n"
    "    using rhoField = VolField<scalar>;\n"
    "};\n"
)
IO = "src/io/IOstreamFwd.H"
IO_TEXT = ("typedef void (*IstreamManip)(std::istream&, int&);\n"
           "typedef void (*OstreamManip)(std::ostream&, const int);\n")
THERMO = "src/thermo/transport.H"
THERMO_TEXT = ("using IsothermalThermophysicalTransportPhaseModel =\n"
               "    ThermophysicalTransportModel<phaseModel>;\n")
CALC = "src/alpha/calc.C"
CALC_TEXT = ("Foam::scalar Foam::alpha::calcNut()\n"
             "{\n"
             "    const scalar alphaOne = 1.0;\n"
             "    return alphaOne;\n"
             "}\n")
FILES = {FWD: FWD_TEXT, IO: IO_TEXT, THERMO: THERMO_TEXT, CALC: CALC_TEXT}

ALIASES = {"volLabelField": "typedef", "volVectorField": "typedef",
           "volSymmTensorField": "using",
           "IsothermalThermophysicalTransportPhaseModel": "using"}


@pytest.fixture
def built(tmp_path, corpus):
    """(index path, Reader) over FILES built by the real build."""
    cfg = _cfg(tmp_path, corpus, FILES)
    index.build(cfg)
    return cfg.index_path, index.open_reader(cfg.index_path)


def _conn(path):
    return sqlite3.connect(path)


def _chunk_id(path, needle):
    """The one chunk of `path` whose body holds `needle` (chunk boundaries stay unknown here)."""
    with _conn(path) as conn:
        rows = conn.execute("SELECT id, text FROM chunks WHERE path = ? ORDER BY start_line",
                            (needle[0],)).fetchall()
    found = [chunk_id for chunk_id, text in rows if needle[1] in text]
    assert len(found) == 1, f"fixture drift: {needle[1]} must sit in exactly one chunk"
    return found[0]


def _alias_keyword_chunk(path):
    return _chunk_id(path, (FWD, "volVectorField"))


def test_symbols_has_a_not_null_kind_column(built):
    """FR-001: `kind` is part of the table, declared NOT NULL — the writer must supply it."""
    path, _ = built
    with _conn(path) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info(symbols)")}
    assert "kind" in columns, f"symbols has no kind column: {sorted(columns)}"
    assert columns["kind"][3] == 1, "kind must be NOT NULL so no row can omit its source"


def test_alias_rows_carry_the_declaration_they_came_from(built):
    """T005①/FR-001: one row per alias, `full == leaf == name`, kind from the declaration."""
    path, _ = built
    with _conn(path) as conn:
        rows = conn.execute(
            "SELECT full, leaf, kind FROM symbols WHERE kind IN ('typedef','using')"
            " ORDER BY full").fetchall()
    assert rows == [(name, name, kind) for name, kind in sorted(ALIASES.items())]


def test_pre_existing_rows_take_the_kind_of_their_chunk(built):
    """FR-001: a class/function row is labelled with `chunks.kind`, unchanged by this feature."""
    path, _ = built
    with _conn(path) as conn:
        rows = conn.execute(
            "SELECT s.kind, c.kind FROM symbols s JOIN chunks c ON c.id = s.chunk_id"
            " WHERE s.kind NOT IN ('typedef','using') ORDER BY s.chunk_id").fetchall()
    assert sorted(rows) == [("class", "class"), ("function", "function")], \
        f"expected one class and one function row, got {rows}"


def test_an_alias_chunk_keeps_its_symbol_column_null(built):
    """T005②/FR-005: the names live in `symbols`; `chunks.symbol` stays NULL (plan P4)."""
    path, _ = built
    with _conn(path) as conn:
        row = conn.execute("SELECT symbol, kind FROM chunks WHERE id = ?",
                           (_alias_keyword_chunk(path),)).fetchone()
    assert row == (None, "text"), f"alias chunk must stay a text chunk with no symbol: {row}"


def test_every_alias_of_one_chunk_is_findable(built):
    """T005③/FR-003: three aliases in one chunk are three findable symbols."""
    path, reader = built
    chunk_id = _alias_keyword_chunk(path)
    for name in ("volLabelField", "volVectorField", "volSymmTensorField"):
        assert reader.find_symbols(name, 10) == [chunk_id], f"{name} is not its own symbol row"


def test_find_symbols_still_returns_chunk_ids(built):
    """FR-006: the existing contract (list of chunk ids) is unchanged by the new method."""
    _, reader = built
    ids = reader.find_symbols("volVectorField", 10)
    assert isinstance(ids, list) and [type(i) for i in ids] == [int]


def test_member_aliases_produce_no_rows_at_all(built):
    """SC-005 in miniature: `alphaField` / `rhoField` are members, so they cannot be looked up."""
    path, reader = built
    assert reader.find_symbols("alphaField", 10) == []
    assert reader.find_symbols("rhoField", 10) == []
    with _conn(path) as conn:
        member_rows = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE kind IN ('typedef','using')"
            " AND full IN ('alphaField','rhoField','type','cmptType')").fetchone()[0]
    assert member_rows == 0


def test_a_function_pointer_typedef_produces_no_row(built):
    """FR-004 through the build: `IstreamManip` is a name inside parentheses, so it is not one."""
    path, reader = built
    with _conn(path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM symbols WHERE chunk_id = ?",
                            (_chunk_id(path, (IO, "IstreamManip")),)).fetchone()[0]
        symbol = conn.execute("SELECT symbol FROM chunks WHERE id = ?",
                              (_chunk_id(path, (IO, "IstreamManip")),)).fetchone()[0]
    assert (rows, symbol) == (0, None)
    assert reader.find_symbols("IstreamManip", 10) == []


def test_meta_records_schema_4(built):
    """T005④/FR-008."""
    path, reader = built
    assert reader.meta("schema") == "4"


def test_a_schema_3_index_is_refused(built):
    """SC-007 / FR-008, the other half of the version bump: a schema-3 index is refused.

    This test lives here rather than in `test_index_build.py` for one reason: that file was
    at 396 of its 400 lines (C11) before this feature, so a new test cannot fit in it. It is
    also where it belongs by meaning — FR-008 is "schema 4 exists *and* 3 does not open",
    and the assertion above is its first half.

    A schema-3 index has no `symbols.kind` and no alias rows at all, so reading it would
    answer "no definition for volScalarField" as if that were the corpus's answer — the
    silent downgrade 原则 III exists to prevent. `3` is the version 003 really shipped.
    """
    path, _ = built
    with _conn(path) as conn:
        conn.execute("UPDATE meta SET value = '3' WHERE key = 'schema'")
    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_search_backfills_the_alias_name_into_the_hit(built):
    """T005⑤/SC-004/FR-007: symbol-channel hit on an alias chunk -> symbol is the alias."""
    _, reader = built
    hits = search(reader, "volVectorField", top_k=5)
    assert hits, "the alias chunk must be reachable at all"
    assert hits[0].path == FWD
    assert (hits[0].symbol, hits[0].kind) == ("volVectorField", "text"), \
        "an alias hit carries the alias name and keeps the chunk's own kind"


def test_search_backfills_a_multi_line_using_alias(built):
    """The same path for a `using` declaration continued over two lines."""
    _, reader = built
    hits = search(reader, "IsothermalThermophysicalTransportPhaseModel", top_k=5)
    assert (hits[0].symbol, hits[0].kind) == \
        ("IsothermalThermophysicalTransportPhaseModel", "text")


def test_a_chunk_that_already_has_a_symbol_is_not_backfilled(built):
    """SC-004 second half: a class/function hit keeps exactly what it had before (FR-007)."""
    _, reader = built
    hits = search(reader, "calcNut", top_k=5)
    assert (hits[0].symbol, hits[0].kind) == ("Foam::alpha::calcNut", "function")


def test_a_lexical_only_hit_on_an_alias_chunk_gets_no_backfill(built):
    """FR-007: only a *symbol-channel* hit is backfilled.

    `volFieldMarker` appears in the alias chunk's body but is not one of its symbols, so this
    hit comes from the lexical channel alone and must keep symbol None — otherwise the field
    would mean "matched some word in a block that declares something", which is not a
    declaration point.
    """
    _, reader = built
    hits = [hit for hit in search(reader, "volFieldMarker", top_k=5) if hit.path == FWD]
    assert hits, "the lexical channel must still find the word"
    assert all(hit.symbol is None for hit in hits if hit.kind == "text")
