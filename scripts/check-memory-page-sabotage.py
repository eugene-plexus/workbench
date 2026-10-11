"""Prove the Memory page's checks are observed (MR1, the page side).

Each case breaks one thing in the page's code, and the test that is named
for it must fail. Files are restored from exact bytes kept beside the run,
never from Git. A mutation that stops the code compiling does not count as a
caught defect, and neither does a test name that runs nothing.

  python scripts/check-memory-page-sabotage.py             # every unit-test case
  python scripts/check-memory-page-sabotage.py --anchors   # only check each anchor is unique
  python scripts/check-memory-page-sabotage.py --browser   # also the Chrome cases (needs WORKBENCH_PLAYWRIGHT)
  python scripts/check-memory-page-sabotage.py --chrome-only   # only the Chrome cases
  python scripts/check-memory-page-sabotage.py --label TEXT ...  # only the cases whose label has TEXT

The Chrome cases rebuild the page for each break and run
`tests/test_memory_browser.py` (or, for the top bar's one row, the chat title's
room beside the chip and the Memory page's contrast,
`tests/test_reading_browser.py`); the page is rebuilt from the
restored source at the end. Run it with every ambient EUGENE_PLEXUS_* variable
cleared.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
SRC = "web/src/"
NPX = "npx.cmd" if os.name == "nt" else "npx"
NPM = "npm.cmd" if os.name == "nt" else "npm"
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

API = SRC + "lib/api.ts"
MEMORY = SRC + "lib/memory.ts"
REMEMBER = SRC + "components/RememberThis.tsx"
MESSAGE = SRC + "components/MessageView.tsx"
TOAST = SRC + "components/MemoryToast.tsx"
CHIP = SRC + "components/MemoryChip.tsx"
CHAT = SRC + "components/ChatView.tsx"
COMPOSER = SRC + "components/Composer.tsx"
APP = SRC + "App.tsx"
PAGE = SRC + "components/Memory.tsx"
KEEP = SRC + "lib/useKeepFocus.ts"
STYLES = SRC + "styles.css"

MEMORY_BROWSER = "tests/test_memory_browser.py"
READING_BROWSER = "tests/test_reading_browser.py"

T_API = "src/lib/api.test.ts"
T_LIB = "src/lib/memory.test.ts"
T_REMEMBER = "src/components/RememberThis.test.tsx"
T_TOAST = "src/components/MemoryToast.test.tsx"
T_CHIP = "src/components/MemoryChip.test.tsx"
T_CHAT = "src/components/ChatView.test.tsx"
T_COMPOSER = "src/components/Composer.test.tsx"
T_APP = "src/App.test.tsx"
T_PAGE = "src/components/Memory.test.tsx"
T_WORDS = "src/words.test.ts"
T_KEEP = "src/lib/useKeepFocus.test.tsx"
T_STYLES = "src/memoryStyles.test.ts"
T_CONTROLS = "src/memoryControls.test.tsx"

# (label, file, before, after, test file, a fragment of the test's name)
CASES: list[tuple[str, str, str, str, str, str]] = [
    # --- what the server said ------------------------------------------------
    (
        "a refusal loses the fields it came with",
        API,
        "  throw new ApiError(message, response.status, detail);",
        "  throw new ApiError(message, response.status);",
        T_API,
        "keeps the server's sentence, and the fields",
    ),
    (
        "an export is asked for without the request secret",
        API,
        '    await fetch(path, { credentials: "same-origin", headers: headers() }),',
        '    await fetch(path, { credentials: "same-origin" }),',
        T_API,
        "is asked for with the request secret",
    ),
    (
        "put sends the wrong verb",
        API,
        'api<T>(path, { method: "PUT", body: JSON.stringify(body) });',
        'api<T>(path, { method: "PATCH", body: JSON.stringify(body) });',
        T_API,
        "sends its body as JSON with the verb PUT",
    ),
    # --- a note is one line of at most 240 characters -------------------------
    (
        "length counted in UTF-16 units, not characters",
        MEMORY,
        "  return Array.from(text).length;",
        "  return text.length;",
        T_LIB,
        "counts characters as the server does",
    ),
    (
        "the limit is 250",
        MEMORY,
        "export const NOTE_LIMIT = 240;",
        "export const NOTE_LIMIT = 250;",
        T_LIB,
        "counts characters as the server does",
    ),
    (
        "a cut ignores where the words end",
        MEMORY,
        "  return (at > limit * 0.6 ? cut.slice(0, at) : cut).trim();",
        "  return cut.trim();",
        T_LIB,
        "is cut at a word",
    ),
    (
        "what a secret looks like is not said",
        MEMORY,
        "  return refusal.looksLike ? `${said} It looks like ${refusal.looksLike}.` : said;",
        "  return said;",
        T_LIB,
        "says what a secret looks like",
    ),
    (
        "any object with no message is a refusal",
        MEMORY,
        '  if (typeof found.message !== "string") return null;',
        "  // no check",
        T_LIB,
        "reads a refusal off an error",
    ),
    (
        "the chip says sent for what is only ready",
        MEMORY,
        '      label: `Memory: ${how} ${view.frozen ? "sent" : "ready"}`,',
        '      label: `Memory: ${how} ${view.frozen ? "ready" : "sent"}`,',
        T_LIB,
        "how many notes the next message carries",
    ),
    (
        "a model held back is not a warning",
        MEMORY,
        '            : "Memory not sent: outside model",\n        short: "",\n        tone: "warn",',
        '            : "Memory not sent: outside model",\n        short: "",\n        tone: "quiet",',
        T_LIB,
        "outside_model",
    ),
    (
        "one note is two notes",
        MEMORY,
        "  return `${count.toLocaleString()} ${count === 1 ? one : many}`;",
        "  return `${count.toLocaleString()} ${many}`;",
        T_LIB,
        "counts and groups",
    ),
    (
        "the year is never said",
        MEMORY,
        '    ...(date.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),',
        "    ...{},",
        T_LIB,
        "says the day a note was kept",
    ),
    # --- Remember this ---------------------------------------------------------
    (
        "a message's note is recorded as typed",
        REMEMBER,
        '        source: "button",\n        chatId,',
        '        source: "typed",\n        chatId,',
        T_REMEMBER,
        "is kept at once, as a note from a button",
    ),
    (
        "the chat and message a note came from are not recorded",
        REMEMBER,
        '        source: "button",\n        chatId,\n        messageId: message.id,\n      });',
        '        source: "button",\n      });',
        T_REMEMBER,
        "is kept at once, as a note from a button",
    ),
    (
        "a long message goes to the server uncut",
        REMEMBER,
        "    if (note.over) {",
        "    if (false) {",
        T_REMEMBER,
        "is not cut quietly",
    ),
    (
        "Keep note is open to a note over the limit",
        REMEMBER,
        "disabled={saving || tooLong || !editing.draft.trim()}",
        "disabled={saving || !editing.draft.trim()}",
        T_REMEMBER,
        "will not keep more than 240",
    ),
    (
        "memory off for the person is not said",
        REMEMBER,
        '    if (memory?.off === "you") {',
        "    if (false) {",
        T_REMEMBER,
        "keeps nothing and says so in the server's words",
    ),
    (
        "focus is not given back to the button",
        REMEMBER,
        "    returnFocus.current = true;\n    setEditing(null);",
        "    setEditing(null);",
        T_REMEMBER,
        "puts focus back",
    ),
    (
        "the draft before a search is kept as the answer",
        MESSAGE,
        'text: message.role === "user" ? message.content : answerParts(message).answer,',
        "text: message.content,",
        T_REMEMBER,
        "keeps the answer, not the draft",
    ),
    (
        "an answer still being written can be remembered",
        MESSAGE,
        '      readOnly || message.redacted || message.status === "running" || editing !== null',
        "      readOnly || message.redacted || editing !== null",
        T_REMEMBER,
        "is not there for an answer still being written",
    ),
    # --- the toast --------------------------------------------------------------
    (
        "Undo does not delete the note",
        TOAST,
        "      await del(`/api/memory/notes/${encodeURIComponent(note.id)}`);\n"
        '      setShown({ kind: "undone", note });',
        '      setShown({ kind: "undone", note });',
        T_TOAST,
        "takes the note back on Undo",
    ),
    (
        "the toast goes after one second",
        TOAST,
        "      ? UNDO_MS\n",
        "      ? 1000\n",
        T_TOAST,
        "is ten seconds for something that can be undone",
    ),
    (
        "a pointer on the toast does not hold it",
        TOAST,
        "    if (stay === null || held || busy) return;",
        "    if (stay === null || busy) return;",
        T_TOAST,
        "waits while the pointer is on it",
    ),
    (
        "a parent that draws again starts the clock over",
        TOAST,
        "  }, [stay, held, busy, shown]);",
        "  }, [stay, held, busy, shown, onClose]);",
        T_TOAST,
        "is not started over by a parent",
    ),
    (
        "a candidate's Delete deletes the first note",
        TOAST,
        "                        onClick={() => void forget(note)}",
        "                        onClick={() => void forget(shown.candidates[0]!)}",
        T_TOAST,
        "deletes only the one that was picked",
    ),
    (
        "a refusal is only advice",
        TOAST,
        '      role={alert ? "alert" : "status"}',
        '      role="status"',
        T_TOAST,
        "says why in plain words",
    ),
    (
        "a refusal does not say the message was sent",
        TOAST,
        '              {shown.messageSent ? " Your message was still sent." : ""}',
        '              {""}',
        T_TOAST,
        "says why in plain words",
    ),
    (
        "words already kept are said to be new",
        TOAST,
        '    return { kind: "remembered", note: command.remembered, created: command.created };',
        '    return { kind: "remembered", note: command.remembered, created: true };',
        T_TOAST,
        "is a note kept, or the same words",
    ),
    (
        "Undo for words that were already kept",
        TOAST,
        "            {actions(shown.created ? () => void takeBack(shown.note) : null)}",
        "            {actions(() => void takeBack(shown.note))}",
        T_TOAST,
        "offers no Undo for words that were already kept",
    ),
    (
        "a question or a refusal goes by itself",
        TOAST,
        '      : shown.kind === "undone"\n        ? 4000\n        : null;',
        '      : shown.kind === "undone"\n        ? 4000\n        : 4000;',
        T_TOAST,
        "stays until it is dismissed",
    ),
    # --- the chip and its drawer -------------------------------------------------
    (
        "turning memory on sets it on instead of clearing it",
        CHIP,
        'void change({ memory: event.target.checked ? null : "off" });',
        'void change({ memory: event.target.checked ? "on" : "off" });',
        T_CHIP,
        "turns it back on by clearing the setting",
    ),
    (
        "Send for this chat clears the choice",
        CHIP,
        "onSendHere={() => void change({ memoryExternal: true })}",
        "onSendHere={() => void change({ memoryExternal: null })}",
        T_CHIP,
        "sends for this chat on a press",
    ),
    (
        "Refresh memory asks another route",
        CHIP,
        "onAdopt(await post<ChatMemoryView>(`/api/chats/${chatId}/memory/refresh`));",
        "onAdopt(await post<ChatMemoryView>(`/api/chats/${chatId}/memory/reload`));",
        T_CHIP,
        "takes the notes again from the server",
    ),
    (
        "a change in the notes goes unsaid",
        CHIP,
        "{own.sending && own.frozen && own.changed && (",
        "{false && (",
        T_CHIP,
        "says the notes changed",
    ),
    (
        "the exact text is not shown",
        CHIP,
        "              {own.text && (",
        "              {false && (",
        T_CHIP,
        "is what the model was given, byte for byte",
    ),
    (
        "the switch waits for the server before it moves",
        CHIP,
        'checked={busy && wanted !== null ? wanted : own.chatSwitch === "on"}',
        'checked={own.chatSwitch === "on"}',
        T_CHIP,
        "turns memory off for the chat at once",
    ),
    (
        "no way to send to a model outside the machines",
        CHIP,
        '{code === "outside_model" && view.outside?.canSendForThisChat && (',
        "{false && (",
        T_CHIP,
        "sends for this chat on a press",
    ),
    (
        "the drawer reads nothing when it opens",
        CHIP,
        "    void onReload();\n  }, [onReload]);",
        "  }, [onReload]);",
        T_CHIP,
        "reads again when it opens",
    ),
    (
        "the drawer is not a dialog over the whole window",
        CHIP,
        '      role={overlay ? "dialog" : undefined}',
        "      role={undefined}",
        T_CHIP,
        "is a dialog, takes focus",
    ),
    (
        "Escape leaves the drawer open",
        CHIP,
        '    if (event.key === "Escape" && !event.nativeEvent.isComposing) {',
        '    if (event.key === "Esc" && !event.nativeEvent.isComposing) {',
        T_CHIP,
        "closes with its button and with Escape",
    ),
    (
        "the chip has no name of its own",
        CHIP,
        "      aria-label={words.label}\n",
        "",
        T_CHIP,
        "is a button named by its words",
    ),
    # --- the chat ----------------------------------------------------------------
    (
        "what a command did is not said",
        CHAT,
        "            say(outcomeOf(notes));\n",
        "",
        T_CHAT,
        "says what was kept, in a toast over the thread",
    ),
    (
        "the chip is not read again after a send",
        CHAT,
        "            say(outcomeOf(notes));\n            void memory.reload();",
        "            say(outcomeOf(notes));",
        T_CHAT,
        "says what was kept, in a toast over the thread",
    ),
    (
        "Remember this where the owner has turned Memory off",
        CHAT,
        '    chat.readOnly || code === "install_off"',
        "    chat.readOnly",
        T_CHAT,
        "is not offered where the owner has turned Memory off",
    ),
    (
        "the chip ignores the chat's model",
        CHAT,
        '    `${detail?.chat.model ?? ""}|${detail?.chat.settings.memory ?? ""}|'
        '${detail?.chat.settings.memoryExternal ?? ""}`,',
        '    `|${detail?.chat.settings.memory ?? ""}|${detail?.chat.settings.memoryExternal ?? ""}`,',
        T_CHAT,
        "reads the notes again when the chat's model changes",
    ),
    (
        "the toast covers the jump pill",
        CHAT,
        '${away ? "bottom-16" : "bottom-4"}',
        "bottom-4",
        T_CHAT,
        "sits above the jump pill",
    ),
    (
        "a new chat is not told its model",
        COMPOSER,
        "    if (chat.model || !chosen || told.current === chosen.id) return;",
        "    return;",
        T_COMPOSER,
        "tells the chat, once",
    ),
    (
        "a new chat is told again and again",
        COMPOSER,
        "    told.current = chosen.id;\n",
        "",
        T_COMPOSER,
        "asks once, however often",
    ),
    (
        "what a command did is not passed on",
        COMPOSER,
        "sent.answer, sent.memory);",
        "sent.answer, undefined);",
        T_COMPOSER,
        "passes on, with the message",
    ),
    # --- the top bar --------------------------------------------------------------
    (
        "the Memory tab is not marked as the page",
        APP,
        '              aria-current={view === "memory" ? "page" : undefined}',
        "              aria-current={undefined}",
        T_APP,
        "opens at /memory, marks the page",
    ),
    (
        "the address does not say /memory",
        APP,
        '    if (window.location.pathname !== "/memory") window.history.pushState(null, "", "/memory");\n',
        "",
        T_APP,
        "opens at /memory, marks the page",
    ),
    (
        "the tab is named for something else",
        APP,
        '  memory: "Memory",',
        '  memory: "Notes",',
        T_APP,
        "opens at /memory, marks the page",
    ),
    (
        "the back button forgets the page",
        APP,
        "      setMemoryOpen(memoryFromPath(window.location.pathname));\n      setMediaPerson(null);",
        "      setMediaPerson(null);",
        T_APP,
        "follows the browser's back button",
    ),
    (
        "opening a chat leaves the page open",
        APP,
        "    setChatId(id);\n    setMedia(null);\n    setMemoryOpen(false);",
        "    setChatId(id);\n    setMedia(null);",
        T_APP,
        "goes back to the chat it came from",
    ),
    # --- the page -------------------------------------------------------------------
    (
        "a deleted note cannot be had back",
        PAGE,
        "              setUndo(note);",
        "              void 0;",
        T_PAGE,
        "deletes a note for real at once",
    ),
    (
        "Unpin pins",
        PAGE,
        "{ pinned: !note.pinned }",
        "{ pinned: true }",
        T_PAGE,
        "pins a note, which moves it",
    ),
    (
        "delete all without the word typed",
        PAGE,
        'disabled={busy || typed.trim().toLowerCase() !== "delete"}',
        "disabled={busy}",
        T_PAGE,
        "waits for the word",
    ),
    (
        "delete all without saying all",
        PAGE,
        'api<{ deleted: number }>("/api/memory/notes?confirm=all", { method: "DELETE" }),',
        'api<{ deleted: number }>("/api/memory/notes", { method: "DELETE" }),',
        T_PAGE,
        "deletes every note, says how many",
    ),
    (
        "focus is left on nothing after delete all",
        PAGE,
        "              setFocusId(`${uid}-notes`);\n            }}",
        "            }}",
        T_PAGE,
        "deletes every note, says how many",
    ),
    (
        "where the owner turned memory off a note can be added",
        PAGE,
        "{!off && notes.length > 0 && <AddNote busy={busy} working={working} />}",
        "{notes.length > 0 && <AddNote busy={busy} working={working} />}",
        T_PAGE,
        "leaves notes the person already has",
    ),
    (
        "where the owner turned memory off a note can be edited",
        PAGE,
        '        {!locked && (\n          <button\n            type="button"\n            id={`edit-${note.id}`}',
        '        {true && (\n          <button\n            type="button"\n            id={`edit-${note.id}`}',
        T_PAGE,
        "leaves notes the person already has",
    ),
    (
        "the owner's never can be changed here",
        PAGE,
        "disabled={busy || ownerSet}",
        "disabled={busy}",
        T_PAGE,
        "says the owner's never is final",
    ),
    (
        "the two boxes keep notes that are not always used",
        PAGE,
        'await post("/api/memory/notes", { text: job.text, kind: job.kind, pinned: true });',
        'await post("/api/memory/notes", { text: job.text, kind: job.kind, pinned: false });',
        T_PAGE,
        "keeps each line as a note that is always used",
    ),
    (
        "the limit does not stop the rest of the lines",
        PAGE,
        '          if (why === "full" || why === "off") {',
        "          if (false) {",
        T_PAGE,
        "stops at the limit",
    ),
    (
        "lines left in the boxes go with the boxes",
        PAGE,
        "      onLeft(left.length);",
        "      void left;",
        T_PAGE,
        "keeps what it can, and leaves the lines",
    ),
    (
        "a budget below the floor is saved",
        PAGE,
        "tokens < floor ||",
        "tokens < 0 ||",
        T_PAGE,
        "will not save",
    ),
    (
        "JSON is asked for as Markdown",
        PAGE,
        "`/api/memory/export?format=${format}`",
        "`/api/memory/export?format=md`",
        T_PAGE,
        "does the same for JSON",
    ),
    (
        "an export is fetched and not saved",
        PAGE,
        "      saveFile(file.blob, file.name ?? `workbench-memory.${format}`);",
        "      void file;",
        T_PAGE,
        "fetches the Markdown as a file",
    ),
    (
        "a chat that was deleted is not said",
        PAGE,
        'return chatsLoaded ? "From a chat that was deleted" : "From a chat";',
        'return "From a chat";',
        T_PAGE,
        "say when the chat a note came from was deleted",
    ),
    (
        "a note is added with its spaces",
        PAGE,
        "{ text: text.trim(), kind, pinned }),",
        "{ text, kind, pinned }),",
        T_PAGE,
        "keeps the note with its kind",
    ),
    (
        "the count is of units, not characters",
        PAGE,
        "  const length = characters(text.trim());",
        "  const length = text.trim().length;",
        T_PAGE,
        "so 240 emoji fit and 241 do not",
    ),
    (
        "the notes are said to be used when relevant",
        PAGE,
        "                  Used when there is room",
        "                  Used when relevant",
        T_PAGE,
        "does not say they are used when relevant",
    ),
    (
        "the owner is said to read chats whatever the owner may",
        PAGE,
        "            {data.ownerReadsChats && (",
        "            {true && (",
        T_PAGE,
        "says that the owner can read chats",
    ),
    (
        "the tokens and the room are the wrong way round",
        PAGE,
        "{tokenWords(usage.tokens, usage.budget)}",
        "{tokenWords(usage.budget, usage.tokens)}",
        T_PAGE,
        "says tokens and notes the way the design does",
    ),
    (
        "the page does not say only the person can see it",
        PAGE,
        "                What Workbench remembers about you. Only you can see this page.",
        "                What Workbench remembers about you.",
        T_PAGE,
        "says in a line what it is",
    ),
    # --- the keyboard, and a model nobody can place -----------------------------------
    (
        "a held control does not get the keyboard back",
        KEEP,
        "      target.focus();",
        "      void target;",
        T_KEEP,
        "is given back to the control that had it",
    ),
    (
        "the keyboard is taken from where the person went",
        KEEP,
        " && (!now || now === document.body)) {",
        ") {",
        T_KEEP,
        "does not take focus from where the person has gone",
    ),
    (
        "the drawer does not note where the keyboard was",
        CHIP,
        "  async function change(settings: Settings) {\n    keepFocus();\n",
        "  async function change(settings: Settings) {\n",
        T_CHIP,
        "keeps the keyboard where it was",
    ),
    (
        "the page does not note where the keyboard was",
        PAGE,
        "  const working: Working = async (work) => {\n    keepFocus();\n",
        "  const working: Working = async (work) => {\n",
        T_PAGE,
        "keeps the keyboard on the switch",
    ),
    (
        "a note kept from a button does not give the keyboard back",
        REMEMBER,
        "  async function keep(words: string, fromEditor: boolean) {\n    returnFocus.current = true;\n",
        "  async function keep(words: string, fromEditor: boolean) {\n",
        T_REMEMBER,
        "keeps the keyboard on the button",
    ),
    (
        "a model nobody can place is said to be outside",
        MEMORY,
        '          view.model?.locality === "unknown"',
        "          false",
        T_LIB,
        "does not say a model is outside when it is only not known",
    ),
    (
        "Escape does not close a toast",
        TOAST,
        '        if (event.key === "Escape") {',
        '        if (event.key === "Esc") {',
        T_TOAST,
        "closes on Escape",
    ),
    (
        "a toast closed from the keyboard leaves it nowhere",
        TOAST,
        '    if (inside) queueMicrotask(() => document.getElementById("message-composer")?.focus());',
        "    void inside;",
        T_TOAST,
        "closes on Escape",
    ),
    (
        "the keyboard is not given a place in the toast after Undo",
        TOAST,
        '    if (!now || now === document.body) root.current?.querySelector("button")?.focus();',
        "    void now;",
        T_TOAST,
        "keeps the keyboard in the toast after Undo",
    ),
    (
        "the keyboard is taken into a toast it was not in",
        TOAST,
        "    keyboardWasIn.current = root.current?.contains(document.activeElement) ?? false;",
        "    keyboardWasIn.current = true;",
        T_TOAST,
        "does not take the keyboard into the toast when it was not there",
    ),
    (
        "an owner-off page with no notes still shows its lists and its export",
        PAGE,
        "  const bare = off && notes.length === 0;",
        "  const bare = false;",
        T_PAGE,
        "says so, and offers nothing that would keep a note",
    ),
    (
        "memory turned off does not say who turned it off",
        PAGE,
        "Memory is off because you turned it off. ",
        "Memory is off. ",
        T_PAGE,
        "shows Use memory as it is in effect",
    ),
    # --- the words ---------------------------------------------------------------------
    (
        "a word for how it works underneath on the way a person reads it",
        PAGE,
        "/** The lines of a box, one note each: trimmed, with the empty ones left out. */",
        "/** The lines of a box (a vector of notes), one each. */",
        T_WORDS,
        "keeps how memory works underneath",
    ),
    # --- every control has a name and a test id ------------------------------------------
    (
        "the drawer's Try again has no test id",
        CHIP,
        'data-testid="memory-drawer-retry"',
        'data-x="memory-drawer-retry"',
        T_CONTROLS,
        "while it reads, and when it could not",
    ),
    (
        "the link back to a note's chat has no test id",
        PAGE,
        'data-testid="memory-note-from-link"',
        'data-x="memory-note-from-link"',
        T_CONTROLS,
        "with notes of every kind, from every source: every control",
    ),
    (
        "the exact text's disclosure has no test id",
        CHIP,
        'data-testid="memory-exact-summary"',
        'data-x="memory-exact-summary"',
        T_CONTROLS,
        "with the exact text it sent",
    ),
    (
        "the toast's Dismiss has no name",
        TOAST,
        '        aria-label="Dismiss"\n',
        "",
        T_CONTROLS,
        "the toast > a note kept",
    ),
    (
        "the chip has no name",
        CHIP,
        "      aria-label={words.label}\n",
        "",
        T_CONTROLS,
        "the Memory chip > sending",
    ),
    (
        "Remember this has no test id",
        REMEMBER,
        'data-testid="remember-this"',
        'data-x="remember-this"',
        T_CONTROLS,
        "is a button with a name and a test id",
    ),
    # --- the stylesheet: the top bar with a fifth page ----------------------------------
    (
        "the tabs keep their wide padding on a phone",
        STYLES,
        "      padding-inline: 0.25rem;",
        "      padding-inline: 0.5rem;",
        T_STYLES,
        "tightens each tab's padding below 40 rem",
    ),
    (
        "the small-phone type is not the floor",
        STYLES,
        "  @media (max-width: 22.99rem) {\n    .nav-tab {\n      font-size: 0.8125rem;",
        "  @media (max-width: 22.99rem) {\n    .nav-tab {\n      font-size: 0.875rem;",
        T_STYLES,
        "goes to the 13 px floor only on a phone narrower than 368 px",
    ),
    (
        "the type goes below the floor",
        STYLES,
        "  @media (max-width: 22.99rem) {\n    .nav-tab {\n      font-size: 0.8125rem;",
        "  @media (max-width: 22.99rem) {\n    .nav-tab {\n      font-size: 0.75rem;",
        T_STYLES,
        "goes to the 13 px floor only on a phone narrower than 368 px",
    ),
    (
        "the small type reaches a 375 px phone",
        STYLES,
        "  @media (max-width: 22.99rem) {",
        "  @media (max-width: 24rem) {",
        T_STYLES,
        "goes to the 13 px floor only on a phone narrower than 368 px",
    ),
    (
        "the block loses its marker",
        STYLES,
        "  /* ---- MR1: the top bar with a fifth page ---- */",
        "  /* ---- the top bar with a fifth page ---- */",
        T_STYLES,
        "is in the stylesheet once, marked",
    ),
    (
        "the block leaves the components layer",
        STYLES,
        "/* MR1: memory */\n@layer components {",
        "/* MR1: memory */\n@layer utilities {",
        T_STYLES,
        "is in the stylesheet once, marked",
    ),
    (
        "a rule slips in between the end mark and the layer's close",
        STYLES,
        "  /* ---- end MR1 ---- */\n}",
        "  /* ---- end MR1 ---- */\n  .stray {\n    color: red;\n  }\n}",
        T_STYLES,
        "is in the stylesheet once, marked",
    ),
]

# What the reading check sees in Chrome: (label, file, before, after). Each
# rebuilds the page, and `tests/test_reading_browser.py` must fail.
READING_CASES: list[tuple[str, str, str, str]] = [
    (
        "the five tabs wrap to a second row on a phone",
        STYLES,
        "      padding-inline: 0.25rem;",
        "      padding-inline: 0.5rem;",
    ),
    (
        "the title is squeezed beside the chip on a tablet",
        CHAT,
        'max-sm:basis-full md:max-lg:basis-full"',
        'max-sm:basis-full"',
    ),
    (
        "the Memory page's first line is too faint to read",
        PAGE,
        '              <p data-testid="memory-intro" className="help">',
        '              <p data-testid="memory-intro" className="help" style={{ color: "rgba(128, 128, 128, 0.35)" }}>',
    ),
]

# What only a real browser sees: (label, file, before, after). Each rebuilds the
# page, and `tests/test_memory_browser.py` must fail.
BROWSER_CASES: list[tuple[str, str, str, str]] = [
    (
        "the drawer does not show the exact text",
        CHIP,
        "              {own.text && (",
        "              {own.text && own.text.length < 0 && (",
    ),
    (
        "the chip is not read again after a send",
        CHAT,
        "            say(outcomeOf(notes));\n            void memory.reload();",
        "            say(outcomeOf(notes));",
    ),
    (
        "what a command did is not said",
        CHAT,
        "            say(outcomeOf(notes));\n",
        "            void outcomeOf(notes);\n",
    ),
    (
        "Send for this chat does not send",
        CHIP,
        "onSendHere={() => void change({ memoryExternal: true })}",
        "onSendHere={() => void change({ memoryExternal: null })}",
    ),
    (
        "a new chat is not told its model",
        COMPOSER,
        "    patch<Chat>(`/api/chats/${chat.id}`, { model: chosen.id }).then(onChat, () => undefined);",
        "    void onChat;",
    ),
    (
        "a refusal does not say the message was sent",
        TOAST,
        '              {shown.messageSent ? " Your message was still sent." : ""}',
        '              {""}',
    ),
    (
        "the drawer does not note where the keyboard was",
        CHIP,
        "  async function change(settings: Settings) {\n    keepFocus();\n",
        "  async function change(settings: Settings) {\n",
    ),
    (
        "forget deletes the first note it found, not the one picked",
        TOAST,
        "                        onClick={() => void forget(note)}",
        "                        onClick={() => void forget(shown.candidates[0]!)}",
    ),
    (
        "an export is fetched and not saved",
        PAGE,
        "      saveFile(file.blob, file.name ?? `workbench-memory.${format}`);",
        "      void [saveFile, file];",
    ),
    (
        "delete all without the word typed",
        PAGE,
        'disabled={busy || typed.trim().toLowerCase() !== "delete"}',
        "disabled={busy}",
    ),
]

# Every Chrome case, with the check that has to catch it.
CHROME_CASES: list[tuple[str, str, str, str, str]] = [
    *[(label, name, before, after, MEMORY_BROWSER) for label, name, before, after in BROWSER_CASES],
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
        [NPX, "vitest", "run", test, "-t", name],
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


def anchors(cases: list[tuple[str, str, str, str, str, str]]) -> None:
    for label, name, before, _, _, _ in cases:
        found = (ROOT / name).read_text(encoding="utf-8").count(before)
        if found != 1:
            raise SystemExit(f"{name}: the anchor for '{label}' is in {found} places; fix it.")
    for label, name, before, _, _ in CHROME_CASES:
        found = (ROOT / name).read_text(encoding="utf-8").count(before)
        if found != 1:
            raise SystemExit(f"{name}: the anchor for '{label}' is in {found} places; fix it.")
    print(f"{len(cases)} anchors and {len(CHROME_CASES)} browser anchors, each in one place.")


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
    # A failed run quotes the script's own lines after pytest's "E  " margin and
    # "AssertionError: " (which stands before the script's first line).
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
        [
            str(PYTHON),
            "-m",
            "pytest",
            "-q",
            "-x",
            "-p",
            "no:cacheprovider",
            test,
        ],
        cwd=ROOT,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=400,
    )
    return result.returncode == 0, ANSI.sub("", result.stdout + result.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchors", action="store_true")
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--chrome-only", action="store_true")
    parser.add_argument("--label", action="append", default=[])
    args = parser.parse_args()
    wanted = [c for c in CASES if not args.label or any(t in c[0] for t in args.label)]
    cases = [] if args.chrome_only else wanted
    chrome_cases = [c for c in CHROME_CASES if not args.label or any(t in c[0] for t in args.label)]
    anchors(CASES)
    if args.anchors:
        return 0

    checks = sorted({(test, name) for _, _, _, _, test, name in cases})
    for test, name in checks:
        passed, output, ran = vitest(test, name)
        if not passed or ran == 0:
            print(output[-3000:])
            raise SystemExit(f"Baseline failed or ran nothing ({test}: {name}); no files changed.")
    print(f"Baseline: {len(checks)} named checks pass and run.", flush=True)

    backup = Path(tempfile.mkdtemp(prefix="workbench-memory-page-sabotage-"))
    print(f"Exact backups: {backup}", flush=True)
    caught = 0
    for index, (label, name, before, after, test, check) in enumerate(cases):
        path = ROOT / name
        original = path.read_bytes()
        (backup / f"{index}-{path.name}").write_bytes(original)
        try:
            path.write_bytes(original.decode("utf-8").replace(before, after).encode("utf-8"))
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
                path.write_bytes(original.decode("utf-8").replace(before, after).encode("utf-8"))
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
