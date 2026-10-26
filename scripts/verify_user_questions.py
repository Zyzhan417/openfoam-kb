#!/usr/bin/env python3
"""Gate for 007's user-language question set: the panel is only meaningful if it is clean.

Four rules, and the first one is the whole point of the feature:

  1. no domain vocabulary  -- checked against scripts/cfd_terms.txt, which is itself derived
                              from the corpus and validated against questions we know are
                              legitimate (`make_cfd_terms.py --check`). A panel that leaked
                              `mesh` or `turbulence` would not measure a vocabulary gap; it
                              would measure the same thing the old panel measured.
  2. fields complete        -- everything `judge_runs.py` and `verify_declared_targets.py`
                              consume. The first draft of the spec listed 5 fields; running the
                              existing tools on that shape raises KeyError.
  3. no duplicates          -- 12 questions that repeat each other inflate nothing but look
                              thorough. Compared by content-word overlap, not by string equality.
  4. answerability declared -- each row says whether the corpus can answer it. `unanswerable`
                              needs a `corpus_absence` record: 006 shipped two "the corpus says
                              nothing" rows and both were falsified.

    PYTHONPATH=src .venv/bin/python scripts/verify_user_questions.py
Exit 0 only when every rule passes.
"""
import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "scripts" / "task_eval_questions_user.json"
TERMS = ROOT / "scripts" / "cfd_terms.txt"

REQUIRED = ("qid", "query", "query_user", "source", "kind",
            "corpus_answerability", "accept")
ANSWERABILITY = {"answerable", "rewording-needed", "unanswerable"}
EXPECTED = 12
# Content-word overlap above this share means two questions ask the same thing.
DUPLICATE_SHARE = 0.6
STOP = set("""a an the is are was were be been being do does did doing have has had
how what when where which who why for of in to from at by with and or not no
it its this that these those there here can could should would may might must
i we you they he she my our your their me us them on into over under about
find need want get got make made use used using way thing something
""".split())


def load_terms():
    if not TERMS.exists():
        raise SystemExit(f"TERMS_MISSING: {TERMS}（先跑 scripts/make_cfd_terms.py）")
    terms = set()
    for line in TERMS.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            terms.add(line.lower())
    return terms


def content_words(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP and len(w) > 2}


def check_domain(doc, terms):
    """Rule 1: neither the user's words nor the translated query may use domain vocabulary."""
    failures = []
    for q in doc["questions"]:
        for field in ("query_user", "query"):
            words = {w.lower() for w in re.findall(r"[a-z]+", q.get(field) or "")}
            hits = sorted(words & terms)
            if hits:
                failures.append(f"{q['qid']}.{field} 含领域词: {hits}")
    print(f"[{'PASS' if not failures else 'FAIL'}] 规则1 无领域词：{len(doc['questions'])} 条 × "
          f"2 个字段，对照 {len(terms)} 词的词表")
    for f in failures:
        print(f"       - {f}")
    return failures


def check_fields(doc):
    """Rule 2: the fields the existing tools read must exist, and be self-consistent."""
    failures = []
    for q in doc["questions"]:
        missing = [k for k in REQUIRED if k not in q]
        if missing:
            failures.append(f"{q.get('qid')}: 缺字段 {missing}")
            continue
        if q["kind"] not in ("positive", "excluded"):
            failures.append(f"{q['qid']}: kind={q['kind']!r} 不在 positive/excluded")
        if q["corpus_answerability"] not in ANSWERABILITY:
            failures.append(f"{q['qid']}: corpus_answerability={q['corpus_answerability']!r}")
        unanswerable = q["corpus_answerability"] == "unanswerable"
        if not unanswerable:
            for key in ("target_path", "target_basename"):
                if not q.get(key):
                    failures.append(f"{q['qid']}: 可答题必须给 {key}")
        else:
            if q.get("target_path") or q.get("target_basename"):
                failures.append(f"{q['qid']}: unanswerable 却有 target_*，口径自相矛盾")
            if q["kind"] != "excluded":
                failures.append(f"{q['qid']}: unanswerable 必须 kind=excluded")
            if not q.get("corpus_absence"):
                failures.append(f"{q['qid']}: unanswerable 缺 corpus_absence 机械记录")
    print(f"[{'PASS' if not failures else 'FAIL'}] 规则2 字段完整与自洽")
    for f in failures:
        print(f"       - {f}")
    return failures


def check_duplicates(doc):
    """Rule 3: content-word overlap, so 'the setting file' vs 'settings file' still collides."""
    failures = []
    rows = doc["questions"]
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            a = content_words(rows[i].get("query_user") or rows[i]["query"])
            b = content_words(rows[j].get("query_user") or rows[j]["query"])
            if not a or not b:
                continue
            share = len(a & b) / min(len(a), len(b))
            if share > DUPLICATE_SHARE:
                failures.append(f"{rows[i]['qid']} 与 {rows[j]['qid']} 重复度 "
                                f"{share:.0%}（共享 {sorted(a & b)}）")
    print(f"[{'PASS' if not failures else 'FAIL'}] 规则3 无重复（内容词重叠 > "
          f"{DUPLICATE_SHARE:.0%} 判重）")
    for f in failures:
        print(f"       - {f}")
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", default=str(QUESTIONS))
    ap.add_argument("--allow-partial", action="store_true",
                    help="不足 12 条时只警告（T007 扩写中途用）")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.questions)
    if not path.exists():
        print(f"QUESTIONS_MISSING: {path}", file=sys.stderr)
        return 2
    doc = json.loads(path.read_text(encoding="utf-8"))

    failures = []
    if len(doc["questions"]) != EXPECTED:
        msg = f"应为 {EXPECTED} 条，实为 {len(doc['questions'])} 条"
        (print(f"[WARN] 规则0 条数：{msg}") if args.allow_partial
         else failures.append(msg))
    terms = load_terms()
    failures += check_domain(doc, terms)
    failures += check_fields(doc)
    failures += check_duplicates(doc)

    counts = {}
    for q in doc["questions"]:
        k = q.get("corpus_answerability", "?")
        counts[k] = counts.get(k, 0) + 1
    print(f"\n可答性分布: {dict(sorted(counts.items()))}")
    print(f"failures={len(failures)}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
