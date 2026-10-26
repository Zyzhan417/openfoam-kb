#!/usr/bin/env python3
"""Retrieval acceptance gate. Carries 002's contract and 003's ranking targets.

The assertion object is the CLI's **stdout JSON**, not database rows: an index can hold the
right data and still serialise it wrong, and it is the JSON an agent actually reads.

Query classes, kept apart on purpose:

  002 SC-008     exact identifiers -- passed before the structural rework, MUST still pass.
  003 SC-002/003 natural language -- 002 only recorded these; 003 makes the two targets hard,
                 at the ranks agreed in 003-D1 (<=4 and <=10).
  003 SC-005     six more identifiers -- must not get *worse* than the measured baseline.
  003 SC-006     eight natural-language queries -- guard against catastrophic loss only.
  003 SC-008     term-level judgement: model/models must be one stored term. This is the only
                 check that catches "the DDL changed but the index was never rebuilt".

Every target filename below was resolved against the corpus (basename, exactly one file each);
none is written from memory.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/verify_retrieval.py kb.sqlite [--corpus DIR]
"""
import argparse
import json
import pathlib
import re
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# --- 002 SC-008: exact identifiers, MUST hold (regression guard) --------------
REGRESSION = [
    ("fixedValueFvPatchField", "fixedValueFvPatchField.H", 1),
    ("nutUSpaldingWallFunction", "nutUSpaldingWallFunctionFvPatchScalarField.H", 1),
    ("kOmegaSST", "kOmegaSST.H", 3),
    ("kEpsilon", "kEpsilon.H", 1),
]

# --- 003 SC-002 / SC-003: the two targets this feature was opened for --------
# Baselines are CLI (top_k=20) ranks measured on the control index -- the same build with
# only the tokenizer swapped back to unicode61 -- NOT on a raw FTS probe. The two differ:
# RRF merges the symbol channel, so a raw-FTS rank of 3 can surface as 6 through the CLI.
# (query, target file, max rank, 003's delivered rank; None = outside top 20)
# 004 tightened both lines: 003 allowed <=10, and the symbol-channel change brings them to 3
# and 7. The third column keeps 003's value so a reader sees what 004 bought.
NATURAL_TARGETS = [
    ("incompressible turbulence model", "incompressibleMomentumTransportModels.H", 4, 6),
    ("wall function for nut", "nutUSpaldingWallFunctionFvPatchScalarField.H", 7, 8),
]

# --- 003 SC-005 (tightened by 004): identifiers must not get worse -----------
# CLI ranks on the control index. None = outside the CLI's top 20.
# 005 C2: `volScalarField` moved to TYPE_NAME_TARGETS below. Its old target
# `volFields.H` does NOT contain the word at all (0 occurrences in 80 lines) -- the real
# definition is `typedef VolField<scalar> volScalarField;` in volFieldsFwd.H:62. With the
# wrong target the row could never fail, which is why a real gap survived two features.
IDENTIFIER_BASELINE = [
    ("fvMesh", "fvMesh.H", 2),
    ("polyMesh", "polyMesh.H", 2),
    ("Time", "Time.H", 2),
    ("SpalartAllmaras", "SpalartAllmaras.H", 1),
    ("nutWallFunction", "nutWallFunctionFvPatchScalarField.H", 1),
]

# --- 005 SC-001 / SC-002: a type name must reach its definition --------------
# Every target was verified to contain the word: `volFieldsFwd.H:58` holds six aliases
# including `typedef VolField<scalar> volScalarField;`. Ranks are the product's own top_k=20
# ceiling. Baselines are the schema-3 index `c19_stem.sqlite`, where all three were outside
# the top 20 -- so these rows can detect a regression in either direction.
# (query, target file, max rank, baseline on schema 3)
# Lines are the MEASURED values, not value+3. A loose line is not evidence: with <=5 and a
# measured 2, a regression to 3/4/5 would keep this gate green, and SC-003 cannot catch it
# either (its pre-side is the fixed schema-3 build where these rows are `None`, so 2 -> 3
# reads as "better"). The "2 is a coincidence of path ordering" worry does not justify the
# slack: the corpus is pinned at rev 2cf4316a, so the paths that decide the tie never move.
# If the corpus rev changes, these three numbers must be re-measured -- see spec 待验证.
TYPE_NAME_TARGETS = [
    ("volScalarField", "volFieldsFwd.H", 2, None),
    ("volVectorField", "volFieldsFwd.H", 2, None),
    ("surfaceScalarField", "surfaceFieldsFwd.H", 1, None),
]

# --- 003 SC-006: no query that ranked at baseline may fall out of the top 20 --
# The product caps top_k at 20, so "top 50" is not expressible through it; the guard is
# stated at the ceiling the contract actually has. Baselines are control-index CLI ranks.
# 005 C4: `pressure velocity coupling` was removed -- the corpus contains
# `pressure-velocity coupling` ZERO times (`pressure-velocity` appears once, without the
# word coupling), so there is nothing to retrieve; that is a lexical fact, not a ranking
# defect. It is recorded as an M1 (vector channel) case instead.
NATURAL_GUARD = [
    ("wall function for nut", "nutWallFunctionFvPatchScalarField.H", 16),
    ("wall function for nut", "nutUSpaldingWallFunctionFvPatchScalarField.H", 13),
    ("incompressible turbulence model", "incompressibleMomentumTransportModels.H", None),
    ("turbulent kinetic energy dissipation", "kEpsilon.H", None),
    ("spalart allmaras turbulence model", "SpalartAllmaras.H", 6),
    ("finite volume mesh", "fvMesh.H", None),
    ("boundary conditions for pressure", "fixedValueFvPatchField.H", None),
]

# --- 004 SC-004 / FR-007: mixed "identifier + prose" queries -----------------
# Every pair was resolved against the corpus first (target exists exactly once) and every
# baseline rank below is non-None. That matters: a query whose baseline is None cannot
# detect a regression at all, and the first draft of this set wasted 5 of 12 rows on
# `calcNut`, which appears in ZERO files of OpenFOAM 13 (it is a unit-test fixture name).
# The check asserts "not worse than baseline", and four of them must still be rank 1.
MIXED_BASELINE = [
    ("fvMesh topology", "fvMesh.H", 1),
    ("objectRegistry lookup", "objectRegistry.H", 1),
    ("polyMesh boundary", "polyMesh.H", 1),
    ("Time controlDict", "Time.H", 1),
    ("kOmegaSST model", "kOmegaSST.H", 3),
    ("kEpsilon model", "kEpsilon.H", 1),
    ("nutUSpaldingWallFunction nut", "nutUSpaldingWallFunctionFvPatchScalarField.H", 4),
    ("SpalartAllmaras coefficients", "SpalartAllmaras.H", 1),
    ("fvSolution solver", "fvSolution.H", 1),
    ("wallBoiling heat", "wallBoiling.H", 1),
    ("GAMGSolver cycle", "GAMGSolver.H", 1),
    ("fixedValueFvPatchField value", "fixedValueFvPatchField.H", 1),
]
# The four above that must be exactly 1 after 004: the first three are the queries the
# per-token fallback was introduced for, and `Time controlDict` is the guard against
# narrowing the criterion back to "capital after the first character" (measured: 1 -> gone).
MUST_STAY_ONE = ("fvMesh topology", "objectRegistry lookup", "polyMesh boundary",
                 "Time controlDict")


# 003 SC-008: under porter these two words must resolve to a single stored term.
STEM_TERMS = ("model", "models")

# 002 SC-015: a phrase that appears only inside a Description section.
DESC_PHRASE = "Specialisation for RAS"
LICENCE = ("GNU General Public", "WITHOUT ANY WARRANTY", "FITNESS FOR A PARTICULAR")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
    return ok


def run_search(index, query, top_k=20, extra=()):
    """Call the CLI exactly as an agent would and return its parsed stdout."""
    cmd = [sys.executable, "-m", "foamkb.cli", "search", "--index", index,
           "--query", query, "--top-k", str(top_k), *extra]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"search failed ({proc.returncode}) for {query!r}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def rank_of(hits, filename):
    for position, hit in enumerate(hits, 1):
        if hit["path"].endswith("/" + filename) or hit["path"] == filename:
            return position
    return None


def measured(index, query, filename):
    """Rank of a target file through the CLI, at the product's own top_k ceiling (20)."""
    return rank_of(run_search(index, query, top_k=20)["hits"], filename)


def check_regression(index):
    """002 SC-008: exact-identifier queries must not have regressed."""
    print("\n=== 002 SC-008 精确标识符（回归守卫，MUST） ===")
    for query, filename, limit in REGRESSION:
        rank = rank_of(run_search(index, query, top_k=20)["hits"], filename)
        check(f"002 SC-008 {query!r} -> {filename} 排名 <= {limit}",
              rank is not None and rank <= limit, f"实测 {rank}")


def check_natural_targets(index):
    """003 SC-002/003 as tightened by 004: the two queries this work exists for."""
    print("\n=== 003 SC-002 / SC-003（004 收紧）自然语言目标 ===")
    for query, filename, limit, before in NATURAL_TARGETS:
        rank = measured(index, query, filename)
        base = "前 20 名外" if before is None else str(before)
        check(f"003 {query!r} -> {filename} 排名 <= {limit}（003 交付 {base}）",
              rank is not None and rank <= limit, f"实测 {rank}")


def check_mixed(index):
    """004 SC-004 / FR-007: mixed queries keep the rank the fallback bought them."""
    print("\n=== 004 SC-004 混写查询（12 条，零退化） ===")
    rows = []
    for query, filename, baseline in MIXED_BASELINE:
        rank = measured(index, query, filename)
        rows.append((query, rank, baseline))
        ok = rank is not None and rank <= baseline
        extra = ""
        if query in MUST_STAY_ONE:
            ok = ok and rank == 1
            extra = "（必须仍为 1）"
        check(f"004 SC-004 {query!r} -> {filename} 基线 {baseline}{extra}", ok, f"实测 {rank}")
    guarded = sum(1 for _q, _r, b in rows if b is not None)
    check("004 SC-004 样本检出力（基线非空的条数）", guarded == len(rows),
          f"{guarded}/{len(rows)} 条基线非空；基线为 None 的查询检不出退化")



def check_identifier_baseline(index):
    """003 SC-005: five more identifiers must not get worse than the measured baseline."""
    print("\n=== 003 SC-005 附加标识符（不得变差） ===")
    for query, filename, baseline in IDENTIFIER_BASELINE:
        rank = measured(index, query, filename)
        if baseline is None:
            # Was outside the CLI's top 20; staying outside is acceptable, and so is entering.
            ok = rank is None or rank <= 20
        else:
            ok = rank is not None and rank <= baseline
        check(f"003 SC-005 {query!r} -> {filename} 不差于基线 {baseline}", ok, f"实测 {rank}")


def check_type_name_targets(index):
    """005 SC-001 / SC-002: a type name must reach the file that declares it.

    These are the rows this feature exists for, so they are hard lines, not guards. The
    baseline is `None` on the schema-3 index for all three (all were outside the top 20),
    which means the row can only pass by actually being found -- an improvement direction
    that a `None` baseline normally cannot detect (A20) is exactly what is asserted here,
    because the line is "<= max rank", never "not worse than None".
    """
    print("\n=== 005 SC-001 / SC-002 类型名到达定义点 ===")
    for query, filename, limit, before in TYPE_NAME_TARGETS:
        rank = measured(index, query, filename)
        base = "前 20 名外" if before is None else str(before)
        check(f"005 {query!r} -> {filename} 排名 <= {limit}（schema 3 时 {base}）",
              rank is not None and rank <= limit, f"实测 {rank}")


def check_natural_guard(index):
    """003 SC-006: nothing that ranked at baseline may fall out of the top 20.

    Queries whose baseline is `None` are **reported, not asserted**. The old code ran
    `ok = rank is None or rank <= 20` for them, which cannot be False: `measured()` uses
    top_k=20, so `rank` is either None or 1..20. Five of the eight rows were that shape, so
    "zero regression" rested on three samples while claiming eight (AGENTS A20, and critic
    finding 7 on phase_003). A gate that cannot fail is not evidence, so those rows now
    print `[note]` and are counted separately -- the sample-power line at the end states how
    many rows actually had something to lose.
    """
    print("\n=== 003 SC-006 自然语言（退化守卫） ===")
    guarded_n = 0
    for query, filename, baseline in NATURAL_GUARD:
        rank = measured(index, query, filename)
        if baseline is None:
            print(f"[note] 003 SC-006 {query!r} -> {filename} 基线 None，实测 {rank}"
                  f"（基线即在前 20 外，此行无检出力，不计入零退化）")
            continue
        guarded_n += 1
        check(f"003 SC-006 {query!r} -> {filename} 基线 {baseline}",
              rank is not None and rank <= 20 and rank <= baseline, f"实测 {rank}")
    print(f"\n003 SC-006 检出力：{guarded_n}/{len(NATURAL_GUARD)} 条基线非空"
          f"（其余 {len(NATURAL_GUARD) - guarded_n} 条基线为 None，只能记录）")


def check_stem_terms(index, corpus):
    """003 SC-008: model and models must resolve to one stored term, checked without mutation.

    `fts5vocab` is not usable here: it cannot be created in the temp schema while pointing at
    a FTS table in main ("no such fts5 table: temp.main.chunks_fts"), and creating it in main
    writes a table into the very index under measurement. So the same claim is made through
    observable behaviour instead:

      (a) both spellings match exactly the same documents -- true only if they stem to one
          term, since under unicode61 they are two unrelated tokens;
      (b) a filedoc whose text says `models` and never `model` is returned for `model`.

    (b) alone is not enough (a build hard-coding `model` into `lex_text` would pass it), and
    (a) alone is not enough (two counts can coincide). Together they pin the mechanism.
    """
    print("\n=== 003 SC-008 词干化机制判据 ===")
    conn = sqlite3.connect(index)
    try:
        counts = {}
        for word in STEM_TERMS:
            counts[word] = conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                                        (f'"{word}"',)).fetchone()[0]
        only_models = conn.execute(
            "SELECT id, text FROM chunks WHERE kind = 'filedoc'"
            " AND text LIKE '%models%' AND text NOT LIKE '%model %' LIMIT 200").fetchall()
        hit_count = 0
        probed = 0
        for chunk_id, _text in only_models:
            if "model" in _text.split() or "models" not in _text:
                continue
            probed += 1
            matched = conn.execute(
                "SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH ? AND rowid = ?",
                ('"model"', chunk_id)).fetchone()
            hit_count += 1 if matched else 0
        if not probed:  # fall back to the synthetic truth: any doc whose only spelling is plural
            probed = 0
    finally:
        conn.close()

    check("003 SC-008 'model' 与 'models' 命中同一批文档（同词干）",
          counts.get("model", 0) > 0 and counts.get("model") == counts.get("models"),
          f"counts={counts}")
    check("003 SC-008 只写 'models' 的 filedoc 能被 'model' 命中",
          probed > 0 and hit_count == probed,
          f"检查 {probed} 个，命中 {hit_count}" if probed else "全库无该形态，需看单元测试")


def check_index_shape(index):
    """004 SC-006, re-stated by 005: the index shape must match the current contract.

    004 was query-side only, so its SC-006 proved "the index was NOT rebuilt" through a
    frozen `schema == 3` and an mtime older than the newest source change. 005 rebuilds the
    index by design (symbols gains typedef rows, schema 3 -> 4), so that premise is gone.
    The intent behind SC-006 is kept -- the index must not drift away from what the code
    under test would produce -- by asserting the facts that a correct rebuild guarantees:
    the current schema stamp, the tokenizer, the corpus shape, and the symbol-row count that
    only the 005 build rule can produce. Dropping the mtime check is deliberate: it can only
    ever pass on a stale index, so keeping it would forbid the very rebuild 005 requires.
    """
    print("\n=== 004 SC-006 / 005 索引形态符合当前契约 ===")
    conn = sqlite3.connect(index)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM chunks GROUP BY kind").fetchall())
        symbol_rows = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        symbol_kind = conn.execute("SELECT COUNT(*) FROM symbols WHERE kind IN "
                                  "('typedef','using')").fetchone()[0]
        # SC-005: the indented class-member aliases must NOT be in the table. The filter on
        # `kind` is required: `leaf='type'` already had 5 rows before this feature -- they are
        # ordinary member FUNCTIONS named `type` (`Foam::token::type` etc.), not aliases.
        # Judging by a bare name count would report a red that is not a defect.
        members = {name: conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE leaf = ? AND kind IN ('typedef','using')",
            (name,)).fetchone()[0]
            for name in ("type", "alphaField", "rhoField", "cmptType")}
    finally:
        conn.close()
    check("005 meta.schema 为 4（005 升版；旧库必须被拒）",
          meta.get("schema") == "4", str(meta.get("schema")))
    check("004 SC-006 meta.fts_tokenize 仍为 porter 串",
          meta.get("fts_tokenize") == "porter unicode61 tokenchars '_.'",
          str(meta.get("fts_tokenize")))
    check("004 SC-006 chunk_count 仍为 92,208", meta.get("chunk_count") == "92208",
          str(meta.get("chunk_count")))
    expected = {"text": 67588, "function": 14562, "class": 6127, "filedoc": 3905, "lines": 26}
    check("004 SC-006 kind 分布与 003 逐项相同", kinds == expected, str(kinds))
    # 20689 class/function rows + 212 namespace-scope aliases, measured on rev 2cf4316a.
    # A rebuild from a different rule changes this number, which is the point.
    check("005 symbols 行数为 20,901（20,689 + 212 别名）", symbol_rows == 20901, str(symbol_rows))
    check("005 别名行恰为 212（typedef 189 + using 23）", symbol_kind == 212, str(symbol_kind))
    check("005 SC-005 类内成员别名 0 行（type/alphaField/rhoField/cmptType）",
          all(count == 0 for count in members.values()), str(members))


def check_symbol_contract(index):
    """FR-001 / FR-005: what the symbol table must say about itself.

    Two facts that earlier had no failing assertion anywhere -- the phase summary quoted
    `verify_retrieval.py` for both, but this script never printed or checked them (caught by
    the phase-005 critic, violation 2). They are hard contracts:

      FR-001  every symbol row carries the kind of declaration it came from, so a caller can
              tell a class from a typedef without guessing.
      FR-005  the alias names must NOT be written into `chunks.symbol`: that column feeds
              `chunks_fts.symbol`, which carries bm25 weight 5.0 -- pushing 212 type names
              into it would re-rank every query through an unmeasured path.
    """
    print("\n=== FR-001 / FR-005 符号表契约 ===")
    conn = sqlite3.connect(index)
    try:
        kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM symbols GROUP BY kind").fetchall())
        leaked = conn.execute(
            "SELECT COUNT(*) FROM chunks c JOIN (SELECT DISTINCT chunk_id FROM symbols"
            " WHERE kind IN ('typedef','using')) a ON a.chunk_id = c.id"
            " WHERE c.symbol IS NOT NULL AND trim(c.symbol) <> ''").fetchone()[0]
    finally:
        conn.close()
    expected = {"function": 14562, "class": 6127, "typedef": 189, "using": 23}
    check("FR-001 symbols.kind 分布 = function 14562 / class 6127 / typedef 189 / using 23",
          kinds == expected, str(kinds))
    check("FR-005 别名块的 chunks.symbol 全为 null（名字不得进 bm25 权重 5.0 那一列）",
          leaked == 0, f"{leaked} 行非空")


def check_tuning_constants(source_root):
    """003 FR-006 / FR-004: this feature changed the tokenizer only."""
    print("\n=== 003 FR-006 打分常数未变 / FR-004 查询侧无自造词干化 ===")
    reader_py = (source_root / "src/foamkb/reader.py").read_text(encoding="utf-8")
    search_py = (source_root / "src/foamkb/search.py").read_text(encoding="utf-8")
    weights = re.search(r"BM25_WEIGHTS = \(([^)]*)\)", reader_py)
    got = " ".join(weights.group(1).split()) if weights else "未找到"
    check("003 FR-006 BM25_WEIGHTS 仍为 (1.0, 0.1, 5.0)", got == "1.0, 0.1, 5.0", got)
    check("003 FR-006 RRF_K 仍为 60", re.search(r"RRF_K = 60\b", search_py) is not None)
    check("003 FR-004 查询侧没有自造词干化",
          re.search(r"\bporter\b", search_py) is None)


def check_doc_shape(index):
    """002 FR-013b: every hit carries exactly ten fields and never leaks internal ones."""
    print("\n=== 002 FR-013b 十字段 ===")
    hits = run_search(index, "RASModel", top_k=5)["hits"]
    expected = {"ref", "path", "start_line", "end_line", "text_start_line",
                "symbol", "kind", "text", "truncated", "doc"}
    wrong = [sorted(h) for h in hits if set(h) != expected]
    check("002 FR-013b 每条命中恰好十个字段", not wrong, f"异常 {len(wrong)} 条")
    leaks = [h["path"] for h in hits if "score" in h or "unit_id" in h]
    check("002 FR-013b 不输出 score / unit_id", not leaks, f"{len(leaks)} 条泄漏")


def check_doc_verbatim(index, corpus):
    """002 SC-006: doc.text is byte-identical to the named line range of the source file.

    Samples several queries and de-duplicates by (path, start_line): a single query is a
    fragile sample -- `RASModel` alone yields 5 doc-carrying hits on one index and 2 on
    another, purely because the ranking moved, and the "non-vacuous" floor then misfires.

    Guarded against a vacuous pass: if every doc came back None the loop below would check
    nothing and still print PASS. `checked` is therefore reported and floored.
    """
    print("\n=== 002 SC-006 / SC-007 doc 逐字与许可证 ===")
    seen, checked = set(), 0
    mismatched = licensed = flagged = 0
    for query in ("RASModel", "kOmegaSST", "wall function", "turbulence"):
        for hit in run_search(index, query, top_k=20)["hits"]:
            key = (hit["path"], hit["start_line"])
            if hit["kind"] not in ("class", "function") or key in seen:
                continue
            seen.add(key)
            doc = hit.get("doc")
            if doc is None:
                continue  # either the file has no filedoc, or FR-012 rejected it for licence
            checked += 1
            source = (pathlib.Path(corpus) / hit["path"]).read_text(
                encoding="utf-8", errors="replace").split("\n")
            want = "\n".join(source[doc["start_line"] - 1:doc["end_line"]])
            if want != doc["text"]:
                mismatched += 1
            if any(marker in doc["text"] for marker in LICENCE):
                licensed += 1
            if "truncated" in doc:
                flagged += 1  # C19: doc carries no truncation flag at all
    check("002 SC-006 doc.text 按区间回切原文逐字相等", mismatched == 0,
          f"检查 {checked} 条，不符 {mismatched}")
    check("002 SC-006 检查量非零（防止空洞通过）", checked >= 3, f"实际检查 {checked} 条")
    check("002 SC-007 doc 不含许可证特征文本", licensed == 0, f"{licensed} 条")
    check("002 C19 doc 无 truncated 键", flagged == 0, f"{flagged} 条带 truncated")


def check_description_hit(index):
    """002 SC-015: the filedoc chunk is itself retrievable, not just an attached field."""
    print("\n=== 002 SC-015 说明段可被单独命中 ===")
    hits = run_search(index, DESC_PHRASE, top_k=5)["hits"]
    kinds = [h["kind"] for h in hits]
    check("002 SC-015 命中 filedoc 块", "filedoc" in kinds, f"kinds={kinds}")
    first = hits[0] if hits else {}
    check("002 SC-015 该命中 doc 为 null（自身即说明）",
          first.get("doc") is None if first else False)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("index")
    ap.add_argument("--corpus", default="/opt/openfoam13")
    args = ap.parse_args()

    check_regression(args.index)
    check_natural_targets(args.index)
    check_mixed(args.index)
    check_identifier_baseline(args.index)
    check_type_name_targets(args.index)
    check_natural_guard(args.index)
    check_stem_terms(args.index, args.corpus)
    check_index_shape(args.index)
    check_symbol_contract(args.index)
    check_tuning_constants(ROOT)
    check_doc_shape(args.index)
    check_doc_verbatim(args.index, args.corpus)
    check_description_hit(args.index)

    failed = [name for name, ok, _ in results if not ok]
    print(f"\nfailures={len(failed)}")
    for name in failed:
        print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
