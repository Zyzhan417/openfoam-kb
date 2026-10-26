#!/usr/bin/env python3
"""T-C：新题 4/11 这个下界要不要补？把「语义邻域」加进尺子看缺口还在不在。

**预注册判据**（跑之前写死）：
  第三口径 = union ∪ 同目录 ∪ 同 stem（机械可判，无需人看原文）
  · 若第三口径下新题完成数 ≥ 旧题 accept 口径 − 1 条 → **词汇缺口不成立**，
    T-A（术语建议）的立论基础被削弱，优先级降到最低
  · 若新题仍比旧题差 ≥ 3 条 → **缺口稳固**，T-A 值得做
  · 否则 → 未定

为什么加这两条松弛：007 §二之二 已证 7/11 新题两臂都落在 union 外，其中 U09 交 `Table.H`
而划定者只列 `Table.C`（同 stem）、U02 交 `TableReaderNew.C` 而划定给的是另一个目录里的
`userTimeNew.C`（不同目录不同 stem）。所以「同 stem」「同目录」正是能抓住这类漏判的最小松弛。

**这一条不改任何已发布的数字**：它只回答「如果尺子再宽一点，缺口还在不在」，
是一个**敏感带**，不是新结论。宽尺子的数字不得替换 §一 的 accept 口径数字（A22/D11）。

    PYTHONPATH=src .venv/bin/python scripts/probe_third_caliber.py
    PYTHONPATH=src .venv/bin/python scripts/probe_third_caliber.py --json out.json
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS = ROOT / "reports" / ".orchestrator" / "phase_summaries"
OLD_RUNS = RUNS / "phase_006-runs"
NEW_RUNS = RUNS / "phase_007-runs"
SETS = ROOT / "scripts" / "accept_sets.json"


def final_ref(runs_dir, arm, qid):
    """Final ref, or None only when the run legitimately produced no ref.

    A missing file is NOT "no ref": treating it that way made this script conclude
    「词汇缺口不成立」 when there were no records at all -- the opposite of the real result
    (缺口稳固). Found while auditing this file for publication; it is D13 in the worst shape,
    because the false conclusion is a **negative** one that would cancel a whole direction.
    """
    ap = runs_dir / arm / f"{qid}.answers.jsonl"
    if not ap.exists():
        print(f"RUNS_UNAVAILABLE: {ap} 不存在 —— 读不到运行记录不等于「本题没交出 ref」",
              file=sys.stderr)
        print("这些记录属于开发过程产物，未随公开仓库提供（见 README §7）。", file=sys.stderr)
        raise SystemExit(4)
    rows = [json.loads(ln) for ln in ap.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fin = [r for r in rows if r.get("at_call") == 15] or rows
    return fin[-1].get("ref") if fin else None


def path_of(ref):
    return ref.partition(":")[2].rpartition("::")[0] if ref else None


def stem(p):
    return p.rsplit(".", 1)[0]


def parent(p):
    return p.rsplit("/", 1)[0]


def in_third(path, allowed):
    """union ∪ 同 stem ∪ 同目录。任一命中即算落点可接受。"""
    if not path:
        return False
    if path in allowed:
        return True
    if any(stem(path) == stem(a) for a in allowed):
        return True
    if any(parent(path) == parent(a) for a in allowed):
        return True
    return False


def tally(runs_dir, qids, doc, relax):
    out = {}
    for arm in ("A", "B"):
        hit = []
        for qid in qids:
            allowed = set(doc[qid].get("s1") or [])
            got = path_of(final_ref(runs_dir, arm, qid))
            if not allowed:
                continue          # 不可答，不入分母
            ok = in_third(got, allowed) if relax else (got in allowed)
            if ok:
                hit.append(qid)
        out[arm] = hit
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args(argv)

    doc = json.loads(SETS.read_text(encoding="utf-8"))["questions"]
    old_qids = [q for q in doc if q.startswith("Q")]
    new_qids = [q for q in doc if q.startswith("U")]

    print("=== 旧题（12 条，006 记录）===")
    old_narrow = tally(OLD_RUNS, old_qids, doc, relax=False)
    old_wide = tally(OLD_RUNS, old_qids, doc, relax=True)
    for arm in ("A", "B"):
        print(f"  臂 {arm}: accept 口径 {len(old_narrow[arm])}/12 → "
              f"第三口径 {len(old_wide[arm])}/12")

    print("\n=== 新题（11 条可答，007 记录）===")
    new_narrow = tally(NEW_RUNS, new_qids, doc, relax=False)
    new_wide = tally(NEW_RUNS, new_qids, doc, relax=True)
    for arm in ("A", "B"):
        print(f"  臂 {arm}: accept 口径 {len(new_narrow[arm])}/11 → "
              f"第三口径 {len(new_wide[arm])}/11")
        gained = sorted(set(new_wide[arm]) - set(new_narrow[arm]))
        if gained:
            print(f"        第三口径新抓住：{gained}")

    print("\n=== 缺口（旧 − 新，单位：条）===")
    results = {}
    for arm in ("A", "B"):
        d_narrow = len(old_narrow[arm]) - len(new_narrow[arm])
        d_wide = len(old_wide[arm]) - len(new_wide[arm])
        results[arm] = dict(old_narrow=len(old_narrow[arm]), old_wide=len(old_wide[arm]),
                            new_narrow=len(new_narrow[arm]), new_wide=len(new_wide[arm]),
                            gap_narrow=d_narrow, gap_wide=d_wide)
        print(f"  臂 {arm}: accept 口径缺口 {d_narrow:+d} 条 → 第三口径缺口 {d_wide:+d} 条")

    # 预注册判据按臂分开
    print("\n=== 结论（按预注册判据，逐臂）===")
    verdicts = {}
    for arm in ("A", "B"):
        g = results[arm]["gap_wide"]
        if g <= 1:
            v = "词汇缺口不成立（T-A 优先级降到最低）"
        elif g >= 3:
            v = "缺口稳固（T-A 值得做）"
        else:
            v = "未定"
        verdicts[arm] = v
        print(f"  臂 {arm}: 第三口径缺口 {g:+d} 条 → {v}")
    agree = len(set(verdicts.values())) == 1
    print(f"\n  两臂{'一致' if agree else '**不一致**'} → 合计落档："
          f"{list(verdicts.values())[0] if agree else '未定（两臂档位不同，不挑一个报）'}")

    print("\n注意：这是**敏感带**，不替换 §一 的 accept 口径数字。"
          "宽尺子的数字只回答「尺子再宽一点缺口还在不在」。")

    if args.json:
        args.json.write_text(json.dumps(
            {"old_narrow": old_narrow, "old_wide": old_wide,
             "new_narrow": new_narrow, "new_wide": new_wide,
             "gap": results, "verdicts": verdicts,
             "note": "第三口径 = union ∪ 同stem ∪ 同目录；敏感带，不替换 accept 口径"},
            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"落盘 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
