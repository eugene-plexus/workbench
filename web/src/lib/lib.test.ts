import { attachmentProblem, searchProblem } from "../components/Composer";
import { parseFrames } from "./events";
import { SECRET_KEY, takeFragment } from "./session";
import type { ChatDetail, Message, Model, Models } from "./types";
import { applyEvent, place } from "./useChat";
import { modelLabel, progressWords, statusWords } from "./words";

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

  it("names a model that cannot search", () => {
    expect(searchProblem(listing(true), model({ webSearch: false }))).toMatch(
      /cannot search the web/,
    );
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
});
