"""Splitting what was read from a file into passages, structure first (MR4, MRC12).

A passage is the unit a search finds and a model is shown: about 300 tokens of
one stretch of one file, with where it came from (the page or the lines, and the
headings above it). Nothing here searches or stores; it is a pure function of an
`extract.Extracted`, so every rule is a unit test.

**Structure first, then pack.** A file is cut where its author cut it: Markdown,
Word and HTML headings give sections, and a passage never crosses a heading, so
its heading path (`Budget > GPUs`) is always true of all of its words; a PDF's
pages give page ranges, and a passage may cross from one page to the next (its
`page_start` and `page_end` say so); everything else splits on blank lines. Blocks
inside a section are packed together to about `TARGET` (1,100 characters, about
300 tokens), at most `MAX` (1,800), and a smaller tail is joined to the one before
it. **No overlap**: no word is in two passages (200 to 400 tokens without overlap
was best on precision; recall barely moves with overlap). A fenced code block and
a table are kept whole unless one alone is more than `MAX`, and then it is cut
between lines, never inside one.

**Sizes are weighed in characters, with a CJK character weighing three**, so the
same numbers give about 300 tokens of Latin text and of Chinese; what a passage
costs a model's window is `token_count.estimate`, the cautious rule every budget
here uses, kept as `tokens_est`.

**A passage records where it was taken from**, as `char_start` and `char_end` in
the text Workbench shows ("Show indexed text": `extract.render`), and, for a file
that is read as its own lines (`extract.LINE_KINDS`), `line_start` and `line_end`.
Text a PDF reader hands over is hard-wrapped, so a PDF's paragraphs are re-joined
(a word broken by a hyphen at the end of a line is made whole again: a search for
"information" must find "informa-" and "tion"), and its offsets are those of the
paragraph, not of every sentence in it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .. import token_count
from . import extract

#: What built the passages. Recorded in the index's `meta`, so a change is a
#: visible rebuild and never a silent mix of two ways of splitting.
CHUNKER = "structure-first"
CHUNKER_VERSION = 1
#: Weights (see the module): about 300 tokens, at most about 500, at least about 80.
TARGET = 1_100
MAX = 1_800
MIN = 250
#: The words between two blocks of one passage.
JOINER = "\n\n"
#: A heading path is for a person to read; a CSV's column names are one too.
MAX_PATH = 300
MAX_HEADER = 200

CJK_WEIGHT = 3


def weight(text: str) -> int:
    """How much room text takes in a passage: its characters, with each CJK
    character three, because it is about as many tokens as three Latin ones."""
    other, cjk = token_count.counts(text)
    return other + CJK_WEIGHT * cjk


@dataclass(frozen=True)
class Passage:
    """One passage, as it is stored (`index.Index.commit_reading`)."""

    ord: int
    text: str
    heading_path: str | None
    page_start: int | None
    page_end: int | None
    line_start: int | None
    line_end: int | None
    char_start: int
    char_end: int
    tokens_est: int


@dataclass
class _Block:
    """A stretch of text that is kept together if it can be: a paragraph, a fenced code
    block, a table, a CSV row. A block is never divided by packing, only by `_cut_block`
    when it alone is over `MAX`, and then between its lines."""

    text: str
    start: int
    end: int
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    #: What stood between this block and the one before it: a blank line between
    #: paragraphs, and, between two pieces of one paragraph that was cut, the line
    #: break or the space it was cut at.
    sep: str = JOINER


@dataclass
class _Section:
    """What lies under one heading, in order."""

    path: str | None
    blocks: list[_Block]


# --------------------------------------------------------------------------- #
# cutting a block that is too long
# --------------------------------------------------------------------------- #

#: What may follow the end of a sentence before the space, and what may start the next one.
_AFTER_END = r"\"')\]\N{RIGHT DOUBLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
_BEFORE_START = r"A-Z0-9\"'(\[\N{LEFT DOUBLE QUOTATION MARK}\N{LEFT SINGLE QUOTATION MARK}"
_SENTENCE = re.compile(r"(?<=[.!?])[" + _AFTER_END + r"]*\s+(?=[" + _BEFORE_START + r"])")


def _hard_split(unit: str, limit: int) -> list[str]:
    """A run with no line break and no sentence end cut into pieces of at most
    `limit`, at a space where there is one near the end of a piece."""
    pieces: list[str] = []
    rest = unit
    while weight(rest) > limit:
        # `weight` counts a CJK character as three, so cut by weight, not by index.
        cut = _prefix(rest, limit)
        space = rest.rfind(" ", 0, cut)
        if space > cut // 2:
            cut = space
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return [p for p in pieces if p]


def _prefix(text: str, limit: int) -> int:
    """How many characters of `text` weigh at most `limit` (at least one)."""
    used = 0
    for index, char in enumerate(text):
        used += weight(char)
        if used > limit:
            return max(1, index)
    return len(text)


def _units(text: str) -> tuple[list[str], str]:
    """The pieces a long text may be cut between, and what joins them: its
    lines when it has any, else its sentences."""
    if "\n" in text:
        return text.split("\n"), "\n"
    parts = _SENTENCE.split(text)
    if len(parts) > 1:
        return parts, " "
    return [text], " "


def _split_text(text: str, limit: int) -> list[str]:
    """`text` cut into pieces of at most `limit`, each as large as it can be:
    between lines or sentences, and inside one only when it alone is too long."""
    units, joiner = _units(text)
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for unit in units:
        w = weight(unit)
        if w > limit:
            if current:
                pieces.append(joiner.join(current))
                current, size = [], 0
            pieces.extend(_hard_split(unit, limit))
            continue
        extra = w + (len(joiner) if current else 0)
        if current and size + extra > limit:
            pieces.append(joiner.join(current))
            current, size = [], 0
            extra = w
        current.append(unit)
        size += extra
    if current:
        pieces.append(joiner.join(current))
    return [p for p in pieces if p.strip()]


def _cut_block(block: _Block) -> list[_Block]:
    """A block of at most `MAX` as it is; a longer one in pieces of about
    `TARGET`, each knowing which lines and which characters it came from when
    that can be told (a piece that is a stretch of the block's own text)."""
    if weight(block.text) <= MAX:
        return [block]
    out: list[_Block] = []
    cursor = 0
    for piece in _split_text(block.text, TARGET):
        found = block.text.find(piece, cursor)
        if found >= 0:
            cursor = found + len(piece)
            start, end = block.start + found, block.start + found + len(piece)
            first = last = None
            if block.line_start is not None:
                first = block.line_start + block.text.count("\n", 0, found)
                last = first + piece.count("\n")
        else:
            start, end, first, last = block.start, block.end, block.line_start, block.line_end
        sep = JOINER if not out else ("\n" if "\n" in block.text else " ")
        out.append(_Block(piece, start, end, block.page, first, last, sep))
    return out


# --------------------------------------------------------------------------- #
# the sections of a file
# --------------------------------------------------------------------------- #

_FENCE = ("```", "~~~")
#: The line under a setext heading (`Title` over `=====`), which is not text.
_UNDERLINE = re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$")
#: What `extract.render` puts between two parts (a PDF's pages).
_PART_JOIN = "\n\n"
_MARKS = (
    r"\N{BULLET}\N{MIDDLE DOT}\N{BLACK SMALL SQUARE}\N{WHITE BULLET}\N{TRIANGULAR BULLET}"
    r"\N{HYPHEN BULLET}*\N{EN DASH}\N{EM DASH}-"
)
_BULLET = re.compile(r"^\s*(?:[" + _MARKS + r"]|\(?\d{1,3}[.)]|\(?[A-Za-z][.)])\s+")
_CLOSERS = (
    "\"')]}\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}"
    "\N{RIGHT DOUBLE QUOTATION MARK}\N{RIGHT SINGLE QUOTATION MARK}"
)
_ENDS = ".!?:;"


def _heading_path(stack: list[tuple[int, str]]) -> str | None:
    if not stack:
        return None
    path = " > ".join(text for _, text in stack)
    return path if len(path) <= MAX_PATH else path[: MAX_PATH - 1].rstrip() + "…"


def _line_offsets(lines: list[str]) -> list[int]:
    offsets: list[int] = []
    at = 0
    for line in lines:
        offsets.append(at)
        at += len(line) + 1
    return offsets


def _text_sections(extracted: extract.Extracted) -> list[_Section]:
    """A file that is one text (Markdown, Word, HTML, text, CSV, code) as its
    sections: its headings, and under each its paragraphs, fences and tables."""
    text = extracted.parts[0].text
    lines = text.split("\n")
    offsets = _line_offsets(lines)
    headings = {h.line: h for h in extracted.headings}
    markdownish = extracted.kind in ("markdown", "docx", "html")
    fences = extracted.kind == "markdown"
    by_line = extracted.kind in extract.LINE_KINDS
    csv = extracted.kind == "csv"
    header = next((ln.strip() for ln in lines if ln.strip()), "") if csv else ""
    csv_path = (
        header if len(header) <= MAX_HEADER else header[: MAX_HEADER - 1].rstrip() + "…"
    ) or None

    sections = [_Section(csv_path, [])]
    stack: list[tuple[int, str]] = []
    run: list[int] = []
    kind = "text"
    fence: str | None = None
    heading_at = 0

    def flush() -> None:
        nonlocal kind
        if run:
            first, last = run[0], run[-1]
            body = "\n".join(lines[first - 1 : last])
            if body.strip():
                sections[-1].blocks.append(
                    _Block(
                        body,
                        offsets[first - 1],
                        offsets[last - 1] + len(lines[last - 1]),
                        None,
                        first if by_line else None,
                        last if by_line else None,
                    )
                )
            run.clear()
        kind = "text"

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if fence is not None:
            run.append(number)
            if stripped.startswith(fence) and len(run) > 1:
                flush()
                fence = None
            continue
        found = headings.get(number)
        if number == heading_at + 1 and _UNDERLINE.match(line):
            continue  # the underline of a heading belongs to the heading
        if found is not None:
            heading_at = number
            flush()
            while stack and stack[-1][0] >= found.level:
                stack.pop()
            stack.append((found.level, found.text))
            sections.append(_Section(_heading_path(stack), []))
            continue
        if fences and stripped.startswith(_FENCE):
            flush()
            fence, kind = stripped[:3], "fence"
            run.append(number)
            continue
        if not stripped:
            flush()
            continue
        if csv:
            flush()
            run.append(number)
            kind = "table"
            flush()
            continue
        is_row = markdownish and stripped.startswith("|")
        if is_row != (kind == "table"):
            flush()
        kind = "table" if is_row else "text"
        run.append(number)
    flush()
    return [s for s in sections if s.blocks]


def _percentile_width(lines: list[str]) -> int:
    """How wide a page's lines usually are: the 80th percentile of their lengths."""
    widths = sorted(len(line.strip()) for line in lines if line.strip())
    if not widths:
        return 0
    return widths[int(0.8 * (len(widths) - 1))]


def _join_lines(lines: list[str]) -> str:
    """Hard-wrapped lines as one paragraph: a word broken by a hyphen at the end
    of a line is made whole ("infor-" and "mation" are "information"), the rest are
    joined by a space. A word that already has hyphens in it ("state-of-the-" and
    "art") keeps the one at the break, because it is one of its own. A word that is
    hyphenated and was broken at its hyphen ("well-" and "known") cannot be told from
    one broken in the middle, and loses it: a search for "well" then misses it, the
    cost of making the common case ("infor-mation") right."""
    out = lines[0]
    for line in lines[1:]:
        if len(out) > 1 and out.endswith("-") and out[-2].isalpha() and line[:1].islower():
            compound = out.rsplit(None, 1)[-1].count("-") > 1
            out = out + line if compound else out[:-1] + line
        else:
            out = f"{out} {line}"
    return out


def _pdf_paragraphs(body: str) -> list[tuple[str, int, int]]:
    """A page's text as paragraphs, each with where it starts and ends in `body`.

    A reader gives a PDF as lines as wide as the page, with blank lines only
    where the layout has a gap, so a paragraph ends at a blank line, after a line
    that is clearly short and ends a sentence, or before a bullet or a number."""
    lines = body.split("\n")
    offsets = _line_offsets(lines)
    width = max(40, _percentile_width(lines))
    paragraphs: list[tuple[str, int, int]] = []
    current: list[int] = []

    def flush() -> None:
        if not current:
            return
        text = _join_lines([lines[i].strip() for i in current])
        first, last = current[0], current[-1]
        paragraphs.append((text, offsets[first], offsets[last] + len(lines[last])))
        current.clear()

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if current and _BULLET.match(line):
            flush()
        current.append(index)
        if len(stripped) < 0.6 * width and stripped.rstrip(_CLOSERS).endswith(tuple(_ENDS)):
            flush()
    flush()
    return paragraphs


def _pdf_sections(extracted: extract.Extracted) -> list[_Section]:
    """A PDF as one section whose blocks are its pages' paragraphs."""
    blocks: list[_Block] = []
    at = 0
    for part in extracted.parts:
        rendered = extract.render_part(part)
        body = part.text.strip()
        if body and part.page is not None:
            body_start = at + len(extract.page_marker(part.page)) + 1
            for text, start, end in _pdf_paragraphs(body):
                blocks.append(_Block(text, body_start + start, body_start + end, part.page))
        at += len(rendered) + len(_PART_JOIN)
    return [_Section(None, blocks)] if blocks else []


# --------------------------------------------------------------------------- #
# packing
# --------------------------------------------------------------------------- #


def _group_weight(group: list[_Block]) -> int:
    return sum(weight(b.text) for b in group) + sum(len(b.sep) for b in group[1:])


def _pack(blocks: list[_Block]) -> list[list[_Block]]:
    """Consecutive blocks as groups of about `TARGET`: a group is closed when the
    next block would take it past the target and it is already at least `MIN`, or
    past `MAX` whatever it is. A last group under `MIN` joins the one before it."""
    groups: list[list[_Block]] = []
    current: list[_Block] = []
    size = 0
    for block in blocks:
        w = weight(block.text)
        if current:
            grown = size + len(block.sep) + w
            if (grown > TARGET and size >= MIN) or grown > MAX:
                groups.append(current)
                current, size = [], 0
        size += w + (len(block.sep) if current else 0)
        current.append(block)
    if current:
        groups.append(current)
    if (
        len(groups) > 1
        and _group_weight(groups[-1]) < MIN
        and _group_weight(groups[-2]) + len(groups[-1][0].sep) + _group_weight(groups[-1]) <= MAX
    ):
        groups[-2].extend(groups.pop())
    return groups


def _passage(order: int, path: str | None, group: list[_Block]) -> Passage:
    text = group[0].text + "".join(b.sep + b.text for b in group[1:])
    pages = [b.page for b in group if b.page is not None]
    starts = [b.line_start for b in group if b.line_start is not None]
    ends = [b.line_end for b in group if b.line_end is not None]
    return Passage(
        ord=order,
        text=text,
        heading_path=path,
        page_start=min(pages) if pages else None,
        page_end=max(pages) if pages else None,
        line_start=min(starts) if starts else None,
        line_end=max(ends) if ends else None,
        char_start=min(b.start for b in group),
        char_end=max(b.end for b in group),
        tokens_est=token_count.estimate(text),
    )


def chunk(extracted: extract.Extracted) -> list[Passage]:
    """The passages of a file, in reading order, numbered from 0."""
    if extracted.pages is not None:
        sections = _pdf_sections(extracted)
    elif extracted.parts:
        sections = _text_sections(extracted)
    else:
        sections = []
    out: list[Passage] = []
    for section in sections:
        pieces = [piece for block in section.blocks for piece in _cut_block(block)]
        for group in _pack(pieces):
            out.append(_passage(len(out), section.path, group))
    return out
