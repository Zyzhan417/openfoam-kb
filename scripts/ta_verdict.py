#!/usr/bin/env python3
"""T-A 落档：按预注册（reports/.orchestrator/TA-preregistration.md）算，不得事后改判据。

两套判据集：
  clean = 7 条词表与 accept 文件名零重合（主判据）
  all   = 11 条（含 4 条有重合风险）
落档以 clean 为准；两套不一致则「未定」，不挑一个报。

kill line 先于判据：若 C 臂整体几乎没引用词表里的词，实验无效，只能记「未定」。
判定「引用过」的口径：某条查询里出现的词（小写、去标点）∈ 词表。
"
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNS = ROOT / "reports" / ".orchestrator" / "phase_summaries" / "phase_007-runs"
SETS = ROOT / "scripts" / "accept_sets.json"
VOCAB = ROOT / "scripts" / "cfd_terms.txt"

CLEAN = ["U01", "U04", "U05", "U07", "U08", "U09", "U11"]
RISKY = {"U02": ["field", "patch"], "U03": ["solver"],
         "U06": ["decompose", "decomposition", "reconstruct"], "U10": ["convergence"]}
ALL = CLEAN + sorted(RISKY)


def load_vocab():
    out = set()
    for line in VOCAB.read_text(encoding="utf-8").splitlines():
        w = line.split("#", 1)[0].strip().lower()
        if w:
            out.add(w)
    return out


def queries(arm, qid):
    """All queries from one transcript. **Raises when the file is absent.**

    Returning [] here made a missing run read as "the agent never used a vocab word", which
    then triggered the kill line and printed "未定（kill line）" -- a confident conclusion about
    an experiment that had no data. That is D13 (不得用空结果掩盖错误) in the exact shape the
    rule forbids, found while auditing this file for publication. Missing records now stop the
    run with the same code `judge_runs.py` uses for the same condition (4 = records unusable).
    """
    f = RUNS / arm / f"{qid}.transcript.jsonl"
    if not f.exists():
        print(f"RUNS_UNAVAILABLE: {f} 不存在 —— 读不到运行记录不等于「agent 没用词表词」",
              file=sys.stderr)
        print("这些记录属于开发过程产物，未随公开仓库提供（见 README §7）。", file=sys.stderr)
        raise SystemExit(4)
    cs = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [c["argv"][c["argv"].index("--query") + 1]
            for c in cs if "--query" in (c.get("argv") or [])]


def path_of(ref):
    return ref.partition(":")[2].rpartition("::")[0] if ref else None


def final_path(arm, qid):
    f = RUNS / arm / f"{qid}.answers.jsonl"
    if not f.exists():
        return None
    rows = [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fin = [r for r in rows if r.get("at_call") == 15] or rows
    return path_of(fin[-1].get("ref")) if fin else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args(argv)

    # 先检查记录齐不齐，再打印任何东西：报错前先吐出半张表，读者会以为那半张是结果。
    missing = [q for q in CLEAN + sorted(RISKY)
               for arm in ("A", "C")
               if not (RUNS / arm / f"{q}.transcript.jsonl").exists()]
    if missing:
        print(f"RUNS_UNAVAILABLE: 缺 {len(missing)} 个 transcript（首缺 "
              f"{missing[0]}）—— 读不到运行记录不等于「agent 没用词表词」", file=sys.stderr)
        print("这些记录属于开发过程产物，未随公开仓库提供（见 README §7）。", file=sys.stderr)
        return 4

    doc = json.loads(SETS.read_text(encoding="utf-8"))["questions"]
    vocab = load_vocab()

    # ---------- 逐题 ----------
    print(f"{'qid':4s} {'集':6s} {'A 交':34s} {'C 交':34s} A C  词表词用量")
    rows = {}
    for qid in ALL:
        accept = set(doc[qid].get("s1") or [])
        pa, pc = final_path("A", qid), final_path("C", qid)
        okA, okC = pa in accept, pc in accept
        used = set()
        for q in queries("C", qid):
            for w in q.lower().replace('"', " ").replace(",", " ").split():
                w = w.strip("()[]{}")
                if w in vocab:
                    used.add(w)
        rows[qid] = dict(accept=sorted(accept), A=pa, C=pc, okA=okA, okC=okC,
                         vocab_used=sorted(used), panel="clean" if qid in CLEAN else "risky")
        tag = "clean" if qid in CLEAN else "risk "
        print(f"{qid:4s} {tag:6s} {(pa or 'null').split('/')[-1][:32]:34s} "
              f"{(pc or 'null').split('/')[-1][:32]:34s} {'✓' if okA else ' '} "
              f"{'✓' if okC else ' '}  {len(used)}")

    # ---------- 两套判据集 ----------
    res = {}
    for name, qids in (("clean", CLEAN), ("all", ALL)):
        a = sum(1 for q in qids if rows[q]["okA"])
        c = sum(1 for q in qids if rows[q]["okC"])
        res[name] = dict(A=a, C=c, n=len(qids), delta=c - a)
    print(f"\n=== 两套判据集（预注册口径）===")
    for name in ("clean", "all"):
        r = res[name]
        print(f"  {name:5s}（n={r['n']}）: A {r['A']}/{r['n']}  C {r['C']}/{r['n']}  "
              f"Δ = {r['delta']:+d} 条")

    # ---------- kill line ----------
    total_q = sum(len(queries("C", q)) for q in ALL)
    used_q = sum(1 for q in ALL if rows[q]["vocab_used"])
    used_words = sorted({w for q in ALL for w in rows[q]["vocab_used"]})
    print(f"\n=== kill line 检查（先于判据）===")
    print(f"  C 臂共 {total_q} 条查询；有引用词表词的题 {used_q}/{len(ALL)}；"
          f"累计用到 {len(used_words)} 个不同词表词：{used_words}")
    kill = used_q == 0
    print(f"  → {'触发 kill line（实验无效）' if kill else '未触发（agent 确实看过词表）'}")

    # ---------- 落档 ----------
    print(f"\n=== 落档 ===")
    d_clean, d_all = res["clean"]["delta"], res["all"]["delta"]

    def grade(d):
        if d >= 2:
            return "词表有效"
        if d <= -2:
            return "词表有害"
        return "未定"

    g_clean, g_all = grade(d_clean), grade(d_all)
    print(f"  clean（主判据）Δ={d_clean:+d} → {g_clean}")
    print(f"  all  （对照）  Δ={d_all:+d} → {g_all}")
    if kill:
        final = "未定（kill line：agent 几乎没用词表词）"
    elif g_clean != g_all:
        final = "未定（两套判据集不一致，按预注册不挑一个报）"
    else:
        final = g_clean
    print(f"\n  **最终落档：{final}**")

    print(f"\n局限（引用时必须一并给出）：clean n={res['clean']['n']} → 1 条 = "
          f"{100/res['clean']['n']:.1f}pp；词表 140 词、偏类型/类名；只测一个模型。")

    if args.json:
        args.json.write_text(json.dumps(
            {"per_question": rows, "panels": res, "kill_line": kill,
             "vocab_used_words": used_words, "final": final,
             "prereg": "reports/.orchestrator/TA-preregistration.md"},
            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"  落盘 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
