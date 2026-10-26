#!/usr/bin/env python3
"""Calibrate the task-level panel, judge frozen run records, and prove the two arms differ
in exactly one field.

Three modes, one file, because they share the answer table and nothing else:

    --calibrate          reproduce the published baseline (2/12 hit@5) from the CLI itself
    --judge <runs-dir>   mechanical verdicts over <runs-dir>/{A,B}/<qid>.{transcript,answers}.jsonl
    --compare-protocols  the prompt builder must differ only in the `protocol` field
    --prompt --arm A|B --qid Q01   print the exact prompt a run must be given

Why mechanical (spec D6): the two A-group targets that turned out to be wrong were both
caught by reading Description text, but a human judge reading 28 answers will reproduce the
same bias at scale. Here the only question asked of an answer is falsifiable: does the quote
occur verbatim in the block the ref points at, and did that ref come from this run's own
search results?

Deliberately imports nothing from `foamkb` (plan risk 2): the judge runs the CLI as a
subprocess. A judge that shares an implementation with the thing it judges goes green on the
same bug.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/judge_runs.py --calibrate
    PYTHONPATH=src .venv/bin/python scripts/judge_runs.py --judge reports/.../phase_006-runs
"""
import argparse
import json
import os
import pathlib
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import judge_core  # noqa: E402
from judge_core import (  # noqa: E402,F401 -- re-exported so existing callers keep working
    AcceptInvalid,
    CAPS,
    block_text,
    judge_run,
    load_accept,
    load_questions,
    parse_ref,
    read_jsonl,
    run_is_valid,
)
DEFAULT_INDEX = pathlib.Path.home() / ".local" / "share" / "foamkb" / "c20_typedef.sqlite"
QUESTIONS = ROOT / "scripts" / "task_eval_questions.json"
CAPS = (3, 8, 15)
RANK_CEILING = 20

PROMPT = """你要回答一个关于 OpenFOAM 源码库的问题，只能用 foamkb 检索工具取证。

题目：{query}

工具（每轮一条命令，argv 与真实 CLI 相同，调用会被记录；CLI 只有 search，没有 read）：
  PYTHONPATH=src .venv/bin/python scripts/eval_wrapper.py search \\
      --index {index} --query "<你的查询>" --top-k 20 --max-chars 300 \\
      --transcript {transcript} --answers {answers} --budget 15
  每次调用的 stderr 会打印 `[wrapper] CALL n/15`；到第 3、8、15 次时会提示该交 checkpoint。
  扫结果用 --max-chars 300；锁定某一块要看全文时，把 --max-chars 抬到 3000–8000 重查同一条
  （仍算一次调用）。top-k 20 是上限，别超过。

硬规则：
1. 不得直接读 /opt/openfoam13，不得 grep/rg 源码，只能通过上面的包装器。
2. 只引用包装器输出里出现过的 ref。
3. 在第 3、8、15 次调用之后，各往 {answers} 追加一行 JSON：
   {{"at_call": n, "ref": "<ref 或 null>", "quote": "<块 text 的逐字片段>"}}
   ref 为 null 表示还没找到。quote 必须逐字来自该块的 text。
4. 最多 15 次调用。--max-chars 必须在 200–8000 之间（越界会被 CLI 拒绝，白烧一次调用）。
5. 不得打开仓库根目录的 AGENT_PROTOCOL.md（若存在）。

{protocol}
"""


# [moved to judge_core.py] load_questions


def cli_search(index, query, top_k=RANK_CEILING):
    argv = [sys.executable, "-m", "foamkb.cli", "search", "--index", str(index),
            "--query", query, "--top-k", str(top_k), "--max-chars", "200"]
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run(argv, cwd=str(ROOT), env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"INDEX_UNAVAILABLE: CLI exit={proc.returncode}: {proc.stderr.strip()[:200]}")
    return json.loads(proc.stdout)["hits"]


def rank_of(hits, basename):
    for i, hit in enumerate(hits, start=1):
        if pathlib.PurePosixPath(hit["path"]).name == basename:
            return i
    return None


def calibrate(index, questions):
    """SC-001: the published baseline is 2 of 12. Mismatch means the measuring path drifted."""
    rows, mismatched, hit5 = [], [], 0
    for q in questions:
        if q["kind"] != "positive":
            print(f"[note] {q['qid']} {q['query']!r}: 已移出评分（负样本前提不成立，见 spec C6），不入分母")
            continue
        rank = rank_of(cli_search(index, q["query"]), q["target_basename"])
        rows.append((q["qid"], q["query"], q["target_basename"], rank, q["baseline_rank"]))
        hit5 += 1 if rank is not None and rank <= 5 else 0
        if rank != q["baseline_rank"]:
            mismatched.append((q["qid"], q["baseline_rank"], rank))
    print(f"{'qid':4s} {'rank':>4s} {'declared':>8s}  query -> target")
    for qid, query, base, rank, declared in rows:
        print(f"{qid:4s} {str(rank if rank is not None else '-'):>4s} "
              f"{str(declared if declared is not None else '-'):>8s}  {query} -> {base}")
    print(f"\nhit@5 = {hit5}/{len(rows)}（positive-only）；"
          f"all = {hit5}/{len(questions)}（把 2 条负样本也算进分母，仅用于对照旧口径）")
    for qid, declared, rank in mismatched:
        print(f"[MISMATCH] {qid} 声明基线 {declared}，实测 {rank} —— 口径漂了，先对齐再走")
    return 1 if mismatched else 0


# [moved to judge_core.py] parse_ref / block_text / quote_ok / verdict_for / AcceptInvalid / load_accept / read_jsonl / judge_run / run_is_valid


def two_ruler_tally(index, runs_dir, questions, rev):
    """SC-003: print the exact and accept tallies side by side, never one instead of the other.

    The point of the whole feature is that the 006 number stays reproducible. So this asserts it:
    under the exact ruler the counts must come out 5/12 and 3/12, character for character. If a
    later change to the judge quietly moves the old number, this refuses rather than reporting a
    delta between two rulers as if it were a delta between two agents.
    """
    out = {}
    print("\n=== 两把尺子并排（旧题；SC-003 不得互相覆盖）===")
    print(f"{'臂':2s} {'exact':>9s} " + " ".join(f"{c:>7s}" for c in ("s1", "s2", "inter", "union")))
    for arm in ("A", "B"):
        row = {}
        pos = [q for q in questions.values() if q["kind"] == "positive"]
        for name, ruler in (("exact", None),) + tuple(
                (c, load_accept(ROOT / "scripts" / "accept_sets.json", caliber=c))
                for c in ("s1", "s2", "inter", "union")):
            done = 0
            for q in pos:
                tp = runs_dir / arm / f"{q['qid']}.transcript.jsonl"
                ap = runs_dir / arm / f"{q['qid']}.answers.jsonl"
                if not tp.exists():
                    continue
                calls, answers, results = judge_run(index, q, tp, ap, rev, accept=ruler)
                ok, _ = run_is_valid(calls, answers)
                if not ok:
                    continue
                fin = [r for r in results if isinstance(r["at_call"], int) and r["at_call"] == 15]
                chosen = (fin or results)[-1] if (fin or results) else None
                done += 1 if chosen and chosen["verdict"] == "done" else 0
            row[name] = done
        out[arm] = row
        print(f"{arm:2s} {row['exact']:3d}/12    " +
              " ".join(f"{row[c]:3d}/12 " for c in ("s1", "s2", "inter", "union")))
    expected = {"A": 5, "B": 3}
    if any(out[a]["exact"] != n for a, n in expected.items()):
        print(f"[FAIL] exact 口径应为 A 5/12、B 3/12（006 的冻结结论），实测 "
              f"A {out['A']['exact']}/12、B {out['B']['exact']}/12 —— "
              f"判定器改动了旧数字，两把尺子不可相减", file=sys.stderr)
        return out, False
    print("  [PASS] exact 逐字复现 006 的 5/12 与 3/12（旧结论未被新尺子覆盖）")
    return out, True


def summarise(verdicts_by_run):
    for cap in CAPS:
        done = sum(1 for run in verdicts_by_run
                   if run["by_cap"].get(cap) == "done")
        print(f"  cap={cap:2d}: done={done}")


def resolve_ruler(args):
    """Load the optional second ruler. A bad accept file is a caller error: exit 2, not 4."""
    if args is None or not getattr(args, "caliber", None):
        return None, 0
    try:
        ruler = load_accept(args.accept, spec_path=args.spec, caliber=args.caliber)
    except AcceptInvalid as exc:
        print(str(exc), file=sys.stderr)
        return None, exc.exit_code
    print(f"尺子：--caliber {args.caliber}（{args.accept}），"
          f"sha 已核对 {pathlib.Path(args.spec).name}")
    return ruler, 0


def judge_arm(index, runs_dir, questions, rev, arm, ruler):
    """Score one arm. Returns (n_missing_files, n_scored) -- the caller decides on exiting."""
    verdicts_by_run, invalid, missing_files = [], 0, 0
    print(f"\n=== 臂 {arm} ===")
    for qid in sorted(questions):
        tp = runs_dir / arm / f"{qid}.transcript.jsonl"
        ap = runs_dir / arm / f"{qid}.answers.jsonl"
        calls, answers, results = judge_run(index, questions[qid], tp, ap, rev, accept=ruler)
        if not tp.exists():
            missing_files += 1
        ok, missing = run_is_valid(calls, answers)
        invalid += 0 if ok else 1
        by_cap = {}
        for cap in CAPS:
            eligible = [r for r in results
                        if isinstance(r["at_call"], int) and r["at_call"] <= cap]
            by_cap[cap] = eligible[-1]["verdict"] if eligible else "missing"
        if questions[qid]["kind"] == "excluded":
            why = ("该题不可答：语料无陈述处" if questions[qid].get("corpus_absence")
                   else "该题已移出评分：负样本前提被推翻")
            print(f"  {qid} calls={len(calls):2d} verdict=excluded (不入分母；{why})")
            continue
        # An invalid run has no usable denominator (spec Edge Cases: 缺 checkpoint 的运行
        # 不进入任何分母). It is reported, never tallied.
        verdicts_by_run.append({"qid": qid, "by_cap": by_cap, "results": results, "valid": ok})
        note = "" if ok else f"  INVALID(missing checkpoints {missing})"
        print(f"  {qid} calls={len(calls):2d} verdict={by_cap[15]:12s}"
              f" 3/8/15={by_cap[3]}/{by_cap[8]}/{by_cap[15]}{note}")
    scored = [r for r in verdicts_by_run if r["valid"]]
    tally = {}
    for run in scored:
        tally[run["by_cap"][15]] = tally.get(run["by_cap"][15], 0) + 1
    print(f"  汇总: {dict(sorted(tally.items()))}  无效运行={invalid}"
          f"  记录缺失={missing_files}/{len(questions)}")
    if missing_files:
        print(f"[FAIL] 臂 {arm} 有 {missing_files} 条运行记录不存在 —— "
              f"这些题不能计入任何分母（spec 原则 III：空不等于零完成）")
    print(f"  有效运行 {len(scored)}/{len(verdicts_by_run)}（无效与缺失均不入分母）")
    summarise(scored)
    return missing_files, len(scored)


def judge(index, runs_dir, args=None):
    rev = sqlite3.connect(str(index)).execute(
        "SELECT value FROM meta WHERE key='rev'").fetchone()[0]
    # Honour --questions: the 007 panel is a different question set, and silently scoring it
    # against 006's table made every run read "missing" (records present, question list wrong).
    qpath = getattr(args, "questions", None) if args is not None else None
    questions = {q["qid"]: q for q in load_questions(pathlib.Path(qpath) if qpath else None)}
    if not runs_dir.is_dir():
        print(f"RUNS_UNAVAILABLE: {runs_dir} 不存在 —— 读不到运行记录不等于「零完成」",
              file=sys.stderr)
        return 4
    ruler, code = resolve_ruler(args)
    if code:
        return code
    for arm in ("A", "B"):
        missing_files, scored = judge_arm(index, runs_dir, questions, rev, arm, ruler)
        # D13: an exit code that does not carry "records missing" makes any chained gate green
        # exactly when the records are gone. Print the table, then refuse.
        if missing_files or not scored:
            return 4
    # SC-003: the old ruler's number must stay reproducible next to the new one. Exit 5 (not 1)
    # so a caller can tell "the judge moved the 006 number" apart from "a gate failed".
    # Only meaningful for the 006 panel: the 007 questions have no basename target to reproduce,
    # so asserting 5/12 there would compare against runs that do not exist.
    if any(q.startswith("Q") for q in questions):
        _out, ok = two_ruler_tally(index, runs_dir, questions, rev)
        return 0 if ok else 5
    return 0


# [moved to stop_gap.py] stop_gap / _rev


def load_vocab(path):
    """The term list, one word per line, `# count` suffix stripped."""
    words = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            words.append(line)
    return words


VOCAB_BLOCK = """
补充资料（只在本臂提供）：本语料是 OpenFOAM 的 C++ 源码。下面列出它在代码里实际使用的
一部分词汇 —— **只是词，没有定义，也不是答案**。你可以把它们当作改写查询时的候选词，
用不用、用哪一个由你判断。不要假设这些词覆盖了你需要的东西。

{vocab}
"""


def render_prompt(question, arm, index, runs_dir, protocol_path, vocab_path=None):
    """Build one run's prompt.

    Arms A and B must stay **byte-identical** to their frozen prompts, so `{vocab}` is not a
    slot in PROMPT -- it is appended for arm C only. That keeps the earlier 24 runs reproducible
    and makes "the only difference between A and C is the term-list block" checkable by
    string subtraction rather than by reading.
    """
    protocol = ""
    if arm == "B":
        protocol = pathlib.Path(protocol_path).read_text(encoding="utf-8")
    text = PROMPT.format(query=question["query"], index=index,
                         transcript=runs_dir / arm / f"{question['qid']}.transcript.jsonl",
                         answers=runs_dir / arm / f"{question['qid']}.answers.jsonl",
                         protocol=protocol)
    if arm == "C":
        if vocab_path is None:
            raise SystemExit("arm C 需要 --vocab")
        words = load_vocab(vocab_path)
        block = VOCAB_BLOCK.format(vocab=", ".join(words))
        text = text + block
    return text


def compare_vocab(questions, index, runs_dir, protocol_path, vocab_path):
    """SC 级断言：arm C 与 arm A 的 prompt 差异必须**只有**词表段与臂目录。

    006 的教训是「规程把两条考题原文和靶心名写进示例」——那份泄露是**读**出来的。
    这里把「只差一项」变成可机械核对的字符串减法：把 C 的 prompt 去掉臂目录替换与词表块，
    剩下的必须与 A 逐字节相同。任何第三个差异项（哪怕一个空行）都会红。
    """
    words = load_vocab(vocab_path)
    block = VOCAB_BLOCK.format(vocab=", ".join(words))
    bad = 0
    for q in questions:
        a = render_prompt(q, "A", index, runs_dir, protocol_path)
        c = render_prompt(q, "C", index, runs_dir, protocol_path, vocab_path=vocab_path)
        if c == a:
            print(f"[FAIL] {q['qid']}: C 臂与 A 臂完全相同 —— 词表没进 prompt")
            bad += 1
            continue
        norm = (c.replace(str(runs_dir / "C"), "<RUNS>")
                 .replace(block, ""))
        if norm != a.replace(str(runs_dir / "A"), "<RUNS>"):
            print(f"[FAIL] {q['qid']}: 去掉词表与臂目录后两臂仍不同 —— 有第三个差异项")
            bad += 1
    print(f"[{'PASS' if not bad else 'FAIL'}] A 与 C 的差异只有词表段与臂目录"
          f"（{len(questions)} 条，词表 {len(words)} 词）")
    return 1 if bad else 0


def compare_protocols(questions, index, runs_dir, protocol_path):
    """SC-003: only two differences are allowed -- the protocol text and the arm directory.

    The transcript/answers path must contain A or B (each arm writes its own records), so
    that token is normalised out before comparing. Anything else differing means the arms are
    not comparable, which would make the whole measurement meaningless.
    """
    protocol = pathlib.Path(protocol_path).read_text(encoding="utf-8")
    bad = 0
    for q in questions:
        a = render_prompt(q, "A", index, runs_dir, protocol_path)
        b = render_prompt(q, "B", index, runs_dir, protocol_path)
        if b == a:
            print(f"[FAIL] {q['qid']}: B 臂与 A 臂完全相同 —— 规程没进 prompt")
            bad += 1
            continue
        norm = b.replace(protocol, "").replace(str(runs_dir / "B"), "<RUNS>")
        if norm != a.replace(str(runs_dir / "A"), "<RUNS>"):
            print(f"[FAIL] {q['qid']}: 去掉规程与臂目录后两臂仍不同 —— 有第三个差异项")
            bad += 1
    print(f"[{'PASS' if not bad else 'FAIL'}] 两臂差异只有 protocol 与臂目录（{len(questions)} 条）")
    return 1 if bad else 0


def distance_class(got_path, target_path):
    """How far is the answered file from the declared target? Diagnostic only.

    The exact-match verdict (FR-004) answers "did the agent name the file I declared". This
    answers a different question -- "how far off was it" -- and it is here because the first
    panel run showed a systematic gap: agents answer with the `.C` implementation when the
    question asks *how something works*, while most targets are `.H` declarations. Counting
    only exact matches then reports a wording convention as a capability failure.

    It must never change a verdict (that would be post-hoc re-scoring); it only gets printed
    so the summary can say how much of the gap is convention and how much is wrong code.
    """
    if not got_path:
        return "no-ref"
    if got_path == target_path:
        return "exact"
    if got_path.rsplit(".", 1)[0] == target_path.rsplit(".", 1)[0]:
        return "same-stem"          # foo.C vs foo.H -- the implementation of the declared thing
    if got_path.rsplit("/", 1)[0] == target_path.rsplit("/", 1)[0]:
        return "same-dir"           # sibling file: plural/singular, base class, etc.
    if got_path.rsplit("/", 1)[0].rsplit("/", 1)[0] == target_path.rsplit("/", 1)[0].rsplit("/", 1)[0]:
        return "same-parent"        # one directory up: e.g. a Fwd.H or a sibling sub-library
    return "elsewhere"


def diagnose(index, runs_dir):
    """Per-question distance table for the final answers. Never affects exit status."""
    questions = {q["qid"]: q for q in load_questions()}
    print("\n=== 诊断：最终答案离靶心多远（不参与判定，仅用于解释完成率）===")
    for arm in ("A", "B"):
        tally = {}
        print(f"\n-- 臂 {arm}")
        for qid in sorted(questions):
            q = questions[qid]
            if q["kind"] != "positive":
                continue
            answers = read_jsonl(runs_dir / arm / f"{qid}.answers.jsonl")
            final = [a for a in answers if a.get("at_call") == 15] or answers
            got = parse_ref(final[-1]["ref"])[1] if final and final[-1].get("ref") else None
            cls = distance_class(got, q["target_path"])
            tally[cls] = tally.get(cls, 0) + 1
            print(f"  {qid} {cls:12s} 交 {str(got).split('/')[-1]:52s} "
                  f"靶 {q['target_path'].split('/')[-1]}")
        print(f"  小计 {dict(sorted(tally.items()))}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=str(DEFAULT_INDEX))
    ap.add_argument("--questions", default=str(QUESTIONS))
    ap.add_argument("--protocol", default=str(ROOT / "AGENT_PROTOCOL.md"))
    ap.add_argument("--runs", default=str(ROOT / "reports" / ".orchestrator" /
                                          "phase_summaries" / "phase_006-runs"))
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--calibrate", action="store_true")
    mode.add_argument("--judge", action="store_true")
    mode.add_argument("--compare-protocols", action="store_true")
    mode.add_argument("--compare-vocab", action="store_true",
                      help="断言 arm C 与 arm A 只差词表段与臂目录")
    mode.add_argument("--diagnose", action="store_true")
    mode.add_argument("--prompt")
    ap.add_argument("--arm", choices=("A", "B", "C"),
                    help="C = 裸 CLI + 词表（T-A 实验臂）")
    ap.add_argument("--vocab", default=str(ROOT / "scripts" / "cfd_terms.txt"),
                    help="arm C 追加的词表（默认 scripts/cfd_terms.txt）")
    ap.add_argument("--qid")
    ap.add_argument("--accept", default=str(ROOT / "scripts" / "accept_sets.json"),
                    help="可接受集合文件（--judge 时并排打印 exact 与四口径）")
    ap.add_argument("--caliber", choices=("s1", "s2", "inter", "union"),
                    help="用哪一口径当判定尺子；省略则用 exact（006 口径）")
    ap.add_argument("--spec", default=str(ROOT / "specs" / "007-eval-ruler-and-gaps" / "spec.md"),
                    help="记录 accept_sets.json sha256 的 spec；给 --caliber 时强制核对")
    args = ap.parse_args(argv)

    index = pathlib.Path(args.index)
    if not index.exists():
        print(f"INDEX_UNAVAILABLE: {index}", file=sys.stderr)
        return 4
    questions = load_questions(args.questions)
    if args.calibrate:
        return calibrate(index, questions)
    if args.judge:
        return judge(index, pathlib.Path(args.runs), args)
    if args.compare_protocols:
        return compare_protocols(questions, index, pathlib.Path(args.runs), args.protocol)
    if args.compare_vocab:
        return compare_vocab(questions, index, pathlib.Path(args.runs),
                             args.protocol, args.vocab)
    if args.diagnose:
        return diagnose(index, pathlib.Path(args.runs))
    if args.prompt:
        q = next(q for q in questions if q["qid"] == args.prompt)
        print(render_prompt(q, args.arm or "A", index, pathlib.Path(args.runs),
                            args.protocol, vocab_path=args.vocab))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
