/**
 * The Folders section of the Documents screen (MR7): what a person sees from opening it to
 * removing a folder, against a small fake Workbench that answers as the real one does
 * (src/eugene_plexus_workbench/folder_docs_api.py): it keeps folders, moves them through their
 * states as it is asked, refuses what the real one refuses, and keeps every call so a test can say
 * what the page asked for. The panels a card opens (its files, what was left out, its rules) are in
 * FolderPanels.test.tsx.
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, api, del, post } from "../lib/api";
import { useDocumentFolders } from "../lib/useDocumentFolders";
import type { Me } from "../lib/types";
import {
  choice,
  counts,
  FakeFolders,
  folder,
  goneFolder,
  LIMITS,
  NOW,
  problemFolder,
  readingFolder,
  refusal,
  syncingFolder,
  waitingFolder,
  withdrawnFolder,
} from "../test-folders";
import { DocumentFolders } from "./DocumentFolders";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  documentText: vi.fn(),
  fileOf: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));
vi.mock("../lib/memory", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/memory")>()),
  saveFile: vi.fn(),
}));

let server: FakeFolders;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
  vi.mocked(api).mockReset();
  vi.mocked(post).mockReset();
  vi.mocked(del).mockReset();
  server = new FakeFolders([folder()]);
  server.install();
});
afterEach(() => vi.useRealTimers());

const settle = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

const OWNER: Pick<Me, "owner" | "consoleUrl"> = { owner: true, consoleUrl: "http://nas:8279/" };
const PERSON: Pick<Me, "owner" | "consoleUrl"> = { owner: false, consoleUrl: null };

/** The section as the Documents screen draws it: the hook reads the folders, the section shows them. */
function Section(props: {
  me?: Pick<Me, "owner" | "consoleUrl">;
  off?: boolean;
  busy?: boolean;
  askHere?: boolean;
  onAsk?: () => void;
  onTools?: () => void;
}) {
  const folders = useDocumentFolders();
  return (
    <DocumentFolders
      folders={folders}
      me={props.me ?? PERSON}
      off={props.off ?? false}
      busy={props.busy ?? false}
      askHere={props.askHere ?? false}
      onAsk={props.onAsk ?? vi.fn()}
      onTools={props.onTools}
    />
  );
}

async function show(props: Parameters<typeof Section>[0] = {}) {
  const view = render(<Section {...props} />);
  await settle();
  return view;
}

const click = (id: string, at = 0) => fireEvent.click(screen.getAllByTestId(id)[at]!);
const text = (id: string, at = 0) => screen.getAllByTestId(id)[at]!.textContent ?? "";
/** The counts of the open card, one item each: `12 files`, `10 ready`. */
const counted = () => screen.getAllByTestId("folder-count").map((item) => item.textContent);
const cards = () => screen.queryAllByTestId("folder");

describe("the section", () => {
  it("is headed Folders, and says what it is for and that it only reads", async () => {
    await show();
    expect(screen.getByRole("heading", { level: 2, name: "Folders" })).toBeInTheDocument();
    expect(text("folders-intro")).toBe(
      "Point your documents at a folder the owner of this Workbench gave you. Workbench reads the files in it and keeps your documents in step with it, so a chat with Documents on can answer from them. Reading a folder for your documents never changes, adds or deletes anything in it.",
    );
  });

  it("says it is reading them while it waits, and nothing else", async () => {
    vi.mocked(api).mockReturnValue(new Promise(() => undefined) as never);
    render(<Section />);
    await settle();
    expect(screen.getByRole("status")).toHaveTextContent("Reading your folders…");
    expect(screen.queryByTestId("folders-add")).toBeNull();
    expect(screen.queryByTestId("folders-limits")).toBeNull();
  });

  it("says why it could not read them, in the server's words, and reads again with Try again", async () => {
    server.refuse.set("GET /api/documents/folders", new Error("Workbench answered 502."));
    await show();
    expect(screen.getByTestId("folders-problem")).toHaveTextContent(
      "Could not read your folders" + "Workbench answered 502.",
    );
    expect(cards()).toHaveLength(0);
    server.refuse.clear();
    click("folders-retry");
    await settle();
    expect(cards()).toHaveLength(1);
    expect(screen.queryByTestId("folders-problem")).toBeNull();
  });

  it("keeps the folders it showed when a later read fails, and says so beside them", async () => {
    await show();
    server.refuse.set("GET /api/documents/folders", new Error("Workbench answered 502."));
    await wait(30_000);
    expect(cards()).toHaveLength(1);
    expect(screen.getByTestId("folders-problem")).toHaveTextContent(
      "Could not read your folders again" + "Workbench answered 502.",
    );
    server.refuse.clear();
    click("folders-retry");
    await settle();
    expect(screen.queryByTestId("folders-problem")).toBeNull();
  });

  it("says plainly that Workbench does not watch folders, and how often it looks, from the number in effect", async () => {
    server.page.limits = { ...LIMITS, syncMinutes: 5 };
    await show();
    expect(text("folders-limits")).toContain(
      "Workbench does not watch your folders. It looks at each one again every 5 minutes, and whenever you choose Sync now, so a change can take that long to show.",
    );
  });

  it("says plainly that a quick check misses an edit that keeps a file's size and time, and that a deep check finds it", async () => {
    await show();
    expect(text("folders-limits")).toContain(
      "A quick check compares each file's size and time with what Workbench read, so an edit that keeps both is missed. Sync now with a deep check reads every file again and finds it.",
    );
  });

  it("says plainly that folders on job sites cannot be added", async () => {
    await show();
    expect(text("folders-limits")).toContain(
      "Only folders on the machine Workbench runs on can be added. Folders on your job sites cannot be added yet.",
    );
  });

  it("says plainly that a first read of many files takes a while, and the caps from the numbers in effect", async () => {
    await show();
    const limits = text("folders-limits");
    expect(limits).toContain("The first read of a big folder takes a while.");
    expect(limits).toContain(
      "Workbench keeps its own copy of each file it reads, so a search never touches your folder.",
    );
    expect(limits).toContain("it takes up to 10,000 files and 2,048 MB, 12 folders deep,");
    expect(limits).toContain("and a file may be up to 50 MiB.");
    expect(limits).toContain("keeps up to 100,000 passages for you");
    expect(limits).toContain("The owner of this Workbench sets these limits.");
  });

  it("totals the folders apart from the person's own files", async () => {
    server.page.folders = [folder(), readingFolder({ id: "g2", name: "Field reports" })];
    await show();
    expect(text("folders-totals")).toBe("2 folders · 416 files · 1,935 passages · 4.2 MiB");
    expect(
      screen.getByText("These are counted apart from the files you add yourself."),
    ).toBeInTheDocument();
  });

  it("says no totals when there is no folder", async () => {
    server.page = { ...server.page, folders: [] };
    await show();
    expect(screen.queryByTestId("folders-totals")).toBeNull();
    expect(cards()).toHaveLength(0);
  });

  it("has Ask about your documents only when the screen says it belongs here, and it asks", async () => {
    const asked = vi.fn();
    const view = await show({ askHere: true, onAsk: asked });
    fireEvent.click(screen.getByTestId("documents-ask"));
    expect(asked).toHaveBeenCalledTimes(1);
    view.unmount();
    await show({ askHere: false });
    expect(screen.queryByTestId("documents-ask")).toBeNull();
  });

  it("tells the server's words where this Workbench cannot read folders, and offers no way to add one", async () => {
    server.page = {
      ...server.page,
      available: false,
      reason: "Local servers need Workbench's own OS account. Use a Windows service install.",
    };
    await show();
    expect(screen.getByTestId("folders-unavailable")).toHaveTextContent(
      "Folders cannot be used on this Workbench" +
        "Local servers need Workbench's own OS account. Use a Windows service install.",
    );
    expect(screen.queryByTestId("folders-add")).toBeNull();
    // A folder it already has is still shown.
    expect(cards()).toHaveLength(1);
  });

  it("says a word of its own where the server gives no reason it cannot read folders", async () => {
    server.page = { ...server.page, available: false, reason: null };
    await show();
    expect(screen.getByTestId("folders-unavailable")).toHaveTextContent(
      "Workbench cannot read folders here.",
    );
  });
});

describe("where the owner has turned documents off", () => {
  it("lets the person look at their folders and remove them, and nothing else", async () => {
    await show({ off: true });
    expect(cards()).toHaveLength(1);
    expect(screen.queryByTestId("folders-add")).toBeNull();
    expect(screen.queryByTestId("folders-limits")).toBeNull();
    expect(screen.queryByTestId("folder-sync")).toBeNull();
    expect(screen.queryByTestId("folder-deep-details")).toBeNull();
    expect(screen.queryByTestId("folder-rules-toggle")).toBeNull();
    expect(screen.getByTestId("folder-files-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("folder-left-toggle")).toBeInTheDocument();
    expect(screen.getByTestId("folder-remove")).toBeInTheDocument();
    expect(text("folder-locked")).toBe(
      "The owner of this Workbench has turned off documents, so this folder is not checked now.",
    );
  });

  it("is the same where the server says documents are not enabled", async () => {
    server.page = { ...server.page, enabled: false };
    await show();
    expect(screen.queryByTestId("folders-add")).toBeNull();
    expect(screen.queryByTestId("folder-sync")).toBeNull();
  });

  it("lets a folder be removed, and says it cannot be checked", async () => {
    await show({ off: true });
    click("folder-remove");
    click("folder-remove-go");
    await settle();
    expect(server.made("DELETE")).toEqual([{ path: "/api/documents/folders/g1", body: undefined }]);
    expect(cards()).toHaveLength(0);
  });
});

describe("a folder, by where it stands", () => {
  it("synced: checked a while ago and when the next check is, with what it holds", async () => {
    await show();
    expect(text("folder-name")).toBe("Work notes");
    expect(text("folder-badge")).toBe("Synced");
    expect(screen.getByTestId("folder")).toHaveAttribute("data-state", "synced");
    expect(text("folder-status")).toBe("Checked 3 minutes ago.Next check in about 7 minutes.");
    expect(
      screen.getAllByTestId("folder-count").map((item) => [item.dataset.kind, item.dataset.n]),
    ).toEqual([
      ["files", "12"],
      ["ready", "10"],
      ["reading", "0"],
      ["skipped", "2"],
    ]);
    expect(counted()).toEqual(["12 files", "10 ready", "0 being read", "2 skipped"]);
    expect(text("folder-holds")).toBe("31 passages · 47 KiB · Added Oct 10");
    expect(text("folder-last")).toBe(
      "Last check, a scheduled one: nothing had changed. It looked at 12 files in 3 folders, and took under a second.",
    );
  });

  it("keeps the time true between reads: it follows the clock, not the last answer", async () => {
    await show();
    expect(text("folder-status")).toContain("Checked 3 minutes ago.");
    // Time goes by while the answer stays the same: the next check was due in 7 minutes.
    await wait(4 * 60 * 1000);
    expect(text("folder-status")).toBe("Checked 7 minutes ago.Next check in about 3 minutes.");
    await wait(4 * 60 * 1000);
    expect(text("folder-status")).toBe("Checked 11 minutes ago.The next check is due now.");
  });

  it("keeps the time true even when the folders cannot be read again: the clock ticks by itself", async () => {
    await show();
    server.refuse.set("GET /api/documents/folders", new Error("Workbench answered 502."));
    // The clock ticks each ten seconds, and nothing new is read until the half minute is up: what
    // was shown is kept, and its words follow the clock (a tick at 20 s says 200 s, one at 30 s 210).
    await wait(29 * 1000);
    expect(text("folder-status")).toBe("Checked 3 minutes ago.Next check in about 7 minutes.");
    expect(screen.queryByTestId("folders-problem")).toBeNull();
    await wait(1000);
    expect(text("folder-status")).toBe("Checked 4 minutes ago.Next check in about 6 minutes.");
    expect(screen.getByTestId("folders-problem")).toBeInTheDocument();
  });

  it("waiting: says it will look soon, for the first time", async () => {
    server.page.folders = [waitingFolder()];
    await show();
    expect(text("folder-badge")).toBe("Waiting");
    expect(text("folder-status")).toBe(
      "Workbench will look at this folder soon, for the first time.",
    );
    expect(counted()).toEqual(["0 files", "0 ready", "0 being read", "0 skipped"]);
    expect(screen.queryByTestId("folder-last")).toBeNull();
  });

  it("syncing, still looking: says so, with no bar that would say how far", async () => {
    server.page.folders = [syncingFolder({ phase: "looking", done: 0, total: 0 })];
    await show();
    expect(text("folder-badge")).toBe("Syncing");
    expect(text("folder-status")).toBe("Looking at this folder…");
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("syncing, reading: says how many files of how many, with a bar that says the same", async () => {
    server.page.folders = [syncingFolder()];
    await show();
    expect(text("folder-status")).toBe("Reading this folder: 1,200 of 4,000 files.");
    const bar = screen.getByRole("progressbar", { name: "Reading Work notes" });
    expect(bar).toHaveAttribute("max", "4000");
    expect(bar).toHaveAttribute("value", "1200");
    expect(screen.getByTestId("folder")).toHaveAttribute("aria-busy", "true");
  });

  it("syncing: says the check is one the person asked for, and a deep one", async () => {
    server.page.folders = [syncingFolder({ manual: true, deep: true })];
    await show();
    expect(text("folder-status")).toBe("Deep check: Reading this folder: 1,200 of 4,000 files.");
  });

  it("moves on by itself: the counts and the bar follow each read, then it is synced", async () => {
    server.page.folders = [syncingFolder({ done: 1_200 })];
    await show();
    expect(text("folder-status")).toContain("1,200 of 4,000");
    server.update("g1", { syncing: { ...syncingFolder().syncing!, done: 3_000 } });
    await wait(1_000);
    expect(text("folder-status")).toContain("3,000 of 4,000");
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "3000");
    server.update("g1", folder({ id: "g1", nextAt: NOW + 600 }));
    await wait(1_000);
    expect(text("folder-badge")).toBe("Synced");
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("synced, with files still being read: says how many are done, as a count and a bar", async () => {
    server.page.folders = [readingFolder()];
    await show();
    expect(text("folder-badge")).toBe("Synced");
    expect(counted()).toEqual(["404 files", "380 ready", "15 being read", "9 skipped"]);
    expect(text("folder-reading")).toBe(
      "Reading the files: 389 of 404 are done. 4 files can be searched by words already, and are having their meaning added.",
    );
    const bar = screen.getByRole("progressbar", { name: "Files of Work notes that are done" });
    expect(bar).toHaveAttribute("max", "404");
    expect(bar).toHaveAttribute("value", "389");
  });

  it("says nothing of reading once every file is read", async () => {
    await show();
    expect(screen.queryByTestId("folder-reading")).toBeNull();
  });

  it("says failed once a file has", async () => {
    server.page.folders = [
      folder({ counts: counts({ byState: { ...counts().byState, failed: 1, ready: 9 } }) }),
    ];
    await show();
    expect(text("folder-counts")).toContain("1 failed");
  });

  it("problem: the server's own words in an error, and when the last check that worked was", async () => {
    server.page.folders = [problemFolder()];
    await show();
    expect(text("folder-badge")).toBe("Problem");
    expect(text("folder-status")).toBe(
      "Workbench could not look at this folder (permission denied). Its files are as they were at the last check.The last check that worked was 3 minutes ago.",
    );
    expect(screen.getByTestId("folder-status")).toHaveClass("notice--error");
    // The files it had are still there, and still counted.
    expect(text("folder-counts")).toContain("12 files");
    expect(screen.getByTestId("folder-sync")).toBeInTheDocument();
  });

  it("not available: the server's words, that it looks again, no counts and no files to open", async () => {
    server.page.folders = [goneFolder()];
    await show();
    expect(text("folder-badge")).toBe("Not available");
    expect(text("folder-status")).toBe(
      "This folder is not there any more. 12 documents from it were taken out of your search, and they come back if the folder does.Workbench last looked 3 minutes ago, and looks again at each check.",
    );
    expect(screen.getByTestId("folder-status")).toHaveClass("notice--warn");
    expect(screen.queryByTestId("folder-counts")).toBeNull();
    expect(screen.queryByTestId("folder-files-toggle")).toBeNull();
    expect(screen.queryByTestId("folder-left-toggle")).toBeNull();
    expect(screen.queryByTestId("folder-rules-toggle")).toBeNull();
    // It can be tried again, and removed.
    expect(screen.getByTestId("folder-sync")).toBeInTheDocument();
    expect(screen.getByTestId("folder-remove")).toBeInTheDocument();
  });

  it("withdrawn: the server's words, when, and only Dismiss", async () => {
    server.page.folders = [withdrawnFolder()];
    await show();
    expect(text("folder-badge")).toBe("Withdrawn");
    expect(text("folder-status")).toBe(
      "The owner took away your access to this folder. 12 documents from it were taken out of your search.Taken away 2 days ago.",
    );
    expect(screen.queryByTestId("folder-counts")).toBeNull();
    expect(screen.queryByTestId("folder-sync")).toBeNull();
    expect(screen.queryByTestId("folder-deep-details")).toBeNull();
    expect(screen.queryByTestId("folder-remove")).toBeNull();
    expect(screen.queryByTestId("folder-files-toggle")).toBeNull();
    expect(screen.getByTestId("folder-dismiss")).toHaveAccessibleName("Dismiss: Work notes");
  });

  it("says a limit that was reached, in a warning", async () => {
    server.page.folders = [
      folder({
        caps: {
          ...folder().caps,
          files: { limit: 10_000, found: 10_000, reached: true },
          depth: { limit: 12, reached: 2 },
        },
      }),
    ];
    await show();
    expect(screen.getByTestId("folder-caps")).toHaveClass("notice--warn");
    expect(text("folder-caps")).toBe(
      "This folder has more than the 10,000 files Workbench keeps for one folder, so the files that come last are left out.2 folders more than 12 folders deep are not looked at.",
    );
  });

  it("says nothing of limits that were not reached", async () => {
    await show();
    expect(screen.queryByTestId("folder-caps")).toBeNull();
  });

  it("says what was left out in a line, from the last check", async () => {
    server.page.folders = [
      folder({
        last: {
          ...folder().last!,
          ignored: { files: 31, folders: 4 },
          links: 2,
          unlistedTotal: 1,
        },
      }),
    ];
    await show();
    expect(text("folder-left-summary")).toBe(
      "Left out: 31 files and 4 folders ignored, 2 links not followed, 1 folder that could not be looked into.",
    );
  });

  it("says nothing of what was left out when nothing was", async () => {
    await show();
    expect(screen.queryByTestId("folder-left-summary")).toBeNull();
  });

  it("shows each folder in its own card, with its own state", async () => {
    server.page.folders = [
      folder(),
      syncingFolder({}, { id: "g2", name: "Field reports" }),
      problemFolder({ id: "g3", name: "Archive" }),
    ];
    await show();
    expect(cards().map((c) => c.dataset.state)).toEqual(["synced", "syncing", "problem"]);
    expect(screen.getAllByTestId("folder-name").map((n) => n.textContent)).toEqual([
      "Work notes",
      "Field reports",
      "Archive",
    ]);
  });

  it("wraps a name that is long and has no spaces, in any direction of writing", async () => {
    server.page.folders = [folder({ name: "x".repeat(120) })];
    await show();
    const name = within(cards()[0]!).getByRole("heading", { level: 3 });
    expect(name).toHaveAttribute("dir", "auto");
    expect(name).toHaveClass("title-card");
  });
});

describe("Sync now", () => {
  it("asks for a quick check, and the card follows it: waiting, then syncing", async () => {
    await show();
    click("folder-sync");
    await settle();
    expect(server.made("POST")).toEqual([
      { path: "/api/documents/folders/g1/sync", body: { deep: false } },
    ]);
    expect(text("folder-badge")).toBe("Waiting");
    expect(text("folder-status")).toBe("Workbench will check this folder in a moment.");
    server.update("g1", syncingFolder({ manual: true }));
    await wait(1_000);
    expect(text("folder-status")).toBe("Sync now: Reading this folder: 1,200 of 4,000 files.");
  });

  it("is named for its folder, so a screen reader can tell them apart", async () => {
    server.page.folders = [folder(), folder({ id: "g2", name: "Field reports" })];
    await show();
    expect(screen.getAllByTestId("folder-sync").map((b) => b.getAttribute("aria-label"))).toEqual([
      "Sync now: Work notes",
      "Sync now: Field reports",
    ]);
    expect(screen.getAllByRole("button", { name: /Sync now/ })[0]).toHaveTextContent("Sync now");
  });

  it("does not ask again while a check is under way or about to be, and says it cannot", async () => {
    server.page.folders = [syncingFolder()];
    await show();
    const button = screen.getByTestId("folder-sync");
    expect(button).toHaveAttribute("aria-disabled", "true");
    // It stays in the order of the keyboard: it is not taken away from whoever is on it.
    expect(button).not.toBeDisabled();
    fireEvent.click(button);
    await settle();
    expect(server.made("POST")).toEqual([]);
  });

  it("is held while it asks, and the keyboard is given back to it when it is done", async () => {
    await show();
    const button = screen.getByTestId("folder-sync");
    button.focus();
    fireEvent.click(button);
    expect(button).toBeDisabled();
    // A browser puts focus on the page when the control that has it is disabled (jsdom does not).
    const lost = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(button, "focus");
    await settle();
    expect(focus).toHaveBeenCalled();
    lost.mockRestore();
  });

  it("says why when the server refuses, on the card", async () => {
    server.refuse.set(
      "POST /api/documents/folders/g1/sync",
      refusal(409, "The owner took away your access to this folder."),
    );
    await show();
    click("folder-sync");
    await settle();
    expect(text("folder-problem")).toBe("The owner took away your access to this folder.");
    expect(screen.getByTestId("folder-problem")).toHaveAttribute("role", "alert");
    // And the screen reads the folder again, so it shows what is true.
    expect(server.made("GET").length).toBeGreaterThan(1);
  });

  it("is held while something else on the screen is being changed", async () => {
    await show({ busy: true });
    expect(screen.getByTestId("folder-sync")).toBeDisabled();
    expect(screen.getByTestId("folder-remove")).toBeDisabled();
  });
});

describe("a deep check", () => {
  it("is an expert option: a disclosure that says what it is for, with a button that asks for one", async () => {
    await show();
    expect(screen.getByTestId("folder-deep-details")).not.toHaveAttribute("open");
    expect(text("folder-deep-details")).toContain(
      "A quick check compares each file's size and time with what Workbench read, so an edit that keeps both is missed. A deep check reads every file again and compares what is in it, so it finds that edit. It takes longer on a big folder.",
    );
    click("folder-deep");
    await settle();
    expect(server.made("POST")).toEqual([
      { path: "/api/documents/folders/g1/sync", body: { deep: true } },
    ]);
  });

  it("is named for its folder, and described by what it is for", async () => {
    await show();
    expect(screen.getByTestId("folder-deep")).toHaveAccessibleName("Run a deep check: Work notes");
    expect(screen.getByTestId("folder-deep")).toHaveAccessibleDescription(/reads every file again/);
    expect(screen.getByTestId("folder-deep-toggle")).toHaveAccessibleName(
      "A deep check: Work notes",
    );
  });

  it("is not asked for while a check is under way", async () => {
    server.page.folders = [syncingFolder()];
    await show();
    expect(screen.getByTestId("folder-deep")).toHaveAttribute("aria-disabled", "true");
    click("folder-deep");
    await settle();
    expect(server.made("POST")).toEqual([]);
  });
});

describe("Remove folder", () => {
  it("asks first, and says the folder's files are never touched and what is deleted", async () => {
    await show();
    click("folder-remove");
    expect(screen.getByRole("group", { name: "Remove Work notes?" })).toBeInTheDocument();
    expect(text("folder-remove-confirm")).toContain(
      "Remove Work notes? Workbench stops checking it, and takes the 12 files it has from it out of your documents: its copies and the text it read from them are deleted, so no chat can find them again. The folder, the files in it and your access to it are never touched. You can add it again.",
    );
    expect(server.made("DELETE")).toEqual([]);
  });

  it("puts the keyboard on Keep it, and takes it back to Remove folder when the question is closed", async () => {
    await show();
    click("folder-remove");
    expect(screen.getByTestId("folder-remove-keep")).toHaveFocus();
    fireEvent.click(screen.getByTestId("folder-remove-keep"));
    expect(screen.queryByTestId("folder-remove-confirm")).toBeNull();
    expect(screen.getByTestId("folder-remove")).toHaveFocus();
    expect(server.made("DELETE")).toEqual([]);
  });

  it("is closed by Escape, as Keep it is", async () => {
    await show();
    click("folder-remove");
    fireEvent.keyDown(screen.getByTestId("folder-remove-confirm"), { key: "Escape" });
    expect(screen.queryByTestId("folder-remove-confirm")).toBeNull();
    expect(screen.getByTestId("folder-remove")).toHaveFocus();
    expect(server.made("DELETE")).toEqual([]);
  });

  it("removes it, and the card goes; the keyboard goes to the heading of the section", async () => {
    await show();
    click("folder-remove");
    click("folder-remove-go");
    await settle();
    expect(server.made("DELETE")).toEqual([{ path: "/api/documents/folders/g1", body: undefined }]);
    expect(cards()).toHaveLength(0);
    expect(screen.getByRole("heading", { level: 2, name: "Folders" })).toHaveFocus();
  });

  it("makes the folder one that can be added again", async () => {
    server.page = {
      ...server.page,
      grants: [choice({ id: "g1", name: "Work notes", added: true })],
    };
    await show();
    expect(screen.getByTestId("folders-add-none")).toHaveTextContent(
      "Every folder you were given is added.",
    );
    click("folder-remove");
    click("folder-remove-go");
    await settle();
    expect(screen.getByTestId("folders-add-select")).toHaveValue("g1");
  });

  it("says why when it could not be removed, on the card, which stays", async () => {
    server.refuse.set(
      "DELETE /api/documents/folders/g1",
      refusal(
        500,
        "Workbench could not delete harbour.md (another program is using it). Nothing was removed.",
      ),
    );
    await show();
    click("folder-remove");
    click("folder-remove-go");
    await settle();
    expect(text("folder-problem")).toBe(
      "Workbench could not delete harbour.md (another program is using it). Nothing was removed.",
    );
    expect(cards()).toHaveLength(1);
  });

  it("is available for a folder that is not there, and for one that has a problem", async () => {
    server.page.folders = [goneFolder(), problemFolder({ id: "g2", name: "Archive" })];
    await show();
    expect(screen.getAllByTestId("folder-remove")).toHaveLength(2);
    expect(screen.getAllByTestId("folder-remove")[1]).toHaveAccessibleName(
      "Remove folder: Archive",
    );
  });
});

describe("Dismiss", () => {
  it("clears a withdrawn folder at once, with no question: its documents are out already", async () => {
    server.page.folders = [withdrawnFolder()];
    await show();
    click("folder-dismiss");
    await settle();
    expect(server.made("DELETE")).toEqual([{ path: "/api/documents/folders/g1", body: undefined }]);
    expect(cards()).toHaveLength(0);
    expect(screen.getByRole("heading", { level: 2, name: "Folders" })).toHaveFocus();
  });

  it("says why when it could not", async () => {
    server.page.folders = [withdrawnFolder()];
    server.refuse.set("DELETE /api/documents/folders/g1", new Error("Workbench answered 502."));
    await show();
    click("folder-dismiss");
    await settle();
    expect(text("folder-problem")).toBe("Workbench answered 502.");
    expect(cards()).toHaveLength(1);
  });
});

describe("Add a folder", () => {
  const given = [
    choice({ id: "g1", name: "Work notes", added: true }),
    choice({ id: "g2", name: "Field reports" }),
    choice({ id: "g3", name: "Archive", writable: true }),
  ];

  it("offers the folders given and not added, by name, never by path", async () => {
    server.page = { ...server.page, grants: given };
    await show();
    const select = screen.getByTestId("folders-add-select");
    expect(select).toHaveAccessibleName("Folder to add");
    expect([...within(select).getAllByRole("option")].map((o) => o.textContent)).toEqual([
      "Field reports",
      "Archive",
    ]);
    expect(select).toHaveValue("g2");
    expect(screen.getByTestId("folders-add").textContent).not.toMatch(/[A-Za-z]:\\|\/home\//);
  });

  it("says what adding does, beside the button that adds, and that it only reads once, in the intro", async () => {
    server.page = { ...server.page, grants: given };
    await show();
    expect(screen.getByTestId("folders-add-go")).toHaveAccessibleDescription(
      "Workbench starts reading it at once. If it holds things that should not be read, choose what to leave out first.",
    );
    // The same sentence is not said twice on the screen.
    expect(
      screen
        .getByTestId("folders-section")
        .textContent?.split("never changes, adds or deletes anything in it."),
    ).toHaveLength(2);
  });

  it("adds the folder chosen with the easy defaults: nothing left out, hidden files not read, .gitignore respected", async () => {
    server.page = { ...server.page, grants: given };
    await show();
    fireEvent.change(screen.getByTestId("folders-add-select"), { target: { value: "g3" } });
    click("folders-add-go");
    await settle();
    expect(server.made("POST")).toEqual([
      {
        path: "/api/documents/folders",
        body: { grantId: "g3", ignore: [], includeHidden: false, useGitignore: true },
      },
    ]);
    // It is a card now, waiting for its first look, and the keyboard is on it.
    expect(cards()).toHaveLength(2);
    expect(cards()[1]).toHaveAttribute("data-folder-id", "g3");
    expect(cards()[1]).toHaveAttribute("data-state", "waiting");
    expect(within(cards()[1]!).getByRole("heading", { level: 3 })).toHaveFocus();
    // And it is no longer offered.
    expect(
      [...within(screen.getByTestId("folders-add-select")).getAllByRole("option")].map(
        (o) => o.textContent,
      ),
    ).toEqual(["Field reports"]);
  });

  it("adds the first folder not added when none is chosen", async () => {
    server.page = { ...server.page, grants: given };
    await show();
    click("folders-add-go");
    await settle();
    expect(server.made("POST")[0]!.body).toMatchObject({ grantId: "g2" });
  });

  it("lets what to leave out be written first, under a disclosure that starts closed", async () => {
    server.page = { ...server.page, grants: given };
    await show();
    expect(screen.getByTestId("folders-add-rules")).not.toHaveAttribute("open");
    expect(screen.getByTestId("folders-add-rules-toggle")).toHaveAccessibleName(
      "Choose what to leave out first",
    );
    fireEvent.change(screen.getByTestId("folders-add-ignore"), {
      target: { value: "*.log\n\n/archive/\n" },
    });
    fireEvent.click(screen.getByTestId("folders-add-hidden"));
    fireEvent.click(screen.getByTestId("folders-add-gitignore"));
    click("folders-add-go");
    await settle();
    expect(server.made("POST")[0]!.body).toEqual({
      grantId: "g2",
      ignore: ["*.log", "/archive/"],
      includeHidden: true,
      useGitignore: false,
    });
  });

  it("says how many patterns are written, of how many are allowed", async () => {
    server.page = { ...server.page, grants: given };
    await show();
    expect(text("folders-add-count")).toBe("0 of 100 patterns");
    fireEvent.change(screen.getByTestId("folders-add-ignore"), {
      target: { value: "a\nb\nc" },
    });
    expect(text("folders-add-count")).toBe("3 of 100 patterns");
  });

  it("says what is always left out, from the server's own list", async () => {
    server.page = { ...server.page, grants: given };
    await show();
    const always = text("folders-add-always");
    expect(always).toContain(
      "Folders: .git, node_modules, __pycache__, .venv, site-packages, $RECYCLE.BIN",
    );
    expect(always).toContain("Files: Thumbs.db, desktop.ini, .DS_Store");
    expect(always).toContain("Temporary files: ~$*, *.tmp, *.swp, *~, *.crdownload");
    expect(always).toContain("These stay left out even when hidden files are read.");
  });

  it("says a pattern the server cannot use under the patterns, in its words, and opens them to show it", async () => {
    server.page = { ...server.page, grants: given };
    server.refuse.set(
      "POST /api/documents/folders",
      refusal(422, "Start a pattern with a name, not with !. This list only ignores."),
    );
    await show();
    fireEvent.change(screen.getByTestId("folders-add-ignore"), { target: { value: "!keep" } });
    click("folders-add-go");
    await settle();
    expect(text("folders-add-error")).toBe(
      "Start a pattern with a name, not with !. This list only ignores.",
    );
    expect(screen.getByTestId("folders-add-error")).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("folders-add-ignore")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("folders-add-ignore")).toHaveAccessibleDescription(
      /Start a pattern with a name/,
    );
    expect(screen.getByTestId("folders-add-rules")).toHaveAttribute("open");
    expect(screen.queryByTestId("folders-add-problem")).toBeNull();
    // What was typed stays, so it can be mended; typing clears the reason.
    expect(screen.getByTestId("folders-add-ignore")).toHaveValue("!keep");
    fireEvent.change(screen.getByTestId("folders-add-ignore"), { target: { value: "keep" } });
    expect(screen.queryByTestId("folders-add-error")).toBeNull();
  });

  it.each([
    [409, "This folder is already in your documents."],
    [404, "There is no such folder."],
    [403, "The owner of this Workbench has turned off documents."],
    [503, "Local servers need Workbench's own OS account."],
  ])("says any other refusal (%i) by the button, in the server's words", async (status, said) => {
    server.page = { ...server.page, grants: given };
    server.refuse.set("POST /api/documents/folders", refusal(status, said));
    await show();
    click("folders-add-go");
    await settle();
    expect(text("folders-add-problem")).toBe(said);
    expect(screen.queryByTestId("folders-add-error")).toBeNull();
    expect(cards()).toHaveLength(1);
  });

  it("is held while it adds, and by a screen that is busy", async () => {
    server.page = { ...server.page, grants: given };
    const view = await show();
    click("folders-add-go");
    expect(screen.getByTestId("folders-add-go")).toBeDisabled();
    expect(screen.getByTestId("folders-add-select")).toBeDisabled();
    await settle();
    view.unmount();
    await show({ busy: true });
    expect(screen.getByTestId("folders-add-go")).toBeDisabled();
  });

  it("tells the owner who has been given no folder where to give one, and opens it", async () => {
    server.page = { ...server.page, grants: [] };
    const opened = vi.fn();
    await show({ me: OWNER, onTools: opened });
    expect(text("folders-add-none")).toBe(
      "No folder has been given to you yet. Give yourself one in Toolbox · Tools: under Folders · File tools, choose “Grant an existing folder” and pick your own name as the person. It will be here once it is granted.",
    );
    expect(screen.queryByTestId("folders-add-select")).toBeNull();
    fireEvent.click(screen.getByTestId("folders-open-tools"));
    expect(opened).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("folders-open-tools")).toHaveAccessibleName("Open Toolbox · Tools");
  });

  it("tells anyone else to ask the owner, and gives them nothing to open", async () => {
    server.page = { ...server.page, grants: [] };
    await show({ me: PERSON, onTools: vi.fn() });
    expect(text("folders-add-none")).toBe(
      "No folder has been given to you yet. The owner of this Workbench gives folders to people, in Toolbox · Tools. Ask them to give you one.",
    );
    expect(screen.queryByTestId("folders-open-tools")).toBeNull();
  });

  it("gives the owner no button where there is no way to open Tools", async () => {
    server.page = { ...server.page, grants: [] };
    await show({ me: OWNER });
    expect(screen.queryByTestId("folders-open-tools")).toBeNull();
  });

  it("says every folder given is added, and tells the owner how to be given another, and opens it", async () => {
    server.page = {
      ...server.page,
      grants: [choice({ id: "g1", name: "Work notes", added: true })],
    };
    const opened = vi.fn();
    await show({ me: OWNER, onTools: opened });
    expect(text("folders-add-none")).toBe(
      "Every folder you were given is added. To add another, give yourself one in Toolbox · Tools, under Folders · File tools.",
    );
    expect(screen.queryByTestId("folders-add-go")).toBeNull();
    fireEvent.click(screen.getByTestId("folders-open-tools"));
    expect(opened).toHaveBeenCalledTimes(1);
  });

  it("says every folder given is added, and nothing more, to anyone else", async () => {
    server.page = {
      ...server.page,
      grants: [choice({ id: "g1", name: "Work notes", added: true })],
    };
    await show({ me: PERSON, onTools: vi.fn() });
    expect(text("folders-add-none")).toBe("Every folder you were given is added.");
    expect(screen.queryByTestId("folders-open-tools")).toBeNull();
  });

  it("is the one thing on the screen of a person with no folder at all, beside the limits", async () => {
    server.page = { ...server.page, folders: [], grants: [] };
    await show({ me: PERSON });
    expect(cards()).toHaveLength(0);
    expect(screen.getByTestId("folders-add")).toBeInTheDocument();
    expect(screen.getByTestId("folders-limits")).toBeInTheDocument();
  });
});

describe("what the server refuses to a person who is not asking for their own", () => {
  it("shows a refusal's words and nothing else when the folder is not theirs", async () => {
    server.refuse.set(
      "POST /api/documents/folders/g1/sync",
      new ApiError("There is no such folder.", 404, { message: "There is no such folder." }),
    );
    await show();
    click("folder-sync");
    await settle();
    expect(text("folder-problem")).toBe("There is no such folder.");
  });
});
