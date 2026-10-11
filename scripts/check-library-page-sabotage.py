"""Prove the saved-documents page's checks are observed (MR4, the page side).

Each case breaks one thing in the page's code, and the test that is named for it
must fail. Files are restored from exact bytes kept beside the run, never from
Git. A mutation that stops the code compiling does not count as a caught defect,
and neither does a test name that runs nothing.

  python scripts/check-library-page-sabotage.py              # every unit-test case
  python scripts/check-library-page-sabotage.py --anchors    # only check each anchor is unique
  python scripts/check-library-page-sabotage.py --python     # also the cases the Python checks catch
  python scripts/check-library-page-sabotage.py --browser    # also the Chrome cases (needs WORKBENCH_PLAYWRIGHT)
  python scripts/check-library-page-sabotage.py --chrome-only    # only the Chrome cases
  python scripts/check-library-page-sabotage.py --label TEXT ...   # only the cases whose label has TEXT
  python scripts/check-library-page-sabotage.py --only PATH ...    # only the cases that break a file whose path has PATH

The Chrome cases rebuild the page for each break and run `tests/test_library_browser.py`
(or, for the top bar and the title's room, `tests/test_reading_browser.py`); the page is
rebuilt from the restored source at the end. Run it with every ambient EUGENE_PLEXUS_*
variable cleared.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SRC = "web/src/"
NPM = "npm.cmd" if os.name == "nt" else "npm"
# Vitest is run with node itself: going through npx costs ten seconds a run on Windows.
VITEST = WEB / "node_modules" / "vitest" / "vitest.mjs"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

API = SRC + "lib/api.ts"
WORDS = SRC + "lib/words.ts"
DOCS = SRC + "lib/documents.ts"
SAVED = SRC + "lib/savedDocuments.ts"
SAVED_HOOK = SRC + "lib/useSavedDocuments.ts"
CHAT_HOOK = SRC + "lib/useChatDocuments.ts"
CHAT_HOOK_DOCS = SRC + "lib/useChat.ts"
TYPES = SRC + "lib/types.ts"
EXPORT = SRC + "lib/conveniences.ts"
SCREEN = SRC + "components/Documents.tsx"
ADD = SRC + "components/AddDocuments.tsx"
ROW = SRC + "components/DocumentRow.tsx"
TRY = SRC + "components/TestQuestion.tsx"
CHIP = SRC + "components/DocumentsChip.tsx"
PASSAGES = SRC + "components/SourcePassages.tsx"
TEXT = SRC + "components/DocumentText.tsx"
CARD = SRC + "components/Attachments.tsx"
MESSAGE = SRC + "components/MessageView.tsx"
CHAT = SRC + "components/ChatView.tsx"
COMPOSER = SRC + "components/Composer.tsx"
MARKED = SRC + "components/Marked.tsx"
APP = SRC + "App.tsx"

LIBRARY_BROWSER = "tests/test_library_browser.py"
READING_BROWSER = "tests/test_reading_browser.py"
CONVENIENCES_BROWSER = "tests/test_conveniences_browser.py"
PAGE_AGREES = "tests/test_documents_page.py"
SAVED_AGREES = "tests/test_saved_page.py"

T_SAVED = "src/lib/savedDocuments.test.ts"
T_DOCS = "src/lib/documents.test.ts"
T_LIB = "src/lib/lib.test.ts"
T_API = "src/lib/api.test.ts"
T_CHAT_EVENTS = "src/lib/useChat.test.ts"
T_SAVED_HOOK = "src/lib/useSavedDocuments.test.tsx"
T_CHAT_HOOK = "src/lib/useChatDocuments.test.tsx"
T_SCREEN = "src/components/Documents.test.tsx"
T_ADD = "src/components/AddDocuments.test.tsx"
T_TRY = "src/components/TestQuestion.test.tsx"
T_CHIP = "src/components/DocumentsChip.test.tsx"
T_MESSAGE = "src/components/MessageSearch.test.tsx"
T_CHAT = "src/components/ChatViewDocuments.test.tsx"
T_COMPOSER = "src/components/ComposerSearch.test.tsx"
T_APP = "src/App.test.tsx"
T_WORDS = "src/words.test.ts"
T_CONTROLS = "src/libraryControls.test.tsx"

# (label, file, before, after, test file, a fragment of the test's name)
CASES: list[tuple[str, str, str, str, str, str]] = [
    # --- where a document stands ----------------------------------------------------
    (
        "a failed document is still being waited for",
        SAVED,
        'const FINISHED: readonly SavedState[] = ["ready", "skipped", "failed"];',
        'const FINISHED: readonly SavedState[] = ["ready", "skipped"];',
        T_SAVED,
        "is pending until it is ready, skipped or failed",
    ),
    (
        "a document that can be found by words is taken for finished",
        SAVED,
        "export const isPending = (doc: SavedDocument): boolean => !FINISHED.includes(doc.state);",
        'export const isPending = (doc: SavedDocument): boolean => doc.state === "queued" || doc.state === "reading";',
        T_SAVED,
        "is pending until it is ready, skipped or failed",
    ),
    (
        "a document being read does not say how many pages it has",
        SAVED,
        'label: doc.pages != null ? `Reading… ${count(doc.pages, "page")}` : "Reading…",',
        'label: "Reading…",',
        T_SAVED,
        "says how many pages a document that is being read has",
    ),
    (
        "a waiting document says it is being read",
        SAVED,
        'return { state, tone: "quiet", label: "Waiting to be read" };',
        'return { state, tone: "quiet", label: "Reading…" };',
        T_SAVED,
        "says each state in the design's words",
    ),
    (
        "a ready document says it is ready for words",
        SAVED,
        'return { state, tone: "ok", label: "Ready" };',
        'return { state, tone: "ok", label: "Ready for words" };',
        T_SAVED,
        "says each state in the design's words",
    ),
    (
        "a state with no words is called ready",
        SAVED,
        'return { state, tone: "ok", label: "Ready for words" };',
        'return { state, tone: "ok", label: "Ready" };',
        T_SAVED,
        "is ready for words in a state it has no words for yet",
    ),
    (
        "a skipped document is said in a tone that is not a warning",
        SAVED,
        'return { state, tone: "warn", label: endedWords(doc) };',
        'return { state, tone: "ok", label: endedWords(doc) };',
        T_SAVED,
        "gives the server's own sentence for a document that was skipped or failed",
    ),
    (
        "a failed document is said in a tone that is not an error",
        SAVED,
        'return { state, tone: "error", label: endedWords(doc) };',
        'return { state, tone: "warn", label: endedWords(doc) };',
        T_SAVED,
        "gives the server's own sentence for a document that was skipped or failed",
    ),
    (
        "a sentence that came with its word is given the word again",
        SAVED,
        'return new RegExp(`^${word}\\\\b`, "i").test(said) ? said : `${word}: ${said}`;',
        "return `${word}: ${said}`;",
        T_SAVED,
        "puts the word in front of a sentence that came without it",
    ),
    (
        "a sentence that came without its word is shown without it",
        SAVED,
        'return new RegExp(`^${word}\\\\b`, "i").test(said) ? said : `${word}: ${said}`;',
        "return said;",
        T_SAVED,
        "puts the word in front of a sentence that came without it",
    ),
    (
        "a document that ended with no reason says nothing",
        SAVED,
        '      ? "Skipped: Workbench did not read this file."',
        '      ? ""',
        T_SAVED,
        "is never silent about a document that ended without a reason",
    ),
    # --- sizes, dates and what a document is ---------------------------------------
    (
        "a size over ten MiB keeps its decimal",
        SAVED,
        "value >= 10 || Number.isInteger(value) ? Math.round(value) : value.toFixed(1)",
        "Number.isInteger(value) ? Math.round(value) : value.toFixed(1)",
        T_SAVED,
        "says a size as the server does",
    ),
    (
        "a small size is not at least one KiB",
        SAVED,
        "return `${Math.max(1, Math.round(bytes / 1024))} KiB`;",
        "return `${Math.round(bytes / 1024)} KiB`;",
        T_SAVED,
        "says a size as the server does",
    ),
    (
        "a document added in another year is said without it",
        SAVED,
        '...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),',
        "...{},",
        T_SAVED,
        "says the day a document was added",
    ),
    (
        "a document's passages are counted before it can be found",
        SAVED,
        'if (doc.searchable && doc.passages > 0) parts.push(count(doc.passages, "passage"));',
        'if (doc.passages > 0) parts.push(count(doc.passages, "passage"));',
        T_SAVED,
        "says a language only for code and text",
    ),
    (
        "a document's lines are said beside its pages",
        SAVED,
        'else if (doc.lines != null) parts.push(count(doc.lines, "line"));',
        'if (doc.lines != null) parts.push(count(doc.lines, "line"));',
        T_SAVED,
        "says the pages of a document that has them, and its lines only when it has no pages",
    ),
    (
        "a language is said for every kind of file",
        SAVED,
        'language && (doc.kind === "code" || doc.kind === "text")',
        "language",
        T_SAVED,
        "says a language only for code and text",
    ),
    (
        "the totals leave out the bytes",
        SAVED,
        "    sizeWords(totals.bytes),\n  ].join",
        "  ].join",
        T_SAVED,
        "count documents, passages and bytes",
    ),
    (
        "the caps state the passages kept as the limit",
        SAVED,
        "`Workbench keeps up to ${limits.maxPassages.toLocaleString()} passages for you (${totals.passagesKept.toLocaleString()} used).`",
        "`Workbench keeps up to ${totals.passagesKept.toLocaleString()} passages for you (${limits.maxPassages.toLocaleString()} used).`",
        T_SAVED,
        "state every cap, with the numbers the server gave",
    ),
    (
        "the caps do not say how long a file may take",
        SAVED,
        '    `It stops reading a file that takes longer than ${count(limits.readTimeoutSeconds, "second")}.`,\n',
        "",
        T_SAVED,
        "state every cap, with the numbers the server gave",
    ),
    (
        "the new chats words say the opposite",
        SAVED,
        "return prefs.effective.newChats\n    ? ",
        "return !prefs.effective.newChats\n    ? ",
        T_SAVED,
        "says what a new chat starts with, as it is in effect",
    ),
    (
        "the choices about outside models are in another order",
        SAVED,
        '  {\n    value: "ask",\n    label: "Ask me in each chat",',
        '  {\n    value: "never",\n    label: "Ask me in each chat",',
        T_SAVED,
        "offers the three choices about models outside their machines",
    ),
    # --- adding files ----------------------------------------------------------------
    (
        "an added file says it is waiting",
        SAVED,
        'return upload.message ?? "Added. Workbench is reading it.";',
        'return "Waiting";',
        T_SAVED,
        "says each row's words",
    ),
    (
        "a file going says no percent",
        SAVED,
        "return `Adding… ${Math.round(upload.progress * 100)}%`;",
        'return "Adding…";',
        T_SAVED,
        "says each row's words",
    ),
    (
        "a refusal is not said in the server's words",
        SAVED,
        'return upload.message ?? "Not added.";',
        'return "Not added.";',
        T_SAVED,
        "says each row's words",
    ),
    (
        "the summary says one file more than it has gone",
        SAVED,
        "return `Adding ${uploads.length - left + 1} of ${uploads.length}…`;",
        "return `Adding ${uploads.length - left} of ${uploads.length}…`;",
        T_SAVED,
        "says where it is while files go",
    ),
    (
        "the summary says a file that was not added was",
        SAVED,
        'refused === 1 ? "was" : "were"',
        '"was"',
        T_SAVED,
        "says where it is while files go",
    ),
    (
        "the summary says nothing was added when nothing was",
        SAVED,
        "if (added > 0 || refused === 0) said.push(",
        "if (true) said.push(",
        T_SAVED,
        "says where it is while files go",
    ),
    (
        "a refusal names the first file's words instead of the file's own",
        SAVED,
        "refused?.find((r) => r.name === name)?.message ??\n    refused?.[0]?.message ??",
        "refused?.[0]?.message ??",
        T_SAVED,
        "reads a refusal off an ApiError",
    ),
    (
        "a refusal does not fall back to the error's words",
        SAVED,
        '(typeof error?.message === "string" ? error.message : String(failure));',
        '"Not added.";',
        T_SAVED,
        "reads a refusal off an ApiError",
    ),
    (
        "a refusal loses its status",
        SAVED,
        'return { message, status: typeof error?.status === "number" ? error.status : 0 };',
        "return { message, status: 0 };",
        T_SAVED,
        "reads a refusal off an ApiError",
    ),
    # --- a search that cannot be used -------------------------------------------------
    (
        "any failure is taken for a search that cannot be used",
        SAVED,
        "if (!error || error.status !== 503) return null;",
        "if (!error) return null;",
        T_SAVED,
        "is only a 503, with the server's words",
    ),
    (
        "a damaged search cannot be made again",
        SAVED,
        "canRebuild: detail.canRebuild === true,",
        "canRebuild: false,",
        T_SAVED,
        "is only a 503, with the server's words",
    ),
    (
        "a search's code is lost",
        SAVED,
        'code: typeof detail.code === "string" ? detail.code : null,',
        "code: null,",
        T_SAVED,
        "is only a 503, with the server's words",
    ),
    (
        "a 503 with no words has no words",
        SAVED,
        'message: typeof detail.message === "string" ? detail.message : String(error.message ?? ""),',
        'message: typeof detail.message === "string" ? detail.message : "",',
        T_SAVED,
        "falls back to the error's own words for a 503",
    ),
    (
        "a rebuild does not say the files it could not use",
        SAVED,
        "  if (made.left > 0) {",
        "  if (false) {",
        T_SAVED,
        "says what a rebuild came to",
    ),
    (
        "a rebuild of nothing says it is making the search",
        SAVED,
        '    made.documents === 0\n      ? "Workbench found no files to make the search from."',
        '    made.documents === -1\n      ? "Workbench found no files to make the search from."',
        T_SAVED,
        "says what a rebuild came to",
    ),
    # --- where in a file, and which words ----------------------------------------------
    (
        "pages are not said as a range",
        SAVED,
        "return pageEnd != null && pageEnd !== pageStart\n      ? `pages ${pageStart} to ${pageEnd}`\n      : `page ${pageStart}`;",
        "return `page ${pageStart}`;",
        T_SAVED,
        "says a page, pages, a line or lines",
    ),
    (
        "lines come before pages",
        SAVED,
        "  if (pageStart != null) {\n    return pageEnd",
        "  if (pageStart != null && lineStart == null) {\n    return pageEnd",
        T_SAVED,
        "says a page, pages, a line or lines",
    ),
    (
        "a line is not grouped",
        SAVED,
        "? `lines ${lineStart.toLocaleString()} to ${lineEnd.toLocaleString()}`",
        "? `lines ${lineStart} to ${lineEnd}`",
        T_SAVED,
        "says a page, pages, a line or lines",
    ),
    (
        "a passage is not named by its heading",
        SAVED,
        "    placeWords(source.page_start, source.page_end, source.line_start, source.line_end),\n    source.heading,\n  ]);",
        "    placeWords(source.page_start, source.page_end, source.line_start, source.line_end),\n  ]);",
        T_SAVED,
        "names a passage by its file, its place and its heading",
    ),
    (
        "a passage a question found is not named by its place",
        SAVED,
        "    placeWords(hit.pageStart, hit.pageEnd, hit.lineStart, hit.lineEnd),\n",
        "",
        T_SAVED,
        "names a passage that a question tried found the same way",
    ),
    (
        "a phrase a passage was found by is not quoted",
        SAVED,
        "phrases.map((phrase) => `“${phrase}”`)",
        "phrases",
        T_SAVED,
        "lists the words a passage was found by",
    ),
    (
        "a word inside another word is marked",
        SAVED,
        'const pattern = new RegExp(`(?<!${edge})(?:${list.map(escaped).join("|")})(?!${edge})`, "giu");',
        'const pattern = new RegExp(`(?:${list.map(escaped).join("|")})`, "giu");',
        T_SAVED,
        "marks those words where they are",
    ),
    (
        "a word is marked only in the case it was asked in",
        SAVED,
        '`(?<!${edge})(?:${list.map(escaped).join("|")})(?!${edge})`, "giu");',
        '`(?<!${edge})(?:${list.map(escaped).join("|")})(?!${edge})`, "gu");',
        T_SAVED,
        "marks those words where they are",
    ),
    (
        "the longer of two words is not marked first",
        SAVED,
        "    (a, b) => b.length - a.length,\n",
        "    (a, b) => a.length - b.length,\n",
        T_SAVED,
        "marks those words where they are",
    ),
    (
        "a word's own marks are taken for a pattern",
        SAVED,
        'const escaped = (word: string): string => word.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&");',
        "const escaped = (word: string): string => word;",
        T_SAVED,
        "does not take a word with a regular expression's own marks for one",
    ),
    (
        "a text with nothing to mark is cut anyway",
        SAVED,
        "  if (list.length === 0 || !text) return [{ text, hit: false }];",
        "  if (!text) return [{ text, hit: false }];",
        T_SAVED,
        "is the whole text, unmarked",
    ),
    # --- Test a question ----------------------------------------------------------------
    (
        "a found question does not say no model was asked",
        SAVED,
        'about ${count(tokens, "token")}. No model was asked.`;',
        'about ${count(tokens, "token")}.`;',
        T_SAVED,
        "says what a found question would send",
    ),
    (
        "a found question counts the passages and not the tokens",
        SAVED,
        "const tokens = result.passages.reduce((sum, hit) => sum + hit.tokens, 0);",
        "const tokens = result.passages.length;",
        T_SAVED,
        "says what a found question would send",
    ),
    (
        "nothing matched is not said as a normal result",
        SAVED,
        '"Nothing in your documents matched, so Workbench would send no passages. No model was asked."',
        '"Nothing in your documents matched."',
        T_SAVED,
        "says nothing matched is a normal result",
    ),
    (
        "a question with no words is not told to try some",
        SAVED,
        ' Try a few of the words the file uses."',
        '"',
        T_SAVED,
        "says nothing matched is a normal result",
    ),
    (
        "no documents does not say some are being read",
        SAVED,
        "      return searched.waiting > 0\n",
        "      return false\n",
        T_SAVED,
        "says nothing matched is a normal result",
    ),
    (
        "the search does not say what is still being read",
        SAVED,
        '  if (waiting > 0) {\n    said.push(\n      `${count(waiting, "document")} ${waiting === 1',
        '  if (false) {\n    said.push(\n      `${count(waiting, "document")} ${waiting === 1',
        T_SAVED,
        "says how much was searched",
    ),
    (
        "the words searched for are not said",
        SAVED,
        "  return said ? `Searched for: ${said}` : null;",
        "  return null;",
        T_SAVED,
        "says the words it searched for",
    ),
    # --- a chat's chip ---------------------------------------------------------------------
    (
        "the chip says Documents where it could not read",
        SAVED,
        '? { label: "Documents: not available", short: "", tone: "warn" }',
        '? { label: "Documents", short: "", tone: "quiet" }',
        T_SAVED,
        "says plain Documents before it has read anything",
    ),
    (
        "the chip counts documents it is not searching",
        SAVED,
        "(view.searching.library ? library.documents : 0) + (view.searching.chat ? chat.documents : 0);",
        "library.documents + chat.documents;",
        T_SAVED,
        "counts this chat's own searched files with the person's documents",
    ),
    (
        "the chip does not count this chat's own files",
        SAVED,
        "(view.searching.library ? library.documents : 0) + (view.searching.chat ? chat.documents : 0);",
        "(view.searching.library ? library.documents : 0);",
        T_SAVED,
        "counts this chat's own searched files with the person's documents",
    ),
    (
        "the chip does not group a count",
        SAVED,
        "      label: `Documents (${here.toLocaleString()})`,",
        "      label: `Documents (${here})`,",
        T_SAVED,
        "says how many documents the next message searches",
    ),
    (
        "the chip says on when it has nothing to search",
        SAVED,
        'label: waiting > 0 ? "Documents: being read" : "Documents: none yet",',
        "label: `Documents (${here})`,",
        T_SAVED,
        "says when it is on and there is nothing to search yet",
    ),
    (
        "the chip does not warn for a model outside the machines",
        SAVED,
        '        short: "",\n        tone: "warn",\n      };\n    case "window_too_small":',
        '        short: "",\n        tone: "quiet",\n      };\n    case "window_too_small":',
        T_SAVED,
        "for outside_model says",
    ),
    (
        "the chip calls a model it cannot place outside",
        SAVED,
        '          view.model?.locality === "unknown"',
        '          view.model?.locality === "never"',
        T_SAVED,
        "does not call a model it cannot place outside",
    ),
    (
        "the chip says a small window is the owner's off",
        SAVED,
        '      return { label: "Documents not sent: small window", short: "", tone: "warn" };',
        '      return { label: "Documents off", short: "Off", tone: "quiet" };',
        T_SAVED,
        "for window_too_small says",
    ),
    (
        "the chip says off for a chat whose documents are on",
        SAVED,
        '    return view.switch === "on"\n      ? { label: "Documents on", short: "On", tone: "quiet" }',
        '    return view.switch === "off"\n      ? { label: "Documents on", short: "On", tone: "quiet" }',
        T_SAVED,
        "says only whether Documents was on",
    ),
    (
        "the owner is told which passages in a chat",
        SAVED,
        '  return `Documents are ${how} in this chat${view.documentsOnly ? ", and it answers only from them" : ""}.`;',
        "  return `Documents are ${how} in this chat.`;",
        T_SAVED,
        "says only whether Documents was on",
    ),
    (
        "the drawer does not say what is still being read",
        SAVED,
        '${library.waiting > 0 ? `, ${library.waiting.toLocaleString()} more being read` : ""}',
        '${""}',
        T_SAVED,
        "counts the person's documents, and this chat's own files",
    ),
    (
        "the drawer says nothing of this chat's own files",
        SAVED,
        "  if (chat.documents > 0 || chat.waiting > 0) {",
        "  if (false) {",
        T_SAVED,
        "counts the person's documents, and this chat's own files",
    ),
    (
        "the room passages may take leaves the window out",
        SAVED,
        "of this model's ${budget.window.toLocaleString()}-token window, and fewer",
        "of this model's window, and fewer",
        T_SAVED,
        "says the room passages may take",
    ),
    # --- the hint ------------------------------------------------------------------------------
    (
        "the hint is for a chat whose documents are on",
        SAVED,
        '  if (view.switch === "on" || view.reasonCode === "install_off") return null;',
        '  if (view.reasonCode === "install_off") return null;',
        T_SAVED,
        "is not there while Documents are on",
    ),
    (
        "the hint is for documents the owner turned off",
        SAVED,
        '  if (view.switch === "on" || view.reasonCode === "install_off") return null;',
        '  if (view.switch === "on") return null;',
        T_SAVED,
        "is not there while Documents are on",
    ),
    (
        "the hint is for a person with nothing to search",
        SAVED,
        "  return view.counts.library.documents > 0 ? view.counts.library.documents : null;",
        "  return view.counts.library.documents;",
        T_SAVED,
        "is not there while Documents are on",
    ),
    (
        "the hint is hidden for everyone in the browser",
        SAVED,
        'window.localStorage.getItem(`${HINT_KEY}:${person}`) === "hidden"',
        'window.localStorage.getItem(HINT_KEY) === "hidden"',
        T_SAVED,
        "is hidden for good in this browser by the person who hid it",
    ),
    (
        "the hint is lost when the browser will not keep it, with an error",
        SAVED,
        '    window.localStorage.setItem(`${HINT_KEY}:${person}`, "hidden");\n  } catch {\n    // Remembering that it was hidden is a convenience.\n  }',
        '    window.localStorage.setItem(`${HINT_KEY}:${person}`, "hidden");\n  } catch (error) {\n    throw error;\n  }',
        T_SAVED,
        "is not lost to a browser that will not keep it",
    ),
    (
        "the hint says the singular in the plural",
        SAVED,
        '`Your ${documents === 1 ? "document is" : `${documents.toLocaleString()} documents are`} off here.`',
        "`Your ${documents.toLocaleString()} documents are off here.`",
        T_SAVED,
        "is for a person who has documents to search and a chat that has Documents off",
    ),
    # --- a view that is not a chat's documents ---------------------------------------------------
    (
        "a view of notes is taken for a chat's documents",
        SAVED,
        '  if (view.switch !== "on" && view.switch !== "off") return null;\n',
        "",
        T_SAVED,
        "is not taken for one",
    ),
    (
        "a view with no counts is taken for a chat's documents",
        SAVED,
        '  return counts && typeof counts === "object" && counts.library && counts.chat\n    ? (view as ChatDocumentsView)\n    : null;',
        "  return view as ChatDocumentsView;",
        T_SAVED,
        "is not taken for one",
    ),
    # --- under an answer ----------------------------------------------------------------------
    (
        "what was sent is said as what was used",
        SAVED,
        'return `Sent to the model: ${count(passages, "passage")} from ${count(documents, "document")}`;',
        'return `Used by the model: ${count(passages, "passage")} from ${count(documents, "document")}`;',
        T_SAVED,
        "says what was sent, and never what the model used",
    ),
    (
        "a way of finding it does not know is claimed",
        SAVED,
        '  if (mode === "meaning and words") return "Found by meaning and words.";\n  return null;',
        '  if (mode === "meaning and words") return "Found by meaning and words.";\n  return "Found by words only.";',
        T_SAVED,
        "says how they were found only where it knows",
    ),
    (
        "meaning is claimed for a words only search",
        SAVED,
        '  if (mode === "words") return "Found by words only.";',
        '  if (mode === "words") return "Found by meaning and words.";',
        T_SAVED,
        "says how they were found only where it knows",
    ),
    (
        "what did not fit is said when nothing did not",
        SAVED,
        "  return didNotFit > 0\n    ?",
        "  return didNotFit >= 0\n    ?",
        T_SAVED,
        "says what matched and did not fit the room",
    ),
    (
        "nothing matched is said with no sentence of its own",
        SAVED,
        '    case "nothing_matched":\n      return "Nothing in your documents matched.";',
        '    case "nothing_matched":\n      return "";',
        T_SAVED,
        "says nothing matched in the server's words",
    ),
    (
        "a failed search is said with no sentence of its own",
        SAVED,
        '      return "Searching your documents failed, so this answer used none. See Workbench\'s log.";',
        '      return "";',
        T_SAVED,
        "says nothing matched in the server's words",
    ),
    (
        "any failed search can be mended by rebuilding",
        SAVED,
        '  record.status === "failed" && record.code === "index_damaged";',
        '  record.status === "failed";',
        T_SAVED,
        "can be mended in Documents only when the index is damaged",
    ),
    (
        "a search that was not run can be mended by rebuilding",
        SAVED,
        '  record.status === "failed" && record.code === "index_damaged";',
        '  record.code === "index_damaged";',
        T_SAVED,
        "can be mended in Documents only when the index is damaged",
    ),
    (
        "a hint is taken from a note that is not about why",
        SAVED,
        "const noted = record.notes.find((note) => note.code === record.code && note.hint);",
        "const noted = record.notes.find((note) => note.hint);",
        T_SAVED,
        "keeps the hint the server gave for why documents were held back",
    ),
    (
        "an exported passage does not say it was cited",
        SAVED,
        '${source.cited ? " (cited in the answer)" : ""}',
        '${""}',
        T_SAVED,
        "is a line in a chat exported as Markdown",
    ),
    (
        "an exported passage the owner may not read names the document",
        SAVED,
        '  if (isHidden(source)) return "Passage sent to the model: a document";',
        '  if (isHidden(source)) return "Passage sent to the model: a passage";',
        T_MESSAGE,
        "says a passage the owner may not read is a document",
    ),
    (
        "a passage is taken for hidden when it only says so",
        SAVED,
        '  "redacted" in source && source.redacted === true;',
        '  "redacted" in source;',
        T_SAVED,
        "tells a passage the owner may not read from one that is",
    ),
    # === searching a file instead of reading it, and the sources under an answer =====
    (
        "Search it instead is called something else",
        DOCS,
        '      return "Search it instead";',
        '      return "Search";',
        T_DOCS,
        "is an offer with a button of its own",
    ),
    (
        "choosing Search it instead keeps another choice",
        DOCS,
        '      return { mode: "search" };',
        '      return { mode: "whole" };',
        T_DOCS,
        "is an offer with a button of its own",
    ),
    (
        "the offer to search is told from the others by the wrong key",
        DOCS,
        'offer.action === "readFirst" ? "first" : "search"',
        'offer.action === "readFirst" ? "first" : "all"',
        T_DOCS,
        "is an offer with a button of its own",
    ),
    (
        "a choice to search is not told to the person",
        DOCS,
        'if (choice.mode === "search") return "search it instead";',
        'if (choice.mode === "search") return "search";',
        T_DOCS,
        "is a choice a person can be told they made",
    ),
    (
        "a file chosen to be searched looks like one read whole",
        DOCS,
        "      default:\n        return decision.kind;",
        '      case "search":\n        return "whole";\n      default:\n        return decision.kind;',
        T_DOCS,
        "is its own look of a file's card",
    ),
    (
        "a file chosen to be searched does not say it is kept for this chat only",
        DOCS,
        'choice: "Your choice: search it instead. It is kept for this chat only.",',
        'choice: "Your choice: search it instead.",',
        T_DOCS,
        "is its own look of a file's card",
    ),
    (
        "a searched file says its passages are found",
        DOCS,
        'found ? `${count(found, "passage")} to search.` : null,',
        "found ? `${found} passages found.` : null,",
        T_DOCS,
        "says how many passages it has once it has some",
    ),
    (
        "a searched file's passages are not read from its decision",
        DOCS,
        "const found = file.search?.passages ?? file.decision?.search?.passages;",
        "const found = file.search?.passages;",
        T_DOCS,
        "says how many passages it has once it has some",
    ),
    (
        "a searched file does not say how it will be used when the server did not",
        DOCS,
        'serverSentence(file, "Will be searched for each question, not read whole."),',
        'serverSentence(file, "Will be searched."),',
        T_DOCS,
        "says what it will do in its own words when the server gave none",
    ),
    (
        "Send's reason for a file that needs a choice does not offer searching",
        DOCS,
        "      choose: `Choose how much of ${name} to read, search it instead, or remove it.`,",
        "      choose: `Choose how much of ${name} to read, or remove it.`,",
        T_DOCS,
        "is that a file needs a choice, whether it fits whole or not",
    ),
    (
        "Send's reason for a file that is too long does not offer searching",
        DOCS,
        "      too_long: `Choose how much of ${name} to read, search it instead, or remove it.`,",
        "      too_long: `Choose how much of ${name} to read, or remove it.`,",
        T_DOCS,
        "is that a file needs a choice, whether it fits whole or not",
    ),
    (
        "a passage is taken for one when it only says it is not",
        DOCS,
        'source.kind === "document" && "passage" in source && source.passage === true;',
        'source.kind === "document" && "passage" in source;',
        T_DOCS,
        "is not a passage when it only says it is not one",
    ),
    (
        "a passage is taken for a file that was sent",
        DOCS,
        'source.kind === "document" && !isPassageSource(source);',
        'source.kind === "document";',
        T_DOCS,
        "tells each from the others",
    ),
    (
        "a passage is taken for a web page",
        DOCS,
        'source is WebSource => source.kind !== "document";',
        "source is WebSource => !isDocumentSource(source);",
        T_DOCS,
        "tells each from the others",
    ),
    (
        "the Documents picker asks for pictures",
        DOCS,
        "export const DOCUMENT_ACCEPT = [",
        'export const DOCUMENT_ACCEPT = [\n  "image/png",',
        T_DOCS,
        "is documents only",
    ),
    (
        "the Documents picker asks for no Markdown or code",
        DOCS,
        'export const DOCUMENT_ACCEPT = [\n  "application/pdf",\n  DOCX,\n  ".docx",\n  "text/*",\n  ...PLAIN_EXTENSIONS,\n  ...KNOWN_EXTENSIONS,\n]',
        'export const DOCUMENT_ACCEPT = [\n  "application/pdf",\n  DOCX,\n  ".docx",\n  "text/*",\n  ...PLAIN_EXTENSIONS,\n]',
        T_DOCS,
        "is documents only",
    ),
    # === the working line, the connection and the events ===============================
    (
        "a search is said to be a reading of files",
        WORDS,
        'return progress.phase === "searching" ? "Searching your documents…" : "Reading your files…";',
        'return "Reading your files…";',
        T_LIB,
        "says Searching your documents, which is not",
    ),
    (
        "reading files is said to be a search",
        WORDS,
        'return progress.phase === "searching" ? "Searching your documents…" : "Reading your files…";',
        'return "Searching your documents…";',
        T_LIB,
        "says Searching your documents, which is not",
    ),
    (
        "a search says it is the model's wait",
        WORDS,
        '"Searching your documents…" : "Reading your files…";',
        '"Waiting for the model…" : "Reading your files…";',
        T_LIB,
        "says Searching your documents, which is not",
    ),
    (
        "a saved document's text is asked for at another address",
        API,
        "textOf(`/api/documents/${encodeURIComponent(id)}/text`);",
        "textOf(`/api/documents/${id}/text`);",
        T_API,
        "is asked for at the document's own address",
    ),
    (
        "an upload reports the progress of a body with nothing in it",
        API,
        "if (event.lengthComputable && event.total > 0)",
        "if (event.lengthComputable)",
        T_API,
        "says how much of the file has gone",
    ),
    (
        "an upload does not say it is all there when the answer comes",
        API,
        "          options.onProgress?.(1);\n",
        "",
        T_API,
        "says how much of the file has gone",
    ),
    (
        "an upload goes without the request secret",
        API,
        "for (const [name, value] of Object.entries(headers())) request.setRequestHeader(name, value);",
        "",
        T_API,
        "posts the form to the path with the request secret",
    ),
    (
        "a lost connection is said in other words",
        API,
        '"The connection to Workbench was lost while the file was being added."',
        '"The upload failed."',
        T_API,
        "says the connection was lost when there is no answer at all",
    ),
    (
        "a stopped upload is not an abort",
        API,
        'request.onabort = () => reject(new DOMException("The upload was stopped.", "AbortError"));',
        'request.onabort = () => reject(new Error("The upload was stopped."));',
        T_API,
        "stops when told to",
    ),
    (
        "an upload that was stopped before it began is sent",
        API,
        "if (options.signal?.aborted) {",
        "if (false) {",
        T_API,
        "stops when told to",
    ),
    (
        "an upload does not stop when told to",
        API,
        'options.signal?.addEventListener("abort", () => request.abort(), { once: true });',
        "",
        T_API,
        "stops when told to",
    ),
    (
        "an upload answered with nothing is read as something",
        API,
        "const empty = request.status === 204 || request.status === 205;",
        "const empty = false;",
        T_API,
        "is an answer with nothing in it",
    ),
    (
        "the passages that came with an answer are not kept",
        CHAT_HOOK_DOCS,
        "retrieval: event.retrieval === undefined ? m.retrieval : event.retrieval,",
        "retrieval: m.retrieval,",
        T_CHAT_EVENTS,
        "arrives with the passages, before the model's first word",
    ),
    (
        "the words of an answer take the passages away",
        CHAT_HOOK_DOCS,
        "retrieval: event.retrieval === undefined ? m.retrieval : event.retrieval,",
        "retrieval: event.retrieval,",
        T_CHAT_EVENTS,
        "arrives with the passages, before the model's first word",
    ),
    (
        "an event that says there is no search cannot clear it",
        CHAT_HOOK_DOCS,
        "retrieval: event.retrieval === undefined ? m.retrieval : event.retrieval,",
        "retrieval: event.retrieval ?? m.retrieval,",
        T_CHAT_EVENTS,
        "can be cleared by an event that says it is none",
    ),
    # === a document list that watches ===================================================
    (
        "a read that was overtaken puts its documents back",
        SAVED_HOOK,
        "      if (mine !== latest.current) return;\n      setData(next);",
        "      setData(next);",
        T_SAVED_HOOK,
        "drops one that a later read overtook",
    ),
    (
        "a damaged search is not let go of when the documents can be read",
        SAVED_HOOK,
        "      setBlocked(null);\n      setProblem(null);\n      failing.current = false;",
        "      setProblem(null);\n      failing.current = false;",
        T_SAVED_HOOK,
        "is let go of when the documents can be read again",
    ),
    (
        "a damaged search keeps the documents it had",
        SAVED_HOOK,
        "        setData(null);\n",
        "",
        T_SAVED_HOOK,
        "shows the server's words and what can be done, and nothing that may be out of date",
    ),
    (
        "a failed read is waited for as long as a good one",
        SAVED_HOOK,
        "      failing.current = true;\n",
        "",
        T_SAVED_HOOK,
        "waits longer after a read that failed",
    ),
    (
        "documents that are all read are asked after again",
        SAVED_HOOK,
        "    if (!watching) return;\n",
        "",
        T_SAVED_HOOK,
        "asks nothing more while every document is read",
    ),
    (
        "the first look at a document being read is as slow as the next",
        SAVED_HOOK,
        "    next(FIRST_POLL_MS);",
        "    next(POLL_MS);",
        T_SAVED_HOOK,
        "asks again soon after a document turns up unread",
    ),
    (
        "a read that failed is not waited longer for",
        SAVED_HOOK,
        "if (!stopped) next(failing.current ? RETRY_MS : POLL_MS);",
        "if (!stopped) next(POLL_MS);",
        T_SAVED_HOOK,
        "waits longer after a read that failed",
    ),
    (
        "a screen that was left is asked for again",
        SAVED_HOOK,
        "        if (!stopped) next(failing.current ? RETRY_MS : POLL_MS);",
        "        next(failing.current ? RETRY_MS : POLL_MS);",
        T_SAVED_HOOK,
        "does not ask again when the screen goes while a read is on its way",
    ),
    (
        "a screen that was left keeps its timer",
        SAVED_HOOK,
        "      window.clearTimeout(timer);\n",
        "",
        T_SAVED_HOOK,
        "stops when the screen goes",
    ),
    (
        "a document that can be found by words is not watched",
        SAVED_HOOK,
        "const watching = data?.documents.some(isPending) ?? false;",
        'const watching = data?.documents.some((d) => d.state === "queued" || d.state === "reading") ?? false;',
        T_SAVED_HOOK,
        "keeps asking while a document can be found by words and is not ready yet",
    ),
    (
        "a read that failed says nothing",
        SAVED_HOOK,
        "        setProblem(failure instanceof Error ? failure.message : String(failure));",
        "        setProblem(null);",
        T_SAVED_HOOK,
        "is not a failure to read: any other error keeps what is shown",
    ),
    # === a chat's documents that are watched ============================================
    (
        "a chat's documents are read at another address",
        CHAT_HOOK,
        "api<unknown>(`/api/chats/${encodeURIComponent(chatId)}/documents`)",
        "api<unknown>(`/api/chats/${chatId}/documents`)",
        T_CHAT_HOOK,
        "are asked for at the chat's own address",
    ),
    (
        "something that is not a view is shown as one",
        CHAT_HOOK,
        "setView(readView(next));",
        "setView(next as ChatDocumentsView);",
        T_CHAT_HOOK,
        "are not a view when the server answered with something else",
    ),
    (
        "a view the server just gave is put back by an older read",
        CHAT_HOOK,
        "    latest.current += 1;\n    setView(next);",
        "    setView(next);",
        T_CHAT_HOOK,
        "take a view the server just answered with over a read that is still on its way",
    ),
    (
        "a read that was overtaken puts its view back",
        CHAT_HOOK,
        "      if (mine !== latest.current) return;\n      setView(readView(next));",
        "      setView(readView(next));",
        T_CHAT_HOOK,
        "never put back what a newer read replaced",
    ),
    (
        "the files of the chat are not waited for",
        CHAT_HOOK,
        "view.counts.library.waiting + view.counts.chat.waiting > 0",
        "view.counts.library.waiting > 0",
        T_CHAT_HOOK,
        "count a chat's own files too",
    ),
    (
        "a chat is asked after as slowly as it likes",
        CHAT_HOOK,
        "window.setTimeout(() => void reload(), WAIT_MS);",
        "window.setTimeout(() => void reload(), WAIT_MS * 3);",
        T_CHAT_HOOK,
        "are asked about again every two seconds until none is, and not again",
    ),
    (
        "a chat is not asked after again once a read failed",
        CHAT_HOOK,
        "  }, [waiting, reads, reload]);",
        "  }, [waiting, reload]);",
        T_CHAT_HOOK,
        "keep being asked about after a read that failed",
    ),
    (
        "a read that failed does not count as a read",
        CHAT_HOOK,
        "      setError(failure instanceof Error ? failure.message : String(failure));\n      setReads((n) => n + 1);",
        "      setError(failure instanceof Error ? failure.message : String(failure));",
        T_CHAT_HOOK,
        "keep being asked about after a read that failed",
    ),
    (
        "a chat that was left is still asked after",
        CHAT_HOOK,
        "    return () => window.clearTimeout(timer);",
        "    return;",
        T_CHAT_HOOK,
        "stop when the chat is left",
    ),
    (
        "a chat's documents are not read again when the model changes",
        CHAT_HOOK,
        "  }, [reload, stamp]);",
        "  }, [reload]);",
        T_CHAT_HOOK,
        "are read again when the stamp changes",
    ),
    (
        "a chat's documents are not read again when the chat says so",
        CHAT_HOOK,
        "  return { view, error, reload, adopt };",
        "  return { view, error, reload: async () => undefined, adopt };",
        T_CHAT_HOOK,
        "are read again when the chat says so",
    ),
    (
        "the chat's documents that could not be read keep an old error",
        CHAT_HOOK,
        "      setView(readView(next));\n      setError(null);",
        "      setView(readView(next));",
        T_CHAT_HOOK,
        "keep being asked about after a read that failed",
    ),
    (
        "a view the server just gave leaves the old error",
        CHAT_HOOK,
        "    setView(next);\n    setError(null);\n  }, []);",
        "    setView(next);\n  }, []);",
        T_CHAT_HOOK,
        "take a view the server just answered with as the end of an error",
    ),
    # === the Documents screen ============================================================
    (
        "Back to chat goes nowhere",
        SCREEN,
        '<button type="button" data-testid="documents-back" className="btn" onClick={onClose}>',
        '<button type="button" data-testid="documents-back" className="btn" onClick={() => undefined}>',
        T_SCREEN,
        "is a page with a heading that takes the keyboard, and a way back to the chat",
    ),
    (
        "the screen does not say the search's own headline",
        SCREEN,
        '<span className="notice-title">{data.search.headline}</span>',
        '<span className="notice-title">Searching your documents</span>',
        T_SCREEN,
        "says in one line what it is, and says search is by words only, in the server's own headline",
    ),
    (
        "the screen says words only when the search is by meaning too",
        SCREEN,
        "{!data.search.meaning && WORDS_ONLY_HELP}",
        "{WORDS_ONLY_HELP}",
        T_SCREEN,
        "says what the server says of the search, and no more, when it searches by meaning too",
    ),
    (
        "the screen says the owner can read chats where the owner cannot",
        SCREEN,
        "{data.ownerReadsChats && (",
        "{true && (",
        T_SCREEN,
        "is not said where the owner cannot read chats",
    ),
    (
        "Ask about your documents is offered when nothing can be searched",
        SCREEN,
        "{!off && ready > 0 && (",
        "{!off && (",
        T_SCREEN,
        "is not offered while nothing can be searched yet",
    ),
    (
        "Ask about your documents does not start a chat",
        SCREEN,
        "                      onClick={onAsk}",
        "                      onClick={onClose}",
        T_SCREEN,
        "is offered once something can be searched, and starts a chat with Documents on",
    ),
    (
        "a document that cannot be searched yet is counted as ready",
        SCREEN,
        "const ready = data.documents.filter((doc) => doc.searchable).length;",
        "const ready = data.documents.length;",
        T_SCREEN,
        "is not offered while nothing can be searched yet",
    ),
    (
        "Test a question is there before anything is added",
        SCREEN,
        "{!off && data.documents.length > 0 && <TestQuestion />}",
        "{!off && <TestQuestion />}",
        T_SCREEN,
        "is empty at first: where to start, and nothing to test or ask yet",
    ),
    (
        "the owner's off still offers to add files",
        SCREEN,
        "{!off && <AddDocuments onChanged={() => void reload()} />}",
        "{<AddDocuments onChanged={() => void reload()} />}",
        T_SCREEN,
        "says so, and offers nothing that would keep a document",
    ),
    (
        "the owner's off still shows the settings",
        SCREEN,
        "        {!off && (\n          <Settings",
        "        {true && (\n          <Settings",
        T_SCREEN,
        "says so, and offers nothing that would keep a document",
    ),
    (
        "a person with nothing kept sees an empty list under the owner's off",
        SCREEN,
        "{!bare && (",
        "{true && (",
        T_SCREEN,
        "is only that line for a person with nothing kept",
    ),
    (
        "the owner's off says the documents stay when there are none",
        SCREEN,
        '              {data.documents.length > 0 &&\n                " The documents you already have stay yours: you can still open and remove them."}',
        '              {" The documents you already have stay yours: you can still open and remove them."}',
        T_SCREEN,
        "is only that line for a person with nothing kept",
    ),
    (
        "the owner's off lets a document be read again",
        SCREEN,
        "          locked={off}",
        "          locked={false}",
        T_SCREEN,
        "leaves what the person already has to open, read and remove, and not to read again",
    ),
    (
        "a removed document does not give the keyboard to the list's heading",
        SCREEN,
        "              () => del(`/api/documents/${encodeURIComponent(doc.id)}`),\n              `${uid}-list`,",
        "              () => del(`/api/documents/${encodeURIComponent(doc.id)}`),",
        T_SCREEN,
        "gives the keyboard to the list's heading once the document it was on is gone",
    ),
    (
        "a document is removed at another address",
        SCREEN,
        "() => del(`/api/documents/${encodeURIComponent(doc.id)}`),",
        "() => del(`/api/document/${encodeURIComponent(doc.id)}`),",
        T_SCREEN,
        "removes the document at its own address, and it is gone from the list and from the totals",
    ),
    (
        "a document is read again at another address",
        SCREEN,
        "post(`/api/documents/${encodeURIComponent(doc.id)}/reread`)",
        "post(`/api/documents/${encodeURIComponent(doc.id)}/read`)",
        T_SCREEN,
        "asks the server to, and the document waits to be read again",
    ),
    (
        "the original is asked for at another address",
        SCREEN,
        "fileOf(`/api/documents/${encodeURIComponent(doc.id)}/original`)",
        "fileOf(`/api/documents/${encodeURIComponent(doc.id)}/file`)",
        T_SCREEN,
        "fetches the file with the secret and saves it under the name it was added with",
    ),
    (
        "the original is saved with no name when the server gave none",
        SCREEN,
        "saveFile(file.blob, file.name ?? doc.name);",
        'saveFile(file.blob, file.name ?? "document");',
        T_SCREEN,
        "falls back to the document's own name when the server gave none",
    ),
    (
        "what went wrong with one document is said at the top",
        SCREEN,
        "setRowProblems((now) => ({ ...now, [id]: failureWords(failure) }));",
        "setProblem(failureWords(failure));",
        T_SCREEN,
        "says why on the document's own row when it could not be removed, and keeps it",
    ),
    (
        "the words about a document stay when something else is done to it",
        SCREEN,
        "      delete rest[id];\n",
        "",
        T_SCREEN,
        "clears the words about a document when something else is done to it",
    ),
    (
        "the screen does not read again after a change",
        SCREEN,
        "    } finally {\n      await reload();\n      setBusy(false);",
        "    } finally {\n      setBusy(false);",
        T_SCREEN,
        "removes the document at its own address, and it is gone from the list and from the totals",
    ),
    (
        "the screen is not held while a change is made",
        SCREEN,
        "    keepFocus();\n    setBusy(true);\n    try {\n      return await work();",
        "    keepFocus();\n    try {\n      return await work();",
        T_SCREEN,
        "are held while a change is made",
    ),
    (
        "a read that failed again says it could not read at all",
        SCREEN,
        '{data ? "Could not read your documents again" : "Could not read your documents"}',
        '{data ? "Could not read your documents" : "Could not read your documents again"}',
        T_SCREEN,
        "keeps what it showed when a later read fails, and says so",
    ),
    (
        "a search that cannot be mended is called damaged",
        SCREEN,
        '? "Your documents\' search is damaged"\n                  : "Your documents cannot be searched right now"}',
        '? "Your documents cannot be searched right now"\n                  : "Your documents\' search is damaged"}',
        T_SCREEN,
        "offers no Rebuild for what Rebuild cannot mend, and still says why and lets the person try again",
    ),
    (
        "Rebuild is offered for what it cannot mend",
        SCREEN,
        "                {blocked.canRebuild && (\n                  <button",
        "                {true && (\n                  <button",
        T_SCREEN,
        "offers no Rebuild for what Rebuild cannot mend, and still says why and lets the person try again",
    ),
    (
        "Rebuild search is asked for at another address",
        SCREEN,
        'post<Rebuilt>("/api/documents/rebuild")',
        'post<Rebuilt>("/api/documents/rebuilt")',
        T_SCREEN,
        "makes the search again from the person's files, and shows them being read",
    ),
    (
        "a choice about documents is sent to another address",
        SCREEN,
        'put("/api/documents/prefs", body)',
        'put("/api/documents/prefs", {})',
        T_SCREEN,
        "turn it on for new chats, show it on, and say so",
    ),
    (
        "the choice just made is not shown while the server catches up",
        SCREEN,
        "  const newChats = busy && wanted.newChats !== undefined ? wanted.newChats : effective.newChats;",
        "  const newChats = effective.newChats;",
        T_SCREEN,
        "turn it on for new chats, show it on, and say so",
    ),
    (
        "the owner's choice can be changed by the person",
        SCREEN,
        "disabled={busy || ownerSet}",
        "disabled={busy}",
        T_SCREEN,
        "are the owner's where the owner set them: shown, said, and not for the person to change",
    ),
    (
        "the owner's choice can be taken back by the person",
        SCREEN,
        "{prefs.external !== null && !ownerSet && (",
        "{prefs.external !== null && (",
        T_SCREEN,
        "are the owner's where the owner set them: shown, said, and not for the person to change",
    ),
    (
        "the default is offered to a person who never chose",
        SCREEN,
        "{prefs.external !== null && !ownerSet && (",
        "{!ownerSet && (",
        T_SCREEN,
        "let the person choose, show the choice in effect with their own name on it, and take it back",
    ),
    (
        "Use the default sets another choice",
        SCREEN,
        "onClick={() => onPrefs({ external: null })}",
        'onClick={() => onPrefs({ external: "never" })}',
        T_SCREEN,
        "let the person choose, show the choice in effect with their own name on it, and take it back",
    ),
    (
        "whose word the choice is is not said",
        SCREEN,
        "          {setByWords(effective.externalSetBy)}",
        "",
        T_SCREEN,
        "show the choice about models outside the person's machines, and whose word it is",
    ),
    (
        "what Documents in new chats does is not said",
        SCREEN,
        "          {newChatsWords(prefs)}",
        "",
        T_SCREEN,
        "show Documents in new chats as it is in effect",
    ),
    (
        "the owner's off is not told from the person's own",
        SCREEN,
        "const off = !data.installEnabled;",
        "const off = false;",
        T_SCREEN,
        "says so, and offers nothing that would keep a document",
    ),
    # === one document in the list ======================================================
    (
        "a document being read is not marked busy",
        ROW,
        'aria-busy={words.tone === "busy" || undefined}',
        "aria-busy={undefined}",
        T_SCREEN,
        "says each state in the design's words, and a skip or a failure in the server's own",
    ),
    (
        "a failure is said like a skip",
        ROW,
        'className={`notice ${words.tone === "error" ? "notice--error" : "notice--warn"}`}',
        'className="notice notice--warn"',
        T_SCREEN,
        "says each state in the design's words, and a skip or a failure in the server's own",
    ),
    (
        "the notes are said of a document that could not be read",
        ROW,
        "{doc.notes.length > 0 && !ended && (",
        "{doc.notes.length > 0 && (",
        T_SCREEN,
        "leaves the reader's notes out for a document that was skipped or failed",
    ),
    (
        "the question before removing is backed out of with another key",
        ROW,
        'if (event.key === "Escape") leave();',
        'if (event.key === "Enter") leave();',
        T_SCREEN,
        "is backed out of with Escape",
    ),
    (
        "the question before removing does not give the keyboard to Keep it",
        ROW,
        "              autoFocus\n",
        "",
        T_SCREEN,
        "asks first, says it is for real, and keeps the document if the person keeps it",
    ),
    (
        "the question before removing does not say it cannot be undone",
        ROW,
        "This cannot be undone.",
        "It can be put back.",
        T_SCREEN,
        "asks first, says it is for real, and keeps the document if the person keeps it",
    ),
    (
        "Remove can be pressed twice",
        ROW,
        '              data-testid="saved-document-remove-go"\n              disabled={busy}',
        '              data-testid="saved-document-remove-go"',
        T_SCREEN,
        "keeps another question that is open held while a removal is done",
    ),
    (
        "the text is offered for a document that cannot be found by words",
        ROW,
        "{doc.searchable && text.action}",
        "{text.action}",
        T_SCREEN,
        "offers what can be done with each: the original and removing always, the text once it can be found, reading again once it has stopped",
    ),
    (
        "a document being read can be read again",
        ROW,
        "{!locked && finished && (",
        "{!locked && (",
        T_SCREEN,
        "offers what can be done with each: the original and removing always, the text once it can be found, reading again once it has stopped",
    ),
    (
        "a document that failed cannot be read again",
        ROW,
        'const finished = doc.state === "ready" || doc.state === "skipped" || doc.state === "failed";',
        'const finished = doc.state === "ready" || doc.state === "skipped";',
        T_SCREEN,
        "offers what can be done with each: the original and removing always, the text once it can be found, reading again once it has stopped",
    ),
    (
        "a locked document can still be read again",
        ROW,
        "{!locked && finished && (",
        "{finished && (",
        T_SCREEN,
        "leaves what the person already has to open, read and remove, and not to read again",
    ),
    (
        "the keyboard is not given back after a question that was backed out of",
        ROW,
        "    opener.current?.focus();",
        "",
        T_SCREEN,
        "asks first, says it is for real, and keeps the document if the person keeps it",
    ),
    (
        "the day a document was added is not a time",
        ROW,
        "<time dateTime={date.toISOString()} title={date.toLocaleString()}>",
        "<time>",
        T_SCREEN,
        "is each document with its name, what it is, where it stands and the day it was added",
    ),
    (
        "Open original is not named for its document",
        ROW,
        "aria-label={`Open original: ${doc.name}`}",
        'aria-label="Open original"',
        T_SCREEN,
        "names each button for the document it acts on, so a row of buttons can be told apart",
    ),
    (
        "Re-read is not named for its document",
        ROW,
        "aria-label={`Re-read: ${doc.name}`}",
        'aria-label="Re-read"',
        T_SCREEN,
        "names each button for the document it acts on, so a row of buttons can be told apart",
    ),
    (
        "Remove is not named for its document",
        ROW,
        "aria-label={`Remove: ${doc.name}`}",
        'aria-label="Remove"',
        T_SCREEN,
        "names each button for the document it acts on, so a row of buttons can be told apart",
    ),
    (
        "the question before removing is not named for its document",
        ROW,
        "aria-label={`Remove ${doc.name}?`}",
        'aria-label="Remove?"',
        T_SCREEN,
        "asks first, says it is for real, and keeps the document if the person keeps it",
    ),
    (
        "a document's state is not in the list",
        ROW,
        "      data-state={doc.state}\n",
        "",
        T_SCREEN,
        "is each document with its name, what it is, where it stands and the day it was added",
    ),
    # === adding files ====================================================================
    (
        "the picker is asked for every kind of file",
        ADD,
        "accept={DOCUMENT_ACCEPT}",
        'accept=""',
        T_ADD,
        "is a button that opens a picker for documents, many at a time, and not for pictures or sound",
    ),
    (
        "the picker takes one file at a time",
        ADD,
        "          multiple\n",
        "",
        T_ADD,
        "is a button that opens a picker for documents, many at a time, and not for pictures or sound",
    ),
    (
        "a file that is going cannot be stopped",
        ADD,
        "            signal: stop.current?.signal,\n",
        "",
        T_ADD,
        "stops what is going, and sends nothing more, when the screen is left",
    ),
    (
        "a file that was stopped is said to have failed",
        ADD,
        "          if (stop.current?.signal.aborted) return;\n",
        "",
        T_ADD,
        "stops what is going, and sends nothing more, when the screen is left",
    ),
    (
        "a file that is going does not say how much has gone",
        ADD,
        "            onProgress: (progress) => show(key, { progress }),\n",
        "",
        T_ADD,
        "shows how much of the file that is going has gone, and gives the bar a name",
    ),
    (
        "the bar does not say how much has gone",
        ADD,
        "value={Math.round(row.progress * 100)}",
        "value={row.progress}",
        T_ADD,
        "shows how much of the file that is going has gone, and gives the bar a name",
    ),
    (
        "the bar has no name",
        ADD,
        "aria-label={`Adding ${row.name}`}",
        'aria-label="Adding"',
        T_ADD,
        "shows how much of the file that is going has gone, and gives the bar a name",
    ),
    (
        "a file that is already there says nothing",
        ADD,
        "? `${file.name} is already in your documents, exactly as it is.`",
        "? null",
        T_ADD,
        "says when a file is already there, and when it takes the place of an older one",
    ),
    (
        "a file that takes the place of another says nothing",
        ADD,
        '? "Added. It takes the place of the older file with the same name."',
        "? null",
        T_ADD,
        "says when a file is already there, and when it takes the place of an older one",
    ),
    (
        "a lost connection is a refusal",
        ADD,
        'state: why.status >= 400 && why.status < 500 ? "refused" : "failed",',
        'state: "refused",',
        T_ADD,
        "says the connection was lost when it was, and goes on to the next",
    ),
    (
        "the list is not told to read again after a file",
        ADD,
        "        changed.current();\n",
        "",
        T_ADD,
        "tells the list to read again after each file, so each shows up waiting to be read",
    ),
    (
        "a folder is refused in other words",
        ADD,
        "message: `${name} is a folder. Choose or drop the files in it.`,",
        "message: `${name} was not added.`,",
        T_ADD,
        "refuses a folder in plain words, and adds the files dropped with it",
    ),
    (
        "a folder is added as a file",
        ADD,
        '          files.filter((file) => !(file.type === "" && folders.has(file.name))),',
        "          files,",
        T_ADD,
        "refuses a folder in plain words, and adds the files dropped with it",
    ),
    (
        "a file with the name of a folder is taken for one",
        ADD,
        '!(file.type === "" && folders.has(file.name))',
        "!folders.has(file.name)",
        T_ADD,
        "is not fooled by a file that has the name of a folder",
    ),
    (
        "anything dragged over is marked as a place to drop",
        ADD,
        '      onDragEnter={(event) => {\n        if (!event.dataTransfer?.types.includes("Files")) return;\n',
        "      onDragEnter={(event) => {\n",
        T_ADD,
        "shows where to drop while files are over the screen, and not for anything else",
    ),
    (
        "a file moving over what is inside is not still over",
        ADD,
        "        dragDepth.current = Math.max(0, dragDepth.current - 1);\n",
        "        dragDepth.current = 0;\n",
        T_ADD,
        "stays marked while the files move over what is inside it",
    ),
    (
        "the same file cannot be chosen again",
        ADD,
        '            event.target.value = "";\n',
        "",
        T_ADD,
        "can choose the same file again",
    ),
    (
        "a person is not asked to stay while files go",
        ADD,
        '          {going && (\n            <p className="text-xs text-muted">Stay on this page until every file is added.</p>\n          )}',
        "",
        T_ADD,
        "says where it is while files go, asks to be stayed on, and says what came of them after",
    ),
    (
        "a file that was added is said again once all have gone",
        ADD,
        '    : uploads.filter((row) => !(row.state === "added" && row.message === null));',
        "    : uploads;",
        T_ADD,
        "does not say again, in a list of its own, a file that was added with nothing more to say: it is in the list of documents",
    ),
    (
        "a note on an added file is not kept once all have gone",
        ADD,
        '!(row.state === "added" && row.message === null)',
        '!(row.state === "added")',
        T_ADD,
        "keeps what the person has to read: a refusal, a file that was already there, one that took another's place",
    ),
    (
        "the list can be cleared while files go",
        ADD,
        "          {!going && (\n            <div>\n              <button",
        "          {true && (\n            <div>\n              <button",
        T_ADD,
        "clears the list once nothing is going, and not before",
    ),
    (
        "a file sent while others go does not wait",
        ADD,
        "    if (running.current) return;\n",
        "",
        T_ADD,
        "is added to while it goes: the new files wait behind the ones already there",
    ),
    (
        "a folder is given a size",
        ADD,
        "{row.size > 0 && (",
        "{(",
        T_ADD,
        "refuses a folder in plain words, and adds the files dropped with it",
    ),
    # === Test a question ================================================================
    (
        "a question can be as long as anyone likes",
        TRY,
        "const MAX_QUESTION = 2000;",
        "const MAX_QUESTION = 200;",
        T_TRY,
        "is a heading, what it does in two lines, and a question with a button",
    ),
    (
        "a question is sent with its spaces",
        TRY,
        "const asked = question.trim();",
        "const asked = question;",
        T_TRY,
        "asks the server, without a chat or a model, with the question as it was trimmed",
    ),
    (
        "a question that is being asked is asked again by Enter",
        TRY,
        "    if (!asked || busy) return;",
        "    if (!asked) return;",
        T_TRY,
        "is held while it asks, so it is not asked twice",
    ),
    (
        "a question of spaces is asked",
        TRY,
        "disabled={busy || !question.trim()}",
        "disabled={busy}",
        T_TRY,
        "will not ask an empty question, or one of spaces",
    ),
    (
        "the question can be changed while it is asked",
        TRY,
        "            disabled={busy}\n            placeholder=",
        "            placeholder=",
        T_TRY,
        "is held while it asks, so it is not asked twice",
    ),
    (
        "a failure stays when the next question works",
        TRY,
        "    setBusy(true);\n    setProblem(null);",
        "    setBusy(true);",
        T_TRY,
        "keeps the last result off the screen when the next try fails, and clears the words when the next works",
    ),
    (
        "the last result stays beside a failure",
        TRY,
        "    setResult(null);\n",
        "",
        T_TRY,
        "keeps the last result off the screen when the next try fails, and clears the words when the next works",
    ),
    (
        "the question does not say it is wrong when it failed",
        TRY,
        "aria-invalid={problem ? true : undefined}",
        "aria-invalid={undefined}",
        T_TRY,
        "says why in the server's own words, and is never an empty list",
    ),
    (
        "the question is not described by what failed",
        TRY,
        "aria-describedby={problem ? `${uid}-problem` : undefined}",
        "aria-describedby={undefined}",
        T_TRY,
        "says why in the server's own words, and is never an empty list",
    ),
    (
        "a failure is not an alert",
        TRY,
        '          role="alert"\n          data-testid="documents-test-problem"',
        '          data-testid="documents-test-problem"',
        T_TRY,
        "says why in the server's own words, and is never an empty list",
    ),
    (
        "what was found is not announced",
        TRY,
        '<p role="status" className="notice" data-testid="documents-test-words">',
        '<p className="notice" data-testid="documents-test-words">',
        T_TRY,
        "says how many passages would be sent, and that no model was asked, and echoes the question",
    ),
    (
        "a passage says nothing of which words found it",
        TRY,
        "      {matched && (\n",
        "      {false && (\n",
        T_TRY,
        "shows the start of each passage with the words it was found by marked, and names them",
    ),
    (
        "the heading is said where no word was found in it",
        TRY,
        '{hit.why.inHeading.length > 0 && ` (in the heading: ${hit.why.inHeading.join(", ")})`}',
        '{` (in the heading: ${hit.why.inHeading.join(", ")})`}',
        T_TRY,
        "shows the start of each passage with the words it was found by marked, and names them",
    ),
    (
        "a phrase is not marked where it stands",
        TRY,
        "words={[...hit.why.terms, ...hit.why.phrases]}",
        "words={hit.why.terms}",
        T_TRY,
        "quotes a phrase it was found by",
    ),
    (
        "the whole passage is not named",
        TRY,
        "label={`The whole passage ${hit.rank}`}",
        'label="The whole passage"',
        T_TRY,
        "opens the whole passage in a quiet box, and closes it again",
    ),
    (
        "how much was searched is not said",
        TRY,
        "            <li>{searchedCounts(found)}</li>\n",
        "",
        T_TRY,
        "says what was searched for, how much, and what did not fit",
    ),
    (
        "a question with no words says what it searched for",
        TRY,
        '            {searched && <li className="wrap-anywhere">{searched}</li>}',
        '            {<li className="wrap-anywhere">{searched}</li>}',
        T_TRY,
        "says a question with no words in it has none to search for",
    ),
    (
        "a file of a chat is not said",
        TRY,
        '{hit.scope === "chat" && <span className="text-muted"> · a file of a chat</span>}',
        "",
        T_TRY,
        "says a passage is from a file of a chat when it is",
    ),
    (
        "a passage that did not fit is not said",
        TRY,
        "            {left && <li>{left}</li>}\n",
        "",
        T_TRY,
        "says what was searched for, how much, and what did not fit",
    ),
    # === a chat's chip and drawer ========================================================
    (
        "the chip is not named by its words",
        CHIP,
        "      aria-label={words.label}\n      aria-expanded={open}",
        '      aria-label="Documents"\n      aria-expanded={open}',
        T_CHIP,
        "is a button named by its words, with a glyph that is only a glyph",
    ),
    (
        "the chip does not say whether its drawer is open",
        CHIP,
        "      aria-expanded={open}\n",
        "      aria-expanded={false}\n",
        T_CHIP,
        "says whether its drawer is open, and opens it on a press",
    ),
    (
        "the chip warns with the plain glyph",
        CHIP,
        'const Icon = words.tone === "warn" ? TriangleAlert : Files;',
        "const Icon = Files;",
        T_CHIP,
        "warns, with a different glyph and its words, when it could not read",
    ),
    (
        "the chip keeps no count for a narrow window",
        CHIP,
        '<span aria-hidden className="tabular-nums xl:hidden">',
        '<span aria-hidden className="tabular-nums hidden">',
        T_CHIP,
        "keeps the glyph and the count for a window too narrow for the words, and the words for the rest",
    ),
    (
        "the chip keeps its words for a narrow window",
        CHIP,
        '<span aria-hidden className="max-xl:hidden">',
        '<span aria-hidden className="hidden">',
        T_CHIP,
        "keeps the glyph and the count for a window too narrow for the words, and the words for the rest",
    ),
    (
        "a warning chip is not in the warning colour",
        CHIP,
        '  warn: "text-warn",',
        '  warn: "",',
        T_CHIP,
        "warns for documents that were held back, in the warning colour and with the warning glyph",
    ),
    (
        "a chat that is searching is not marked",
        CHIP,
        'className={`shrink-0 ${words.tone === "on" ? "text-accent" : ""}`}',
        'className="shrink-0"',
        T_CHIP,
        "marks a chat that is searching, quietly: the glyph is blue and the words are plain",
    ),
    (
        "the drawer says the chat's own files are read, not searched",
        CHIP,
        "The files of this chat that you chose to search are searched for each question, and the",
        "The files of this chat that you chose to search are read for each question, and the",
        T_CHIP,
        "says only this chat's own files are searched when Documents are off for the rest",
    ),
    (
        "the owner's always is said to be the person's",
        CHIP,
        '? "the owner of this Workbench has set"',
        '? "you have set"',
        T_CHIP,
        "says whose word",
    ),
    (
        "Send for this chat is offered where the person cannot",
        CHIP,
        '        {code === "outside_model" && view.outside?.canSendForThisChat && (',
        '        {code === "outside_model" && (',
        T_CHIP,
        "offers no way round the owner's or the person's own never",
    ),
    (
        "Stop sending sends again",
        CHIP,
        "                onClick={onStopSending}",
        "                onClick={onSendHere}",
        T_CHIP,
        "says when it is sending because the person chose to, and can stop",
    ),
    (
        "a person with no documents is led nowhere",
        CHIP,
        "{library.waiting === 0 && (",
        "{true && (",
        T_CHIP,
        "says the documents are still being read, and offers nothing to add",
    ),
    (
        "a person with no documents is told nothing",
        CHIP,
        ': "You have no documents to search yet."}',
        ': "You have no documents."}',
        T_CHIP,
        "says there is nothing to search yet, and leads to the screen that adds documents",
    ),
    (
        "the chat's own files are said where there are none",
        CHIP,
        '{chat.documents > 0 && " The files of this chat that you chose to search are searched too."}',
        '{" The files of this chat that you chose to search are searched too."}',
        T_CHIP,
        "says it searches the person's documents and sends the passages that match, and where to see which",
    ),
    (
        "a small window is not a warning",
        CHIP,
        '  const warn = code === "outside_model" || code === "model_unknown" || code === "window_too_small";',
        '  const warn = code === "outside_model" || code === "model_unknown";',
        T_CHIP,
        "every reason nothing was sent, in the server's words",
    ),
    (
        "the server's hint is left out",
        CHIP,
        '        {view.reason}\n        {view.hint && <span className="mt-1 block text-muted">{view.hint}</span>}\n        {code === "outside_model" && view.outside?.canSendForThisChat && (',
        '        {view.reason}\n        {code === "outside_model" && view.outside?.canSendForThisChat && (',
        T_CHIP,
        "gives the server's hint after its reason",
    ),
    (
        "Escape does not close the drawer",
        CHIP,
        'if (event.key === "Escape" && !event.nativeEvent.isComposing) {',
        'if (event.key === "Esc" && !event.nativeEvent.isComposing) {',
        T_CHIP,
        "closes with its button and with Escape",
    ),
    (
        "Tab is kept inside a drawer that is not a dialog",
        CHIP,
        'if (event.key !== "Tab" || !overlay || !sheet.current) return;',
        'if (event.key !== "Tab" || !sheet.current) return;',
        T_CHIP,
        "lets Tab leave a panel",
    ),
    (
        "the drawer does not take focus when it is a dialog",
        CHIP,
        "    if (openedAsOverlay.current) heading.current?.focus({ preventScroll: true });",
        "",
        T_CHIP,
        "is a dialog, takes focus, and keeps Tab inside",
    ),
    (
        "the drawer does not give focus back",
        CHIP,
        "        opener.focus({ preventScroll: true });",
        "        void opener;",
        T_CHIP,
        "gives focus back",
    ),
    (
        "the drawer does not read again when it opens",
        CHIP,
        "    reloaded.current = true;\n    void onReload();",
        "    reloaded.current = true;",
        T_CHIP,
        "is a named section that is a panel above the chat, reads again when it opens, and says nothing it has not read",
    ),
    (
        "a setting made in the drawer does not tell the chat",
        CHIP,
        "      onChat(await patch<Chat>(`/api/chats/${chatId}`, { settings }));",
        "      await patch<Chat>(`/api/chats/${chatId}`, { settings });",
        T_CHIP,
        "sends for this chat on a press, and takes the chat's new settings and the view again",
    ),
    (
        "a setting made in the drawer does not read the view again",
        CHIP,
        "      onChat(await patch<Chat>(`/api/chats/${chatId}`, { settings }));\n      await onReload();",
        "      onChat(await patch<Chat>(`/api/chats/${chatId}`, { settings }));",
        T_CHIP,
        "sends for this chat on a press, and takes the chat's new settings and the view again",
    ),
    (
        "a setting that was refused is not said",
        CHIP,
        "      setProblem(failureWords(failure));\n    } finally {\n      setBusy(false);",
        "      setProblem(null);\n    } finally {\n      setBusy(false);",
        T_CHIP,
        "puts the switch back and says why when the server refused",
    ),
    (
        "the drawer does not keep the keyboard where it was",
        CHIP,
        "    keepFocus();\n    setBusy(true);",
        "    setBusy(true);",
        T_CHIP,
        "keeps the keyboard where it was: held while the server answers, and focused again after",
    ),
    (
        "turning Documents off leaves the setting unsaid",
        CHIP,
        'void change({ documents: event.target.checked ? "on" : "off" });',
        'void change({ documents: event.target.checked ? "on" : null });',
        T_CHIP,
        "turns it off by saying so, not by leaving it unsaid",
    ),
    (
        "turning Documents only off says it is off",
        CHIP,
        "void change({ documentsOnly: event.target.checked ? true : null });",
        "void change({ documentsOnly: event.target.checked ? true : false });",
        T_CHIP,
        "turns off by clearing the setting, so the chat is as it was",
    ),
    (
        "the switch does not show what was just chosen",
        CHIP,
        '                        checked={busy && wanted.on !== undefined ? wanted.on : own.switch === "on"}',
        '                        checked={own.switch === "on"}',
        T_CHIP,
        "turns Documents on for the chat at once, and the chat's setting follows",
    ),
    (
        "the owner's off still has a switch",
        CHIP,
        '  const canSwitch = own !== null && own.reasonCode !== "install_off";',
        "  const canSwitch = own !== null;",
        T_CHIP,
        "offers the owner's off no switch and no counts: it is the owner's",
    ),
    (
        "the owner's off still has counts",
        CHIP,
        '              {own.reasonCode !== "install_off" && (\n                <section aria-labelledby={`${uid}-counts`}',
        "              {true && (\n                <section aria-labelledby={`${uid}-counts`}",
        T_CHIP,
        "offers the owner's off no switch and no counts: it is the owner's",
    ),
    (
        "a chat that sends nothing says what the room is",
        CHIP,
        '{own.sending && <p className="help">{budgetWords(own.budget)}</p>}',
        '<p className="help">{budgetWords(own.budget)}</p>',
        T_CHIP,
        "does not say what the room is for a chat that sends nothing",
    ),
    (
        "a chat with no files of its own lists none",
        CHIP,
        "{own.chatDocuments.length > 0 && (",
        "{true && (",
        T_CHIP,
        "shows no list when the chat has no such files",
    ),
    (
        "the owner is not told it is private",
        CHIP,
        "what a person keeps in their documents is private to them.",
        "what a person keeps in their documents is shared.",
        T_CHIP,
        "is told whether Documents was on, and that what is in them is private; nothing to change",
    ),
    (
        "the owner can manage someone else's documents",
        CHIP,
        '          {own && (\n            <button\n              type="button"\n              data-testid="documents-manage"',
        '          {true && (\n            <button\n              type="button"\n              data-testid="documents-manage"',
        T_CHIP,
        "is told whether Documents was on, and that what is in them is private; nothing to change",
    ),
    (
        "Try again does not try again",
        CHIP,
        "                    onClick={() => void onReload()}\n                  >\n                    Try again",
        "                    onClick={() => undefined}\n                  >\n                    Try again",
        T_CHIP,
        "says why it could not read, in the server's words, and tries again",
    ),
    (
        "the hint's button does not say what it does",
        CHIP,
        'aria-label="Turn on Documents"',
        'aria-label="Turn on"',
        T_CHIP,
        "turns Documents on from the hint",
    ),
    (
        "the hint's other button does not say what it hides",
        CHIP,
        'aria-label="Hide this hint"',
        'aria-label="Hide"',
        T_CHIP,
        "can be hidden, and its button says what it hides",
    ),
    (
        "the hint is not held while it turns Documents on",
        CHIP,
        '            data-testid="documents-hint-on"\n            aria-label="Turn on Documents"\n            disabled={busy}',
        '            data-testid="documents-hint-on"\n            aria-label="Turn on Documents"',
        T_CHIP,
        "is held while it turns Documents on",
    ),
    (
        "the hint does not say why it could not",
        CHIP,
        '      {problem && (\n        <p role="alert" data-testid="documents-hint-problem" className="text-xs text-warn">',
        '      {false && (\n        <p role="alert" data-testid="documents-hint-problem" className="text-xs text-warn">',
        T_CHIP,
        "says why it could not, as an alert, beside the hint",
    ),
    # === the passages under an answer ===================================================
    (
        "a passage is cited before the answer says which",
        PASSAGES,
        "const cited = source.cited === true;",
        "const cited = source.cited !== false;",
        T_MESSAGE,
        "are not marked as cited until the answer is done and says which",
    ),
    (
        "a cited passage does not say so in its name",
        PASSAGES,
        '${cited ? ", cited in the answer" : ""}`}',
        "`}",
        T_MESSAGE,
        "name the row that opens it",
    ),
    (
        "a cited passage is not marked",
        PASSAGES,
        '          {cited && (\n            <span\n              data-testid="passage-cited"',
        '          {false && (\n            <span\n              data-testid="passage-cited"',
        T_MESSAGE,
        "mark the passage the answer cited, with its number, and no other",
    ),
    (
        "a passage of this chat's own file is not said",
        PASSAGES,
        '{source.scope === "chat" && (',
        "{false && (",
        T_MESSAGE,
        "say a passage came from a file attached to this chat",
    ),
    (
        "a passage says nothing of the words it matched",
        PASSAGES,
        '          {matched && (\n            <p data-testid="passage-matched"',
        '          {false && (\n            <p data-testid="passage-matched"',
        T_MESSAGE,
        "show the start of each passage and the words it matched, behind the passage's own row",
    ),
    (
        "the owner's passages are listed with their files",
        PASSAGES,
        "const shown = passages.filter((p): p is PassageSource => !isHidden(p));",
        "const shown = passages as PassageSource[];",
        T_MESSAGE,
        "is a document, for each passage, and nothing of which",
    ),
    (
        "the passages are counted as documents one by one",
        PASSAGES,
        "const documents = new Set(shown.map((p) => p.doc_id)).size;",
        "const documents = shown.length;",
        T_MESSAGE,
        "count the documents the passages came from, not the passages again",
    ),
    (
        "the owner's one passage is said in the plural",
        PASSAGES,
        'Sent to the model: {hidden === 1 ? "1 passage" : `${hidden} passages`} of the',
        "Sent to the model: {`${hidden} passages`} of the",
        T_MESSAGE,
        "says one passage in the singular",
    ),
    (
        "the owner is not told what a person keeps is private",
        PASSAGES,
        "What a person keeps in their documents is private to them, even where you may read their",
        "What a person keeps in their documents is shared with you where you may read their",
        T_MESSAGE,
        "is a document, for each passage, and nothing of which",
    ),
    (
        "what was searched for is not said",
        PASSAGES,
        '          {searched && <li className="wrap-anywhere">{searched}</li>}\n          {how && <li>{how}</li>}',
        "          {how && <li>{how}</li>}",
        T_MESSAGE,
        "say what was searched for, how, and what did not fit",
    ),
    (
        "how the passages were found is not said",
        PASSAGES,
        "          {how && <li>{how}</li>}\n",
        "",
        T_MESSAGE,
        "say what was searched for, how, and what did not fit",
    ),
    (
        "what did not fit is not said",
        PASSAGES,
        '          {left && <li data-testid="passage-left-out">{left}</li>}\n',
        "",
        T_MESSAGE,
        "say what was searched for, how, and what did not fit",
    ),
    (
        "the reader's notes are not said",
        PASSAGES,
        "          {record?.notes.map((note, at) => (",
        "          {[].map((note: { code: string; message: string }, at: number) => (",
        T_MESSAGE,
        "say what was searched for, how, and what did not fit",
    ),
    (
        "a search that failed is said like one that was skipped",
        PASSAGES,
        'const failed = record.status === "failed";',
        "const failed = false;",
        T_MESSAGE,
        "is said in its own words, as an alert, and never as nothing matched",
    ),
    (
        "a search that was skipped is an alert",
        PASSAGES,
        '      role={failed ? "alert" : undefined}',
        '      role="alert"',
        T_MESSAGE,
        "is said in the server's words, in a notice, and never as nothing matched",
    ),
    (
        "the server's hint for a search is left out",
        PASSAGES,
        '        {hint && <span className="mt-1 block text-muted">{hint}</span>}',
        "",
        T_MESSAGE,
        "gives the server's hint for why documents were held back from a model outside the machines",
    ),
    (
        "a search with no documents leads nowhere",
        PASSAGES,
        '{onOpenDocuments && (canRebuild(record) || record.code === "no_documents") && (',
        "{onOpenDocuments && canRebuild(record) && (",
        T_MESSAGE,
        "leads a person whose chat answered with no documents to add some",
    ),
    (
        "a search that cannot be mended by Rebuild offers it",
        PASSAGES,
        '{onOpenDocuments && (canRebuild(record) || record.code === "no_documents") && (',
        "{onOpenDocuments && (",
        T_MESSAGE,
        "offers nothing for what Rebuild cannot mend",
    ),
    (
        "the way into the screen says the wrong thing",
        PASSAGES,
        '{canRebuild(record) ? " to rebuild the search" : " to add some"}',
        '{canRebuild(record) ? " to add some" : " to rebuild the search"}',
        T_MESSAGE,
        "offers to rebuild the search when that is what mends it, and opens the screen that does",
    ),
    (
        "Workbench's reply is not said to be the owner's",
        PASSAGES,
        '{mine ? "your" : "the person\'s"} documents',
        '"your" documents',
        T_MESSAGE,
        "is Workbench speaking for the person whose documents they are, to the owner reading the chat, who cannot ask",
    ),
    (
        "Workbench's reply can be asked of anyone",
        PASSAGES,
        "      {askAnyway && (\n        <div>",
        "      {true && (\n        <div>",
        T_MESSAGE,
        "is Workbench speaking for the person whose documents they are, to the owner reading the chat, who cannot ask",
    ),
    (
        "Ask the model anyway can be pressed twice",
        PASSAGES,
        '            data-testid="ask-anyway"\n            disabled={asking}',
        '            data-testid="ask-anyway"',
        T_MESSAGE,
        "is held while it asks, so it is not asked twice",
    ),
    (
        "Workbench's reply is not named",
        PASSAGES,
        '<p className="eyebrow text-muted">Workbench</p>',
        '<p className="eyebrow text-muted">Answer</p>',
        T_MESSAGE,
        "is shown plainly as Workbench speaking, not as a model's answer",
    ),
    (
        "Workbench's reply says it asked the model",
        PASSAGES,
        "so Workbench replied\n        itself and did not ask the model.",
        "so Workbench replied\n        itself and asked the model.",
        T_MESSAGE,
        "is shown plainly as Workbench speaking, not as a model's answer",
    ),
    (
        "a passage is marked with a span",
        MARKED,
        '<mark key={at} className="rounded-sm bg-info-bg px-0.5 font-medium text-strong">\n            {part.text}\n          </mark>',
        '<span key={at} className="rounded-sm bg-info-bg px-0.5 font-medium text-strong">\n            {part.text}\n          </span>',
        T_MESSAGE,
        "show the start of each passage and the words it matched, behind the passage's own row",
    ),
    # === the answer, as a message shows it ===============================================
    (
        "Workbench's own reply is shown as a model's answer",
        MESSAGE,
        'const ownReply = message.finish === "documents_only";',
        "const ownReply = false;",
        T_MESSAGE,
        "is shown plainly as Workbench speaking, not as a model's answer",
    ),
    (
        "Workbench's reply says a search that failed twice",
        MESSAGE,
        "    search && !ownReply && [",
        "    search && [",
        T_MESSAGE,
        "does not say a search that failed twice: Workbench's reply has already said it",
    ),
    (
        "a chat with no documents is not told it had none",
        MESSAGE,
        '["skipped", "failed", "no_documents"].includes(search.status)',
        '["skipped", "failed"].includes(search.status)',
        T_MESSAGE,
        "leads a person whose chat answered with no documents to add some",
    ),
    (
        "Workbench's reply is the owner's own",
        MESSAGE,
        "            mine={!readOnly}",
        "            mine",
        T_MESSAGE,
        "is Workbench speaking for the person whose documents they are, to the owner reading the chat, who cannot ask",
    ),
    (
        "the owner can ask the model anyway",
        MESSAGE,
        "              readOnly\n                ? undefined\n                : async () => {",
        "              false\n                ? undefined\n                : async () => {",
        T_MESSAGE,
        "is Workbench speaking for the person whose documents they are, to the owner reading the chat, who cannot ask",
    ),
    (
        "Ask the model anyway does not ask anyway",
        MESSAGE,
        "                        askAnyway: true,",
        "                        askAnyway: false,",
        T_MESSAGE,
        "can be asked of the model anyway: a new try that asks it, which the chat then shows",
    ),
    (
        "Ask the model anyway does not tell the chat",
        MESSAGE,
        "                      onChanged();\n                    } catch (error) {\n                      setProblem(error instanceof Error ? error.message : String(error));\n                    } finally {\n                      setActing(false);",
        "                    } catch (error) {\n                      setProblem(error instanceof Error ? error.message : String(error));\n                    } finally {\n                      setActing(false);",
        T_MESSAGE,
        "can be asked of the model anyway: a new try that asks it, which the chat then shows",
    ),
    (
        "Ask the model anyway does not say why it could not",
        MESSAGE,
        "                      setProblem(error instanceof Error ? error.message : String(error));\n                    } finally {\n                      setActing(false);",
        "                      setProblem(null);\n                    } finally {\n                      setActing(false);",
        T_MESSAGE,
        "says why it could not ask, in the server's words, and keeps the reply",
    ),
    (
        "nothing matched is not said in Sources",
        MESSAGE,
        "{(message.sources.length > 0 || message.searches > 0 || nothing) && (",
        "{(message.sources.length > 0 || message.searches > 0) && (",
        T_MESSAGE,
        "says nothing matched in Sources, quietly, once",
    ),
    (
        "nothing matched is said twice",
        MESSAGE,
        "          {nothing && search && (",
        "          {false && search && (",
        T_MESSAGE,
        "says nothing matched in Sources, quietly, once",
    ),
    (
        "a chat is exported without its passages",
        EXPORT,
        "            isPassageSource(s)\n              ? oneLine(passageLine(s))\n              : isDocumentSource(s)",
        "            isDocumentSource(s)",
        T_MESSAGE,
        "lists each passage that was sent: the file and the place, and whether the answer cited it",
    ),
    # === the chat that holds the chip, the hint and the composer ===========================
    (
        "a chat's documents are not read again when its model changes",
        CHAT,
        '`${detail?.chat.model ?? ""}|${detail?.chat.settings.documents ?? ""}|',
        '`${""}|${detail?.chat.settings.documents ?? ""}|',
        T_CHAT,
        "reads the documents again when the chat's model changes, and when a documents setting does, and not when something else does",
    ),
    (
        "a chat's documents are not read again when Documents is turned on",
        CHAT,
        '|${detail?.chat.settings.documents ?? ""}|${detail?.chat.settings.documentsOnly ?? ""}|',
        '|${""}|${detail?.chat.settings.documentsOnly ?? ""}|',
        T_CHAT,
        "reads the documents again when the chat's model changes, and when a documents setting does, and not when something else does",
    ),
    (
        "a chat's documents are not read again when Documents only is turned on",
        CHAT,
        '|${detail?.chat.settings.documentsOnly ?? ""}|${detail?.chat.settings.documentsExternal ?? ""}`',
        '|${""}|${detail?.chat.settings.documentsExternal ?? ""}`',
        T_CHAT,
        "reads the documents again when the chat's model changes, and when a documents setting does, and not when something else does",
    ),
    (
        "a chat's documents are not read again when Send for this chat is chosen",
        CHAT,
        '|${detail?.chat.settings.documentsExternal ?? ""}`',
        '|${""}`',
        T_CHAT,
        "reads the documents again when the chat's model changes, and when a documents setting does, and not when something else does",
    ),
    (
        "a chat's documents are not read again after a message is sent",
        CHAT,
        "            void documents.reload();\n",
        "",
        T_CHAT,
        "reads the documents again after a message is sent",
    ),
    (
        "the chip is not a chip for someone else's chat",
        CHAT,
        "          {renaming === null && !confirmDelete && (\n            <DocumentsChip",
        "          {renaming === null && !confirmDelete && !chat.readOnly && (\n            <DocumentsChip",
        T_CHAT,
        "is a chip even in someone else's chat, where it says whether Documents was on and nothing more",
    ),
    (
        "the chip says it has nothing when a later read fails",
        SAVED,
        "  if (!view) {\n    return failed",
        '  if (failed) return { label: "Documents: not available", short: "", tone: "warn" };\n  if (!view) {\n    return failed',
        T_CHAT,
        "keeps what it said when a later read fails, and says why in the drawer",
    ),
    (
        "the chip opens the drawer and never closes it",
        CHAT,
        'onToggle={() => setPanel((now) => (now === "documents" ? null : "documents"))}',
        'onToggle={() => setPanel("documents")}',
        T_CHAT,
        "opens its drawer on a press, reads the documents again, and closes it on a second press",
    ),
    (
        "the drawer is open with the settings",
        CHAT,
        '      {panel === "documents" && (\n        <DocumentsDrawer',
        '      {(panel === "documents" || panel === "settings") && (\n        <DocumentsDrawer',
        T_CHAT,
        "is one panel at a time with the chat's settings and Memory, whichever is opened last",
    ),
    (
        "Manage documents goes nowhere",
        CHAT,
        "          onManage={onOpenDocuments}\n          onClose={() => setPanel(null)}",
        "          onManage={() => undefined}\n          onClose={() => setPanel(null)}",
        T_CHAT,
        "opens the Documents screen from the drawer's Manage documents",
    ),
    (
        "the drawer does not tell the chat what it changed",
        CHAT,
        "          onChat={(next) =>\n            update((shown) => ({ ...shown, chat: { ...next, running: shown.chat.running } }))\n          }\n          onReload={documents.reload}",
        "          onChat={() => undefined}\n          onReload={documents.reload}",
        T_CHAT,
        "is told the chat changed, and reads again, when the drawer's switch is turned",
    ),
    (
        "the hint comes back for a person who hid it",
        CHAT,
        "useState(() => hintHidden(me.sub));",
        "useState(false);",
        T_CHAT,
        "can be hidden, for good, by the person who hid it",
    ),
    (
        "the hint looks for another person's choice to hide it",
        CHAT,
        "const [hintGone, setHintGone] = useState(() => hintHidden(me.sub));",
        'const [hintGone, setHintGone] = useState(() => hintHidden("everyone"));',
        T_CHAT,
        "can be hidden, for good, by the person who hid it",
    ),
    (
        "hiding the hint is for no one",
        CHAT,
        "                  hideHint(me.sub);\n",
        '                  hideHint("everyone");\n',
        T_CHAT,
        "can be hidden, for good, by the person who hid it",
    ),
    (
        "hiding the hint is not kept",
        CHAT,
        "                  hideHint(me.sub);\n",
        "",
        T_CHAT,
        "can be hidden, for good, by the person who hid it",
    ),
    (
        "the hint turns Documents off",
        CHAT,
        '      await saveSettings({ documents: "on" });',
        '      await saveSettings({ documents: "off" });',
        T_CHAT,
        "turns Documents on in this chat, and is gone once it is",
    ),
    (
        "the hint does not read the documents again",
        CHAT,
        '      await saveSettings({ documents: "on" });\n      await documents.reload();',
        '      await saveSettings({ documents: "on" });',
        T_CHAT,
        "turns Documents on in this chat, and is gone once it is",
    ),
    (
        "the hint of a chat does not say why it could not",
        CHAT,
        "      setHintProblem(failureWords(failure));",
        "      setHintProblem(null);",
        T_CHAT,
        "says why it could not turn Documents on, and stays",
    ),
    (
        "the hint of a chat is not held while it turns Documents on",
        CHAT,
        "                busy={turningOn}",
        "                busy={false}",
        T_CHAT,
        "is held while it turns Documents on",
    ),
    (
        "the owner's reading of a chat is led into the screen",
        CHAT,
        "documents={chat.readOnly ? undefined : { onManage: onOpenDocuments }}",
        "documents={{ onManage: onOpenDocuments }}",
        T_CHAT,
        "offers the owner reading someone's chat no way into the screen",
    ),
    # === the composer and the files in it ================================================
    (
        "the hint is shown with files attached",
        COMPOSER,
        "{pending.length === 0 && documentsHint}",
        "{documentsHint}",
        T_COMPOSER,
        "is not there while files are attached: the composer is full enough, and the person is in the middle of something",
    ),
    (
        "the hint is not shown when nothing is attached",
        COMPOSER,
        "{pending.length === 0 && documentsHint}",
        "{pending.length > 0 && documentsHint}",
        T_COMPOSER,
        "is under the tools while nothing is attached",
    ),
    (
        "the offer to search is not its own button",
        CARD,
        "data-testid={`document-offer-${offerKey(offer)}`}",
        'data-testid={`document-offer-${offer.action === "readAll" ? "all" : "first"}`}',
        T_COMPOSER,
        "is the last of the buttons on a file that is longer than is read at once: read it all, the first part, or search it",
    ),
    (
        "a choice to search cannot be taken back",
        CARD,
        'const searching = decision?.chosen?.mode === "search";',
        "const searching = false;",
        T_COMPOSER,
        "is taken back by Choose again, and the file is the person's to choose for again",
    ),
    # === the top bar and the pages =======================================================
    (
        "an address is taken for the Documents page when it only starts like it",
        APP,
        "return /^\\/documents\\/?$/.test(path);",
        "return /^\\/documents/.test(path);",
        T_APP,
        "what an address says is the Documents page",
    ),
    (
        "the Documents page is named another way in the tab",
        APP,
        '  documents: "Documents",',
        '  documents: "Docs",',
        T_APP,
        "opens at /documents, marks the page a person is on, and names the tab",
    ),
    (
        "the Documents page is at another address",
        APP,
        'window.history.pushState(null, "", "/documents");',
        'window.history.pushState(null, "", "/docs");',
        T_APP,
        "opens at /documents, marks the page a person is on, and names the tab",
    ),
    (
        "the Documents page does not mark its tab",
        APP,
        'aria-current={view === "documents" ? "page" : undefined}',
        "aria-current={undefined}",
        T_APP,
        "opens at /documents, marks the page a person is on, and names the tab",
    ),
    (
        "the Documents page does not name the tab",
        APP,
        '        view !== "documents" &&\n',
        "",
        T_APP,
        "opens at /documents, marks the page a person is on, and names the tab",
    ),
    (
        "the Documents page does not take the keyboard",
        APP,
        '      view === "media" ||\n      view === "documents" ||\n',
        '      view === "media" ||\n',
        T_APP,
        "moves focus to its heading when a person opens it, and not when the address did",
    ),
    (
        "Back to chat goes to no chat",
        APP,
        "<Documents onClose={() => open(chatId)}",
        "<Documents onClose={() => open(null)}",
        T_APP,
        "goes back to the chat it came from, with Back to chat, and to the start page when there was none",
    ),
    (
        "a chat that is opened leaves Documents open",
        APP,
        "    setChatId(id);\n    setMedia(null);\n    setMemoryOpen(false);\n    setDocumentsOpen(false);",
        "    setChatId(id);\n    setMedia(null);\n    setMemoryOpen(false);",
        T_APP,
        "goes back to the chat it came from, with Back to chat, and to the start page when there was none",
    ),
    (
        "Memory opens beside Documents",
        APP,
        "    setMemoryOpen(true);\n    setDocumentsOpen(false);",
        "    setMemoryOpen(true);",
        T_APP,
        "gives way to another page's tab, and is the page again when that page is left",
    ),
    (
        "the browser's back button does not leave Documents",
        APP,
        "      setDocumentsOpen(documentsFromPath(window.location.pathname));",
        "",
        T_APP,
        "follows the browser's back button",
    ),
    (
        "Manage documents opens nothing from a chat",
        APP,
        "            onOpenDocuments={openDocuments}",
        "            onOpenDocuments={() => undefined}",
        T_APP,
        "opens from a chat's own Manage documents",
    ),
    (
        "Ask about your documents turns Documents off",
        APP,
        '{ settings: { documents: "on" } }',
        '{ settings: { documents: "off" } }',
        T_APP,
        "starts a chat with Documents on from Ask about your documents, and opens it",
    ),
    (
        "Ask about your documents does not say when Documents could not be turned on",
        APP,
        "`The chat was made, but Documents could not be turned on in it: ${",
        "`The chat was made: ${",
        T_APP,
        "opens the chat all the same, and says so, when Documents could not be turned on in it",
    ),
    (
        "Ask about your documents says nothing when no chat could be made",
        APP,
        "      setActionError(error instanceof Error ? error.message : String(error));\n    } finally {\n      newPending.current = false;\n    }\n  }, [open]);\n\n  useEffect(() => {",
        "      void error;\n    } finally {\n      newPending.current = false;\n    }\n  }, [open]);\n\n  useEffect(() => {",
        T_APP,
        "stays on the screen, and says why, when the chat could not be made",
    ),
    (
        "six pages in the top bar do not scroll on a phone",
        APP,
        "max-sm:flex-nowrap max-sm:overflow-x-auto max-sm:[&>*]:shrink-0",
        "max-sm:flex-nowrap max-sm:[&>*]:shrink-0",
        T_APP,
        "keeps to one row that scrolls sideways below 640 px, and wraps above it",
    ),
    (
        "the page a person is on is not brought into view",
        APP,
        '\'nav[aria-label="Workbench"] [aria-current="page"]\'',
        '\'nav[aria-label="Workbench"] [aria-current="false"]\'',
        T_APP,
        "brings the page a person is on into view, whether they opened it or the address did",
    ),
    (
        "a browser that cannot scroll an element into view stops the page",
        APP,
        '?.scrollIntoView?.({ block: "nearest", inline: "nearest" });',
        '?.scrollIntoView({ block: "nearest", inline: "nearest" });',
        T_APP,
        "is not stopped by a browser that cannot scroll an element into view",
    ),
    (
        "the top bar does not hold the words a tab hides from the eye",
        APP,
        'className="relative flex flex-wrap items-center border-b border-line',
        'className="flex flex-wrap items-center border-b border-line',
        T_APP,
        "keeps to one row that scrolls sideways below 640 px, and wraps above it",
    ),
]

# (label, file, before, after, test file, a fragment of the test's name)
PYTHON_CASES: list[tuple[str, str, str, str, str, str]] = [
    # === the page and the server agree (Python) ==========================================
    (
        "the page does not read the search of a file's decision",
        TYPES,
        "  search?: FileSearch | null;\n  room: {",
        "  room: {",
        PAGE_AGREES,
        "test_a_decision_has_the_fields_the_page_reads",
    ),
    (
        "the page does not read the search of a file in the list",
        TYPES,
        "   * getting on; null for any other. */\n  search?: FileSearch | null;\n}",
        "   * getting on; null for any other. */\n}",
        PAGE_AGREES,
        "test_a_file_in_the_list_has_the_fields_the_page_reads",
    ),
    (
        "the page has no words for a file that is searched",
        TYPES,
        '  | "native"\n  | "search"\n  | "choose"',
        '  | "native"\n  | "choose"',
        PAGE_AGREES,
        "test_every_kind_of_decision_is_one_the_page_has_words_for",
    ),
    (
        "the page has no state for a document that is held for meaning",
        TYPES,
        '"queued" | "reading" | "searchable" | "embedding" | "ready" | "skipped" | "failed";',
        '"queued" | "reading" | "searchable" | "ready" | "skipped" | "failed";',
        SAVED_AGREES,
        "test_a_saved_document_has_the_fields_the_page_reads_in_every_state_it_can_be_in",
    ),
    (
        "the page misnames a field of a document",
        TYPES,
        "  passages: number;\n  embedded: number;",
        "  passages: number;\n  embedded2: number;",
        SAVED_AGREES,
        "test_a_saved_document_has_the_fields_the_page_reads_in_every_state_it_can_be_in",
    ),
    (
        "the page misnames what an added document says",
        TYPES,
        "  unchanged: boolean;",
        "  unchangedFile: boolean;",
        SAVED_AGREES,
        "test_a_file_that_was_added_has_the_fields_the_page_reads_and_so_has_a_refusal",
    ),
    (
        "the page misnames a refusal",
        TYPES,
        "export interface AddRefusal {\n  name: string;\n  status: number;",
        "export interface AddRefusal {\n  name: string;\n  code: number;",
        SAVED_AGREES,
        "test_a_file_that_was_added_has_the_fields_the_page_reads_and_so_has_a_refusal",
    ),
    (
        "the page misnames whether the owner reads chats",
        TYPES,
        "  prefs: DocumentsPrefs;\n  installEnabled: boolean;\n  ownerReadsChats: boolean;",
        "  prefs: DocumentsPrefs;\n  installEnabled: boolean;\n  ownerCanRead: boolean;",
        SAVED_AGREES,
        "test_the_screens_data_has_the_fields_the_page_reads_down_to_each_number",
    ),
    (
        "the page misnames a total",
        TYPES,
        "    passagesKept: number;",
        "    passagesHeld: number;",
        SAVED_AGREES,
        "test_the_screens_data_has_the_fields_the_page_reads_down_to_each_number",
    ),
    (
        "the page misnames a limit",
        TYPES,
        "    readTimeoutSeconds: number;",
        "    readTimeout: number;",
        SAVED_AGREES,
        "test_the_screens_data_has_the_fields_the_page_reads_down_to_each_number",
    ),
    (
        "the page misnames whose word a choice is",
        TYPES,
        '    externalSetBy: "owner" | "you" | "default";\n  };\n}',
        '    externalChosenBy: "owner" | "you" | "default";\n  };\n}',
        SAVED_AGREES,
        "test_the_screens_data_has_the_fields_the_page_reads_down_to_each_number",
    ),
    (
        "the page misnames how much did not fit",
        TYPES,
        "  didNotFit: number;\n  budget: {",
        "  leftOut: number;\n  budget: {",
        SAVED_AGREES,
        "test_a_question_that_was_tried_has_the_fields_the_page_reads_whatever_it_found",
    ),
    (
        "the page misnames where a word was found",
        TYPES,
        "inHeading: string[] };",
        "inTitle: string[] };",
        SAVED_AGREES,
        "test_a_question_that_was_tried_has_the_fields_the_page_reads_whatever_it_found",
    ),
    (
        "the page misnames a chat's own documents",
        TYPES,
        "  chatDocuments: SavedDocument[];",
        "  ownDocuments: SavedDocument[];",
        SAVED_AGREES,
        "test_a_chats_documents_have_the_fields_the_page_reads_in_every_state",
    ),
    (
        "the page has no words for a window too small",
        TYPES,
        '  "install_off" | "no_model" | "model_unknown" | "window_too_small" | "outside_model" | "chat_off";',
        '  "install_off" | "no_model" | "model_unknown" | "outside_model" | "chat_off";',
        SAVED_AGREES,
        "test_every_reason_a_chat_sends_nothing_is_one_the_page_has_words_for",
    ),
    (
        "the page misnames what the owner may read of a chat",
        TYPES,
        'export interface ChatDocumentsReadOnly {\n  chatId: string;\n  readOnly: true;\n  switch: "on" | "off";\n  documentsOnly: boolean;',
        'export interface ChatDocumentsReadOnly {\n  chatId: string;\n  readOnly: true;\n  switch: "on" | "off";\n  onlyDocuments: boolean;',
        SAVED_AGREES,
        "test_the_owner_reading_a_chat_has_the_fields_the_page_reads_and_nothing_of_the_documents",
    ),
    (
        "the page misnames a setting of a chat",
        TYPES,
        "  documentsExternal?: true | null;",
        "  documentsOutside?: true | null;",
        SAVED_AGREES,
        "test_a_chats_settings_are_the_ones_the_page_reads_and_writes",
    ),
    (
        "the page misnames a field of a passage",
        TYPES,
        "  chunk_ids: number[];\n  tokens: number;\n  terms: string[];",
        "  piece_ids: number[];\n  tokens: number;\n  terms: string[];",
        SAVED_AGREES,
        "test_a_passage_under_an_answer_has_the_fields_the_page_reads",
    ),
    (
        "the page misnames a field of a search record",
        TYPES,
        "  askedAnyway: boolean;",
        "  askedRegardless: boolean;",
        SAVED_AGREES,
        "test_a_search_record_has_the_fields_the_page_reads_in_each_thing_it_can_say",
    ),
    (
        "the page has no words for a chat with no documents",
        TYPES,
        'export type SearchStatus = "used" | "nothing_matched" | "skipped" | "failed" | "no_documents";',
        'export type SearchStatus = "used" | "nothing_matched" | "skipped" | "failed";',
        SAVED_AGREES,
        "test_every_status_a_search_can_come_to_is_one_the_page_has_words_for",
    ),
    (
        "the page does not know a search is a stage",
        TYPES,
        'phase?: "started" | "finished" | "approval" | "reading" | "searching";',
        'phase?: "started" | "finished" | "approval" | "reading";',
        SAVED_AGREES,
        "test_what_the_page_says_while_an_answer_waits_is_what_the_server_sends",
    ),
]

# (label, file, before, after, the Chrome check that must fail)
CHROME_CASES: list[tuple[str, str, str, str, str]] = [
    # === what only a real browser sees ===================================================
    (
        "a file is sent in a field the server does not read",
        ADD,
        'form.append("files", file, file.name);',
        'form.append("file", file, file.name);',
        LIBRARY_BROWSER,
    ),
    (
        "the list does not read again while documents are read",
        SAVED_HOOK,
        "const watching = data?.documents.some(isPending) ?? false;",
        "const watching = (data?.documents.some(isPending) ?? false) && false;",
        LIBRARY_BROWSER,
    ),
    (
        "Test a question is asked at another address",
        TRY,
        'post<TestedQuestion>("/api/documents/test", { question: asked })',
        'post<TestedQuestion>("/api/documents/tes", { question: asked })',
        LIBRARY_BROWSER,
    ),
    (
        "the passages found are not marked where they stand",
        TRY,
        "words={[...hit.why.terms, ...hit.why.phrases]}",
        "words={[]}",
        LIBRARY_BROWSER,
    ),
    (
        "the hint turns Documents off",
        CHAT,
        '      await saveSettings({ documents: "on" });',
        '      await saveSettings({ documents: "off" });',
        LIBRARY_BROWSER,
    ),
    (
        "the drawer's switch turns Documents the wrong way",
        CHIP,
        'void change({ documents: event.target.checked ? "on" : "off" });',
        'void change({ documents: event.target.checked ? "off" : "on" });',
        LIBRARY_BROWSER,
    ),
    (
        "an answer does not mark the passage it cited",
        PASSAGES,
        "const cited = source.cited === true;",
        "const cited = false;",
        LIBRARY_BROWSER,
    ),
    (
        "a passage under an answer is not marked where it stands",
        PASSAGES,
        "<Marked text={source.snippet} words={[...source.terms, ...source.phrases]} />",
        "<Marked text={source.snippet} words={[]} />",
        LIBRARY_BROWSER,
    ),
    (
        "Ask the model anyway does not ask anyway",
        MESSAGE,
        "                        askAnyway: true,",
        "                        askAnyway: false,",
        LIBRARY_BROWSER,
    ),
    (
        "a removed document is removed at another address",
        SCREEN,
        "() => del(`/api/documents/${encodeURIComponent(doc.id)}`),",
        "() => del(`/api/document/${encodeURIComponent(doc.id)}`),",
        LIBRARY_BROWSER,
    ),
    (
        "the Documents tab opens no page",
        APP,
        "    setDocumentsOpen(true);\n    setMemoryOpen(false);",
        "    setDocumentsOpen(false);\n    setMemoryOpen(false);",
        LIBRARY_BROWSER,
    ),
    (
        "six pages in the top bar of a phone are not one row that scrolls",
        APP,
        "max-sm:flex-nowrap max-sm:overflow-x-auto max-sm:[&>*]:shrink-0",
        "max-sm:[&>*]:shrink-0",
        READING_BROWSER,
    ),
    (
        "the top bar's hidden words widen the page on a phone",
        APP,
        'className="relative flex flex-wrap items-center border-b border-line',
        'className="flex flex-wrap items-center border-b border-line',
        CONVENIENCES_BROWSER,
    ),
    (
        "the title has no row of its own on a tablet",
        CHAT,
        'max-lg:basis-full"\n              data-testid="chat-title"',
        'max-sm:basis-full md:max-lg:basis-full"\n              data-testid="chat-title"',
        READING_BROWSER,
    ),
]

ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("EUGENE_PLEXUS_")},
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
    "FORCE_COLOR": "0",
    "NO_COLOR": "1",
}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def vitest(test: str, name: str) -> tuple[bool, str, int]:
    """(passed, output, how many tests ran and passed) for one named test."""
    result = subprocess.run(
        ["node", str(VITEST), "run", test, "-t", name],
        cwd=WEB,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    output = ANSI.sub("", result.stdout + result.stderr)
    ran = re.search(r"Tests\s+(?:\d+ failed \| )?(\d+) passed", output)
    return result.returncode == 0, output, int(ran.group(1)) if ran else 0


def pytest(test: str, name: str) -> tuple[bool, str, int]:
    """(passed, output, how many tests ran) for the Python tests whose name has `name`."""
    result = subprocess.run(
        [str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider", test, "-k", name],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    output = ANSI.sub("", result.stdout + result.stderr)
    ran = re.search(r"(\d+) passed", output)
    return result.returncode == 0, output, int(ran.group(1)) if ran else 0


def broken(output: str) -> bool:
    """A mutation that stops the code compiling or importing proves nothing."""
    markers = (
        "Transform failed",
        "SyntaxError",
        "Failed to resolve import",
        "Cannot find module",
        "Unexpected token",
        "ERROR: Expected",
    )
    return any(m in output for m in markers)


def anchors() -> None:
    every = [
        *((label, name, before) for label, name, before, _, _, _ in CASES),
        *((label, name, before) for label, name, before, _, _, _ in PYTHON_CASES),
        *((label, name, before) for label, name, before, _, _ in CHROME_CASES),
    ]
    bad = []
    for label, name, before in every:
        found = (ROOT / name).read_text(encoding="utf-8").count(before)
        if found != 1:
            bad.append(f"{name}: the anchor for '{label}' is in {found} places; fix it.")
    if bad:
        raise SystemExit("\n".join(bad))
    print(
        f"{len(CASES)} anchors, {len(PYTHON_CASES)} for Python and {len(CHROME_CASES)} for "
        f"Chrome, each in one place."
    )


def build() -> tuple[bool, str]:
    result = subprocess.run(
        [NPM, "run", "build"],
        cwd=WEB,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    return result.returncode == 0, ANSI.sub("", result.stdout + result.stderr)


def why(output: str) -> str:
    """The first line a failed Chrome run says, so the log shows what caught each case."""
    said = [
        re.sub(r"^(E\s+)?(AssertionError:\s*)?", "", line.strip()) for line in output.splitlines()
    ]
    found = [
        line[:160] for line in said if line.startswith("FAIL") and not line.startswith("FAILED")
    ]
    found += [
        line[:160]
        for line in said
        if re.search(r"Timeout \d+ms exceeded|\w+Error(?: \[\w+\])?:", line)
        and not line.startswith("FAILED")
    ]
    return " | ".join(found[:2]) if found else "(no failing line found)"


def chrome(test: str) -> tuple[bool, str]:
    result = subprocess.run(
        [str(PYTHON), "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", test],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=500,
    )
    return result.returncode == 0, ANSI.sub("", result.stdout + result.stderr)


def swapped(path: Path, original: bytes, before: str, after: str) -> None:
    path.write_bytes(original.decode("utf-8").replace(before, after).encode("utf-8"))


def main() -> int:
    # What a failing test prints may not be in the console's code page.
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchors", action="store_true")
    parser.add_argument("--python", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--chrome-only", action="store_true")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--only", action="append", default=[])
    args = parser.parse_args()

    def wanted(label: str, name: str) -> bool:
        return (not args.label or any(t in label for t in args.label)) and (
            not args.only or any(t in name for t in args.only)
        )

    cases = [] if args.chrome_only else [c for c in CASES if wanted(c[0], c[1])]
    python_cases = (
        [c for c in PYTHON_CASES if wanted(c[0], c[1])]
        if args.python and not args.chrome_only
        else []
    )
    chrome_cases = [c for c in CHROME_CASES if wanted(c[0], c[1])]
    anchors()
    if args.anchors:
        return 0

    checks = sorted({(test, name) for _, _, _, _, test, name in cases})
    missing = []
    for test, name in checks:
        passed, output, ran = vitest(test, name)
        if not passed or ran == 0:
            print(output[-1500:])
            missing.append(f"{test}: {name}")
    if missing:
        raise SystemExit(
            "Baseline failed or ran nothing, so no files were changed:\n  " + "\n  ".join(missing)
        )
    py_checks = sorted({(test, name) for _, _, _, _, test, name in python_cases})
    for test, name in py_checks:
        passed, output, ran = pytest(test, name)
        if not passed or ran == 0:
            print(output[-3000:])
            raise SystemExit(f"Baseline failed or ran nothing ({test}: {name}); no files changed.")
    print(
        f"Baseline: {len(checks)} named checks and {len(py_checks)} Python checks pass and run.",
        flush=True,
    )

    backup = Path(tempfile.mkdtemp(prefix="workbench-library-page-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for index, (label, name, before, after, test, check) in enumerate(cases):
        path = ROOT / name
        original = path.read_bytes()
        (backup / f"{index}-{path.name}").write_bytes(original)
        try:
            swapped(path, original, before, after)
            passed, output, _ = vitest(test, check)
            ok = not passed and not broken(output)
            print(f"{'CAUGHT' if ok else 'ESCAPED'}: {label}", flush=True)
            if not ok:
                print(output[-2500:])
            caught += ok
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
    total = len(cases)

    for index, (label, name, before, after, test, check) in enumerate(python_cases):
        path = ROOT / name
        original = path.read_bytes()
        (backup / f"p{index}-{path.name}").write_bytes(original)
        try:
            swapped(path, original, before, after)
            passed, output, _ = pytest(test, check)
            ok = not passed
            print(f"{'CAUGHT' if ok else 'ESCAPED'} (Python): {label}", flush=True)
            if not ok:
                print(output[-2500:])
            caught += ok
        finally:
            path.write_bytes(original)
            assert path.read_bytes() == original
    total += len(python_cases)

    if args.browser or args.chrome_only:
        if not os.getenv("WORKBENCH_PLAYWRIGHT"):
            raise SystemExit("--browser needs WORKBENCH_PLAYWRIGHT.")
        # The checks must pass on the page as it is before anything is broken.
        built, output = build()
        if not built:
            print(output[-3000:])
            raise SystemExit("The page does not build as it is; no files changed.")
        for test in sorted({c[4] for c in chrome_cases}):
            passed, output = chrome(test)
            if not passed:
                print(output[-3000:])
                raise SystemExit(f"Chrome baseline failed ({test}); no files changed.")
        print(f"Chrome baseline: {len({c[4] for c in chrome_cases})} checks pass.", flush=True)
        for index, (label, name, before, after, test) in enumerate(chrome_cases):
            path = ROOT / name
            original = path.read_bytes()
            (backup / f"b{index}-{path.name}").write_bytes(original)
            try:
                swapped(path, original, before, after)
                built, output = build()
                if not built:
                    print(f"BROKEN (does not build, proves nothing): {label}", flush=True)
                    print(output[-1500:], flush=True)
                    continue
                passed, output = chrome(test)
                ok = not passed and "PASS:" not in output
                print(f"{'CAUGHT' if ok else 'ESCAPED'} (Chrome): {label}", flush=True)
                print(f"    {why(output)}" if ok else output[-2500:], flush=True)
                caught += ok
            finally:
                path.write_bytes(original)
                assert path.read_bytes() == original
        total += len(chrome_cases)
        built, output = build()
        if not built:
            print(output[-3000:])
            raise SystemExit("The restored page does not build.")

    for test, name in checks:
        passed, output, ran = vitest(test, name)
        if not passed or ran == 0:
            print(output[-3000:])
            raise SystemExit(f"Restored baseline failed ({test}: {name}).")
    print(f"{caught}/{total} caught; restored baseline passes.")
    return 0 if caught == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
