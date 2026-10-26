#!/usr/bin/env python3
"""Build a control index that differs from the given one in exactly one thing: the
FTS5 tokenizer. Used as the RED end of SC-002 / SC-003 / SC-008.

Why a separate script instead of rebuilding with the tokenizer switched back: `src/**`
is subagent-owned (AGENTS E17 — one line counts), so the orchestrator may not edit the
DDL to produce a comparison build. This script touches no product code; it reads the
chunks already in an index and re-creates only the FTS table with `unicode61`.

The invariants that make the comparison meaningful:
  * the same rows enter the index (taken from the source index's own FTS membership via
    an fts rowid join — contentless tables cannot be read back, so the row set is joined,
    never recomputed from a threshold that could drift);
  * `lex_text` is produced by the same function the build uses, so subword expansion is
    identical and only the tokenizer differs;
  * every other table and every meta key is copied verbatim (only fts_tokenize and the
    schema note differ).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/make_control_index.py SRC.sqlite DST.sqlite
"""
import argparse
import sqlite3
import sys

# The control index must carry byte-identical lex_text to the product build; recomputing
# the subwords locally would introduce a second variable and invalidate the comparison.
from foamkb.index import _lex_text  # noqa: PLC2701

CONTROL_TOKENIZE = "unicode61 tokenchars '_.'"
DDL = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE chunks (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, start_line INTEGER NOT NULL,
  end_line INTEGER NOT NULL, symbol TEXT, kind TEXT NOT NULL, unit_id INTEGER,
  text TEXT NOT NULL, sha256 TEXT NOT NULL);
CREATE INDEX idx_chunks_path ON chunks(path);
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  lex_text, path, symbol, content='', tokenize="{tokenize}");
CREATE TABLE symbols (chunk_id INTEGER NOT NULL, full TEXT NOT NULL, leaf TEXT NOT NULL);
CREATE INDEX idx_symbols_leaf ON symbols(leaf);
CREATE TABLE files (path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
  lines INTEGER NOT NULL, bytes INTEGER NOT NULL);
"""


def copy_table(src, dst, table, columns):
    """Copy one table verbatim, in a single executemany (files has 8,911 rows)."""
    rows = src.execute(f"SELECT {columns} FROM {table}").fetchall()
    marks = ",".join("?" * len(columns.split(",")))
    dst.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
    return len(rows)


def indexed_rows(src):
    """Every chunk that is in the source's FTS, with the columns _lex_text needs.

    Joining the contentless FTS table on rowid is the only way to learn which chunks were
    admitted (its admission rule is a build-time threshold; re-deriving it here would be a
    copied rule that can drift from the product's).
    """
    return src.execute(
        "SELECT c.id, c.path, c.symbol, c.text FROM chunks c"
        " JOIN chunks_fts f ON f.rowid = c.id ORDER BY c.id").fetchall()


def copy_meta(src, dst, source_tokenize):
    """Copy meta verbatim, then record the tokenizer this control index actually uses."""
    meta = dict(src.execute("SELECT key, value FROM meta").fetchall())
    meta["fts_tokenize"] = CONTROL_TOKENIZE
    meta["control_of_tokenize"] = source_tokenize
    dst.executemany("INSERT INTO meta(key, value) VALUES (?,?)", sorted(meta.items()))
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="index built by the product (porter)")
    ap.add_argument("dest", help="control index to write (unicode61)")
    args = ap.parse_args()

    src = sqlite3.connect(args.source)
    tokenize = dict(src.execute("SELECT key, value FROM meta").fetchall()).get("fts_tokenize")
    if not tokenize:
        raise SystemExit("source index has no meta.fts_tokenize; it is not a schema-3 index")

    dst = sqlite3.connect(args.dest)
    dst.executescript(DDL.format(tokenize=CONTROL_TOKENIZE))
    chunks = copy_table(src, dst, "chunks",
                        "id,path,start_line,end_line,symbol,kind,unit_id,text,sha256")
    symbols = copy_table(src, dst, "symbols", "chunk_id,full,leaf")
    files = copy_table(src, dst, "files", "path,sha256,lines,bytes")
    rows = indexed_rows(src)
    dst.executemany(
        "INSERT INTO chunks_fts(rowid, lex_text, path, symbol) VALUES (?,?,?,?)",
        [(cid, _lex_text(path, sym, text), path, sym) for cid, path, sym, text in rows])
    copy_meta(src, dst, tokenize)
    dst.commit()

    print(f"control index: {args.dest}")
    print(f"  source tokenize : {tokenize}")
    print(f"  control tokenize: {CONTROL_TOKENIZE}")
    print(f"  chunks={chunks} symbols={symbols} files={files} fts_rows={len(rows)}")
    for conn in (src, dst):
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
