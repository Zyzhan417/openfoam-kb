#!/usr/bin/env python3
"""A/B rank comparison for the ranking acceptance tables: the SC-003 evidence command.

`verify_retrieval.py` asserts lines; it does not print a same/better/worse tally, and a
tally that only exists in a phase summary is not evidence (AGENTS A1: a third party must be
able to run the stated command and see the stated numbers). This script IS that command.

It compares two (source tree, index) pairs and reports, per declared row:

    same    rank identical on both sides
    better  pre-side missing (outside the top 20) or strictly worse than now
    worse   now missing, or strictly worse than pre

Two accounting bases are printed because they disagree, and quoting only one is the A20
failure this project already shipped once (004: "12 rows" vs 18 unique pairs):

    rows      every row of the five acceptance tables, duplicates included
    pairs     unique (query, target) pairs -- the honest sample size
    queries   distinct queries, compared on the full result sequence

Usage (the pre-side tree is a `git worktree add` of the commit before the change):

    PYTHONPATH=src .venv/bin/python scripts/compare_retrieval.py \
        --base-root /path/to/worktree --base-index c19.sqlite \
        --head-index c20.sqlite [--corpus /opt/openfoam13]

Exit 0 when `worse == 0` on the pair basis; 1 otherwise.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOP_K = 20


def load_tables(path):
    """Import verify_retrieval.py purely for its data tables (no assertions are run)."""
    spec = importlib.util.spec_from_file_location("declared_for_compare", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = []
    for query, target, _limit in module.REGRESSION:
        rows.append(("REGRESSION", query, target))
    for query, target, _limit, _before in module.NATURAL_TARGETS:
        rows.append(("NATURAL_TARGETS", query, target))
    for query, target, _baseline in module.IDENTIFIER_BASELINE:
        rows.append(("IDENTIFIER_BASELINE", query, target))
    for query, target, _baseline in module.NATURAL_GUARD:
        rows.append(("NATURAL_GUARD", query, target))
    for query, target, _baseline in module.MIXED_BASELINE:
        rows.append(("MIXED_BASELINE", query, target))
    for query, target, _limit, _before in getattr(module, "TYPE_NAME_TARGETS", ()):
        rows.append(("TYPE_NAME_TARGETS", query, target))
    return rows


def hits(src_root, index, query):
    """Ranked hits from the CLI of ONE source tree; None when the call fails."""
    env = dict(os.environ, PYTHONPATH=str(pathlib.Path(src_root) / "src"))
    proc = subprocess.run(
        [sys.executable, "-m", "foamkb.cli", "search", "--index", str(index),
         "--query", query, "--top-k", str(TOP_K)],
        capture_output=True, text=True, env=env, cwd=str(src_root), timeout=300)
    if proc.returncode != 0:
        return None, proc.stderr.strip()[:120]
    return json.loads(proc.stdout)["hits"], None


def rank_of(hits_list, filename):
    for position, hit in enumerate(hits_list or [], 1):
        if hit["path"] == filename or hit["path"].endswith("/" + filename):
            return position
    return None


def classify(before, after):
    if before == after:
        return "same"
    if before is None or (after is not None and after < before):
        return "better"
    return "worse"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-root", required=True, help="source tree of the pre-change build")
    ap.add_argument("--base-index", required=True, help="index built by that pre-change code")
    ap.add_argument("--head-index", required=True, help="index built by the current code")
    ap.add_argument("--tables", default=str(ROOT / "scripts" / "verify_retrieval.py"))
    args = ap.parse_args()

    rows = load_tables(pathlib.Path(args.tables))
    cache = {}

    def result(src_root, index, query):
        key = (str(src_root), str(index), query)
        if key not in cache:
            cache[key] = hits(src_root, index, query)
        return cache[key]

    print(f"=== 逐行比对（top_k={TOP_K}；改前 {args.base_root} / {args.base_index}）===")
    tallies = {"same": 0, "better": 0, "worse": 0}
    errors, better_rows, worse_rows = [], [], []
    seen = {}
    for group, query, target in rows:
        old, old_err = result(args.base_root, args.base_index, query)
        new, new_err = result(ROOT, args.head_index, query)
        if old_err or new_err:
            errors.append((query, old_err or new_err))
            continue
        verdict = classify(rank_of(old, target), rank_of(new, target))
        tallies[verdict] += 1
        seen.setdefault((query, target), verdict)
        if verdict != "same":
            entry = (group, query, target, rank_of(old, target), rank_of(new, target))
            (better_rows if verdict == "better" else worse_rows).append(entry)

    print(f"rows   : total={len(rows)} same={tallies['same']} "
          f"better={tallies['better']} worse={tallies['worse']}")
    pair_counts = {value: list(seen.values()).count(value) for value in tallies}
    print(f"pairs  : total={len(seen)} same={pair_counts['same']} "
          f"better={pair_counts['better']} worse={pair_counts['worse']}"
          f"  (去重口径：同一 (查询,目标) 在多张表里重复，只计一次)")

    print("\n--- 改善的行 ---")
    for group, query, target, before, after in better_rows:
        print(f"  [{group}] {query!r} -> {target}: {before} → {after}")
    if not better_rows:
        print("  （无）")
    print("\n--- 退化的行 ---")
    for group, query, target, before, after in worse_rows:
        print(f"  [{group}] {query!r} -> {target}: {before} → {after}")
    if not worse_rows:
        print("  （无）")

    queries = []
    for _group, query, _target in rows:
        if query not in queries:
            queries.append(query)
    identical = 0
    for query in queries:
        old, _ = result(args.base_root, args.base_index, query)
        new, _ = result(ROOT, args.head_index, query)
        if [(h["path"], h["start_line"]) for h in (old or [])] == \
                [(h["path"], h["start_line"]) for h in (new or [])]:
            identical += 1
    print(f"\nqueries: total={len(queries)} 结果序列逐条相同={identical} "
          f"变化={len(queries) - identical}")

    if errors:
        print(f"\n命令失败 {len(errors)} 条（比对不完整）:")
        for query, err in errors[:5]:
            print(f"  {query!r}: {err}")
    print(f"\nfailures={len(errors) + pair_counts['worse']}")
    return 0 if not errors and pair_counts["worse"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
