"""Deterministic chunker: struct units first, 120-line windows as the fallback (FR-009..FR-016).

The file-level Description section is cut out as its own chunk before gluing runs; those
rules live in `filedoc` (C17), the span arithmetic for units stays here.
"""

import re

from . import filedoc
from .config import (BANNER_MAX_BLANK, CHUNK_MODES, FILEDOC_MAX_CHARS, GLUE_MIN_CHARS, MAX_CHARS,
                     MIN_CHARS, SYMBOL_KEYWORDS)
from .errors import InvalidArgument
# `is_separator_only` is also imported from here by the chunker tests, so it stays a
# module-level name; the classification itself lives in `lines` (C17 split).
from .lines import comment_mask, is_comment_span, is_separator_only

FIXED_WINDOW = 120
CLASS_LOOKAHEAD = 60
_PREPROCESSOR = ("#include", "#define")
_IDENT = re.compile(r"([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*$")
# `enum class Color` / `enum struct Tag` are scoped enums: the second keyword is not the name.
# ':' is not a terminator here: a qualified name (`class Foam::dictionary`) needs it, and a
# trailing `class Foo:` is trimmed by _plain_name's identifier match.
_DECLARED = re.compile(r"(class|struct|enum)\s+(?:class\s+|struct\s+)?([^\s;{(,]+)")
def _chars(lines, first, last):
    """Character count of the exact slice a chunk would hold, newlines included."""
    return sum(len(lines[index]) for index in range(first, last + 1)) + last - first


def _attach_separators(lines, spans):
    """FR-007a: a rule-only span joins its neighbour instead of standing alone.

    The preceding span wins; a rule at the very start of the file joins the following
    span instead. Lines are never dropped: a file that is nothing but rules keeps its
    first span as a text block so coverage stays exact (FR-009).
    """
    kept, carry = [], None
    for span in spans:
        if span[3] == "text" and span[4] is None and is_separator_only(lines, span[0], span[1]):
            if kept:
                kept[-1] = (kept[-1][0], span[1]) + kept[-1][2:]
            else:
                carry = span
            continue
        kept.append(((carry[0] if carry else span[0]), span[1]) + span[2:])
        carry = None
    # A file that is nothing but rules has no non-rule neighbour: keep one span over
    # the whole run so every line is still covered exactly once (FR-009).
    return kept or [(spans[0][0], spans[-1][1]) + spans[0][2:]]


def _glue_regions(lines, spans, glue_min_chars):
    """FR-007: neighbouring text spans glue in file order up to the threshold (FR-008).

    A unit span ends the run, so gluing can never cross one, and spans are only ever
    joined with the span that follows them, so non-adjacent regions stay apart. The
    emitted range still covers the blank lines in between: it is one exact slice.
    """
    out, buffer = [], None
    for span in spans:
        if span[3] != "text" or span[4] is not None:
            if buffer is not None:
                out.append(buffer)
                buffer = None
            out.append(span)
            continue
        buffer = span if buffer is None else (buffer[0], span[1]) + buffer[2:]
        if _chars(lines, buffer[0], buffer[1]) >= glue_min_chars:
            out.append(buffer)
            buffer = None
    if buffer is not None:
        out.append(buffer)
    return out


def _banner_start(lines, mask, head):
    """First line of the comment-only spans above `head`, each within BANNER_MAX_BLANK (FR-004).

    Blank lines inside a banner split it into several spans; absorbing one span at a time
    keeps the whole banner (and therefore `Class Foo Declaration`) in the unit.
    """
    start = head
    while start > 0:
        probe = start - 1
        while probe >= 0 and not lines[probe].strip():
            probe -= 1
        if probe < 0 or start - 1 - probe > BANNER_MAX_BLANK:
            break
        span_start = probe
        while span_start > 0 and lines[span_start - 1].strip():
            span_start -= 1
        if not is_comment_span(mask, span_start, probe):
            break
        start = span_start
    return start


def _after_template(text):
    """Tail of a line after its leading `template<...>` group, or '' when it never closes."""
    depth = 0
    for index, char in enumerate(text):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth == 0:
                return text[index + 1:].strip()
    return ""


def _plain_name(token):
    """Declared identifier with template arguments and namespace qualifiers removed (FR-005)."""
    while "<" in token:
        trimmed = re.sub(r"<[^<>]*>", "", token)
        if trimmed == token:
            token = token.split("<", 1)[0]
            break
        token = trimmed
    found = re.match(r"[A-Za-z_]\w*", token.rsplit("::", 1)[-1])
    return found.group(0) if found else None


def _declared_name(head):
    """Name declared by a class/struct/enum head line, else None (FR-002, FR-005)."""
    text = head.strip()
    if text.startswith("template"):
        text = _after_template(text)
    match = _DECLARED.match(text)
    return _plain_name(match.group(2)) if match else None


def _old_starts_unit(lines, index):
    """Unindented line ending in '{', or a '(' line whose next line is a bare '{'."""
    line = lines[index]
    paren = "(" in line and index + 1 < len(lines) and lines[index + 1].strip() == "{"
    return paren or (line == line.lstrip() and line.rstrip().endswith("{"))


def _class_head_end(lines, index):
    """Line index of the '{' that opens a class/struct/enum head, or None (FR-002).

    The head must be unindented and start with the keyword; a ';' (forward declaration)
    or a preprocessor line met before the brace vetoes it, so `class Foo;` followed by a
    function cannot swallow that function.
    """
    line = lines[index]
    if line != line.lstrip() or _declared_name(line) is None:
        return None
    if "{" in line:
        return index
    if line.rstrip().endswith(";"):
        return None
    for offset in range(1, CLASS_LOOKAHEAD + 1):
        if index + offset >= len(lines):
            return None
        text = lines[index + offset].strip()
        if text.startswith("{"):
            return index + offset
        if text.endswith(";") or text.startswith("#"):
            return None
    return None


def _starts_unit(lines, index):
    """True when this line opens a structural unit (FR-001, FR-002)."""
    stripped = lines[index].strip()
    if stripped in ("{", "{\\"):
        return False
    if stripped.startswith("namespace"):
        return False
    if _old_starts_unit(lines, index):
        return True
    return _class_head_end(lines, index) is not None


def _unit_head(lines, mask, index):
    """First line of the unit: the keyword line plus its `template` and comment banner."""
    head = index
    while head > 0:
        above = lines[head - 1].strip()
        if above.startswith("template"):
            head -= 1
        elif not above and head > 1 and lines[head - 2].strip().startswith("template"):
            head -= 2
        else:
            break
    return _banner_start(lines, mask, head)


def _brace_delta(line):
    return line.count("{") - line.count("}")


def _unit_end(lines, start):
    """Closing line of the unit, or None if the braces never balance; literals are not parsed."""
    if "{" in lines[start] and _brace_delta(lines[start]) == 0:
        return start
    depth = 0
    opened = False
    for index in range(start, len(lines)):
        if lines[index].strip().startswith(_PREPROCESSOR):
            continue
        depth += _brace_delta(lines[index])
        if depth > 0:
            opened = True
        elif opened:
            return index
    return None


def _symbol_and_kind(head):
    """Class name for a class head, else the last identifier before the first '(' (FR-005, FR-006)."""
    declared = _declared_name(head)
    if declared is not None:
        return declared, "class"
    cut = head.find("(")
    found = _IDENT.search(head[:cut]) if cut >= 0 else None
    symbol = found.group(1) if found else None
    if symbol in SYMBOL_KEYWORDS:
        return None, "text"
    if head.strip().startswith("#define"):
        return symbol, "macro"
    return symbol, "function" if symbol else "text"


def _windows(first, last, size):
    """Inclusive 0-based ranges of near-equal blocks of at most `size` lines."""
    span = last - first + 1
    if span <= size:
        return [(first, last)]
    step = -(-span // (-(-span // size)))
    return [(start, min(start + step - 1, last)) for start in range(first, last + 1, step)]


def _gap_spans(lines, first, last):
    """Cover every non-blank line outside units, dropping blank stretches (FR-015)."""
    spans, index = [], first
    while index <= last:
        if not lines[index].strip():
            index += 1
            continue
        start = index
        while index <= last and lines[index].strip():
            index += 1
        spans.append((start, index - 1, None, "text", None))
    return spans


def _span_units(lines, max_lines, glue_min_chars):
    """Ascending 0-based spans covering the file, or None when balancing fails (FR-013)."""
    spans, mask = [], comment_mask(lines)
    index = cursor = 0
    while index < len(lines):
        if not _starts_unit(lines, index):
            index += 1
            continue
        end = _unit_end(lines, index)
        if end is None:
            return None
        head = max(_unit_head(lines, mask, index), cursor)
        spans.extend(_gap_spans(lines, cursor, head - 1))
        symbol, kind = _symbol_and_kind(lines[index])
        spans.extend((first, last, symbol, kind, head + 1)
                     for first, last in _windows(head, end, max_lines))
        index = cursor = end + 1
    spans = spans + _gap_spans(lines, cursor, len(lines) - 1)
    # C17/FR-011: the Description section leaves its span before gluing runs, so the glue
    # never sees those lines and can never pull them into a neighbour; `_glue_regions` and
    # `_attach_separators` both treat a non-"text" kind as a run terminator.
    return _glue_regions(lines, filedoc.carve(lines, _attach_separators(lines, spans)),
                         glue_min_chars)


def _fixed_spans(count):
    """120-line windows from line 1, advancing without overlap (FR-014)."""
    return [(start, min(start + FIXED_WINDOW, count) - 1, None, "lines", None)
            for start in range(0, count, FIXED_WINDOW)]


def _chunk(src, first, last, symbol, kind, unit_id):
    return {"path": src.path, "start_line": first + 1, "end_line": last + 1,
            "symbol": symbol, "kind": kind, "unit_id": unit_id,
            "text": "\n".join(src.lines[first:last + 1])}


def _pack(src, span, limit, max_chars, warnings, keep_code_in_tail=True):
    """Greedy line-boundary packing under `limit`; a line over max_chars warns (FR-017)."""
    first, last, symbol, kind, unit_id = span
    chunks, index = [], first
    while index <= last:
        used, stop = 0, index
        while stop <= last:
            width = len(src.lines[stop]) + (stop > index)
            if used + width > limit:
                break
            used, stop = used + width, stop + 1
        # FR-007a: a chunk must never hold nothing but the rules absorbed from the gap,
        # so hand lines back (blank lines included) until the tail carries real code.
        while keep_code_in_tail and stop > index + 1 and is_separator_only(src.lines, stop, last):
            stop -= 1
        if stop == index:
            if len(src.lines[index]) > max_chars:
                warnings.append({"path": src.path, "reason": "line_too_long",
                                 "line": index + 1, "chars": len(src.lines[index]),
                                 "max_chars": max_chars})
            stop = index + 1
        chunks.append(_chunk(src, index, stop - 1, symbol, kind, unit_id))
        index = stop
    return chunks


def _split_chars(src, span, max_chars, warnings, merge_rules=True):
    """Split one span at line boundaries so no chunk exceeds max_chars (FR-017).

    A filedoc span is the single exception (FR-011, C17): cutting a description in half is
    what the kind exists to prevent, so it becomes exactly one chunk, bounded by
    FILEDOC_MAX_CHARS, and a section wider than that only warns.
    """
    if span[3] != filedoc.FILEDOC_KIND:
        return _pack(src, span, max_chars, max_chars, warnings, merge_rules)
    width = _chars(src.lines, span[0], span[1])
    if width > FILEDOC_MAX_CHARS:
        warnings.append({"path": src.path, "reason": "filedoc_too_wide", "line": span[0] + 1,
                         "chars": width, "max_chars": FILEDOC_MAX_CHARS})
    return [_chunk(src, span[0], span[1], span[2], span[3], span[4])]


def _absorb_stub(out, stub, lines, max_chars):
    """C18: merge `stub` backwards into the text chunk above it; True when merged.

    Only `text` may absorb it: extending a class/function span would pull the stub into a
    unit span, which FR-010 / SC-016 forbid. The merged chunk must also still fit max_chars.
    """
    if not out or out[-1]["kind"] != "text":
        return False
    above = out[-1]
    first, last = above["start_line"] - 1, stub["end_line"] - 1
    if _chars(lines, first, last) > max_chars:
        return False
    out[-1] = dict(above, end_line=stub["end_line"], text="\n".join(lines[first:last + 1]))
    return True


def _absorb_filedoc_stubs(chunks, lines, glue_min_chars, max_chars):
    """C18/FR-007: a below-floor text chunk that only exists because a filedoc follows it
    joins the text chunk above instead of standing alone as boilerplate.

    C17 made this shape common: the licence tail sits between the licence paragraph (which
    already passed the floor, so the buffer had flushed) and the Description, so it can
    neither reach the floor nor be glued forwards — 3,871 stubs of ~245 chars came out that
    way. Only neighbouring chunks ever join, and the result stays a verbatim slice (FR-009).
    """
    out = []
    for index, chunk in enumerate(chunks):
        following = chunks[index + 1] if index + 1 < len(chunks) else None
        is_stub = (following is not None and following["kind"] == filedoc.FILEDOC_KIND
                   and chunk["kind"] == "text" and _chars(
                       lines, chunk["start_line"] - 1, chunk["end_line"] - 1) < glue_min_chars)
        if is_stub and _absorb_stub(out, chunk, lines, max_chars):
            continue
        out.append(chunk)
    return out


def chunk_source(src, mode, max_lines, max_chars, glue_min_chars=GLUE_MIN_CHARS):
    """Split one SourceFile into chunks; pure, no I/O, deterministic for equal input."""
    if mode not in CHUNK_MODES:
        raise InvalidArgument(f"mode must be one of {CHUNK_MODES}: {mode!r}")
    if max_lines < 1:
        raise InvalidArgument(f"max_lines must be >= 1: {max_lines}")
    if not MIN_CHARS <= max_chars <= MAX_CHARS:
        raise InvalidArgument(f"max_chars must be within {MIN_CHARS}-{MAX_CHARS}: {max_chars}")
    lines = src.lines
    warnings = []
    if not lines or not "".join(lines).strip():
        return {"chunks": [], "warnings": warnings}
    spans = _span_units(lines, max_lines, glue_min_chars) if mode == "struct" else None
    fell_back = mode == "struct" and spans is None
    if spans is None:
        spans = _fixed_spans(len(lines))
        if fell_back:
            # No balanced units, so the spans are 120-line windows — but the file still has
            # a Description section, and FR-011 does not exempt a fallen-back file. `lines`
            # mode (the 120-line control) is left alone: it is not a structural split.
            spans = filedoc.carve(lines, spans)
    chunks = [chunk for span in spans
              for chunk in _split_chars(src, span, max_chars, warnings, mode == "struct")]
    if mode == "struct":
        # C18: the stub pass runs on the glued chunks, because its measure of "narrow" and
        # its max_chars ceiling are both about the chunk a reader will actually see.
        chunks = _absorb_filedoc_stubs(chunks, lines, glue_min_chars, max_chars)
    if fell_back:
        warnings.append({"path": src.path, "reason": "struct_fallback", "chunks": len(chunks)})
    return {"chunks": chunks, "warnings": warnings}
