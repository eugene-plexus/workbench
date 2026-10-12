/**
 * What the Folders section of the Documents screen says (MR7), in one place. Plain words:
 * "folder", "files", "ready", "being read", "skipped", "left out". How a file is read and
 * searched underneath is never on the way a person reads it (words.test.ts holds this file to
 * that).
 *
 * The server is the authority on every number and on every reason: why a file was skipped, why
 * something was left out, what is wrong with a folder, and what a pattern that cannot be used is
 * told. Those are shown as they came. What is composed here is composed only from the fields it
 * sent (the counts, the times, the limits), so a number on the screen is a number the server
 * counted. Times are told against Workbench's own clock (`serverNow`), since it stamped them.
 *
 * The limits the person has to know are stated plainly, from the numbers in effect and not from
 * constants: no file watcher (a look every N minutes, and Sync now), a quick check that an edit
 * keeping a file's size and time slips past, folders on job sites that cannot be added, and a first
 * read that takes a while.
 */

import { count } from "./documents";
import { durationWords } from "./documentMeaning";
import { IDLE_POLL_MS, POLL_MS, RETRY_MS, sizeWords } from "./savedDocuments";
import type {
  DocumentFolder,
  DocumentFolderDetail,
  DocumentFolders,
  FolderCaps,
  FolderCounts,
  FolderLast,
  FolderLimits,
  FolderRun,
  FolderState,
  LeftOut,
} from "./types";

// --- reading the answer ---

/** What `GET /api/documents/folders` answered, or null when it answered with something else:
 * the page tells nothing of folders it did not read, and says so. */
export function readFolders(value: unknown): DocumentFolders | null {
  const found = value as Partial<DocumentFolders> | null | undefined;
  if (
    !found ||
    typeof found !== "object" ||
    !Array.isArray(found.folders) ||
    !Array.isArray(found.grants) ||
    typeof found.available !== "boolean" ||
    !found.limits ||
    typeof found.limits !== "object"
  ) {
    return null;
  }
  return found as DocumentFolders;
}

// --- the counts ---

/** Files that have stopped being read: read, skipped or failed. */
export const doneCount = (counts: FolderCounts): number =>
  (counts.byState.ready ?? 0) + (counts.byState.skipped ?? 0) + (counts.byState.failed ?? 0);

/** Files still being read, whatever step they are at: all that are not at an end. Counted from the
 * rest, so a step the server adds later is still one of them. */
export const readingCount = (counts: FolderCounts): number =>
  Math.max(0, counts.files - doneCount(counts));

/** The states of the files that are still being read, as the folder's own counts name them. */
export function readingStates(counts: FolderCounts): string[] {
  const ended = ["ready", "skipped", "failed"];
  return Object.entries(counts.byState)
    .filter(([state, n]) => n > 0 && !ended.includes(state))
    .map(([state]) => state);
}

export type CountKind = "files" | "ready" | "reading" | "skipped" | "failed";

export interface CountItem {
  kind: CountKind;
  n: number;
  /** The words after the number: `files`, `ready`, `being read`, `skipped`, `failed`. */
  label: string;
}

/** `412 files · 380 ready · 15 being read · 9 skipped`, and `failed` once one has. Each of the
 * first four is always said, so a zero is seen and not guessed at. */
export function countItems(counts: FolderCounts): CountItem[] {
  const items: CountItem[] = [
    { kind: "files", n: counts.files, label: counts.files === 1 ? "file" : "files" },
    { kind: "ready", n: counts.byState.ready ?? 0, label: "ready" },
    { kind: "reading", n: readingCount(counts), label: "being read" },
    { kind: "skipped", n: counts.byState.skipped ?? 0, label: "skipped" },
  ];
  const failed = counts.byState.failed ?? 0;
  if (failed > 0) items.push({ kind: "failed", n: failed, label: "failed" });
  return items;
}

/** How far the reading of a folder's files has got, while any is still being read: how many are
 * done of how many, and how many of the rest can be searched by words already. */
export function readingWords(counts: FolderCounts): string | null {
  const unfinished = readingCount(counts);
  if (unfinished === 0) return null;
  const said = [
    `Reading the files: ${doneCount(counts).toLocaleString()} of ${counts.files.toLocaleString()} are done.`,
  ];
  // Found by words while the rest of their reading (their meaning) is added.
  const found = unfinished - (counts.byState.queued ?? 0) - (counts.byState.reading ?? 0);
  if (found > 0) {
    said.push(
      `${count(found, "file")} can be searched by words already, and ${found === 1 ? "is" : "are"} having ${found === 1 ? "its" : "their"} meaning added.`,
    );
  }
  return said.join(" ");
}

/** A size, or nothing for no bytes at all: the size is said as the server says it, which has no
 * word for none (it never says less than 1 KiB). */
const sizeOrNone = (bytes: number): string[] => (bytes > 0 ? [sizeWords(bytes)] : []);

/** What a folder holds, in one line: `1,904 passages · 4.2 MiB`. */
export function holdsWords(counts: FolderCounts): string {
  return [count(counts.passages, "passage"), ...sizeOrNone(counts.bytes)].join(" · ");
}

/** The totals of every folder, apart from the files the person added themselves. */
export function foldersTotals(folders: DocumentFolder[]): string {
  const files = folders.reduce((sum, folder) => sum + folder.counts.files, 0);
  const passages = folders.reduce((sum, folder) => sum + folder.counts.passages, 0);
  const bytes = folders.reduce((sum, folder) => sum + folder.counts.bytes, 0);
  return [
    count(folders.length, "folder"),
    count(files, "file"),
    count(passages, "passage"),
    ...sizeOrNone(bytes),
  ].join(" · ");
}

/** Whether any file of the folders can be found now: a file read, or found by words while the rest
 * of its reading goes on. A file waiting to be read, skipped or failed cannot. */
export function anySearchable(folders: DocumentFolder[]): boolean {
  return folders.some((folder) => {
    const { byState } = folder.counts;
    const waiting = (byState.queued ?? 0) + (byState.reading ?? 0);
    return folder.counts.files - waiting - (byState.skipped ?? 0) - (byState.failed ?? 0) > 0;
  });
}

// --- time ---

/** `just now`, `a minute ago`, `7 minutes ago`, `an hour ago`, `3 hours ago`, `a day ago`,
 * `2 days ago`: how long ago, as a person says it. */
export function agoWords(seconds: number): string {
  if (seconds < 45) return "just now";
  if (seconds < 90) return "a minute ago";
  if (seconds < 45 * 60) return `${Math.round(seconds / 60)} minutes ago`;
  if (seconds < 90 * 60) return "an hour ago";
  if (seconds < 22 * 3600) return `${Math.round(seconds / 3600)} hours ago`;
  if (seconds < 36 * 3600) return "a day ago";
  return `${Math.round(seconds / 86400)} days ago`;
}

/** The time a check took: `under a second`, `3 seconds`, `2 minutes`. */
export function tookWords(seconds: number): string {
  if (seconds < 1) return "under a second";
  if (seconds < 60) return count(Math.round(seconds), "second");
  return count(Math.round(seconds / 60), "minute");
}

// --- where a folder stands ---

export type BadgeTone = "quiet" | "ok" | "warn";

/** The word on a folder's badge, and how it looks (a glyph and words are on it too: never colour
 * alone). */
export function stateBadge(state: FolderState): { label: string; tone: BadgeTone } {
  switch (state) {
    case "waiting":
      return { label: "Waiting", tone: "quiet" };
    case "syncing":
      return { label: "Syncing", tone: "quiet" };
    case "synced":
      return { label: "Synced", tone: "ok" };
    case "problem":
      return { label: "Problem", tone: "warn" };
    case "unavailable":
      return { label: "Not available", tone: "warn" };
    case "withdrawn":
      return { label: "Withdrawn", tone: "warn" };
    default:
      return { label: "Unknown", tone: "warn" };
  }
}

/** What a check that is under way is doing: `Looking at this folder…`, or how many files of how
 * many it has taken in. A check the person asked for says so. */
export function syncingWords(run: FolderRun | null): string {
  const how = run?.deep ? "Deep check: " : run?.manual ? "Sync now: " : "";
  if (run && run.phase === "reading" && run.total > 0) {
    return `${how}Reading this folder: ${run.done.toLocaleString()} of ${count(run.total, "file")}.`;
  }
  return `${how}Looking at this folder…`;
}

/** `Checked 3 minutes ago.` */
export function checkedWords(checkedAt: number | null, now: number): string | null {
  return checkedAt == null ? null : `Checked ${agoWords(now - checkedAt)}.`;
}

/** `Next check in about 7 minutes.`, or that it is due. */
export function nextWords(nextAt: number | null, now: number): string | null {
  if (nextAt == null) return null;
  return nextAt <= now
    ? "The next check is due now."
    : `Next check in ${durationWords(nextAt - now)}.`;
}

/** The lines a folder's state is told in, under its name: what is true of it now. A problem, a
 * missing folder and a withdrawal are the server's own sentence, then when. A state this page does
 * not know is the server's headline, as it came. */
export function stateLines(folder: DocumentFolder, now: number): string[] {
  const problem = folder.problem;
  switch (folder.state) {
    case "waiting":
      return [
        folder.checkedAt == null
          ? "Workbench will look at this folder soon, for the first time."
          : "Workbench will check this folder in a moment.",
      ];
    case "syncing":
      return [syncingWords(folder.syncing)];
    case "synced":
      return [checkedWords(folder.checkedAt, now), nextWords(folder.nextAt, now)].filter(
        (line): line is string => line !== null,
      );
    case "problem":
      return [
        problem?.message ?? folder.headline,
        folder.syncedAt != null
          ? `The last check that worked was ${agoWords(now - folder.syncedAt)}.`
          : "It has not been checked well yet.",
      ];
    case "unavailable":
      return [
        problem?.message ?? folder.headline,
        folder.checkedAt != null
          ? `Workbench last looked ${agoWords(now - folder.checkedAt)}, and looks again at each check.`
          : "Workbench looks again at each check.",
      ];
    case "withdrawn":
      return [
        problem?.message ?? folder.headline,
        problem?.at != null ? `Taken away ${agoWords(now - problem.at)}.` : "",
      ].filter(Boolean);
    default:
      return [folder.headline];
  }
}

/** What the last check did, in a sentence: `Last check, a scheduled one: 3 new, 1 changed. It looked
 * at 412 files in 38 folders, and took under a second.` Null before one has ended. */
export function lastWords(last: FolderLast | null): string | null {
  if (!last) return null;
  const how =
    last.mode === "deep"
      ? "a deep check"
      : last.mode === "now"
        ? "one you asked for"
        : "a scheduled one";
  const changes = [
    last.added > 0 && `${last.added.toLocaleString()} new`,
    last.changed > 0 && `${last.changed.toLocaleString()} changed`,
    last.moved > 0 && `${last.moved.toLocaleString()} moved`,
    last.removed > 0 && `${last.removed.toLocaleString()} removed`,
    last.skipped > 0 && `${last.skipped.toLocaleString()} skipped`,
  ].filter(Boolean);
  const found = changes.length > 0 ? `${changes.join(", ")}.` : "nothing had changed.";
  const looked = `It looked at ${count(last.files, "file")} in ${count(last.directories, "folder")}${
    last.seconds != null ? `, and took ${tookWords(last.seconds)}` : ""
  }.`;
  return `Last check, ${how}: ${found} ${looked}`;
}

// --- what was left out, and why ---

/** The headings of what a check left out, in the order they are told, by the server's code. */
export const LEFT_HEADINGS: Record<string, string> = {
  ignored_default: "Always left out",
  ignored_hidden: "Hidden",
  ignored_temporary: "Temporary files",
  ignored_list: "Your ignore list",
  ignored_gitignore: "A .gitignore file",
  link: "Links, not followed",
  cloud: "Kept online by a cloud-sync program",
  special: "Not ordinary files",
  odd_name: "Names Workbench cannot open",
  too_deep: "Too deep",
  over_files: "Over the file limit",
};

/** The heading for a code: a code this page does not know says its own name. */
export const leftHeading = (code: string): string =>
  LEFT_HEADINGS[code] ?? code.replaceAll("_", " ");

export interface LeftGroup {
  code: string;
  heading: string;
  /** How many were left out for this reason, in all. */
  total: number;
  /** The ones the server named (it names the first 200 of every kind together). */
  items: LeftOut[];
}

/** What was left out, grouped by reason in the order of `LEFT_HEADINGS`, each with how many there
 * were in all and the ones that are named. */
export function leftGroups(detail: Pick<DocumentFolderDetail, "left" | "leftCounts">): LeftGroup[] {
  const codes = new Set([
    ...detail.left.map((item) => item.code),
    ...Object.keys(detail.leftCounts),
  ]);
  const order = Object.keys(LEFT_HEADINGS);
  return [...codes]
    .sort((a, b) => {
      const at = order.indexOf(a);
      const bt = order.indexOf(b);
      return (at < 0 ? order.length : at) - (bt < 0 ? order.length : bt) || a.localeCompare(b);
    })
    .map((code) => {
      const items = detail.left.filter((item) => item.code === code);
      return {
        code,
        heading: leftHeading(code),
        total: Math.max(detail.leftCounts[code] ?? 0, items.length),
        items,
      };
    });
}

/** How many things the last check left out, from the groups: every one, named or not. */
export const leftTotal = (groups: LeftGroup[]): number =>
  groups.reduce((sum, group) => sum + group.total, 0);

/** The first line of what was left out, on the folder's card (from the last check's numbers; the
 * names are in the list): `Left out: 31 files and 4 folders ignored, 2 links not followed.` Null
 * when there is nothing to say. */
export function leftSummary(last: FolderLast | null): string | null {
  if (!last) return null;
  const parts: string[] = [];
  const { files, folders } = last.ignored;
  if (files > 0 || folders > 0) {
    parts.push(
      `${[files > 0 && count(files, "file"), folders > 0 && count(folders, "folder")]
        .filter(Boolean)
        .join(" and ")} ignored`,
    );
  }
  if (last.links > 0) parts.push(`${count(last.links, "link")} not followed`);
  if (last.unlistedTotal > 0) {
    parts.push(`${count(last.unlistedTotal, "folder")} that could not be looked into`);
  }
  return parts.length > 0 ? `Left out: ${parts.join(", ")}.` : null;
}

/** The limits this folder has run into, said so: each one a sentence. */
export function capsWords(caps: FolderCaps): string[] {
  const said: string[] = [];
  if (caps.files.reached) {
    said.push(
      `This folder has more than the ${caps.files.limit.toLocaleString()} files Workbench keeps for one folder, so the files that come last are left out.`,
    );
  }
  if (caps.bytes.reached) {
    said.push(
      `The copies Workbench keeps of this folder's files reached ${sizeWords(caps.bytes.limit)}, so files after that are skipped.`,
    );
  }
  if (caps.depth.reached > 0) {
    said.push(
      `${count(caps.depth.reached, "folder")} more than ${caps.depth.limit} folders deep ${caps.depth.reached === 1 ? "is" : "are"} not looked at.`,
    );
  }
  if (caps.entries.reached) {
    said.push(
      "This folder has more names than Workbench looks at in one check, so some are not looked at.",
    );
  }
  return said;
}

// --- a file that was skipped or failed ---

/** The reason a file was skipped or failed, as a few plain words, by the server's code. */
export const FILE_REASONS: Record<string, string> = {
  unsupported: "A type Workbench does not read",
  not_document: "A picture or a sound recording",
  bad_zip: "Not a Word file",
  too_large: "Larger than Workbench reads",
  over_folder_size: "Over the folder's size limit",
  busy: "In use by another program",
  changed: "Changed while it was read",
  unreadable: "Could not be opened",
  hard_links: "More than one hard link",
  special: "Not an ordinary file",
  link: "A link, not followed",
  mount: "Another disk is mounted here",
  empty: "No text in it",
  password: "Protected by a password",
  damaged: "Damaged, or could not be read",
  no_text: "A scan, with no text in it",
  too_big: "Too big to open",
  memory: "Needs more memory than a reader is given",
  timeout: "Took too long to read",
  crashed: "The reader stopped unexpectedly",
  unavailable: "Workbench's reader would not start",
  missing: "Workbench's copy of it is gone",
  internal: "Workbench's own error",
  too_many_passages: "Over the passage limit",
};

/** The words for a reason's code: one this page does not know says its own name. */
export const reasonWords = (code: string): string =>
  FILE_REASONS[code] ?? code.replaceAll("_", " ");

// --- the list of a folder's files ---

export type FileFilter = "all" | "ready" | "reading" | "skipped" | "failed";

export const FILE_FILTERS: { value: FileFilter; label: string }[] = [
  { value: "all", label: "All files" },
  { value: "ready", label: "Ready" },
  { value: "reading", label: "Being read" },
  { value: "skipped", label: "Skipped" },
  { value: "failed", label: "Failed" },
];

/** How many files each filter holds, from the folder's own counts. */
export function filterCount(filter: FileFilter, counts: FolderCounts): number {
  switch (filter) {
    case "all":
      return counts.files;
    case "ready":
      return counts.byState.ready ?? 0;
    case "reading":
      return readingCount(counts);
    case "skipped":
      return counts.byState.skipped ?? 0;
    case "failed":
      return counts.byState.failed ?? 0;
  }
}

/** The filters to offer: every one that has files, and the one in use even when it has none (a
 * list that empties itself must not take its own filter away). */
export function filterOptions(
  counts: FolderCounts,
  current: FileFilter,
): { value: FileFilter; label: string; n: number }[] {
  return FILE_FILTERS.map((option) => ({ ...option, n: filterCount(option.value, counts) })).filter(
    (option) => option.value === "all" || option.value === current || option.n > 0,
  );
}

/** The reasons to offer: every code the folder's files were skipped or failed for, with how many,
 * most first. */
export function reasonOptions(counts: FolderCounts): { code: string; label: string; n: number }[] {
  return Object.entries(counts.byCode)
    .filter(([, n]) => n > 0)
    .map(([code, n]) => ({ code, label: reasonWords(code), n }))
    .sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
}

/** One request for a folder's files: a state (none for any), and which of that state's files. */
export interface FileRequest {
  state: string | null;
  offset: number;
  limit: number;
}

/** A file that waits to be read comes after the ones being read. */
const waitingLast = (a: string, b: string): number =>
  Number(a === "queued") - Number(b === "queued");

/** The requests that make one page of a filter. The server filters by one state, and "being read"
 * is every step a file goes through before it is read, so that page is cut from the lists of those
 * steps one after the other (the ones being read now, then the ones that wait), by the numbers the
 * folder says each holds: a page that falls across two of them is two requests. */
export function fileRequests(
  filter: FileFilter,
  counts: FolderCounts,
  offset: number,
  limit: number,
): FileRequest[] {
  if (filter === "all") return [{ state: null, offset, limit }];
  if (filter !== "reading") return [{ state: filter, offset, limit }];
  const requests: FileRequest[] = [];
  let start = 0;
  for (const state of readingStates(counts).sort(waitingLast)) {
    const end = start + (counts.byState[state] ?? 0);
    const from = Math.max(offset, start);
    const to = Math.min(offset + limit, end);
    if (from < to) requests.push({ state, offset: from - start, limit: to - from });
    start = end;
  }
  return requests;
}

/** `1 to 50 of 412`: where a page is in the list. */
export function rangeWords(offset: number, shown: number, total: number): string {
  if (total === 0) return "No files";
  return `${(offset + 1).toLocaleString()} to ${(offset + shown).toLocaleString()} of ${total.toLocaleString()}`;
}

// --- the rules ---

/** The ignore list as the person types it: a pattern on each line. */
export const ignoreText = (list: string[]): string => list.join("\n");

/** What was typed, as the list the server is sent: a line each, and a blank one is not a
 * pattern. Nothing else is judged here: the server says what it cannot use, and why. */
export const parseIgnore = (text: string): string[] =>
  text.split(/\r?\n/).filter((line) => line.trim() !== "");

export interface Rules {
  ignore: string[];
  includeHidden: boolean;
  useGitignore: boolean;
}

/** Whether the rules typed are not the ones the folder has. */
export function rulesChanged(folder: Rules, draft: Rules): boolean {
  return (
    folder.includeHidden !== draft.includeHidden ||
    folder.useGitignore !== draft.useGitignore ||
    folder.ignore.length !== draft.ignore.length ||
    folder.ignore.some((line, i) => line !== draft.ignore[i])
  );
}

/** How many patterns are typed, of how many are allowed. */
export const patternsWords = (typed: number, most: number): string =>
  `${typed.toLocaleString()} of ${most.toLocaleString()} patterns`;

/** What a change of rules does, said before it is saved. */
export const RULES_EFFECT =
  "Workbench checks the folder again at once. Files that are left out now leave your documents, and files that are not are read.";

// --- limits, stated ---

/** The limits a person has to know, each a sentence, from the numbers in effect. */
export function limitsWords(limits: FolderLimits): string[] {
  return [
    `Workbench does not watch your folders. It looks at each one again every ${count(limits.syncMinutes, "minute")}, and whenever you choose Sync now, so a change can take that long to show.`,
    "A quick check compares each file's size and time with what Workbench read, so an edit that keeps both is missed. Sync now with a deep check reads every file again and finds it.",
    "Only folders on the machine Workbench runs on can be added. Folders on your job sites cannot be added yet.",
    "The first read of a big folder takes a while. Files can be searched as each one is ready, and the counts on the folder say how far it has got.",
    `Workbench keeps its own copy of each file it reads, so a search never touches your folder. From one folder it takes up to ${limits.maxFiles.toLocaleString()} files and ${limits.maxMB.toLocaleString()} MB, ${limits.maxDepth} folders deep, and a file may be up to ${sizeWords(limits.maxFileBytes)}. ${limits.reads}`,
    `With the files you add yourself, Workbench keeps up to ${limits.maxPassages.toLocaleString()} passages for you.`,
    "The owner of this Workbench sets these limits.",
  ];
}

/** What the Folders section says first: what a folder is for, and that it is only ever read. */
export const FOLDERS_INTRO =
  "Point your documents at a folder the owner of this Workbench gave you. Workbench reads the files in it and keeps your documents in step with it, so a chat with Documents on can answer from them.";
export const READ_ONLY_WORDS =
  "Reading a folder for your documents never changes, adds or deletes anything in it.";

/** What the Add card says beside its button: what adding does, and where to stop it reading what
 * should not be read. */
export const ADD_WORDS =
  "Workbench starts reading it at once. If it holds things that should not be read, choose what to leave out first.";

/** What a deep check is for, said beside the button that runs one. */
export const DEEP_WORDS =
  "A quick check compares each file's size and time with what Workbench read, so an edit that keeps both is missed. A deep check reads every file again and compares what is in it, so it finds that edit. It takes longer on a big folder.";

/** What the Add card says when there is nothing to add: no folder was given to the person, or every
 * one they were given is added. Local folders are granted by the owner in Toolbox · Tools, so the
 * owner is told where to give themselves one, and anyone else whom to ask. */
export function nothingToAddWords(owner: boolean, anyGiven: boolean): string {
  if (anyGiven) {
    return owner
      ? "Every folder you were given is added. To add another, give yourself one in Toolbox · Tools, under Folders · File tools."
      : "Every folder you were given is added.";
  }
  return owner
    ? "No folder has been given to you yet. Give yourself one in Toolbox · Tools: under Folders · File tools, choose “Grant an existing folder” and pick your own name as the person. It will be here once it is granted."
    : "No folder has been given to you yet. The owner of this Workbench gives folders to people, in Toolbox · Tools. Ask them to give you one.";
}

// --- removing ---

/** What Remove folder does, asked before it is done. */
export function removeFolderWords(folder: Pick<DocumentFolder, "name" | "counts">): string {
  const files = folder.counts.files;
  return `Remove ${folder.name}? Workbench stops checking it${
    files > 0
      ? `, and takes the ${count(files, "file")} it has from it out of your documents: its copies and the text it read from them are deleted, so no chat can find them again`
      : ""
  }. The folder, the files in it and your access to it are never touched. You can add it again.`;
}

/** What leaving one file out does, asked before it is done. The server puts it on the folder's
 * ignore list, or it would come back at the next check. */
export function leaveOutWords(path: string): string {
  return `Leave out ${path}? Workbench deletes its copy of the file and the text it read from it, and adds /${path} to this folder's ignore list, so that it does not come back at the next check. The file in your folder is not touched.`;
}

// --- looking again ---

/** Whether a folder is changing: a check is under way or due, or its files are still being read. */
export const folderBusy = (folder: DocumentFolder): boolean =>
  folder.state === "syncing" || folder.state === "waiting" || readingCount(folder.counts) > 0;

/** How soon the folders are asked about again, in milliseconds: each second while anything is
 * changing, and otherwise each half minute, or when a check is due if that is sooner, so that
 * the screen sees it begin. A read that failed while something was changing is asked again after
 * a pause. */
export function foldersWait(page: DocumentFolders, now: number, failing: boolean): number {
  if (page.folders.some(folderBusy)) return failing ? RETRY_MS : POLL_MS;
  const due = page.folders.flatMap((folder) => (folder.nextAt != null ? [folder.nextAt] : []));
  const soonest = due.length > 0 ? Math.min(...due) - now : Number.POSITIVE_INFINITY;
  return Math.min(IDLE_POLL_MS, Math.max(POLL_MS, Math.ceil(soonest * 1000) + 1500));
}
