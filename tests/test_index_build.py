"""index build: five tables, meta keys, fallback counts, atomic replace (FR-016..FR-019)."""

import os
import re
import sqlite3

import pytest

from conftest import _cfg
from foamkb import chunker, index, loader
from foamkb.config import build_config
from foamkb.errors import IndexUnavailable, InternalError, InvalidArgument


REQUIRED_META_KEYS = (
    "schema", "rev", "built_at", "corpus_root", "corpus_files", "corpus_lines",
    "chunk_count", "chunk_mode", "max_lines", "include_globs", "exclude_globs",
    "fallback_files", "fallback_chunks",
    # FR-016 (002): schema 2 adds the glue threshold, the banner allowance and the
    # class-unit count the structural gate reads back.
    "glue_min_chars", "banner_max_blank", "class_unit_count",
    # FR-003 (003): schema 3 also records the tokenizer it was built with. 003 FR-002's
    # version change, not a relaxed assertion — the key is required, not optional.
    "fts_tokenize",
)
NORMAL = "Foam::scalar Foam::demo::calcNut()\n{\n    return 1.0;\n}\n"
# 200 lines whose only unit never closes: the whole file must fall back to fixed windows.
BIG_UNBALANCED = "\n".join(["unbalanced()", "{"] + [f"v{i}=1;" for i in range(198)]) + "\n"


def _cfg(tmp_path, corpus, files, **kwargs):
    root = corpus(files)
    return build_config(root, str(tmp_path / "kb.sqlite"), ("**/*",), **kwargs)


def _expected_counts(cfg):
    """File, line and chunk totals derived from the public loader/chunker only."""
    files = lines = chunks = 0
    for src in loader.iter_sources(cfg):
        files += 1
        lines += len(src.lines)
        chunks += len(chunker.chunk_source(
            src, cfg.chunk_mode, cfg.max_lines, cfg.max_chars)["chunks"])
    return files, lines, chunks


def _meta_rows(path):
    with sqlite3.connect(path) as conn:
        return dict(conn.execute("SELECT key, value FROM meta").fetchall())


def _snapshot(path):
    """rev, chunk_count and every chunk's verbatim slice, in id order."""
    reader = index.open_reader(path)
    with sqlite3.connect(path) as conn:
        ids = [row[0] for row in conn.execute("SELECT id FROM chunks ORDER BY id")]
    chunks = [reader.get_chunk(cid) for cid in ids]
    return {
        "rev": reader.rev(),
        "chunk_count": reader.meta("chunk_count"),
        "chunks": [(c["path"], c["start_line"], c["end_line"], c["text"]) for c in chunks],
    }


def _valid_index(tmp_path, corpus):
    cfg = _cfg(tmp_path, corpus, {"src/ok.C": NORMAL})
    return cfg, index.build(cfg)


def test_build_writes_five_tables_and_full_meta(tmp_path, corpus):
    cfg = _cfg(tmp_path, corpus, {"src/demo/demo.C": NORMAL, "src/util/util.C": NORMAL})
    report = index.build(cfg)

    assert os.path.isfile(cfg.index_path)
    assert not os.path.exists(cfg.tmp_index_path)
    with sqlite3.connect(cfg.index_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        files = conn.execute(
            "SELECT path, sha256, lines, bytes FROM files ORDER BY path").fetchall()
    assert {"meta", "chunks", "chunks_fts", "symbols", "files"} <= names
    assert {"idx_chunks_path", "idx_symbols_leaf"} <= names
    assert [row[0] for row in files] == ["src/demo/demo.C", "src/util/util.C"]
    for path, sha, lines, size in files:
        assert re.fullmatch(r"[0-9a-f]{64}", sha), path
        assert (lines, size) == (4, len(NORMAL.encode())), path

    meta = _meta_rows(cfg.index_path)
    assert set(REQUIRED_META_KEYS) <= set(meta)
    assert re.fullmatch(r"[0-9a-f]{8}", meta["rev"])
    assert meta["rev"] == report.rev
    assert (int(meta["corpus_files"]), int(meta["corpus_lines"]), int(meta["chunk_count"])) \
        == _expected_counts(cfg) == (2, 8, 2)
    assert meta["chunk_mode"] == "struct"


def test_rev_depends_only_on_the_input_manifest(tmp_path, corpus):
    root = corpus({"src/ok.C": NORMAL})
    first = index.build(build_config(root, str(tmp_path / "a.sqlite"), ("**/*",)))
    again = index.build(build_config(root, str(tmp_path / "b.sqlite"), ("**/*",)))
    assert first.rev == again.rev  # build time never enters rev (FR-017)

    corpus({"src/extra.C": NORMAL})
    changed = index.build(build_config(root, str(tmp_path / "c.sqlite"), ("**/*",)))
    assert changed.rev != first.rev


def test_build_report_is_mirrored_to_stderr(tmp_path, corpus, capsys):
    cfg = _cfg(tmp_path, corpus, {"src/ok.C": NORMAL, "src/bad.C": BIG_UNBALANCED})
    report = index.build(cfg)
    err = capsys.readouterr().err
    assert f"rev={report.rev}" in err
    assert f"corpus_files={report.corpus_files}" in err
    assert f"corpus_lines={report.corpus_lines}" in err
    assert f"chunk_count={report.chunk_count}" in err
    assert f"fallback_files={report.fallback_files}" in err
    assert f"fallback_chunks={report.fallback_chunks}" in err


def test_fallback_counts_are_pinned_and_stored(tmp_path, corpus):
    cfg = _cfg(tmp_path, corpus, {"src/bad.C": BIG_UNBALANCED, "src/ok.C": NORMAL})
    report = index.build(cfg)

    assert len(BIG_UNBALANCED.split("\n")) == 201  # 200 lines plus the trailing empty one
    assert report.fallback_files == 1
    assert report.fallback_chunks == 2  # ceil(200 / 120)
    assert report.chunk_count == 3
    meta = _meta_rows(cfg.index_path)
    assert meta["fallback_files"] == "1"
    assert meta["fallback_chunks"] == "2"


def test_empty_file_is_recorded_without_chunks(tmp_path, corpus):
    cfg = _cfg(tmp_path, corpus, {"src/empty.C": "", "src/ok.C": NORMAL})
    report = index.build(cfg)

    with sqlite3.connect(cfg.index_path) as conn:
        row = conn.execute(
            "SELECT lines, bytes FROM files WHERE path = 'src/empty.C'").fetchone()
        chunked = conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE path = 'src/empty.C'").fetchone()[0]
    assert row == (0, 0)
    assert chunked == 0
    assert report.chunk_count == 1


def test_empty_and_blank_files_do_not_change_corpus_files(tmp_path, corpus):
    cfg = _cfg(tmp_path, corpus, {
        "src/ok.C": NORMAL, "src/empty.C": "", "src/blank.C": "   \n\t\n"})
    report = index.build(cfg)

    on_disk = sum(len(names) for _, _, names in os.walk(cfg.corpus_root))
    assert on_disk == 3
    assert report.corpus_files == 3
    assert int(_meta_rows(cfg.index_path)["corpus_files"]) == 3
    with sqlite3.connect(cfg.index_path) as conn:
        chunked = {row[0] for row in conn.execute("SELECT DISTINCT path FROM chunks")}
        blank = conn.execute(
            "SELECT lines, bytes FROM files WHERE path = 'src/blank.C'").fetchone()
    assert chunked == {"src/ok.C"}
    assert blank[0] == 2 and blank[1] > 0


def test_missing_index_raises_index_unavailable(tmp_path):
    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(str(tmp_path / "absent.sqlite"))
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_non_sqlite_bytes_raise_index_unavailable(tmp_path):
    path = tmp_path / "kb.sqlite"
    path.write_bytes(b"\x00\x01 not a database at all " * 64)
    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(str(path))
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_schema_mismatch_raises_index_unavailable(tmp_path, corpus):
    cfg, _ = _valid_index(tmp_path, corpus)
    with sqlite3.connect(cfg.index_path) as conn:
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema'")
    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(cfg.index_path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_missing_required_meta_key_raises_index_unavailable(tmp_path, corpus):
    cfg, _ = _valid_index(tmp_path, corpus)
    with sqlite3.connect(cfg.index_path) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'rev'")
    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(cfg.index_path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_truncated_index_raises_index_unavailable_at_open(tmp_path, corpus):
    cfg, _ = _valid_index(tmp_path, corpus)
    os.truncate(cfg.index_path, 200)
    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(cfg.index_path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_truncated_index_fails_the_next_query_of_an_open_reader(tmp_path, corpus):
    cfg, _ = _valid_index(tmp_path, corpus)
    reader = index.open_reader(cfg.index_path)
    os.truncate(cfg.index_path, 200)
    with pytest.raises(IndexUnavailable) as caught:
        reader.search_lexical("calcNut", 5, None)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_a_malformed_fts_expression_is_an_argument_error_not_a_lost_index(tmp_path, corpus):
    """R4-6b: raw MATCH syntax errors belong to the caller, so they are not 'rebuild me'."""
    cfg, _ = _valid_index(tmp_path, corpus)
    reader = index.open_reader(cfg.index_path)
    with pytest.raises(InvalidArgument) as caught:
        reader.search_lexical('"unclosed', 5, None)
    assert type(caught.value) is InvalidArgument
    assert caught.value.code == "INVALID_ARGUMENT"


def test_replace_failure_raises_internal_error_and_keeps_tmp(tmp_path, corpus, monkeypatch):
    cfg, _ = _valid_index(tmp_path, corpus)
    before = _snapshot(cfg.index_path)
    corpus({"src/new.C": NORMAL})

    def boom(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(index.os, "replace", boom)
    with pytest.raises(InternalError) as caught:
        index.build(cfg)
    assert type(caught.value) is InternalError
    assert caught.value.code == "INTERNAL_ERROR"
    assert os.path.isfile(cfg.tmp_index_path)
    assert _snapshot(cfg.index_path) == before


class _MetaBoomConnection(sqlite3.Connection):
    """Connection that dies while meta is written, i.e. just before commit."""

    def execute(self, sql, *args):
        if "INSERT INTO meta" in sql:
            raise sqlite3.OperationalError("simulated disk full while writing meta")
        return super().execute(sql, *args)

    def executemany(self, sql, *args):
        if "INSERT INTO meta" in sql:
            raise sqlite3.OperationalError("simulated disk full while writing meta")
        return super().executemany(sql, *args)


def test_failure_before_commit_keeps_tmp_and_old_index(tmp_path, corpus, monkeypatch):
    cfg, _ = _valid_index(tmp_path, corpus)
    before = _snapshot(cfg.index_path)
    corpus({"src/new.C": NORMAL})
    real_connect = sqlite3.connect

    def fake_connect(path, *args, **kwargs):
        kwargs["factory"] = _MetaBoomConnection
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(index.sqlite3, "connect", fake_connect)
    with pytest.raises(InternalError) as caught:
        index.build(cfg)
    assert type(caught.value) is InternalError
    assert caught.value.code == "INTERNAL_ERROR"

    assert os.path.isfile(cfg.tmp_index_path), "the temporary index must survive for diagnosis"
    assert _snapshot(cfg.index_path) == before


def test_a_leftover_tmp_is_cleaned_up_before_the_next_build(tmp_path, corpus):
    cfg, _ = _valid_index(tmp_path, corpus)
    with open(cfg.tmp_index_path, "wb") as handle:
        handle.write(b"stale bytes from an interrupted build")
    report = index.build(cfg)
    assert report.chunk_count == 1
    assert not os.path.exists(cfg.tmp_index_path)
    assert _snapshot(cfg.index_path)["chunk_count"] == "1"


# --- schema 2, its three meta keys and the glue wiring (FR-016, FR-015) ------

CLASS_ONLY = (
    "class wallFn\n"
    "{\n"
    "public:\n"
    "    scalar nut() const;\n"
    "};\n"
)
# Four 59-character comment paragraphs, blank-line separated: 242 characters in total,
# i.e. one chunk at a 400-character glue threshold and four chunks at 40.
GLUE_PARAGRAPHS = "\n\n".join("// " + letter * 56 for letter in "abcd") + "\n"


def test_meta_has_new_keys(tmp_path, corpus):
    """FR-016: meta carries schema 2 plus glue threshold, banner allowance and class count."""
    cfg = _cfg(tmp_path, corpus, {"src/wall/wallFn.H": CLASS_ONLY, "src/ok.C": NORMAL})
    index.build(cfg)

    meta = _meta_rows(cfg.index_path)
    with sqlite3.connect(cfg.index_path) as conn:
        classes = conn.execute("SELECT COUNT(*) FROM chunks WHERE kind = 'class'").fetchone()[0]
    # 005 FR-008 的版本变更: `symbols.kind` plus the namespace-scope alias rows changed what
    # an index *contains*, so the contract version is 4. The original assertion (003: "3")
    # was not wrong — the expected value moved with the contract, and nothing was loosened.
    assert meta["schema"] == "4"
    assert meta["glue_min_chars"] == "400"
    assert meta["banner_max_blank"] == "2"
    assert classes == 1, "the fixture must really contain one class chunk"
    assert int(meta["class_unit_count"]) == classes


def test_schema_v1_index_rejected(tmp_path, corpus):
    """SC-009 / SC-007: a schema-1 index is refused, never read with schema-3 semantics."""
    cfg, _ = _valid_index(tmp_path, corpus)
    with sqlite3.connect(cfg.index_path) as conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema'")

    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(cfg.index_path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_schema_v2_index_rejected(tmp_path, corpus):
    """SC-007 · 003 FR-002 的版本变更: schema 2 is refused, never read as schema 4.

    Its FTS table carries no stemming, so every ranking differs — "the tables look fine"
    is exactly the silent sorting swap 原则 III forbids.
    """
    cfg, _ = _valid_index(tmp_path, corpus)
    with sqlite3.connect(cfg.index_path) as conn:
        conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema'")

    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(cfg.index_path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"


def test_index_without_fts_tokenize_key_is_refused(tmp_path, corpus):
    """FR-003 · 003 FR-002 的版本变更: an index that cannot say how it was built is refused.

    The presence guard matters: without it, "the key is gone" and "the build never wrote
    it" look identical, and the refusal would pass for the wrong reason.
    """
    cfg, _ = _valid_index(tmp_path, corpus)
    assert "fts_tokenize" in _meta_rows(cfg.index_path), "the build must write it first"
    with sqlite3.connect(cfg.index_path) as conn:
        conn.execute("DELETE FROM meta WHERE key = 'fts_tokenize'")

    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(cfg.index_path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"
    assert "fts_tokenize" in str(caught.value), f"the message must name it: {caught.value}"


def test_glue_min_chars_reaches_chunking(tmp_path, corpus):
    """FR-015: a build_config override must reach chunk_source, not die in the dataclass.

    Before the wiring fix `_populate` called chunk_source without cfg.glue_min_chars, so
    the module default of 400 always ran and the tight build below was identical.
    """
    root = corpus({"src/notes.C": GLUE_PARAGRAPHS})
    wide = build_config(root, str(tmp_path / "wide.sqlite"), ("**/*",), glue_min_chars=400)
    tight = build_config(root, str(tmp_path / "tight.sqlite"), ("**/*",), glue_min_chars=40)

    assert index.build(wide).chunk_count == 1
    assert index.build(tight).chunk_count == 4
    assert _meta_rows(wide.index_path)["glue_min_chars"] == "400"
    assert _meta_rows(tight.index_path)["glue_min_chars"] == "40"


@pytest.mark.parametrize("key", ("glue_min_chars", "banner_max_blank", "class_unit_count"))
def test_missing_structural_meta_key_raises_index_unavailable(tmp_path, corpus, key):
    """FR-016: the three keys schema 2 added are required, not decorative (C15 / P3).

    Without them in REQUIRED_META an index that predates the structural build opens
    happily, and the gate that reads glue_min_chars back then sees None and passes.
    """
    cfg, _ = _valid_index(tmp_path, corpus)
    with sqlite3.connect(cfg.index_path) as conn:
        conn.execute("DELETE FROM meta WHERE key = ?", (key,))
    with pytest.raises(IndexUnavailable) as caught:
        index.open_reader(cfg.index_path)
    assert type(caught.value) is IndexUnavailable
    assert caught.value.code == "INDEX_UNAVAILABLE"
    assert key in str(caught.value), f"the message must name the missing key, got {caught.value}"
