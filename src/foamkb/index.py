"""Build the sqlite index atomically (FR-017, FR-018, FR-019).

The read side lives in `reader`; `Reader`, `open_reader` and `SCHEMA_VERSION` are re-exported
here so that `from foamkb import index; index.open_reader(...)` keeps working unchanged.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from .chunker import chunk_source
from .config import BANNER_MAX_BLANK
from .declarations import namespace_scope_aliases
from .errors import InternalError
from .loader import iter_sources
from .reader import SCHEMA_VERSION, Reader, open_reader  # re-exported read side (FR-014)

__all__ = ("BuildReport", "Reader", "SCHEMA_VERSION", "build", "open_reader")

# A symbol-less chunk needs this many code characters for FTS: 12 is the length of the
# shortest real declaration ('class Foo'), so lowering it would drop those from search.
MIN_CODE_CHARS = 12

_IDENT = re.compile(r"[A-Za-z_]\w*")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

# FR-001: the FTS5 tokenizer, defined once. `porter` stems (model/models, model/modelling);
# `unicode61` keeps the identifier rules; `tokenchars '_.'` keeps `kOmegaSST` and
# `fvPatchField` whole. The DDL below and meta['fts_tokenize'] both interpolate THIS
# constant, so the index can only ever record the tokenizer it was really built with.
FTS_TOKENIZE = "porter unicode61 tokenchars '_.'"

_DDL = f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL, symbol TEXT, kind TEXT NOT NULL, unit_id INTEGER,
  text TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE INDEX idx_chunks_path ON chunks(path);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  lex_text, path, symbol, content='', tokenize="{FTS_TOKENIZE}");
CREATE TABLE symbols (chunk_id INTEGER NOT NULL, full TEXT NOT NULL, leaf TEXT NOT NULL,
  kind TEXT NOT NULL);
CREATE INDEX idx_symbols_leaf ON symbols(leaf);
CREATE TABLE files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
  lines INTEGER NOT NULL, bytes INTEGER NOT NULL);
"""


@dataclass(frozen=True)
class BuildReport:
    """What one build produced; the same numbers go to stderr (FR-013, FR-017)."""

    rev: str
    corpus_files: int
    corpus_lines: int
    chunk_count: int
    fallback_files: int
    fallback_chunks: int

    def as_line(self):
        return " ".join(f"{key}={value}" for key, value in vars(self).items())


def _rev(sources):
    """8 hex chars over the sorted (path, sha256) manifest; build time never enters it."""
    manifest = "\n".join(f"{src.path}\0{src.sha256}"
                         for src in sorted(sources, key=lambda src: src.path))
    return hashlib.sha256(manifest.encode("utf-8")).hexdigest()[:8]


def _lex_text(path, symbol, text):
    """First path segment + symbol + body + subwords of long identifiers (ARCHITECTURE §7.3)."""
    subwords = dict.fromkeys(
        sub for token in _IDENT.findall(text) if len(token) >= 4
        for piece in token.split("_") for sub in _CAMEL.findall(piece))
    return " ".join((path.split("/", 1)[0], symbol or "", text, " ".join(subwords)))


# C17: filedoc is decided by the chunker now (`filedoc.carve`), so the build only writes
# what it is given. The kind guard, the candidate pick and the licence test moved there —
# they describe how a chunk is cut, and reading them here meant the build rewrote a chunk
# after the fact, which is how a class chunk could lose its symbol to a relabel.


def _write_chunks(conn, src, chunks):
    """Insert chunks plus their FTS mirror (rowid = chunks.id) and their symbol rows."""
    for chunk in chunks:
        cursor = conn.execute(
            "INSERT INTO chunks(path, start_line, end_line, symbol, kind, unit_id, text, sha256)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (chunk["path"], chunk["start_line"], chunk["end_line"], chunk["symbol"],
             chunk["kind"], chunk["unit_id"], chunk["text"], src.sha256))
        # Comment-only blocks would outrank real definitions via bm25's short-document bias.
        # FR-013a: a filedoc is admitted explicitly — its body is prose, so the code-character
        # gate below would see too few code characters and drop the very text we want found.
        code = sum(len(s) for s in map(str.strip, chunk["text"].split("\n"))
                   if s and not s.startswith(("//", "/*", "*", "\\*")))
        if chunk["kind"] == "filedoc" or chunk["symbol"] or code >= MIN_CODE_CHARS:
            conn.execute(
                "INSERT INTO chunks_fts(rowid, lex_text, path, symbol) VALUES (?,?,?,?)",
                (cursor.lastrowid, _lex_text(chunk["path"], chunk["symbol"], chunk["text"]),
                 chunk["path"], chunk["symbol"]))
        _write_symbols(conn, cursor.lastrowid, chunk)


def _write_symbols(conn, chunk_id, chunk):
    """The symbol rows for one chunk: its own declared name, then its namespace aliases.

    FR-001: `kind` is written from `chunks.kind` for a class/function row and from the
    declaration itself for an alias row, so the table says what each name *is*.

    FR-005 / plan P4: an alias-only chunk keeps `chunks.symbol = NULL` on purpose. Writing
    the name there would push it into `chunks_fts.symbol` (bm25 weight 5.0) and change every
    ranking, which is the uncontrolled re-sort 002/003 measured against.
    """
    if chunk["symbol"]:
        conn.execute("INSERT INTO symbols(chunk_id, full, leaf, kind) VALUES (?,?,?,?)",
                     (chunk_id, chunk["symbol"], chunk["symbol"].rsplit("::", 1)[-1],
                      chunk["kind"]))
    # FR-003: `full` and `leaf` are both the alias itself — a typedef line cannot say which
    # namespace it sits in, and guessing would make `full` matching silently miss.
    conn.executemany(
        "INSERT INTO symbols(chunk_id, full, leaf, kind) VALUES (?,?,?,?)",
        [(chunk_id, name, name, kind) for name, kind in namespace_scope_aliases(chunk["text"])])


def _populate(conn, cfg):
    """Fill files / chunks / fts / symbols / meta from the corpus and return the report."""
    sources = list(iter_sources(cfg))
    conn.executemany("INSERT INTO files(path, sha256, lines, bytes) VALUES (?,?,?,?)",
                     [(src.path, src.sha256, len(src.lines), src.byte_size) for src in sources])
    fallback_files = fallback_chunks = chunk_count = class_unit_count = 0
    for src in sources:
        result = chunk_source(src, cfg.chunk_mode, cfg.max_lines, cfg.max_chars,
                              cfg.glue_min_chars)
        chunks = result["chunks"]
        class_unit_count += sum(1 for chunk in chunks if chunk["kind"] == "class")
        for warning in result["warnings"]:
            if warning["reason"] == "struct_fallback":
                fallback_files += 1
                fallback_chunks += warning["chunks"]
        _write_chunks(conn, src, chunks)
        chunk_count += len(chunks)
    report = BuildReport(rev=_rev(sources), corpus_files=len(sources),
                         corpus_lines=sum(len(src.lines) for src in sources),
                         chunk_count=chunk_count, fallback_files=fallback_files,
                         fallback_chunks=fallback_chunks)
    meta = {
        "schema": str(SCHEMA_VERSION),
        # FR-003: the tokenizer string comes from the same constant the DDL interpolated,
        # so the recorded value cannot describe a tokenizer the index was not built with.
        "fts_tokenize": FTS_TOKENIZE,
        "rev": report.rev,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpus_root": cfg.corpus_root,
        "corpus_files": str(report.corpus_files),
        "corpus_lines": str(report.corpus_lines),
        "chunk_count": str(report.chunk_count),
        "chunk_mode": cfg.chunk_mode,
        "max_lines": str(cfg.max_lines),
        "include_globs": json.dumps(list(cfg.include_globs)),
        "exclude_globs": json.dumps(list(cfg.exclude_globs)),
        "fallback_files": str(report.fallback_files),
        "fallback_chunks": str(report.fallback_chunks),
        # FR-016: the three keys the structural gate reads back from a finished build.
        "glue_min_chars": str(cfg.glue_min_chars),
        "banner_max_blank": str(BANNER_MAX_BLANK),
        "class_unit_count": str(class_unit_count),
    }
    conn.executemany("INSERT INTO meta(key, value) VALUES (?,?)", sorted(meta.items()))
    return report


def build(cfg):
    """Write the whole index to <index>.tmp, then swap it in atomically (FR-018)."""
    tmp = cfg.tmp_index_path
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(_DDL)
        report = _populate(conn, cfg)
        conn.commit()
    except sqlite3.Error as exc:
        print(f"internal: cannot write {tmp}: {exc}", file=sys.stderr)
        raise InternalError(f"cannot write {tmp}: {exc}") from exc
    finally:
        conn.close()
    try:
        os.replace(tmp, cfg.index_path)
    except OSError as exc:
        print(f"internal: cannot replace {cfg.index_path}: {exc}", file=sys.stderr)
        raise InternalError(f"cannot replace {cfg.index_path}: {exc}") from exc
    print(report.as_line(), file=sys.stderr)
    return report
