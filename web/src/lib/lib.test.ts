import { attachmentProblem, searchProblem } from "../components/Composer";
import { reading as documentReading } from "../test-documents";
import { parseFrames } from "./events";
import { SECRET_KEY, takeFragment } from "./session";
import type { ChatDetail, Message, Model, Models } from "./types";
import { applyEvent, place, progressAfter } from "./useChat";
import {
  announce,
  EDIT_KEYS,
  modelLabel,
  progressWords,
  SEARCH_HINT,
  SEARCH_PRIVACY,
  splitWorking,
  statusWords,
  workingWords,
} from "./words";

const model = (over: Partial<Model> = {}): Model => ({
  id: "qwen3-14b",
  contextLength: 32768,
  imageInput: false,
  audioInput: false,
  fileInput: false,
  webSearch: true,
  ready: true,
  onDemand: false,
  ...over,
});

const message = (over: Partial<Message> = {}): Message => ({
  id: "a",
  seq: 2,
  role: "assistant",
  content: "",
  reasoning: "",
  attachments: [],
  status: "running",
  error: null,
  sources: [],
  searches: 0,
  search: false,
  model: "m",
  finish: null,
  createdAt: 0,
  finishedAt: null,
  ...over,
});

describe("the request secret (W3)", () => {
  afterEach(() => window.localStorage.clear());

  it("is taken from the fragment once and the fragment cleared", () => {
    window.history.replaceState(null, "", "/#signin=abc123");
    expect(takeFragment()).toEqual({ signedIn: true, error: null });
    expect(window.localStorage.getItem(SECRET_KEY)).toBe("abc123");
    expect(window.location.hash).toBe("");
  });

  it("carries a failed sign-in's reason", () => {
    window.history.replaceState(null, "", "/#signin-error=Eugene%20said%20no");
    expect(takeFragment()).toEqual({ signedIn: false, error: "Eugene said no" });
    expect(window.localStorage.getItem(SECRET_KEY)).toBeNull();
  });
});

describe("pieces of an answer go where they say (useChat)", () => {
  it("appends the next piece", () => {
    expect(place("Hello", " there", 5)).toEqual({ text: "Hello there", gap: false });
  });
  it("skips a piece it already has", () => {
    expect(place("Hello there", " there", 5)).toEqual({ text: "Hello there", gap: false });
  });
  it("keeps what follows a piece it already has", () => {
    expect(place("Hello there friend", " there", 5)).toEqual({
      text: "Hello there friend",
      gap: false,
    });
  });
  it("reports a gap rather than showing one", () => {
    expect(place("Hello", " world", 11)).toEqual({ text: "Hello", gap: true });
  });
  it("counts as the page does, so an emoji does not shift what follows", () => {
    expect(place("Hi 👋", "!", "Hi 👋".length)).toEqual({ text: "Hi 👋!", gap: false });
  });
  it("replaces a running answer with a finished one", () => {
    const detail = {
      chat: { id: "c", running: true },
      messages: [message({ content: "Half" })],
      ownerName: null,
    } as unknown as ChatDetail;
    const done = applyEvent(detail, {
      type: "done",
      message: message({ content: "Whole", status: "done" }),
    });
    expect(done.detail.messages[0]!.content).toBe("Whole");
    expect(done.detail.chat.running).toBe(false);
  });
  it("takes where a search fell from its progress, and nothing else from it", () => {
    const detail = {
      chat: { id: "c", running: true },
      messages: [message({ id: "a", content: "Draft." })],
      ownerName: null,
    } as unknown as ChatDetail;
    const searched = applyEvent(detail, {
      type: "progress",
      id: "a",
      progress: { stage: "tool", tool: "web_search", phase: "started" },
      answerFrom: 6,
      reasoningFrom: 0,
    });
    expect(searched.detail.messages[0]).toMatchObject({ answerFrom: 6, reasoningFrom: 0 });
    const reading = applyEvent(searched.detail, {
      type: "progress",
      id: "a",
      progress: { stage: "prompt", prompt_tokens: 10 },
    });
    expect(reading.detail.messages[0]).toMatchObject({ answerFrom: 6, content: "Draft." });
  });
});

describe("server-sent events", () => {
  it("splits frames and keeps a partial one", () => {
    const { events, rest } = parseFrames('data: {"a":1}\n\n: keepalive\n\ndata: {"b"');
    expect(events).toEqual(['{"a":1}']);
    expect(rest).toBe('data: {"b"');
  });
});

describe("what Workbench says", () => {
  it("names a model with its window, as the console does", () => {
    expect(modelLabel(model())).toBe("qwen3-14b · 32k context");
    expect(modelLabel(model({ contextLength: null }))).toBe("qwen3-14b · context unknown");
  });

  it("says what an answer is waiting on", () => {
    expect(progressWords(null)).toBe("Waiting for the model…");
    expect(progressWords({ stage: "tool", tool: "web_search" })).toBe("Searching the web…");
    expect(progressWords({ stage: "tool", tool: "web_search", phase: "started" })).toBe(
      "Searching the web…",
    );
    expect(progressWords({ stage: "tool", tool: "web_search", phase: "finished" })).toBe(
      "Reading what the search found…",
    );
    expect(
      progressWords({
        stage: "prompt",
        prompt_tokens: 1000,
        cached_tokens: 0,
        processed_tokens: 250,
      }),
    ).toBe("Reading your message: 25%");
  });

  it("says why an answer is not a finished one", () => {
    expect(statusWords(message({ status: "done" }))).toBeNull();
    expect(statusWords(message({ status: "stopped" }))).toBe("Stopped.");
    expect(statusWords(message({ status: "interrupted" }))).toMatch(/restarted/);
    expect(statusWords(message({ status: "failed", error: "the key was refused" }))).toBe(
      "the key was refused",
    );
    expect(statusWords(message({ status: "done", finish: "length" }))).toMatch(/length limit/);
  });

  it("tells a length-limited answer what to do, by the setting that capped it", () => {
    // 2026-10-10: the cap was the install's 4,096, and this told Troy to raise
    // his chat's own setting.
    const capped = (outputCap: Message["outputCap"]) =>
      statusWords(message({ status: "done", finish: "length", outputCap }));
    expect(capped({ tokens: 4096, source: "install" })).toContain(
      "this install's output cap of 4,096 tokens (Default max output tokens",
    );
    expect(capped({ tokens: 2000, source: "profile" })).toContain(
      "the 2,000-token cap on this model's Library profile",
    );
    expect(capped({ tokens: 512, source: "request" })).toContain(
      "this chat's “Longest answer (tokens)”, 512",
    );
    // Not said, or none sent: no setting is blamed.
    const unsaid = capped(null);
    expect(unsaid).toContain(
      "an output cap, the model's context window, or its backend's own limit",
    );
    expect(unsaid).not.toContain("Longest answer");
    for (const said of [capped(null), capped({ tokens: 1, source: "install" })]) {
      expect(said?.toLowerCase()).toContain("ask it to continue");
    }
    expect(EDIT_KEYS).toContain("Cmd+Enter on a Mac");
  });
});

describe("what an answer being written is doing (2026-10-10)", () => {
  const at = 1_000;
  const writing = (over: Partial<Message> = {}) => message({ createdAt: at, ...over });

  it("waits until something comes, then says thinking or writing", () => {
    expect(workingWords(null, writing(), at + 5)).toBe("Waiting for the model…");
    expect(workingWords({ stage: "working" }, writing(), at + 5)).toBe("The model is working…");
    // Troy's case: Strata counts only at the end, so the time it has run.
    expect(workingWords(null, writing({ reasoning: "Let me see" }), at + 72)).toBe(
      "Thinking · 1 min 12 s",
    );
    expect(workingWords(null, writing({ content: "The answer" }), at + 18)).toBe(
      "Writing the answer · 18 s",
    );
  });

  it("says the backend's own count where it gives one", () => {
    const count = {
      stage: "generating" as const,
      generated_tokens: 1234,
      tokens_per_second: 83.14,
    };
    expect(workingWords(count, writing({ reasoning: "Let me see" }), at + 72)).toBe(
      "Thinking · 1,234 tokens · 83.1 tok/s",
    );
    expect(
      workingWords({ stage: "generating", generated_tokens: 7 }, writing({ content: "Hi" }), at),
    ).toBe("Writing the answer · 7 tokens");
  });

  it("keeps saying what it reads and which tool it runs", () => {
    expect(
      workingWords({ stage: "tool", tool: "web_search" }, writing({ reasoning: "x" }), at + 3),
    ).toBe("Searching the web…");
    expect(
      workingWords(
        { stage: "prompt", prompt_tokens: 100, processed_tokens: 50, cached_tokens: 0 },
        writing(),
        at,
      ),
    ).toBe("Reading your message: 50%");
  });

  it("keeps a running count across the pieces it counts, and clears anything else", () => {
    const delta = { type: "delta" as const, id: "a", reasoning: "more" };
    const count = { stage: "generating" as const, generated_tokens: 10 };
    expect(progressAfter(delta, count)).toBeUndefined();
    expect(progressAfter(delta, { stage: "working" })).toBeNull();
    expect(progressAfter({ type: "delta", id: "a" }, { stage: "working" })).toBeUndefined();
  });
});

describe("what an answer says while its files are read (MR2)", () => {
  const reading = { stage: "documents", phase: "reading", files: 2 } as const;

  it("says the files are being read, not that the model is awaited", () => {
    expect(progressWords(reading)).toBe("Reading your files…");
    expect(progressWords({ stage: "documents", phase: "reading", files: 1 })).toBe(
      "Reading your files…",
    );
    expect(progressWords(null)).toBe("Waiting for the model…");
  });

  it("is the working line before anything is written, and the model's own count is said as it always was", () => {
    const at = 1_000;
    const writing = (over: Partial<Message> = {}) => message({ createdAt: at, ...over });
    expect(workingWords(reading, writing(), at + 12)).toBe("Reading your files…");
    expect(
      workingWords({ stage: "generating", generated_tokens: 5 }, writing({ content: "Hi" }), at),
    ).toBe("Writing the answer · 5 tokens");
  });

  it("is a progress event like any other, and the model's first words end it", () => {
    const event = { type: "progress" as const, id: "a", progress: reading };
    expect(progressAfter(event, null)).toEqual(reading);
    const delta = { type: "delta" as const, id: "a", content: "The" };
    expect(progressAfter(delta, reading)).toBeNull();
  });
});

describe("what an answer says while the person's documents are searched (MR4)", () => {
  const searching = { stage: "documents", phase: "searching" } as const;

  it("says Searching your documents, which is not the model's wait and not the reading of files", () => {
    expect(progressWords(searching)).toBe("Searching your documents…");
    expect(progressWords(searching)).not.toBe(
      progressWords({ stage: "documents", phase: "reading", files: 1 }),
    );
    expect(progressWords(searching)).not.toMatch(/model/);
  });

  it("is the working line before anything is written, and the model's own count is said as it always was", () => {
    const at = 1_000;
    const writing = (over: Partial<Message> = {}) => message({ createdAt: at, ...over });
    expect(workingWords(searching, writing(), at + 30)).toBe("Searching your documents…");
    expect(
      workingWords({ stage: "generating", generated_tokens: 5 }, writing({ content: "Hi" }), at),
    ).toBe("Writing the answer · 5 tokens");
  });

  it("is a progress event like any other, and the model's first words end it", () => {
    const event = { type: "progress" as const, id: "a", progress: searching };
    expect(progressAfter(event, null)).toEqual(searching);
    const delta = { type: "delta" as const, id: "a", content: "The" };
    expect(progressAfter(delta, searching)).toBeNull();
  });

  it("is told to a screen reader once, as a line with nothing that ticks", () => {
    expect(splitWorking(progressWords(searching))).toEqual({
      say: "Searching your documents…",
      tail: "",
    });
  });
});

describe("the search switch says why it is off (W6)", () => {
  const listing = (available: boolean, reason: string | null = null): Models => ({
    models: [model()],
    webSearch: { available, reason },
  });

  it("gives the gateway's own reason", () => {
    const reason = "no search account is set up; add one under Backends, then Add a search account";
    expect(searchProblem(listing(false, reason), model())).toBe(reason);
  });

  it("names a model that cannot search, as Eugene's limit and not the model's", () => {
    const said = searchProblem(listing(true), model({ webSearch: false }));
    expect(said).toMatch(
      /^Eugene cannot give .+ tools as it runs here, so it cannot search the web/,
    );
    expect(said).not.toMatch(/neither search nor use tools/);
  });

  it("is on when both the install and the model allow it", () => {
    expect(searchProblem(listing(true), model())).toBeNull();
  });
});

describe("an attachment goes to a model that takes it (W7)", () => {
  const png = { id: "f", name: "a.png", mediaType: "image/png" };
  it("says when the model does not take it", () => {
    expect(attachmentProblem(model(), [png])).toMatch(/does not take images/);
  });
  it("is quiet when it does", () => {
    expect(attachmentProblem(model({ imageInput: true }), [png])).toBeNull();
  });

  // MR2: Workbench reads a PDF and sends its text, which any model takes.
  const pdf = { id: "p", name: "a.pdf", mediaType: "application/pdf" };
  const read = { ...pdf, kind: "document" as const, reading: documentReading() };
  it("does not ask a model to take a PDF that Workbench read, and sends as text", () => {
    expect(attachmentProblem(model(), [read])).toBeNull();
    expect(attachmentProblem(model({ imageInput: true }), [png, read])).toBeNull();
  });
  it("still asks it of a PDF that Workbench did not read, which goes as the file", () => {
    expect(attachmentProblem(model(), [pdf])).toMatch(/does not take PDFs/);
    expect(attachmentProblem(model({ fileInput: true }), [pdf])).toBeNull();
    const unread = { ...pdf, kind: "document" as const, reading: null };
    expect(attachmentProblem(model(), [unread])).toMatch(/does not take PDFs/);
  });
  it("still asks it of an image beside a PDF that was read", () => {
    expect(attachmentProblem(model(), [png, read])).toBe(
      "qwen3-14b does not take images. Pick a model that does, or remove the file.",
    );
  });
});

describe("a working line is said once and shown ticking (splitWorking)", () => {
  it("splits what is said from what changes every second", () => {
    expect(splitWorking("Thinking · 1 min 12 s")).toEqual({
      say: "Thinking",
      tail: " · 1 min 12 s",
    });
    expect(splitWorking("Writing the answer · 1,234 tokens · 83.1 tok/s")).toEqual({
      say: "Writing the answer",
      tail: " · 1,234 tokens · 83.1 tok/s",
    });
    expect(splitWorking("Reading your message: 25%")).toEqual({
      say: "Reading your message",
      tail: ": 25%",
    });
  });

  it("leaves a line with nothing that ticks whole", () => {
    for (const line of [
      "Waiting for the model…",
      "Searching the web…",
      "Reading your files…",
      "Searching your documents…",
      "Review the tool calls to continue.",
      // A colon that is not followed by a number is part of the sentence.
      "Using search: the web…",
    ]) {
      expect(splitWorking(line)).toEqual({ say: line, tail: "" });
    }
  });

  it("joins back to the line it was given, for every line the page makes", () => {
    const at = 1_000;
    const lines = [
      workingWords(null, message({ createdAt: at, reasoning: "x" }), at + 72),
      workingWords(
        { stage: "generating", generated_tokens: 7, tokens_per_second: 20 },
        message({ createdAt: at, content: "Hi" }),
        at,
      ),
      progressWords({ stage: "prompt", prompt_tokens: 100, processed_tokens: 50 }),
      progressWords({ stage: "documents", phase: "reading", files: 3 }),
      progressWords({ stage: "documents", phase: "searching" }),
    ];
    for (const line of lines) {
      const { say, tail } = splitWorking(line);
      expect(say + tail).toBe(line);
    }
  });
});

describe("what a screen reader is told when the newest answer changes (announce)", () => {
  it("says the phase, and for a stop the cause that was observed", () => {
    expect(announce(message({ status: "running" }))).toBe("Waiting for the model.");
    expect(announce(message({ status: "running", reasoning: "Hm" }))).toBe("Thinking.");
    expect(announce(message({ status: "running", reasoning: "Hm", content: "A" }))).toBe(
      "Writing the answer.",
    );
    expect(announce(message({ status: "done" }))).toBe("Answer finished.");
    expect(announce(message({ status: "done", finish: "length" }))).toBe(
      "Answer finished at its length limit.",
    );
    expect(announce(message({ status: "stopped" }))).toBe("Answer stopped.");
    expect(announce(message({ status: "stopped", finish: "repetition_detected" }))).toBe(
      "Answer stopped because it was repeating.",
    );
    expect(announce(message({ status: "interrupted" }))).toContain("Workbench restarted");
  });

  it("says nothing of a failure (its note is already an alert), a person's message or no message", () => {
    expect(announce(message({ status: "failed", error: "The key was refused" }))).toBe("");
    expect(announce(message({ role: "user", status: "done" }))).toBe("");
    expect(announce(undefined)).toBe("");
  });
});

describe("the search switch says where the words go", () => {
  it("is one sentence on the screen and in the tooltip", () => {
    expect(SEARCH_PRIVACY).toBe("The words searched for go to the internet.");
    expect(SEARCH_HINT).toContain(SEARCH_PRIVACY);
    expect(SEARCH_HINT).toBe(
      "Search the web before answering. The words searched for go to the internet.",
    );
  });
});
