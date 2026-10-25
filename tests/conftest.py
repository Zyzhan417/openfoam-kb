"""Test bootstrap: expose src/ on sys.path, offer a corpus factory and the shared helpers."""

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Imported after SRC is on sys.path: the package is not installed.
from foamkb import index  # noqa: E402
from foamkb.config import build_config  # noqa: E402
from foamkb.loader import SourceFile  # noqa: E402


# --- helpers shared by more than one split test module -----------------------
# They live here (not in one test file) because the per-concern splits below
# need them from two or three files each.
def _src(*lines, path="case.C"):
    """A SourceFile built straight from lines; the chunker never touches the filesystem."""
    return SourceFile(path=path, lines=list(lines), sha256="0" * 64, byte_size=0)


def _ranges(chunks):
    return [(chunk["start_line"], chunk["end_line"]) for chunk in chunks]


def _assert_non_overlapping(chunks):
    """Adjacent ranges never overlap: prev.end_line < next.start_line (FR-015, 不重叠)."""
    for prev, nxt in zip(chunks, chunks[1:]):
        assert prev["end_line"] < nxt["start_line"]


def _cfg(tmp_path, corpus, files, **kwargs):
    """A build_config over a corpus written by the `corpus` fixture (index modules)."""
    root = corpus(files)
    return build_config(root, str(tmp_path / "kb.sqlite"), ("**/*",), **kwargs)


# --- the corpus both search test modules assert on (query/rank split) --------
NUT = """Foam::scalar Foam::alpha::calcNut()
{
    const scalar alphaOne = 1.0;
    return alphaOne;
}
"""
MESH = """Foam::tmp Foam::beta::kOmegaSST() const
{
    const scalar betaTwo = 2.0;
    return betaTwo;
}
"""
DELTA = """Foam::scalar Foam::delta::deltaField()
{
    const scalar deltaFour = 4.0;
    return deltaFour;
}
"""
EPS = """Foam::scalar Foam::epsilon::epsPatch()
{
    const scalar epsilonFive = 5.0;
    return epsilonFive;
}
"""
TWIN = """Foam::scalar Foam::omega::firstTwin()
{
    return firstValue;
}

Foam::scalar Foam::omega::secondTwin()
{
    return secondValue;
}
"""
LONG = """Foam::scalar Foam::zeta::longRun()
{
    const scalar stepA = 1000.0;
    const scalar stepB = 2000.0;
    const scalar stepC = 3000.0;
    const scalar stepD = 4000.0;
    const scalar stepE = 5000.0;
    const scalar stepF = 6000.0;
    return stepA + stepB + stepC + stepD + stepE + stepF;
}
"""
UNIT = """Foam::scalar Foam::unit::gammaTurb()
{
    const scalar gammaThree = 3.0;
    return gammaThree;
}
"""

SEARCH_FILES = {
    "src/alpha/nutWall.C": NUT,
    "src/beta/mesh.C": MESH,
    "src/delta/deltaField.C": DELTA,
    "src/epsilon/epsPatch.C": EPS,
    "src/omega/twin.C": TWIN,
    "src/zeta/longFile.C": LONG,
    "tests/unit/unitMesh.C": UNIT,
}


@pytest.fixture
def corpus(tmp_path):
    """Return a factory writing {relative path: text or bytes} under one root."""

    def _write(files):
        root = tmp_path / "corpus"
        root.mkdir(exist_ok=True)
        for rel, payload in files.items():
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(payload, bytes):
                target.write_bytes(payload)
            else:
                target.write_text(payload, encoding="utf-8")
        return root

    return _write


@pytest.fixture
def search_reader(tmp_path, corpus):
    """A reader over SEARCH_FILES, shared by test_search_query and test_search_rank."""
    root = corpus(SEARCH_FILES)
    cfg = build_config(root, str(tmp_path / "kb.sqlite"), ("**/*",))
    index.build(cfg)
    return index.open_reader(cfg.index_path)
