/**
 * "Search it instead" on a file attached to a chat (MR4): a file that is too long to be
 * read whole may be kept as a document of this chat and searched for each question. What
 * a person sees from the offer to the message that carries it, against a fake server that
 * answers as the real one does (src/eugene_plexus_workbench/documents_api.py).
 */

import { act, fireEvent, render, screen, within } from "@testing-library/react";

import { ApiError, api, del, patch, post, put } from "../lib/api";
import type { Chat, ChatFile, ChatFiles, Me, Message, Model, Models } from "../lib/types";
import { FIRST_POLL_MS, POLL_MS } from "../lib/useChatFiles";
import {
  choose,
  fileSearch,
  fresh,
  listing,
  MODEL,
  preparing,
  scan,
  searched,
  tooLong,
} from "../test-documents";
import { Composer } from "./Composer";

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
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
const model = (over: Partial<Model> = {}): Model => ({
  id: "local",
  ready: true,
  onDemand: false,
  imageInput: true,
  fileInput: false,
  audioInput: false,
  webSearch: false,
  contextLength: 32768,
  ...over,
});
const models: Models = {
  models: [model()],
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

let listed: ChatFile[];
let uploads: ChatFile[];

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.mocked(api)
    .mockReset()
    .mockImplementation((async (_path: string, init?: RequestInit) => {
      if (init?.method === "POST") return uploads.shift();
      return listing(listed, { model: MODEL }) as ChatFiles;
    }) as never);
  vi.mocked(del).mockReset().mockResolvedValue(undefined);
  vi.mocked(patch).mockReset().mockResolvedValue(chat);
  vi.mocked(post).mockReset().mockResolvedValue({ user: sent, answer: sent });
  vi.mocked(put).mockReset();
  listed = [];
  uploads = [];
});
afterEach(() => vi.useRealTimers());

const wait = (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

function composer() {
  const onSent = vi.fn();
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
  return { onSent };
}

const send = () => screen.getByTestId("send");
const chip = () => screen.getByTestId("document-chip");

/** A long file is attached, and the server has read it. */
async function longFile(found: ChatFile = choose()) {
  composer();
  uploads.push(fresh({ id: found.id, name: found.name, mediaType: found.mediaType }));
  fireEvent.drop(screen.getByTestId("composer-area"), {
    dataTransfer: { files: [new File(["x"], found.name, { type: "application/octet-stream" })] },
  });
  await wait(0);
  listed = [found];
  await wait(FIRST_POLL_MS);
}

/** The server keeps the choice and answers with how the file stands now. */
function keeps(after: ChatFile) {
  vi.mocked(put).mockImplementation(async () => {
    listed = [after];
    return after;
  });
}

describe("the offer", () => {
  it("is the last of the buttons on a file that is longer than is read at once: read it all, the first part, or search it", async () => {
    await longFile();
    expect(chip()).toHaveAttribute("data-state", "choose");
    const offers = within(chip())
      .getAllByRole("button")
      .map((b) => b.getAttribute("data-testid"))
      .filter((id) => id?.startsWith("document-offer-"));
    expect(offers).toEqual(["document-offer-all", "document-offer-first", "document-offer-search"]);
    expect(within(chip()).getByRole("button", { name: "Search it instead" })).toBeInTheDocument();
  });

  it("is offered beside the part that fits, on a file too long for the model's window", async () => {
    await longFile(tooLong());
    expect(chip()).toHaveAttribute("data-state", "too_long");
    const offers = within(chip())
      .getAllByRole("button")
      .map((b) => b.textContent?.trim());
    expect(offers).toContain("Read the first 1,614 tokens (pages 1 to 2 of 30)");
    expect(offers).toContain("Search it instead");
    expect(screen.getByTestId("send-held")).toHaveTextContent(
      "Choose how much of long.pdf to read, search it instead, or remove it.",
    );
  });

  it("is a file's own button, wrapped on a phone like the others, and held while a choice is kept", async () => {
    await longFile();
    expect(screen.getByTestId("document-offer-search")).toHaveClass("btn", "btn-wrap");
    let finish: (file: ChatFile) => void = () => undefined;
    vi.mocked(put).mockReturnValue(new Promise<ChatFile>((resolve) => (finish = resolve)) as never);
    fireEvent.click(screen.getByTestId("document-offer-search"));
    expect(screen.getByTestId("document-offer-search")).toBeDisabled();
    expect(screen.getByTestId("document-offer-all")).toBeDisabled();
    await act(async () => finish(searched()));
  });
});

describe("choosing to search it", () => {
  it("keeps the choice with the file for the model that is picked, and the card says how it will be used", async () => {
    await longFile();
    keeps(searched());
    fireEvent.click(screen.getByTestId("document-offer-search"));
    await wait(0);
    expect(put).toHaveBeenCalledExactlyOnceWith("/api/chats/one/files/f2/reading?model=local", {
      mode: "search",
    });
    expect(chip()).toHaveAttribute("data-state", "search");
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "Will be searched for each question, not read whole. 12 passages to search.",
    );
    expect(screen.getByTestId("document-choice")).toHaveTextContent(
      "Your choice: search it instead. It is kept for this chat only.",
    );
    // Nothing more to choose: the offers are gone and the person may choose again.
    expect(screen.queryByTestId("document-offer-search")).toBeNull();
    expect(screen.getByTestId("document-choose-again")).toBeInTheDocument();
  });

  it("lets Send go, and sends the file with the message as any other", async () => {
    const { onSent } = await (async () => {
      const shown = composer();
      uploads.push(fresh({ id: "f2", name: "log.txt", mediaType: "text/plain" }));
      fireEvent.drop(screen.getByTestId("composer-area"), {
        dataTransfer: { files: [new File(["x"], "log.txt")] },
      });
      await wait(0);
      listed = [choose()];
      await wait(FIRST_POLL_MS);
      return shown;
    })();
    keeps(searched());
    fireEvent.click(screen.getByTestId("document-offer-search"));
    await wait(0);
    expect(send()).toBeEnabled();
    expect(screen.queryByTestId("send-held")).toBeNull();
    fireEvent.change(screen.getByTestId("composer"), { target: { value: "What is in it?" } });
    fireEvent.click(send());
    await wait(0);
    expect(post).toHaveBeenCalledExactlyOnceWith("/api/chats/one/messages", {
      content: "What is in it?",
      attachments: ["f2"],
      model: "local",
      search: false,
    });
    expect(onSent).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("document-chip")).toBeNull();
  });

  it("waits while its own document is made, says so, and then is ready by itself", async () => {
    await longFile();
    keeps(preparing());
    fireEvent.click(screen.getByTestId("document-offer-search"));
    await wait(0);
    // Reading again, in the card's own words and the server's.
    expect(chip()).toHaveAttribute("data-state", "reading");
    expect(screen.getByTestId("document-status")).toHaveTextContent(
      "Getting this file ready to search…",
    );
    expect(send()).toBeDisabled();
    expect(screen.getByTestId("send-held")).toHaveTextContent(
      "Waiting for Workbench to read log.txt.",
    );
    expect(screen.getByTestId("document-choose-again")).toBeInTheDocument();
    // The page asks again each second until it can be searched.
    listed = [preparing()];
    await wait(POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "reading");
    listed = [searched()];
    await wait(POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "search");
    expect(send()).toBeEnabled();
    const calls = vi.mocked(api).mock.calls.length;
    await wait(30_000);
    expect(vi.mocked(api).mock.calls.length).toBe(calls);
  });

  it("says in the server's words when its own document could not be made, and the person can choose again", async () => {
    await longFile();
    const message = "Failed: Workbench could not read this file (the text is cut off).";
    const failed = searched({
      search: fileSearch({ status: "failed", message, passages: 0 }),
      decision: {
        ...searched().decision!,
        kind: "unreadable",
        send: false,
        message,
        search: fileSearch({ status: "failed", message, passages: 0 }),
      },
    });
    keeps(failed);
    fireEvent.click(screen.getByTestId("document-offer-search"));
    await wait(0);
    expect(chip()).toHaveAttribute("data-state", "failed");
    expect(screen.getByTestId("document-status")).toHaveTextContent(message);
    expect(screen.getByTestId("document-status")).toHaveClass("notice--error");
    expect(send()).toBeDisabled();
    // Taking the choice back brings the offers back.
    vi.mocked(put).mockImplementation(async () => {
      listed = [choose()];
      return choose();
    });
    fireEvent.click(screen.getByTestId("document-choose-again"));
    await wait(0);
    expect(vi.mocked(put).mock.calls[1]![1]).toEqual({ mode: null });
    expect(chip()).toHaveAttribute("data-state", "choose");
    expect(screen.getByTestId("document-offer-search")).toBeInTheDocument();
  });

  it("is taken back by Choose again, and the file is the person's to choose for again", async () => {
    await longFile();
    keeps(searched());
    fireEvent.click(screen.getByTestId("document-offer-search"));
    await wait(0);
    vi.mocked(put).mockImplementation(async () => {
      listed = [choose()];
      return choose();
    });
    fireEvent.click(screen.getByTestId("document-choose-again"));
    await wait(0);
    expect(vi.mocked(put).mock.calls[1]).toEqual([
      "/api/chats/one/files/f2/reading?model=local",
      { mode: null },
    ]);
    expect(chip()).toHaveAttribute("data-state", "choose");
    expect(send()).toBeDisabled();
    expect(screen.queryByTestId("document-choice")).toBeNull();
  });

  it("is refused with the server's words on the card, and the file stays as it was", async () => {
    await longFile();
    vi.mocked(put).mockRejectedValue(
      new ApiError("The owner of this Workbench has turned off documents.", 403, {
        message: "The owner of this Workbench has turned off documents.",
      }),
    );
    fireEvent.click(screen.getByTestId("document-offer-search"));
    await wait(0);
    expect(screen.getByTestId("document-problem")).toHaveTextContent("Your choice was not kept");
    expect(screen.getByTestId("document-problem")).toHaveTextContent(
      "The owner of this Workbench has turned off documents.",
    );
    expect(chip()).toHaveAttribute("data-state", "choose");
    expect(send()).toBeDisabled();
  });

  it("is not offered for a file that was not read, which has nothing to search", async () => {
    composer();
    uploads.push(fresh({ id: "f4", name: "scan.pdf" }));
    fireEvent.drop(screen.getByTestId("composer-area"), {
      dataTransfer: { files: [new File(["x"], "scan.pdf")] },
    });
    await wait(0);
    listed = [scan()];
    await wait(FIRST_POLL_MS);
    expect(chip()).toHaveAttribute("data-state", "skipped");
    expect(screen.queryByTestId("document-offer-search")).toBeNull();
  });
});

describe("the Documents hint in the composer", () => {
  function withHint(initial?: ChatFile[]) {
    render(
      <Composer
        chat={chat}
        initialAttachments={initial}
        me={me}
        models={models}
        modelsError={null}
        running={false}
        documentsHint={<p data-testid="a-hint">Your 3 documents are off here.</p>}
        onSent={vi.fn()}
        onChat={vi.fn()}
      />,
    );
  }

  it("is under the tools while nothing is attached", () => {
    withHint();
    expect(screen.getByTestId("a-hint")).toBeInTheDocument();
    expect(screen.getByTestId("composer-area")).toContainElement(screen.getByTestId("a-hint"));
  });

  it("is not there while files are attached: the composer is full enough, and the person is in the middle of something", async () => {
    listed = [choose()];
    withHint([choose()]);
    await wait(0);
    expect(screen.getByTestId("document-chip")).toBeInTheDocument();
    expect(screen.queryByTestId("a-hint")).toBeNull();
  });

  it("is back once the file is taken away", async () => {
    listed = [choose()];
    withHint([choose()]);
    await wait(0);
    fireEvent.click(screen.getByRole("button", { name: "Remove log.txt" }));
    await wait(0);
    expect(screen.queryByTestId("document-chip")).toBeNull();
    expect(screen.getByTestId("a-hint")).toBeInTheDocument();
  });
});
