"""Config owns every default and every argument range check (FR-001, FR-009)."""

import os
from dataclasses import FrozenInstanceError, is_dataclass

import pytest

from foamkb.config import (BANNER_MAX_BLANK, GLUE_MIN_CHARS, MAX_CHARS,
                           SYMBOL_KEYWORDS, build_config, is_identifier_shaped,
                           search_config)
from foamkb.errors import InvalidArgument

DEFAULT_EXCLUDES = ("**/lnInclude/**", "platforms/**", ".git/**")
CONTROL_KEYWORDS = frozenset({"for", "if", "while", "switch", "catch",
                             "else", "return", "do", "try"})
# FR-006 as amended by spec C13: the OpenFOAM forAll* loop macros expand to `for`
# and are equally un-declarable, so they belong to the same rejection set. These six
# are the complete forAll* family `#define`d in src/OpenFOAM of the corpus.
LOOP_MACROS = frozenset({"forAll", "forAllIter", "forAllConstIter", "forAllReverse",
                         "forAllStarEdges", "forAllStarFaces"})
UNDECLARABLE_NAMES = CONTROL_KEYWORDS | LOOP_MACROS


def _assert_invalid(exc_info):
    exc = exc_info.value
    assert type(exc) is InvalidArgument, f"expected InvalidArgument, got {type(exc).__name__}"
    assert exc.code == "INVALID_ARGUMENT", f"expected code INVALID_ARGUMENT, got {exc.code!r}"


def _build(root, **kwargs):
    return build_config(root, str(root / "kb.sqlite"), ["**/*.C"], **kwargs)


# --- normal path (FR-001) -------------------------------------------------

def test_build_config_defaults(corpus):
    cfg = _build(corpus({"fvMesh/a.C": "int main() {}\n"}))
    assert is_dataclass(cfg), "build_config must return a dataclass config object"
    assert cfg.chunk_mode == "struct", f"chunk_mode default must be struct, got {cfg.chunk_mode!r}"
    assert cfg.max_lines == 150, f"max_lines default must be 150, got {cfg.max_lines!r}"
    assert cfg.max_chars == 2000, f"max_chars default must be 2000, got {cfg.max_chars!r}"


def test_build_config_default_exclude_globs(corpus):
    cfg = _build(corpus({"a.C": "x\n"}))
    missing = [g for g in DEFAULT_EXCLUDES if g not in cfg.exclude_globs]
    assert missing == [], f"default exclude_globs must contain the three globs, missing {missing}"


def test_build_config_keeps_explicit_globs(corpus):
    root = corpus({"a.C": "x\n"})
    cfg = build_config(root, str(root / "kb.sqlite"), ["src/**/*.C"], ["platforms/**"])
    assert tuple(cfg.include_globs) == ("src/**/*.C",), f"explicit include_globs must be kept, got {cfg.include_globs!r}"
    assert tuple(cfg.exclude_globs) == ("platforms/**",), f"explicit exclude_globs must be kept, got {cfg.exclude_globs!r}"


def test_build_config_include_globs_has_no_default(corpus):
    root = corpus({"a.C": "x\n"})
    with pytest.raises(TypeError) as ei:
        build_config(root, str(root / "kb.sqlite"))
    assert "include_globs" in str(ei.value), f"TypeError must name include_globs, got {str(ei.value)!r}"


def test_build_config_tmp_index_path_is_sibling(corpus, tmp_path):
    root = corpus({"a.C": "x\n"})
    index_path = str(tmp_path / "kb.sqlite")
    cfg = build_config(root, index_path, ["**/*.C"])
    assert cfg.index_path == index_path, f"index_path must be kept verbatim, got {cfg.index_path!r}"
    assert cfg.tmp_index_path == index_path + ".tmp", f"tmp_index_path must be index_path + '.tmp', got {cfg.tmp_index_path!r}"
    assert os.path.dirname(cfg.tmp_index_path) == os.path.dirname(index_path), "tmp index must live in the index directory"


def test_build_config_is_frozen(corpus):
    cfg = _build(corpus({"a.C": "x\n"}))
    with pytest.raises(FrozenInstanceError) as ei:
        cfg.max_lines = 10
    assert "max_lines" in str(ei.value), f"FrozenInstanceError must name the field, got {str(ei.value)!r}"


def test_search_config_defaults():
    cfg = search_config("/tmp/kb.sqlite")
    assert is_dataclass(cfg), "search_config must return a dataclass config object"
    assert cfg.top_k == 5, f"top_k default must be 5, got {cfg.top_k!r}"
    assert cfg.max_chars == 1500, f"preview max_chars default must be 1500, got {cfg.max_chars!r}"


def test_search_config_is_frozen():
    cfg = search_config("/tmp/kb.sqlite")
    with pytest.raises(FrozenInstanceError) as ei:
        cfg.top_k = 10
    assert "top_k" in str(ei.value), f"FrozenInstanceError must name the field, got {str(ei.value)!r}"


# --- error path (FR-009) --------------------------------------------------

def test_missing_corpus_root_is_invalid(tmp_path):
    with pytest.raises(InvalidArgument) as ei:
        build_config(str(tmp_path / "absent"), str(tmp_path / "kb.sqlite"), ["**/*.C"])
    _assert_invalid(ei)


def test_corpus_root_that_is_a_file_is_invalid(tmp_path):
    target = tmp_path / "fvMesh.C"
    target.write_text("x\n", encoding="utf-8")
    with pytest.raises(InvalidArgument) as ei:
        build_config(str(target), str(tmp_path / "kb.sqlite"), ["**/*.C"])
    _assert_invalid(ei)


@pytest.mark.parametrize("mode", ["overlap", "STRUCT", "fixed ", ""])
def test_illegal_chunk_mode_is_invalid(corpus, mode):
    with pytest.raises(InvalidArgument) as ei:
        _build(corpus({"a.C": "x\n"}), chunk_mode=mode)
    _assert_invalid(ei)


@pytest.mark.parametrize("max_lines", [0, -1])
def test_max_lines_below_one_is_invalid(corpus, max_lines):
    with pytest.raises(InvalidArgument) as ei:
        _build(corpus({"a.C": "x\n"}), max_lines=max_lines)
    _assert_invalid(ei)


@pytest.mark.parametrize("max_chars", [199, 8001])
def test_build_max_chars_out_of_range_is_invalid(corpus, max_chars):
    with pytest.raises(InvalidArgument) as ei:
        _build(corpus({"a.C": "x\n"}), max_chars=max_chars)
    _assert_invalid(ei)


@pytest.mark.parametrize("top_k", [0, 21])
def test_search_top_k_out_of_range_is_invalid(top_k):
    with pytest.raises(InvalidArgument) as ei:
        search_config("/tmp/kb.sqlite", top_k=top_k)
    _assert_invalid(ei)


@pytest.mark.parametrize("max_chars", [199, 8001])
def test_search_max_chars_out_of_range_is_invalid(max_chars):
    with pytest.raises(InvalidArgument) as ei:
        search_config("/tmp/kb.sqlite", max_chars=max_chars)
    _assert_invalid(ei)


# --- T001/T002 (002-structural-chunking FR-015): glue threshold ownership ------

def test_config_glue_min_chars_default(corpus):
    """The glue threshold, banner blank allowance and keyword set live only in config.

    FR-015 makes config the single source: no environment variable, no external file.
    The default is the number the B400 decision (spec C1) was measured with.
    """
    assert GLUE_MIN_CHARS == 400, f"GLUE_MIN_CHARS must be 400, got {GLUE_MIN_CHARS!r}"
    assert BANNER_MAX_BLANK == 2, f"BANNER_MAX_BLANK must be 2, got {BANNER_MAX_BLANK!r}"
    # Superseded assertion: this used to require SYMBOL_KEYWORDS == CONTROL_KEYWORDS
    # (the nine C++ keywords). spec C13 extended FR-006 to also reject OpenFOAM's
    # forAll* loop macros, so equality with the keyword set is now wrong by spec.
    assert SYMBOL_KEYWORDS == UNDECLARABLE_NAMES, \
        f"SYMBOL_KEYWORDS must be the 9 C++ keywords + 6 forAll* macros, got {sorted(SYMBOL_KEYWORDS)!r}"
    assert isinstance(SYMBOL_KEYWORDS, frozenset), "SYMBOL_KEYWORDS must be immutable"

    cfg = _build(corpus({"a.C": "x\n"}))
    assert cfg.glue_min_chars == GLUE_MIN_CHARS == 400, \
        f"BuildConfig.glue_min_chars default must be GLUE_MIN_CHARS, got {cfg.glue_min_chars!r}"


def test_glue_min_chars_is_caller_overridable(corpus):
    """FR-015 keeps the default in config but lets a caller (CLI) override it."""
    cfg = _build(corpus({"a.C": "x\n"}), glue_min_chars=50)
    assert cfg.glue_min_chars == 50, f"explicit glue_min_chars must be kept, got {cfg.glue_min_chars!r}"


@pytest.mark.parametrize("glue", [0, -1, MAX_CHARS + 1, True, 400.0, "400"])
def test_glue_min_chars_out_of_range_is_invalid(corpus, glue):
    """T002: the threshold must be an int within 1..MAX_CHARS, else INVALID_ARGUMENT.

    A threshold of 0 would glue nothing and glue above MAX_CHARS could never be met.
    bool and float are rejected explicitly: both compare "equal" to 1 / 400.0 but a
    non-int threshold makes the glue loop compare unlike types.
    """
    with pytest.raises(InvalidArgument) as ei:
        _build(corpus({"a.C": "x\n"}), glue_min_chars=glue)
    _assert_invalid(ei)


@pytest.mark.parametrize("glue", [1, MAX_CHARS])
def test_glue_min_chars_accepts_the_bounds(corpus, glue):
    """The inclusive bounds themselves are legal."""
    assert _build(corpus({"a.C": "x\n"}), glue_min_chars=glue).glue_min_chars == glue


# --- 004 FR-002: the identifier-shape criterion lives in config -------------

@pytest.mark.parametrize("token", [
    "kOmegaSST",        # internal capital + camelCase: plainly an identifier
    "fvMesh",           # camelCase, the classic mixed-query token
    "nutkWallFunction", # camelCase
    "kEpsilon2006",     # digits
    "Foam::calcNut",    # qualified name
    "SST",              # all-caps abbreviation with an internal capital
    "LES",              # all-caps abbreviation that must still count (decision D)
    "Time",             # D: OpenFOAM class names start with a capital — see docstring
    "Foam",             # same: excluding it drops `Time controlDict` out of the top 20
    "nut_wall",         # underscore
    "x1",               # digit
])
def test_identifier_shaped_recognises_code_tokens(token):
    """004 FR-002 / D: underscore, any digit, or a capital *anywhere* makes it a candidate.

    Rebar against the narrower "capital after the first character" rule that was
    measured and rejected: it scored `Time controlDict` 1 -> out of the top 20.
    """
    assert is_identifier_shaped(token) is True, \
        f"{token!r} must be treated as identifier-shaped, got False"


@pytest.mark.parametrize("token", [
    "incompressible", "wall", "function", "for", "the", "velocity", "",
])
def test_identifier_shaped_rejects_prose_tokens(token):
    """004 FR-003: an all-lowercase English word must never trigger the fallback.

    The empty string is asserted too: an implementation slicing ``token[1:]`` would
    blow up or answer True on it, and the caller feeds whatever ``query.split()`` gives.
    """
    assert is_identifier_shaped(token) is False, \
        f"{token!r} must not be treated as identifier-shaped, got True"
