"""The file-level Description section as a chunk of its own (FR-011..FR-013a, C17).

C17 moved the doc boundary out of the read side. Before it, `reader.doc_slice` trimmed the
Description out of whatever chunk happened to hold it, which meant the chunk boundary
decided how much prose the reader could see: the 400-char glue (FR-007) and `max_chars`
cut 1,394 of 3,905 sections (35.7%) mid-prose, and multiValveEngine.H reached the reader
with 4 of its 194 lines. Cutting the section out of the file's line array here makes it
atomic, so no later rule can shorten it.

`carve` splits the host span around the section instead of only adding a chunk: the lines
of the section must leave the span that held them, or FR-009's "every non-blank line
exactly once" would see them twice.
"""

from .config import LICENSE_MARKERS
from .lines import is_separator_only

FILEDOC_KIND = "filedoc"
SYMBOL_KINDS = ("class", "function")
# FR-011 ②: the preprocessor directive set, and ONLY it, ends a branch-② section (C16).
# Cutting on "stripped form starts with #" also cut `#inputMode merge` and `# Patch: ...`,
# example lines that live inside `\\verbatim` blocks, not directives.
DOC_DIRECTIVES = ("#ifndef", "#define", "#include", "#ifdef", "#endif", "#else", "#elif")
# FR-011 ②: the other boundary is the standard section rule.
DOC_SECTION_RULE = "// * * *"


def _description_line(lines, include):
    """Index of the Description line that precedes `include`, else None (FR-011)."""
    for index, line in enumerate(lines):
        if line.strip().startswith("Description") and (include is None or index < include):
            return index
    return None


def _section_end(lines, offset):
    """Last line of the section starting at `offset`, per FR-011's two branches.

    ① the first line at or after `offset` carrying the block-comment close `*/`. OpenFOAM
       writes the licence, `Class` and `Description` into ONE block comment, so `*/` is
       where the section ends.
    ② no `*/` follows, so the description is not inside a block comment: the section ends
       at the last non-blank line, cut before a line whose stripped form starts with a
       DOC_DIRECTIVES entry or with the section rule. Any other `#` line is prose (C16).
    """
    for index in range(offset, len(lines)):
        if "*/" in lines[index]:
            return index
    end = offset
    for index in range(offset, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith(DOC_DIRECTIVES) or stripped.startswith(DOC_SECTION_RULE):
            break
        if stripped:
            end = index
    return end


def section(lines):
    """(first, last) 0-based of the file's Description section, or None (FR-011, FR-012).

    None means "no doc here": either no Description line before the first `#include`, or a
    section whose own text carries a licence marker — FR-012 then forbids the file a
    filedoc at all, which is stronger than emitting an empty one.

    `Description:` with a colon is a legal variant (levelSet.H, cutTriTet.H), so the
    predicate is "starts with", not equality.
    """
    include = next((index for index, line in enumerate(lines) if "#include" in line), None)
    offset = _description_line(lines, include)
    if offset is None:
        return None
    end = _section_end(lines, offset)
    while end > offset and not lines[end].strip():
        end -= 1
    if any(marker in "\n".join(lines[offset:end + 1]) for marker in LICENSE_MARKERS):
        return None
    return offset, end


def _clip(lines, spans, first, last):
    """Shorten the section so it never takes lines from a class/function span (FR-011).

    FR-011 says a candidate MUST NOT fall inside a symbol-carrying span: those spans are
    what a query looks the symbol up by, so taking their lines away deletes the symbol.
    The corpus never triggers this (all 3,905 sections are comment-only), but a file with
    no `*/` after its Description would otherwise run to the last non-blank line of the
    file and swallow the class below it. A doc cut short is recoverable; a lost unit is not.
    """
    for span in spans:
        if span[3] in SYMBOL_KINDS and span[1] >= first:
            last = min(last, span[0] - 1)
            break
    while last > first and not lines[last].strip():
        last -= 1
    if last < first:
        return None
    return first, last


def _residuals(span, first, last):
    """The parts of `span` outside the inclusive [first, last] range."""
    if span[1] < first or span[0] > last:
        return [span]
    parts = []
    if span[0] < first:
        parts.append((span[0], first - 1) + span[2:])
    if span[1] > last:
        parts.append((last + 1, span[1]) + span[2:])
    return parts


def _has_text(lines, span):
    """True when the span covers at least one non-blank line."""
    return any(lines[index].strip() for index in range(span[0], span[1] + 1))


def _join(out, part):
    """Join a rule-only fragment to the contiguous span above it (FR-007a).

    False when that is impossible — the fragment opens the file, or the span above is not
    adjacent. The caller then abandons the carve, because the alternative is a chunk that
    holds nothing but section rules, which SC-012 forbids.
    """
    if not out or out[-1][1] + 1 != part[0]:
        return False
    out[-1] = (out[-1][0], part[1]) + out[-1][2:]
    return True


def carve(lines, spans):
    """Spans with the Description section cut out as one kind='filedoc' span (FR-011).

    Order matters only in that the result stays ascending and disjoint, which FR-009
    requires. A fragment left with no non-blank line is dropped (blank lines need no
    coverage); one left with nothing but section rules joins the span above it.
    """
    found = section(lines)
    if found is None:
        return spans
    clipped = _clip(lines, spans, *found)
    if clipped is None:
        return spans
    first, last = clipped
    out = []
    for span in spans:
        for part in _residuals(span, first, last):
            if not _has_text(lines, part):
                continue
            if is_separator_only(lines, part[0], part[1]) and not _join(out, part):
                return spans
            out.append(part)
    out.append((first, last, None, FILEDOC_KIND, None))
    out.sort(key=lambda span: span[0])
    return out
