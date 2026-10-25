"""Namespace-scope type aliases: the one place `typedef` / `using` lines are read (005 FR-002).

This is **not** part of chunking: it reads a chunk's body and never moves a boundary, so
`chunker.py` stays at its line budget and a change to "how blocks are cut" cannot silently
change "which names are symbols" (plan P2 / C11).

Measured on the corpus (`/opt/openfoam13` rev 2cf4316a, 8,911 files, the four build globs):
**212 rows / 204 distinct names over 116 chunks**, of which 23 are `using` and 189 `typedef`.
Keeping every indentation instead would give 1,144 rows / 488 distinct names, including
`type` 97x, `alphaField` 56x, `rhoField` 52x — class *member* aliases, which is why the
column-0 rule below is the whole point of this module.
"""

import re

from .config import SYMBOL_KEYWORDS

# T001-1 / FR-004: `^\S` is the whole "namespace scope" rule — a line indented by even one
# space declares a class member, not a namespace-level type. It is one token on purpose: the
# column-0 rule is written once, so it can be widened in one place (`^\s*` accepts any
# indentation, and `tests/test_declarations.py` is what notices).
_SCOPE = r"^(?=\S)"
# `typedef <type> <name>;` — the head is lazy so `VolField<scalar> volScalarField` splits at
# the last whitespace-separated identifier, which is the declared name.
_TYPEDEF = re.compile(_SCOPE + r"typedef\s+(?P<head>.+?)\s+(?P<name>[A-Za-z_]\w*)\s*;")
# `using <name> = <rhs>` — the name sits *before* the `=`, so a `using X =` continued on the
# next line is still read from this line alone (measured: 1 such alias in the corpus).
_USING = re.compile(_SCOPE + r"using\s+(?P<name>[A-Za-z_]\w*)\s*=")


def namespace_scope_aliases(text):
    """`[(name, kind)]` for the aliases this chunk body declares at namespace scope.

    `kind` is `"typedef"` or `"using"`; the order is source order and a repeated name keeps
    only its first declaration, so one name never produces two rows for the same chunk.

    Four exclusions, each measured rather than guessed (FR-004):

    1. **Indented lines.** See `_SCOPE` above — this is the difference between 204 real
       type names and 488 names that include every class's `type` / `alphaField` alias.
    2. **A name in `SYMBOL_KEYWORDS`.** The intersection with the 204 measured names is
       empty, so this guard costs nothing today; without it a future `typedef int for;`
       would become a "symbol" that hijacks a control-flow query.
    3. **`(` anywhere in a `typedef` line.** Function-pointer typedefs put the name inside
       parentheses (`typedef void (*IstreamManip)(...)`), and the same test drops the
       macro-typed pair `typedef INT_SIZE(int, _t) label;` / `UINT_SIZE` — which is how 214
       alias-shaped column-0 corpus lines become the measured 212.
    4. **Anything not on one line.** A declaration split after the type
       (`typedef VolField<scalar>` / `    volScalarField;`) yields nothing rather than half
       a name.

    Known boundary (T001-6): the scan is line-anchored, so a `typedef ...;` written at
    column 0 *inside* a `/* ... */` block is collected. Measured on that corpus: 0 of the
    214 alias-shaped column-0 lines sit inside a block comment, so this costs nothing today
    and a state machine here would be a rule with no effect.
    """
    out, seen = [], set()
    for line in text.split("\n"):
        name, kind = _alias(line)
        if name is None or name in SYMBOL_KEYWORDS or name in seen:
            continue
        seen.add(name)
        out.append((name, kind))
    return out


def _alias(line):
    """`(name, kind)` declared by this one line, else `(None, None)`."""
    found = _TYPEDEF.match(line)
    if found is not None:
        # FR-004: an empty name cannot happen through the character class, but a name that
        # is only a prefix of something longer must not leak in either.
        return (None if "(" in line else found.group("name")), "typedef"
    found = _USING.match(line)
    if found is not None:
        return found.group("name"), "using"
    return None, None
