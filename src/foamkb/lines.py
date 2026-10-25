"""Whole-file line classification: comment-only lines and section rules (FR-004, FR-007a).

Split out of `chunker` for C17: the unit rules and the new `filedoc` rules both ask "what
kind of line is this", so neither of them should own the answer alone.

The judgement is whole-file, not line-by-line: a block comment opened on one line stays
open until its `*/`, so the lines inside it are comment even when they start with `|` or
`\\*` and carry no `//` prefix at all — OpenFOAM's `Class Foo Declaration` banners are
exactly that shape. Reading a single line's first characters cannot see that state.
"""

import re

# FR-007a: a section rule, in the four shapes the corpus actually carries — `// ****`,
# `// ----`, `/** **/` and the licence-banner closer `\*------*/` (8,619 of those).
SEPARATOR = re.compile(
    r"^(//\s*[\*\-]+\s*|//\s*\*+\s*//|/\*[\*\-]+.*[\*\-]+\*/|\\\*[\*\-]+.*[\*\-]+\*/)$")
_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _literal_free(line):
    """Blank out string and char literals so comment markers inside them are not seen."""
    return _LITERAL.sub('""', line)


def comment_mask(lines):
    """Whole-file mask: True where a line carries only comment text and whitespace (FR-004)."""
    mask, in_block = [], False
    for line in lines:
        text = _literal_free(line)
        comment_only, index = True, 0
        while index < len(text):
            if in_block:
                end = text.find("*/", index)
                if end < 0:
                    break
                index, in_block = end + 2, False
            elif text.startswith("//", index):
                break
            elif text.startswith("/*", index):
                index, in_block = index + 2, True
            else:
                if not text[index].isspace():
                    comment_only = False
                index += 1
        mask.append(comment_only)
    return mask


def is_comment_span(mask, first, last):
    """True when every line of the inclusive span is comment-only."""
    return last >= first and all(mask[index] for index in range(first, last + 1))


def is_separator_only(lines, first, last):
    """True when the whole inclusive span carries nothing but section rules (FR-007a).

    Blank lines inside the span are allowed — `_gap_spans` never emits them, but a
    caller asking about a raw range must not be told a half-empty rule span is text.
    A span with no rule at all is not a separator span: it is an empty gap.
    """
    seen = False
    for line in lines[first:last + 1]:
        text = line.strip()
        if text and not SEPARATOR.match(text):
            return False
        seen = seen or bool(text)
    return seen
