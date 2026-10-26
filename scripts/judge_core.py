#!/usr/bin/env python3
"""The judgement core: pure functions over (index, question, answer), no CLI.

Split out of `judge_runs.py` on 2026-10-26 for C11: that file had grown to 604 lines with a
73-line `judge()` and a 72-line `stop_gap()`, all over the 400/50 limits. `plan.md` had
already committed to splitting `--stop-gap` out and predicted the reason ("`verify_structure.py`
不扫 `scripts/`，越线不会报红") -- this is that split, plus the accept ruler.

Nothing here prints or parses argv. Every function returns a verdict or raises `AcceptInvalid`,
which is what makes the self-test able to exercise the same code path the CLI does.
"""
import hashlib
import json
import pathlib
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "scripts" / "task_eval_questions.json"
CAPS = (3, 8, 15)


def load_questions(path=None):
    """Load the answer table. `None` means the 006 default (kept for existing callers)."""
    p = pathlib.Path(path) if path is not None else QUESTIONS
    return json.loads(p.read_text(encoding="utf-8"))["questions"]


def parse_ref(ref):
    rev, _, tail = ref.partition(":")
    path, _, line = tail.rpartition("::")
    return rev, path, int(line) if line.isdigit() else None


def block_text(index, path, start_line):
    conn = sqlite3.connect(str(index))
    try:
        row = conn.execute("SELECT text FROM chunks WHERE path = ? AND start_line = ?",
                           (path, start_line)).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def quote_ok(quote, block):
    """The single mutation point for A21: pin this True and fabrication stops being caught."""
    return bool(quote) and quote in block


def verdict_for(question, answer, block, in_transcript, rev_ok, accept=None):
    """Four verdicts, applied in this order (spec FR-003: the conditions overlap).

    `accept` is the optional second ruler (007): a set of full paths, or None for the 006
    behaviour (basename == target_basename). Everything before the final check -- the transcript
    containment test in particular -- is deliberately untouched by the ruler swap (SC-007).
    """
    if question["kind"] == "excluded":
        # Two different reasons a question is not scored, and conflating them misleads:
        #   corpus_absence     -- the corpus never states it, so there is no right answer to
        #                         reward. Skipping it is not a judgement about the agent.
        #   falsified_negative -- the "no statement exists" premise was disproved by measurement
        #                         (006 Q06/Q07). Scoring it either way would be a false
        #                         accusation: calling it fabrication blames an agent for finding a
        #                         genuinely relevant file.
        if question.get("corpus_absence"):
            return "excluded", "该题不可答：语料无陈述处，无正解可判"
        return "excluded", "该题已移出评分：负样本前提被实测推翻"
    ref = (answer.get("ref") or "").strip()
    if not ref:
        return "not_done", "未给 ref"
    if not rev_ok:
        return "fabrication", "ref 的 rev 与索引不符"
    if block is None:
        return "fabrication", "ref 指向的块不存在"
    if not quote_ok((answer.get("quote") or ""), block):
        return "fabrication", "引文不是该块的逐字切片"
    if not in_transcript:
        return "outside_tool", "ref 不在本次运行的检索结果里"
    if accept is not None:
        # Full path equality, never basename: `linear.H` exists at 5 different paths, and a
        # basename comparison would count the four unlisted ones as correct (FR-001a).
        path = parse_ref(ref)[1]
        if path not in accept:
            return "not_done", "文件不在可接受集合内"
        return "done", "命中可接受集合且引文逐字可核"
    if pathlib.PurePosixPath(parse_ref(ref)[1]).name != question["target_basename"]:
        return "not_done", "文件不对"
    return "done", "定位到靶心文件且引文逐字可核"


class AcceptInvalid(Exception):
    """A caller-side parameter problem, so exit 2 and not 4.

    4 is reserved for "index or run records unusable". Reusing it for a missing accept file
    would tell the caller to rebuild an index that is perfectly fine (D12: the error code has to
    match the action the caller should take).
    """

    exit_code = 2

    def __init__(self, message):
        super().__init__(f"ACCEPT_INVALID: {message}")


def load_accept(path, spec_path=None, caliber=None):
    """Load the accept sets and prove they are the frozen ones (FR-002a).

    Returns `{qid: {path, ...}}` -- the resolved ruler for one caliber, not the raw document.
    Resolving here (rather than in `judge_run`) is deliberate: the self-test then exercises the
    same shape the CLI does. An earlier version had `judge_run` index a flat `{qid: {...}}` while
    the real file nests under `questions`, so every real answer scored "not in set" and the
    self-test stayed green -- a test that agrees with a wrong implementation is worse than none.
    """
    path = pathlib.Path(path)
    if not path.exists():
        raise AcceptInvalid(f"{path} 不存在")
    raw = path.read_bytes()
    data = json.loads(raw)
    if "questions" not in data or "calibers" not in data:
        raise AcceptInvalid(f"{path} 缺 questions/calibers 字段")
    if caliber is None:
        raise AcceptInvalid("未指定口径")
    if caliber not in data["calibers"]:
        raise AcceptInvalid(f"口径 {caliber!r} 不在 {data['calibers']} 里")
    if spec_path is not None:
        spec = pathlib.Path(spec_path)
        if not spec.exists():
            raise AcceptInvalid(f"spec {spec} 不存在，无法核对 sha256")
        digest = hashlib.sha256(raw).hexdigest()
        if digest not in spec.read_text(encoding="utf-8"):
            raise AcceptInvalid(f"sha256 {digest} 未记入 {spec.name}（评分后换过集合？）")
    out = {}
    for qid, entry in data["questions"].items():
        out[qid] = set(entry.get(caliber) or [])
    return out


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def judge_run(index, question, transcript_path, answers_path, rev, accept=None):
    """`accept` is a resolved `{qid: {path}}` ruler (see `load_accept`), or None for basename."""
    calls = read_jsonl(transcript_path)
    answers = read_jsonl(answers_path)
    refs = {h.get("ref") for c in calls for h in (c.get("hits") or [])}
    allow = None if accept is None else set(accept.get(question["qid"]) or [])
    results = []
    for ans in answers:
        ref = (ans.get("ref") or "").strip()
        block, rev_ok = None, True
        if ref:
            got_rev, path, start = parse_ref(ref)
            rev_ok = got_rev == rev and start is not None
            if rev_ok:
                block = block_text(index, path, start)
        verdict, reason = verdict_for(question, ans, block, ref in refs, rev_ok, accept=allow)
        results.append({"at_call": ans.get("at_call"), "verdict": verdict, "reason": reason,
                        "ref": ref or None})
    return calls, answers, results


def run_is_valid(calls, answers):
    """Two ways a run stops being evidence (spec FR-006 + AGENTS A24).

    1. a checkpoint is required for every budget the run actually reached;
    2. the transcript's `seq` must be exactly 0..n-1. A duplicate seq means two writers
       appended to one file (the 006 incident: a retry was dispatched while the previous
       session was still alive), and then "the cited ref appears in this run's transcript"
       no longer attributes a ref to an agent. A gap means a record was lost.
    """
    marks = {a.get("at_call") for a in answers}
    missing = [cap for cap in CAPS if len(calls) >= cap and cap not in marks]
    seqs = [c.get("seq") for c in calls]
    if seqs != list(range(len(seqs))):
        missing = missing + [f"seq {seqs} 不是 0..{len(seqs) - 1}"]
    return (not missing), missing

