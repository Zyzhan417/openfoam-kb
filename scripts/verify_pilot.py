#!/usr/bin/env python3
"""Pilot acceptance gate (T024 / SC-010, ARCHITECTURE §10 five checks).

Runs the five M0 exit checks against a PILOT index (built from reports/pilot-300.txt) and
prints one PASS/FAIL line per check. Check 4 needs a deliberately undecodable file, so it
builds its own tiny corpus in a temp dir instead of touching the real one.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/verify_pilot.py pilot.sqlite \\
        --pilot-list reports/pilot-300.txt
"""
import argparse
import json
import pathlib
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

import foamkb.cli as cli  # noqa: E402

results = []

# Five identifiers with exactly one obvious home in the PILOT SUBSET, used by check 5.
# These must name files that are actually in reports/pilot-300.txt: §10.5 is an acceptance
# on the pilot index, and demanding a file the subset never included can only return None.
# (An earlier version listed fixedValueFvPatchField / kOmegaSST / kEpsilon / volScalarField,
# none of which the 300-file sample contains -- the check failed on all five while the
# product was fine.)
IDENTIFIERS = [
    ("Barycentric", "Barycentric.H"),
    ("DiagTensor", "DiagTensor.H"),
    ("LESeddyViscosity", "LESeddyViscosity.H"),
    ("MRFnoSlipFvPatchVectorField", "MRFnoSlipFvPatchVectorField.H"),
    ("IOMRFZoneList", "IOMRFZoneList.H"),
]


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return ok


def search_json(index, query, top_k=5):
    cmd = [sys.executable, "-m", "foamkb.cli", "search", "--index", index,
           "--query", query, "--top-k", str(top_k)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"search failed for {query!r}: {proc.stderr}")
    return json.loads(proc.stdout)


def check_1_verbatim(index, corpus, sample=10):
    """§10.1: ten sampled chunks, text == sed -n 'start,end p' of the source file."""
    conn = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    rows = conn.execute("SELECT path, start_line, end_line, text FROM chunks").fetchall()
    picks = random.Random(20261024).sample(rows, min(sample, len(rows)))
    bad = []
    for path, start, end, text in picks:
        source = (pathlib.Path(corpus) / path).read_text(
            encoding="utf-8", errors="replace").split("\n")
        if "\n".join(source[start - 1:end]) != text:
            bad.append(f"{path}:{start}-{end}")
    check("§10.1 抽查 10 块逐字相等", not bad, f"抽 {len(picks)} 块，不符 {len(bad)}"
          + (f" 例 {bad[0]}" if bad else ""))


def check_2_coverage(index, corpus):
    """§10.2: every non-blank line covered exactly once, no overlapping ranges."""
    conn = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    spans = {}
    overlap = 0
    for path, start, end in conn.execute(
            "SELECT path, start_line, end_line FROM chunks ORDER BY path, start_line"):
        spans.setdefault(path, []).append((start, end))
    missing = 0
    for path, ranges in spans.items():
        covered = set()
        last_end = 0
        for start, end in ranges:
            if start <= last_end:
                overlap += 1
            last_end = max(last_end, end)
            covered.update(range(start, end + 1))
        try:
            lines = (pathlib.Path(corpus) / path).read_text(
                encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            if line.strip() and lineno not in covered:
                missing += 1
    check("§10.2 非空白行全覆盖且无重叠", overlap == 0 and missing == 0,
          f"重叠 {overlap}，漏行 {missing}")


def check_3_determinism(index, corpus, pilot_files):
    """§10.3: same subset built twice gives the same rev and chunk_count."""
    first = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    rev1, count1 = (first.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()[0]
                    for k in ("rev", "chunk_count"))
    with tempfile.TemporaryDirectory() as tmp:
        second = str(pathlib.Path(tmp) / "second.sqlite")
        includes = [arg for path in pilot_files for arg in ("--include", path)]
        proc = subprocess.run(
            [sys.executable, "-m", "foamkb.cli", "build", "--corpus", corpus,
             "--index", second, *includes],
            capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0:
            check("§10.3 重复构建 rev 与块数不变", False, f"第二次构建失败: {proc.stderr[-200:]}")
            return
        again = sqlite3.connect(f"file:{second}?mode=ro", uri=True)
        rev2, count2 = (again.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()[0]
                        for k in ("rev", "chunk_count"))
        rows1 = first.execute("SELECT path,start_line,end_line,symbol,kind,text FROM chunks "
                             "ORDER BY path,start_line").fetchall()
        rows2 = again.execute("SELECT path,start_line,end_line,symbol,kind,text FROM chunks "
                             "ORDER BY path,start_line").fetchall()
    check("§10.3 重复构建 rev 与块数不变", rev1 == rev2 and count1 == count2 and rows1 == rows2,
          f"rev {rev1}=={rev2}, chunks {count1}=={count2}, 逐块相同={rows1 == rows2}")


def check_4_bad_bytes(index):
    """§10.4: an undecodable file aborts the build non-zero, and the old index survives."""
    with tempfile.TemporaryDirectory() as tmp:
        corpus = pathlib.Path(tmp) / "corpus"
        corpus.mkdir()
        (corpus / "good.H").write_text("class good {};\n", encoding="utf-8")
        (corpus / "bad.H").write_bytes(b"\xff\xfe not utf-8 \x80\x81")
        target = pathlib.Path(tmp) / "kb.sqlite"
        shutil.copy(index, target)          # a good index already exists
        before = target.read_bytes()

        proc = subprocess.run(
            [sys.executable, "-m", "foamkb.cli", "build", "--corpus", str(corpus),
             "--index", str(target), "--include", "**/*.H"],
            capture_output=True, text=True, timeout=300)
        exit_ok = proc.returncode != 0
        named = "bad.H" in (proc.stderr or "")
        survived = target.read_bytes() == before
        queryable = subprocess.run(
            [sys.executable, "-m", "foamkb.cli", "search", "--index", str(target),
             "--query", "fixedValueFvPatchField", "--top-k", "1"],
            capture_output=True, text=True, timeout=300).returncode == 0
    check("§10.4 解码失败非零退出且报出路径", exit_ok and named,
          f"exit={proc.returncode}, 报出文件名={named}")
    check("§10.4 失败后旧索引完好可查", survived and queryable,
          f"字节未变={survived}, 可查询={queryable}")


def check_5_identifiers(index):
    """§10.5: five known identifiers put the right file in the top 3."""
    bad = []
    for query, filename in IDENTIFIERS:
        hits = search_json(index, query, top_k=3)["hits"]
        rank = next((i for i, h in enumerate(hits, 1) if h["path"].endswith("/" + filename)), None)
        if rank is None:
            bad.append(f"{query} 未进前 3")
        print(f"     {query!r:<28} -> {filename:<52} 排名 {rank}")
    check("§10.5 5 个已知标识符进前 3", not bad, "; ".join(bad) or "5/5 达标")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("index")
    ap.add_argument("--corpus", default="/opt/openfoam13")
    ap.add_argument("--pilot-list", default="reports/pilot-300.txt")
    args = ap.parse_args()

    listed = [line.strip() for line in pathlib.Path(args.pilot_list).read_text().splitlines()
              if line.strip() and not line.startswith("#")]

    check_1_verbatim(args.index, args.corpus)
    check_2_coverage(args.index, args.corpus)
    check_3_determinism(args.index, args.corpus, listed)
    check_4_bad_bytes(args.index)
    check_5_identifiers(args.index)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\nfailures={len(failed)}")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
