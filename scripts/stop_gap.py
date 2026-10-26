#!/usr/bin/env python3
"""SC-005: the stop gap -- runs that had the answer inside their first 3 calls but did not
hand it in. Unit is **runs**, not percentage points (FR-010 compares it with a run count).

Standalone per `plan.md`: this mode would have pushed `judge_runs.py` past 400 lines, and
`verify_structure.py` does not scan `scripts/`, so the overrun would not have gone red.

    PYTHONPATH=src .venv/bin/python scripts/stop_gap.py --caliber s1
    PYTHONPATH=src .venv/bin/python scripts/stop_gap.py --caliber s1 --questions <new set> \
        --runs reports/.orchestrator/phase_summaries/phase_007-runs
"""
import argparse
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import judge_core  # noqa: E402
from judge_core import CAPS, load_questions, read_jsonl, run_is_valid  # noqa: E402,F401

DEFAULT_INDEX = pathlib.Path.home() / ".local" / "share" / "foamkb" / "c20_typedef.sqlite"


def stop_gap(index, runs_dir, caliber, questions_path):
    """SC-005: how many runs had the target within their first 3 calls but did not hand it in.

    Unit is **runs**, not percentage points: the gap and the set-switch delta have to be
    comparable, and one run is 1/11 or 1/12 of a denominator, not 8.3% of a story.

    Refuses (exit 2) unless the accept file's sha256 is already recorded in the spec AND the
    freeze log shows no undeclared re-freeze. Running this against an unfrozen ruler would
    produce a number about a ruler that can still move.
    """
    accept = pathlib.Path(ROOT / "scripts" / "accept_sets.json")
    try:
        ruler = judge_core.load_accept(accept, spec_path=ROOT / "specs" / "007-eval-ruler-and-gaps" / "spec.md",
                            caliber=caliber)
    except judge_core.AcceptInvalid as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    log = ROOT / "scripts" / "accept_sets" / "freeze-log.jsonl"
    if not log.exists():
        print("ACCEPT_INVALID: 无 freeze-log.jsonl，无法确认冻结顺序", file=sys.stderr)
        return 2
    entries = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    undeclared = [e for e in entries if len(entries) > 1 and not e.get("reason")]
    if undeclared:
        print(f"ACCEPT_INVALID: {len(undeclared)} 次重冻结没有写原因，先补 freeze-log",
              file=sys.stderr)
        return 2
    print(f"尺子：{caliber}，sha 已核，freeze-log {len(entries)} 条（无未说明的重冻结）")

    doc = json.loads(pathlib.Path(questions_path).read_text(encoding="utf-8"))
    questions = {q["qid"]: q for q in doc["questions"]}
    pos = [q for q in questions.values() if q["kind"] == "positive"]
    print(f"\n=== 判据缺口 stop_gap（cap=3 已命中 vs 最终交对；单位：条，分母 {len(pos)}）===")
    out = {}
    for arm in ("A", "B"):
        early_hit, final_ok, dropped = [], [], []
        for q in pos:
            tp = runs_dir / arm / f"{q['qid']}.transcript.jsonl"
            ap = runs_dir / arm / f"{q['qid']}.answers.jsonl"
            if not tp.exists():
                continue
            calls, answers, results = judge_core.judge_run(index, q, tp, ap, _rev(index), accept=ruler)
            ok, _ = run_is_valid(calls, answers)
            if not ok:
                continue

            def verdict_at(cap):
                elig = [r for r in results if isinstance(r["at_call"], int) and r["at_call"] <= cap]
                return elig[-1]["verdict"] if elig else "missing"

            got_early = verdict_at(3) == "done"
            got_final = verdict_at(15) == "done"
            if got_early:
                early_hit.append(q["qid"])
            if got_final:
                final_ok.append(q["qid"])
            if got_early and not got_final:
                dropped.append(q["qid"])
        out[arm] = {"early": early_hit, "final": final_ok, "dropped": dropped}
        gap = len(early_hit) - len(final_ok)
        gained = [q for q in final_ok if q not in early_hit]
        print(f"  臂 {arm}: cap3 已命中 {len(early_hit)} 条，最终交对 {len(final_ok)} 条，"
              f"stop_gap = {gap} 条")
        # A zero gap can still hide churn: something dropped after being found early while
        # something else was only found late. Printing the three lists makes that visible instead
        # of letting "stop_gap = 0" read as "nothing was lost".
        print(f"      早命中后丢掉 ({len(dropped)}): {', '.join(dropped) if dropped else '无'}")
        print(f"      后来才命中   ({len(gained)}): {', '.join(gained) if gained else '无'}")
        if gap == 0 and (dropped or gained):
            print(f"      [注意] gap 为 0 但有 {len(dropped)} 丢 {len(gained)} 得，两者相抵；"
                  f"「0」不等于「无损耗」")
    return 0


def _rev(index):
    return sqlite3.connect(str(index)).execute(
        "SELECT value FROM meta WHERE key='rev'").fetchone()[0]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=str(DEFAULT_INDEX))
    ap.add_argument("--caliber", required=True, choices=("s1", "s2", "inter", "union"))
    ap.add_argument("--questions", default=str(QUESTIONS_DEFAULT))
    ap.add_argument("--runs", default=str(ROOT / "reports" / ".orchestrator" /
                                          "phase_summaries" / "phase_006-runs"))
    args = ap.parse_args(argv)
    index = pathlib.Path(args.index)
    if not index.exists():
        print(f"INDEX_UNAVAILABLE: {index}", file=sys.stderr)
        return 4
    return stop_gap(index, pathlib.Path(args.runs), args.caliber, args.questions)


QUESTIONS_DEFAULT = ROOT / "scripts" / "task_eval_questions.json"

if __name__ == "__main__":
    sys.exit(main())
