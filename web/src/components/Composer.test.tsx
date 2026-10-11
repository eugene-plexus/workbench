import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ApiError, del, patch, post } from "../lib/api";
import type { Chat, Me, Message, Models } from "../lib/types";
import { Composer, type Pending } from "./Composer";
import { MessageView } from "./MessageView";

vi.mock("../lib/api", async (actual) => ({
  ...(await actual<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
}));

const me: Me = { sub: "ada", name: "Ada", owner: false, ownerReadsChats: false, username: null };
const chat: Chat = {
  id: "one",
  title: "Build a birdhouse",
  model: "local",
  search: false,
  settings: {},
  createdAt: 1,
  updatedAt: 1,
  running: false,
  readOnly: false,
};
const models: Models = {
  models: [
    {
      id: "local",
      ready: true,
      onDemand: false,
      imageInput: true,
      fileInput: true,
      audioInput: true,
      webSearch: false,
      contextLength: 4096,
    },
  ],
  webSearch: { available: false, reason: "No search account." },
};
const sent: Message = {
  id: "u",
  seq: 1,
  role: "user",
  content: "Hello",
  reasoning: "",
  attachments: [],
  status: "done",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "local",
  finish: null,
  createdAt: 1,
  finishedAt: null,
};
const dot: Pending = { id: "f1", name: "dot.png", mediaType: "image/png" };

function composer(initialAttachments?: Pending[]) {
  return (
    <Composer
      chat={chat}
      initialAttachments={initialAttachments}
      me={me}
      models={models}
      modelsError={null}
      running={false}
      onSent={vi.fn()}
      onChat={vi.fn()}
    />
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  vi.clearAllMocks();
});

it("names the edit box apart from the composer (workbench#2)", () => {
  render(
    <>
      <MessageView
        chatId="one"
        message={sent}
        progress={null}
        busy={false}
        readOnly={false}
        onChanged={vi.fn()}
      />
      {composer()}
    </>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit your message" }));
  const mine = screen.getAllByRole("textbox", { name: "Your message" });
  const edit = screen.getAllByRole("textbox", { name: "Edit your message" });
  expect(mine).toHaveLength(1);
  expect(edit).toHaveLength(1);
  expect(mine[0]).toHaveAttribute("data-testid", "composer");
  expect(edit[0]).toHaveValue("Hello");
});

describe("Remove on an attachment not yet sent (workbench#3)", () => {
  it("deletes the upload, then drops the chip", async () => {
    vi.mocked(del).mockResolvedValue(undefined);
    render(composer([dot]));
    fireEvent.click(screen.getByRole("button", { name: "Remove dot.png" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Remove dot.png" })).not.toBeInTheDocument(),
    );
    expect(del).toHaveBeenCalledWith("/api/chats/one/files/f1");
  });

  it("keeps the chip and says why when the delete is refused", async () => {
    vi.mocked(del).mockRejectedValue(
      new ApiError("This file was sent with a message, so it stays with the chat.", 409),
    );
    render(composer([dot]));
    fireEvent.click(screen.getByRole("button", { name: "Remove dot.png" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "dot.png was not removed: This file was sent with a message, so it stays with the chat.",
    );
    expect(screen.getByRole("button", { name: "Remove dot.png" })).toBeEnabled();
  });

  it("holds Send while the delete is under way", async () => {
    vi.mocked(del).mockReturnValue(new Promise(() => undefined));
    render(composer([dot]));
    expect(screen.getByTestId("send")).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Remove dot.png" }));
    await waitFor(() => expect(screen.getByTestId("send")).toBeDisabled());
    expect(screen.getByRole("button", { name: "Remove dot.png" })).toBeDisabled();
  });
});

it("keeps the placeholder short and the keys in the described-by hint", () => {
  render(composer());
  const box = screen.getByTestId("composer");
  expect(box).toHaveAttribute("placeholder", "Ask anything");
  fireEvent.change(box, { target: { value: "hi" } });
  expect(box).toHaveAccessibleDescription(/Enter sends; Shift\+Enter adds a line/);
});

describe("opening a chat", () => {
  const screenIs = (coarse: boolean) => {
    window.matchMedia = vi.fn((query: string) => ({
      matches: coarse && query === "(pointer: coarse)",
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
  };
  afterEach(() => {
    delete (window as { matchMedia?: unknown }).matchMedia;
  });

  it("renders and takes focus where the browser has no matchMedia (jsdom, an old browser)", () => {
    expect(typeof window.matchMedia).not.toBe("function");
    render(composer());
    expect(screen.getByTestId("composer")).toHaveFocus();
  });

  it("takes focus under a mouse", () => {
    screenIs(false);
    render(composer());
    expect(screen.getByTestId("composer")).toHaveFocus();
  });

  it("does not take focus under a finger, so a phone's keyboard does not rise over the chat", () => {
    screenIs(true);
    render(composer());
    expect(screen.getByTestId("composer")).not.toHaveFocus();
  });
});

describe("the field", () => {
  afterEach(() => {
    document.documentElement.style.fontSize = "";
  });

  it("is at the reading size, 4.5 rem or more, and dir follows what is typed", () => {
    render(composer());
    const box = screen.getByTestId("composer");
    expect(box).toHaveClass("control", "text-read", "min-h-[4.5rem]");
    expect(box).toHaveAttribute("dir", "auto");
    expect(box).toHaveAttribute("rows", "2");
  });

  it("is 4.5 of the person's rems tall, not 72 px: a larger text setting makes it larger", () => {
    render(composer());
    // jsdom has no root size: 16 px.
    expect(screen.getByTestId("composer").style.height).toBe("72px");
    cleanup();
    document.documentElement.style.fontSize = "20px";
    render(composer());
    expect(screen.getByTestId("composer").style.height).toBe("90px");
  });
});

describe("the hint and the draft", () => {
  const hint = () => document.getElementById("composer-hint")!;
  const keys = "Enter sends; Shift+Enter adds a line. Drop files here or paste an image.";

  it("says the keys, which leave a phone, and nothing of a draft until there is one", () => {
    render(composer());
    expect(hint()).toHaveClass("max-sm:sr-only");
    expect(hint()).toHaveTextContent(keys);
    expect(hint().textContent).not.toContain("draft");
    expect(screen.queryByTestId("draft-lost")).toBeNull();
  });

  it("says a draft is kept, in the same hint", () => {
    render(composer());
    fireEvent.change(screen.getByTestId("composer"), { target: { value: "hi" } });
    expect(hint()).toHaveTextContent("Text draft kept in this tab until you send or sign out.");
    expect(screen.queryByTestId("draft-lost")).toBeNull();
  });

  it("says a draft that cannot be kept in a warning that is never hidden, on any screen", () => {
    const blocked = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    try {
      render(composer());
      const box = screen.getByTestId("composer");
      fireEvent.change(box, { target: { value: "hi" } });
      const warning = screen.getByTestId("draft-lost");
      expect(warning).toHaveTextContent(
        "This browser cannot save your draft after you leave this chat.",
      );
      expect(warning).toHaveClass("text-warn");
      expect(warning.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
      // Not the hint, which leaves a phone and goes in a short window.
      expect(warning.closest("#composer-hint")).toBeNull();
      expect(warning.className).not.toContain("sr-only");
      expect(hint().textContent).not.toContain("cannot save");
      // Still the field's description, with the keys.
      expect(box).toHaveAccessibleDescription(/Enter sends[\s\S]*cannot save your draft/);
    } finally {
      blocked.mockRestore();
    }
  });
});

describe("the search switch", () => {
  const searching: Models = {
    models: [{ ...models.models[0]!, webSearch: true }],
    webSearch: { available: true, reason: null },
  };
  const withSearch = (on: boolean) =>
    render(
      <Composer
        chat={{ ...chat, search: on }}
        me={me}
        models={searching}
        modelsError={null}
        running={false}
        onSent={vi.fn()}
        onChat={vi.fn()}
      />,
    );

  it("says where the words go, on the screen, while it is on", () => {
    withSearch(true);
    const note = screen.getByTestId("search-privacy");
    expect(note).toHaveTextContent("Search is on. The words searched for go to the internet.");
    expect(note.className).not.toContain("sr-only");
    expect(note.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId("search-switch")).toHaveAccessibleDescription(/go to the internet/);
  });

  it("says nothing of the internet while it is off", () => {
    withSearch(false);
    expect(screen.queryByTestId("search-privacy")).toBeNull();
    expect(screen.getByTestId("search-switch")).not.toHaveAttribute("aria-describedby");
  });

  it("keeps the sentence in the tooltip too", () => {
    withSearch(true);
    expect(screen.getByTestId("search-switch").closest("label")).toHaveAttribute(
      "title",
      "Search the web before answering. The words searched for go to the internet.",
    );
  });

  it("says why it is off, with a glyph and the reason it was given", () => {
    render(composer());
    const off = screen.getByTestId("search-off");
    expect(off).toHaveTextContent("Search is off: No search account.");
    expect(off.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByTestId("search-switch")).toBeDisabled();
  });
});

describe("the controls", () => {
  it("are two groups in one wrapping row, so a label never strands from its switch", () => {
    render(composer());
    const first = screen.getByRole("button", { name: "Attach" }).parentElement!;
    const second = screen.getByTestId("search-switch").closest("div")!;
    expect(first).toContainElement(screen.getByTestId("model-picker"));
    expect(first).toHaveClass("min-w-0", "flex-[1_1_16rem]");
    expect(second).toHaveClass("ml-auto");
    expect(second).toContainElement(screen.getByTestId("send"));
    expect(second).toContainElement(screen.getByText("Toolbox · Tools:"));
    expect(first.parentElement).toBe(second.parentElement);
    expect(first.parentElement).toHaveClass("flex-wrap");
  });

  it("let the model picker shrink to a phone, and stop at 18 rem on anything wider", () => {
    render(composer());
    expect(screen.getByTestId("model-picker")).toHaveClass(
      "control",
      "control--sm",
      "min-w-0",
      "max-w-full",
      "sm:max-w-72",
    );
  });

  it("make Stop and Send the same kind of button, so the composer is as tall running as idle", () => {
    const { rerender } = render(composer());
    expect(screen.getByTestId("send")).toHaveClass("btn", "btn-primary");
    expect(screen.getByRole("button", { name: "Attach" })).toHaveClass("btn");
    rerender(
      <Composer
        chat={chat}
        me={me}
        models={models}
        modelsError={null}
        running
        onSent={vi.fn()}
        onChat={vi.fn()}
      />,
    );
    expect(screen.getByTestId("stop")).toHaveClass("btn");
    expect(screen.getByTestId("stop")).not.toHaveClass("btn-primary");
  });

  it("name the tools with a glyph that is only a glyph", () => {
    render(composer());
    const label = screen.getByText("Toolbox · Tools:");
    expect(label.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
    expect(label).toHaveAttribute("title", "Tools the model can use");
  });

  it("take an attached file back with an icon button that keeps its name", () => {
    render(composer([dot]));
    expect(screen.getByRole("button", { name: "Remove dot.png" })).toHaveClass("icon-btn");
    const chip = screen.getByLabelText("Attached").firstElementChild!;
    // A file name with no break in it wraps inside the chip, never widening the row.
    expect(chip).toHaveClass("max-w-full", "min-w-0", "wrap-anywhere");
  });
});

describe("what is said around the field", () => {
  it("says a file is being dropped in a status of its own, with its glyph", () => {
    render(composer());
    fireEvent.dragEnter(screen.getByTestId("composer-area"), {
      dataTransfer: { types: ["Files"] },
    });
    const drop = screen.getByRole("status");
    expect(drop).toHaveClass("notice");
    expect(drop).toHaveTextContent("Drop files to attach them.");
    expect(drop.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("says why a send failed in an error notice with the words the server gave", async () => {
    vi.mocked(post).mockRejectedValue(new Error("The model is not loaded."));
    render(composer());
    fireEvent.change(screen.getByTestId("composer"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("send"));
    const failure = await screen.findByText("The model is not loaded.");
    expect(failure).toHaveAttribute("role", "alert");
    expect(failure).toHaveClass("notice", "notice--error");
    expect(failure.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("a new chat, and the model its picker shows", () => {
  const fresh: Chat = { ...chat, model: null };

  function show(over: { chat?: Chat; models?: Models | null; onChat?: () => void } = {}) {
    const onChat = over.onChat ?? vi.fn();
    const props = (models: Models | null, shown: Chat) => (
      <Composer
        chat={shown}
        me={me}
        models={models}
        modelsError={null}
        running={false}
        onSent={vi.fn()}
        onChat={onChat}
      />
    );
    const view = render(
      props(over.models === undefined ? models : over.models, over.chat ?? fresh),
    );
    return { ...view, onChat, props };
  }

  it("tells the chat, once, which model the picker shows, so what asks the server agrees with the screen", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, model: "local" });
    const { onChat } = show();
    await waitFor(() => expect(onChat).toHaveBeenCalledWith({ ...chat, model: "local" }));
    expect(patch).toHaveBeenCalledExactlyOnceWith("/api/chats/one", { model: "local" });
    expect(screen.getByTestId("model-picker")).toHaveValue("local");
  });

  it("does not make that the person's own pick: nothing is remembered until they choose", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, model: "local" });
    const { onChat } = show();
    await waitFor(() => expect(onChat).toHaveBeenCalled());
    expect(localStorage.getItem("workbench-model:ada")).toBeNull();
  });

  it("leaves a chat that has a model alone", () => {
    show({ chat });
    expect(patch).not.toHaveBeenCalled();
  });

  it("waits for the models to be known", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, model: "local" });
    const { rerender, props } = show({ models: null });
    expect(patch).not.toHaveBeenCalled();
    rerender(props(models, fresh));
    await waitFor(() => expect(patch).toHaveBeenCalledOnce());
  });

  it("asks once, however often the models are listed again", async () => {
    vi.mocked(patch).mockResolvedValue({ ...chat, model: "local" });
    const { rerender, props } = show();
    await waitFor(() => expect(patch).toHaveBeenCalledOnce());
    rerender(props({ ...models, models: models.models.map((m) => ({ ...m })) }, fresh));
    rerender(props({ ...models, models: models.models.map((m) => ({ ...m })) }, fresh));
    expect(patch).toHaveBeenCalledOnce();
  });

  it("says nothing when it is refused: the first message carries the model anyway", async () => {
    vi.mocked(patch).mockRejectedValue(new Error("Workbench answered 502."));
    const { onChat } = show();
    await waitFor(() => expect(patch).toHaveBeenCalledOnce());
    expect(onChat).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("a message that begins with a command", () => {
  const typed = async (words: string, onSent: () => void) => {
    render(
      <Composer
        chat={chat}
        me={me}
        models={models}
        modelsError={null}
        running={false}
        onSent={onSent}
        onChat={vi.fn()}
      />,
    );
    fireEvent.change(screen.getByTestId("composer"), { target: { value: words } });
    fireEvent.click(screen.getByTestId("send"));
    await waitFor(() => expect(onSent).toHaveBeenCalled());
  };

  it("passes on, with the message, what it did to the person's notes", async () => {
    const memory = {
      command: "remember",
      remembered: { id: "n1", text: "I use metric units" },
      created: true,
    };
    vi.mocked(post).mockResolvedValue({ user: sent, answer: sent, memory });
    const onSent = vi.fn();
    await typed("Remember that I use metric units", onSent);
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/chats/one/messages", {
      content: "Remember that I use metric units",
      attachments: [],
      model: "local",
      search: false,
    });
    expect(onSent).toHaveBeenCalledWith(expect.objectContaining({ id: "u" }), sent, memory);
  });

  it("passes nothing for a message that was only a message", async () => {
    vi.mocked(post).mockResolvedValue({ user: sent, answer: sent });
    const onSent = vi.fn();
    await typed("Hello", onSent);
    expect(onSent).toHaveBeenCalledWith(expect.objectContaining({ id: "u" }), sent, undefined);
  });
});
