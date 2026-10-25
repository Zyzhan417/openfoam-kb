"""index tokenizer: the FTS5 tokenizer string, and stem-level evidence it is in force.

FR-001 fixes the tokenizer; FR-009 / SC-008 fix how its effect is proven. The DDL
assertion alone only says what the build *declares* — the term-level checks in this
file are what distinguish a rebuilt index from one whose DDL changed after the fact.
"""

import sqlite3

from conftest import _cfg
from foamkb import index

# FR-001: the one tokenizer string. Both halves are asserted everywhere it appears —
# dropping `porter` loses stemming, dropping `tokenchars '_.'` splits `kOmegaSST` and
# `fvPatchField`, which is the identifier channel this project lives on.
TOKENIZER = "porter unicode61 tokenchars '_.'"
NORMAL = "Foam::scalar Foam::demo::calcNut()\n{\n    return 1.0;\n}\n"


def _chunk_id(index_path, path, kind="filedoc"):
    with sqlite3.connect(index_path) as conn:
        return conn.execute("SELECT id FROM chunks WHERE path = ? AND kind = ?",
                            (path, kind)).fetchone()[0]


# --- T001: what the DDL declares (FR-001) ------------------------------------

def test_fts_index_declares_the_porter_tokenizer(tmp_path, corpus):
    """FR-001: chunks_fts is built with `porter unicode61 tokenchars '_.'`.

    Both substrings are asserted. Asserting only `porter` would pass while `tokenchars
    '_.'` was deleted, and that regression splits every dotted identifier in the corpus
    without failing a single ranking test on identifier-free fixtures.
    """
    cfg = _cfg(tmp_path, corpus, {"src/ok.C": NORMAL})
    index.build(cfg)

    with sqlite3.connect(cfg.index_path) as conn:
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'").fetchone()[0]
    assert "porter unicode61" in ddl, ddl
    assert "tokenchars '_.'" in ddl, ddl


# --- T002: the stem actually in the index (FR-009, SC-008) -------------------
# A Description whose body says `LES models` and never the bare word `model`. The
# banner is the real OpenFOAM shape and runs past the 400-char glue floor, so the
# Description section becomes the filedoc chunk on its own terms.
MODELS_FILE = (
    "/*--------------------------------*- C++ -*----------------------------------*\\\n"
    "Description\n"
    "    Transport of LES models for incompressible turbulence in the solver library.\n"
    "    The selection is driven by the mesh and by the case dictionary, not by the\n"
    "    solver name, so a single implementation serves every incompressible case.\n"
    "    Coefficients are read from a dictionary and blended across the whole field.\n"
    "    Nothing here is specific to a particular geometry or a particular boundary.\n"
    "\\*---------------------------------------------------------------------------*/\n"
    '\n#include "turbulence.H"\n\nclass transport\n{\n};\n'
)


def _vocab_terms(index_path, wanted, col="lex_text"):
    """Distinct FTS terms for `wanted` in one column, read through fts5vocab.

    A separate connection is mandatory: `open_reader` sets PRAGMA query_only = ON, and
    CREATE VIRTUAL TABLE on the reader's connection is refused. The column filter keeps
    the claim about the chunk BODY — a path or symbol token must not be able to satisfy it.
    """
    with sqlite3.connect(index_path) as conn:
        conn.execute("CREATE VIRTUAL TABLE vocab USING fts5vocab(chunks_fts, 'instance')")
        return [row[0] for row in conn.execute(
            "SELECT DISTINCT term FROM vocab WHERE col = ? AND term IN (?,?) ORDER BY term",
            (col, *wanted))]


def test_model_and_models_are_one_term_and_the_models_only_doc_is_found(tmp_path, corpus):
    """FR-009 / SC-008: stem-level proof, not just a ranking that happened to move.

    ① `"model"` must return the filedoc whose text only ever says `models`.
    ② `model` and `models` must be ONE term inside the built index.
    Both are needed. ① alone is satisfied by a build that hard-codes `model` into
    _lex_text while the tokenizer stays unicode61; ② alone is satisfied by a DDL edited
    after the index was written, because it reads the index that is on disk.
    """
    cfg = _cfg(tmp_path, corpus, {"src/transport/turbulence.H": MODELS_FILE})
    index.build(cfg)
    doc_id = _chunk_id(cfg.index_path, "src/transport/turbulence.H")
    doc = index.open_reader(cfg.index_path).get_chunk(doc_id)
    assert doc is not None, "the fixture must yield a filedoc chunk"
    text = doc["text"]

    # Without this the rest proves nothing: the fixture must really lack a bare `model`.
    assert "LES models" in text
    assert "model" not in text.split(), f"the fixture must not contain a bare `model`: {text!r}"

    assert doc_id in index.open_reader(cfg.index_path).search_lexical('"model"', 10), (
        "a doc that only says `models` must still be found by `model`")
    terms = _vocab_terms(cfg.index_path, ("model", "models"))
    assert terms == ["model"], f"both spellings must resolve to the one term 'model', got {terms}"


# --- T004: the tokenizer the index records (FR-003) --------------------------

def test_meta_records_the_tokenizer_it_used(tmp_path, corpus):
    """FR-003 · 003 FR-002 的版本变更: meta.fts_tokenize is the string the DDL really used.

    ONE hard-coded literal is compared against both places: the value stored in meta and
    the chunks_fts DDL in sqlite_master. Importing the product constant instead would let
    a typo agree with itself, and a second copy of the string elsewhere (config.py) is
    the AGENTS D15 drift this design avoids by interpolating a single constant.
    """
    cfg = _cfg(tmp_path, corpus, {"src/ok.C": NORMAL})
    index.build(cfg)
    with sqlite3.connect(cfg.index_path) as conn:
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'").fetchone()[0]
        stored = dict(conn.execute("SELECT key, value FROM meta").fetchall())["fts_tokenize"]

    assert stored == TOKENIZER
    assert TOKENIZER in ddl, f"meta claims {stored!r}, but the DDL says: {ddl}"

