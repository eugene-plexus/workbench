/**
 * Every control on the Documents screens has a name a screen reader says and a
 * `data-testid` a check can find it by (MR2 asks for both, as Memory's do). Each
 * screen is drawn in each state it has, and every control on it is held to both:
 * a control added later with neither fails here, by name. The check is the one
 * Memory's screens use (test-controls.ts).
 */

import { fireEvent, render, screen } from "@testing-library/react";

import { Composer } from "./components/Composer";
import { Attachments } from "./components/Attachments";
import { MessageView } from "./components/MessageView";
import { api, fileText } from "./lib/api";
import type { Facts } from "./lib/documents";
import type { Chat, ChatFile, Me, Message, Models } from "./lib/types";
import { controlsIn, lacking, wrong } from "./test-controls";
import {
  choose,
  damaged,
  everything,
  file,
  fresh,
  MODEL,
  native,
  noRoom,
  partial,
  partSource,
  reading,
  scan,
  source,
  tooLong,
} from "./test-documents";

vi.mock("./lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./lib/api")>()),
  api: vi.fn(),
  del: vi.fn(),
  fileText: vi.fn(),
  fileUrl: vi.fn(),
  patch: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(api)
    .mockReset()
    .mockReturnValue(new Promise(() => undefined) as never);
  vi.mocked(fileText).mockReset().mockResolvedValue("[page 1]\nAnnual report.");
});

const FACTS: Facts = {
  model: MODEL,
  request: { used: 4218, window: 32768, reserve: 1024, full: null },
};

function chips(
  files: ChatFile[],
  over: { facts?: Facts | null; problems?: Record<string, { title: string; text: string }> } = {},
) {
  return render(
    <Attachments
      files={files}
      facts={over.facts === undefined ? FACTS : over.facts}
      problems={over.problems ?? {}}
      choosing={null}
      locked={false}
      removing={null}
      onRemove={vi.fn()}
      onChoose={vi.fn().mockResolvedValue(undefined)}
    />,
  );
}

const picture: ChatFile = { id: "i", name: "shot.png", mediaType: "image/png", kind: "image" };

describe("the chips in the composer", () => {
  const STATES: [string, ChatFile][] = [
    ["being read", fresh()],
    ["read whole", file()],
    ["read whole as the person chose", everything()],
    ["a part, chosen", partial()],
    ["sent as the file itself", native()],
    ["longer than the default, to choose", choose()],
    ["too long for the window, with the part that fits", tooLong()],
    [
      "too long for the window, with nothing that fits",
      tooLong({ decision: { ...tooLong().decision!, offers: [] } }),
    ],
    ["with no room", noRoom()],
    ["skipped", scan()],
    ["failed", damaged()],
    [
      "with notes from the reader",
      file({
        decision: { ...file().decision!, notes: ["Page 2 has no text (it may be a picture)."] },
      }),
    ],
  ];

  it.each(STATES)("a file %s", (_name, found) => {
    const view = chips([found]);
    expect(controlsIn(view.container).length).toBeGreaterThanOrEqual(1);
    expect(wrong(view)).toEqual([]);
  });

  it("an image beside a document", () => {
    const view = chips([picture, file()]);
    expect(controlsIn(view.container).length).toBeGreaterThanOrEqual(3);
    expect(wrong(view)).toEqual([]);
  });

  it("every state at once, with what is said under them", () => {
    const view = chips(
      STATES.map(([, found], i) => ({ ...found, id: `s${i}`, name: `${found.name}-${i}` })),
      {
        facts: {
          model: { ...MODEL, known: false, window: null },
          request: {
            used: 40000,
            window: 32768,
            reserve: 1024,
            full: "This chat and its documents come to about 40,000 tokens.",
          },
        },
      },
    );
    expect(controlsIn(view.container).length).toBeGreaterThanOrEqual(20);
    expect(wrong(view)).toEqual([]);
  });

  it("a file that a message or a choice was refused for", () => {
    const view = chips([choose()], {
      problems: { f2: { title: "Your message was not sent", text: "About 9,300 tokens." } },
    });
    expect(screen.getByTestId("document-problem")).toBeInTheDocument();
    expect(wrong(view)).toEqual([]);
  });

  it("with the text Workbench read, opened", async () => {
    const view = chips([file()]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    await screen.findByTestId("document-text");
    expect(wrong(view)).toEqual([]);
  });

  it("with the text Workbench read, when it could not be opened", async () => {
    vi.mocked(fileText).mockRejectedValue(new Error("The text is no longer on disk."));
    const view = chips([file()]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    await screen.findByTestId("document-text-error");
    expect(wrong(view)).toEqual([]);
  });

  it("with the text of a file that is long enough to scroll", async () => {
    vi.mocked(fileText).mockResolvedValue("a line of the text\n".repeat(2000));
    const view = chips([file()]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    await screen.findByTestId("document-text");
    expect(wrong(view)).toEqual([]);
  });
});

describe("the composer with files in it", () => {
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
        fileInput: false,
        audioInput: false,
        webSearch: false,
        contextLength: 32768,
      },
    ],
    webSearch: { available: false, reason: "No search account." },
  };

  const composer = (initial: ChatFile[]) =>
    render(
      <Composer
        chat={chat}
        initialAttachments={initial}
        me={me}
        models={models}
        modelsError={null}
        running={false}
        onSent={vi.fn()}
        onChat={vi.fn()}
      />,
    );

  it("empty, and with its file input", () => {
    const view = composer([]);
    expect(wrong(view)).toEqual([]);
    expect(screen.getByTestId("attach-input")).toHaveAttribute("aria-label");
  });

  it("with a file that needs a choice and one that cannot be read, and Send held", () => {
    const view = composer([choose(), scan(), picture]);
    expect(screen.getByTestId("send")).toBeDisabled();
    expect(screen.getByTestId("send-held")).toBeInTheDocument();
    expect(wrong(view)).toEqual([]);
  });

  it("with a file that is ready, and Send free", () => {
    const view = composer([file(), partial()]);
    expect(screen.getByTestId("send")).toBeEnabled();
    expect(wrong(view)).toEqual([]);
  });
});

describe("what a message shows of documents", () => {
  const message = (over: Partial<Message> = {}): Message => ({
    id: "a",
    seq: 2,
    role: "assistant",
    content: "The code word is TAMARIND-6042.",
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

  /** What a message adds for documents: its Sources and its files. The message's own
   * controls are not this slice's. */
  const mine = (view: { container: HTMLElement }) =>
    [
      ...view.container.querySelectorAll(
        '[data-testid="document-sources"], [data-testid="document-file"]',
      ),
    ].flatMap(controlsIn);

  const render1 = (shown: Message) =>
    render(
      <MessageView
        chatId="c"
        message={shown}
        progress={null}
        busy={false}
        readOnly={false}
        onChanged={vi.fn()}
      />,
    );

  it("the files under an answer are text: they add no control that is unnamed", () => {
    const view = render1(message({ sources: [source(), partSource()] }));
    expect(screen.getAllByTestId("document-source")).toHaveLength(2);
    expect(mine(view)).toEqual([]);
    expect(lacking(mine(view), view.container)).toEqual([]);
  });

  it("a document in the person's message has a name for its button, and a test id", async () => {
    const view = render1(
      message({
        role: "user",
        content: "What is the code word?",
        files: [{ id: "f1", name: "report.pdf", mediaType: "application/pdf", reading: reading() }],
      }),
    );
    expect(mine(view)).toHaveLength(1);
    expect(lacking(mine(view), view.container)).toEqual([]);
    fireEvent.click(screen.getByTestId("document-text-toggle"));
    await screen.findByTestId("document-text");
    // The button and the box (a keyboard stop only while its text scrolls).
    expect(mine(view).length).toBeGreaterThanOrEqual(1);
    expect(lacking(mine(view), view.container)).toEqual([]);
  });
});
