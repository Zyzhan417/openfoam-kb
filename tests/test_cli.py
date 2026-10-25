"""CLI contract: exit codes 0/2/3/4/5, JSON-only stdout, SC-004 end-to-end (FR-019..FR-024)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from foamkb import cli, index
from foamkb.config import GLUE_MIN_CHARS
from foamkb.errors import IndexUnavailable, InternalError, InvalidArgument, NotFound

ROOT = Path(__file__).resolve().parents[1]
# The ten public hit fields; score / unit_id never reach stdout (FR-022, FR-013b).
# This is a CONTRACT CHANGE, not a loosened assertion (AGENTS C10): FR-013 added `doc`
# ({start_line, end_line, text}, or null) and FR-013b added `kind`, taking the closed list
# from eight to ten. The list is still compared as an exact set of names, never "at least",
# and the two internal fields stay excluded by name.
TEN = ("ref", "path", "start_line", "end_line", "text_start_line", "symbol", "kind", "text",
       "truncated", "doc")
ALPHA = """Foam::scalar Foam::alpha::calcAlpha()
{
    const scalar fvMeshWeight = 1.0;
    return fvMeshWeight;
}
"""
BETA = """Foam::scalar Foam::beta::calcBeta()
{
    const scalar betaTwo = 2.0;
    return betaTwo;
}
"""


def _run(capsys, argv):
    """Call main() in-process and return (exit code, stdout, stderr)."""
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _build_argv(root, index_path):
    return ["build", "--corpus", str(root), "--index", index_path, "--include", "*.C"]


@pytest.fixture
def built(corpus, tmp_path, capsys):
    """A built index over the two-file synthetic corpus, with its output consumed."""
    root = corpus({"src/alpha.C": ALPHA, "src/beta.C": BETA})
    index_path = str(tmp_path / "kb.sqlite")
    assert cli.main(_build_argv(root, index_path)) == 0
    capsys.readouterr()
    return root, index_path


def test_build_exits_zero_with_report_on_stderr_only(built, capsys):
    """T035: build returns 0, stdout stays empty, the report goes to stderr (FR-024)."""
    root, index_path = built
    code, out, err = _run(capsys, _build_argv(root, index_path))
    assert code == 0
    assert out == ""
    first = err.splitlines()[0]
    assert "rev=" in first and "chunk_count=" in first


def test_build_glue_min_chars_flag_lands_in_meta(corpus, tmp_path, capsys):
    """T020/FR-015: the glue floor is overridable from the CLI, and the build records it."""
    root = corpus({"src/alpha.C": ALPHA})
    index_path = str(tmp_path / "glue.sqlite")
    assert cli.main(_build_argv(root, index_path) + ["--glue-min-chars", "800"]) == 0
    capsys.readouterr()
    assert index.open_reader(index_path).meta("glue_min_chars") == "800"


def test_build_glue_min_chars_default_stays_the_config_constant(corpus, tmp_path, capsys):
    """T020/FR-015: no flag → GLUE_MIN_CHARS. The CLI must not invent a default of its own."""
    root = corpus({"src/alpha.C": ALPHA})
    index_path = str(tmp_path / "default.sqlite")
    assert cli.main(_build_argv(root, index_path)) == 0
    capsys.readouterr()
    assert index.open_reader(index_path).meta("glue_min_chars") == str(GLUE_MIN_CHARS)


def test_build_glue_min_chars_out_of_range_is_invalid_argument(corpus, tmp_path, capsys):
    """FR-015/D12: the range check lives in config, so 0 is the caller's argument error (2)."""
    root = corpus({"src/alpha.C": ALPHA})
    index_path = str(tmp_path / "bad.sqlite")
    code, out, err = _run(capsys, _build_argv(root, index_path) + ["--glue-min-chars", "0"])
    assert code == 2
    assert out == ""
    assert err.splitlines()[0].startswith("INVALID_ARGUMENT: ")
    assert "glue_min_chars" in err.splitlines()[0]
    assert not os.path.exists(index_path), "a rejected build must not leave an index behind"


def test_search_stdout_is_json_with_exactly_ten_fields(built, capsys):
    """T020a: stdout is one JSON object {rev, hits}, each hit carrying exactly ten fields.

    FR-013 added `doc` and FR-013b added `kind`; the set stays closed, so a missing or extra
    key still fails here, and `score` / `unit_id` stay out by name (FR-022, FR-013b).
    """
    _, index_path = built
    code, out, err = _run(capsys, ["search", "--index", index_path, "--query", "calcAlpha"])
    assert code == 0
    assert out.strip(), "stdout must carry the result JSON"
    payload = json.loads(out)
    assert sorted(payload) == ["hits", "rev"]
    assert isinstance(payload["rev"], str) and payload["rev"]
    assert payload["hits"]
    for hit in payload["hits"]:
        assert sorted(hit) == sorted(TEN)
        assert len(hit) == 10, f"a hit must carry exactly ten fields, got {sorted(hit)}"
        assert "kind" in hit and isinstance(hit["kind"], str) and hit["kind"]
        assert "score" not in hit and "unit_id" not in hit
        # "calcAlpha" sits on line 1, so the preview opens where the chunk starts.
        assert hit["text_start_line"] == hit["start_line"]
    assert err == ""


def test_search_without_matches_exits_zero(built, capsys):
    """T035: a query that cannot match is success with hits == [], not an error (SC-009)."""
    _, index_path = built
    code, out, _ = _run(capsys, ["search", "--index", index_path,
                                 "--query", "zzz_no_such_token_zzz"])
    assert code == 0
    assert out.strip(), "stdout must carry the result JSON"
    assert json.loads(out)["hits"] == []


def test_empty_query_exits_two_with_code_line(built, capsys):
    """T036: an empty --query maps to INVALID_ARGUMENT / 2 and writes nothing to stdout."""
    _, index_path = built
    code, out, err = _run(capsys, ["search", "--index", index_path, "--query", ""])
    assert code == 2
    assert out == ""
    assert err.splitlines()[0].startswith("INVALID_ARGUMENT: ")


def test_top_k_zero_exits_two_with_code_line(built, capsys):
    """T036: --top-k 0 is rejected by the search guard as INVALID_ARGUMENT / 2."""
    _, index_path = built
    code, out, err = _run(capsys, ["search", "--index", index_path, "--query", "calcAlpha",
                                   "--top-k", "0"])
    assert code == 2
    assert out == ""
    assert err.splitlines()[0].startswith("INVALID_ARGUMENT: ")


def test_exit_code_table_maps_the_closed_error_family():
    """T037: the module-level table holds all four codes, data-asserted, no test seam added."""
    assert cli._EXIT_CODES[InvalidArgument] is 2
    assert cli._EXIT_CODES[NotFound] is 3
    assert cli._EXIT_CODES[IndexUnavailable] is 4
    assert cli._EXIT_CODES[InternalError] is 5
    assert len(cli._EXIT_CODES) == 4


def test_not_found_from_search_exits_three(built, monkeypatch, capsys):
    """T037: NOT_FOUND is unreachable in M0 but the mapping and message format are real."""
    _, index_path = built

    def boom(*_args, **_kwargs):
        raise NotFound("no chunk at that ref")

    monkeypatch.setattr(cli, "search", boom)
    code, out, err = _run(capsys, ["search", "--index", index_path, "--query", "calcAlpha"])
    assert code == 3
    assert out == ""
    assert err.splitlines()[0] == "NOT_FOUND: no chunk at that ref"


def test_missing_index_exits_four(tmp_path, capsys):
    """T038: searching an absent index reports INDEX_UNAVAILABLE and exits 4 (FR-019)."""
    code, out, err = _run(capsys, ["search", "--index", str(tmp_path / "absent.sqlite"),
                                   "--query", "calcAlpha"])
    assert code == 4
    assert out == ""
    assert err.splitlines()[0].startswith("INDEX_UNAVAILABLE: ")


@pytest.mark.parametrize("argv", [
    ["build"],
    ["search", "--index", "x", "--query", "q", "--top-k", "abc"],
    ["search", "--index", "x", "--query", "q", "--chunk-mode", "bogus"],
    # FR-015: the glue floor is a BUILD knob; search must not accept it.
    ["search", "--index", "x", "--query", "q", "--glue-min-chars", "800"],
])
def test_argument_parsing_failures_keep_the_error_contract(argv, capsys):
    """T036/R4-7: argparse must not escape as SystemExit; it is one INVALID_ARGUMENT line."""
    code, out, err = _run(capsys, argv)
    assert code == 2
    assert out == ""
    assert err.splitlines()[0].startswith("INVALID_ARGUMENT: ")


def test_help_is_not_an_error(capsys):
    """--help exits 0 and its text stays on stdout, never behind INVALID_ARGUMENT."""
    code, out, err = _run(capsys, ["--help"])
    assert code == 0
    assert "usage: foamkb" in out
    assert "INVALID_ARGUMENT" not in err


def test_unexpected_build_failure_exits_five(corpus, tmp_path, monkeypatch, capsys):
    """T039: an unexpected exception becomes INTERNAL_ERROR / 5, never a traceback."""
    root = corpus({"src/alpha.C": ALPHA})

    def boom(_cfg):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cli, "build", boom)
    code, out, err = _run(capsys, _build_argv(root, str(tmp_path / "kb.sqlite")))
    assert code == 5
    assert out == ""
    assert err.splitlines()[0] == "INTERNAL_ERROR: disk on fire"


def test_real_subprocess_writes_pure_json_to_stdout(built):
    """T040: `python -m foamkb.cli` is pipeable — stdout parses, diagnostics stay on stderr."""
    _, index_path = built
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    proc = subprocess.run(
        [sys.executable, "-m", "foamkb.cli", "search", "--index", index_path,
         "--query", "calcAlpha"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=60, check=False)
    assert proc.returncode == 0
    assert proc.stdout.strip(), "stdout must carry the result JSON"
    payload = json.loads(proc.stdout)
    assert sorted(payload) == ["hits", "rev"]
    assert payload["hits"]
    for hit in payload["hits"]:
        assert sorted(hit) == sorted(TEN)
    assert "rev=" not in proc.stdout


def test_sc004_bad_bytes_nonzero_exit_path_in_stderr_old_index_unchanged(corpus, tmp_path, capsys):
    """T049: SC-004 as one CLI conjunct — failed rebuild leaves the old index answers intact."""
    root = corpus({"src/alpha.C": ALPHA, "src/beta.C": BETA})
    index_path = str(tmp_path / "kb.sqlite")
    assert cli.main(_build_argv(root, index_path)) == 0
    capsys.readouterr()
    code, out, _ = _run(capsys, ["search", "--index", index_path, "--query", "calcBeta"])
    assert code == 0
    assert out.strip(), "stdout must carry the result JSON"
    before = json.loads(out)
    snapshot = before["hits"]
    assert snapshot
    assert all(sorted(hit) == sorted(TEN) for hit in snapshot)

    (root / "src" / "beta.C").write_bytes(
        b"Foam::scalar Foam::beta::calcBeta()\n{\n    const scalar x = \xff\xfe;\n}\n")
    code, out, err = _run(capsys, _build_argv(root, index_path))
    assert code == 2
    assert out == ""
    first = err.splitlines()[0]
    assert first.startswith("INVALID_ARGUMENT: ")
    assert "src/beta.C" in first

    code, out, _ = _run(capsys, ["search", "--index", index_path, "--query", "calcBeta"])
    assert code == 0
    assert out.strip(), "stdout must carry the result JSON"
    after = json.loads(out)
    assert after["rev"] == before["rev"]
    assert after["hits"] == snapshot
