"""Traverse the corpus, filter it by globs and normalize bytes into a SourceFile stream."""

import hashlib
import os
from dataclasses import dataclass
from fnmatch import fnmatchcase

from .errors import InvalidArgument


@dataclass(frozen=True)
class SourceFile:
    """One decoded source file: relative POSIX path, normalized lines, sha256, byte size."""

    path: str
    lines: list
    sha256: str
    byte_size: int


def _matches(path, globs):
    """Case-sensitive glob match on the relative POSIX path; '**/' also covers zero dirs."""
    for pattern in globs:
        if fnmatchcase(path, pattern):
            return True
        if pattern.startswith("**/") and fnmatchcase(path, pattern[3:]):
            return True
    return False


def _collect(cfg):
    """Sorted relative POSIX paths of every included, non-excluded regular file."""
    root = cfg.corpus_root
    found = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Linked directories are never descended into nor reported as sources.
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if _matches(rel, cfg.exclude_globs) or not _matches(rel, cfg.include_globs):
                continue
            found.append(rel)
    return sorted(found)


def _decode(raw):
    """Decode as UTF-8 with the BOM stripped, then unify every line break to '\\n'."""
    text = raw.decode("utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read(root, rel):
    """Build one SourceFile; an undecodable file is named by its relative path."""
    with open(os.path.join(root, *rel.split("/")), "rb") as handle:
        raw = handle.read()
    try:
        text = _decode(raw)
    except UnicodeDecodeError as exc:
        raise InvalidArgument(f"file is not valid UTF-8: {rel}") from exc
    data = text.encode("utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return SourceFile(path=rel, lines=lines, sha256=hashlib.sha256(data).hexdigest(),
                      byte_size=len(data))


def iter_sources(cfg):
    """Yield every selected source in path order; zero input and bad bytes are rejected."""
    relatives = _collect(cfg)
    if not relatives:
        raise InvalidArgument(
            f"no source file selected under {cfg.corpus_root}: "
            f"include={list(cfg.include_globs)} exclude={list(cfg.exclude_globs)}")
    for rel in relatives:
        yield _read(cfg.corpus_root, rel)
