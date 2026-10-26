#!/usr/bin/env python3
"""Measure the vocabulary gap: same target, two wordings (user's vs the corpus's).

Why this exists: 007 rests on the claim "a user's words do not match the corpus's words". That
claim has to be reproducible, not an anecdote from one session. Each pair below asks for the
same concept twice: once in words a person who knows nothing about CFD would use, once in the
words the code itself uses.

The point is the CONTRAST, measured against the **accept set** (007's ruler), not against
"did anything come back" -- any query returns something, so `hits>0` alone proves nothing.

Deliberately reads nothing but the CLI's stdout: same measuring path as the product.
    PYTHONPATH=src .venv/bin/python scripts/probe_vocab_gap.py [--index PATH] [--json OUT]

Limits to quote alongside any number this prints:
  - the denominator is the user-question set's answerable entries (U12 is an empty accept set,
    so it is excluded -- see scripts/verify_user_questions.py for the answerability rule);
  - **both** wordings were constructed by the orchestrator, not blindly; this is a probe, not a
    pre-registered experiment (A22);
  - single-turn CLI, so it is not the same quantity as an agent's multi-turn completion rate;
  - short (2-3 word) queries on purpose: a multi-word AND in FTS5 goes to zero for reasons of
    length, which would masquerade as a vocabulary gap.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_INDEX = pathlib.Path.home() / ".local" / "share" / "foamkb" / "c20_typedef.sqlite"

# qid -> (user's words, the corpus's words). Keys match scripts/accept_sets/raw_new.json.
#
# The corpus wording was chosen by reading what the accept files actually say, then trying
# several phrasings and keeping one that hits. **That is post-hoc selection** -- it biases the
# gap upward, because a phrasing that fails to hit simply got swapped for one that does. It is
# the honest way to keep the contrast meaningful (a corpus query that returns 0 proves nothing),
# but any number from here must be reported as "corpus wording selected to hit", never as
# "the corpus wording has an X% success rate".
PAIRS = {
    "U01": ("settings default value", "startFrom latestTime"),
    "U02": ("lookup name implementation", "runTimeSelection New"),
    "U03": ("progress print line", "Solving residual Initial"),
    "U04": ("output directory file name", "IOobject path instance"),
    "U05": ("resume previous result", "Time findTimes"),
    "U06": ("split work processes", "numberOfSubdomains decomposeParDict"),
    "U07": ("command line options", "argList parse"),
    "U08": ("error file line number", "at line file name"),
    "U09": ("table interpolate missing", "interpolation table"),
    "U10": ("stop criterion rounds", "convergenceControl criteriaSatisfied"),
    "U11": ("unit convert check", "units multiplier compare"),
}


def search(index, query, top_k=20):
    """Raise on failure -- never return [] for an error (D13: no empty result masking errors).

    A first version of this probe passed an out-of-range --max-chars, the CLI rejected it, and
    the caller turned the failure into an empty hit list; the table then read "both sides 0/11",
    which is "my measuring tool is broken" wearing the costume of "the measurement is zero".
    """
    proc = subprocess.run(
        [sys.executable, "-m", "foamkb.cli", "search", "--index", str(index),
         "--query", query, "--top-k", str(top_k), "--max-chars", "200"],
        cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"search failed for {query!r}: exit={proc.returncode} "
                         f"{proc.stderr.strip()[:200]}\n"
                         "refusing to report hits=0 for a failed search")
    return json.loads(proc.stdout)["hits"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=str(DEFAULT_INDEX))
    ap.add_argument("--accept-sets", default=str(ROOT / "scripts/accept_sets/raw_new.json"))
    ap.add_argument("--json", type=pathlib.Path)
    args = ap.parse_args(argv)
    index = pathlib.Path(args.index)
    if not index.exists():
        print(f"INDEX_UNAVAILABLE: {index}", file=sys.stderr)
        return 4

    accept_file = pathlib.Path(args.accept_sets)
    if not accept_file.exists():
        print(f"ACCEPT_SETS_MISSING: {accept_file}", file=sys.stderr)
        return 4
    by_qid = {q["qid"]: q for q in json.loads(accept_file.read_text())["questions"]}

    print(f"{'qid':4s} {'用户词汇（用户会说的话）':30s} {'中':>2s}  "
          f"{'语料词汇（代码里的话）':36s} {'中':>2s}  判读")
    rows, tot, skipped = [], 0, []
    for qid, (user_q, corpus_q) in PAIRS.items():
        if qid not in by_qid:
            skipped.append(f"{qid}: 不在 {accept_file.name}")
            continue
        accept = {a["path"] for a in by_qid[qid]["accept"]}
        if not accept:
            skipped.append(f"{qid}: accept 为空（不可答，按设计排除）")
            continue
        tot += 1
        uh, ch = search(index, user_q), search(index, corpus_q)
        u = len(accept & {h["path"] for h in uh})
        c = len(accept & {h["path"] for h in ch})
        # 判读：只有语料词真能命中，这一对才证明了"缺口"。
        # 见下面 A20 说明——两边都 0 的样本在"缺口"这个方向上是瞎的。
        if c and not u:
            verdict = "缺口"
        elif c and u:
            verdict = "两组都能命中"
        elif not c and u:
            verdict = "反向（语料词没选对）"
        else:
            verdict = "两边都 0（证明不了）"
        rows.append({"qid": qid, "user_query": user_q, "corpus_query": corpus_q,
                     "accept_n": len(accept), "user_hits": u, "corpus_hits": c,
                     "verdict": verdict})
        print(f"{qid:4s} {user_q[:30]:30s} {u:2d}  {corpus_q[:36]:36s} {c:2d}  {verdict}")

    for s in skipped:
        print(f"  [skip] {s}")
    if not tot:
        print("!! 分母为 0，无可测题", file=sys.stderr)
        return 2

    # A20 的口径：报"缺口"时，分母只能是**语料词真有命中**的样本。
    # 语料词也 0 命中的样本没有可对比的基线，它在"缺口"这个方向上是瞎的——
    # 把它算进分母，会让"我的语料词没选对"伪装成"词汇缺口"。
    comparable = [r for r in rows if r["corpus_hits"]]
    gap_n = sum(1 for r in comparable if not r["user_hits"])
    blind = [r["qid"] for r in rows if not r["corpus_hits"]]

    print(f"\n总样本 {tot} 条；其中**语料词确实命中**（成对可比）= {len(comparable)} 条，"
          f"其余 {len(blind)} 条不可比")
    if blind:
        print(f"  不可比（语料词也没命中，需重选语料措辞）：{', '.join(blind)}")
    if comparable:
        print(f"\n在成对可比的 {len(comparable)} 条上：")
        print(f"  用户词命中 accept 集: {sum(1 for r in comparable if r['user_hits'])}/{len(comparable)}"
              f" = {sum(1 for r in comparable if r['user_hits']) / len(comparable):.0%}")
        print(f"  语料词命中 accept 集: {len(comparable)}/{len(comparable)} = 100%")
        print(f"  → 词汇缺口 = {gap_n}/{len(comparable)} = {gap_n / len(comparable):.0%}")
    else:
        print("\n!! 没有任何一条成对可比，本样本不能证明词汇缺口", file=sys.stderr)
        return 2

    print("\n引用这些数字时必须一并给出：")
    print(f"  - 总样本 {tot} 条，**可比 {len(comparable)} 条**，不可比 {len(blind)} 条")
    print("  - 两种提法都由编排方构造，**不盲**；这是探针不是预注册实验")
    print("  - 单轮 CLI 口径，与 agent 多轮完成率不是同一个量")
    print("  - accept 集来自单轮划定，未经第二人复核")

    if args.json:
        args.json.write_text(json.dumps(
            {"tot": tot, "comparable": len(comparable), "gap_n": gap_n,
             "blind": blind, "rows": rows, "skipped": skipped,
             "caveats": [f"总样本 {tot}", f"可比 {len(comparable)}",
                         "语料词是事后挑选使其命中（偏向上放大缺口）",
                         "提法由编排方构造不盲", "单轮 CLI", "accept 单轮划定"]},
            ensure_ascii=False, indent=1) + "\n")
        print(f"\n落盘 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
