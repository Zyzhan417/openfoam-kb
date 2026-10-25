"""loader contract: order, path form, normalization, hashing and error paths (T011/T012)."""

import hashlib
import os
from pathlib import PurePosixPath

import pytest

from foamkb.config import build_config
from foamkb.errors import InvalidArgument
from foamkb.loader import iter_sources


def _config(root, include=("*",), exclude=()):
    """Build a config for one synthetic corpus; the index path is never touched here."""
    return build_config(root, str(root.parent / "index.db"), include, exclude)


def _by_path(files):
    return {source.path: source for source in files}


def test_sources_are_yielded_in_path_dictionary_order(corpus):
    root = corpus({"zeta.C": "z\n", "alpha/beta.C": "b\n", "Mid.C": "m\n"})

    assert [s.path for s in iter_sources(_config(root))] == [
        "Mid.C",
        "alpha/beta.C",
        "zeta.C",
    ]


def test_path_is_relative_posix_without_dot_segments_and_keeps_case(corpus):
    root = corpus({"Src/Api/Foo.C": "x\n"})
    sources = list(iter_sources(_config(root)))

    assert len(sources) == 1
    path = sources[0].path
    assert path == "Src/Api/Foo.C"
    assert not path.startswith("/")
    assert "\\" not in path
    assert all(part not in (".", "..") for part in PurePosixPath(path).parts)
    assert PurePosixPath(path).parts == ("Src", "Api", "Foo.C")


def test_sha256_and_byte_size_follow_the_normalized_text(corpus):
    root = corpus({"norm.C": "alpha\r\nbeta\r", "plain.C": "alpha\nbeta\n"})
    sources = _by_path(iter_sources(_config(root)))
    expected = "alpha\nbeta\n".encode("utf-8")

    assert len(sources) == 2
    assert sources["norm.C"].sha256 == hashlib.sha256(expected).hexdigest()
    assert sources["norm.C"].byte_size == len(expected)
    assert sources["norm.C"].sha256 == sources["plain.C"].sha256


def test_trailing_newline_does_not_create_an_extra_line(corpus):
    root = corpus({"trail.C": "a\nb\n", "no_trail.C": "a\nb"})
    sources = _by_path(iter_sources(_config(root)))

    assert len(sources) == 2
    assert sources["trail.C"].lines == ["a", "b"]
    assert sources["no_trail.C"].lines == ["a", "b"]


def test_empty_file_is_yielded_with_no_lines(corpus):
    root = corpus({"empty.C": ""})
    sources = list(iter_sources(_config(root)))

    assert len(sources) == 1
    assert sources[0].lines == []
    assert sources[0].byte_size == 0
    assert sources[0].sha256 == hashlib.sha256(b"").hexdigest()


def test_whitespace_only_file_yields_blank_lines_without_error(corpus):
    root = corpus({"blank.C": "  \n\t\n"})
    sources = list(iter_sources(_config(root)))

    assert len(sources) == 1
    assert sources[0].lines == ["  ", "\t"]
    assert all(line.strip() == "" for line in sources[0].lines)


def test_undecodable_bytes_raise_invalid_argument_naming_the_relative_path(corpus):
    root = corpus({"bad/latin1.C": b"int x; // caf\xe9\n"})

    with pytest.raises(InvalidArgument) as excinfo:
        list(iter_sources(_config(root)))
    exc = excinfo.value
    assert type(exc) is InvalidArgument
    assert exc.code == "INVALID_ARGUMENT"
    assert "bad/latin1.C" in str(exc)


def test_undecodable_file_is_named_even_when_other_files_decode(corpus):
    root = corpus({"aaa/ok.C": "fine\n", "zzz/bad.C": b"ok\n\x80\n"})

    with pytest.raises(InvalidArgument) as excinfo:
        list(iter_sources(_config(root)))
    exc = excinfo.value
    assert type(exc) is InvalidArgument
    assert exc.code == "INVALID_ARGUMENT"
    assert "zzz/bad.C" in str(exc)


def test_empty_include_globs_selects_nothing_and_raises(corpus):
    root = corpus({"one.C": "x\n"})

    with pytest.raises(InvalidArgument) as excinfo:
        list(iter_sources(_config(root, include=())))
    exc = excinfo.value
    assert type(exc) is InvalidArgument
    assert exc.code == "INVALID_ARGUMENT"


def test_include_globs_matching_nothing_raise_invalid_argument(corpus):
    root = corpus({"one.C": "x\n"})

    with pytest.raises(InvalidArgument) as excinfo:
        list(iter_sources(_config(root, include=("*.H",))))
    exc = excinfo.value
    assert type(exc) is InvalidArgument
    assert exc.code == "INVALID_ARGUMENT"


def test_corpus_without_any_file_raises_invalid_argument(corpus):
    root = corpus({})

    with pytest.raises(InvalidArgument) as excinfo:
        list(iter_sources(_config(root)))
    exc = excinfo.value
    assert type(exc) is InvalidArgument
    assert exc.code == "INVALID_ARGUMENT"


def test_exclude_that_removes_every_file_raises_invalid_argument(corpus):
    root = corpus({"only.C": "x\n"})

    with pytest.raises(InvalidArgument) as excinfo:
        list(iter_sources(_config(root, include=("*.C",), exclude=("*.C",))))
    exc = excinfo.value
    assert type(exc) is InvalidArgument
    assert exc.code == "INVALID_ARGUMENT"


def test_exclude_glob_wins_over_include_for_the_same_file(corpus):
    root = corpus({"excluded/skip.C": "s\n", "keep.C": "k\n"})

    paths = [s.path for s in iter_sources(
        _config(root, include=("*.C",), exclude=("excluded/*",)))]

    assert paths == ["keep.C"]


def test_default_excludes_drop_a_root_level_lnInclude_directory(corpus):
    """The '**/' prefix must also cover zero directories, i.e. lnInclude at the root."""
    root = corpus({"lnInclude/root.C": "linked\n", "src/real/keep.C": "k\n"})
    cfg = build_config(root, str(root.parent / "index.db"), ("**/*",))

    assert [s.path for s in iter_sources(cfg)] == ["src/real/keep.C"]


def test_symlinked_file_is_not_followed_and_target_is_kept_once(corpus):
    root = corpus({"real/target.C": "t\n"})
    os.symlink(root / "real" / "target.C", root / "link.C")

    assert [s.path for s in iter_sources(_config(root))] == ["real/target.C"]


def test_symlinked_directory_is_not_traversed(corpus):
    root = corpus({"real/inner.C": "i\n"})
    os.symlink(root / "real", root / "alias")

    assert [s.path for s in iter_sources(_config(root))] == ["real/inner.C"]


def test_utf8_bom_is_stripped_and_lines_start_at_line_one(corpus):
    root = corpus({"bom.C": b"\xef\xbb\xbfint a;\nint b;\n"})
    sources = list(iter_sources(_config(root)))

    assert len(sources) == 1
    assert sources[0].lines == ["int a;", "int b;"]
    assert sources[0].lines[0][0] != "\ufeff"


def test_crlf_and_lone_cr_normalize_to_lf_with_matching_line_counts(corpus):
    root = corpus({
        "crlf.C": "a\r\nb\r\nc\r\n",
        "cr.C": "a\rb\rc\r",
        "lf.C": "a\nb\nc\n",
    })
    sources = _by_path(iter_sources(_config(root)))

    assert len(sources) == 3
    assert sources["crlf.C"].lines == ["a", "b", "c"]
    assert sources["cr.C"].lines == ["a", "b", "c"]
    assert len(sources["crlf.C"].lines) == len(sources["lf.C"].lines)
    assert all("\r" not in line for line in sources["crlf.C"].lines)


def test_single_line_without_trailing_newline_counts_as_one_line(corpus):
    root = corpus({"one.C": "only line"})
    sources = list(iter_sources(_config(root)))

    assert len(sources) == 1
    assert sources[0].lines == ["only line"]


def test_empty_file_among_others_yields_no_lines_and_no_error(corpus):
    root = corpus({"a_empty.C": "", "b_full.C": "x\n"})
    sources = list(iter_sources(_config(root)))

    assert [s.path for s in sources] == ["a_empty.C", "b_full.C"]
    assert sources[0].lines == []
    assert sources[1].lines == ["x"]


def test_glob_matching_is_case_sensitive(corpus):
    root = corpus({"Src/Foo.C": "x\n"})

    with pytest.raises(InvalidArgument) as excinfo:
        list(iter_sources(_config(root, include=("src/*.C",))))
    exc = excinfo.value
    assert type(exc) is InvalidArgument
    assert exc.code == "INVALID_ARGUMENT"

    assert [s.path for s in iter_sources(_config(root, include=("Src/*.C",)))] == [
        "Src/Foo.C",
    ]
