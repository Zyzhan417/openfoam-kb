"""Two-channel RRF search, preview truncation and ref parsing (FR-020..FR-023)."""

from dataclasses import dataclass

from .config import is_identifier_shaped  # 004 FR-002: the criterion lives in config
from .errors import FoamKBError, InternalError, InvalidArgument, NotFound

RRF_K = 60
SYMBOL_LIMIT = 10
LEXICAL_LIMIT = 50
QUERY_MAX = 512
TOP_K_MIN, TOP_K_MAX = 1, 20
PREVIEW_MIN, PREVIEW_MAX = 200, 8000
# FR-013: the kinds whose hit carries the file's Description. A filedoc hit already carries
# it as its own `text`, and a text/lines hit explains no symbol, so both get None.
DOC_KINDS = ("class", "function")


@dataclass(frozen=True)
class Hit:
    """One citable chunk: ten public fields plus two internal ones (twelve attributes in all).

    Public, and the only ones that reach stdout: ref, path, start_line, end_line,
    text_start_line, symbol, kind, text, truncated, doc — exactly these ten (FR-013b, which
    superseded FR-022's eight-field list per FR-020; FR-022's preview-start, text_start_line
    and score-clauses still hold). `doc` is the file's Description section for a
    class/function hit, else None (FR-013).
    Internal, never serialised: `score` (the RRF fusion score) and `unit_id`.
    """

    ref: str
    path: str
    start_line: int
    end_line: int
    text_start_line: int
    symbol: str | None
    kind: str
    unit_id: int | None
    text: str
    score: float
    truncated: bool
    doc: dict | None


def make_ref(reader, chunk):
    """<rev>:<path>::<start_line>; the embedded rev makes stale refs fail loudly (FR-023)."""
    return f"{reader.rev()}:{chunk['path']}::{chunk['start_line']}"


def resolve_ref(reader, ref):
    """Return the chunk a ref names; everything else is NotFound, never a neighbour."""
    if not isinstance(ref, str):
        raise NotFound(f"ref must be a string: {ref!r}")
    try:
        head, start_text = ref.rsplit("::", 1)
        rev, path = head.split(":", 1)
    except ValueError as exc:
        raise NotFound(f"malformed ref (want <rev>:<path>::<start_line>): {ref}") from exc
    current = reader.rev()
    if rev != current:
        raise NotFound(f"ref rev {rev!r} does not match index rev {current!r}: {ref}")
    if not (start_text.isascii() and start_text.isdigit() and int(start_text) > 0):
        raise NotFound(f"malformed ref, start_line must be a positive integer: {ref}")
    if not path:
        raise NotFound(f"malformed ref, empty path: {ref}")
    start_line = int(start_text)
    chunk_id = reader.chunk_id_at(path, start_line)
    if chunk_id is None:
        raise NotFound(f"no chunk starts at {path}::{start_line} in rev {current}: {ref}")
    return reader.get_chunk(chunk_id)


def _symbol_channel(reader, normalized, prefix):
    """Channel A's ids plus the term that answered them (005 FR-007).

    Tries the whole query first, then — only once that misses — the tokens that look like
    code identifiers (004 FR-002/FR-004). Retrying *every* token made a prose word that
    happens to be a symbol name (`incompressible`) hijack four top slots; retrying none of
    them cost the mixed queries their rank 1.

    The second return value is what a hit is backfilled with: `symbol_at` needs the string
    that actually matched, and for a retried query that is one token, not the whole query.
    """
    symbol_ids = reader.find_symbols(normalized, SYMBOL_LIMIT, prefix)
    if symbol_ids:
        return symbol_ids, normalized
    # No de-duplication: the loop stops at the first non-empty answer, so a repeated token
    # could only ever re-ask a question already found empty.
    for token in normalized.split():
        if not is_identifier_shaped(token):
            continue
        symbol_ids = reader.find_symbols(token, SYMBOL_LIMIT, prefix)
        if symbol_ids:
            return symbol_ids, token
    return symbol_ids, normalized


def search(reader, query, top_k=5, path_prefix=None, max_chars=1500):
    """Merge the symbol and lexical channels with RRF (k=60) and return the best top_k."""
    normalized = _normalize(query)
    top_k = _check_int(top_k, "top_k", TOP_K_MIN, TOP_K_MAX)
    max_chars = _check_int(max_chars, "max_chars", PREVIEW_MIN, PREVIEW_MAX)
    prefix = _check_prefix(path_prefix)
    try:
        symbol_ids, symbol_term = _symbol_channel(reader, normalized, prefix)
        channels = (symbol_ids,
                    reader.search_lexical(_match_expression(normalized), LEXICAL_LIMIT, prefix))
        scores = {}
        for ids in channels:
            for rank, chunk_id in enumerate(ids, 1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        ranked = [(score, chunk) for chunk_id, score in scores.items()
                  if (chunk := reader.get_chunk(chunk_id)) is not None]
    except FoamKBError:
        raise
    except Exception as exc:
        raise InternalError(f"search failed: {exc}") from exc
    ranked.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["start_line"]))
    return [_hit(reader, chunk, score, normalized, max_chars,
                 _declared_symbol(reader, chunk, symbol_ids, symbol_term))
            for score, chunk in ranked[:top_k]]


def _declared_symbol(reader, chunk, symbol_ids, term):
    """The alias this chunk declares under `term`, or None when it needs no backfill.

    FR-007: only a symbol-channel hit can want it, and `chunks.symbol` being None is what
    says the name lives in the `symbols` table instead of on the chunk. `kind` is left
    alone — an alias hit keeps the chunk's own kind, so the public `kind` enum gains no
    `typedef` value.
    """
    if chunk["symbol"] is not None or chunk["id"] not in symbol_ids:
        return None
    return reader.symbol_at(chunk["id"], term)


def _hit(reader, chunk, score, query, max_chars, symbol=None):
    """Turn a stored chunk into a Hit whose text starts exactly at text_start_line."""
    text, truncated, skipped = _preview(chunk["text"], query, max_chars)
    return Hit(ref=make_ref(reader, chunk), path=chunk["path"], start_line=chunk["start_line"],
               end_line=chunk["end_line"], text_start_line=chunk["start_line"] + skipped,
               symbol=chunk["symbol"] if symbol is None else symbol,
               kind=chunk["kind"], unit_id=chunk["unit_id"],
               text=text, score=score, truncated=truncated,
               doc=_file_doc(reader, chunk))


def _file_doc(reader, chunk):
    """The hit's file-level Description, or None (FR-013, FR-014, C19).

    The only way to a doc is `reader.file_doc`, so this module still assembles no SQL. The
    doc is returned whole: no `max_chars` cut and no `truncated` flag (C19) — cutting it
    would undo the reason C17 gave the section a chunk of its own.
    """
    if chunk["kind"] not in DOC_KINDS:
        return None
    return reader.file_doc(chunk["path"])


def _preview(text, query, max_chars):
    """Start at the line head holding the query; return (text, truncated, skipped_lines)."""
    found = text.find(query)
    start = text.rfind("\n", 0, found) + 1 if found > 0 else 0
    body, skipped = text[start:], text.count("\n", 0, start)
    if len(body) <= max_chars:
        return body, False, skipped
    cut = body.rfind("\n", 0, max_chars)
    if cut <= 0:
        cut = max_chars  # one over-long line: fall back to a character cut
    return body[:cut], True, skipped


def _match_expression(query):
    """Quote each token as an FTS5 phrase so operators inside it stay literal (FR-020).

    Inner quotes are doubled. Preview location still uses the unescaped query, so
    this escaping never reaches _preview.
    """
    return " ".join('"' + token.replace('"', '""') + '"' for token in query.split())


def _normalize(query):
    """Strip and collapse inner whitespace before either channel sees the query (FR-020)."""
    if not isinstance(query, str):
        raise InvalidArgument(f"query must be a string: {query!r}")
    stripped = query.strip()
    # The 512 limit counts what the caller passed, before whitespace collapsing (FR-021).
    if len(stripped) > QUERY_MAX:
        raise InvalidArgument(f"query must be at most {QUERY_MAX} characters: {len(stripped)}")
    normalized = " ".join(stripped.split())
    if not normalized:
        raise InvalidArgument("query must not be empty or blank")
    return normalized


def _check_int(value, name, low, high):
    """Reject bools, non-ints and out-of-range values with the documented code (FR-021)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidArgument(f"{name} must be an integer: {value!r}")
    if not low <= value <= high:
        raise InvalidArgument(f"{name} must be within {low}-{high}: {value}")
    return value


def _check_prefix(path_prefix):
    """Accept only a relative directory: no leading/trailing slash, no '..' segment."""
    if path_prefix is None:
        return None
    if not isinstance(path_prefix, str) or not path_prefix:
        raise InvalidArgument(f"path_prefix must be a non-empty relative directory: {path_prefix!r}")
    if path_prefix.startswith("/") or path_prefix.endswith("/") or ".." in path_prefix.split("/"):
        raise InvalidArgument("path_prefix must be relative, without a leading/trailing slash"
                              f" or '..' segment: {path_prefix}")
    return path_prefix
