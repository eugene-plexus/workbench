"""Prove the search-by-meaning page's checks are observed (MR5, the page side).

Each case breaks one thing in the page's code, and the test that is named for it
must fail. Files are restored from exact bytes kept beside the run, never from
Git. A mutation that stops the code compiling does not count as a caught defect,
and neither does a test name that runs nothing.

  python scripts/check-meaning-page-sabotage.py              # every unit-test case
  python scripts/check-meaning-page-sabotage.py --anchors    # only check each anchor is unique
  python scripts/check-meaning-page-sabotage.py --python     # also the cases the Python checks catch
  python scripts/check-meaning-page-sabotage.py --browser    # also the Chrome cases (needs WORKBENCH_PLAYWRIGHT)
  python scripts/check-meaning-page-sabotage.py --chrome-only    # only the Chrome cases
  python scripts/check-meaning-page-sabotage.py --label TEXT ...   # only the cases whose label has TEXT
  python scripts/check-meaning-page-sabotage.py --only PATH ...    # only the cases that break a file whose path has PATH

The Chrome cases rebuild the page for each break and run `tests/test_library_browser.py`;
the page is rebuilt from the restored source at the end. Run it with every ambient
EUGENE_PLEXUS_* variable cleared.
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

MEANING = SRC + "lib/documentMeaning.ts"
SAVED = SRC + "lib/savedDocuments.ts"
SAVED_HOOK = SRC + "lib/useSavedDocuments.ts"
TYPES = SRC + "lib/types.ts"
SEARCH = SRC + "components/DocumentsSearch.tsx"
SCREEN = SRC + "components/Documents.tsx"
ROW = SRC + "components/DocumentRow.tsx"
TRY = SRC + "components/TestQuestion.tsx"
CHIP = SRC + "components/DocumentsChip.tsx"
PASSAGES = SRC + "components/SourcePassages.tsx"
APP = SRC + "App.tsx"
CONTROLS = SRC + "test-controls.ts"

DURATIONS = "tests/fixtures/duration_words.json"
CODES_TABLE = "tests/fixtures/search_codes.json"
LIBRARY_BROWSER = "tests/test_library_browser.py"
PAGE_AGREES = "tests/test_saved_page.py"
MEANING_AGREES = "tests/test_meaning_page.py"

T_MEANING = "src/lib/documentMeaning.test.ts"
T_SAVED = "src/lib/savedDocuments.test.ts"
T_HOOK = "src/lib/useSavedDocuments.test.tsx"
T_SEARCH = "src/components/DocumentsSearch.test.tsx"
T_SCREEN = "src/components/Documents.test.tsx"
T_TRY = "src/components/TestQuestion.test.tsx"
T_CHIP = "src/components/DocumentsChip.test.tsx"
T_MESSAGE = "src/components/MessageSearch.test.tsx"
T_APP = "src/App.test.tsx"
T_CONTROLS = "src/libraryControls.test.tsx"
T_WORDS = "src/words.test.ts"

# (label, file, before, after, test file, a fragment of the test's name)
CASES: list[tuple[str, str, str, str, str, str]] = [
    # --- what each code says, how it looks, and what a person can do about it -----------------
    (
        "the detail keeps the words the headline has said",
        MEANING,
        "const rest = said.startsWith(WORDS_ONLY_LEAD)\n    ? sentence(said.slice(WORDS_ONLY_LEAD.length))\n    : said;",
        "const rest = said;",
        T_MEANING,
        "says the detail as a sentence of its own, with a capital",
    ),
    (
        "a model's name is given a capital like a word",
        MEANING,
        r"const sentence = (text: string): string => (/^[a-z]+(?:\s|$)/.test(text) ? capital(text) : text);",
        "const sentence = (text: string): string => capital(text);",
        T_MEANING,
        "leaves a model's name as it is written when a sentence begins with one",
    ),
    (
        "a model that is running but not in use is said to be in use",
        MEANING,
        'return facts.code === "ok" && facts.model?.resolved && facts.model.label ? facts.model : null;',
        "return facts.model?.resolved && facts.model.label ? facts.model : null;",
        T_MEANING,
        "is not one that is running while the search is paused, or failing, which is not in use",
    ),
    (
        "a model outside the machines is not a warning",
        MEANING,
        'if (model && model.locality !== "local") return "warn";',
        'if (model && model.locality === "never") return "warn";',
        T_MEANING,
        "is a warning in itself when it runs outside the person's machines",
    ),
    (
        "a search model that is not there is said twice",
        MEANING,
        'if (adding.state === "waiting" && facts.code !== "ok") return null;',
        "",
        T_MEANING,
        "does not say a search model that is not there twice",
    ),
    (
        "Rebuild does not say how many passages it adds meaning to",
        MEANING,
        'return `Rebuild search adds meaning again to ${count(rebuild.passages, "passage")} with ${model}${time}. Searching by words keeps working while it does.`;',
        "return `Rebuild search adds meaning again with ${model}${time}. Searching by words keeps working while it does.`;",
        T_MEANING,
        "says how many passages it adds meaning to again",
    ),
    (
        "a time at a half is rounded up, not to the even number",
        MEANING,
        "return floor % 2 === 0 ? floor : floor + 1;",
        "return floor + 1;",
        T_MEANING,
        "goes to the even number at a half",
    ),
    (
        "a passage the search model refused is counted as having its meaning",
        MEANING,
        "const has = Math.max(0, embedded - refused);",
        "const has = embedded;",
        T_MEANING,
        "do not count a passage the search model refused as one that has it",
    ),
    (
        "a chat's own files are counted among the refused",
        MEANING,
        'doc.scope === "library" ? (doc.meaning?.refused ?? 0) : 0',
        "doc.meaning?.refused ?? 0",
        T_MEANING,
        "leave a chat's own files out of what they refuse",
    ),
    (
        "one passage is said as every passage",
        MEANING,
        'passages === 1 ? "Meaning is added to the passage." : "Meaning is added to every passage."',
        '"Meaning is added to every passage."',
        T_MEANING,
        "say meaning is added to every passage, or to one",
    ),
    (
        "a document read with no search model says only Ready",
        MEANING,
        'if (meaning.state === "words_only") return { tone: "ok", label: WORDS_ONLY_ROW };',
        "",
        T_MEANING,
        "is found by words only, and says why, for a document read while no search model ran",
    ),
    (
        "a waiting document has no clock",
        MEANING,
        'tone: meaning.state === "waiting" || meaning.state === "limited" ? "quiet" : "ok",',
        'tone: "ok",',
        T_MEANING,
        "is a wait, with a clock, in the server's words",
    ),
    (
        "a code that needs somebody is not a warning",
        MEANING,
        '  not_running: "warn",',
        '  not_running: "info",',
        T_MEANING,
        "a state as it should be is quiet and one that needs somebody is a warning",
    ),
    (
        "a person who is not the owner is not told to ask the owner",
        MEANING,
        "if (!OWNER_FIXES.includes(code)) return null;",
        'if (code === "ok") return null;',
        T_MEANING,
        "tells a person who is not the owner to ask them",
    ),
    (
        "the page of the console that mends a code is the wrong one",
        MEANING,
        'if (code === "none_set_up") return "library";',
        'if (code === "none_set_up") return "backends";',
        T_MEANING,
        "names the page of Eugene's console that mends a code",
    ),
    (
        "the link to Backends goes to a page the console does not have",
        MEANING,
        'export const CONSOLE_ROUTE = { library: "library", backends: "inference" } as const;',
        'export const CONSOLE_ROUTE = { library: "library", backends: "backends" } as const;',
        T_MEANING,
        "keeps its pages where the console has them",
    ),
    (
        "the console's address loses the page it is given",
        MEANING,
        'return new URL(`${path.replace(/^\\/+|\\/+$/g, "")}/`, base).toString();',
        "return new URL(path, base).toString();",
        T_MEANING,
        "is linked to by its pages, whatever the address ends with",
    ),
    (
        "a passage found by meaning alone does not say it has none of the words",
        MEANING,
        'return hasWords ? "Found by meaning" : "Found by meaning: none of your words are in it";',
        'return "Found by meaning";',
        T_MEANING,
        "says it in full, and that a passage found by meaning alone",
    ),
    (
        "a search none of whose ways could run is said as nothing matched",
        MEANING,
        'return `Nothing was searched: search by meaning could not run.${why ? ` ${why}` : ""}`;',
        'return "Nothing in your documents matched.";',
        T_MEANING,
        "says a search none of whose ways could run, with why, and never that nothing matched",
    ),
    (
        "a note about meaning is not known by its code",
        MEANING,
        'code.startsWith("meaning_");',
        'code.startsWith("meaning-");',
        T_MEANING,
        "knows a note about meaning by its code",
    ),
    (
        "each way puts a passage where the other did",
        MEANING,
        "said.push(`words put it ${ordinal(places.words)}`);",
        "said.push(`meaning put it ${ordinal(places.words)}`);",
        T_MEANING,
        "says where each way put it, in its own list",
    ),
    (
        "a place is said as 11st",
        MEANING,
        "const teen = n % 100 >= 11 && n % 100 <= 13;",
        "const teen = false;",
        T_MEANING,
        "says a place the way a person does",
    ),
    (
        "the search model is not named for a passage found by meaning",
        MEANING,
        "return run?.used && run.model ? `${how}, with the search model ${run.model}.` : `${how}.`;",
        "return `${how}.`;",
        T_MEANING,
        "says how the passages were found, and by which search model when meaning ran",
    ),
    (
        "meaning that was never in play is said beside every answer",
        MEANING,
        "return run.quiet && !askedFor ? null : run.message;",
        "return run.message;",
        T_MEANING,
        "says why meaning did not run, in the server's words, unless it was never in play",
    ),
    (
        "both ways is asked for by naming both",
        MEANING,
        '(choice === "both" ? null : [choice])',
        '(choice === "both" ? ["words", "meaning"] : [choice])',
        T_MEANING,
        "names none for both, so a chat's own rule applies",
    ),
    (
        "the drawer does not say where Rebuild is",
        MEANING,
        'if (facts.actions.includes("rebuild")) more.push("Rebuild search is on the Documents screen.");',
        "",
        T_MEANING,
        "says why, in the server's words, and where Rebuild is",
    ),
    (
        "the screen says words only for what searches by meaning",
        MEANING,
        '    ? "Your own files, searched for the words in your question and for what it means. Only you can see them."',
        '    ? "Your own files, searched for the words in your question. Only you can see them."',
        T_MEANING,
        "is searched for the words of a question, and for what it means when that is in use",
    ),
    # --- where a document stands, and what the screen is waiting on -------------------------------
    (
        "a document being given its meaning says only Ready for words",
        SAVED,
        'return more\n        ? { state, tone: more.tone, label: more.label }\n        : { state, tone: "ok", label: "Ready for words" };',
        'return { state, tone: "ok", label: "Ready for words" };',
        T_SAVED,
        "is ready for words, with the server's own sentence for how its meaning is going",
    ),
    (
        "a ready document ignores what is true of its meaning",
        SAVED,
        'return { state, tone: "ok", label: more ? more.label : "Ready" };',
        'return { state, tone: "ok", label: "Ready" };',
        T_SAVED,
        "is ready when every passage has its meaning, and says why a document read without any has none",
    ),
    (
        "a tried question that could not run is worded as nothing matched",
        SAVED,
        "return unavailableWords(result.meaning);",
        'return "Nothing in your documents matched.";',
        T_SAVED,
        "says nothing was searched, and why, in the server's words",
    ),
    (
        "meaning being added is not watched",
        SAVED,
        'if (owing.some((doc) => !held(doc)) || header === "working" || takenUp) return "busy";',
        'if (header === "working" || takenUp) return "busy";',
        T_SAVED,
        "is meaning being added: each second, like reading",
    ),
    (
        "a document read before any search model ran is not taken up quickly",
        SAVED,
        'const takenUp = page.search.meaning && docs.some((doc) => doc.meaning?.state === "words_only");',
        "const takenUp = false;",
        T_SAVED,
        "is quick for a document read before any search model ran",
    ),
    (
        "a document that waits is watched every second",
        SAVED,
        'doc.meaning?.state === "waiting" || doc.meaning?.state === "limited";',
        'doc.meaning?.state === "waiting-never";',
        T_SAVED,
        "is meaning that waits, which is asked about every few seconds",
    ),
    (
        "the search model is not named under an answer",
        SAVED,
        "return foundByWords(mode, meaning);",
        "return foundByWords(mode, null);",
        T_SAVED,
        "says the search model when meaning ran, and not when it did not",
    ),
    (
        "the screen never looks again by itself",
        SAVED_HOOK,
        '      going === "idle"\n        ? IDLE_POLL_MS',
        '      going === "idle"\n        ? IDLE_POLL_MS * 1000',
        T_HOOK,
        "asks only for the half-minute look while every document is read",
    ),
    (
        "meaning that waits is asked about as often as meaning being added",
        SAVED_HOOK,
        '        : going === "waiting"\n          ? WAITING_POLL_MS',
        '        : going === "waiting"\n          ? IDLE_POLL_MS',
        T_HOOK,
        "asks every few seconds, not each second, while meaning waits for the search model",
    ),
    (
        "the first look after something starts is no sooner than the rest",
        SAVED_HOOK,
        "? FIRST_POLL_MS\n              : POLL_MS;",
        "? POLL_MS\n              : POLL_MS;",
        T_HOOK,
        "asks again soon after a document turns up unread",
    ),
    # --- the header ---------------------------------------------------------------------------------
    (
        "what words only finds is said whatever the mode",
        SEARCH,
        "{!facts.meaning && (",
        "{true && (",
        T_SEARCH,
        "says what words only finds, for as long as meaning is not in use, and not otherwise",
    ),
    (
        "Rebuild is offered whatever the server said",
        SEARCH,
        'const rebuild = facts.actions.includes("rebuild") ? facts.rebuild : null;',
        "const rebuild = facts.rebuild;",
        T_SEARCH,
        "offers Rebuild only when the server lists it as something to do",
    ),
    (
        "Rebuild is not held while a change is made",
        SEARCH,
        "disabled={busy}",
        "",
        T_SEARCH,
        "holds Rebuild while a change is made",
    ),
    (
        "the link to the console goes to the wrong page",
        SEARCH,
        "href={consolePage(consoleUrl, CONSOLE_ROUTE.library)}",
        'href={consolePage(consoleUrl, "models")}',
        T_SEARCH,
        "links the owner to the Library",
    ),
    (
        "a person who is not the owner is given the link",
        SEARCH,
        "const consoleUrl = me?.owner && me.consoleUrl ? me.consoleUrl : null;",
        "const consoleUrl = me?.consoleUrl ? me.consoleUrl : null;",
        T_SEARCH,
        "gives no link to someone who is not the owner",
    ),
    (
        "the owner is not linked to Backends",
        SEARCH,
        '{step === "backends" && consoleUrl && (',
        "{false && (",
        T_SEARCH,
        "names it, and links the owner to Backends, where it is started",
    ),
    (
        "the search model in use is not named",
        SEARCH,
        '{model && (\n          <span data-testid="documents-search-model" className="font-medium">',
        '{false && (\n          <span data-testid="documents-search-model" className="font-medium">',
        T_SEARCH,
        "says which search model is in use",
    ),
    (
        "a model whose prefixes are not known is not said to be",
        SEARCH,
        "{note && (",
        "{false && (",
        T_SEARCH,
        "says when the search model's prefixes are not known, in the server's words",
    ),
    (
        "how adding meaning is going is not said in the header",
        SEARCH,
        '{adding && <span data-testid="documents-search-adding">{adding}</span>}',
        "",
        T_SEARCH,
        "says how adding meaning is going, as the server says it",
    ),
    (
        "a person who is not the owner is not told whom to ask",
        SEARCH,
        '{asksOwner && <span data-testid="documents-ask-owner">{asksOwner}</span>}',
        "",
        T_SEARCH,
        "tells a person who is not the owner to ask the owner",
    ),
    (
        "the link to the console can reach back to this page",
        SEARCH,
        'rel="noopener noreferrer"\n              className="link inline-flex min-h-7 items-center gap-1"',
        'className="link inline-flex min-h-7 items-center gap-1"',
        T_CONTROLS,
        "with no search model set up, the link to Eugene's console is named and has an id",
    ),
    (
        "the gate does not look at links that open a new tab",
        CONTROLS,
        r""".filter((el) => !/(^|\s)(noopener|noreferrer)(\s|$)/.test(el.getAttribute("rel") ?? ""))""",
        ".filter(() => false)",
        T_CONTROLS,
        "fails for a link that opens a new tab and has no rel",
    ),
    (
        "a word that is not for a screen gets into the words of the header",
        SEARCH,
        " * The page promises nothing the server did not say: words only is said as what it is, with",
        " * The page promises nothing the server did not say, embeddings: words only is said as what it is, with",
        T_WORDS,
        "keeps how documents are read underneath off every screen about them",
    ),
    # --- the screen ---------------------------------------------------------------------------------
    (
        "the screen is not told who is reading",
        SCREEN,
        "                me={me}\n",
        "",
        T_SCREEN,
        "says no search model is set up",
    ),
    (
        "what was said of adding meaning again stays for ever",
        SCREEN,
        "if (data && data.totals.owed === 0) setMeaningRebuilt(null);",
        "if (data && data.totals.owed === -1) setMeaningRebuilt(null);",
        T_SCREEN,
        "stops saying meaning is being added again once none is owed",
    ),
    (
        "the totals of meaning are not shown",
        SCREEN,
        '{meaning && (\n        <p data-testid="documents-meaning-totals" className="text-sm">',
        '{false && meaning && (\n        <p data-testid="documents-meaning-totals" className="text-sm">',
        T_SCREEN,
        "is totalled at the foot",
    ),
    (
        "Rebuild is asked for at another address",
        SCREEN,
        'post<MeaningRebuilt>("/api/documents/meaning/rebuild")',
        'post<MeaningRebuilt>("/api/documents/rebuild")',
        T_SCREEN,
        "offers Rebuild search with its passages and time, and adds meaning again when it is pressed",
    ),
    (
        "the intro does not follow how documents are searched",
        SCREEN,
        "{introWords(data.search)}",
        "{introWords({ ...data.search, meaning: false })}",
        T_SCREEN,
        "says meaning and words, the search model in use and where it runs",
    ),
    (
        "an empty list does not follow how documents are searched",
        SCREEN,
        '<p className="help">{emptyWords(data.search)}</p>',
        '<p className="help">{emptyWords({ ...data.search, meaning: false })}</p>',
        T_SCREEN,
        "says what an empty list is for, as it is searched",
    ),
    (
        "Test a question is not told how documents are searched",
        SCREEN,
        "<TestQuestion search={data.search} />",
        "<TestQuestion />",
        T_SCREEN,
        "does not offer one way at a time where no search model has been set up",
    ),
    (
        "a document's refused passages are not noted on its row",
        ROW,
        "const notes = [...doc.notes, ...(doc.meaning?.notes ?? [])];",
        "const notes = [...doc.notes];",
        T_SCREEN,
        "notes each passage the search model refused",
    ),
    (
        "the Documents page is not told who is reading",
        APP,
        "            me={phase.me}\n            onClose={() => open(chatId)}\n            onAsk={() => void askAboutDocuments()}",
        "            onClose={() => open(chatId)}\n            onAsk={() => void askAboutDocuments()}",
        T_APP,
        "is told who is reading, and where Eugene's console is",
    ),
    # --- Test a question ----------------------------------------------------------------------------
    (
        "the way that was chosen is not asked for",
        TRY,
        "ways ? { question: asked, ways } : { question: asked },",
        "{ question: asked },",
        T_TRY,
        "asks the server for the one way that was chosen, by its name",
    ),
    (
        "one way at a time is offered where there is no second way",
        TRY,
        'const offerWays = !search || (search.code !== "none_set_up" && search.code !== "no_gateway");',
        "const offerWays = true;",
        T_TRY,
        "unless no search model has been set up",
    ),
    (
        "how it was searched is said for a question that searched nothing",
        TRY,
        'const SEARCHED: readonly TestedQuestion["status"][] = ["found", "nothing_matched"];',
        'const SEARCHED: readonly TestedQuestion["status"][] = ["found", "nothing_matched", "no_words", "no_documents", "unavailable"];',
        T_TRY,
        "says how it was searched only for a question that was searched",
    ),
    (
        "why a search could not run is said twice",
        TRY,
        'found && found.status !== "unavailable"',
        "found",
        T_TRY,
        "says a search that none of its ways could run, and that nothing was searched, once",
    ),
    (
        "how a passage was found is not said",
        TRY,
        "{viaWords(hit.via, matched !== null)}",
        '{""}',
        T_TRY,
        "says words for a passage that has the words, with the places of that way",
    ),
    (
        "where each way put a passage is not said",
        TRY,
        "const places = placesWords(hit.why.ranks, hit.why.similarity);",
        "const places = null;",
        T_TRY,
        "says both, with the words it has and where each way put it",
    ),
    (
        "the way that was chosen is not said while the choice is closed",
        TRY,
        '{way !== "both" && chosen && <span>: {chosen.label.toLowerCase()}</span>}',
        "",
        T_TRY,
        "says in its name which way is chosen when it is closed, so a choice is not hidden",
    ),
    (
        "a question is not said to have been searched the way it was chosen",
        TRY,
        "searchedByWords(found.mode, result?.chose ?? false)",
        "searchedByWords(found.mode, false)",
        T_TRY,
        "says a question was searched the way it was chosen",
    ),
    # --- under an answer, and in a chat's drawer -----------------------------------------------------
    (
        "a row does not say how its passage was found",
        PASSAGES,
        '· <span className="whitespace-nowrap">{viaTag(source.via)}</span>',
        "",
        T_MESSAGE,
        "is said on each row, short",
    ),
    (
        "a row's name does not say how its passage was found",
        PASSAGES,
        "${passageTitle(source)}, ${viaTag(source.via)}${cited",
        "${passageTitle(source)}${cited",
        T_MESSAGE,
        "is in the row's name too",
    ),
    (
        "a passage found by meaning does not say it has none of the words",
        PASSAGES,
        '{source.via !== "words" && (',
        "{false && (",
        T_MESSAGE,
        "says in full, once the row is open",
    ),
    (
        "a note about meaning has no mark of its own",
        PASSAGES,
        'data-testid={isMeaningNote(note.code) ? "passage-meaning-note" : undefined}',
        "data-testid={undefined}",
        T_MESSAGE,
        "is shown in its own words, beside how it was found",
    ),
    (
        "a search that was not run says nothing of meaning",
        PASSAGES,
        ".filter((note) => isMeaningNote(note.code))",
        ".filter(() => false)",
        T_MESSAGE,
        "is also said under a search that was not run for another reason",
    ),
    (
        "the passages under an answer do not name the search model",
        PASSAGES,
        "modeWords(record.mode, record.meaning)",
        "modeWords(record.mode)",
        T_MESSAGE,
        "says meaning and words, and which search model, when meaning ran",
    ),
    (
        "nothing matched does not say how it was searched",
        PASSAGES,
        "const how = searchedByWords(record.mode, false);",
        "const how = null;",
        T_MESSAGE,
        "nothing matched, by words and meaning",
    ),
    (
        "the drawer does not say how documents are searched",
        CHIP,
        "<SearchMode search={own.search} />",
        "",
        T_CHIP,
        "says meaning and words, in the server's own headline, with nothing more",
    ),
]

# (label, file, before, after, test file, a fragment of the test's name)
PYTHON_CASES: list[tuple[str, str, str, str, str, str]] = [
    # === the page and the server agree (Python) ==========================================
    (
        "the page does not read a document's meaning",
        TYPES,
        "  indexedAt: number | null;\n  meaning: DocumentMeaning | null;\n}",
        "  indexedAt: number | null;\n}",
        PAGE_AGREES,
        "test_a_saved_document_has_the_fields_the_page_reads_in_every_state_it_can_be_in",
    ),
    (
        "the page has no words for a search that none of its ways could run",
        TYPES,
        '  status: "found" | "nothing_matched" | "no_words" | "no_documents" | "unavailable";',
        '  status: "found" | "nothing_matched" | "no_words" | "no_documents";',
        PAGE_AGREES,
        "test_a_question_that_was_tried_has_the_fields_the_page_reads_whatever_it_found",
    ),
    (
        "the page does not read what meaning came to for a tried question",
        TYPES,
        '  mode: "words" | "meaning" | "meaning and words" | "none";\n  meaning: MeaningRun;\n  passages: TestedPassage[];',
        '  mode: "words" | "meaning" | "meaning and words" | "none";\n  passages: TestedPassage[];',
        PAGE_AGREES,
        "test_a_question_that_was_tried_has_the_fields_the_page_reads_whatever_it_found",
    ),
    (
        "the page does not read what Rebuild would do",
        TYPES,
        "  adding: AddingView | null;\n  rebuild: RebuildView | null;\n}",
        "  adding: AddingView | null;\n}",
        PAGE_AGREES,
        "test_the_screens_data_has_the_fields_the_page_reads_down_to_each_number",
    ),
    (
        "the page does not read what is owed",
        TYPES,
        "    embedded: number;\n    owed: number;\n  };",
        "    embedded: number;\n  };",
        PAGE_AGREES,
        "test_the_screens_data_has_the_fields_the_page_reads_down_to_each_number",
    ),
    (
        "the page does not read what meaning came to for an answer",
        TYPES,
        "   * why; null for a search that was not run (MR5). */\n  meaning: MeaningRun | null;\n}",
        "   * why; null for a search that was not run (MR5). */\n}",
        PAGE_AGREES,
        "test_a_search_record_has_the_fields_the_page_reads_in_each_thing_it_can_say",
    ),
    (
        "the page has no words for a code the server says",
        TYPES,
        '  | "damaged"\n  | "no_gateway";',
        '  | "no_gateway";',
        MEANING_AGREES,
        "test_every_code_the_header_can_say_is_one_the_page_has_words_for",
    ),
    (
        "the page has no look for a code the server says",
        MEANING,
        '  not_search_model: "warn",\n',
        "",
        MEANING_AGREES,
        "test_every_code_the_header_can_say_is_one_the_page_has_words_for",
    ),
    (
        "the page has nothing to do for an action the server offers",
        TYPES,
        'export type SearchAction = "rebuild" | "get_search_model";',
        'export type SearchAction = "rebuild";',
        MEANING_AGREES,
        "test_every_action_the_header_offers_is_one_the_page_has_a_button_or_a_link_for",
    ),
    (
        "the page tells a person to ask the owner for a code the server never says",
        MEANING,
        '  "outside_only",\n];',
        '  "outside_only",\n  "never_said",\n];',
        MEANING_AGREES,
        "test_the_codes_that_name_the_console_are_codes_the_server_says",
    ),
    (
        "the page does not read the prefixes of a search model",
        TYPES,
        "  builtWith: string | null;\n  dimension: number | null;\n  prefixes: SearchPrefixes;\n  floor: number | null;",
        "  builtWith: string | null;\n  dimension: number | null;\n  floor: number | null;",
        MEANING_AGREES,
        "test_the_header_has_the_fields_the_page_reads_down_to_each_number",
    ),
    (
        "the page does not read where each way put a passage",
        TYPES,
        "export interface Places {\n  words?: number;\n  meaning?: number;\n}",
        "export interface Places {\n  words?: number;\n}",
        MEANING_AGREES,
        "test_a_passage_found_by_meaning_has_the_fields_the_page_reads",
    ),
    (
        "the page asks for a way the server has no name for",
        TYPES,
        'export type Way = "words" | "meaning";',
        'export type Way = "words" | "meanings";',
        MEANING_AGREES,
        "test_a_passage_found_by_meaning_has_the_fields_the_page_reads",
    ),
    (
        "the page has no words for a state of a document's meaning",
        TYPES,
        'export type MeaningState = "ready" | "words_only" | "adding" | "waiting" | "limited";',
        'export type MeaningState = "ready" | "words_only" | "adding" | "waiting";',
        MEANING_AGREES,
        "test_the_key_at_its_limit_is_a_state_the_page_has_words_for",
    ),
    (
        "the page has no words for a state of adding",
        TYPES,
        '  state: "idle" | "working" | "waiting" | "limited";\n  message: string | null;\n  passages: number;',
        '  state: "idle" | "working" | "waiting";\n  message: string | null;\n  passages: number;',
        MEANING_AGREES,
        "test_the_key_at_its_limit_is_a_state_the_page_has_words_for",
    ),
    (
        "a time in the shared table is not the one the server says",
        DURATIONS,
        '  150,\n  "about 2 minutes"',
        '  150,\n  "about 3 minutes"',
        MEANING_AGREES,
        "test_a_time_is_said_the_way_the_server_says_it",
    ),
    (
        "a sentence in the shared table is not the one the server says",
        CODES_TABLE,
        '"headline": "Searching by words only. No search model is running.",\n  "detail": "Searching by words only: no search model is set up.",',
        '"headline": "Searching by words only.",\n  "detail": "Searching by words only: no search model is set up.",',
        MEANING_AGREES,
        "test_the_sentences_the_pages_tests_use_are_the_ones_the_server_says",
    ),
]

# (label, file, before, after, the Chrome check that must fail)
CHROME_CASES: list[tuple[str, str, str, str, str]] = [
    # === what only a real browser sees ===================================================
    (
        "the open screen never looks again by itself",
        SAVED_HOOK,
        '      going === "idle"\n        ? IDLE_POLL_MS',
        '      going === "idle"\n        ? IDLE_POLL_MS * 1000',
        LIBRARY_BROWSER,
    ),
    (
        "a document read before any search model ran is not taken up",
        SAVED,
        'const takenUp = page.search.meaning && docs.some((doc) => doc.meaning?.state === "words_only");',
        "const takenUp = false;",
        LIBRARY_BROWSER,
    ),
    (
        "the page is not told who is reading, so the owner has no link",
        APP,
        "            me={phase.me}\n            onClose={() => open(chatId)}\n            onAsk={() => void askAboutDocuments()}",
        "            onClose={() => open(chatId)}\n            onAsk={() => void askAboutDocuments()}",
        LIBRARY_BROWSER,
    ),
    (
        "Sources does not say how a passage was found",
        PASSAGES,
        'data-testid="passage-via"',
        'data-testid="passage-via-x"',
        LIBRARY_BROWSER,
    ),
    (
        "one way at a time sends no way",
        TRY,
        "ways ? { question: asked, ways } : { question: asked },",
        "{ question: asked },",
        LIBRARY_BROWSER,
    ),
    (
        "the drawer does not say how documents are searched",
        CHIP,
        "<SearchMode search={own.search} />",
        '<SearchMode search={{ ...own.search, headline: "Searching." }} />',
        LIBRARY_BROWSER,
    ),
    (
        "a search model that stopped is not named",
        MEANING,
        'const said = (facts.detail ?? "").trim();',
        'const said = (facts.detail ?? "").trim().slice(0, 0);',
        LIBRARY_BROWSER,
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

    backup = Path(tempfile.mkdtemp(prefix="workbench-meaning-page-sabotage-"))
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
