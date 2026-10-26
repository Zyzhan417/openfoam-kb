#!/usr/bin/env python3
"""Build `scripts/accept_sets.json` from the raw enumerations (007 T003).

Why a generator and not a hand-edited JSON: the four calibers are pure set arithmetic on the raw
rounds (`s1`, `s2`, `s1&s2`, `s1|s2`). Hand-editing them is where a post-hoc tweak would hide --
one path added "because it should be right" and the arithmetic no longer holds. Generating them
means `verify_accept_sets.py` can check the relationship exactly instead of trusting a note.

What replaced the third-person arbitration (spec FR-002): the original plan said "a path only one
round listed must be arbitrated by a third person". Measuring it showed the two rounds disagree
75% of the time, so arbitration would have had to settle most of the ruler by hand -- which is
the failure mode the arbitration was supposed to prevent. Reporting four calibers removes the
human step entirely: nothing is merged, everything is arithmetic.

    PYTHONPATH=src .venv/bin/python scripts/build_accept_sets.py            # write
    PYTHONPATH=src .venv/bin/python scripts/build_accept_sets.py --check    # fail if stale
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "scripts" / "accept_sets"
OUT = ROOT / "scripts" / "accept_sets.json"

# (raw round 1, raw round 2 or None) per panel. New questions got a blind second round late
# (T005b), so the file may legitimately carry one caliber for them until that lands.
PANELS = {
    "old": (RAW / "raw_s1.json", RAW / "raw_s2.json"),
    "new": (RAW / "raw_new.json", RAW / "raw_new_s2.json"),
}


def load_round(path):
    """qid -> {path: {start_line, evidence, why, basename_shared?}}"""
    if not path.exists():
        return None
    doc = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for q in doc["questions"]:
        out[q["qid"]] = {
            e["path"]: {k: v for k, v in e.items() if k != "path"}
            for e in q.get("accept", [])
        }
    return out


def build():
    calibers, questions = [], {}
    for panel, (p1, p2) in PANELS.items():
        r1, r2 = load_round(p1), load_round(p2)
        if r1 is None:
            print(f"[skip] {panel}: {p1.name} 不存在", file=sys.stderr)
            continue
        names = ["s1"] + (["s2", "inter", "union"] if r2 else [])
        for name in names:
            if name not in calibers:
                calibers.append(name)
        for qid in sorted(r1):
            a = set(r1[qid])
            b = set(r2.get(qid, {})) if r2 else set()
            evidence = {p: r1[qid][p] for p in a}
            if r2:
                # Round 2's evidence fills in paths round 1 did not list; round 1 wins overlaps
                # (its digest is the one that was visible when the first freeze happened).
                for p in b - a:
                    evidence[p] = r2[qid][p]
                entry = {"panel": panel, "s1": sorted(a), "s2": sorted(b),
                         "inter": sorted(a & b), "union": sorted(a | b)}
            else:
                entry = {"panel": panel, "s1": sorted(a)}
            # A question nobody could answer under either round carries an empty s1. That is a
            # measurement ("no statement found"), not a missing value -- keep it explicit.
            entry["no_statement_found"] = not (a | b)
            entry["evidence"] = evidence
            questions[qid] = entry
    return {"note": ("由 build_accept_sets.py 从 raw_s1/raw_s2/raw_new/raw_new_s2 生成；"
                     "四口径 = s1 / s2 / 交集 / 并集，不做人工合并"),
            "calibers": calibers, "questions": questions}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只比对，不写盘；不一致则 exit 1")
    args = ap.parse_args(argv)

    doc = build()
    text = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + "\n"
    if args.check:
        if not OUT.exists():
            print(f"STALE: {OUT} 不存在", file=sys.stderr)
            return 1
        # 比**内容**不比字节序：`evidence` 的键来自 set 迭代，顺序随 PYTHONHASHSEED 变，
        # 直接比字符串会给出「内容完全相同却报 STALE」的假红。审计上传物时踩到过一次。
        if json.loads(OUT.read_text(encoding="utf-8")) != json.loads(text):
            print(f"STALE: {OUT} 与 raw 轮次不一致（有人手改过，或 raw 更新后没重生成）",
                  file=sys.stderr)
            return 1
        print(f"[PASS] accept_sets.json 与 raw 轮次一致（{len(doc['questions'])} 条题，"
              f"口径 {doc['calibers']}）")
        return 0

    OUT.write_text(text, encoding="utf-8")
    old = [q for q in doc["questions"].values() if q["panel"] == "old"]
    new = [q for q in doc["questions"].values() if q["panel"] == "new"]
    print(f"写入 {OUT}：{len(doc['questions'])} 条题（旧 {len(old)} / 新 {len(new)}），"
          f"口径 {doc['calibers']}")
    for qid, q in sorted(doc["questions"].items()):
        sizes = " ".join(f"{c}={len(q.get(c) or [])}" for c in doc["calibers"] if c in q)
        print(f"  {qid:4s} {sizes}")
    # The denominators differ per caliber, and that has to be visible at generation time rather
    # than discovered during reporting: an empty set under `inter` is not an unanswered question,
    # it is a question the conservative caliber cannot score at all.
    print("\n各口径分母（= 集合非空的题数）：")
    for panel in ("old", "new"):
        block = [q for q in doc["questions"].values() if q["panel"] == panel]
        parts = []
        for c in doc["calibers"]:
            n = sum(1 for q in block if q.get(c))
            parts.append(f"{c}={n}/{len(block)}")
        print(f"  {panel}: " + "  ".join(parts))
    empty = [(qid, c) for qid, q in sorted(doc["questions"].items())
             for c in doc["calibers"] if c in q and not q[c]]
    if empty:
        print("  空集（该口径下不可评分，不计入分母）：" +
              ", ".join(f"{q}/{c}" for q, c in empty))
    return 0


if __name__ == "__main__":
    sys.exit(main())
