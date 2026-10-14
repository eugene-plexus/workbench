"""Prove the Documents page's checks are observed (MR2, the page side).

Each case breaks one thing in the page's code, and the test that is named for it
must fail. Files are restored from exact bytes kept beside the run, never from
Git. A mutation that stops the code compiling does not count as a caught defect,
and neither does a test name that runs nothing.

  python scripts/check-documents-page-sabotage.py              # every unit-test case
  python scripts/check-documents-page-sabotage.py --anchors    # only check each anchor is unique
  python scripts/check-documents-page-sabotage.py --python     # also the cases the Python checks catch
  python scripts/check-documents-page-sabotage.py --browser    # also the Chrome cases (needs WORKBENCH_PLAYWRIGHT)
  python scripts/check-documents-page-sabotage.py --chrome-only    # only the Chrome cases
  python scripts/check-documents-page-sabotage.py --label TEXT ...   # only the cases whose label has TEXT

The Chrome cases rebuild the page for each break and run
`tests/test_documents_browser.py` (or, for the contrast of the chips and for Send
staying on a phone's screen, `tests/test_reading_browser.py`); the page is rebuilt
from the restored source at the end. Run it with every ambient EUGENE_PLEXUS_*
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
FILES = SRC + "lib/useChatFiles.ts"
TYPES = SRC + "lib/types.ts"
EXPORT = SRC + "lib/conveniences.ts"
CHIPS = SRC + "components/Attachments.tsx"
TEXT = SRC + "components/DocumentText.tsx"
COMPOSER = SRC + "components/Composer.tsx"
MESSAGE = SRC + "components/MessageView.tsx"

DOCUMENTS_BROWSER = "tests/test_documents_browser.py"
READING_BROWSER = "tests/test_reading_browser.py"
PAGE_AGREES = "tests/test_documents_page.py"

T_API = "src/lib/api.test.ts"
T_LIB = "src/lib/lib.test.ts"
T_DOCS = "src/lib/documents.test.ts"
T_FILES = "src/lib/useChatFiles.test.tsx"
T_CHIPS = "src/components/Attachments.test.tsx"
T_COMPOSER = "src/components/ComposerDocuments.test.tsx"
T_MESSAGE = "src/components/MessageDocuments.test.tsx"
T_WORDS = "src/words.test.ts"
T_CONTROLS = "src/documentsControls.test.tsx"

# (label, file, before, after, test file, a fragment of the test's name)
CASES: list[tuple[str, str, str, str, str, str]] = [
    # --- what the server said, and what the page asks for ----------------------
    (
        "the text of a file is asked for without the request secret",
        "web/src/lib/api.ts",
        'async function textOf(path: string): Promise<string> {\n  const response = await checked(\n    await fetch(path, { credentials: "same-origin", headers: headers() }),',
        'async function textOf(path: string): Promise<string> {\n  const response = await checked(\n    await fetch(path, { credentials: "same-origin" }),',
        "src/lib/api.test.ts",
        "is asked for by the file's id, with the request secret",
    ),
    (
        "the text of a file is asked for at the file's own address",
        API,
        "`/api/files/${encodeURIComponent(id)}/text`",
        "`/api/files/${encodeURIComponent(id)}`",
        T_API,
        "is asked for by the file's id, with the request secret",
    ),
    (
        "the working line says the model is awaited while files are read",
        "web/src/lib/words.ts",
        'return progress.phase === "searching" ? "Searching your documents…" : "Reading your files…";',
        'return "Waiting for the model…";',
        "src/lib/lib.test.ts",
        "says the files are being read, not that the model is awaited",
    ),
    # --- where a file stands ---------------------------------------------------
    (
        "a file that is decided can go whatever its decision says",
        DOCS,
        "  file.decision ? file.decision.send : !isReading(file);",
        "  !isReading(file);",
        T_DOCS,
        "goes when its decision says it can",
    ),
    (
        "a file whose text is read again is not being read",
        DOCS,
        '  file.reading?.state === "reading" || file.decision?.kind === "pending";',
        '  file.reading?.state === "reading";',
        T_DOCS,
        "is being read until its reading ends, or while its text is read again",
    ),
    (
        "a skipped file looks like one that failed",
        DOCS,
        '        return file.reading?.state === "skipped" ? "skipped" : "failed";',
        '        return "failed";',
        T_DOCS,
        "has the look",
    ),
    # --- what Workbench found, and what it says --------------------------------
    (
        "a file's tokens are not said",
        DOCS,
        "  if (reading?.tokens != null) parts.push(about(reading.tokens));",
        "  // no tokens",
        T_DOCS,
        "is its kind, its pages or lines, and about how many tokens",
    ),
    (
        "a PDF is nameless before it is read",
        DOCS,
        '(file.mediaType === "application/pdf" ? "PDF" : null)',
        "null",
        T_DOCS,
        "is only the kind while it is read",
    ),
    (
        "one token is said as tokens",
        DOCS,
        '  `about ${tokens.toLocaleString()} ${tokens === 1 ? "token" : "tokens"}`;',
        "  `about ${tokens.toLocaleString()} tokens`;",
        T_DOCS,
        "is its kind, its pages or lines, and about how many tokens",
    ),
    (
        "a button to read the first part does not say which part",
        "web/src/lib/documents.ts",
        "return `Read the first ${offer.tokens.toLocaleString()} tokens (${offer.where})`;",
        "return `Read the first ${offer.tokens.toLocaleString()} tokens`;",
        "src/lib/documents.test.ts",
        "offers each choice as a button",
    ),
    (
        "every offer chooses to read it all",
        "web/src/lib/documents.ts",
        'return { mode: "first", tokens: offer.tokens };',
        'return { mode: "whole" };',
        "src/lib/documents.test.ts",
        "offers each choice as a button",
    ),
    (
        "the first part is chosen with no tokens",
        DOCS,
        '{ mode: "first", tokens: offer.tokens };',
        '{ mode: "first", tokens: 0 };',
        T_DOCS,
        "offers each choice as a button",
    ),
    (
        "reading it all because the person chose to is not said",
        DOCS,
        '            ? "Will be read whole, as you chose."',
        '            ? "Will be read whole."',
        T_DOCS,
        "is that it will be read whole as the person chose",
    ),
    (
        "a part does not say the rest is not sent",
        DOCS,
        "          ? `Will read ${cut.where} (${about(cut.tokens)}). The rest of the file is not sent.`",
        "          ? `Will read ${cut.where} (${about(cut.tokens)}).`",
        T_DOCS,
        "is which part will be read",
    ),
    (
        "a part does not say what the person chose",
        DOCS,
        '        choice: own,\n      };\n    }\n    case "native":',
        '        choice: null,\n      };\n    }\n    case "native":',
        T_DOCS,
        "is which part will be read",
    ),
    (
        "reading the whole after the first part came to it is not said",
        DOCS,
        '        choice: chosen?.mode === "first" ? own : null,',
        "        choice: null,",
        T_DOCS,
        "is that it will be read whole as the person chose",
    ),
    (
        "a model that reads PDFs is not named",
        DOCS,
        '          ? `${model?.id ?? "This model"} reads PDFs itself, so Workbench sends the file as it is.`',
        '          ? "This model reads PDFs itself, so Workbench sends the file as it is."',
        T_DOCS,
        "is that the model reads PDFs itself, naming the model",
    ),
    (
        "an earlier choice that no longer fits is not said, for a long file",
        DOCS,
        "          decision?.choiceNoLongerFits && chosen\n            ? `Your earlier choice (${choiceWords(chosen)}) no longer fits this model.`",
        "          false && chosen\n            ? `Your earlier choice (${choiceWords(chosen)}) no longer fits this model.`",
        T_DOCS,
        "says a choice that no longer fits",
    ),
    (
        "the room in the window is not said, for a file too long",
        DOCS,
        '              room != null ? `about ${room.toLocaleString()} tokens` : "only part"',
        '              "only part"',
        T_DOCS,
        "is that it is too long for the window",
    ),
    (
        "the server's sentence for a skipped file is replaced by the page's",
        DOCS,
        "  file.decision?.message || file.reading?.message || fallback;",
        "  fallback;",
        T_DOCS,
        "is the server's own sentence for a file it skipped",
    ),
    (
        "a screen reader is not told what the file needs",
        DOCS,
        '${words.title ? `${words.title}. ` : ""}${words.sentence}`',
        "${words.sentence}`",
        T_DOCS,
        "is told to a screen reader with the file's name",
    ),
    # --- why Send waits --------------------------------------------------------
    (
        "Send does not wait while the files are checked against another model",
        DOCS,
        '  if (checking) return `Checking your files against ${modelId ?? "this model"}…`;',
        "  void checking;",
        T_DOCS,
        "is that the files are being checked against another model",
    ),
    (
        "a file that needs a choice is said before one still being read",
        DOCS,
        ".sort((a, b) => a.rank - b.rank || a.order - b.order);",
        ".sort((a, b) => b.rank - a.rank || a.order - b.order);",
        T_DOCS,
        "says the file still being read first",
    ),
    (
        "a request that does not fit as a whole is not why Send waits",
        DOCS,
        "  if (facts?.request?.full) {",
        "  if (facts?.request?.full && false) {",
        T_DOCS,
        "is that the whole message does not fit",
    ),
    (
        "the files that need attention beyond the first are not counted",
        DOCS,
        '      ? `${sentence} (${more === 1 ? "1 more file needs" : `${more} more files need`} attention too.)`\n',
        "      ? sentence\n",
        T_DOCS,
        "says the file still being read first",
    ),
    (
        "the line about the window is said when the request does not fit",
        DOCS,
        "  if (!request || request.window == null || request.full) return null;",
        "  if (!request || request.window == null) return null;",
        T_DOCS,
        "says nothing when the window is not known",
    ),
    # --- a refusal that names files --------------------------------------------
    (
        "a refusal is taken for any code",
        DOCS,
        "  if (detail.code !== code) return null;",
        "  void code;",
        T_DOCS,
        "is nothing for any other failure",
    ),
    (
        "a refusal is taken for any status",
        DOCS,
        '  if (!found || found.status !== 409 || !found.detail || typeof found.detail !== "object") {',
        '  if (!found || !found.detail || typeof found.detail !== "object") {',
        T_DOCS,
        "is nothing for any other failure",
    ),
    (
        "a message does not keep how its file was read",
        DOCS,
        "  return { id: file.id, name: file.name, mediaType: file.mediaType, reading: file.reading ?? null };",
        "  return { id: file.id, name: file.name, mediaType: file.mediaType };",
        T_DOCS,
        "keeps its name, its type and how it was read",
    ),
    # --- what an answer lists ---------------------------------------------------
    (
        "a file sent as the file itself is listed with pages",
        DOCS,
        '  if (source.native) return "sent as a file";',
        "  void source.native;",
        T_DOCS,
        "is that the file itself was sent",
    ),
    (
        "lines are listed without their grouping",
        DOCS,
        "    return `lines ${grouped(from)} to ${grouped(to)} of ${grouped(lines)}`;",
        "    return `lines ${from} to ${to} of ${lines}`;",
        T_DOCS,
        "is the lines that went, grouped like every number",
    ),
    (
        "a part known only by its share is said whole",
        DOCS,
        "  if (source.partial && source.fraction != null) {",
        "  if (false) {",
        T_DOCS,
        "is how much of the text, when only that is known of a part",
    ),
    (
        "the first part is not marked",
        DOCS,
        '[source.partial ? "first part" : null, source.replayed ? "attached earlier" : null]',
        '[null, source.replayed ? "attached earlier" : null]',
        T_DOCS,
        "tags only the first part, and a file attached earlier",
    ),
    (
        "a file sent again is not marked",
        DOCS,
        '[source.partial ? "first part" : null, source.replayed ? "attached earlier" : null]',
        '[source.partial ? "first part" : null, null]',
        T_DOCS,
        "tags only the first part, and a file attached earlier",
    ),
    (
        "an exported file loses its marks",
        DOCS,
        '${tags.length ? ` (${tags.join(", ")})` : ""}',
        '${""}',
        T_DOCS,
        "is one line, for a chat exported as text",
    ),
    (
        "a document source is not told from a page",
        "web/src/lib/documents.ts",
        'source.kind === "document" && !isPassageSource(source);',
        'source.kind === "web";',
        "src/components/MessageDocuments.test.tsx",
        "are listed by name and by the pages that were sent",
    ),
    (
        "the file picker does not offer Markdown",
        DOCS,
        '".markdown", ".md", ".mdown",',
        '".markdown", ".mdown",',
        T_DOCS,
        "keeps what it showed",
    ),
    (
        "the file picker does not offer Word",
        "web/src/lib/documents.ts",
        '  "audio/mpeg",\n  DOCX,\n  ".docx",',
        '  "audio/mpeg",\n  DOCX,',
        "src/lib/documents.test.ts",
        "keeps what it showed",
    ),
    (
        "a word that says how documents are read slips in",
        DOCS,
        "/** A file Workbench reads as a document (the server's word for it). */",
        "/** A file Workbench reads as a document, in chunks. */",
        T_WORDS,
        "keeps how documents are read underneath off every screen",
    ),
    # --- watching files being read ----------------------------------------------
    (
        "it asks for ever, even when nothing is being read",
        FILES,
        "    if (!watching) return;\n    let stopped = false;",
        "    let stopped = false;",
        T_FILES,
        "asks again each second while it is still being read, and not again once it is read",
    ),
    (
        "it asks again more slowly than it says",
        FILES,
        "next(failing.current ? RETRY_MS : POLL_MS);",
        "next(failing.current ? RETRY_MS : POLL_MS * 5);",
        T_FILES,
        "asks again each second while it is still being read",
    ),
    (
        "it does not wait longer after a failed read",
        FILES,
        "next(failing.current ? RETRY_MS : POLL_MS);",
        "next(POLL_MS);",
        T_FILES,
        "says why it could not ask, keeps what it shows, and waits longer",
    ),
    (
        "it keeps asking after the page goes",
        FILES,
        "      stopped = true;\n      window.clearTimeout(timer);",
        "      window.clearTimeout(timer);",
        T_FILES,
        "does not ask again when the page goes while an answer is on its way",
    ),
    (
        "it does not ask for the model that is picked",
        FILES,
        '  const query = model ? `?model=${encodeURIComponent(model)}` : "";',
        '  const query = "";',
        T_FILES,
        "asks soon after a document is attached, for the model that is picked",
    ),
    (
        "it takes an answer that is not a listing for one",
        FILES,
        "        if (!got || !Array.isArray(got.files)) return;",
        "        void got;",
        T_FILES,
        "takes an answer that is not a listing for no answer at all",
    ),
    (
        "a change of model does not ask again",
        FILES,
        "    if (asked.current === model) return;\n    asked.current = model;",
        "    return;\n    asked.current = model;",
        T_FILES,
        "asks as soon as the model is changed, says it is checking",
    ),
    (
        "a change of model does not say it is checking",
        FILES,
        "    if (current.current.some(isDocument)) void reload({ checking: true });",
        "    if (current.current.some(isDocument)) void reload();",
        T_FILES,
        "asks as soon as the model is changed, says it is checking",
    ),
    (
        "an answer that a later one overtook is shown",
        FILES,
        "        if (!alive.current || mine !== latest.current) return;\n        // A server that answered",
        "        if (!alive.current) return;\n        // A server that answered",
        T_FILES,
        "drops an answer that a later one overtook",
    ),
    (
        "a file the server says was sent stays",
        FILES,
        "    else if (!found.sent) next.push({ ...mine, ...found });",
        "    else next.push({ ...mine, ...found });",
        T_FILES,
        "lets go of a file the server says was sent",
    ),
    (
        "a file that was taken back comes back",
        FILES,
        "    if (file.sent || held.has(file.id) || known.has(file.id)) continue;",
        "    if (file.sent || held.has(file.id)) continue;",
        T_FILES,
        "is not added when it was sent, taken back, or sent just now",
    ),
    (
        "a file the page never held is not added",
        FILES,
        "    known.add(file.id);\n    next.push(file);",
        "    known.add(file.id);",
        T_FILES,
        "is added when the page first asks",
    ),
    (
        "the page's version of a file wins over the server's",
        FILES,
        "    else if (!found.sent) next.push({ ...mine, ...found });",
        "    else if (!found.sent) next.push({ ...found, ...mine });",
        T_FILES,
        "takes the server's version of a file, over the page's",
    ),
    (
        "taking a file back does not ask again for the rest",
        FILES,
        "      setProblems((now) => without(now, id));\n      if (current.current.some(isDocument)) void reload();",
        "      setProblems((now) => without(now, id));",
        T_FILES,
        "takes a file back, forgets what it was refused for, and asks again for the rest",
    ),
    (
        "a sent message keeps what its files were refused for",
        FILES,
        "    commit([]);\n    setProblems({});\n    setFacts(null);",
        "    commit([]);\n    setFacts(null);",
        T_FILES,
        "lets go of everything once the message is sent",
    ),
    (
        "a sent message keeps what the page knew of the model",
        FILES,
        "    setProblems({});\n    setFacts(null);",
        "    setProblems({});",
        T_FILES,
        "lets go of everything once the message is sent",
    ),
    (
        "a choice is made for the chat's model, not the one picked",
        FILES,
        "          `${base}/${encodeURIComponent(file.id)}/reading${query}`,",
        "          `${base}/${encodeURIComponent(file.id)}/reading`,",
        T_FILES,
        "sends the first part the offer names",
    ),
    (
        "taking a choice back sends nothing to clear it",
        FILES,
        "          choice ?? { mode: null },",
        "          choice ?? {},",
        T_FILES,
        "sends 'read it all' as the whole, and takes a choice back with null",
    ),
    (
        "the server's answer to a choice is not kept",
        FILES,
        '        if (item && typeof item.id === "string") {',
        "        if (false) {",
        T_FILES,
        "shows the server's answer to the choice at once",
    ),
    (
        "a refused choice says the error's words, not the server's",
        FILES,
        "          refused?.message ?? (failure instanceof Error ? failure.message : String(failure));",
        "          failure instanceof Error ? failure.message : String(failure);",
        T_FILES,
        "is refused with the server's sentence, and leaves the file as it was",
    ),
    (
        "a refused send leaves the file's decision as the page believed it",
        FILES,
        "          return named?.decision ? { ...file, decision: named.decision } : file;",
        "          return file;",
        T_FILES,
        "puts each file's own words on its chip, takes the decisions the server made",
    ),
    (
        "a file refused while it was read is told its refusal's raw words",
        FILES,
        '  if (!decision || decision.kind === "pending") {',
        "  if (!decision) {",
        T_FILES,
        "says a file that was still being read was only not read yet",
    ),
    (
        "a screen reader is told of a file that has only begun to be read",
        FILES,
        '      if (state !== "reading" && seen.current.get(file.id) !== state) {',
        "      if (seen.current.get(file.id) !== state) {",
        T_FILES,
        "says nothing for a file that has only been attached and is being read",
    ),
    # --- the chips ---------------------------------------------------------------
    (
        "what is offered to choose is never shown",
        CHIPS,
        '    words.state === "choose" || words.state === "too_long" ? (decision?.offers ?? []) : [];',
        "    [];",
        T_CHIPS,
        "longer than the default: a warning, the limit, and both offers",
    ),
    (
        "an offer takes the choice back instead of making it",
        CHIPS,
        "                    onClick={() => void choose(offerChoice(offer))}",
        "                    onClick={() => void choose(null)}",
        T_CHIPS,
        "longer than the default: a warning, the limit, and both offers",
    ),
    (
        "an offer can be pressed while its choice is kept",
        CHIPS,
        "                    disabled={disabled}\n                    onClick={() => void choose(offerChoice(offer))}",
        "                    onClick={() => void choose(offerChoice(offer))}",
        T_CHIPS,
        "is held, and so is its Remove, while a choice is kept or a message is sent",
    ),
    (
        "a file can be taken back while a message is sent",
        CHIPS,
        '          disabled={locked || removing}\n          onClick={() => onRemove(file)}\n          className="icon-btn -mr-1 -mt-1 shrink-0"',
        '          onClick={() => onRemove(file)}\n          className="icon-btn -mr-1 -mt-1 shrink-0"',
        T_CHIPS,
        "is held, and so is its Remove, while a choice is kept or a message is sent",
    ),
    (
        "an image can be taken back while a message is sent",
        CHIPS,
        "                disabled={locked || removing !== null}",
        "                disabled={false}",
        T_CHIPS,
        "is held while a message is sent or another file is taken back",
    ),
    (
        "a choice is not shown, and cannot be chosen again",
        "web/src/components/Attachments.tsx",
        '  const kept =\n    searching ||\n    ((words.state === "whole" || words.state === "partial") && decision?.chosen != null);',
        "  const kept = false;",
        "src/components/Attachments.test.tsx",
        "a part: which part, how many tokens, that the rest is not sent, and the choice in effect",
    ),
    (
        "a file that was skipped offers its text",
        CHIPS,
        '      {(kept || file.reading?.state === "ready") && (\n        <div className="-mx-1 flex flex-wrap items-center gap-x-1">\n          {kept && (\n            <button\n              type="button"\n              data-testid="document-choose-again"\n              aria-describedby={`${uid}-name`}\n              disabled={disabled}\n              onClick={() => void choose(null)}\n              className="act"\n            >\n              <RotateCcw size={14} aria-hidden /> Choose again\n            </button>\n          )}\n          {file.reading?.state === "ready" && text.action}',
        '      {(true) && (\n        <div className="-mx-1 flex flex-wrap items-center gap-x-1">\n          {kept && (\n            <button\n              type="button"\n              data-testid="document-choose-again"\n              aria-describedby={`${uid}-name`}\n              disabled={disabled}\n              onClick={() => void choose(null)}\n              className="act"\n            >\n              <RotateCcw size={14} aria-hidden /> Choose again\n            </button>\n          )}\n          {text.action}',
        T_CHIPS,
        "skipped: the server's own words, a warning, and what to do",
    ),
    (
        "a refusal is not an alert",
        CHIPS,
        '        <p role="alert" data-testid="document-problem" className="notice notice--error">',
        '        <p data-testid="document-problem" className="notice notice--error">',
        T_CHIPS,
        "says why a message or a choice was refused, as an alert on its own chip",
    ),
    (
        "a card does not say which look it has",
        CHIPS,
        "      data-state={words.state}",
        '      data-state="whole"',
        T_CHIPS,
        "longer than the default: a warning, the limit, and both offers",
    ),
    (
        "the keyboard is not kept on the card when a choice is made",
        CHIPS,
        "    if (!now || now === document.body) root.current?.focus({ preventScroll: true });",
        "    void now;",
        T_CHIPS,
        "keeps the keyboard on the card when a choice is made",
    ),
    (
        "the keyboard is taken from someone who went elsewhere",
        CHIPS,
        "    if (!now || now === document.body) root.current?.focus({ preventScroll: true });",
        "    root.current?.focus({ preventScroll: true });",
        T_CHIPS,
        "does not take the keyboard from someone who went elsewhere",
    ),
    (
        "Eugene not saying the window is not said",
        CHIPS,
        "      {documents && model && !model.known && (",
        "      {false && (",
        T_CHIPS,
        "is that Eugene does not say how large the window is",
    ),
    (
        "the line about the window is never shown",
        CHIPS,
        "      {request && (",
        "      {false && (",
        T_CHIPS,
        "is how much of the window the message comes to",
    ),
    (
        "a message that does not fit as a whole is not said",
        CHIPS,
        "      {documents && facts?.request?.full && (",
        "      {false && (",
        T_CHIPS,
        "is that the whole does not fit, in place of the line above",
    ),
    (
        "many files push Send off the screen",
        CHIPS,
        "max-h-[max(8rem,calc(100dvh-32rem))] ",
        "",
        T_CHIPS,
        "scroll inside a box that leaves the window its message box and Send",
    ),
    (
        "the dots that say a file is read are spoken",
        CHIPS,
        '          <span aria-hidden className="working-dots">',
        '          <span className="working-dots">',
        T_CHIPS,
        "says so, with a glyph that is only a glyph, and offers nothing to choose",
    ),
    (
        "how long a file has been read is said at once",
        CHIPS,
        "          {waited >= 3 && (",
        "          {waited >= 0 && (",
        T_CHIPS,
        "says how long it has been read once that is worth saying",
    ),
    (
        "a file that failed is drawn as one that was skipped",
        CHIPS,
        '          className={`notice ${words.tone === "error" ? "notice--error" : "notice--warn"}`}',
        '          className="notice notice--warn"',
        T_CHIPS,
        "failed: the server's own words, in the colour of a failure",
    ),
    (
        "what the reader noted is not listed",
        CHIPS,
        "      {words.notes.length > 0 && (",
        "      {false && (",
        T_CHIPS,
        "lists what the reader noted, quietly",
    ),
    (
        "what to do about a file that cannot be read is not said",
        CHIPS,
        '            {words.hint && <span className="mt-1 block text-muted">{words.hint}</span>}\n',
        "",
        T_CHIPS,
        "skipped: the server's own words, a warning, and what to do",
    ),
    (
        "the choice in effect is not said",
        CHIPS,
        "      {words.choice && (",
        "      {false && (",
        T_CHIPS,
        "a part: which part, how many tokens, that the rest is not sent, and the choice in effect",
    ),
    # --- what Workbench read -----------------------------------------------------
    (
        "the text is not fetched",
        "web/src/components/DocumentText.tsx",
        "const read = await (saved ? documentText(id) : fileText(id));",
        'const read = "";',
        "src/components/Attachments.test.tsx",
        "is offered for a file that was read, and shows its text in a box that scrolls",
    ),
    (
        "closing the box keeps the text",
        TEXT,
        "      setOpen(false);\n      setText(null);",
        "      setOpen(false);",
        T_CHIPS,
        "shows no text it had read when it is opened again",
    ),
    (
        "an answer that arrives after the box was closed is shown",
        TEXT,
        "      if (mine === latest.current) setText(read);",
        "      setText(read);",
        T_CHIPS,
        "shows no text that arrived after the box was closed, when it is opened again",
    ),
    (
        "the button does not say whether the box is open",
        TEXT,
        "      aria-expanded={open}",
        "      aria-expanded={false}",
        T_CHIPS,
        "is offered for a file that was read, and shows its text in a box that scrolls",
    ),
    (
        "the button is not told apart by its file",
        TEXT,
        "      aria-describedby={describedBy}",
        "",
        T_CHIPS,
        "is offered for a file that was read, and shows its text in a box that scrolls",
    ),
    (
        "opening is not said",
        TEXT,
        '        <p role="status" className="text-xs text-muted">',
        '        <p className="text-xs text-muted">',
        T_CHIPS,
        "says so while it opens, and why when it cannot",
    ),
    # --- the composer ------------------------------------------------------------
    (
        "Send goes while a file needs a choice",
        COMPOSER,
        "                  hold !== null ||\n                  sending ||",
        "                  sending ||",
        T_COMPOSER,
        "holds Send, with what to do, until the person chooses",
    ),
    (
        "Enter sends while a file needs a choice",
        COMPOSER,
        "    if (removing !== null || hold !== null) return;",
        "    if (removing !== null) return;",
        T_COMPOSER,
        "does not send while it is read, by Send or by Enter",
    ),
    (
        "Send is not described by why it waits",
        COMPOSER,
        '                aria-describedby={hold ? "send-held" : undefined}\n',
        "",
        T_COMPOSER,
        "is a card that says it is being read, and Send waits with the reason beside it",
    ),
    (
        "why Send waits is not said",
        COMPOSER,
        "        {hold && (",
        "        {false && (",
        T_COMPOSER,
        "is a card that says it is being read, and Send waits with the reason beside it",
    ),
    (
        "a refused send is not read as a refusal",
        COMPOSER,
        '      const refused = refusalFor(error, "documents");',
        "      const refused = null as ReturnType<typeof refusalFor>;",
        T_COMPOSER,
        "names the file on its own card, keeps the words, and sends nothing",
    ),
    (
        "the message carries no file",
        COMPOSER,
        "          attachments: pending.map((p) => p.id),",
        "          attachments: [],",
        T_COMPOSER,
        "sends the file's id with the message",
    ),
    (
        "the message keeps more of a file than how it was read",
        COMPOSER,
        "files: pending.map(asAttached)",
        "files: pending",
        T_COMPOSER,
        "sends the file's id with the message",
    ),
    (
        "the files stay in the composer after the message is sent",
        COMPOSER,
        "      docs.clear();",
        "      void docs;",
        T_COMPOSER,
        "sends the file's id with the message",
    ),
    (
        "a PDF that Workbench read is refused for a model that cannot read one",
        COMPOSER,
        "    const kind = file.reading ? null : needs(file.mediaType);",
        "    const kind = needs(file.mediaType);",
        T_LIB,
        "does not ask a model to take a PDF that Workbench read",
    ),
    (
        "an upload does not ask for the model that is picked",
        COMPOSER,
        '        const query = chosenId ? `?model=${encodeURIComponent(chosenId)}` : "";',
        '        const query = "";',
        T_COMPOSER,
        "asks the server about it for the model that is picked, from the upload on",
    ),
    (
        "a file taken back stays in the composer",
        COMPOSER,
        "      docs.drop(file.id);",
        "      void docs;",
        T_COMPOSER,
        "is taken back when the file is removed, and Send is free again",
    ),
    (
        "the file picker is not asked for any type",
        COMPOSER,
        "            accept={ACCEPT}\n",
        "",
        T_COMPOSER,
        "offers Word and text files beside PDFs and images, and has a name",
    ),
    (
        "the file input has no name",
        COMPOSER,
        '            aria-label="Choose files to attach"\n',
        "",
        T_COMPOSER,
        "offers Word and text files beside PDFs and images, and has a name",
    ),
    (
        "the keyboard is lost when a file is taken back",
        COMPOSER,
        "      if (fromKeyboard) textarea.current?.focus();",
        "      void fromKeyboard;",
        T_COMPOSER,
        "returns the keyboard to the message box",
    ),
    (
        "a page that cannot ask says nothing",
        COMPOSER,
        '        {docs.error && pending.some((file) => file.kind === "document") && (',
        "        {false && (",
        T_COMPOSER,
        "says so beside the files, and keeps trying",
    ),
    (
        "a screen reader is told nothing as a file is read",
        COMPOSER,
        "          {filesSaid}",
        '          {""}',
        T_COMPOSER,
        "tells a screen reader what the file came to when it has been read",
    ),
    # --- what a message shows ----------------------------------------------------
    (
        "the files that were sent are not listed",
        "web/src/components/MessageView.tsx",
        "const files = message.sources.filter(isDocumentSource);",
        "const files = message.sources.filter(isDocumentSource).slice(0, 0);",
        "src/components/MessageDocuments.test.tsx",
        "are listed by name and by the pages that were sent",
    ),
    (
        "files are listed among the pages the search found",
        MESSAGE,
        "  const web = message.sources.filter(isWebSource);",
        "  const web = message.sources;",
        T_MESSAGE,
        "are listed apart from the pages the search found",
    ),
    (
        "the first part is not marked under the answer",
        MESSAGE,
        "                    {sourceTags(source).map((tag) => (",
        "                    {([] as string[]).map((tag) => (",
        T_MESSAGE,
        "say the lines that were sent, and mark a first part",
    ),
    (
        "the files are said to be what the model used",
        MESSAGE,
        '              <p className="text-muted">Files sent to the model</p>',
        '              <p className="text-muted">Sources the model used</p>',
        T_MESSAGE,
        "never say the model used them",
    ),
    (
        "a search that linked nothing is not said when files were sent",
        MESSAGE,
        "          {web.length === 0 && message.searches > 0 && (",
        "          {message.sources.length === 0 && message.searches > 0 && (",
        T_MESSAGE,
        "still say a search found nothing the answer linked, when files were sent",
    ),
    (
        "the tokens of a file sent are not said",
        MESSAGE,
        "                    {source.tokens != null && (",
        "                    {false && (",
        T_MESSAGE,
        "are listed by name and by the pages that were sent",
    ),
    (
        "a document in the person's message is a name and no more",
        MESSAGE,
        "                .filter((file) => !file.reading)",
        "                .filter(() => true)",
        T_MESSAGE,
        "show a document's name, what Workbench found in it, and a way to see the text",
    ),
    (
        "a file that was skipped offers its text under the person's message",
        MESSAGE,
        "          {ready && text.action}",
        "          {text.action}",
        T_MESSAGE,
        "offer no text for a file that was skipped or could not be read",
    ),
    (
        "a document's reading is not said under the person's message",
        MESSAGE,
        '          {meta && <span data-testid="document-file-meta">{meta}</span>}\n',
        "",
        T_MESSAGE,
        "show a document's name, what Workbench found in it, and a way to see the text",
    ),
    (
        "an exported chat does not say a file was only sent",
        EXPORT,
        "              ? `Sent to the model: ${oneLine(sourceLine(s))}`",
        "              ? `${oneLine(sourceLine(s))}`",
        T_MESSAGE,
        "lists a file as what was sent to the model, with its pages, and a page as a link",
    ),
    # --- every control has a name and a test id -----------------------------------
    (
        "a card's Remove has no test id",
        CHIPS,
        '          data-testid="attachment-remove"\n          aria-label={`Remove ${file.name}`}\n          disabled={locked || removing}',
        "          aria-label={`Remove ${file.name}`}\n          disabled={locked || removing}",
        T_CONTROLS,
        "the chips in the composer",
    ),
    (
        "an offer has no test id",
        "web/src/components/Attachments.tsx",
        "                    data-testid={`document-offer-${offerKey(offer)}`}\n",
        "",
        "src/documentsControls.test.tsx",
        "the chips in the composer",
    ),
    (
        "the button that shows the text has no test id",
        "web/src/components/DocumentText.tsx",
        "      data-testid={`${test}-text-toggle`}\n",
        "",
        "src/documentsControls.test.tsx",
        "the chips in the composer",
    ),
    (
        "the file input has no name for a screen reader",
        COMPOSER,
        '            aria-label="Choose files to attach"\n',
        "",
        T_CONTROLS,
        "the composer with files in it",
    ),
    (
        "the Attach button has no test id",
        COMPOSER,
        '              data-testid="attach"\n',
        "",
        T_CONTROLS,
        "the composer with files in it",
    ),
]

# What the Python checks catch: (label, file, before, after, test file, -k fragment).
PYTHON_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "the page stops offering Markdown files that the server reads",
        DOCS,
        '".markdown", ".md", ".mdown",',
        '".markdown", ".mdown",',
        PAGE_AGREES,
        "the_file_picker_offers_every_kind_of_text",
    ),
    (
        "the page does not ask for Word",
        "web/src/lib/documents.ts",
        '  "audio/mpeg",\n  DOCX,\n  ".docx",',
        '  "audio/mpeg",\n  DOCX,',
        "tests/test_documents_page.py",
        "the_file_picker_is_asked_for_every_type",
    ),
    (
        "the page reads a source's file by another name than the server's",
        "web/src/lib/types.ts",
        'export interface DocumentSource {\n  kind: "document";\n  doc_id: string;',
        'export interface DocumentSource {\n  kind: "document";\n  docId: string;',
        "tests/test_documents_page.py",
        "a_source_has_the_fields_the_page_reads",
    ),
    (
        "the page does not know a kind of decision the server makes",
        "web/src/lib/types.ts",
        '  | "choose"\n  | "too_long"\n  | "no_room"',
        '  | "choose"\n  | "no_room"',
        "tests/test_documents_page.py",
        "every_kind_of_decision",
    ),
    (
        "the page does not read the room a decision leaves",
        TYPES,
        "  readAllLimit: number | null;\n",
        "",
        PAGE_AGREES,
        "a_decision_has_the_fields_the_page_reads",
    ),
    (
        "the page does not read how a file was read",
        TYPES,
        "  headings: number | null;\n  notes: string[];",
        "  notes: string[];",
        PAGE_AGREES,
        "a_reading_has_the_fields_the_page_reads",
    ),
]

# What only a real browser sees: (label, file, before, after). Each rebuilds the
# page, and `tests/test_documents_browser.py` must fail.
BROWSER_CASES: list[tuple[str, str, str, str]] = [
    (
        "a file is never asked about again, so it is read for ever",
        FILES,
        "  const watching = files.some((file) => isDocument(file) && isReading(file));",
        "  const watching = files.some((file) => isDocument(file) && isReading(file) && false);",
    ),
    (
        "Send goes while a file is being read",
        COMPOSER,
        "                  hold !== null ||\n                  sending ||",
        "                  sending ||",
    ),
    (
        "a refused send is not shown on its card",
        COMPOSER,
        '      const refused = refusalFor(error, "documents");',
        "      const refused = null as ReturnType<typeof refusalFor>;",
    ),
    (
        "a different model is not asked about",
        FILES,
        "    if (asked.current === model) return;\n    asked.current = model;",
        "    return;\n    asked.current = model;",
    ),
    (
        "a file attached before a reload is hidden",
        FILES,
        "    known.add(file.id);\n    next.push(file);",
        "    known.add(file.id);",
    ),
    (
        "the text of a file is asked for at the wrong address",
        API,
        "`/api/files/${encodeURIComponent(id)}/text`",
        "`/api/files/${encodeURIComponent(id)}`",
    ),
    (
        "a first part is not marked under the answer",
        DOCS,
        '[source.partial ? "first part" : null, source.replayed ? "attached earlier" : null]',
        '[null, source.replayed ? "attached earlier" : null]',
    ),
    (
        "the file picker does not offer Word",
        DOCS,
        '  "audio/mpeg",\n  DOCX,\n  ".docx",',
        '  "audio/mpeg",\n  DOCX,',
    ),
    (
        "four files push Send off a phone's screen",
        CHIPS,
        "max-h-[max(8rem,calc(100dvh-32rem))] ",
        "",
    ),
    (
        "the first part is chosen with no tokens, which the server refuses",
        DOCS,
        '{ mode: "first", tokens: offer.tokens };',
        '{ mode: "first", tokens: 0 };',
    ),
]

# What the reading check sees in Chrome: (label, file, before, after). Each
# rebuilds the page, and `tests/test_reading_browser.py` must fail.
READING_CASES: list[tuple[str, str, str, str]] = [
    (
        "a file's numbers are too faint to read",
        CHIPS,
        '            <p data-testid="document-meta" className="text-xs text-muted">',
        '            <p data-testid="document-meta" className="text-xs text-muted" style={{ color: "rgba(128, 128, 128, 0.35)" }}>',
    ),
    (
        "two files push Send off a phone's screen",
        CHIPS,
        "max-h-[max(8rem,calc(100dvh-32rem))] ",
        "",
    ),
]

# Every Chrome case, with the check that has to catch it.
CHROME_CASES: list[tuple[str, str, str, str, str]] = [
    *[
        (label, name, before, after, DOCUMENTS_BROWSER)
        for label, name, before, after in BROWSER_CASES
    ],
    *[
        (label, name, before, after, READING_BROWSER)
        for label, name, before, after in READING_CASES
    ],
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
        "Chrome, each in one place."
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
        timeout=400,
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
    args = parser.parse_args()

    def wanted(label: str) -> bool:
        return not args.label or any(t in label for t in args.label)

    cases = [] if args.chrome_only else [c for c in CASES if wanted(c[0])]
    python_cases = (
        [c for c in PYTHON_CASES if wanted(c[0])] if args.python and not args.chrome_only else []
    )
    chrome_cases = [c for c in CHROME_CASES if wanted(c[0])]
    anchors()
    if args.anchors:
        return 0

    checks = sorted({(test, name) for _, _, _, _, test, name in cases})
    for test, name in checks:
        passed, output, ran = vitest(test, name)
        if not passed or ran == 0:
            print(output[-3000:])
            raise SystemExit(f"Baseline failed or ran nothing ({test}: {name}); no files changed.")
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

    backup = Path(tempfile.mkdtemp(prefix="workbench-documents-page-sabotage-"))
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
