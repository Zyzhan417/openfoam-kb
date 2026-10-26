#!/usr/bin/env python3
"""argv-compatible logging wrapper around `foamkb.cli search`.

Why a wrapper instead of trusting the agent's own log: the M1 A/B experiment's rule 6 is
"指标从日志取，不从自报取" — the number you quote has to be written by the tool, not by the
thing being measured (`reports/2026-10-25-agent-call-interface-decision.md` §11). So the
transcript here is produced by this process, and the judge's `done` verdict requires the
cited ref to appear in *this* run's transcript. That single conjunct is also what makes
"the agent grepped /opt/openfoam13 instead" mechanically detectable: its ref cannot be
there.

    PYTHONPATH=src .venv/bin/python scripts/eval_wrapper.py search \
        --index ~/.local/share/foamkb/c20_typedef.sqlite --query "..." --top-k 20 \
        --transcript runs/A/Q01.transcript.jsonl --budget 15

Wrapper-only flags (`--transcript`, `--budget`) are stripped before forwarding; every other
argv is passed through verbatim so the agent is calling the real CLI surface.

Exit codes: the child's own code, except 2 for a refused call (budget exhausted / bad
wrapper args) and 64 when the child produced output that is not the documented JSON.

Deliberately imports nothing from `foamkb`: a judge that shares an implementation with the
thing it judges goes green on the same bug (AGENTS D15's lesson).
"""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BUDGET_CEILING = 15
HIT_KEYS = ("ref", "path", "start_line", "end_line", "symbol", "kind", "truncated")


def split_wrapper_args(argv):
    """Pull wrapper-only flags out of argv; return (rest, transcript, budget, answers)."""
    rest, transcript, budget, answers = [], None, BUDGET_CEILING, None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--transcript", "--budget", "--answers"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{arg} needs a value")
            value = argv[i + 1]
            if arg == "--transcript":
                transcript = pathlib.Path(value)
            elif arg == "--budget":
                budget = int(value)
            else:
                answers = pathlib.Path(value)
            i += 2
            continue
        rest.append(arg)
        i += 1
    if transcript is None:
        raise SystemExit("--transcript <path> is required")
    if budget > BUDGET_CEILING:
        raise SystemExit(f"--budget {budget} exceeds the ceiling {BUDGET_CEILING}")
    return rest, transcript, budget, answers


def call_count(transcript):
    """Calls already recorded. A failed call still counts — that is the point."""
    if not transcript.exists():
        return 0
    with transcript.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def checkpoints_due(answers):
    """Which 3/8/15 marks the agent has recorded so far (0 if it has written none)."""
    if answers is None or not answers.exists():
        return set()
    marks = set()
    for line in answers.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            marks.add(json.loads(line).get("at_call"))
        except json.JSONDecodeError:
            continue
    return marks


def run_child(rest):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + (":" + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    cmd = [sys.executable, "-m", "foamkb.cli"] + rest
    return subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)


def hits_of(stdout):
    """The documented payload is {"rev": ..., "hits": [...]}; anything else is a broken call."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or "hits" not in payload:
        return None
    return [{k: h.get(k) for k in HIT_KEYS} for h in payload["hits"]]


def record(transcript, seq, rest, code, hits):
    line = {"seq": seq, "argv": rest, "exit": code, "hits": hits}
    with transcript.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, ensure_ascii=False) + "\n")


def announce(seq, budget, transcript, answers):
    """Tell the agent where it is. Cheap, and it is what keeps runs valid (spec FR-006).

    Without this the agent has to count its own calls, and a run that guessed wrong is
    invalid and has to be paid for twice. All of this goes to stderr so stdout stays exactly
    the CLI's JSON payload.
    """
    n = seq + 1
    print(f"[wrapper] CALL {n}/{budget}", file=sys.stderr)
    if n in (3, 8, 15):
        target = f"  →  {answers}" if answers else ""
        print(f"[wrapper] CHECKPOINT DUE: 现在往 answers 文件追加一行 "
              f'{{"at_call": {n}, "ref": ..., "quote": ...}}{target}', file=sys.stderr)
    if n >= budget:
        marks = checkpoints_due(answers)
        # Only marks that are strictly in the past can be missing: the agent cannot have
        # written `at_call = n` yet, because this very call is what triggers it. Warning
        # about the current mark was a false alarm on every run that finished on budget
        # (observed: B/Q13's third attempt printed "缺 checkpoint [15]" and then wrote it).
        missing = [c for c in (3, 8, 15) if c < n and c not in marks]
        if missing:
            print(f"[wrapper] WARNING 预算已用完，answers 缺 checkpoint {missing}；"
                  f"这次运行会被判无效", file=sys.stderr)
        else:
            # Do not claim "all present": the mark for this very call is still owed.
            owed = [c for c in (3, 8, 15) if c <= budget and c not in marks]
            tail = f"仍需写 {owed}" if owed else "此前的 checkpoint 齐全"
            print(f"[wrapper] 预算已用完；{tail}", file=sys.stderr)


def acquire_lock(transcript):
    """One transcript, one writer. Returns the lock path, or None if another process holds it.

    Why (AGENTS A24): a retry was dispatched for a `(arm, qid)` whose previous session was
    still alive. Both appended to the same transcript, so the file became the union of two
    agents' search results and the judge's `done` conjunct ("the cited ref appears in THIS
    run's transcript") stopped meaning anything -- while every individual record still looked
    well formed. Artifact count is not evidence of session end; this lock is.

    Stale locks (the holder died) are taken over and reported, so a killed run does not wedge
    the next one.
    """
    lock = transcript.with_suffix(transcript.suffix + ".lock")
    holder = lock.read_text(encoding="utf-8").strip() if lock.exists() else ""
    if holder.isdigit() and int(holder) != os.getpid():
        # Only ProcessLookupError means "that pid is gone". PermissionError means the pid
        # exists but belongs to another user -- a live holder, not a stale lock. Treating
        # it as stale (the first version did) silently steals a lock that is in use.
        try:
            os.kill(int(holder), 0)
        except ProcessLookupError:
            print(f"[wrapper] stale lock from dead pid {holder}, taking over", file=sys.stderr)
        except OSError:
            print(f"REFUSED: {transcript.name} is locked by pid {holder}, which exists but is "
                  f"not ours (AGENTS A24). Do not steal it; wait or remove {lock} by hand.",
                  file=sys.stderr)
            return None
        else:
            print(f"REFUSED: {transcript.name} is being written by live pid {holder}. "
                  f"One transcript, one writer (AGENTS A24) -- wait for that session to "
                  f"finish, or remove {lock} if you are certain it is dead.", file=sys.stderr)
            return None
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def release_lock(lock):
    if lock is not None:
        lock.unlink(missing_ok=True)


def main(argv):
    rest, transcript, budget, answers = split_wrapper_args(argv)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(transcript)
    if lock is None:
        return 2
    try:
        seq = call_count(transcript)
        if seq >= budget:
            print(f"REFUSED: budget of {budget} calls is exhausted", file=sys.stderr)
            return 2

        child = run_child(rest)
        sys.stdout.write(child.stdout)
        sys.stderr.write(child.stderr)
        hits = hits_of(child.stdout)
        record(transcript, seq, rest, child.returncode, hits if hits is not None else [])
        announce(seq, budget, transcript, answers)
        if hits is None and child.returncode == 0:
            print("BROKEN CALL: stdout was not the documented JSON payload", file=sys.stderr)
            return 64
        return child.returncode
    finally:
        release_lock(lock)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
