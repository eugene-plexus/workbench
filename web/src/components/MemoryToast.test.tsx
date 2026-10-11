import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, del, post } from "../lib/api";
import type { MemoryCommand } from "../lib/types";
import { note } from "../test-memory";
import { MemoryToast, outcomeOf, type ToastOutcome } from "./MemoryToast";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  del: vi.fn(),
  post: vi.fn(),
}));

const kept = note({ id: "n1", text: "my code word is HELIOTROPE" });

function show(outcome: ToastOutcome, over: Partial<Parameters<typeof MemoryToast>[0]> = {}) {
  const calls = { onManage: vi.fn(), onChanged: vi.fn(), onClose: vi.fn() };
  const view = render(<MemoryToast outcome={outcome} {...calls} {...over} />);
  return { ...view, ...calls };
}

const remembered: ToastOutcome = { kind: "remembered", note: kept, created: true };

beforeEach(() => {
  vi.mocked(del).mockReset().mockResolvedValue(undefined);
  vi.mocked(post).mockReset();
});

afterEach(() => vi.useRealTimers());

describe("after Remember that ...", () => {
  it("says what was kept, in its own words, with Undo and Manage beside it", () => {
    show(remembered);
    const toast = screen.getByTestId("memory-toast");
    expect(toast).toHaveAttribute("role", "status");
    expect(toast).toHaveClass("notice", "notice--ok", "notice--float");
    expect(toast).toHaveTextContent("Remembered: “my code word is HELIOTROPE”");
    expect(toast.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // The note's words take their direction from their own letters.
    expect(screen.getByText(/my code word is HELIOTROPE/)).toHaveAttribute("dir", "auto");
    expect(within(toast).getByRole("button", { name: "Undo" })).toHaveClass("btn", "btn-quiet");
    expect(within(toast).getByRole("button", { name: "Manage" })).toHaveClass("btn", "btn-quiet");
    expect(within(toast).getByRole("button", { name: "Dismiss" })).toHaveClass("icon-btn");
  });

  it("closes on Escape, and hands the keyboard back to the message box it was writing in", async () => {
    render(<textarea id="message-composer" aria-label="Your message" />);
    const { onClose } = show(remembered);
    screen.getByRole("button", { name: "Undo" }).focus();
    fireEvent.keyDown(screen.getByRole("button", { name: "Undo" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.getByLabelText("Your message")).toHaveFocus());
  });

  it("does not take the keyboard from where the person is when Dismiss is pressed by a pointer", async () => {
    render(<textarea id="message-composer" aria-label="Your message" />);
    const { onClose } = show(remembered);
    const elsewhere = document.createElement("input");
    document.body.appendChild(elsewhere);
    elsewhere.focus();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onClose).toHaveBeenCalledOnce();
    await Promise.resolve();
    expect(elsewhere).toHaveFocus();
    elsewhere.remove();
  });

  it("opens the Memory page from Manage, and closes from Dismiss", () => {
    const { onManage, onClose } = show(remembered);
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect(onManage).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("takes the note back on Undo: it is deleted for real, and the toast says so", async () => {
    const { onChanged } = show(remembered);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await screen.findByText("Undone.");
    expect(del).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/n1");
    expect(screen.getByTestId("memory-toast")).toHaveTextContent(
      "Undone. “my code word is HELIOTROPE” is not remembered.",
    );
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    // The chat's chip is stale now.
    expect(onChanged).toHaveBeenCalledOnce();
  });

  it("keeps the keyboard in the toast after Undo: that button is gone, so the first one left has it", async () => {
    show(remembered);
    const undo = screen.getByRole("button", { name: "Undo" });
    undo.focus();
    fireEvent.click(undo);
    // What a browser does with the control that has focus when it is disabled:
    // focus is on the page. (jsdom keeps it where it was, so the page says so.)
    const page = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(screen.getByRole("button", { name: "Dismiss" }), "focus");
    await screen.findByText("Undone.");
    await waitFor(() => expect(focus).toHaveBeenCalledOnce());
    page.mockRestore();
  });

  it("does not take the keyboard into the toast when it was not there", async () => {
    show(remembered);
    const elsewhere = document.createElement("input");
    document.body.appendChild(elsewhere);
    elsewhere.focus();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    const focus = vi.spyOn(screen.getByRole("button", { name: "Dismiss" }), "focus");
    await screen.findByText("Undone.");
    expect(focus).not.toHaveBeenCalled();
    expect(elsewhere).toHaveFocus();
    elsewhere.remove();
  });

  it("does not take the keyboard into the toast when it was not there: nothing had it, and a pointer pressed Undo", async () => {
    show(remembered);
    // Focus is on the page (a click on its background, or a browser that does not
    // focus a button a pointer presses): the keyboard was nowhere, so it stays there.
    (document.activeElement as HTMLElement | null)?.blur();
    expect(document.body).toHaveFocus();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    const focus = vi.spyOn(screen.getByRole("button", { name: "Dismiss" }), "focus");
    await screen.findByText("Undone.");
    expect(focus).not.toHaveBeenCalled();
    expect(document.body).toHaveFocus();
  });

  it("says so in the server's words when Undo is refused, and stays", async () => {
    vi.mocked(del).mockRejectedValue(new Error("There is no such note."));
    const { onChanged } = show(remembered);
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("There is no such note.");
    expect(screen.getByRole("button", { name: "Undo" })).toBeEnabled();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("offers no Undo for words that were already kept: there is nothing new to take back", () => {
    show({ kind: "remembered", note: kept, created: false });
    expect(screen.getByTestId("memory-toast")).toHaveTextContent(
      "Already remembered: “my code word is HELIOTROPE”",
    );
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    expect(screen.getByRole("button", { name: "Manage" })).toBeInTheDocument();
  });
});

describe("how long it stays", () => {
  it("is ten seconds for something that can be undone, and then it closes itself", () => {
    vi.useFakeTimers();
    const { onClose } = show(remembered);
    act(() => void vi.advanceTimersByTime(9_900));
    expect(onClose).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(200));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("waits while the pointer is on it, and starts the ten seconds over when it leaves", () => {
    vi.useFakeTimers();
    const { onClose } = show(remembered);
    const toast = screen.getByTestId("memory-toast");
    act(() => void vi.advanceTimersByTime(8_000));
    fireEvent.mouseEnter(toast);
    act(() => void vi.advanceTimersByTime(60_000));
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseLeave(toast);
    act(() => void vi.advanceTimersByTime(9_900));
    expect(onClose).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(200));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("waits while the keyboard is on it", () => {
    vi.useFakeTimers();
    const { onClose } = show(remembered);
    fireEvent.focus(screen.getByRole("button", { name: "Undo" }));
    act(() => void vi.advanceTimersByTime(60_000));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("is not started over by a parent that draws again, as it does with every piece of an answer", () => {
    vi.useFakeTimers();
    const first = vi.fn();
    const second = vi.fn();
    const { rerender } = render(
      <MemoryToast outcome={remembered} onManage={vi.fn()} onChanged={vi.fn()} onClose={first} />,
    );
    act(() => void vi.advanceTimersByTime(6_000));
    rerender(
      <MemoryToast outcome={remembered} onManage={vi.fn()} onChanged={vi.fn()} onClose={second} />,
    );
    act(() => void vi.advanceTimersByTime(4_100));
    // The clock kept running, and the latest way to close is the one it used.
    expect(second).toHaveBeenCalledOnce();
    expect(first).not.toHaveBeenCalled();
  });

  it("is four seconds for a note taken back", async () => {
    vi.useFakeTimers();
    const { onClose } = show(remembered);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    });
    expect(screen.getByText("Undone.")).toBeInTheDocument();
    act(() => void vi.advanceTimersByTime(3_900));
    expect(onClose).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(200));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("stays until it is dismissed when it says why nothing was kept, or asks which note", () => {
    vi.useFakeTimers();
    const refused = show({
      kind: "refused",
      refusal: { message: "Write what to remember.", reason: "empty" },
      messageSent: true,
    });
    act(() => void vi.advanceTimersByTime(120_000));
    expect(refused.onClose).not.toHaveBeenCalled();
    refused.unmount();
    const asking = show({ kind: "forget", words: "code word", candidates: [kept] });
    act(() => void vi.advanceTimersByTime(120_000));
    expect(asking.onClose).not.toHaveBeenCalled();
  });
});

describe("when words were not kept", () => {
  it("says why in plain words, and that the message was still sent", () => {
    show({
      kind: "refused",
      refusal: {
        message: "A note is at most 240 characters; this one is 312.",
        reason: "too_long",
      },
      messageSent: true,
    });
    const toast = screen.getByTestId("memory-toast");
    expect(toast).toHaveAttribute("role", "alert");
    expect(toast).toHaveClass("notice--warn");
    expect(toast).toHaveTextContent(
      "Not remembered. A note is at most 240 characters; this one is 312. Your message was still sent.",
    );
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
  });

  it("says what a secret looks like, and never shows the secret", () => {
    show({
      kind: "refused",
      refusal: {
        message: "That looks like a password or key, so Workbench will not keep it",
        reason: "secret",
        looksLike: "a card number",
      },
      messageSent: true,
    });
    expect(screen.getByTestId("memory-toast")).toHaveTextContent(
      "That looks like a password or key, so Workbench will not keep it. It looks like a card number.",
    );
  });

  it("does not say the message was sent when it came from a message's own button", () => {
    show({ kind: "refused", refusal: { message: "Memory is off." }, messageSent: false });
    expect(screen.getByTestId("memory-toast")).not.toHaveTextContent("still sent");
  });

  it("opens the Memory page from Manage", () => {
    const { onManage } = show({
      kind: "refused",
      refusal: { message: "You have 300 notes, the limit.", reason: "full", limit: 300 },
      messageSent: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect(onManage).toHaveBeenCalledOnce();
  });
});

describe("after forget ...", () => {
  const candidates = [
    note({ id: "a", text: "my code word is HELIOTROPE" }),
    note({ id: "b", text: "my other code word is ANISE", kind: "project" }),
  ];
  const asking: ToastOutcome = { kind: "forget", words: "code word", candidates };

  it("lists the notes it might mean, and deletes none of them on its own", () => {
    show(asking);
    const toast = screen.getByTestId("memory-toast");
    expect(toast).toHaveAttribute("role", "status");
    expect(toast).toHaveTextContent("Delete one of these notes?");
    const rows = within(toast).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("my code word is HELIOTROPE");
    expect(within(rows[0]!).getByText("my code word is HELIOTROPE")).toHaveAttribute("dir", "auto");
    expect(del).not.toHaveBeenCalled();
    expect(
      within(toast).getByRole("button", { name: "Delete note: my code word is HELIOTROPE" }),
    ).toHaveClass("btn-danger");
  });

  it("deletes only the one that was picked, says so, and offers to keep it again", async () => {
    const { onChanged } = show(asking);
    fireEvent.click(
      screen.getByRole("button", { name: "Delete note: my other code word is ANISE" }),
    );
    await screen.findByText("Deleted:");
    expect(del).toHaveBeenCalledExactlyOnceWith("/api/memory/notes/b");
    expect(screen.getByTestId("memory-toast")).toHaveTextContent(
      "Deleted: “my other code word is ANISE”",
    );
    expect(onChanged).toHaveBeenCalledOnce();
    // The other one is not mentioned, let alone deleted.
    expect(screen.queryByText(/HELIOTROPE/)).toBeNull();

    // Undo keeps the same words again, as a new note, and says so like any other.
    vi.mocked(post).mockResolvedValue({
      note: note({ id: "c", text: "my other code word is ANISE", kind: "project" }),
      created: true,
    });
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await screen.findByText("Remembered:");
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/memory/notes", {
      text: "my other code word is ANISE",
      kind: "project",
      pinned: false,
    });
    expect(onChanged).toHaveBeenCalledTimes(2);
  });

  it("says so when no note matches, with a way to the page", () => {
    const { onManage } = show({ kind: "forget", words: "umbrella", candidates: [] });
    expect(screen.getByTestId("memory-toast")).toHaveTextContent("No note matches “umbrella”.");
    expect(screen.queryByRole("listitem")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    expect(onManage).toHaveBeenCalledOnce();
  });

  it("says why a deletion was refused, and keeps the list", async () => {
    vi.mocked(del).mockRejectedValue(new ApiError("There is no such note.", 404));
    show(asking);
    fireEvent.click(
      screen.getByRole("button", { name: "Delete note: my code word is HELIOTROPE" }),
    );
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("There is no such note."),
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});

describe("what a message that began with a command did", () => {
  it("is nothing for a message that had none", () => {
    expect(outcomeOf(undefined)).toBeNull();
  });

  it("is a note kept, or the same words that were already kept", () => {
    const command: MemoryCommand = { command: "remember", remembered: kept, created: false };
    expect(outcomeOf(command)).toEqual({ kind: "remembered", note: kept, created: false });
  });

  it("is words refused, in a message that was sent anyway", () => {
    const refused = { message: "Write what to remember.", reason: "empty" as const };
    expect(outcomeOf({ command: "remember", refused })).toEqual({
      kind: "refused",
      refusal: refused,
      messageSent: true,
    });
  });

  it("is notes to choose from", () => {
    expect(outcomeOf({ command: "forget", words: "code word", candidates: [kept] })).toEqual({
      kind: "forget",
      words: "code word",
      candidates: [kept],
    });
  });
});
