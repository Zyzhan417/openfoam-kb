#!/usr/bin/env python3
"""Fixture-driven self-test for the task-level judge (AGENTS A3/A21).

`judge_runs.py` is a *judgement function*: it decides whether an agent's answer counts. A21
says such a function needs both directions exercised — pin its predicate `True` and the
"must be caught" case has to go red; pin it `False` and the "must be accepted" case has to.
This script is the "must be caught/accepted" half: it builds a two-file synthetic corpus,
indexes it with the product's own `build`, then feeds the judge nine hand-made answers that
cover every verdict class. `scripts/mutations/006-task-eval.json` is the other half.

The index is built here rather than reused from ~/.local/share because a self-test that needs
a 98 MB artefact cannot run on a fresh clone, and a gate that gets skipped is not a gate.

    PYTHONPATH=src .venv/bin/python scripts/self_test_judge.py
Exit 0 only when all nine cases land in their expected verdict.
"""
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import judge_runs  # noqa: E402

CORPUS = {
    "src/foo.H": """/*--------------------------------*- C++ -*----------------------------------*\\
Description
    Foo transports a scalar field through cells.
\\*---------------------------------------------------------------------------*/

class Foo
{
    scalar value_;
};
""",
    "src/bar.H": """/*--------------------------------*- C++ -*----------------------------------*\\
Description
    Bar cuts a mesh into pieces.
\\*---------------------------------------------------------------------------*/

class Bar
{
    label n_;
};
""",
    "src/baz.H": """/*--------------------------------*- C++ -*----------------------------------*\\
Description
    Baz is only here to be a block the search never returned.
\\*---------------------------------------------------------------------------*/

class Baz
{
    label m_;
};
""",
}

POSITIVE = {"qid": "T1", "query": "foo", "kind": "positive",
            "target_basename": "foo.H", "target_path": "src/foo.H"}
EXCLUDED = {"qid": "T2", "query": "pressure coupling", "kind": "excluded",
            "falsified_negative_reason": "fixture: 前提已被实测推翻"}


def build_index(tmp):
    corpus = tmp / "corpus"
    for rel, text in CORPUS.items():
        path = corpus / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    index = tmp / "kb.sqlite"
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run([sys.executable, "-m", "foamkb.cli", "build", "--corpus", str(corpus),
                           "--index", str(index), "--include", "**/*.H"],
                          cwd=str(ROOT), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"fixture build failed: {proc.stderr[-400:]}")
    conn = sqlite3.connect(str(index))
    rev = conn.execute("SELECT value FROM meta WHERE key='rev'").fetchone()[0]
    blocks = {(p, s): (e, t) for p, s, e, t in
              conn.execute("SELECT path, start_line, end_line, text FROM chunks")}
    conn.close()
    return index, rev, blocks


def write_run(tmp, hits, answers):
    run = tmp / "run"
    run.mkdir(exist_ok=True)
    (run / "t.transcript.jsonl").write_text(
        json.dumps({"seq": 0, "argv": [], "exit": 0, "hits": hits}) + "\n", encoding="utf-8")
    (run / "t.answers.jsonl").write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in answers) + "\n", encoding="utf-8")
    return run / "t.transcript.jsonl", run / "t.answers.jsonl"


def cases(rev, blocks):
    """Nine answers, each with the verdict the judge must return."""
    starts = {p: s for (p, s) in blocks if p.endswith(".H")}
    foo_start, bar_start, baz_start = starts["src/foo.H"], starts["src/bar.H"], starts["src/baz.H"]
    foo_text = blocks[("src/foo.H", foo_start)][1]
    bar_text = blocks[("src/bar.H", bar_start)][1]
    baz_text = blocks[("src/baz.H", baz_start)][1]
    foo_ref = f"{rev}:src/foo.H::{foo_start}"
    bar_ref = f"{rev}:src/bar.H::{bar_start}"
    baz_ref = f"{rev}:src/baz.H::{baz_start}"
    quote = foo_text.strip().splitlines()[0]
    hits = [{"ref": r, "path": p, "start_line": 0, "end_line": 0,
             "symbol": None, "kind": "text", "truncated": False}
            for r, p in ((foo_ref, "src/foo.H"), (bar_ref, "src/bar.H"))]
    return hits, [
        ("verbatim quote from a cited ref", POSITIVE,
         {"at_call": 3, "ref": foo_ref, "quote": quote}, "done"),
        ("quote that is not in the block", POSITIVE,
         {"at_call": 3, "ref": foo_ref, "quote": "THIS SENTENCE IS NOT IN THE BLOCK"},
         "fabrication"),
        ("real block never returned by search", POSITIVE,
         {"at_call": 3, "ref": baz_ref, "quote": baz_text.strip().splitlines()[0]}, "outside_tool"),
        ("ref to a line that is not a block start", POSITIVE,
         {"at_call": 3, "ref": f"{rev}:src/foo.H::9999", "quote": quote}, "fabrication"),
        ("no ref at all", POSITIVE, {"at_call": 3, "ref": None, "quote": ""}, "not_done"),
        # C6: an `excluded` row is never scored -- neither a ref nor an empty answer moves it.
        # Scoring it "fabrication" was the false accusation this branch exists to prevent.
        ("excluded row answered with a file", EXCLUDED,
         {"at_call": 3, "ref": foo_ref, "quote": quote}, "excluded"),
        ("excluded row left empty", EXCLUDED,
         {"at_call": 3, "ref": None, "quote": None}, "excluded"),
        ("rev mismatch", POSITIVE,
         {"at_call": 3, "ref": f"deadbeef:src/foo.H::{foo_start}", "quote": quote},
         "fabrication"),
        ("right quote, wrong file (bar was cited from search)", POSITIVE,
         {"at_call": 3, "ref": bar_ref, "quote": bar_text.strip().splitlines()[0]}, "not_done"),
    ]


def accept_cases(rev, blocks):
    """T013: four cases for `--accept`. Written before the mode exists (原则 II)."""
    starts = {p: s for (p, s) in blocks if p.endswith(".H")}
    foo_start, bar_start = starts["src/foo.H"], starts["src/bar.H"]
    foo_text = blocks[("src/foo.H", foo_start)][1]
    foo_ref = f"{rev}:src/foo.H::{foo_start}"
    bar_ref = f"{rev}:src/bar.H::{bar_start}"
    quote = foo_text.strip().splitlines()[0]
    hits = [{"ref": r, "path": p, "start_line": 0, "end_line": 0,
             "symbol": None, "kind": "text", "truncated": False}
            for r, p in ((foo_ref, "src/foo.H"), (bar_ref, "src/bar.H"))]
    # bar.H sits in the same directory as foo.H -- that is the point of case 2: under the
    # accept ruler "same directory" earns nothing, only a listed path does.
    accept = {"T1": {"s1": ["src/foo.H"]}}
    return hits, accept, [
        ("accept: cited path is in the accept set", POSITIVE,
         {"at_call": 3, "ref": foo_ref, "quote": quote}, "done"),
        ("accept: cited path is outside the set (same dir)", POSITIVE,
         {"at_call": 3, "ref": bar_ref, "quote": blocks[("src/bar.H", bar_start)][1].strip().splitlines()[0]},
         "not_done"),
    ]


def main():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="judge-selftest-"))
    try:
        index, rev, blocks = build_index(tmp)
        hits, rows = cases(rev, blocks)
        failures = 0
        for name, question, answer, expected in rows:
            tp, ap = write_run(tmp, hits, [answer])
            _calls, _answers, results = judge_runs.judge_run(index, question, tp, ap, rev)
            got = results[0]["verdict"]
            ok = got == expected
            failures += 0 if ok else 1
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: 期望 {expected}，实测 {got}"
                  f"（{results[0]['reason']}）")

        # T013: the accept ruler, plus its two refusal paths. The refusals use exit 2 and not 4
        # on purpose -- 4 means "index or run records unusable", so reusing it here would send
        # the caller off to rebuild a perfectly good index (D12).
        ahits, accept, arows = accept_cases(rev, blocks)
        accept_file = tmp / "accept.json"
        accept_file.write_text(json.dumps({"calibers": ["s1"], "questions": accept}),
                               encoding="utf-8")
        # Resolve through the production loader, not by hand: the whole point of case 1 and 2 is
        # to exercise the real path from file to verdict.
        ruler = judge_runs.load_accept(accept_file, caliber="s1")
        for name, question, answer, expected in arows:
            tp, ap = write_run(tmp, ahits, [answer])
            _c, _a, results = judge_runs.judge_run(index, question, tp, ap, rev, accept=ruler)
            got = results[0]["verdict"]
            ok = got == expected
            failures += 0 if ok else 1
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: 期望 {expected}，实测 {got}"
                  f"（{results[0]['reason']}）")

        failures += check_accept_refusals(tmp, index, rev, ahits, accept_file)
        total = len(rows) + len(arows) + 2
        print(f"\ncases={total} failures={failures}")
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_accept_refusals(tmp, index, rev, hits, accept_file):
    """Cases 3 and 4: a missing accept file and a sha mismatch must both be ACCEPT_INVALID/2."""
    failures = 0
    question = POSITIVE
    tp, ap = write_run(tmp, hits, [{"at_call": 3, "ref": f"{rev}:src/foo.H::1", "quote": "x"}])

    missing = tmp / "nope.json"
    try:
        judge_runs.load_accept(missing, spec_path=None, caliber="s1")
        print("[FAIL] accept: 文件缺失应拒绝，实际接受了")
        failures += 1
    except judge_runs.AcceptInvalid as exc:
        ok = exc.exit_code == 2 and "ACCEPT_INVALID" in str(exc)
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] accept: 文件缺失 → ACCEPT_INVALID exit={exc.exit_code}")

    # sha mismatch: the spec says a different digest than the file's own.
    spec = tmp / "spec.md"
    spec.write_text("`accept_sets.json` sha256 `" + "0" * 64 + "`\n", encoding="utf-8")
    try:
        judge_runs.load_accept(accept_file, spec_path=spec, caliber="s1")
        print("[FAIL] accept: sha 与 spec 不符应拒绝，实际接受了")
        failures += 1
    except judge_runs.AcceptInvalid as exc:
        ok = exc.exit_code == 2 and "ACCEPT_INVALID" in str(exc)
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] accept: sha 不符 → ACCEPT_INVALID exit={exc.exit_code}")
    return failures


if __name__ == "__main__":
    sys.exit(main())
