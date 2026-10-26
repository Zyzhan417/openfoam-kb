#!/usr/bin/env python3
"""T-B：porter 把泛词归并成高频词干，这个伤害有多大？（只读，零产品代码）

背景实测（phase_007 §三之二）：`set`/`setting`/`settings`/`sets` 在索引里是**同一个词干**，
命中 7,922 块（全库 8.6%）；`point`/`points` 11,283 块。

**预注册判据**（跑之前写死，不许事后调）：

  B1 纯噪声率：把每个泛词干**单独**当查询，看它 top-20 里有多少块与任何 accept 集相关。
     若中位纯噪声率 ≥ 90% → 该词「没有定位价值」（它出现得多，但出现的地方都不是答案）。
  B2 收益：对每条题，比较「用户原词全查」与「去掉泛词干后查」时，accept 最优名次的变化。
     · ≥ 3 题的名次变好（或首次进入 top-20）→ **值得改分词**
     · 1–2 题                              → **未定**，不立项，先补样本量
     · 0 题                                → **不值得**（主导因素是真词汇差异，不是泛词归并）

口径沿革（A22：改判据必须留痕）：初版 B1 定义为「top-20 里『只由泛词命中的块』占比」，
实测发现它在「查询全由泛词构成」时退化（剔完泛词查询为空，比例恒 0/0）——**那个口径判不了任何东西**。
改为上面的「纯噪声率」，它不依赖剔除后的查询是否还有内容。

**测量路径**：走产品 CLI（`python -m foamkb.cli search`），与 `probe_vocab_gap.py` 同一条路（A28）。
初版直连 sqlite，用 `SELECT path, start_line FROM chunks_fts` —— `chunks_fts` 根本没有
`start_line` 列，`except OperationalError: return []` 把错吞成了「所有查询 0 命中」。
**测量脚本失败必须抛错，不得返回空**（D13 扩展，本轮刚写进 AGENTS.md，几小时后自己又犯）。

    PYTHONPATH=src .venv/bin/python scripts/probe_porter_pollution.py
    PYTHONPATH=src .venv/bin/python scripts/probe_porter_pollution.py --json out.json
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# 默认值只是方便本机复跑；部署者用 --index 指定自己的库。
# 审计上传物时发现这里原本只有默认值、没有 --index，等于把本机路径名
# （c20_typedef 是本项目的索引命名）当成了对外契约。
INDEX = pathlib.Path.home() / ".local" / "share" / "foamkb" / "c20_typedef.sqlite"
SETS = ROOT / "scripts" / "accept_sets.json"
TOP_K = 20
NOISE_THRESHOLD = 0.90

# 用户原词查询（冻结于 007，与 probe_vocab_gap.py 的 user 侧一致）
USER_QUERIES = {
    "U01": "settings default value",
    "U02": "lookup name implementation",
    "U03": "progress print line",
    "U04": "output directory file name",
    "U05": "resume previous result",
    "U06": "split work processes",
    "U07": "command line options",
    "U08": "error file line number",
    "U09": "table interpolate missing",
    "U10": "stop criterion rounds",
    "U11": "unit convert check",
}


def cli_search(query, top_k=TOP_K):
    """Run the product CLI. Raise on failure -- never return [] for an error."""
    proc = subprocess.run(
        [sys.executable, "-m", "foamkb.cli", "search", "--index", str(INDEX),
         "--query", query, "--top-k", str(top_k), "--max-chars", "200"],
        cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"搜索失败 query={query!r} exit={proc.returncode}\n"
                         f"{proc.stderr.strip()[:300]}\n"
                         "拒绝把失败当 0 命中（D13 扩展）")
    return json.loads(proc.stdout)["hits"]


def rank_of(hits, accept):
    for i, h in enumerate(hits, 1):
        if h["path"] in accept:
            return i
    return None


def main(argv=None):
    global INDEX
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--index", type=pathlib.Path, default=INDEX,
                    help=f"索引路径（默认 {INDEX}）")
    args = ap.parse_args(argv)
    INDEX = args.index

    doc = json.loads(SETS.read_text(encoding="utf-8"))["questions"]
    all_accept = {p for q in doc.values() for c in ("s1", "s2") for p in (q.get(c) or [])}

    # ---------- B1：泛词干单独查，看纯噪声率 ----------
    print("=== B1 纯噪声率：把泛词干单独当查询，top-%d 里与 accept 相关的比例 ===" % TOP_K)
    generic = ["settings", "name", "file", "value", "options", "error", "table", "lookup", "default"]
    b1 = {}
    for w in generic:
        hits = cli_search(w)
        relevant = sum(1 for h in hits if h["path"] in all_accept)
        noise = 1 - (relevant / len(hits)) if hits else None
        b1[w] = dict(hits=len(hits), relevant=relevant, noise_ratio=noise)
        print(f"  {w:10s} top{len(hits):2d}  相关 {relevant:2d}  纯噪声率 "
              f"{'n/a' if noise is None else f'{noise:.0%}'}")
    ratios = sorted(v["noise_ratio"] for v in b1.values() if v["noise_ratio"] is not None)
    median_noise = ratios[len(ratios) // 2] if ratios else None
    print(f"\n  中位纯噪声率 = {'n/a' if median_noise is None else f'{median_noise:.0%}'}"
          f"（判据：≥{NOISE_THRESHOLD:.0%} 视为该词无定位价值）")

    # ---------- B2：去掉泛词干后，accept 名次是否变好 ----------
    print(f"\n=== B2 收益：去泛词干后 accept 最优名次变化 ===")
    b2, gained = {}, []
    for qid, query in USER_QUERIES.items():
        accept = set(doc.get(qid, {}).get("s1") or [])
        if not accept:
            continue
        keep = [w for w in query.split() if w not in generic]
        before = rank_of(cli_search(query), accept)
        after = rank_of(cli_search(" ".join(keep)), accept) if keep else None
        b2[qid] = dict(query=query, kept=" ".join(keep), before=before, after=after)
        better = (after is not None and (before is None or after < before))
        if better:
            gained.append(qid)
        mark = "↑ 变好" if better else ("—" if before == after else "↓ 变差")
        print(f"  {qid} 原 {str(before):>4s} → 去泛词 {str(after):>4s}  {mark}"
              f"   保留词：{b2[qid]['kept'] or '（全被剔除）'}")

    n = len(gained)
    if median_noise is None:
        verdict = "未定（B1 无可测泛词）"
    elif median_noise < NOISE_THRESHOLD:
        verdict = "不值得（泛词并非纯噪声，它不是主导因素）"
    else:
        verdict = ("值得改分词" if n >= 3 else ("未定" if n >= 1 else "不值得"))

    print(f"\n=== 结论（按预注册判据）===")
    print(f"  B1 中位纯噪声率 {median_noise:.0%}（阈值 {NOISE_THRESHOLD:.0%}）"
          if median_noise is not None else "  B1 无可测泛词")
    print(f"  B2 名次变好的题数 {n}（≥3 值得 / 1–2 未定 / 0 不值得）")
    print(f"  → **{verdict}**")

    if args.json:
        args.json.write_text(json.dumps(
            {"B1_noise": b1, "B1_median_noise": median_noise,
             "B1_threshold": NOISE_THRESHOLD,
             "B2_per_question": b2, "B2_gained": gained,
             "B2_thresholds": {"worth": 3, "undecided": [1, 2]},
             "generic_stems": generic, "verdict": verdict,
             "criteria_revision": ("初版 B1 用「剔除泛词后独有块占比」，全泛词查询下退化，"
                                   "改为此处的纯噪声率；理由见脚本 docstring")},
            ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"  落盘 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
