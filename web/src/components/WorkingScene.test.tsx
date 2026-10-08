import { act, render, screen } from "@testing-library/react";

import { SCENE_DELAY_MS, SCENE_TURN_MS, SCENES } from "../lib/scenes";
import type { Message, Progress } from "../lib/types";
import { MessageView } from "./MessageView";

// Two scenes, so moving to the next one is visible however many ship.
vi.mock("../lib/scenes", () => ({
  SCENES: [
    { file: "eugene-first.svg", phrase: "First…" },
    { file: "eugene-second.svg", phrase: "Second…" },
  ],
  SCENE_DELAY_MS: 600,
  SCENE_TURN_MS: 12_000,
}));

const running = (over: Partial<Message> = {}): Message => ({
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

function show(message: Message, progress: Progress | null = null) {
  const view = (m: Message, p: Progress | null) => (
    <MessageView
      chatId="c"
      message={m}
      progress={p}
      last
      busy
      readOnly={false}
      onChanged={() => undefined}
    />
  );
  const out = render(view(message, progress));
  return { ...out, update: (m: Message, p: Progress | null = null) => out.rerender(view(m, p)) };
}

let reduced = false;
const listeners = new Set<() => void>();

beforeEach(() => {
  vi.useFakeTimers();
  reduced = false;
  listeners.clear();
  window.matchMedia = vi.fn((query: string) => ({
    get matches() {
      return query.includes("reduce") && reduced;
    },
    media: query,
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
  })) as unknown as typeof window.matchMedia;
});

afterEach(() => {
  vi.useRealTimers();
});

const wait = (ms: number) => act(() => vi.advanceTimersByTime(ms));

it("shows only the plain line until the wait outlasts the delay, then Eugene beside it", () => {
  show(running());
  expect(screen.getByTestId("progress")).toHaveTextContent("Waiting for the model…");
  wait(SCENE_DELAY_MS - 50);
  expect(screen.queryByTestId("working-scene")).toBeNull();
  wait(100);
  const art = screen.getByTestId("scene-art");
  expect(art.getAttribute("src")).toMatch(/^\/scenes\/eugene-[a-z]+\.svg$/);
  expect(art).toHaveAttribute("alt", "");
  const phrase = screen.getByTestId("scene-phrase");
  expect(SCENES.map((s) => s.phrase)).toContain(phrase.textContent);
  // A screen reader hears the plain state, not the joke.
  expect(screen.getByTestId("working-scene")).toHaveAttribute("aria-hidden", "true");
  expect(phrase).toHaveAttribute("aria-hidden", "true");
  expect(screen.getByTestId("progress")).toHaveAttribute("aria-live", "polite");
});

it("leaves when the first words arrive, and never shows on a failed answer", () => {
  const { update } = show(running());
  wait(SCENE_DELAY_MS + 10);
  expect(screen.getByTestId("working-scene")).toBeInTheDocument();
  update(running({ content: "Hello" }));
  expect(screen.queryByTestId("working-scene")).toBeNull();
  update(running({ status: "failed", error: "The model stopped.", content: "" }));
  wait(SCENE_DELAY_MS + 10);
  expect(screen.queryByTestId("working-scene")).toBeNull();
});

it("works through a tool round but steps away while the person approves", () => {
  const { update } = show(running(), { stage: "tool", tool: "web_search", phase: "started" });
  wait(SCENE_DELAY_MS + 10);
  expect(screen.getByTestId("working-scene")).toBeInTheDocument();
  const line = screen.getByTestId("progress");
  update(running(), { stage: "tool", tool: "read_file", phase: "approval" });
  expect(screen.queryByTestId("working-scene")).toBeNull();
  expect(screen.getByTestId("progress")).toHaveTextContent("Review the tool calls to continue.");
  // The live line is the same element: the state change is announced.
  expect(screen.getByTestId("progress")).toBe(line);
  update(running(), { stage: "tool", tool: "read_file", phase: "finished" });
  expect(screen.queryByTestId("working-scene")).toBeNull();
  wait(SCENE_DELAY_MS + 10);
  expect(screen.getByTestId("working-scene")).toBeInTheDocument();
});

it("moves to the next scene on a long wait", () => {
  show(running());
  wait(SCENE_DELAY_MS + 10);
  const first = screen.getByTestId("scene-phrase").textContent;
  const at = SCENES.findIndex((s) => s.phrase === first);
  wait(SCENE_TURN_MS);
  const next = SCENES[(at + 1) % SCENES.length];
  expect(next?.phrase).not.toBe(first);
  expect(screen.getByTestId("scene-phrase")).toHaveTextContent(next?.phrase ?? "");
  expect(screen.getByTestId("scene-art").getAttribute("src")).toBe(`/scenes/${next?.file}`);
});

it("shows the still working pose, with the phrase, when reduced motion is asked for", () => {
  reduced = true;
  const { container } = show(running());
  wait(SCENE_DELAY_MS + 10);
  expect(screen.queryByTestId("scene-art")).toBeNull();
  expect(container.querySelector('img[src="/mascots/eugene-working.svg"]')).not.toBeNull();
  expect(screen.getByTestId("scene-phrase")).toBeInTheDocument();
  // The setting changing mid-wait swaps it back.
  reduced = false;
  act(() => listeners.forEach((fn) => fn()));
  expect(screen.getByTestId("scene-art")).toBeInTheDocument();
});
