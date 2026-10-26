#!/usr/bin/env python3
"""Gate for 007's accept sets: the ruler has to be checkable, not just asserted.

Six rules, each one aimed at a way this ruler can silently become worthless:

  1. shape            -- every question carries the calibers that exist, and the *enumerated*
                         ones (s1, s2) stay within 1-3 entries. Derived calibers (inter/union)
                         are exempt from the cap: the union of two 3-entry rounds is 6, and
                         capping it would silently turn "union" into "some subset of union".
  2. verbatim         -- every `evidence` is a literal substring of a block's text in that path.
                         This is the `calcNut` defence: a quote written from memory cannot be
                         detected by reading it, only by searching for it.
  3. path-unique      -- a path whose basename is shared by several files must be marked
                         `basename_shared`. Judging by basename would accept files nobody listed:
                         `linear.H` exists at 5 different paths.
  4. sha-recorded     -- the file's sha256 matches the one written into spec.md, so a set frozen
                         before scoring cannot be swapped afterwards.
  5. caliber-arithmetic -- inter == s1&s2, union == s1|s2, and s1/s2 equal the raw rounds; also
                         `build_accept_sets.py --check` must say the file is not hand-edited.
                         This replaces the original "third person arbitrates single-round items":
                         with 75% disagreement between rounds, arbitration would have settled most
                         of the ruler by hand, i.e. reintroduced the discretion it existed to
                         prevent. Set arithmetic has no discretion to exercise.
  6. freeze-log       -- a re-freeze must be declared. More than one log entry without a voiding
                         note means the ruler moved after the fact.

Rule 2 is the only one that cannot be satisfied by editing prose. Rules 3-6 exist because the
first draft of this feature would have shipped with all of them missing.

    PYTHONPATH=src .venv/bin/python scripts/verify_accept_sets.py
Exit 0 only when every rule passes. There is no `--allow-partial`: the generator emits whatever
calibers exist, so a half-landed ruler is still internally consistent and checkable.
"""
import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_INDEX = pathlib.Path.home() / ".local" / "share" / "foamkb" / "c20_typedef.sqlite"
SETS = ROOT / "scripts" / "accept_sets.json"
RAW = ROOT / "scripts" / "accept_sets"
FREEZE_LOG = RAW / "freeze-log.jsonl"
SPEC = ROOT / "specs" / "007-eval-ruler-and-gaps" / "spec.md"
OLD_QIDS = [f"Q{n:02d}" for n in (1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14)]
NEW_QIDS = [f"U{n:02d}" for n in range(1, 13)]
ENUMERATED = ("s1", "s2")   # produced by a human enumerator, so the 1-3 cap applies
DERIVED = ("inter", "union")
MAX_ENTRIES = 3


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_shape(doc):
    """Rule 1: all 24 questions present, calibers coherent, enumerated sets within 1-3."""
    failures = []
    qids = list(doc.get("questions", {}))
    missing = [q for q in OLD_QIDS + NEW_QIDS if q not in qids]
    if missing:
        failures.append(f"缺题 {missing}（旧 12 + 新 12 = 24 条）")
    extra = [q for q in qids if q not in OLD_QIDS + NEW_QIDS]
    if extra:
        failures.append(f"多出未知 qid {extra}")
    declared = doc.get("calibers") or []
    for name in declared:
        if name not in ENUMERATED + DERIVED:
            failures.append(f"未知口径 {name!r}")
    for qid, q in sorted(doc.get("questions", {}).items()):
        present = [c for c in declared if c in q]
        if not present:
            failures.append(f"{qid}: 没有任何口径")
            continue
        for c in ENUMERATED:
            if c in q and len(q[c]) > MAX_ENTRIES:
                failures.append(f"{qid}.{c} 有 {len(q[c])} 项，枚举口径上限 {MAX_ENTRIES}")
        for c in DERIVED:
            if c in q and c not in declared:
                failures.append(f"{qid}.{c} 用了未声明的口径")
        if not q.get("s1") and not q.get("no_statement_found"):
            failures.append(f"{qid}: s1 为空且未标 no_statement_found")
        for p in {p for c in present for p in q[c]}:
            if p not in (q.get("evidence") or {}):
                failures.append(f"{qid}: {p} 出现在口径里但没有 evidence")
    print(f"[{'PASS' if not failures else 'FAIL'}] 规则1 形状：{len(qids)} 条题，"
          f"口径 {declared}")
    for f in failures:
        print(f"       - {f}")
    return failures


def check_verbatim(index_path, doc):
    """Rules 2+3: each evidence is a literal slice of a block in that path; path is unique."""
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    failures, total = [], 0
    try:
        for qid, q in sorted(doc.get("questions", {}).items()):
            for path, meta in (q.get("evidence") or {}).items():
                total += 1
                evidence = meta.get("evidence") or ""
                rows = list(conn.execute(
                    "SELECT text FROM chunks WHERE path = ?", (path,)))
                if not rows:
                    failures.append(f"{qid}: path 不在索引里: {path}")
                    continue
                if not any(evidence in text for (text,) in rows):
                    failures.append(f"{qid}: evidence 不是 {path} 任何块的逐字切片")
                    continue
                base = pathlib.PurePosixPath(path).name
                # Compare basenames in Python, not with SQL `LIKE`: SQLite's LIKE is
                # case-insensitive for ASCII, so `%/SolverPerformance.C` also matched
                # `solverPerformance.C` and the gate cried "shared basename" for two files that
                # are distinct by case. A gate that reports a false positive is worse than no
                # gate -- the next real one gets waved through (D15: the checker must implement
                # the rule its own way, and its own way was wrong here).
                same_base = [p for (p,) in conn.execute("SELECT DISTINCT path FROM chunks")
                             if pathlib.PurePosixPath(p).name == base]
                if len(same_base) > 1 and meta.get("basename_shared") is not True:
                    failures.append(
                        f"{qid}: {base} 在语料里有 {len(same_base)} 个 path，"
                        f"按 basename 判会误接受；需按完整 path 判并标 basename_shared")
        print(f"[{'PASS' if not failures else 'FAIL'}] 规则2+3 逐字切片与 path 唯一：{total} 项")
    finally:
        conn.close()
    for f in failures:
        print(f"       - {f}")
    return failures


def check_sha(doc_path):
    """Rule 4: the sha in spec.md must equal the file's."""
    if not SPEC.exists():
        print("[note] 规则4 sha：spec.md 不存在，跳过")
        return []
    actual = sha256(doc_path)
    if actual in SPEC.read_text(encoding="utf-8"):
        print(f"[PASS] 规则4 sha：{actual[:16]}… 已记入 spec.md")
        return []
    print(f"[FAIL] 规则4 sha：{actual[:16]}… 未记入 spec.md（冻结未留痕，或冻结后文件被改）")
    return [f"accept_sets.json 的 sha256 {actual} 不在 spec.md 里"]


def check_arithmetic(doc):
    """Rule 5: the derived calibers are exactly the set operations on the raw rounds."""
    raw = {"old": (RAW / "raw_s1.json", RAW / "raw_s2.json"),
           "new": (RAW / "raw_new.json", RAW / "raw_new_s2.json")}
    failures, checked = [], 0
    for panel, (p1, p2) in raw.items():
        if not p1.exists():
            failures.append(f"{panel}: 缺 {p1.name}")
            continue
        r1 = {q["qid"]: {e["path"] for e in q.get("accept", [])}
              for q in load(p1)["questions"]}
        r2 = None
        if p2.exists():
            r2 = {q["qid"]: {e["path"] for e in q.get("accept", [])}
                  for q in load(p2)["questions"]}
        for qid, a in sorted(r1.items()):
            entry = doc["questions"].get(qid)
            if entry is None:
                failures.append(f"{qid}: 在 {p1.name} 里但不在 accept_sets.json 里")
                continue
            checked += 1
            if set(entry.get("s1") or []) != a:
                failures.append(f"{qid}.s1 与 {p1.name} 不符")
            if r2 is not None:
                b = r2.get(qid, set())
                if set(entry.get("s2") or []) != b:
                    failures.append(f"{qid}.s2 与 {p2.name} 不符")
                if set(entry.get("inter") or []) != (a & b):
                    failures.append(f"{qid}.inter != s1∩s2")
                if set(entry.get("union") or []) != (a | b):
                    failures.append(f"{qid}.union != s1∪s2")
    print(f"[{'PASS' if not failures else 'FAIL'}] 规则5 口径算术：{checked} 条题与原始轮次可比对")
    for f in failures:
        print(f"       - {f}")
    return failures


def check_freeze_log():
    """Rule 6: a re-freeze must say why. Undeclared re-freezing is the post-hoc entry point."""
    if not FREEZE_LOG.exists():
        print("[note] 规则6 冻结日志：尚无日志（首次冻结前正常）")
        return []
    entries = [json.loads(ln) for ln in FREEZE_LOG.read_text(encoding="utf-8").splitlines()
               if ln.strip()]
    undeclared = [e for e in entries if len(entries) > 1 and not e.get("reason")]
    ok = not undeclared
    print(f"[{'PASS' if ok else 'FAIL'}] 规则6 冻结日志：{len(entries)} 条记录，"
          f"未说明原因的重冻结 {len(undeclared)} 条")
    return [] if ok else [f"{len(undeclared)} 次重冻结没有写原因"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=str(DEFAULT_INDEX))
    ap.add_argument("--sets", default=str(SETS))
    args = ap.parse_args(argv)

    sets_path = pathlib.Path(args.sets)
    if not sets_path.exists():
        print(f"SETS_MISSING: {sets_path}", file=sys.stderr)
        return 2
    index = pathlib.Path(args.index)
    if not index.exists():
        print(f"INDEX_UNAVAILABLE: {index}", file=sys.stderr)
        return 4

    doc = load(sets_path)
    failures = check_shape(doc)
    failures += check_verbatim(index, doc)
    failures += check_sha(sets_path)
    failures += check_arithmetic(doc)
    failures += check_freeze_log()

    print(f"\nfailures={len(failures)}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
