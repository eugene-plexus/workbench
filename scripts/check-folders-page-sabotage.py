"""Prove the Folders section's checks are observed (MR7, the page side).

Each case breaks one thing in the page's code, and the test that is named for it must fail. Files are
restored from exact bytes kept beside the run, never from Git. A mutation that stops the code
compiling does not count as a caught defect, and neither does a test name that runs nothing.

  python scripts/check-folders-page-sabotage.py              # every unit-test case
  python scripts/check-folders-page-sabotage.py --anchors    # only check each anchor is unique
  python scripts/check-folders-page-sabotage.py --python     # also the cases the Python checks catch
  python scripts/check-folders-page-sabotage.py --browser    # also the Chrome cases (needs WORKBENCH_PLAYWRIGHT)
  python scripts/check-folders-page-sabotage.py --chrome-only    # only the Chrome cases
  python scripts/check-folders-page-sabotage.py --label TEXT ...   # only the cases whose label has TEXT
  python scripts/check-folders-page-sabotage.py --only PATH ...    # only the cases that break a file whose path has PATH

The Chrome cases rebuild the page for each break and run `tests/test_folders_library_browser.py`; the
page is rebuilt from the restored source at the end. Run it with every ambient EUGENE_PLEXUS_*
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

LIB = SRC + "lib/documentFolders.ts"
API = SRC + "lib/api.ts"
TYPES = SRC + "lib/types.ts"
HOOK = SRC + "lib/useDocumentFolders.ts"
FILES_HOOK = SRC + "lib/useFolderFiles.ts"
DETAIL_HOOK = SRC + "lib/useFolderDetail.ts"
SECTION = SRC + "components/DocumentFolders.tsx"
CARD = SRC + "components/FolderCard.tsx"
ADD = SRC + "components/AddFolder.tsx"
RULES = SRC + "components/FolderRules.tsx"
FILES = SRC + "components/FolderFiles.tsx"
LEFT = SRC + "components/FolderLeftOut.tsx"
SCREEN = SRC + "components/Documents.tsx"
TEXT = SRC + "components/DocumentText.tsx"
APP = SRC + "App.tsx"

T_LIB = "src/lib/documentFolders.test.ts"
T_API = "src/lib/api.test.ts"
T_HOOK = "src/lib/useDocumentFolders.test.tsx"
T_SECTION = "src/components/DocumentFolders.test.tsx"
T_PANELS = "src/components/FolderPanels.test.tsx"
T_SCREEN = "src/components/Documents.test.tsx"
T_GATE = "src/libraryControls.test.tsx"
T_WORDS = "src/words.test.ts"
T_APP = "src/App.test.tsx"

PAGE_AGREES = "tests/test_folders_page.py"
FOLDERS_BROWSER = "tests/test_folders_library_browser.py"

# (label, file, before, after, test file, a fragment of the test's name)
CASES: list[tuple[str, str, str, str, str, str]] = [
    # --- reading the answer, and the counts -------------------------------------------------------
    (
        "a page with no grants is taken for a list of folders",
        LIB,
        "    !Array.isArray(found.grants) ||\n",
        "",
        T_LIB,
        "refuses no grants",
    ),
    (
        "a file that was skipped is not counted as done",
        LIB,
        "(counts.byState.skipped ?? 0) + (counts.byState.failed ?? 0);",
        "(counts.byState.failed ?? 0);",
        T_LIB,
        "counts a file that has stopped being read as done",
    ),
    (
        "files being read are counted from two steps only",
        LIB,
        "  Math.max(0, counts.files - doneCount(counts));",
        "  (counts.byState.queued ?? 0) + (counts.byState.reading ?? 0);",
        T_LIB,
        "counts every file that is not at an end",
    ),
    (
        "a step that is over is a step of reading",
        LIB,
        ".filter(([state, n]) => n > 0 && !ended.includes(state))",
        ".filter(([state, n]) => n > 0)",
        T_LIB,
        "names the steps files are at",
    ),
    (
        "failed is said when none has",
        LIB,
        "if (failed > 0) items.push",
        "if (failed >= 0) items.push",
        T_LIB,
        "says failed only once a file has",
    ),
    (
        "one file is said as one files",
        LIB,
        'label: counts.files === 1 ? "file" : "files" }',
        'label: "files" }',
        T_LIB,
        "says one file, not one files",
    ),
    (
        "how many can be searched by words already is not said",
        LIB,
        "  const found = unfinished - (counts.byState.queued ?? 0) - (counts.byState.reading ?? 0);",
        "  const found = 0;",
        T_LIB,
        "how far it has got while some are not",
    ),
    (
        "a folder that holds no bytes is said to hold a kibibyte",
        LIB,
        "(bytes > 0 ? [sizeWords(bytes)] : [])",
        "[sizeWords(bytes)]",
        T_LIB,
        "says no size for a folder that holds no bytes",
    ),
    (
        "a file that was skipped can be searched",
        LIB,
        "    return folder.counts.files - waiting - (byState.skipped ?? 0) - (byState.failed ?? 0) > 0;",
        "    return folder.counts.files - waiting - (byState.failed ?? 0) > 0;",
        T_LIB,
        "finds files that can be searched",
    ),
    # --- time ---------------------------------------------------------------------------------------
    (
        "a minute is up to a minute and a half from the start",
        LIB,
        'if (seconds < 45) return "just now";',
        'if (seconds < 90) return "just now";',
        T_LIB,
        "seconds as",
    ),
    (
        "an hour is only an hour",
        LIB,
        'if (seconds < 90 * 60) return "an hour ago";',
        'if (seconds < 60 * 60) return "an hour ago";',
        T_LIB,
        "seconds as",
    ),
    (
        "a day is never said",
        LIB,
        'if (seconds < 36 * 3600) return "a day ago";\n',
        "",
        T_LIB,
        "seconds as",
    ),
    (
        "a check that took under a second is not said so",
        LIB,
        'if (seconds < 1) return "under a second";\n',
        "",
        T_LIB,
        "says a check that took",
    ),
    (
        "when a folder was checked ignores the clock it is given",
        LIB,
        "`Checked ${agoWords(now - checkedAt)}.`",
        "`Checked ${agoWords(180)}.`",
        T_LIB,
        "says when a folder was checked",
    ),
    (
        "a check due now is not due",
        LIB,
        "  return nextAt <= now\n",
        "  return nextAt < now\n",
        T_LIB,
        "says when the next check is",
    ),
    # --- where a folder stands ------------------------------------------------------------------------
    (
        "a synced folder is a quiet badge",
        LIB,
        '      return { label: "Synced", tone: "ok" };',
        '      return { label: "Synced", tone: "quiet" };',
        T_LIB,
        "is said as",
    ),
    (
        "a check the person asked for is not said",
        LIB,
        ': run?.manual ? "Sync now: " : "";',
        ': "";',
        T_LIB,
        "says a check the person asked for",
    ),
    (
        "a deep check is not said",
        LIB,
        'const how = run?.deep ? "Deep check: " : run?.manual',
        'const how = false ? "Deep check: " : run?.manual',
        T_LIB,
        "says a check the person asked for",
    ),
    (
        "a problem does not say when the last check that worked was",
        LIB,
        "          ? `The last check that worked was ${agoWords(now - folder.syncedAt)}.`",
        '          ? "The last check that worked was a while ago."',
        T_LIB,
        "tells a problem in the server",
    ),
    (
        "a withdrawal does not say when it was",
        LIB,
        'problem?.at != null ? `Taken away ${agoWords(now - problem.at)}.` : "",',
        '"",',
        T_LIB,
        "tells a withdrawn folder",
    ),
    (
        "files that were skipped are not part of what a check did",
        LIB,
        "    last.skipped > 0 && `${last.skipped.toLocaleString()} skipped`,\n",
        "",
        T_LIB,
        "says what the last check did",
    ),
    # --- what was left out ------------------------------------------------------------------------------
    (
        "links that were not followed are not said",
        LIB,
        '  if (last.links > 0) parts.push(`${count(last.links, "link")} not followed`);\n',
        "",
        T_LIB,
        "says what the last check left out in a line",
    ),
    (
        "a limit that was reached is not said",
        LIB,
        "  if (caps.files.reached) {",
        "  if (false) {",
        T_LIB,
        "says each limit a folder has run into",
    ),
    (
        "what was left out is told in an order of its own",
        LIB,
        '  ignored_default: "Always left out",\n  ignored_hidden: "Hidden",',
        '  ignored_hidden: "Hidden",\n  ignored_default: "Always left out",',
        T_LIB,
        "has a heading for every kind",
    ),
    (
        "only the ones the server named are counted",
        LIB,
        "total: Math.max(detail.leftCounts[code] ?? 0, items.length),",
        "total: items.length,",
        T_LIB,
        "counts every one that was left out",
    ),
    # --- the list of a folder's files ---------------------------------------------------------------------
    (
        "the reasons are not the most common first",
        LIB,
        ".sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));",
        ".sort((a, b) => a.label.localeCompare(b.label));",
        T_LIB,
        "offers the reasons a folder",
    ),
    (
        "the filter in use is taken away when it has no files",
        LIB,
        'option.value === "all" || option.value === current || option.n > 0',
        'option.value === "all" || option.n > 0',
        T_LIB,
        "offers the filters that have files",
    ),
    (
        "the files that wait come before the ones being read",
        LIB,
        "readingStates(counts).sort(waitingLast)",
        "readingStates(counts)",
        T_LIB,
        "makes a page of the files being read",
    ),
    (
        "a page across two steps starts in the wrong place",
        LIB,
        "    const from = Math.max(offset, start);",
        "    const from = offset;",
        T_LIB,
        "makes a page of the files being read",
    ),
    (
        "a page is said to start at the file before it",
        LIB,
        "`${(offset + 1).toLocaleString()} to",
        "`${offset.toLocaleString()} to",
        T_LIB,
        "says where a page is in the list",
    ),
    # --- the rules ------------------------------------------------------------------------------------------
    (
        "a blank line is a pattern",
        LIB,
        r'  text.split(/\r?\n/).filter((line) => line.trim() !== "");',
        r"  text.split(/\r?\n/);",
        T_LIB,
        "does not send a blank line",
    ),
    (
        "a changed .gitignore switch is not a change",
        LIB,
        "    folder.useGitignore !== draft.useGitignore ||\n",
        "",
        T_LIB,
        "says when what is typed is not what the folder has",
    ),
    # --- the limits, said plainly -----------------------------------------------------------------------------
    (
        "how often it looks is a number of its own",
        LIB,
        'every ${count(limits.syncMinutes, "minute")}',
        "every 10 minutes",
        T_LIB,
        "says there is no file watcher",
    ),
    (
        "folders on job sites are not said to be left out",
        LIB,
        ', not job site folders yet.",\n',
        '.",\n',
        T_LIB,
        "says folders on job sites cannot be added",
    ),
    (
        "a deep check is not said to find an edit a quick check misses",
        LIB,
        " A deep check reads every file again and finds it, but takes longer on a big folder.",
        " A deep check takes longer on a big folder.",
        T_SECTION,
        "is an expert option: a disclosure that says what it is for",
    ),
    (
        "the owner is not told where folders are given",
        LIB,
        "In Toolbox · Tools, under Folders · File tools, choose “Grant an existing folder” and pick your own name as the person.",
        "Give yourself one.",
        T_LIB,
        "tells the owner where folders are given",
    ),
    (
        "Remove folder does not say the folder is never touched",
        LIB,
        " The folder and your access to it are not touched; you can add it again.`;",
        " You can add it again.`;",
        T_LIB,
        "says what Remove folder does",
    ),
    (
        "leaving a file out does not say it goes on the ignore list",
        LIB,
        "and adds /${path} to this folder's ignore list, so it stays out.",
        "and it is gone.",
        T_LIB,
        "says what leaving one file out does",
    ),
    # --- looking again ------------------------------------------------------------------------------------------
    (
        "files that are being read do not keep a folder busy",
        LIB,
        ' || folder.state === "waiting" || readingCount(folder.counts) > 0;',
        ' || folder.state === "waiting";',
        T_LIB,
        "is busy while a check is under way",
    ),
    (
        "a check that is due is asked about before it begins",
        LIB,
        "Math.ceil(soonest * 1000) + 1500",
        "Math.ceil(soonest * 1000)",
        T_LIB,
        "asks when a check is due",
    ),
    (
        "after a read that failed it is asked again at once",
        LIB,
        "if (page.folders.some(folderBusy)) return failing ? RETRY_MS : POLL_MS;",
        "if (page.folders.some(folderBusy)) return POLL_MS;",
        T_LIB,
        "asks each second while anything is changing",
    ),
    (
        "it is asked about more often than each second",
        LIB,
        "Math.max(POLL_MS, Math.ceil",
        "Math.max(0, Math.ceil",
        T_LIB,
        "does not ask more often than each second",
    ),
    (
        "the folders are asked about by a fixed time",
        HOOK,
        "      foldersWait(data, serverNow(), failing.current),",
        "      30_000,",
        T_HOOK,
        "asks each second while a check is under way",
    ),
    (
        "an answer that is not folders is shown as folders",
        HOOK,
        "      if (!next) throw new Error(NOT_UNDERSTOOD);\n",
        "",
        T_HOOK,
        "is not a list of folders when the answer is not one",
    ),
    (
        "a slow read puts back what a newer one replaced",
        HOOK,
        '      const next = readFolders(await api<unknown>("/api/documents/folders"));\n      if (mine !== latest.current) return;\n',
        '      const next = readFolders(await api<unknown>("/api/documents/folders"));\n',
        T_HOOK,
        "never puts back what a newer read replaced",
    ),
    # --- Workbench's clock ---------------------------------------------------------------------------------------
    (
        "Workbench's clock is never read from an answer",
        API,
        "  if (Number.isFinite(said)) skew = said / 1000 - Date.now() / 1000;\n",
        "",
        T_API,
        "is told by the Date header of an answer",
    ),
    (
        "a refusal tells the clock",
        API,
        "export async function checked(response: Response): Promise<Response> {\n  if (response.ok) {\n    noteClock(response);\n    return response;\n  }",
        "export async function checked(response: Response): Promise<Response> {\n  noteClock(response);\n  if (response.ok) {\n    return response;\n  }",
        T_API,
        "is not told by a refusal",
    ),
    (
        "the time is read again only with each answer, not by the clock",
        SECTION,
        "const now = useServerNow(10_000, data);",
        "const now = useServerNow(10_000_000, data);",
        T_SECTION,
        "the clock ticks by itself",
    ),
    # --- the lists a card opens read again ----------------------------------------------------------------------------
    (
        "a list of files is not read again when the folder changes",
        FILES_HOOK,
        "  }, [folderId, requests, code, q, again, reads]);",
        "  }, [folderId, requests, code, q, reads]);",
        T_PANELS,
        "is read again when the folder says something changed",
    ),
    (
        "a slow page of files puts back an older one",
        FILES_HOOK,
        "        if (mine !== latest.current) return;\n        setShown({",
        "        setShown({",
        T_PANELS,
        "never puts back a page of files that a newer one replaced",
    ),
    (
        "what was left out is not read again after a check",
        DETAIL_HOOK,
        "  }, [folderId, again, reads]);",
        "  }, [folderId, reads]);",
        T_PANELS,
        "is read again after each check",
    ),
    (
        "an older check's list is put back over a newer one's",
        DETAIL_HOOK,
        "        if (mine !== latest.current) return;\n        setDetail(found);",
        "        setDetail(found);",
        T_PANELS,
        "never puts back what an older check found",
    ),
    (
        "the files of a card are not told the folder changed",
        CARD,
        'const again = `${folder.checkedAt ?? ""}|${JSON.stringify(counts.byState)}|${counts.files}`;',
        'const again = "";',
        T_PANELS,
        "is read again when the folder says something changed",
    ),
    (
        "what was left out is not told a check ended",
        CARD,
        'const checked = `${folder.checkedAt ?? ""}|${state}`;',
        'const checked = "";',
        T_PANELS,
        "is read again after each check",
    ),
    # --- a card --------------------------------------------------------------------------------------------------------------
    (
        "Sync now asks again while a check is under way",
        CARD,
        "    if (checking) return;\n",
        "",
        T_SECTION,
        "does not ask again while a check is under way",
    ),
    (
        "a deep check asks for a quick one",
        CARD,
        "{ deep }).then(",
        "{ deep: false }).then(",
        T_SECTION,
        "is an expert option",
    ),
    (
        "Sync now asks for a deep check",
        CARD,
        "onClick={() => sync(false)}",
        "onClick={() => sync(true)}",
        T_SECTION,
        "asks for a quick check, and the card follows it",
    ),
    (
        "the keyboard is not put on Keep it",
        CARD,
        '              autoFocus\n              data-testid="folder-remove-keep"',
        '              data-testid="folder-remove-keep"',
        T_SECTION,
        "puts the keyboard on Keep it",
    ),
    (
        "the section is not told a folder is gone",
        CARD,
        "      () => onRemoved(),\n",
        "      () => undefined,\n",
        T_SECTION,
        "removes it, and the card goes",
    ),
    (
        "Sync now is offered where documents are off",
        CARD,
        '{!locked && !withdrawn && (\n            <button\n              type="button"\n              data-testid="folder-sync"',
        '{!withdrawn && (\n            <button\n              type="button"\n              data-testid="folder-sync"',
        T_SECTION,
        "and nothing else",
    ),
    (
        "a deep check is offered where documents are off",
        CARD,
        '      {!locked && !withdrawn && (\n        <details data-testid="folder-deep-details"',
        '      {!withdrawn && (\n        <details data-testid="folder-deep-details"',
        T_SECTION,
        "and nothing else",
    ),
    (
        "a problem is only a warning",
        CARD,
        'className={`notice ${state === "problem" ? "notice--error" : "notice--warn"}`}',
        'className="notice notice--warn"',
        T_SECTION,
        "problem: the server",
    ),
    (
        "a folder that is not there shows counts",
        CARD,
        '      {!missing && (\n        <>\n          <ul data-testid="folder-counts"',
        '      {true && (\n        <>\n          <ul data-testid="folder-counts"',
        T_SECTION,
        "not available:",
    ),
    (
        "a limit that was reached is not shown on the card",
        CARD,
        "          {caps.length > 0 && (",
        "          {false && (",
        T_SECTION,
        "says a limit that was reached, in a warning",
    ),
    (
        "Sync now is not named for its folder",
        CARD,
        "aria-label={`Sync now: ${folder.name}`}",
        'aria-label="Sync now"',
        T_SECTION,
        "is named for its folder",
    ),
    (
        "a withdrawn folder is asked about before it is removed",
        CARD,
        "          {withdrawn ? (",
        "          {false ? (",
        T_SECTION,
        "withdrawn:",
    ),
    (
        "the bar of a check says it is all done",
        CARD,
        "value={Math.min(run.done, run.total)}",
        "value={run.total}",
        T_SECTION,
        "syncing, reading",
    ),
    (
        "the bar of the reading says all are done",
        CARD,
        "value={doneCount(counts)}",
        "value={counts.files}",
        T_SECTION,
        "synced, with files still being read",
    ),
    (
        "a card that is syncing is not marked busy",
        CARD,
        'aria-busy={state === "syncing" || undefined}',
        "aria-busy={undefined}",
        T_SECTION,
        "syncing, reading",
    ),
    (
        "the keyboard is lost while a request is made",
        CARD,
        "    keepFocus();\n    setPending(true);\n    setProblem(null);\n    try {\n      await work();",
        "    setPending(true);\n    setProblem(null);\n    try {\n      await work();",
        T_SECTION,
        "is held while it asks, and the keyboard is given back to it",
    ),
    (
        "a refusal is not said on the card",
        CARD,
        "      setProblem(failureWords(failure));\n    } finally {\n      setPending(false);\n      onChanged();",
        "      setProblem(null);\n    } finally {\n      setPending(false);\n      onChanged();",
        T_SECTION,
        "says why when the server refuses, on the card",
    ),
    (
        "the folders are not read again after a refusal",
        CARD,
        "      setPending(false);\n      onChanged();\n    }\n  }",
        "      setPending(false);\n    }\n  }",
        T_SECTION,
        "says why when the server refuses, on the card",
    ),
    # --- Add a folder -----------------------------------------------------------------------------------------------------------
    (
        "no folder is chosen when none is",
        ADD,
        '(open[0]?.id ?? "")',
        '""',
        T_SECTION,
        "adds the first folder not added when none is chosen",
    ),
    (
        "a folder that is added is offered again",
        ADD,
        "  const open = choices.filter((choice) => !choice.added);",
        "  const open = choices;",
        T_SECTION,
        "offers the folders given and not added",
    ),
    (
        "a pattern that is refused is said by the button",
        ADD,
        "      if (statusOf(failure) === 422) {",
        "      if (false) {",
        T_SECTION,
        "says a pattern the server cannot use under the patterns",
    ),
    (
        "the patterns are not opened to show what is wrong with one",
        ADD,
        "        setRulesOpen(true);\n",
        "",
        T_SECTION,
        "says a pattern the server cannot use under the patterns",
    ),
    (
        "what to leave out is not sent with the folder",
        ADD,
        "        ...rulesOf(draft),\n",
        "",
        T_SECTION,
        "adds the folder chosen with the easy defaults",
    ),
    (
        "the owner is given a button when anyone was given no folder",
        ADD,
        "{owner && onTools && (",
        "{onTools && (",
        T_SECTION,
        "tells anyone else to ask the owner",
    ),
    (
        "any other refusal is said under the patterns",
        ADD,
        "        setProblem(failureWords(failure));\n      }\n    } finally",
        "        setPatternError(failureWords(failure));\n      }\n    } finally",
        T_SECTION,
        "says any other refusal",
    ),
    (
        "adding is not held while it asks",
        ADD,
        '              disabled={pending || disabled}\n              className="btn btn-primary"',
        '              className="btn btn-primary"',
        T_SECTION,
        "is held while it adds",
    ),
    # --- the rules ------------------------------------------------------------------------------------------------------------------
    (
        "the switches are not sent with the list",
        RULES,
        "  includeHidden: draft.includeHidden,\n  useGitignore: draft.useGitignore,\n});",
        "});",
        T_PANELS,
        "sends the list, a pattern on each line, and the two switches",
    ),
    (
        "rules that changed under the editor are not taken up",
        RULES,
        "  if (seen !== inEffect) {\n    setSeen(inEffect);\n    setDraft(draftOf(folder));\n  }",
        "",
        T_PANELS,
        "takes up rules that changed under it",
    ),
    (
        "Save is free when nothing was changed",
        RULES,
        "disabled={pending || !changed}\n            onClick={() => void save()}",
        "disabled={pending}\n            onClick={() => void save()}",
        T_PANELS,
        "can be saved only once something is changed",
    ),
    (
        "a pattern that is refused is not said under the patterns",
        RULES,
        "      setError(failureWords(failure));\n    } finally",
        "      setError(null);\n    } finally",
        T_PANELS,
        "says a pattern the server cannot use under the patterns",
    ),
    (
        "what saving does is not said",
        RULES,
        '        <p className="help">{RULES_EFFECT}</p>\n',
        "",
        T_PANELS,
        "says what saving does before it is done",
    ),
    (
        "saving does not say the folder is checked again",
        RULES,
        "            Saved. Workbench is checking the folder again.",
        "            Saved.",
        T_PANELS,
        "says it was saved",
    ),
    # --- a folder's files -----------------------------------------------------------------------------------------------------------------
    (
        "a page of files holds twenty-five",
        FILES,
        "export const PAGE_SIZE = 50;",
        "export const PAGE_SIZE = 25;",
        T_PANELS,
        "shows fifty at a time",
    ),
    (
        "a file that was skipped has an original to open",
        FILES,
        "  const hasCopy = !ended;",
        "  const hasCopy = true;",
        T_PANELS,
        "has no original to open when it was skipped or failed",
    ),
    (
        "a file that cannot be searched shows its text",
        FILES,
        "{file.searchable && text.action}",
        "{text.action}",
        T_PANELS,
        "can show the text Workbench read",
    ),
    (
        "a file can be left out where documents are off",
        FILES,
        "          {!locked && (\n            <button\n              ref={opener}",
        "          {true && (\n            <button\n              ref={opener}",
        T_PANELS,
        "cannot be left out where the owner has turned documents off",
    ),
    (
        "a word is asked about at every key",
        FILES,
        "useDebounced(typed, 300)",
        "useDebounced(typed, 0)",
        T_PANELS,
        "finds a file by a word in its path",
    ),
    (
        "the reason is kept when the state is changed",
        FILES,
        '              setCode(null);\n              setOffset(0);\n            }}\n            className="control control--sm"\n          >\n            {filters.map',
        '              setOffset(0);\n            }}\n            className="control control--sm"\n          >\n            {filters.map',
        T_PANELS,
        "forgets the reason when the state is changed",
    ),
    (
        "a list that got shorter stays on a page that is not there",
        FILES,
        "    if (shown && shown.files.length === 0 && shown.total > 0 && offset > 0) {",
        "    if (false) {",
        T_PANELS,
        "goes back to its last page",
    ),
    (
        "a word does not take the list back to its first page",
        FILES,
        "              setTyped(event.target.value);\n              setOffset(0);",
        "              setTyped(event.target.value);",
        T_PANELS,
        "goes back to the first page when a filter or a word is chosen",
    ),
    (
        "leaving a file out does not ask the server",
        FILES,
        "await del(`/api/documents/${encodeURIComponent(file.id)}`);",
        "await Promise.resolve();",
        T_PANELS,
        "is left out: it goes from the list",
    ),
    (
        "an original is saved under the path and not the name the server gives",
        FILES,
        'found.name ?? file.path.split("/").pop() ?? file.path',
        "file.path",
        T_PANELS,
        "can be opened as Workbench holds it, under the name the server gives it",
    ),
    (
        "an empty folder is told no file fits",
        FILES,
        '? "Workbench has read no files from this folder yet."',
        '? "No file fits what you chose."',
        T_PANELS,
        "says when no file has been read yet",
    ),
    (
        "where a page is in the list is not said",
        FILES,
        "{rangeWords(offset, files.length, total)}",
        "{total}",
        T_PANELS,
        "says how many files there are, and where this page is",
    ),
    (
        "the next page is offered at the end of the list",
        FILES,
        "disabled={offset + PAGE_SIZE >= total}",
        "disabled={false}",
        T_PANELS,
        "shows fifty at a time, and moves by fifty",
    ),
    (
        "an earlier page is offered on the first page",
        FILES,
        "            disabled={offset === 0}",
        "            disabled={false}",
        T_PANELS,
        "shows fifty at a time, and moves by fifty",
    ),
    (
        "leaving a file out does not say what it does",
        FILES,
        '<p className="wrap-anywhere">{leaveOutWords(file.path)}</p>',
        '<p className="wrap-anywhere">Leave out {file.path}?</p>',
        T_PANELS,
        "asks before it is left out, and says what that does",
    ),
    (
        "a folder's file has the text box ids of the person's own",
        TEXT,
        'const test = prefix ?? (saved ? "saved-document" : "document");',
        'const test = saved ? "saved-document" : "document";',
        T_PANELS,
        "can show the text Workbench read",
    ),
    # --- what was left out ---------------------------------------------------------------------------------------------------------------------
    (
        "nothing left out is not said",
        LEFT,
        "  const nothing = detail !== null && groups.length === 0 && unlisted.length === 0;",
        "  const nothing = false;",
        T_PANELS,
        "says nothing was left out when nothing was",
    ),
    (
        "how many are not named is not said",
        LEFT,
        "{detail && total > named && (",
        "{false && (",
        T_PANELS,
        "says how many are not named",
    ),
    (
        "a folder that could not be looked into is not said",
        LEFT,
        "      {unlisted.length > 0 && (",
        "      {false && (",
        T_PANELS,
        "says a folder that could not be looked into",
    ),
    (
        "a folder is not marked with a slash",
        LEFT,
        '{item.kind === "folder" && "/"}',
        "",
        T_PANELS,
        "says each thing's path",
    ),
    (
        "a list that could not be read is not said",
        LEFT,
        "      {problem && (",
        "      {false && (",
        T_PANELS,
        "says why when it could not be read, and reads again with Try again",
    ),
    # --- the section, on the screen ----------------------------------------------------------------------------------------------------------------
    (
        "Ask about your documents is here when it is not asked for",
        SECTION,
        "      {askHere && (",
        "      {true && (",
        T_SECTION,
        "only when the screen says it belongs here",
    ),
    (
        "a person with documents off can add a folder",
        SECTION,
        "      {!locked && data.available && (\n        <AddFolder",
        "      {data.available && (\n        <AddFolder",
        T_SECTION,
        "and nothing else",
    ),
    (
        "a Workbench that cannot read folders offers to add one",
        SECTION,
        "      {!locked && data.available && (\n        <AddFolder",
        "      {!locked && (\n        <AddFolder",
        T_SECTION,
        "cannot read folders, and offers no way",
    ),
    (
        "totals are said for no folder",
        SECTION,
        '      {list.length > 0 && (\n        <div className="flex flex-col gap-1">',
        '      {true && (\n        <div className="flex flex-col gap-1">',
        T_SECTION,
        "says no totals when there is no folder",
    ),
    (
        "the keyboard is not given to the card that was added",
        SECTION,
        "    target?.focus();\n    setFocus(null);",
        "    setFocus(null);",
        T_SECTION,
        "adds the folder chosen with the easy defaults",
    ),
    (
        "the keyboard is not given to the heading when a folder is removed",
        SECTION,
        "onRemoved={() => setFocus({ folder: null })}",
        "onRemoved={() => undefined}",
        T_SECTION,
        "removes it, and the card goes",
    ),
    (
        "a person with no folder is told of folders where documents are off",
        SCREEN,
        "const showFolders = !off || kept.length > 0;",
        "const showFolders = true;",
        T_SCREEN,
        "nothing of folders for a person who has none",
    ),
    (
        "a person with only folder files has no Test a question",
        SCREEN,
        "{!off && (data.documents.length > 0 || fromFolders) && (",
        "{!off && data.documents.length > 0 && (",
        T_SCREEN,
        "offers Test a question to a person who has only folder files",
    ),
    (
        "Ask about your documents is in both places",
        SCREEN,
        "askHere={!off && ready === 0 && anySearchable(kept)}",
        "askHere={!off && anySearchable(kept)}",
        T_SCREEN,
        "has Ask about your documents once",
    ),
    (
        "Ask about your documents is there when nothing can be searched",
        SCREEN,
        "askHere={!off && ready === 0 && anySearchable(kept)}",
        "askHere={!off && ready === 0 && kept.length > 0}",
        T_SCREEN,
        "has no Ask about your documents when nothing can be searched yet",
    ),
    (
        "the folders are read as part of the list",
        SCREEN,
        "  const folders = useDocumentFolders();",
        "  const folders = { data: null, problem: null, reload: async () => undefined };",
        T_SCREEN,
        "reads folders on their own schedule",
    ),
    (
        "the Folders section cannot open Toolbox · Tools",
        APP,
        "            onTools={() => {\n              setShowSites(false);\n              setShowTools(true);\n            }}\n",
        "",
        T_APP,
        "from the Folders section",
    ),
    # --- the gates -------------------------------------------------------------------------------------------------------------------------------
    (
        "a word a person reads about how files are read is in a card",
        CARD,
        " * Every sentence is composed from the fields the server sent",
        " * Every vector sentence is composed from the fields the server sent",
        T_WORDS,
        "keeps how documents are read underneath off every screen about them",
    ),
    (
        "a word a person reads about how files are read is in the words",
        LIB,
        "no file watcher (a look every N minutes, and Sync now)",
        "no file watcher (a retrieval every N minutes, and Sync now)",
        T_WORDS,
        "keeps how documents are read underneath off every screen about them",
    ),
    (
        "Sync now has no test id",
        CARD,
        '              data-testid="folder-sync"\n',
        "",
        T_GATE,
        "with a folder in every state",
    ),
    (
        "the box to find a file has no name",
        FILES,
        "htmlFor={`${uid}-find`}",
        "htmlFor={`${uid}-finder`}",
        T_GATE,
        "with a folder's files open",
    ),
    (
        "the choice of folder has no name",
        ADD,
        "htmlFor={`${uid}-folder`}",
        "htmlFor={`${uid}-folders`}",
        T_GATE,
        "with Add a folder",
    ),
    (
        "the patterns have no name",
        SRC + "components/FolderRules.tsx",
        "htmlFor={`${uid}-ignore`}",
        "htmlFor={`${uid}-ignores`}",
        T_GATE,
        "with the rules open",
    ),
]

# What the Python checks catch (`--python`): the page's words and types held to the server's.
PYTHON_CASES: list[tuple[str, str, str, str, str, str]] = [
    (
        "a reason a file can fail for has no words",
        LIB,
        '  crashed: "The reader stopped unexpectedly",\n',
        "",
        PAGE_AGREES,
        "every_reason_a_file_can_be_skipped",
    ),
    (
        "a reason the page words is one the server never says",
        LIB,
        '  internal: "Workbench\'s own error",',
        '  internal: "Workbench\'s own error",\n  invented: "A reason nobody says",',
        PAGE_AGREES,
        "every_reason_a_file_can_be_skipped",
    ),
    (
        "a kind of thing a check leaves out has no heading",
        LIB,
        '  cloud: "Kept online by a cloud-sync program",\n',
        "",
        PAGE_AGREES,
        "every_kind_of_thing_a_check_can_leave_out",
    ),
    (
        "a field the server sends is renamed on the page",
        TYPES,
        "  nextAt: number | null;",
        "  nextAtTime: number | null;",
        PAGE_AGREES,
        "the_pages_types_are_exactly",
    ),
    (
        "a limit the server sends is not on the page",
        TYPES,
        "  maxIgnore: number;\n",
        "",
        PAGE_AGREES,
        "the_pages_types_are_exactly",
    ),
    (
        "a state the server can say is not one of the page's",
        TYPES,
        '  "waiting" | "syncing" | "synced" | "problem" | "unavailable" | "withdrawn";',
        '  "waiting" | "syncing" | "synced" | "problem" | "unavailable";',
        PAGE_AGREES,
        "every_state_a_folder_can_be_in",
    ),
    (
        "a state has no badge",
        LIB,
        '    case "unavailable":\n      return { label: "Not available", tone: "warn" };',
        '    case "gone":\n      return { label: "Not available", tone: "warn" };',
        PAGE_AGREES,
        "every_state_a_folder_can_be_in",
    ),
    (
        "a step of reading is not one a document can be in",
        LIB,
        'const ended = ["ready", "skipped", "failed"];',
        'const ended = ["ready", "skipped", "failed", "gone"];',
        PAGE_AGREES,
        "the_states_the_page_counts",
    ),
]

# What only Chrome catches (`--browser`): the page against the real Workbench.
CHROME_CASES: list[tuple[str, str, str, str, str]] = [
    (
        "Add folder sends no folder",
        ADD,
        "        grantId,\n",
        "",
        FOLDERS_BROWSER,
    ),
    (
        "Sync now does not ask for a check",
        CARD,
        "post(`/api/documents/folders/${encodeURIComponent(folder.id)}/sync`, { deep }).then(",
        "Promise.resolve([post, { deep }]).then(",
        FOLDERS_BROWSER,
    ),
    (
        "rules are saved without the list",
        RULES,
        "  ignore: parseIgnore(draft.text),\n",
        "  ignore: [],\n",
        FOLDERS_BROWSER,
    ),
    (
        "a folder is removed and stays",
        CARD,
        "() => del(`/api/documents/folders/${encodeURIComponent(folder.id)}`),",
        "() => Promise.resolve(void del),",
        FOLDERS_BROWSER,
    ),
    (
        "a filter by state asks for every file",
        LIB,
        '  if (filter !== "reading") return [{ state: filter, offset, limit }];',
        '  if (filter !== "reading") return [{ state: null, offset, limit }];',
        FOLDERS_BROWSER,
    ),
    (
        "what was left out is told in another order",
        LIB,
        '  ignored_default: "Always left out",\n  ignored_hidden: "Hidden",',
        '  ignored_hidden: "Hidden",\n  ignored_default: "Always left out",',
        FOLDERS_BROWSER,
    ),
    (
        "a folder is said to have been checked long ago",
        LIB,
        "`Checked ${agoWords(now - checkedAt)}.`",
        "`Checked ${agoWords(now - checkedAt + 7200)}.`",
        FOLDERS_BROWSER,
    ),
    (
        "how far the reading has got is not said",
        LIB,
        "  const unfinished = readingCount(counts);\n  if (unfinished === 0) return null;",
        "  const unfinished = readingCount(counts);\n  return null;",
        FOLDERS_BROWSER,
    ),
    (
        "the keyboard is not given to the card that was added",
        SECTION,
        "    target?.focus();\n    setFocus(null);",
        "    setFocus(null);",
        FOLDERS_BROWSER,
    ),
    (
        "a withdrawn folder cannot be dismissed",
        CARD,
        "          {withdrawn ? (",
        "          {false ? (",
        FOLDERS_BROWSER,
    ),
]

ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("EUGENE_PLEXUS_")},
    "FORCE_COLOR": "0",
    "NO_COLOR": "1",
    # What a failing test prints (a page's words have an ellipsis) is read as UTF-8, not as the
    # console's code page.
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
}
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def vitest(test: str, name: str) -> tuple[bool, str, int]:
    """(passed, output, how many tests ran and passed) for the tests whose name has `name`."""
    result = subprocess.run(
        ["node", str(VITEST), "run", test, "-t", re.escape(name)],
        cwd=WEB,
        env=ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
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
        errors="replace",
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
        errors="replace",
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
        errors="replace",
        timeout=600,
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

    backup = Path(tempfile.mkdtemp(prefix="workbench-folders-page-sabotage-"))
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
