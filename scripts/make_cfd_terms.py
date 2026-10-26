#!/usr/bin/env python3
"""Derive the banned-term list for 007's user-language question set (T008).

Why this needs a program rather than a hand-typed list: the list has to be derived from the
corpus (so it covers the words the corpus actually uses), and it has to be validated against
questions we already know are legitimate. A hand-typed list that bans `write`, `read`, `value`
or `name` would reject exactly the kind of question 007 exists to measure — the probe's
legitimate user-language query is *"print progress line write results files"*.

So the split is: a word is banned only if it is **domain** vocabulary. Words that are ordinary
software-engineering vocabulary stay allowed even when the corpus uses them heavily, because a
layperson would use them too. The two lists below are the whole judgement, and
`--check` proves the split against known-legitimate questions.

    PYTHONPATH=src .venv/bin/python scripts/make_cfd_terms.py            # write the file
    PYTHONPATH=src .venv/bin/python scripts/make_cfd_terms.py --check    # validate the split
"""
import argparse
import collections
import os
import pathlib
import re
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_INDEX = pathlib.Path.home() / ".local" / "share" / "foamkb" / "c20_typedef.sqlite"
OUT = ROOT / "scripts" / "cfd_terms.txt"

# Ordinary software-engineering words. Allowed even though the corpus uses them heavily:
# a person who knows nothing about CFD still says "read", "write", "value", "object".
GENERIC = {
    "read", "write", "written", "list", "set", "get", "put", "type", "name", "named",
    "data", "object", "function", "method", "call", "index", "value", "values", "base",
    "source", "file", "files", "path", "dir", "default", "option", "options", "entry",
    "key", "keys", "table", "map", "lookup", "create", "new", "add", "remove", "delete",
    "update", "init", "setup", "config", "configure", "setting", "settings", "input",
    "output", "result", "results", "print", "log", "progress", "line", "lines", "text",
    "string", "number", "size", "count", "total", "start", "stop", "end", "begin",
    "check", "test", "valid", "validate", "error", "warn", "warning", "fail", "failure",
    "handle", "manager", "manager", "interface", "impl", "implementation", "class",
    "struct", "code", "program", "app", "module", "component", "system", "user",
    "manual", "auto", "automatic", "custom", "single", "double", "multi", "local",
    "global", "group", "item", "items", "element", "elements", "node", "nodes",
    "parent", "child", "root", "tree", "graph", "array", "vector_list", "queue",
    "cache", "buffer", "stream", "copy", "move", "merge", "split_str", "join",
    "load", "save", "store", "fetch", "parse", "format", "serial", "parallel",
    "version", "release", "build", "compile", "link", "flag", "flags", "arg",
    "args", "param", "params", "argument", "return", "result_", "then", "else",
    "loop", "while", "each", "every", "some", "any", "all", "none", "true", "false",
    "null", "empty", "valid_", "state", "status", "step", "steps", "next", "prev",
    "first", "last", "before", "after", "during", "same", "different", "other",
    "one", "two", "three", "several", "many", "few", "more", "less", "enough",
}

# Domain vocabulary: CFD / OpenFOAM / numerics. A layperson would not type these.
DOMAIN = {
    # things in a simulation case
    "field", "fields", "mesh", "meshes", "cell", "cells", "face", "faces", "point",
    "points", "edge", "edges", "patch", "patches", "boundary", "zone", "zones",
    "poly", "lagrangian", "cloud", "clouds", "parcel", "parcels",
    # physics / quantities
    "scalar", "vector", "tensor", "symmtensor", "sphericaltensor", "velocity",
    "pressure", "density", "viscosity", "temperature", "enthalpy", "thermo",
    "turbulence", "turbulent", "laminar", "reynolds", "viscous", "inviscid",
    "compressible", "incompressible", "buoyancy", "gravity", "mass", "momentum",
    "energy", "species", "combustion", "radiation", "multiphase", "phase", "phases",
    "surface", "surfaces", "volume", "volumetric", "flux", "fluxes", "gradient",
    "divergence", "laplacian", "convection", "diffusion", "advection", "dissipation",
    "production", "vorticity", "strain", "stress", "kinetic", "eddy", "wall",
    "walls", "roughness", "yplus", "kinematic", "dynamic",
    # numerics / solution procedure
    "discretisation", "discretization", "scheme", "schemes", "interpolation",
    "interpolate", "solver", "solvers", "residual", "residuals", "convergence",
    "converge", "converged", "iteration", "iterations", "corrector", "predictor",
    "decomposition", "decompose", "reconstruct", "matrix", "matrices", "implicit",
    "explicit", "cranknicolson", "upwind", "linearupwind", "gauss", "orthogonal",
    "nonorthogonal", "relaxation", "underrelaxation", "tolerance", "courant",
    "transient", "steadystate", "timestep", "subcycle", "subcycling",
    # OpenFOAM-specific
    "foam", "openfoam", "fvmesh", "fvsolution", "fvschemes", "controldict",
    "dictionary", "dictionaries", "dimensioned", "dimensionedscalar",
    "dimensionedvector", "geometricfield", "volscalarfield", "volvectorfield",
    "surfacefield", "pointfield", "fvpatchfield", "fvmatrix", "fvm", "fvc",
    "rts", "runtimeselection", "typedname", "word", "label",
}


def symbol_words(index):
    """Every lowercased word of length ≥3 that appears inside a symbol name."""
    conn = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
    try:
        counter = collections.Counter()
        for (leaf,) in conn.execute("SELECT DISTINCT leaf FROM symbols"):
            for word in re.findall(r"[A-Z]?[a-z]{3,}|[A-Z]{2,}", leaf):
                counter[word.lower()] += 1
        return counter
    finally:
        conn.close()


def banned_terms(index):
    """Domain words that the corpus actually uses, sorted, with their symbol frequency."""
    counter = symbol_words(index)
    terms = {w for w in DOMAIN if counter.get(w)}
    # A domain word the corpus never uses cannot mislead a query, but keeping it costs nothing
    # and protects against a corpus revision that introduces it.
    terms |= {w for w in DOMAIN if not counter.get(w) and len(w) > 4}
    return sorted(terms - GENERIC), counter


LEGITIMATE = [
    "read settings file name equals value nested sections",
    "print progress line write results files",
    "code that computes something over neighbours",
    "find where a value gets updated each step",
    "create object by name lookup table implementation",
]


def check(index):
    """The split must reject nothing in LEGITIMATE, and must reject a known-bad question."""
    terms, _ = banned_terms(index)
    banned = set(terms)
    failures = 0
    print("=== 已知合法的用户提问：不得被词表命中 ===")
    for q in LEGITIMATE:
        hits = sorted({w for w in re.findall(r"[a-z]+", q.lower()) if w in banned})
        ok = not hits
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {q!r}" + (f"  命中: {hits}" if hits else ""))
    print("\n=== 已知含领域词的提问：必须被命中 ===")
    for q in ["turbulence model for incompressible flow",
              "boundary conditions for pressure",
              "how is the mesh decomposed"]:
        hits = sorted({w for w in re.findall(r"[a-z]+", q.lower()) if w in banned})
        ok = bool(hits)
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {q!r}  命中: {hits}")
    print(f"\n词表 {len(terms)} 词，failures={failures}")
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", default=str(DEFAULT_INDEX))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    index = pathlib.Path(args.index)
    if not index.exists():
        print(f"INDEX_UNAVAILABLE: {index}", file=sys.stderr)
        return 4
    if args.check:
        return check(index)

    terms, counter = banned_terms(index)
    header = (
        "# Banned vocabulary for 007's user-language question set.\n"
        "# Generated by scripts/make_cfd_terms.py from the index's symbols table.\n"
        "#\n"
        "# A word is banned only if it is DOMAIN vocabulary. Ordinary software words (read,\n"
        "# write, value, name, object, ...) stay allowed even when the corpus uses them\n"
        "# heavily: a person who knows nothing about CFD says them too, and banning them would\n"
        "# reject exactly the questions this feature exists to measure.\n"
        "#\n"
        f"# {len(terms)} terms; symbol-frequency in parentheses where the corpus uses the word.\n"
    )
    body = "\n".join(f"{w}  # {counter[w]}" if counter.get(w) else w for w in terms)
    OUT.write_text(header + body + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(terms)} terms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
