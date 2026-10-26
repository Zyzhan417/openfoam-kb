#!/usr/bin/env python3
"""FR-010 分档：两种 stop_gap 读法的对比，以及 k 的敏感带。

**用户 2026-10-26 决定：`stop_gap` 取新题（读法 b）。** 本脚本因此把 (b) 作为主读法。

但读法 (b) 有一个必须先讲清的数学性质，否则结论会被误读成「数据支持」：

    Δ   = 旧完成 − (新完成 + k)
    gap = 新stop_gap − k
    Δ − gap = 旧完成 − 新完成 − 新stop_gap        ← k 完全抵消

所以 (b) 下 FR-010 的「比大小」退化成固定比较：
    (旧完成 − 新完成)  vs  新stop_gap
本题集上 新stop_gap = −2（两条臂都是），旧−新 = +1~+5（恒正），
故差值恒 > 0 → **必然落「术语建议」，与 k 无关、与题无关、与 agent 表现无关**。

这不是「实测支持术语建议」，而是「这个比较在该读法下没有判别力」。
`--check` 会断言这条恒等式关系，不让它变成一个看起来可复算的结论。
有判别力的只有读法 (a)，那里 k 只进 Δ、会翻转 —— 本脚本保留它作对照。

**数字不硬编码**：完成数读 `reports/.orchestrator/phase007-numbers.json`，stop_gap 由
`scripts/stop_gap.py` 现场算。第一版把 16 个数全写死在源码里，那样底层数据一变，
敏感性分析会静默给出旧结论，一个自己也测不出来的测量工具。

    PYTHONPATH=src .venv/bin/python scripts/fr010_sensitivity.py
    PYTHONPATH=src .venv/bin/python scripts/fr010_sensitivity.py --check
    PYTHONPATH=src .venv/bin/python scripts/fr010_sensitivity.py --json out.json
"""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
NUMBERS = ROOT / "reports" / ".orchestrator" / "phase007-numbers.json"
RUNS_OLD = ROOT / "reports" / ".orchestrator" / "phase_summaries" / "phase_006-runs"
RUNS_NEW = ROOT / "reports" / ".orchestrator" / "phase_summaries" / "phase_007-runs"
QUESTIONS_NEW = ROOT / "scripts" / "task_eval_questions_user.json"
THRESHOLD = 1
K_MAX = 7


def load_counts():
    """(oldA, oldB, newA, newB) per caliber, from the recorded artifact."""
    if not NUMBERS.exists():
        raise SystemExit(f"NUMBERS_MISSING: {NUMBERS}（先跑四口径重判并落盘）")
    raw = json.loads(NUMBERS.read_text(encoding="utf-8"))
    return {cal: dict(oldA=v["oldA"], oldB=v["oldB"], newA=v["newA"], newB=v["newB"])
            for cal, v in raw.items()}


def live_stop_gap(caliber, runs_dir, questions_path=None):
    """(gapA, gapB) computed now, so a stale constant cannot hide behind a green run."""
    cmd = [sys.executable, str(ROOT / "scripts" / "stop_gap.py"), "--caliber", caliber,
           "--runs", str(runs_dir)]
    if questions_path is not None:
        cmd += ["--questions", str(questions_path)]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), env=env)
    if proc.returncode != 0:
        raise SystemExit(f"stop_gap 失败 {caliber}: exit={proc.returncode}\n{proc.stderr[-400:]}")
    found = re.findall(r"臂 (\w): cap3 已命中 \d+ 条，最终交对 \d+ 条，stop_gap = (-?\d+) 条",
                       proc.stdout)
    if len(found) != 2:
        raise SystemExit(f"stop_gap 输出解析失败 {caliber}: {proc.stdout[-300:]}")
    return int(found[0][1]), int(found[1][1])


def grade(delta, gap):
    diff = delta - gap
    if abs(diff) <= THRESHOLD:
        return "未定"
    return "术语建议" if diff > 0 else "证据充分性"


def verdict_at(counts, gaps, gaps_new, k, reading):
    out = {}
    for cal, d in counts.items():
        dA = d["oldA"] - (d["newA"] + k)
        dB = d["oldB"] - (d["newB"] + k)
        if reading == "b":            # 用户决定：stop_gap 取新题，k 同时进 gap
            gA, gB = gaps_new[cal][0] - k, gaps_new[cal][1] - k
        else:                          # 读法 a：stop_gap 取旧题，k 不进 gap
            gA, gB = gaps[cal]
        ga, gb = grade(dA, gA), grade(dB, gB)
        out[cal] = (dA, dB, ga, gb, ga if ga == gb else "未定")
    return out


def check_identity(counts, gaps_new):
    """读法 (b) 的 Δ−gap 必须与 k 无关；不成立说明推导或口径变了，必须停下。"""
    bad = []
    for cal in counts:
        for arm, idx in (("A", 0), ("B", 1)):
            vals = set()
            for k in range(K_MAX + 1):
                d = counts[cal]["old" + arm] - (counts[cal]["new" + arm] + k)
                vals.add(d - (gaps_new[cal][idx] - k))
            if len(vals) != 1:
                bad.append(f"{cal}.{arm}: Δ−gap={sorted(vals)}")
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="核对数据源 + 读法(b)恒等式")
    ap.add_argument("--json", type=pathlib.Path, help="两种读法落盘（可选）")
    args = ap.parse_args(argv)

    counts = load_counts()
    gaps = {cal: live_stop_gap(cal, RUNS_OLD) for cal in counts}
    gaps_new = {cal: live_stop_gap(cal, RUNS_NEW, QUESTIONS_NEW) for cal in counts}
    bad = check_identity(counts, gaps_new)

    if args.check:
        print(f"[PASS] 完成数来自 {NUMBERS.name}；stop_gap 由 stop_gap.py 现场算")
        for cal in counts:
            print(f"  {cal}: 旧题 gap={gaps[cal]}  新题 gap={gaps_new[cal]}")
        if bad:
            print(f"[FAIL] 读法(b) 的 Δ−gap 应与 k 无关，实测不等：{bad}")
            return 1
        print("[PASS] 读法(b) 的 Δ−gap 对 k 恒定（已确认为代数恒等式）")
        ngaps = {v for pair in gaps_new.values() for v in pair}
        if all(v < 0 for v in ngaps):
            print(f"[注意] 新题 stop_gap 全为负 {sorted(ngaps)} → 读法(b) 的结论必然是"
                  f"「术语建议」，与 k 无关、与题无关、与 agent 表现无关")
        return 0

    print("=== 主读法 (b)：stop_gap 取新题（用户 2026-10-26 决定）===")
    print(f"{'k':>2s}  " + "  ".join(f"{c:^30s}" for c in counts))
    for k in range(K_MAX + 1):
        v = verdict_at(counts, gaps, gaps_new, k, "b")
        cells = []
        for cal in counts:
            dA, dB, ga, gb, fin = v[cal]
            cells.append(f"Δ{dA:+d}/Δ{dB:+d} {ga[:4]}/{gb[:4]}→{fin[:4]}")
        print(f"{k:>2d}  " + "  ".join(f"{c:^30s}" for c in cells))

    verdicts_b = {verdict_at(counts, gaps, gaps_new, k, "b")[c][4]
                  for k in range(K_MAX + 1) for c in counts}
    print(f"\n读法 (b) 全部 (k, 口径, 臂) 的落档集合：{verdicts_b}")
    if bad:
        print(f"[FAIL] 恒等式不成立，结论不可引用：{bad}")
        return 1
    if len(verdicts_b) == 1:
        only = next(iter(verdicts_b))
        print(f"**本结论对所有 k 都相同（{only}）**，原因是 Δ−gap 与 k 无关（代数恒等式），")
        print("而不是因为它在多个 k 下被验证过。决策时应按「该读法无判别力」理解，")
        print("不要当成「实测支持术语建议」。下面附有判别力的读法 (a) 作对照。")

    print("\n=== 对照读法 (a)：stop_gap 取旧题（未采用，但它是唯一有判别力的）===")
    for k in range(K_MAX + 1):
        v = verdict_at(counts, gaps, gaps_new, k, "a")
        cells = [f"{v[cal][2][:4]}/{v[cal][3][:4]}→{v[cal][4][:4]}" for cal in counts]
        print(f"{k:>2d}  " + "  ".join(f"{c:^24s}" for c in cells))
    flips: dict[str, int] = {}
    for cal in counts:
        for k in range(K_MAX + 1):
            if verdict_at(counts, gaps, gaps_new, k, "a")[cal][4] == "证据充分性":
                flips[cal] = k
                break
    print("\n  读法 (a) 的翻转阈值：")
    for cal in counts:
        print(f"    {cal}: k = {flips.get(cal, '未翻')}")
    if flips:
        first = min(flips.values())
        who = [c for c, k in flips.items() if k == first][0]
        print(f"  四口径全部仍为「未定」的 k 上界 = {first - 1}"
              f"（最早翻的是 {who}，k={first}）")

    if args.json:
        args.json.write_text(json.dumps(
            {"primary_reading": "b（用户 2026-10-26 决定：stop_gap 取新题）",
             "reading_b": {k: verdict_at(counts, gaps, gaps_new, k, "b")
                           for k in range(K_MAX + 1)},
             "reading_a": {k: verdict_at(counts, gaps, gaps_new, k, "a")
                           for k in range(K_MAX + 1)},
             "identity_holds": not bad,
             "reading_b_invariant_to_k": True,
             "reading_a_flips": flips,
             "sources": {"counts": str(NUMBERS.relative_to(ROOT)),
                         "stop_gap": "scripts/stop_gap.py（现场算；读法 b 用 --questions 指新题）"}},
            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\n落盘 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
