/**
 * The formula worker's keeper (mathClient.ts): one formula at a time, a
 * formula that takes too long is stopped by ending its worker, and the rest
 * go on in a new one.
 */

import { workerEngine } from "./mathClient";

type Posted = { id: number; tex: string; display: boolean };

/** A worker that answers only when the test tells it to. */
class FakeWorker {
  static made: FakeWorker[] = [];
  posted: Posted[] = [];
  terminated = false;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;

  constructor() {
    FakeWorker.made.push(this);
  }

  postMessage(message: Posted): void {
    this.posted.push(message);
  }

  terminate(): void {
    this.terminated = true;
  }

  say(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }

  fail(message: string): void {
    this.onerror?.({ message, preventDefault: () => undefined } as ErrorEvent);
  }
}

const limits = { formula: 1_500, start: 10_000 };

function engine() {
  return workerEngine(() => new FakeWorker() as unknown as Worker, limits);
}

beforeEach(() => {
  FakeWorker.made = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

it("starts a worker for the first formula, and waits for it to be ready", async () => {
  const typeset = engine().typeset("x", false);
  const [worker] = FakeWorker.made;
  expect(worker?.posted).toEqual([]);
  worker?.say({ ready: true });
  expect(worker?.posted).toEqual([{ id: 1, tex: "x", display: false }]);
  worker?.say({ id: 1, markup: "<span>x</span>" });
  await expect(typeset).resolves.toEqual({ markup: "<span>x</span>" });
});

it("lays out one formula at a time, in order", async () => {
  const e = engine();
  const first = e.typeset("a", false);
  const second = e.typeset("b", true);
  const worker = FakeWorker.made[0];
  worker?.say({ ready: true });
  expect(worker?.posted.map((p) => p.tex)).toEqual(["a"]);
  worker?.say({ id: 1, error: "bad" });
  expect(worker?.posted.map((p) => p.tex)).toEqual(["a", "b"]);
  worker?.say({ id: 2, markup: "m" });
  await expect(first).resolves.toEqual({ error: "bad" });
  await expect(second).resolves.toEqual({ markup: "m" });
  expect(FakeWorker.made).toHaveLength(1);
});

it("stops a formula that takes too long, and lays out the next in a new worker", async () => {
  const e = engine();
  const slow = e.typeset("slow", false);
  const next = e.typeset("next", false);
  const [first] = FakeWorker.made;
  first?.say({ ready: true });
  vi.advanceTimersByTime(1_499);
  expect(first?.terminated).toBe(false);
  vi.advanceTimersByTime(1);
  expect(first?.terminated).toBe(true);
  await expect(slow).resolves.toEqual({ error: "it took longer than 1.5 s to lay out" });
  const second = FakeWorker.made[1];
  expect(second).toBeDefined();
  second?.say({ ready: true });
  expect(second?.posted.map((p) => p.tex)).toEqual(["next"]);
  // A late answer from the ended worker changes nothing.
  first?.say({ id: 1, markup: "late" });
  second?.say({ id: 2, markup: "m" });
  await expect(next).resolves.toEqual({ markup: "m" });
});

it("does not count the worker's start against the formula", async () => {
  const typeset = engine().typeset("x", false);
  const [worker] = FakeWorker.made;
  vi.advanceTimersByTime(5_000);
  worker?.say({ ready: true });
  vi.advanceTimersByTime(1_000);
  worker?.say({ id: 1, markup: "m" });
  await expect(typeset).resolves.toEqual({ markup: "m" });
});

it("fails every waiting formula when the worker does not start", async () => {
  const e = engine();
  const a = e.typeset("a", false);
  const b = e.typeset("b", false);
  vi.advanceTimersByTime(10_000);
  await expect(a).resolves.toEqual({
    error: "the formula worker did not start within 10 s",
    again: true,
  });
  await expect(b).resolves.toEqual({
    error: "the formula worker did not start within 10 s",
    again: true,
  });
  expect(FakeWorker.made[0]?.terminated).toBe(true);
  // The next formula tries again.
  void e.typeset("c", false);
  expect(FakeWorker.made).toHaveLength(2);
});

it("names a worker that stops on a formula, and goes on with the next", async () => {
  const e = engine();
  const a = e.typeset("a", false);
  const b = e.typeset("b", false);
  FakeWorker.made[0]?.say({ ready: true });
  FakeWorker.made[0]?.fail("out of memory");
  await expect(a).resolves.toEqual({
    error: "the formula worker stopped: out of memory",
    again: true,
  });
  FakeWorker.made[1]?.say({ ready: true });
  expect(FakeWorker.made[1]?.posted.map((p) => p.tex)).toEqual(["b"]);
  FakeWorker.made[1]?.say({ id: 2, markup: "m" });
  await expect(b).resolves.toEqual({ markup: "m" });
});

it("says so when no worker can be made", async () => {
  const e = workerEngine(() => {
    throw new Error("blocked");
  }, limits);
  await expect(e.typeset("x", false)).resolves.toEqual({
    error: "formulas cannot be laid out in this browser: Error: blocked",
    again: true,
  });
});

it("skips a formula nobody still shows", async () => {
  const e = engine();
  const gone = e.typeset("gone", false, () => false);
  const shown = e.typeset("shown", false, () => true);
  FakeWorker.made[0]?.say({ ready: true });
  await expect(gone).resolves.toBeNull();
  expect(FakeWorker.made[0]?.posted.map((p) => p.tex)).toEqual(["shown"]);
  FakeWorker.made[0]?.say({ id: 2, markup: "m" });
  await expect(shown).resolves.toEqual({ markup: "m" });
});
