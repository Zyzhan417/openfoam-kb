#!/usr/bin/env python3
"""Deterministic 300-file pilot sample (T023 / ARCHITECTURE §10).

The sample is drawn by rule, never by hand: each category takes its files in sorted path
order from the first N that qualify. Running this twice on the same corpus yields byte-
identical output, so `reports/pilot-300.txt` can be diffed across runs to prove the corpus
and the selection rule have not drifted.

Writes the file list to stdout (and to reports/pilot-300.txt with --write), one relative
POSIX path per line, grouped by category with a `# <category> (<n>)` header. Header lines
start with '#' so the file can be fed straight to `--include` loops after a grep -v '#'.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/pilot_sample.py [--corpus DIR] [--write]
"""
import argparse
import pathlib
import sys

# (label, predicate on the relative posix path, how many to take)
# Counts and categories are ARCHITECTURE §10 verbatim. `>30KB` is applied as a size filter.
#
# Every category is restricted to OpenFOAM source files (.C / .H). This is not a narrowing
# of the rule -- it is what the rule always meant: with the source filter applied, the four
# directory categories reproduce §10's stated populations exactly (1285 / 223 / 861 / 439)
# and the >30KB category reproduces its 94 (92 .C + 2 .H). Without the filter the >30KB
# bucket picks up .so / .pdf / .gz payloads and the build dies on the first PDF
# ("file is not valid UTF-8"), which tests nothing about chunk boundaries.
SOURCE_SUFFIXES = (".C", ".H")


def is_source(path):
    return path.endswith(SOURCE_SUFFIXES)


CATEGORIES = [
    ("src/finiteVolume",
     lambda p, size: p.startswith("src/finiteVolume/") and is_source(p), 80),
    ("src/MomentumTransportModels",
     lambda p, size: p.startswith("src/MomentumTransportModels/") and is_source(p), 70),
    ("applications/modules",
     lambda p, size: p.startswith("applications/modules/") and is_source(p), 60),
    ("src/OpenFOAM/primitives",
     lambda p, size: p.startswith("src/OpenFOAM/primitives/") and is_source(p), 40),
    ("single-file-over-30KB",
     lambda p, size: is_source(p) and size > 30 * 1024, 25),
    ("Make/files-and-options",
     lambda p, size: p.endswith(("/files", "/options")) and "/Make/" in "/" + p, 25),
]

TOTAL = 300
EXCLUDED_PARTS = ("lnInclude/", "platforms/", ".git/")


def sweep(corpus):
    """Every candidate file in the corpus with its size, lnInclude and friends excluded."""
    root = pathlib.Path(corpus)
    found = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in rel for part in EXCLUDED_PARTS):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        found.append((rel, size))
    found.sort()
    return found


def select(files):
    """Take the first N qualifying paths per category, in sorted order. Deterministic."""
    chosen, taken = [], set()
    for label, predicate, quota in CATEGORIES:
        bucket = [(rel, size) for rel, size in files if predicate(rel, size)]
        picked = []
        for rel, size in bucket:
            if len(picked) >= quota:
                break
            if rel in taken:
                continue
            picked.append(rel)
            taken.add(rel)
        chosen.append((label, quota, len(bucket), picked))
    return chosen


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="/opt/openfoam13")
    ap.add_argument("--write", action="store_true",
                    help="also write reports/pilot-300.txt")
    args = ap.parse_args()

    if not pathlib.Path(args.corpus).is_dir():
        raise SystemExit(f"corpus is not a directory: {args.corpus}")

    files = sweep(args.corpus)
    chosen = select(files)

    lines, total = [], 0
    for label, quota, available, picked in chosen:
        lines.append(f"# {label} ({len(picked)}/{quota}, 候选 {available})")
        short = len(picked) < quota
        for rel in picked:
            lines.append(rel)
        total += len(picked)
        state = "SHORT" if short else "ok"
        print(f"  {label:<32} {len(picked):>3}/{quota:<3} 候选 {available:>5}  {state}",
              file=sys.stderr)

    if total != TOTAL:
        print(f"pilot sample is {total} files, ARCHITECTURE §10 requires {TOTAL}",
              file=sys.stderr)
        return 1

    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if args.write:
        out = pathlib.Path(__file__).resolve().parent.parent / "reports/pilot-300.txt"
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}", file=sys.stderr)
    print(f"total {total} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
