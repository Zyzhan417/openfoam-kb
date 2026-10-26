#!/usr/bin/env python3
"""Structure statistics gate for the chunk index (Constitution: chunker semantic
changes must report four numbers from a full-corpus run).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/verify_structure.py <index.sqlite>

Exit 0 only when every assertion holds. Prints one line per check.

Constitution 2.1.0 requires four numbers: chunk count, kind distribution,
class-unit reachability, invariant violations (must be 0). Three extra
assertions guard the rules this feature added, plus the per-function size
limit that the constitution enforces.
"""
import argparse
import ast
import collections
import pathlib
import re
import sqlite3
import sys

SEPARATOR = re.compile(
    r"^(//\s*[\*\-]+\s*|//\s*\*+\s*//|/\*[\*\-]+.*[\*\-]+\*/|\\\*[\*\-]+.*[\*\-]+\*/)$")
LICENSE = ("GNU General Public", "WITHOUT ANY WARRANTY", "FITNESS FOR A PARTICULAR")
KEYWORDS = {"for", "if", "while", "switch", "catch", "else", "return", "do", "try",
            "forAll", "forAllIter", "forAllConstIter", "forAllReverse",
            "forAllStarEdges", "forAllStarFaces"}
CLASS_HEAD = re.compile(r"(?:class|struct|enum)\s+(?:[A-Za-z_]\w*\s+)*([A-Za-z_]\w*)")
FUNCTION_CAP = 50
FILE_TRIGGER = 400

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return cond


def separator_only(text):
    body = [line.strip() for line in text.split("\n") if line.strip()]
    return bool(body) and all(SEPARATOR.match(line) for line in body)


def connect(path):
    """Open read-only. **A missing index is an error, not an empty database.**

    Without this guard sqlite3 raises a bare `unable to open database file` traceback, which a
    deployer reading the last line cannot tell apart from a bug in this script -- and the gate
    then looks broken rather than mis-invoked (D12/D13).
    """
    p = pathlib.Path(path)
    if not p.exists():
        print(f"INDEX_UNAVAILABLE: {path} 不存在 —— 先按 README §3 建索引", file=sys.stderr)
        raise SystemExit(4)
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        print(f"INDEX_UNAVAILABLE: 打不开 {path}: {exc}", file=sys.stderr)
        raise SystemExit(4)


def chunk_stats(conn):
    q = conn.execute
    total = q("SELECT COUNT(*) FROM chunks").fetchone()[0]
    kinds = dict(q("SELECT kind, COUNT(*) FROM chunks GROUP BY kind").fetchall())
    texts = q("SELECT text FROM chunks").fetchall()
    distinct = len({t[0] for t in texts})
    lengths = sorted(len(t[0]) for t in texts)
    return total, kinds, distinct, lengths, texts


def last_line_by_file(conn):
    rows = conn.execute(
        "SELECT path, MAX(end_line) FROM chunks GROUP BY path").fetchall()
    return dict(rows)


def check_invariants(conn):
    """Non-overlap, coverage of every non-blank line, no cross-file chunk."""
    bad = collections.Counter()
    rows = conn.execute(
        "SELECT path, start_line, end_line FROM chunks ORDER BY path, start_line").fetchall()
    prev_path, prev_end = None, 0
    spans = collections.defaultdict(list)
    for path, start, end in rows:
        if start > end:
            bad["inverted_range"] += 1
        if path == prev_path and start <= prev_end:
            bad["overlap"] += 1
        spans[path].append((start, end))
        prev_path, prev_end = path, end
    return bad, spans


def _independent_comment_mask(lines):
    """True where a line carries only block-comment text and whitespace.

    Deliberately NOT imported from foamkb.lines: the verifier must not share the product's
    comment-tracking bug. Handles `//` line comments, `/* */` blocks, and the `\\*---*/`
    banner forms OpenFOAM uses.
    """
    mask, in_block = [], False
    for line in lines:
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            mask.append(True)
            continue
        if not stripped:
            mask.append(True)
            continue
        if stripped.startswith("//"):
            mask.append(True)
            continue
        if stripped.startswith("/*") or stripped.startswith("\\*"):
            if "*/" not in stripped[2:]:
                in_block = True
            mask.append(True)
            continue
        mask.append(False)
    return mask


def coverage_from_files(conn, corpus_root, spans):
    """Every non-blank line of every indexed file must be covered exactly once."""
    missing = uncovered = 0
    for path, file_lines in conn.execute("SELECT path, lines FROM files").fetchall():
        full = pathlib.Path(corpus_root) / path
        if not full.is_file():
            continue  # corpus moved; coverage cannot be judged
        text = full.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        blank = {i + 1 for i, line in enumerate(lines) if not line.strip()}
        covered = set()
        for start, end in spans.get(path, ()):
            covered.update(range(start, end + 1))
        gap = (set(range(1, len(lines) + 1)) - blank) - covered
        if gap:
            uncovered += 1
            missing += len(gap)
    return uncovered, missing


def class_reachability(conn, corpus_root):
    """Share of class/struct/enum declarations covered by a chunk whose symbol is that class.

    Also returns the scoped-name violations: a head like `class Foo::Bar` must produce the
    symbol `Bar`, never `Foo`. Judging that from a guessed namespace-name list is wrong --
    `class Foam` is a real class in this corpus -- so compare each head's last `::` segment
    against the symbol of the chunk that covers it.
    """
    by_class = collections.defaultdict(list)
    covering = []
    for path, start, end, symbol, kind in conn.execute(
            "SELECT path, start_line, end_line, symbol, kind FROM chunks"):
        if kind == "class" and symbol is not None:
            by_class[(path, symbol)].append((start, end))
        if kind == "class":
            covering.append((path, start, end, symbol))
    heads = reached = 0
    scoped_violations = []
    for path in {p for p, in conn.execute("SELECT DISTINCT path FROM chunks")}:
        full = pathlib.Path(corpus_root) / path
        if not full.is_file():
            continue
        lines = full.read_text(encoding="utf-8", errors="replace").split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if line != line.lstrip() or not re.match(r"(class|struct|enum)\s", stripped):
                continue
            if stripped.endswith(";"):
                continue  # forward declaration
            match = CLASS_HEAD.match(stripped)
            if not match:
                continue
            name = match.group(1)
            heads += 1
            if any(a <= i + 1 <= b for a, b in by_class.get((path, name), ())):
                reached += 1
            if "::" in stripped:
                expected = _declared_last_segment(stripped)
                actual = next((sym for p, a, b, sym in covering
                               if p == path and a <= i + 1 <= b), "<no class chunk>")
                if expected and actual != expected:
                    scoped_violations.append((path, i + 1, stripped[:60], expected, actual))
    return reached, heads, scoped_violations


def _declared_last_segment(head):
    """`class Foam::CarrierField<Type>::Functor` -> 'Functor'.

    Template arguments are removed first but must not truncate what follows them,
    which is why the `<...>` groups are stripped as balanced pairs rather than by
    splitting at the first `<`.
    """
    match = re.match(r"(?:class|struct|enum)\s+", head)
    if not match:
        return None
    raw = re.split(r"[\s{]", head[match.end():].strip(), maxsplit=1)[0]
    name, depth = [], 0
    for char in raw:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            name.append(char)
    name = "".join(name)
    if not name or not re.match(r"[A-Za-z_]", name):
        return None
    return name.rsplit("::", 1)[-1]


def check_source_sizes(*dirs):
    """Constitution 2.2.0: every function <= 50 lines and every file <= 400 lines.

    Product code AND tests are both in scope -- 2.1.0 only policed src/, which let
    test_chunker.py reach 862 lines unchecked.
    """
    over_fn, over_file = [], []
    files = [p for d in dirs for p in sorted(pathlib.Path(d).rglob("*.py"))]
    for path in files:
        text = path.read_text()
        lines = text.count("\n")
        if lines > FILE_TRIGGER:
            over_file.append((path.name, lines))
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = node.end_lineno
                if end is None:
                    continue
                span = end - node.lineno + 1
                if span > FUNCTION_CAP:
                    over_fn.append((path.name, node.name, span))
    return over_fn, over_file


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("index")
    ap.add_argument("--corpus", default="/opt/openfoam13")
    ap.add_argument("--src", default="src/foamkb", help="product code root")
    ap.add_argument("--tests", default="tests", help="test root (same limits)")
    args = ap.parse_args()

    conn = connect(args.index)
    # The doc slice is produced by Reader.file_doc, so the gate must ask the product
    # code for it rather than re-deriving the rule (that duplication is how a checker
    # and the checked drift apart).
    from foamkb.index import open_reader
    reader = open_reader(args.index)

    total, kinds, distinct, lengths, texts = chunk_stats(conn)
    median_lines = conn.execute(
        "SELECT end_line - start_line + 1 FROM chunks "
        "ORDER BY end_line - start_line + 1 LIMIT 1 OFFSET ?",
        (total // 2,)).fetchone()
    median_lines = median_lines[0] if median_lines else 0

    # --- the four numbers the constitution requires ---
    print("=== 宪法要求的四个数字 ===")
    print(f"  块数            : {total}")
    print(f"  kind 分布       : {dict(sorted(kinds.items(), key=lambda kv: -kv[1]))}")
    print(f"  中位块行数      : {median_lines}")

    bad, spans = check_invariants(conn)
    uncovered, missing = coverage_from_files(conn, args.corpus, spans)
    violations = sum(bad.values()) + uncovered
    print(f"  不变量违背数    : {violations}"
          f"  (overlap={bad['overlap']} inverted={bad['inverted_range']}"
          f" uncovered_files={uncovered} missing_lines={missing})")

    reached, heads, scoped_bad = class_reachability(conn, args.corpus)
    reach_pct = 100 * reached / heads if heads else 0.0
    print(f"  class 单元可达率: {reached}/{heads} = {reach_pct:.1f}%")
    print("\n=== 附加断言 ===")
    # SC-001
    check("SC-001 class 单元可达率 >= 85%", reach_pct >= 85.0, f"{reach_pct:.1f}%")
    # SC-002
    kw_rows = conn.execute(
        "SELECT COUNT(*) FROM symbols WHERE leaf IN (%s)"
        % ",".join("?" * len(KEYWORDS)), tuple(KEYWORDS)).fetchone()[0]
    check("SC-002 关键字符号数 = 0", kw_rows == 0, f"{kw_rows} 行")
    # SC-003
    check("SC-003 块数 <= 100000 且中位行数 >= 10", total <= 100_000 and median_lines >= 10,
          f"chunks={total} median={median_lines}")
    # SC-004
    check("SC-004 不变量违背数 = 0", violations == 0, f"{violations}")
    # SC-005
    uniq_pct = 100 * distinct / total if total else 0.0
    check("SC-005 唯一文本占比 >= 70%", uniq_pct >= 70.0, f"{uniq_pct:.1f}%")
    # SC-011 determinism is asserted by rebuilding twice; metadata comparison below
    rev = conn.execute("SELECT value FROM meta WHERE key='rev'").fetchone()
    check("SC-011 meta.rev 存在且为 8 位十六进制",
          bool(rev) and re.fullmatch(r"[0-9a-f]{8}", rev[0]) is not None,
          rev[0] if rev else "missing")
    # SC-012  any chunk consisting only of section separators, anywhere in the file
    eof_sep = 0
    for (text,) in conn.execute("SELECT text FROM chunks"):
        if separator_only(text):
            eof_sep += 1
    check("SC-012 纯分隔线块数 = 0（任何位置）", eof_sep == 0, f"{eof_sep}")
    # SC-013
    check("SC-013 限定名类声明取末段作符号", not scoped_bad,
          f"{len(scoped_bad)} 例" + (f"，首例 {scoped_bad[0]}" if scoped_bad else ""))
    # SC-014 (spec C14): the licence test applies to the Description-onward SLICE, not
    # the whole filedoc chunk. The chunk holds licence+Class+Description in one block
    # comment, so requiring the chunk itself to be licence-free rejects 100% of files.
    per_file = dict(conn.execute(
        "SELECT path, COUNT(*) FROM chunks WHERE kind='filedoc' GROUP BY path").fetchall())
    too_many = [p for p, n in per_file.items() if n > 1]
    licensed_slices, no_slice, docs_with_slice = [], 0, []
    for (path,) in conn.execute("SELECT path FROM chunks WHERE kind='filedoc'"):
        doc = reader.file_doc(path)
        if doc is None:
            no_slice += 1
            continue
        docs_with_slice.append(path)
        if any(k in doc["text"] for k in LICENSE):
            licensed_slices.append(path)
    check("SC-014 每文件 filedoc <= 1", not too_many, f"multi={len(too_many)}")
    # SC-014: the doc must contain no CODE lines. Directive text is legitimate inside a
    # description (OpenFOAM documents usage with `#include "setRootCase.H"` examples), so
    # "no directives" was the wrong test -- it fired on 22 files whose docs were correct.
    # The real invariant: every doc line sits inside a block comment (or is blank).
    # The mask is computed HERE, independently of foamkb.lines, so a bug in the product
    # comment tracker cannot hide behind an identical bug in the checker.
    code_leaks = []
    for path in docs_with_slice:
        doc = reader.file_doc(path) or {}
        full = pathlib.Path(args.corpus) / path
        if not full.exists():
            continue
        mask = _independent_comment_mask(
            full.read_text(encoding="utf-8", errors="replace").split("\n"))
        for off, line in enumerate(doc["text"].split("\n")):
            if not line.strip():
                continue
            idx = doc["start_line"] - 1 + off
            if idx < len(mask) and not mask[idx]:
                code_leaks.append(f"{path}:{doc['start_line'] + off}")
                break
    check("SC-014 doc 不含代码行（每行都在块注释内）", not code_leaks,
          f"{len(code_leaks)} 例" + (f"，首例 {code_leaks[0]}" if code_leaks else ""))
    check("SC-014 doc 切片不含许可证", not licensed_slices,
          f"{len(licensed_slices)} 例" + (f"，首例 {licensed_slices[0]}" if licensed_slices else ""))
    check("SC-014 有 doc 的文件数 >= 2500", len(docs_with_slice) >= 2500,
          f"{len(docs_with_slice)} 个文件")
    # SC-016: the candidate guard of FR-011 -- a class/function chunk must never be
    # relabelled filedoc, or its symbol disappears.
    bad_kind = [p for (p,) in conn.execute("SELECT path FROM chunks WHERE kind='filedoc'")
                if not reader.file_doc(p)]
    check("SC-016 class/function 未被改写成 filedoc", kinds.get("class", 0) > 0
          and kinds.get("function", 0) > 0 and not bad_kind,
          f"class={kinds.get('class')} function={kinds.get('function')}")

    print("\n=== 宪法 2.1.0 规模约束（源码）===")
    over_fn, over_file = check_source_sizes(args.src, args.tests)
    check(f"单函数 <= {FUNCTION_CAP} 行", not over_fn, str(over_fn[:3]))
    check(f"单文件 <= {FILE_TRIGGER} 行（产品与测试同样适用）", not over_file,
          str(over_file))

    failed = [name for name, ok, _ in results if not ok]
    print(f"\nfailures={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
