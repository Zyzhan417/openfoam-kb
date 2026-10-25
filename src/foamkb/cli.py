"""The single entry point: two subcommands, JSON on stdout, exit codes 0/2/3/4/5 (FR-024)."""

import argparse
import json
import sys

from .config import CHUNK_MODES, build_config, search_config
from .errors import FoamKBError, IndexUnavailable, InternalError, InvalidArgument, NotFound
from .index import build, open_reader
from .search import search

# One row per code of the closed family; NOT_FOUND stays reserved for M2 (FR-024).
_EXIT_CODES = {InvalidArgument: 2, NotFound: 3, IndexUnavailable: 4, InternalError: 5}
# The ten public hit fields; score / unit_id never reach stdout (FR-022, FR-013b).
# FR-013 adds `doc` ({start_line, end_line, text} or null); the key must appear even when
# it is null, so a caller can tell "no description for this file" from "field missing".
# FR-013b adds `kind` ({class, function, text, filedoc, lines, macro}) after `symbol`: `doc`
# being non-null only implies class/function, so without `kind` a caller cannot tell a text
# hit from a filedoc one. `score` (internal fusion score) and `unit_id` stay out.
_PUBLIC = ("ref", "path", "start_line", "end_line", "text_start_line", "symbol", "kind", "text",
           "truncated", "doc")


class _Parser(argparse.ArgumentParser):
    """Turn argparse's usage+exit path into the documented error contract (FR-024)."""

    def error(self, message):
        raise InvalidArgument(message)


def _parser():
    """build and search parsers; every default is left to config (FR-001)."""
    parser = _Parser(prog="foamkb", description="OpenFOAM source evidence index")
    subs = parser.add_subparsers(dest="command", required=True)
    maker = subs.add_parser("build", help="build the lexical index from a corpus directory")
    maker.add_argument("--corpus", required=True, help="corpus root directory")
    maker.add_argument("--index", required=True, help="index file to write")
    maker.add_argument("--chunk-mode", choices=CHUNK_MODES, default=argparse.SUPPRESS)
    maker.add_argument("--include", action="append", metavar="GLOB")
    maker.add_argument("--exclude", action="append", metavar="GLOB")
    maker.add_argument("--max-lines", type=int, metavar="N", default=argparse.SUPPRESS)
    maker.add_argument("--max-chars", type=int, metavar="N", default=argparse.SUPPRESS)
    # FR-015: the glue floor is overridable HERE, on build, for comparison experiments;
    # its default and its 1..MAX_CHARS range live in config, not in this parser.
    maker.add_argument("--glue-min-chars", type=int, metavar="N", default=argparse.SUPPRESS)
    finder = subs.add_parser("search", help="query a built index")
    finder.add_argument("--index", required=True, help="index file to read")
    finder.add_argument("--query", required=True)
    finder.add_argument("--top-k", type=int, metavar="N", default=argparse.SUPPRESS)
    finder.add_argument("--path-prefix", metavar="P")
    finder.add_argument("--max-chars", type=int, metavar="N", default=argparse.SUPPRESS)
    return parser


def _given(args, names):
    """Forward only the flags the caller passed; no default is re-invented here (FR-001)."""
    return {name: getattr(args, name) for name in names if hasattr(args, name)}


def _build(args):
    """Build through index.build, which already reports to stderr and never to stdout."""
    cfg = build_config(args.corpus, args.index, args.include or (),
                       exclude_globs=args.exclude or None,
                       **_given(args, ("chunk_mode", "max_lines", "max_chars",
                                       "glue_min_chars")))
    build(cfg)


def _search(args):
    """Resolve every search default through config, then print exactly {rev, hits}."""
    cfg = search_config(args.index, **_given(args, ("top_k", "max_chars")))
    reader = open_reader(cfg.index_path)
    hits = search(reader, args.query, top_k=cfg.top_k, max_chars=cfg.max_chars,
                  path_prefix=args.path_prefix)
    print(json.dumps({"rev": reader.rev(), "hits": [
        {name: getattr(hit, name) for name in _PUBLIC} for hit in hits]}, ensure_ascii=False))


def main(argv=None):
    """Parse, run one subcommand, and turn every failure into a documented exit code."""
    try:
        args = _parser().parse_args(argv)
        (_build if args.command == "build" else _search)(args)
    except SystemExit as exc:  # --help exits 0; anything else is not a success
        return 0 if not exc.code else _EXIT_CODES[InternalError]
    except FoamKBError as exc:
        # A bare FoamKBError names no code of its own; treat it as INTERNAL_ERROR.
        print(f"{getattr(exc, 'code', InternalError.code)}: {exc}", file=sys.stderr)
        return _EXIT_CODES.get(type(exc), _EXIT_CODES[InternalError])
    except Exception as exc:  # noqa: BLE001 - unexpected failures are still INTERNAL_ERROR
        print(f"{InternalError.code}: {exc}", file=sys.stderr)
        return _EXIT_CODES[InternalError]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
