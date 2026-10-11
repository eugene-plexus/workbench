"""Splitting a file into passages (MR4, MRC12): structure first, about 300 tokens,
at most about 500, no overlap, and the heading path kept with every passage.

Pure: the input is what `extract` reads, and every claim is a number or a string.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from eugene_plexus_workbench import token_count
from eugene_plexus_workbench.knowledge import chunk, extract

from . import documents as docs


def text_file(body: str, kind: str = "text") -> extract.Extracted:
    return extract.Extracted(
        kind=kind,
        parts=[extract.Part(body)],
        lines=extract.line_count(body),
        headings=extract.markdown_headings(body) if kind in ("markdown", "docx", "html") else [],
    )


def markdown(body: str) -> extract.Extracted:
    return text_file(body, "markdown")


def pdf_of(*pages: str) -> extract.Extracted:
    return extract.Extracted(
        kind="pdf",
        parts=[extract.Part(p, page=n) for n, p in enumerate(pages, start=1)],
        pages=len(pages),
    )


def sentence(n: int) -> str:
    return f"Sentence number {n} says something plain about the harbour."


def paragraph(start: int, count: int) -> str:
    return " ".join(sentence(n) for n in range(start, start + count))


# --------------------------------------------------------------------------- #
# structure
# --------------------------------------------------------------------------- #


def test_headings_give_sections_and_every_passage_keeps_the_path_of_its_headings() -> None:
    body = (
        "# Budget\n\nIntro words.\n\n## GPUs\n\nThe second GPU costs money.\n\n"
        "### Cooling\n\nFans and pipes.\n\n## Memory\n\nRAM is cheap.\n"
    )
    got = chunk.chunk(markdown(body))
    assert [(p.heading_path, p.text) for p in got] == [
        ("Budget", "Intro words."),
        ("Budget > GPUs", "The second GPU costs money."),
        ("Budget > GPUs > Cooling", "Fans and pipes."),
        ("Budget > Memory", "RAM is cheap."),
    ]


def test_a_passage_never_crosses_a_heading_even_when_two_sections_would_fit_in_one() -> None:
    body = "# One\n\nA few words.\n\n# Two\n\nA few more words.\n"
    got = chunk.chunk(markdown(body))
    assert [p.heading_path for p in got] == ["One", "Two"]
    assert chunk.weight(got[0].text) + chunk.weight(got[1].text) < chunk.TARGET


def test_the_heading_line_is_the_path_and_is_not_repeated_in_the_text() -> None:
    got = chunk.chunk(markdown("# Field notes\n\nSome opening words.\n"))
    assert got[0].heading_path == "Field notes" and got[0].text == "Some opening words."


def test_a_setext_heading_and_its_underline_are_a_heading_and_not_text() -> None:
    got = chunk.chunk(markdown("Field notes\n===========\n\nOpening.\n\nSecond\n------\n\nMore.\n"))
    assert [(p.heading_path, p.text) for p in got] == [
        ("Field notes", "Opening."),
        ("Field notes > Second", "More."),
    ]


def test_a_file_without_headings_has_no_path_and_text_before_the_first_heading_has_none() -> None:
    assert chunk.chunk(text_file("Just words.\n\nAnd more words.\n"))[0].heading_path is None
    got = chunk.chunk(markdown("Before any heading.\n\n# Then\n\nAfter.\n"))
    assert [p.heading_path for p in got] == [None, "Then"]


def test_a_heading_inside_a_code_fence_is_not_a_heading() -> None:
    body = "# Real\n\n```\n# not a heading\nx = 1\n```\n\nAfter the fence.\n"
    got = chunk.chunk(markdown(body))
    assert {p.heading_path for p in got} == {"Real"}
    assert "# not a heading" in got[0].text


def test_a_long_heading_path_is_cut_and_says_so() -> None:
    body = "# " + "a" * 200 + "\n\n## " + "b" * 200 + "\n\nWords.\n"
    path = chunk.chunk(markdown(body))[0].heading_path
    assert path is not None and len(path) <= chunk.MAX_PATH and path.endswith("…")


def test_word_and_html_files_are_cut_by_the_headings_they_were_read_with(tmp_path: Path) -> None:
    word = tmp_path / "r.docx"
    word.write_bytes(docs.docx())
    got = chunk.chunk(extract.read(word, "docx"))
    # Word's Title and Heading 1 are both level one, so the title is a sibling, not a parent.
    assert [p.heading_path for p in got] == ["Overview", "Overview > Details", "Conclusion"]
    assert any(docs.CODE_WORD in p.text and p.line_start is None for p in got)
    page = tmp_path / "p.html"
    page.write_bytes(docs.HTML)
    html = chunk.chunk(extract.read(page, "html"))
    assert html and all(p.line_start is None and p.page_start is None for p in html)
    assert "PWNED" not in " ".join(p.text for p in html)


# --------------------------------------------------------------------------- #
# sizes, and no overlap
# --------------------------------------------------------------------------- #


def test_passages_are_about_a_thousand_characters_never_over_the_maximum() -> None:
    body = "# Notes\n\n" + "\n\n".join(paragraph(i * 6, 6) for i in range(80))
    got = chunk.chunk(markdown(body))
    weights = [chunk.weight(p.text) for p in got]
    assert max(weights) <= chunk.MAX
    inner = weights[:-1]
    assert all(w >= chunk.MIN for w in inner), weights
    assert 800 <= sum(inner) / len(inner) <= chunk.TARGET + 250, "about the target"
    assert len(got) > 20


def test_no_sentence_is_in_two_passages_and_none_is_lost() -> None:
    body = "# Notes\n\n" + "\n\n".join(paragraph(i * 6, 6) for i in range(60))
    got = chunk.chunk(markdown(body))
    seen: dict[int, int] = {}
    for p in got:
        for number in re.findall(r"Sentence number (\d+) says", p.text):
            seen[int(number)] = seen.get(int(number), 0) + 1
    assert sorted(seen) == list(range(360))
    assert set(seen.values()) == {1}, "no overlap"


def test_a_paragraph_over_the_maximum_is_cut_at_sentences_and_no_sentence_is_split() -> None:
    long = paragraph(0, 90)
    assert chunk.weight(long) > chunk.MAX
    got = chunk.chunk(text_file(long))
    assert len(got) >= 3 and all(chunk.weight(p.text) <= chunk.MAX for p in got)
    assert all(p.text.endswith("harbour.") for p in got), "cut between sentences"
    assert " ".join(p.text for p in got) == long


def test_a_run_with_no_sentence_end_is_cut_at_a_space_and_never_inside_a_word() -> None:
    words = " ".join(f"word{i:04d}" for i in range(1000))
    got = chunk.chunk(text_file(words))
    assert all(chunk.weight(p.text) <= chunk.MAX for p in got)
    rejoined = " ".join(p.text for p in got)
    assert rejoined == words


def test_a_tail_under_the_minimum_joins_the_passage_before_it() -> None:
    first = paragraph(0, 17)  # 992 characters: a passage of its own once the next would pass 1,100
    tail = ("tail " * 40).strip()  # 199: under the minimum of 250
    got = chunk.chunk(text_file(first + "\n\n" + tail))
    assert [p.text for p in got] == [first + "\n\n" + tail], "one passage, tail included"
    full = ("word " * 359).strip()  # 1,794: a block of its own, and no room for a tail
    two = chunk.chunk(text_file(full + "\n\nTail."))
    assert [p.text for p in two] == [full, "Tail."], (
        "kept apart when joining would pass the maximum"
    )


def test_a_small_block_before_a_big_one_is_not_joined_to_it_past_the_maximum() -> None:
    big = ("word " * 358).strip()  # 1,789: under the maximum alone
    got = chunk.chunk(text_file("Short intro.\n\n" + big))
    assert [p.text for p in got] == ["Short intro.", big], "joining them would be 1,803"
    assert all(chunk.weight(p.text) <= chunk.MAX for p in got)


def test_a_block_under_the_minimum_is_not_left_alone_before_a_big_one_it_fits_with() -> None:
    big = ("word " * 240).strip()  # 1,199: past the target on its own, under the maximum
    got = chunk.chunk(text_file("Short intro.\n\n" + big))
    assert [p.text for p in got] == ["Short intro.\n\n" + big], "12 characters are not a passage"
    assert chunk.weight(got[0].text) <= chunk.MAX


def test_tokens_are_estimated_by_the_cautious_rule_every_budget_uses() -> None:
    got = chunk.chunk(markdown("# T\n\n" + paragraph(0, 5) + "\n\nAn 日本語 word.\n"))
    assert all(p.tokens_est == token_count.estimate(p.text) > 0 for p in got)


def test_a_cjk_character_weighs_three_so_the_same_numbers_give_about_the_same_tokens() -> None:
    chinese = "".join(chr(0x4E00 + i % 500) for i in range(2400))
    got = chunk.chunk(text_file(chinese))
    assert all(chunk.weight(p.text) <= chunk.MAX for p in got)
    assert max(len(p.text) for p in got) <= chunk.MAX // chunk.CJK_WEIGHT
    assert "".join(p.text for p in got) == chinese


def test_passages_are_numbered_in_reading_order_from_zero() -> None:
    got = chunk.chunk(markdown("# A\n\nOne.\n\n# B\n\nTwo.\n\n# C\n\nThree.\n"))
    assert [p.ord for p in got] == [0, 1, 2]
    assert [p.char_start for p in got] == sorted(p.char_start for p in got)


def test_nothing_read_gives_no_passages() -> None:
    assert chunk.chunk(extract.Extracted(kind="text", parts=[])) == []
    assert chunk.chunk(pdf_of("", "   ")) == []


# --------------------------------------------------------------------------- #
# blocks that are kept whole
# --------------------------------------------------------------------------- #


def test_a_fenced_code_block_and_a_table_are_kept_whole() -> None:
    # A blank line inside a fence is not the end of a paragraph.
    fence = (
        "```python\n" + "\n".join(f"line_{i} = {i}" if i != 15 else "" for i in range(30)) + "\n```"
    )
    table = "\n".join(["| a | b |", "| --- | --- |", *[f"| {i} | {i * 2} |" for i in range(30)]])
    body = f"# Code\n\nBefore.\n\n{fence}\n\n# Table\n\n{table}\n"
    got = chunk.chunk(markdown(body))
    assert any(p.text.count("```") == 2 and "line_29 = 29" in p.text for p in got)
    assert any("| 29 | 58 |" in p.text and "| --- | --- |" in p.text for p in got)
    for p in got:
        assert p.text.count("```") in (0, 2), "never half a fence"


def test_a_blank_line_inside_a_fence_does_not_let_a_passage_end_inside_it() -> None:
    # Half a fence apiece, either side of a blank line: if the fence were cut at it, the
    # first half would be packed after the paragraph and the second would start a passage.
    half = "\n".join(f"value_{i:03d} = compute({i}, {i * 7})" for i in range(18))
    fence = f"```\n{half}\n\n{half}\n```"
    got = chunk.chunk(markdown(f"{paragraph(0, 5)}\n\n{fence}\n"))
    assert len(got) == 2
    for p in got:
        assert p.text.count("```") in (0, 2), "never half a fence"
    assert got[1].text == fence, "the fence is one passage, whole"


def test_a_fence_over_the_maximum_is_cut_between_lines_never_inside_one() -> None:
    lines = [f"value_{i:04d} = compute({i}, {i * 7}, {i * 13})" for i in range(120)]
    body = "```\n" + "\n".join(lines) + "\n```\n"
    got = chunk.chunk(markdown(body))
    assert len(got) > 1
    emitted = [ln for p in got for ln in p.text.split("\n") if ln.startswith("value_")]
    assert emitted == lines, "every line once, whole, in order"
    assert all(chunk.weight(p.text) <= chunk.MAX for p in got)


def test_a_csv_keeps_its_column_names_with_every_passage_and_never_splits_a_row() -> None:
    rows = ["name,qty,price", *[f"widget{i},{i},{i}.50" for i in range(400)]]
    got = chunk.chunk(text_file("\n".join(rows), "csv"))
    assert len(got) > 3
    assert {p.heading_path for p in got} == {"name,qty,price"}
    emitted = [ln for p in got for ln in p.text.split("\n") if ln]
    assert emitted == rows


# --------------------------------------------------------------------------- #
# where a passage is from
# --------------------------------------------------------------------------- #


def test_lines_are_the_locator_of_a_file_that_is_read_as_its_own_lines() -> None:
    body = "alpha\nbeta\n\ngamma\ndelta\n"
    [one] = chunk.chunk(text_file(body))
    assert (one.line_start, one.line_end) == (1, 5)
    long = "\n\n".join(f"Block {i}. " + "word " * 160 for i in range(8))
    got = chunk.chunk(text_file(long))
    assert got[0].line_start == 1
    lines = long.split("\n")
    for p in got:
        assert p.line_start is not None and p.line_end is not None
        assert lines[p.line_start - 1].startswith("Block"), "starts where it says"
        assert p.text.split("\n")[0] == lines[p.line_start - 1]
        assert p.text.split("\n")[-1] == lines[p.line_end - 1]


def test_offsets_locate_a_passage_in_the_text_that_show_indexed_text_shows() -> None:
    body = "# T\n\n" + "\n\n".join(paragraph(i * 3, 3) for i in range(20))
    ex = markdown(body)
    rendered = extract.render(ex.parts)
    for p in chunk.chunk(ex):
        assert rendered[p.char_start : p.char_end].startswith(p.text.split("\n")[0])
        assert p.text.split("\n\n")[0] in rendered[p.char_start : p.char_end]


def test_a_pdf_passage_says_its_pages_and_may_run_from_one_page_to_the_next() -> None:
    ex = pdf_of("Page one words about gardens.", "Page two words about harbours.")
    [one] = chunk.chunk(ex)
    assert (one.page_start, one.page_end) == (1, 2)
    assert one.line_start is None and one.heading_path is None


def test_a_page_of_its_own_length_gets_a_passage_of_its_own_with_its_page() -> None:
    pages = [paragraph(i * 12, 12) for i in range(4)]
    got = chunk.chunk(pdf_of(*pages))
    by_page = {(p.page_start, p.page_end) for p in got}
    assert (1, 1) in by_page and (4, 4) in by_page
    found = [p for p in got if "Sentence number 40 says" in p.text]
    assert [(p.page_start, p.page_end) for p in found] == [(4, 4)]


def test_the_code_word_on_page_three_is_in_a_passage_that_names_page_three(tmp_path: Path) -> None:
    path = tmp_path / "r.pdf"
    path.write_bytes(docs.three_pages())
    ex = extract.read(path, "pdf")
    got = chunk.chunk(ex)
    holding = [p for p in got if docs.CODE_WORD in p.text]
    assert len(holding) == 1 and holding[0].page_end == 3
    rendered = extract.render(ex.parts)
    assert docs.CODE_WORD in rendered[holding[0].char_start : holding[0].char_end]


def test_a_word_split_by_a_hyphen_at_the_end_of_a_line_is_made_whole() -> None:
    page = "The committee read the infor-\nmation and the state-of-the-\nart report twice."
    [one] = chunk.chunk(pdf_of(page))
    assert "information" in one.text and "state-of-the-art" in one.text
    assert "-\n" not in one.text


def test_a_pdfs_hard_wrapped_lines_are_one_paragraph_and_a_short_sentence_ends_one() -> None:
    wide = "x" * 20
    page = (
        f"The first paragraph runs on and on across {wide} several lines of text\n"
        f"that the reader hands over as separate lines, each as wide as the {wide}\n"
        "page lets it be, until it stops.\n"
        f"The second paragraph begins here and also runs across {wide} a few lines\n"
        f"of text so that the first one is clearly told from it by the short {wide}\n"
        "line before."
    )
    [one] = chunk.chunk(pdf_of(page))
    first, second = one.text.split("\n\n")
    assert "\n" not in first and "\n" not in second, "each paragraph is joined into one line"
    assert first.endswith("until it stops.") and second.startswith("The second paragraph")
    assert len(_blocks(page)) == 2


def _blocks(page: str) -> list[str]:
    return [t for t, _, _ in chunk._pdf_paragraphs(page)]


def test_a_bullet_starts_a_new_paragraph_on_a_page() -> None:
    page = "Things to buy:\n- a second GPU\n- more memory\n- a better fan\n"
    assert _blocks(page) == ["Things to buy:", "- a second GPU", "- more memory", "- a better fan"]


@pytest.mark.parametrize("kind", ["text", "code"])
def test_a_text_or_code_file_splits_on_blank_lines(kind: str) -> None:
    body = "def one():\n    return 1\n\n\ndef two():\n    return 2\n"
    [p] = chunk.chunk(text_file(body, kind))
    assert p.text == "def one():\n    return 1\n\ndef two():\n    return 2"
    assert (p.line_start, p.line_end) == (1, 6)
