"""Single source of build/search defaults and argument ranges (FR-001, FR-009)."""

import os
from dataclasses import dataclass

from .errors import InvalidArgument

DEFAULT_EXCLUDE_GLOBS = ("**/lnInclude/**", "platforms/**", ".git/**")
CHUNK_MODES = ("struct", "fixed")
MIN_CHARS, MAX_CHARS = 200, 8000
# FR-017 / C17: a filedoc is the one kind exempt from max_chars, because it is a piece of
# prose that must not be cut. Measured corpus widths: p50=248 / p90=1089 / p99=2665 /
# max=7550, so 8000 covers every section and nothing is ever split.
FILEDOC_MAX_CHARS = 8000
# FR-012: a licence banner returned as a description is worse than no description at all.
# Shared by the build (filedoc rejects the section) and the read side (file_doc refuses a
# row written by an older rule), so neither side can drift from the other.
LICENSE_MARKERS = ("GNU General Public", "WITHOUT ANY WARRANTY", "FITNESS FOR A PARTICULAR")
# 002 FR-015: the glue threshold, the banner blank allowance and the control-flow
# keyword set are owned here and nowhere else (no env var, no external file).
GLUE_MIN_CHARS = 400
BANNER_MAX_BLANK = 2
# FR-006: names that look like call heads but cannot be a declared symbol. The C++
# control-flow keyword set plus the OpenFOAM loop macros — these six are exactly the
# `#define`d forAll* family in src/OpenFOAM (verified against the corpus, not guessed);
# forAll alone accounts for 3,726 bogus symbol rows when missing.
SYMBOL_KEYWORDS = frozenset({
    "for", "if", "while", "switch", "catch", "else", "return", "do", "try",
    "forAll", "forAllIter", "forAllConstIter", "forAllReverse",
    "forAllStarEdges", "forAllStarFaces",
})


@dataclass(frozen=True)
class BuildConfig:
    corpus_root: str
    index_path: str
    include_globs: tuple
    exclude_globs: tuple
    chunk_mode: str = "struct"
    max_lines: int = 150
    max_chars: int = 2000
    glue_min_chars: int = GLUE_MIN_CHARS

    @property
    def tmp_index_path(self):
        """Same-directory temporary target written before the atomic replace."""
        return self.index_path + ".tmp"


@dataclass(frozen=True)
class SearchConfig:
    index_path: str
    top_k: int = 5
    max_chars: int = 1500


def is_identifier_shaped(token):
    """004 FR-002 / decision D: may this query token be retried against the symbol table?

    True when the token holds `_`, any digit `0-9`, or a capital letter **anywhere**.
    `search` uses it to decide whether the symbol channel's per-token fallback runs at
    all, so a `True` here is what keeps "identifier + prose" queries hitting rank 1.

    Three deliberate choices, each measured rather than guessed:

    1. A capital at *any* position counts, the first one included. OpenFOAM class names
       start with a capital (`Time`, `Foam`), and `Time` is the real symbol that carries
       `Time controlDict`. Excluding first-letter capitals was measured on the 30-query
       porter set: it moved `Time controlDict` from rank 1 to out of the top 20 (worse=1
       where this rule gives worse=0). Do not narrow it back without re-running that set.
    2. An all-lowercase English word is never identifier-shaped. That is the whole point:
       `incompressible` is both a corpus symbol (`SolidThermoPhaseModel::incompressible`)
       and an ordinary word, and letting it fall back put four such blocks into the top
       four of a prose query. A side effect of "any capital" is that both all-caps
       abbreviations qualify (`SST`, `LES`); the earlier "capital after the first
       character" rule split that pair, but the split was an artefact of the rule, not a
       goal — neither token appears among the queries that regressed.
    3. No lookup of "is this a real symbol" happens here. That would put a query-shape
       rule behind an index read, and the criterion would then depend on the corpus.
       Shape alone is the contract; the symbol table decides what actually matches.

    The digit test is spelled `"0" <= ch <= "9"` rather than `ch.isdigit()` so it covers
    ASCII exactly as FR-002 words it; `isdigit()` also accepts `٣` and `²`.
    """
    return bool(token) and ("_" in token
                            or any(ch.isupper() or "0" <= ch <= "9" for ch in token))


def _check_max_chars(value):
    if not MIN_CHARS <= value <= MAX_CHARS:
        raise InvalidArgument(f"max_chars must be within {MIN_CHARS}-{MAX_CHARS}: {value}")


def _check_glue_min_chars(value):
    """T002: an int within 1..MAX_CHARS; anything else is the caller's argument error."""
    if type(value) is not int or not 1 <= value <= MAX_CHARS:
        raise InvalidArgument(f"glue_min_chars must be an int within 1-{MAX_CHARS}: {value!r}")


def build_config(corpus_root, index_path, include_globs, exclude_globs=None,
                 chunk_mode="struct", max_lines=150, max_chars=2000,
                 glue_min_chars=GLUE_MIN_CHARS):
    """Immutable build config; every default for building lives here."""
    if not os.path.exists(corpus_root):
        raise InvalidArgument(f"corpus_root does not exist: {corpus_root}")
    if not os.path.isdir(corpus_root):
        raise InvalidArgument(f"corpus_root is not a directory: {corpus_root}")
    if chunk_mode not in CHUNK_MODES:
        raise InvalidArgument(f"chunk_mode must be one of {CHUNK_MODES}: {chunk_mode!r}")
    if max_lines < 1:
        raise InvalidArgument(f"max_lines must be >= 1: {max_lines}")
    _check_max_chars(max_chars)
    _check_glue_min_chars(glue_min_chars)
    return BuildConfig(
        corpus_root=str(corpus_root),
        index_path=str(index_path),
        include_globs=tuple(include_globs),
        exclude_globs=DEFAULT_EXCLUDE_GLOBS if exclude_globs is None else tuple(exclude_globs),
        chunk_mode=chunk_mode,
        max_lines=max_lines,
        max_chars=max_chars,
        glue_min_chars=glue_min_chars,
    )


def search_config(index_path, top_k=5, max_chars=1500):
    """Immutable search config; top_k and preview defaults live here."""
    if not 1 <= top_k <= 20:
        raise InvalidArgument(f"top_k must be within 1-20: {top_k}")
    _check_max_chars(max_chars)
    return SearchConfig(index_path=str(index_path), top_k=top_k, max_chars=max_chars)
