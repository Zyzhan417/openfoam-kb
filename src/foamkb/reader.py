"""Read-only view of a built index: Reader, open_reader and the schema constants (FR-014, FR-019).

Split out of `index` so that the reader boundary is a module boundary: `search` may depend
on this module and on nothing that builds an index. The dependency runs one way only —
`index` imports from `reader`, never the reverse — so this module knows nothing about
chunking, corpora or the atomic replace.
"""

import os
import sqlite3

from .config import LICENSE_MARKERS  # FR-012: one marker list for build and read side
from .errors import IndexUnavailable, InvalidArgument

# FR-016: schema 2 adds kind='filedoc' and three meta keys; a schema-1 index is refused.
# C15: the three structural keys are required too — an index that predates the structural
# build opened happily without them, and the gate reading glue_min_chars back saw None.
# 003 FR-002: schema 3 adds the tokenizer to what an index *is*. A schema-2 index is not
# corrupt, it is stemmed differently — reading it would silently change every ranking, so
# it is refused instead (原则 III). `fts_tokenize` is required for the same reason: the
# schema number alone does not say which tokenizer produced this FTS index.
# 005 FR-008: schema 4 adds `symbols.kind` and the namespace-scope alias rows. A schema-3
# index has no alias rows at all, so reading it would answer "no definition for
# volScalarField" as if that were the corpus's answer — refused, for the same reason the
# schema-2 and schema-3 refusals exist (原则 III).
SCHEMA_VERSION = 4
REQUIRED_META = ("schema", "rev", "chunk_mode", "chunk_count",
                 "glue_min_chars", "banner_max_blank", "class_unit_count", "fts_tokenize")
# Column order of chunks_fts: lex_text, path, symbol. Path stays weak: it is file-level.
BM25_WEIGHTS = (1.0, 0.1, 5.0)
# Markers of a malformed MATCH expression: the caller's argument error, not a broken index.
_FTS_SYNTAX = ("fts5: syntax error", "no such column", "unterminated string")


def _prefix(sql, params, path_prefix):
    """Push a path prefix filter into the candidate query instead of trimming afterwards."""
    if path_prefix is None:
        return sql, list(params)
    return sql + " AND substr(c.path, 1, ?) = ?", [*params, len(path_prefix), path_prefix]


class Reader:
    """Read-only view of a built index; every lookup takes an optional path prefix."""

    def __init__(self, conn):
        self._conn = conn

    def rev(self):
        return self.meta("rev")

    def meta(self, key):
        rows = self._query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0][0] if rows else None

    def get_chunk(self, chunk_id):
        keys = ("id", "path", "start_line", "end_line", "symbol", "kind", "unit_id", "text")
        rows = self._query("SELECT " + ", ".join(keys) + " FROM chunks WHERE id = ?", (chunk_id,))
        return dict(zip(keys, rows[0])) if rows else None

    def chunk_id_at(self, path, start_line):
        """Return the id of the chunk starting at path:start_line, else None.

        Exposed for ref resolution: the search layer maps a parsed ref back to a
        chunk through this method and never assembles SQL of its own.
        """
        rows = self._query("SELECT id FROM chunks WHERE path = ? AND start_line = ?",
                           (path, start_line))
        return rows[0][0] if rows else None

    def find_symbols(self, term, limit, path_prefix=None):
        sql, params = _prefix(
            "SELECT s.chunk_id FROM symbols s JOIN chunks c ON c.id = s.chunk_id"
            " WHERE (s.leaf = ? OR s.full = ?)", (term, term), path_prefix)
        rows = self._query(sql + " ORDER BY s.chunk_id LIMIT ?", (*params, limit))
        return [row[0] for row in rows]

    def symbol_at(self, chunk_id, term):
        """The symbol this chunk declares that matches `term`, or None (005 FR-006).

        A namespace-scope alias chunk keeps `chunks.symbol = NULL` and carries its names in
        `symbols` only (FR-005 / plan P4), so the search layer has to ask here rather than
        read a column. One chunk can declare up to 7 aliases, hence the `term` filter and
        the `rowid` order: the answer is exactly the name that matched, chosen
        deterministically. The SQL lives here so `search` still assembles none (C9).
        """
        rows = self._query(
            "SELECT s.leaf FROM symbols s WHERE s.chunk_id = ? AND (s.leaf = ? OR s.full = ?)"
            " ORDER BY s.rowid LIMIT 1", (chunk_id, term, term))
        return rows[0][0] if rows else None

    def file_doc(self, path):
        """The file's Description section, or None when it has none (FR-013, FR-014).

        This is the only way the search layer may obtain a doc: the SQL for it lives here,
        so `search` never assembles a query of its own. One file has at most one filedoc
        (SC-014); ORDER BY keeps the pick deterministic if that ever stops holding.

        C17 supersedes the read-side trim: the chunker now cuts the section out of the
        file, so the filedoc chunk IS the doc and the return is its own
        {start_line, end_line, text} — no offset search, no cut rule, nothing to drift
        from the build. A chunk that still carries a licence marker returns None, so an
        index written by an older rule cannot leak one (FR-012).
        """
        keys = ("id", "path", "start_line", "end_line", "symbol", "kind", "unit_id", "text")
        rows = self._query("SELECT " + ", ".join(keys) + " FROM chunks"
                           " WHERE path = ? AND kind = 'filedoc' ORDER BY start_line LIMIT 1",
                           (path,))
        if not rows:
            return None
        chunk = dict(zip(keys, rows[0]))
        if any(marker in chunk["text"] for marker in LICENSE_MARKERS):
            return None
        return {"start_line": chunk["start_line"], "end_line": chunk["end_line"],
                "text": chunk["text"]}

    def search_lexical(self, query, limit, path_prefix=None):
        sql, params = _prefix(
            "SELECT c.id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
            " WHERE chunks_fts MATCH ?", (query,), path_prefix)
        rows = self._query(sql + " ORDER BY bm25(chunks_fts, ?, ?, ?) LIMIT ?",
                           (*params, *BM25_WEIGHTS, limit))
        return [row[0] for row in rows]

    def _query(self, sql, params):
        try:
            return self._conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if any(token in str(exc) for token in _FTS_SYNTAX):
                raise InvalidArgument(f"invalid FTS5 query: {exc}") from exc
            raise IndexUnavailable(f"index is unreadable: {exc}") from exc
        except sqlite3.Error as exc:
            raise IndexUnavailable(f"index is unreadable: {exc}") from exc


def open_reader(index_path):
    """Validate existence, openness, schema version and required meta keys (FR-019)."""
    if not os.path.isfile(index_path):
        raise IndexUnavailable(f"index not found: {index_path} (run build first)")
    try:
        conn = sqlite3.connect(index_path)
        conn.execute("PRAGMA query_only = ON")
        found = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as exc:
        raise IndexUnavailable(f"index cannot be opened: {index_path}: {exc}") from exc
    missing = [key for key in REQUIRED_META if key not in found]
    if missing:
        conn.close()
        raise IndexUnavailable(f"index lacks required meta keys {missing}: {index_path}")
    if found["schema"] != str(SCHEMA_VERSION):
        conn.close()
        raise IndexUnavailable(f"schema {found['schema']} != {SCHEMA_VERSION}: {index_path}")
    return Reader(conn)
