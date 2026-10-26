"""Reading a file (MR2): what its bytes are, the text in it, and why not when there is none.

Real small files made by `tests/documents.py`: a three-page PDF with a code word
on page three, a Word file with headings, Markdown, CSV, HTML with script in it,
code, and the files that go wrong (a damaged PDF, a locked one, a scan, a file whose
type its name lies about).
"""

from __future__ import annotations

import codecs
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from eugene_plexus_workbench import files
from eugene_plexus_workbench.knowledge import extract

from . import documents as docs

ROOT = Path(__file__).resolve().parent.parent


def written(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------- #
# what a file's bytes say it is
# --------------------------------------------------------------------------- #

PNG_HEAD = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
JPEG_HEAD = b"\xff\xd8\xff\xe0" + b"\0" * 32
WAV_HEAD = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\0" * 32
MP3_HEAD = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" + b"\0" * 32


@pytest.mark.parametrize(
    ("head", "name", "media_type", "kind"),
    [
        (PNG_HEAD, "a.png", "image/png", "image"),
        (JPEG_HEAD, "a.jpg", "image/jpeg", "image"),
        (WAV_HEAD, "a.wav", "audio/wav", "audio"),
        (MP3_HEAD, "a.mp3", "audio/mpeg", "audio"),
        (b"%PDF-1.7\n%\xe2\xe3", "a.pdf", files.PDF, "pdf"),
        (b"PK\x03\x04" + b"\0" * 20, "a.docx", files.DOCX, "docx"),
        (b"# Title\n", "notes.md", "text/markdown", "markdown"),
        (b"a,b\n1,2\n", "data.csv", "text/csv", "csv"),
        (b"a\tb\n1\t2\n", "data.tsv", files.TSV, "csv"),
        (b"<html><body>x</body></html>", "page.html", "text/html", "html"),
        (b"print('x')\n", "run.py", "text/plain", "code"),
        (b"FROM python\n", "Dockerfile", "text/plain", "code"),
        (b"plain words\n", "notes.txt", "text/plain", "text"),
        (b"plain words\n", "README", "text/plain", "text"),
        (b'{"a": 1}\n', "config.json", "text/plain", "text"),
    ],
)
def test_a_files_type_is_what_its_bytes_say(
    head: bytes, name: str, media_type: str, kind: str
) -> None:
    found = files.detect(head, None, name)
    assert (found.media_type, found.kind) == (media_type, kind)
    assert found.is_document == (kind not in ("image", "audio"))


def test_the_name_picks_among_kinds_of_text_and_names_a_language() -> None:
    assert files.detect(b"x = 1\n", None, "run.py").language == "python"
    assert files.detect(b"{}\n", None, "config.json").language == "json"
    assert files.detect(b"FROM x\n", None, "Dockerfile").language == "dockerfile"
    assert files.detect(b"words\n", None, "notes.txt").language is None
    # A page that starts like HTML is HTML, whatever it is called.
    assert files.detect(b"<!DOCTYPE html><title>x</title>", None, "page.txt").kind == "html"
    # A byte-order mark is not part of what a file starts with.
    assert files.detect(codecs.BOM_UTF8 + b"<html>", None, "x.txt").kind == "html"


def test_a_type_the_name_lies_about_is_what_the_bytes_are() -> None:
    # A PDF saved as .txt is read as the PDF it is.
    found = files.detect(docs.three_pages()[:4096], "text/plain", "notes.txt")
    assert (found.media_type, found.kind) == (files.PDF, "pdf")
    # Text called .pdf by the browser is refused, as the text it is, with how it starts.
    html = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"
    with pytest.raises(files.Unsupported) as refused:
        files.detect(html, "application/pdf", "report.pdf")
    text = refused.value.message
    assert "says it is a PDF" in text and "its bytes are text" in text
    assert "<!DOCTYPE html>" in text and "Workbench reads PDF, Word" in text
    # An image that is a PDF, and a PDF that is an image.
    with pytest.raises(files.Unsupported, match="its bytes are a PDF"):
        files.detect(b"%PDF-1.7\n", "image/png", "x.png")
    with pytest.raises(files.Unsupported, match="its bytes are a PNG image"):
        files.detect(PNG_HEAD, "application/pdf", "x.pdf")
    # A Word file that is not one.
    with pytest.raises(files.Unsupported, match="its bytes are a ZIP archive"):
        files.detect(b"PK\x03\x04" + b"\0" * 8, "application/pdf", "x.pdf")
    # What the browser says of text is the name's guess, and the bytes overrule it.
    assert files.detect(b"words\n", "application/pdf-ish", "x.pdf").kind == "text"
    assert files.detect(b"a,b\n", "text/csv", "x.csv").kind == "csv"


@pytest.mark.parametrize(
    ("head", "says"),
    [
        (b"GIF89a\x01\x00", "a GIF image"),
        (b"\x1f\x8b\x08\x00" + b"\0" * 8, "a gzip archive"),
        (b"\x7fELF\x02\x01", "a program"),
        (b"{\\rtf1\\ansi hello}", "an RTF document"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8, "password-protected Office file"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "a WebP image"),
        (b"\x00\x00\x00\x18ftypisom", "an MP4"),
        (b"\x00\x01\x02\x03\x04binary\x00\x00", "looks like a binary file"),
    ],
)
def test_what_is_neither_read_nor_sent_is_refused_by_what_it_is(head: bytes, says: str) -> None:
    with pytest.raises(files.Unsupported) as refused:
        files.detect(head, None, "thing.xyz")
    assert says in refused.value.message
    # Every refusal says what Workbench does take.
    assert "PDF, Word (.docx), Markdown, text, CSV, HTML and code" in refused.value.message
    assert "PNG or JPEG images and WAV or MP3 audio" in refused.value.message


def test_a_binary_file_is_named_with_its_extension() -> None:
    with pytest.raises(files.Unsupported, match=r"binary file, not text \(\.xyz\)"):
        files.detect(b"\x00\x01\x02\x03" * 16, None, "data.xyz")


def test_utf16_text_with_a_byte_order_mark_is_text_and_not_an_mpeg_frame() -> None:
    # `FF FE` is the byte-order mark and also what an MPEG audio frame starts with.
    head = codecs.BOM_UTF16_LE + "hello there".encode("utf-16-le")
    assert files.detect(head, None, "notes.txt").kind == "text"
    assert (
        files.detect(codecs.BOM_UTF16_BE + "hello".encode("utf-16-be"), None, "a.txt").kind
        == "text"
    )
    # And a real MPEG frame is still audio.
    assert files.detect(b"\xff\xfb\x90\x00" + b"\0" * 32, None, "a.mp3").kind == "audio"


def test_text_with_the_odd_control_character_is_still_text() -> None:
    log = b"\x1b[31mred\x1b[0m line\n" * 50  # a terminal log: escapes are not binary
    assert files.detect(log, None, "build.log").kind == "text"


def test_a_zip_is_told_from_a_word_file_by_its_directory_and_never_opened(tmp_path: Path) -> None:
    word = written(tmp_path, "r.docx", docs.docx())
    other = written(tmp_path, "r.xlsx", docs.zip_without_word())
    assert files.zip_holds_document(word) is True
    assert files.zip_holds_document(other) is False
    assert (
        files.zip_holds_document(written(tmp_path, "g.docx", b"PK\x03\x04 not really a zip"))
        is False
    )
    refused = files.not_word("budget.xlsx")
    assert "ZIP archive (.xlsx), not a Word (.docx) file" in refused.message
    assert "Excel, PowerPoint" in refused.message


def test_a_zip_with_more_entries_than_are_looked_at_is_left_to_the_reader(tmp_path: Path) -> None:
    many = written(
        tmp_path,
        "many.docx",
        docs.zip_of({f"x/{n}.xml": b"<a/>" for n in range(4200)} | {"word/document.xml": b"<a/>"}),
    )
    assert files.zip_holds_document(many) is None


def test_sizes_are_said_as_a_person_reads_them() -> None:
    assert files.describe_size(300 * 1024) == "300 KiB"
    assert files.describe_size(files.MIB) == "1 MiB"
    assert files.describe_size(int(4.5 * files.MIB)) == "4.5 MiB"
    assert files.describe_size(50 * files.MIB) == "50 MiB"
    assert files.describe_size(10) == "1 KiB"


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #


def test_a_text_pdf_is_read_a_page_at_a_time_with_the_code_word_on_page_three(
    tmp_path: Path,
) -> None:
    got = extract.read(written(tmp_path, "r.pdf", docs.three_pages()), "pdf")
    assert got.pages == 3 and [p.page for p in got.parts] == [1, 2, 3]
    assert docs.CODE_WORD not in got.parts[0].text + got.parts[1].text
    assert f"The code word is {docs.CODE_WORD}." in got.parts[2].text
    assert "\r" not in "".join(p.text for p in got.parts), "line endings are line feeds"
    whole = extract.render(got.parts)
    assert whole.index("[page 1]") < whole.index("[page 2]") < whole.index("[page 3]")
    assert whole.index("[page 3]") < whole.index(docs.CODE_WORD)
    assert got.notes == [] and got.truncated is False and got.kind == "pdf"


def test_a_pdf_page_with_no_text_says_so_to_the_model_and_in_a_note(tmp_path: Path) -> None:
    got = extract.read(
        written(
            tmp_path, "r.pdf", docs.pdf(["A page with plenty of text on it.", "", "Another one."])
        ),
        "pdf",
    )
    assert got.pages == 3 and "Page 2 has no text (it may be a picture)." in got.notes
    assert "[page 2] (no text on this page)" in extract.render(got.parts)


def test_pdf_pages_with_no_text_are_said_as_ranges(tmp_path: Path) -> None:
    pages = ["Text on page one."] + [""] * 4 + ["Text on page six."] + [""] + ["Text on eight."]
    got = extract.read(written(tmp_path, "r.pdf", docs.pdf(pages)), "pdf")
    assert any("Pages 2 to 5 and 7 have no text (they may be pictures)." in n for n in got.notes)


def test_a_pdf_of_pictures_is_skipped_with_the_reason_and_its_page_count(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable) as skipped:
        extract.read(written(tmp_path, "scan.pdf", docs.pdf_pictures(3)), "pdf")
    assert skipped.value.state == "skipped" and skipped.value.code == "no_text"
    assert skipped.value.pages == 3
    assert skipped.value.message == ("Skipped: this PDF has no text, only pictures of pages.")


def test_a_locked_pdf_is_skipped_as_password_protected(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable) as skipped:
        extract.read(written(tmp_path, "locked.pdf", docs.pdf_encrypted()), "pdf")
    assert skipped.value.state == "skipped" and skipped.value.code == "password"
    assert skipped.value.message == "Skipped: this file is password-protected."


def test_a_damaged_pdf_fails_with_the_cause_each_library_observed(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable) as failed:
        extract.read(written(tmp_path, "bad.pdf", docs.pdf_damaged()), "pdf")
    assert failed.value.state == "failed" and failed.value.code == "damaged"
    # Each library's own words, once: PDFium's is not wrapped in a second "PDFium:".
    assert failed.value.message == (
        "Failed: Workbench could not read this file "
        "(PDFium: Data format error; pypdf: Stream has ended unexpectedly)."
    )


def test_a_pdf_whose_table_of_contents_is_gone_is_still_read(tmp_path: Path) -> None:
    whole = docs.three_pages()
    at = whole.index(b"xref\n")
    broken = whole[:at] + b"trailer\n<< /Size 9 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    got = extract.read(written(tmp_path, "rebuilt.pdf", broken), "pdf")
    assert got.pages == 3 and docs.CODE_WORD in extract.render(got.parts)


def test_a_pdf_that_pdfium_refuses_is_given_to_pypdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path) -> tuple[list[str], int, bool]:
        raise extract._Refused("PDFium: Data format error")

    monkeypatch.setattr(extract, "_pdfium_pages", refuse)
    got = extract.read(written(tmp_path, "r.pdf", docs.three_pages()), "pdf")
    assert got.pages == 3 and f"The code word is {docs.CODE_WORD}." in got.parts[2].text
    # And a locked one is still named as locked, whichever library is asked.
    with pytest.raises(extract.Unreadable, match="password-protected"):
        extract.read(written(tmp_path, "l.pdf", docs.pdf_encrypted()), "pdf")


@pytest.mark.parametrize(
    "data", [b"%PDF-1.4\n", b"%PDF-1.4\n" + b"\x00" * 200, b"%PDF-", b"x" * 20 + b"%PDF-1.4\n"]
)
def test_junk_that_starts_like_a_pdf_fails_by_name(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(extract.Unreadable) as failed:
        extract.read(written(tmp_path, "junk.pdf", data), "pdf")
    assert (
        failed.value.state == "failed"
        and "Workbench could not read this file" in failed.value.reason
    )


def test_a_file_that_is_not_a_pdf_is_not_read_as_one(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable, match="does not start like a PDF"):
        extract.read(written(tmp_path, "x.pdf", b"hello there"), "pdf")


def test_a_pdf_with_no_pages_is_empty_and_not_a_scan(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable) as skipped:
        extract.read(written(tmp_path, "none.pdf", docs.pdf([])), "pdf")
    assert skipped.value.code == "empty" and skipped.value.reason == "this PDF has no pages in it."


def test_a_pdf_longer_than_the_page_cap_is_read_that_far_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract, "MAX_PAGES", 4)
    got = extract.read(
        written(
            tmp_path, "long.pdf", docs.pdf([f"Page text number {n} here." for n in range(1, 8)])
        ),
        "pdf",
    )
    assert got.pages == 7 and len(got.parts) == 4 and got.truncated is True
    assert any("read the first 4 of 7 pages; the rest were not read" in n for n in got.notes)


# --------------------------------------------------------------------------- #
# Word
# --------------------------------------------------------------------------- #


def test_a_word_file_keeps_its_headings_lists_and_table_rows(tmp_path: Path) -> None:
    got = extract.read(written(tmp_path, "r.docx", docs.docx()), "docx")
    text = got.parts[0].text
    assert got.pages is None and got.kind == "docx"
    assert [(h.level, h.text) for h in got.headings] == [
        (1, "Quarterly Report"),
        (1, "Overview"),
        (2, "Details"),
        (1, "Conclusion"),
    ]
    assert "# Overview\n\nThis is the overview paragraph.\n\n## Details" in text
    assert "- First bullet\n- Second bullet" in text
    assert "1. First step\n2. Second step" in text
    assert "| Item | Qty | Price |\n| --- | --- | --- |\n| Widget | 4 | 9.99 |" in text
    assert f"The code word is {docs.CODE_WORD}." in text
    line_of = {h.text: h.line for h in got.headings}
    assert text.split("\n")[line_of["Details"] - 1] == "## Details"


def test_a_word_file_that_is_not_a_zip_fails_by_name(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable) as failed:
        extract.read(written(tmp_path, "x.docx", b"just some words"), "docx")
    assert failed.value.state == "failed" and "not a readable Word file" in failed.value.reason


def test_a_zip_with_no_word_document_in_it_fails_by_name(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable) as failed:
        extract.read(written(tmp_path, "x.docx", docs.zip_without_word()), "docx")
    assert "a ZIP file with no Word document in it" in failed.value.reason


def test_a_word_file_that_expands_past_what_is_opened_is_refused_unopened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bomb = docs.zip_of({"word/document.xml": b"<w:document/>" + b"0" * (3 * files.MIB)})
    assert len(bomb) < 20_000, (
        "it compresses a thousandfold: the file is small, what it holds is not"
    )
    monkeypatch.setattr(extract, "MAX_UNCOMPRESSED", files.MIB)
    with pytest.raises(extract.Unreadable) as refused:
        extract.read(written(tmp_path, "bomb.docx", bomb), "docx")
    assert refused.value.state == "skipped" and refused.value.code == "too_big"
    assert "expands to 3 MB when opened" in refused.value.reason
    monkeypatch.setattr(extract, "MAX_UNCOMPRESSED", 10 * files.MIB)
    monkeypatch.setattr(extract, "MAX_DOCUMENT_XML", files.MIB)
    with pytest.raises(extract.Unreadable, match="text is 3 MB when opened"):
        extract.read(written(tmp_path, "bomb2.docx", bomb), "docx")
    monkeypatch.setattr(extract, "MAX_ZIP_ENTRIES", 2)
    many = docs.zip_of({"word/document.xml": b"<a/>", "a": b"1", "b": b"2"})
    with pytest.raises(extract.Unreadable, match="holds 3 parts"):
        extract.read(written(tmp_path, "many.docx", many), "docx")


def test_an_empty_word_file_is_skipped(tmp_path: Path) -> None:
    import docx as word

    out = tmp_path / "blank.docx"
    word.Document().save(str(out))
    with pytest.raises(extract.Unreadable) as skipped:
        extract.read(out, "docx")
    assert skipped.value.state == "skipped" and skipped.value.code == "empty"


# --------------------------------------------------------------------------- #
# Markdown, CSV, text and code
# --------------------------------------------------------------------------- #


def test_markdown_is_its_own_text_with_its_headings_outside_the_fences(tmp_path: Path) -> None:
    got = extract.read(written(tmp_path, "n.md", docs.MARKDOWN), "markdown")
    assert got.parts[0].text == docs.MARKDOWN.decode().replace("\r", "")
    assert [(h.level, h.text, h.line) for h in got.headings] == [
        (1, "Field notes", 1),
        (2, "The first part", 5),
        (2, "The second part", 9),
    ], "the line in the code fence is not a heading"
    assert got.lines == 15 and docs.CODE_WORD in got.parts[0].text


def test_markdown_underlines_are_headings_too() -> None:
    found = extract.markdown_headings("Title\n=====\n\ntext\n\nSub\n---\n\n- item\n---\n")
    assert [(h.level, h.text, h.line) for h in found] == [(1, "Title", 1), (2, "Sub", 6)]


def test_csv_and_code_are_read_as_they_are(tmp_path: Path) -> None:
    csv = extract.read(written(tmp_path, "d.csv", docs.CSV), "csv")
    assert csv.parts[0].text == docs.CSV.decode() and csv.lines == 3
    code = extract.read(written(tmp_path, "a.py", docs.CODE), "code", "python")
    assert code.parts[0].text == docs.CODE.decode() and code.language == "python"
    assert code.lines == 5 and code.pages is None


def test_line_counts_do_not_count_a_last_empty_line() -> None:
    assert [extract.line_count(t) for t in ("", "a", "a\n", "a\nb", "a\nb\n", "\n")] == [
        0,
        1,
        1,
        2,
        2,
        1,
    ]


@pytest.mark.parametrize(
    ("data", "text", "note"),
    [
        (codecs.BOM_UTF8 + "héllo".encode(), "héllo", None),
        (codecs.BOM_UTF16_LE + "héllo".encode("utf-16-le"), "héllo", None),
        (codecs.BOM_UTF16_BE + "héllo".encode("utf-16-be"), "héllo", None),
        ("héllo wörld".encode(), "héllo wörld", None),
        ("héllo".encode("cp1252"), "héllo", "Windows-1252"),
    ],
)
def test_text_is_decoded_as_utf8_utf16_or_windows_1252_and_says_so(
    tmp_path: Path, data: bytes, text: str, note: str | None
) -> None:
    got = extract.read(written(tmp_path, "t.txt", data), "text")
    assert got.parts[0].text == text
    assert (note is None and got.notes == []) or (note is not None and note in got.notes[0])


def test_an_empty_text_file_is_skipped_by_name(tmp_path: Path) -> None:
    for name, data in (("e.txt", b""), ("w.txt", b"  \n\n\t "), ("z.md", b"\xef\xbb\xbf")):
        with pytest.raises(extract.Unreadable) as skipped:
            extract.read(written(tmp_path, name, data), "text")
        assert (
            skipped.value.state == "skipped"
            and skipped.value.reason == "this file has no text in it."
        )


def test_a_text_file_past_the_cap_is_read_that_far_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract, "MAX_TEXT_CHARS", 1000)
    got = extract.read(written(tmp_path, "big.txt", b"abcdefghi\n" * 500), "text")
    assert got.truncated is True and len(got.parts[0].text) == 1000
    assert "read the first 1,000 characters of this file; the rest was not read" in got.notes[0]
    monkeypatch.setattr(extract, "MAX_READ_BYTES", 401)
    cut = extract.read(written(tmp_path, "cut.txt", "é".encode() * 500), "text")
    assert cut.truncated is True and "\U0000fffd" not in cut.parts[0].text, (
        "no half a character at the cut"
    )
    assert set(cut.parts[0].text) == {"é"}


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #


def test_html_is_its_text_with_no_script_style_comment_or_hidden_words(tmp_path: Path) -> None:
    got = extract.read(written(tmp_path, "p.html", docs.HTML), "html")
    text = got.parts[0].text
    for leaked in (
        "IGNORE",
        "PWNED",
        "alert",
        "color: red",
        "SECRET",
        "document.write",
        "more script",
    ):
        assert leaked not in text, leaked
    assert text.startswith("# The Page\n\n# Welcome\n\nFirst paragraph with bold and a link.")
    assert "- one\n- two" in text
    assert "| A | B |\n| --- | --- |\n| 1 | 2 |" in text
    assert [(h.level, h.text) for h in got.headings] == [(1, "The Page"), (1, "Welcome")]


def test_html_keeps_entities_preformatted_text_and_nesting(tmp_path: Path) -> None:
    page = (
        b"<html><body><p>Fish &amp; chips&nbsp;for &lt;two&gt;.</p>"
        b"<pre>  indented\n    more</pre>"
        b"<ol><li>first<ul><li>nested</li></ul></li><li>second</li></ol>"
        b"<p aria-hidden='true'>screen reader skips me</p>"
        b"<span hidden>hidden span</span><input type='hidden' value='x'>"
        b"<h2>Heading <em>two</em></h2><p>After.</p></body></html>"
    )
    text = extract.read(written(tmp_path, "p.html", page), "html").parts[0].text
    assert "Fish & chips for <two>." in text
    assert "  indented\n    more" in text
    assert "1. first\n  - nested\n2. second" in text
    assert "screen reader" not in text and "hidden span" not in text
    assert "## Heading two\n\nAfter." in text


def test_html_that_names_its_charset_is_read_in_it(tmp_path: Path) -> None:
    page = '<html><head><meta charset="iso-8859-2"></head><body><p>zażółć</p></body></html>'
    text = extract.read(written(tmp_path, "pl.html", page.encode("cp1250")), "html").parts[0].text
    assert "zażółć" in text


def test_a_page_with_nothing_to_read_is_skipped(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable, match="this web page has no text in it"):
        extract.read(written(tmp_path, "e.html", b"<html><script>x()</script></html>"), "html")


# --------------------------------------------------------------------------- #
# making text safe to carry
# --------------------------------------------------------------------------- #


def test_control_and_invisible_characters_are_taken_out_and_counted() -> None:
    dirty = "a\x00b\x07c\u200bd\u202ee\u2066f\U000e0041g\ufeffh\x7fi\u00adj"
    cleaned, removed = extract.clean(dirty, layout=False)
    assert cleaned == "abcdefghij" and removed == 9


def test_the_joiners_a_script_needs_and_emoji_stay() -> None:
    kept = "👨\u200d👩\u200d👧 and \u200cنمی\u200cخواهم \u200e\u200f"  # noqa: RUF001
    assert extract.clean(kept, layout=False) == (kept, 0)


def test_a_lone_surrogate_becomes_the_replacement_character() -> None:
    cleaned, _ = extract.clean("ok \ud800 ok \udfff", layout=False)
    assert cleaned == "ok \ufffd ok \ufffd"
    cleaned.encode("utf-8")  # a JSON body can carry it


def test_line_endings_become_line_feeds_and_layout_text_is_tidied() -> None:
    assert extract.clean("a\r\nb\rc\x0cd\u2028e", layout=False)[0] == "a\nb\nc\nd\ne"
    assert extract.clean("a  \n\n\n\n\nb \t\n\n", layout=True)[0] == "a\n\nb"
    assert extract.clean("a  \n\n\n\nb", layout=False)[0] == "a  \n\n\n\nb", (
        "an author's spacing stays"
    )


def test_what_is_taken_out_of_a_file_is_noted(tmp_path: Path) -> None:
    got = extract.read(written(tmp_path, "t.txt", "hid\u200bden \u202etext".encode()), "text")
    assert got.parts[0].text == "hidden text" and got.notes == [
        "Removed 2 control or invisible characters."
    ]
    one = extract.read(written(tmp_path, "o.txt", "a\u200bb".encode()), "text")
    assert one.notes == ["Removed 1 control or invisible character."]


# --------------------------------------------------------------------------- #
# the shape of a reading
# --------------------------------------------------------------------------- #


def test_a_reading_survives_being_saved_and_a_bad_one_is_refused(tmp_path: Path) -> None:
    got = extract.read(written(tmp_path, "r.pdf", docs.three_pages()), "pdf")
    again = extract.Extracted.from_json(json.loads(json.dumps(got.to_json())))
    assert again == got
    md = extract.read(written(tmp_path, "n.md", docs.MARKDOWN), "markdown")
    assert extract.Extracted.from_json(md.to_json()) == md
    for bad in (None, {}, {"kind": "exe"}, {**got.to_json(), "parts": [[None]]}, "text"):
        with pytest.raises(ValueError):
            extract.Extracted.from_json(bad)


def test_a_failure_is_said_the_way_the_page_shows_it() -> None:
    failed = extract.Unreadable("damaged", "Workbench could not read this file (x).")
    assert failed.message == "Failed: Workbench could not read this file (x)."
    skipped = extract.Unreadable("empty", "this file has no text in it.", state="skipped")
    assert skipped.message == "Skipped: this file has no text in it."
    assert skipped.to_json()["state"] == "skipped"
    assert (
        extract.say("a   long\n cause " * 40, 30).endswith("…")
        and len(extract.say("x" * 999)) == 240
    )


def test_a_kind_it_does_not_know_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(extract.Unreadable, match="does not read spreadsheet files"):
        extract.read(written(tmp_path, "x.xlsx", b"x"), "spreadsheet")


# --------------------------------------------------------------------------- #
# the child process: a result file, or a cause
# --------------------------------------------------------------------------- #


def run_worker(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    base = {"PYTHONPATH": str(ROOT / "src"), "PYTHONUTF8": "1"}
    if sys.platform == "win32":
        base["SYSTEMROOT"] = "C:\\Windows"
    return subprocess.run(
        [sys.executable, "-m", "eugene_plexus_workbench.knowledge.worker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=base,
        cwd=cwd,
        timeout=120,
        check=False,
    )


def test_the_worker_writes_the_reading_whole_and_exits_clean(tmp_path: Path) -> None:
    source = written(tmp_path, "r.pdf", docs.three_pages())
    output = tmp_path / "out.json"
    done = run_worker([str(source), str(output), "pdf", "-"], tmp_path)
    assert done.returncode == 0, done.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["ok"] is True and data["pages"] == 3
    assert extract.Extracted.from_json(data).parts[2].page == 3
    assert not (tmp_path / "out.json.part").exists(), "written whole or not at all"


def test_the_worker_writes_why_not_for_a_file_that_gives_no_text(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    locked = written(tmp_path, "l.pdf", docs.pdf_encrypted())
    done = run_worker([str(locked), str(output), "pdf", "-"], tmp_path)
    assert done.returncode == 0, done.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["ok"] is False and data["code"] == "password" and data["state"] == "skipped"


def test_the_worker_names_a_library_that_raises_something_unexpected(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    done = run_worker([str(tmp_path / "missing.pdf"), str(output), "pdf", "-"], tmp_path)
    data = json.loads(output.read_text(encoding="utf-8"))
    assert done.returncode == 0 and data["ok"] is False and data["code"] == "damaged"
    assert "FileNotFoundError" in data["detail"]


def test_the_worker_is_told_how_to_be_called_and_passes_a_language(tmp_path: Path) -> None:
    assert run_worker([]).returncode == 2
    output = tmp_path / "out.json"
    source = written(tmp_path, "a.py", docs.CODE)
    done = run_worker([str(source), str(output), "code", "python"], tmp_path)
    assert done.returncode == 0
    assert json.loads(output.read_text(encoding="utf-8"))["language"] == "python"


MEMORY_PROBE = textwrap.dedent(
    """
    import sys
    from eugene_plexus_workbench.knowledge import worker
    capped = worker.limit_memory(int(sys.argv[1]) * 1024 * 1024) if sys.argv[1] != "0" else None
    try:
        blob = bytearray(600 * 1024 * 1024)
        print("allocated", capped)
    except MemoryError:
        print("refused", capped)
    """
)


def test_the_worker_names_a_file_that_needs_more_memory_than_a_reader_is_given(
    tmp_path: Path,
) -> None:
    if sys.platform == "darwin":
        pytest.skip("macOS offers no memory cap to set")
    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            """
            import sys
            from eugene_plexus_workbench.knowledge import extract, worker
            worker.MEMORY_CAP = 300 * 1024 * 1024
            extract.read = lambda *args: bytearray(600 * 1024 * 1024)
            sys.exit(worker.main([sys.argv[1], sys.argv[2], "text"]))
            """
        ),
        encoding="utf-8",
    )
    source = written(tmp_path, "any.txt", b"words\n")
    output = tmp_path / "out.json"
    env = {"PYTHONPATH": str(ROOT / "src")}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = "C:\\Windows"
    done = subprocess.run(
        [sys.executable, str(probe), str(source), str(output)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    data = json.loads(output.read_text(encoding="utf-8"))
    assert (data["ok"], data["code"], data["state"]) == (False, "memory", "failed")
    assert "needs more memory than a reader is given" in data["reason"]


def test_the_worker_holds_a_reader_to_a_memory_cap_where_the_os_offers_one(tmp_path: Path) -> None:
    script = tmp_path / "probe.py"
    script.write_text(MEMORY_PROBE, encoding="utf-8")
    env = {"PYTHONPATH": str(ROOT / "src")}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = "C:\\Windows"

    def probe(cap: str) -> str:
        done = subprocess.run(
            [sys.executable, str(script), cap],
            capture_output=True,
            text=True,
            env=env,
            cwd=tmp_path,
            timeout=60,
            check=False,
        )
        return done.stdout.strip()

    assert probe("0") == "allocated None", "with no cap the same allocation is fine"
    if sys.platform == "darwin":
        pytest.skip("macOS offers no memory cap to set")
    assert probe("300") == "refused True"
    assert probe("2000") == "allocated True"
