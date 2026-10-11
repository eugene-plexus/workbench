import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import { ApiError, post } from "../lib/api";
import type { Message } from "../lib/types";
import { note } from "../test-memory";
import { MessageView } from "./MessageView";
import type { RememberProps } from "./RememberThis";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  post: vi.fn(),
}));

const message = (over: Partial<Message> = {}): Message => ({
  id: "m1",
  seq: 1,
  role: "user",
  content: "I use metric units",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "m",
  finish: null,
  createdAt: 0,
  finishedAt: 1,
  ...over,
});

const answer = (over: Partial<Message> = {}) =>
  message({ id: "a1", role: "assistant", content: "Metric it is.", ...over });

function show(
  shown: Message,
  over: { memory?: RememberProps | null; readOnly?: boolean; busy?: boolean } = {},
) {
  const memory: RememberProps | undefined =
    over.memory === null
      ? undefined
      : (over.memory ?? { off: null, onSaved: vi.fn(), onManage: vi.fn() });
  const view = render(
    <MessageView
      chatId="c1"
      message={shown}
      progress={null}
      busy={over.busy ?? false}
      readOnly={over.readOnly ?? false}
      onChanged={vi.fn()}
      memory={memory}
    />,
  );
  return { ...view, memory };
}

const button = (name = "Remember this message") => screen.queryByRole("button", { name });

beforeEach(() => {
  vi.mocked(post).mockReset();
});

describe("the button", () => {
  it("is in the actions of the person's message, beside Copy and Edit, named for what it keeps", () => {
    const { container } = show(message());
    const actions = container.querySelector(".msg-actions")!;
    const remember = screen.getByRole("button", { name: "Remember this message" });
    expect(actions).toContainElement(remember);
    expect(remember).toHaveClass("act");
    expect(remember).toHaveTextContent("Remember this");
    expect(remember).toHaveAttribute("data-testid", "remember-this");
    expect(remember.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    // It comes after the ones that were there.
    const names = Array.from(actions.querySelectorAll("button")).map((b) =>
      b.getAttribute("aria-label"),
    );
    expect(names).toEqual(["Copy your message", "Edit your message", "Remember this message"]);
  });

  it("is in the actions of an answer, too, and says it is the answer", () => {
    const { container } = show(answer());
    const actions = container.querySelector(".msg-actions")!;
    expect(actions).toContainElement(screen.getByRole("button", { name: "Remember this answer" }));
  });

  it("is not there where notes cannot be kept: no memory given, someone else's chat", () => {
    const first = show(message(), { memory: null });
    expect(button()).toBeNull();
    first.unmount();
    show(message(), { readOnly: true });
    expect(button()).toBeNull();
  });

  it("is not there for an answer still being written, or an empty one", () => {
    const writing = show(answer({ status: "running", content: "Metr" }));
    expect(button("Remember this answer")).toBeNull();
    writing.unmount();
    show(answer({ content: "" }));
    expect(button("Remember this answer")).toBeNull();
  });

  it("is not there while the person is editing their own words", () => {
    show(message());
    fireEvent.click(screen.getByRole("button", { name: "Edit your message" }));
    expect(button()).toBeNull();
  });

  it("is still there while another answer is being written: it does not need the chat to be idle", () => {
    show(message(), { busy: true });
    expect(button()).toBeInTheDocument();
  });

  it("keeps the answer, not the draft written before a search", async () => {
    vi.mocked(post).mockResolvedValue({ note: note(), created: true });
    show(answer({ content: "A first try.\n\nThe real answer.", answerFrom: 12, searches: 1 }));
    fireEvent.click(screen.getByRole("button", { name: "Remember this answer" }));
    await waitFor(() => expect(post).toHaveBeenCalledOnce());
    expect(vi.mocked(post).mock.calls[0]![1]).toMatchObject({ text: "The real answer." });
  });
});

describe("a message that fits", () => {
  it("is kept at once, as a note from a button, with where it came from, and the chat says so", async () => {
    const kept = note({ id: "k1", source: "button" });
    vi.mocked(post).mockResolvedValue({ note: kept, created: true });
    const { memory } = show(message());
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    await waitFor(() => expect(memory!.onSaved).toHaveBeenCalledWith(kept, true));
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/memory/notes", {
      text: "I use metric units",
      source: "button",
      chatId: "c1",
      messageId: "m1",
    });
    expect(screen.queryByTestId("remember-editor")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("passes on that the words were already kept", async () => {
    vi.mocked(post).mockResolvedValue({ note: note(), created: false });
    const { memory } = show(message());
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    await waitFor(() => expect(memory!.onSaved).toHaveBeenCalledWith(note(), false));
  });

  it("is one line: line breaks and runs of spacing become single spaces", async () => {
    vi.mocked(post).mockResolvedValue({ note: note(), created: true });
    show(message({ content: "I use metric units\n\nand 24 hour time" }));
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    await waitFor(() => expect(post).toHaveBeenCalledOnce());
    expect(vi.mocked(post).mock.calls[0]![1]).toMatchObject({
      text: "I use metric units and 24 hour time",
    });
  });

  it("counts characters as the server does: 200 emoji fit", async () => {
    vi.mocked(post).mockResolvedValue({ note: note(), created: true });
    show(message({ content: "🐦".repeat(200) }));
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    await waitFor(() => expect(post).toHaveBeenCalledOnce());
    expect(screen.queryByTestId("remember-editor")).toBeNull();
  });

  it("says in the server's words why it was not kept, with what a secret looks like", async () => {
    vi.mocked(post).mockRejectedValue(
      new ApiError("That looks like a password or key, so Workbench will not keep it", 422, {
        message: "That looks like a password or key, so Workbench will not keep it",
        reason: "secret",
        looksLike: "a card number",
      }),
    );
    const { memory } = show(message({ content: "My card is 4242 4242 4242 4242" }));
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    const refused = await screen.findByRole("alert");
    expect(refused).toHaveClass("notice", "notice--error");
    expect(refused).toHaveTextContent(
      "That looks like a password or key, so Workbench will not keep it. It looks like a card number.",
    );
    expect(memory!.onSaved).not.toHaveBeenCalled();
    // Pressing again tries again, and a success takes the notice away.
    vi.mocked(post).mockResolvedValue({ note: note(), created: true });
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("says so when the server could not be reached", async () => {
    vi.mocked(post).mockRejectedValue(new Error("Workbench answered 502."));
    show(message());
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Workbench answered 502.");
  });

  it("keeps the keyboard on the button: a held button loses it, and it is given back", async () => {
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(post).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    show(message());
    const remember = screen.getByRole("button", { name: "Remember this message" });
    remember.focus();
    fireEvent.click(remember);
    expect(remember).toBeDisabled();
    // What a browser does with the control that has focus when it is disabled:
    // focus is on the page. (jsdom keeps it where it was, so the page says so.)
    const page = vi
      .spyOn(Document.prototype, "activeElement", "get")
      .mockReturnValue(document.body);
    const focus = vi.spyOn(remember, "focus");
    finish({ note: note(), created: true });
    await waitFor(() => expect(focus).toHaveBeenCalledOnce());
    page.mockRestore();
  });

  it("does not take the keyboard from where the person went while the note was kept", async () => {
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(post).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    render(<input aria-label="Elsewhere" />);
    show(message());
    const remember = screen.getByRole("button", { name: "Remember this message" });
    fireEvent.click(remember);
    screen.getByLabelText("Elsewhere").focus();
    const focus = vi.spyOn(remember, "focus");
    finish({ note: note(), created: true });
    await waitFor(() => expect(remember).toBeEnabled());
    expect(focus).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Elsewhere")).toHaveFocus();
  });

  it("cannot be pressed twice while it is being kept", async () => {
    let finish: (value: unknown) => void = () => undefined;
    vi.mocked(post).mockReturnValue(new Promise((resolve) => (finish = resolve)));
    show(message());
    const remember = screen.getByRole("button", { name: "Remember this message" });
    fireEvent.click(remember);
    expect(remember).toBeDisabled();
    fireEvent.click(remember);
    finish({ note: note(), created: true });
    await waitFor(() => expect(remember).toBeEnabled());
    expect(post).toHaveBeenCalledOnce();
  });
});

describe("a message that is too long for a note", () => {
  const long = `${"I like to be told the reasons as well as the answers, ".repeat(6)}and nothing else.`;

  function openEditor() {
    show(message({ content: long }));
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    return screen.getByTestId("remember-editor");
  }

  it("is not cut quietly: it opens a box, says the limit and how long the message is", () => {
    const editor = openEditor();
    expect(post).not.toHaveBeenCalled();
    expect(editor).toHaveAccessibleName("Keep a note from this message");
    expect(within(editor).getByTestId("remember-help")).toHaveTextContent(
      `A note is one line of at most 240 characters, and this message is ${long.length}.`,
    );
    const box = within(editor).getByRole("textbox", { name: "Note to keep" });
    expect(box).toHaveFocus();
    expect(box).toHaveAttribute("dir", "auto");
    expect(box).toHaveClass("control");
    expect(box).toHaveAccessibleDescription(/at most 240 characters/);
  });

  it("starts as the first part of the message, cut at a word and short enough to keep", () => {
    const editor = openEditor();
    const box = within(editor).getByRole("textbox", {
      name: "Note to keep",
    }) as HTMLTextAreaElement;
    expect(long.startsWith(box.value)).toBe(true);
    expect(box.value.length).toBeLessThanOrEqual(240);
    expect(box.value.length).toBeGreaterThan(190);
    // It stops at a word, not in the middle of one.
    expect(box.value).toBe(box.value.trimEnd());
    expect(long[box.value.length]).toBe(" ");
    expect(within(editor).getByTestId("remember-count")).toHaveTextContent(
      `${box.value.length} of 240`,
    );
    expect(within(editor).getByRole("button", { name: "Keep note" })).toBeEnabled();
  });

  it("keeps what the person shortens it to, from a button, with the chat and the message", async () => {
    const kept = note({ id: "k2", text: "I like the reasons as well as the answers" });
    vi.mocked(post).mockResolvedValue({ note: kept, created: true });
    const { memory } = show(message({ content: long }));
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    const box = screen.getByRole("textbox", { name: "Note to keep" });
    fireEvent.change(box, { target: { value: "I like the reasons as well as the answers" } });
    expect(screen.getByTestId("remember-count")).toHaveTextContent("41 of 240");
    fireEvent.click(screen.getByRole("button", { name: "Keep note" }));
    await waitFor(() => expect(memory!.onSaved).toHaveBeenCalledWith(kept, true));
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/memory/notes", {
      text: "I like the reasons as well as the answers",
      source: "button",
      chatId: "c1",
      messageId: "m1",
    });
    // The box is gone and focus is back on the button that opened it.
    expect(screen.queryByTestId("remember-editor")).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remember this message" })).toHaveFocus(),
    );
  });

  it("will not keep more than 240, and says how far over it is", () => {
    const editor = openEditor();
    const box = within(editor).getByRole("textbox", { name: "Note to keep" });
    fireEvent.change(box, { target: { value: "x".repeat(260) } });
    expect(within(editor).getByTestId("remember-count")).toHaveTextContent("260 of 240");
    expect(within(editor).getByTestId("remember-count")).toHaveClass("text-error");
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(within(editor).getByRole("button", { name: "Keep note" })).toBeDisabled();
    fireEvent.submit(editor);
    expect(post).not.toHaveBeenCalled();
  });

  it("will not keep nothing", () => {
    const editor = openEditor();
    fireEvent.change(within(editor).getByRole("textbox", { name: "Note to keep" }), {
      target: { value: "   " },
    });
    expect(within(editor).getByRole("button", { name: "Keep note" })).toBeDisabled();
  });

  it("keeps nothing on Cancel or Escape, and puts focus back on the button", async () => {
    const editor = openEditor();
    fireEvent.click(within(editor).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByTestId("remember-editor")).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remember this message" })).toHaveFocus(),
    );
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Note to keep" }), { key: "Escape" });
    expect(screen.queryByTestId("remember-editor")).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("says why when the server refuses what was shortened, and keeps the box open for another try", async () => {
    vi.mocked(post).mockRejectedValue(
      new ApiError("That looks like a password or key, so Workbench will not keep it", 422, {
        message: "That looks like a password or key, so Workbench will not keep it",
        reason: "secret",
        looksLike: "an API key",
      }),
    );
    openEditor();
    fireEvent.click(screen.getByRole("button", { name: "Keep note" }));
    const refused = await screen.findByRole("alert");
    expect(refused).toHaveTextContent("It looks like an API key.");
    expect(screen.getByTestId("remember-editor")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep note" })).toBeEnabled();
  });

  it("is the same for an answer", () => {
    show(answer({ content: long }));
    fireEvent.click(screen.getByRole("button", { name: "Remember this answer" }));
    expect(screen.getByTestId("remember-editor")).toHaveAccessibleName(
      "Keep a note from this answer",
    );
    expect(screen.getByTestId("remember-help")).toHaveTextContent("and this answer is");
  });
});

describe("when memory is off for the person", () => {
  it("keeps nothing and says so in the server's words, with the way to turn it on", () => {
    const memory: RememberProps = { off: "you", onSaved: vi.fn(), onManage: vi.fn() };
    show(message(), { memory });
    fireEvent.click(screen.getByRole("button", { name: "Remember this message" }));
    expect(post).not.toHaveBeenCalled();
    const said = screen.getByTestId("remember-off");
    expect(said).toHaveTextContent(
      "Memory is off for you, so nothing was kept. Turn it on in Memory.",
    );
    fireEvent.click(within(said).getByRole("button", { name: "Open Memory" }));
    expect(memory.onManage).toHaveBeenCalledOnce();
    expect(memory.onSaved).not.toHaveBeenCalled();
  });
});
