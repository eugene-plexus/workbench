/**
 * When previews run (rich content RC6): one at a time, a few at most, and when
 * they all fall silent, the busy one named and the rest started again.
 */

import {
  createRunner,
  MAX_RUNNING,
  PROBATION_MS,
  STUCK_MS,
  type StopReason,
  WATCH_MS,
} from "./previewRunner";

let clock = 0;
let hidden = false;

function make() {
  const runner = createRunner(
    () => clock,
    () => hidden,
  );
  const log: string[] = [];
  const focus = { id: "" };
  // Previews whose frame is never ready (put in a stuck process).
  const slow = new Set<string>();
  const slot = (id: string) => {
    runner.register({
      id,
      key: () => `key-${id}`,
      start: () => {
        log.push(`start ${id}`);
        if (!slow.has(id)) runner.ready(id);
      },
      stop: (why: StopReason) => log.push(`stop ${id} ${why}`),
      focused: () => focus.id === id,
    });
    return id;
  };
  return { runner, log, slot, focus, slow };
}

type Runner = ReturnType<typeof createRunner>;

/** Each in view, wanting to run, in order. */
function shown(runner: Runner, ids: string[]) {
  for (const id of ids) {
    runner.visible(id, true);
    runner.want(id, true);
  }
}

/** Time passing as the page's watch sees it, `heard` saying they still answer at each beat. */
function pass(runner: Runner, ms: number, heard: string[] = []) {
  const end = clock + ms;
  while (clock < end) {
    clock = Math.min(end, clock + WATCH_MS);
    for (const id of heard) runner.alive(id);
    runner.tick();
  }
}

beforeEach(() => {
  clock = 0;
  hidden = false;
  window.localStorage.clear();
});

describe("previews that run by themselves", () => {
  it("start one at a time, each after the one before has had its source a while", () => {
    const { runner, log, slot } = make();
    shown(runner, [slot("a"), slot("b")]);
    expect(log).toEqual(["start a"]);
    pass(runner, PROBATION_MS - WATCH_MS, ["a"]);
    expect(log).toEqual(["start a"]);
    pass(runner, WATCH_MS, ["a"]);
    expect(log).toEqual(["start a", "start b"]);
  });

  it("wait while out of view, and do not start for the person who stopped them", () => {
    const { runner, log, slot } = make();
    slot("a");
    runner.want("a", true);
    expect(log).toEqual([]);
    runner.visible("a", true);
    expect(log).toEqual(["start a"]);
    runner.stopped("a");
    pass(runner, 10_000);
    expect(log).toEqual(["start a"]);
  });

  it("make room by stopping the one longest out of view, never one on screen", () => {
    const { runner, log, slot } = make();
    const ids = ["a", "b", "c", "d", "e"].map(slot);
    shown(runner, ids.slice(0, MAX_RUNNING));
    pass(runner, (PROBATION_MS + WATCH_MS) * MAX_RUNNING, ids.slice(0, MAX_RUNNING));
    expect(log.filter((l) => l.startsWith("start"))).toHaveLength(MAX_RUNNING);
    // All four on screen: the fifth waits.
    shown(runner, ["e"]);
    pass(runner, PROBATION_MS, ids.slice(0, MAX_RUNNING));
    expect(log).not.toContain("start e");
    // a, then b, go out of view: a left first, so a makes room.
    clock += 1;
    runner.visible("a", false);
    clock += 1;
    runner.visible("b", false);
    pass(runner, WATCH_MS, ids.slice(0, MAX_RUNNING));
    expect(log.slice(-2)).toEqual(["stop a room", "start e"]);
  });

  it("when every one falls silent, stop them all, name the one the person was in, and run the rest again", () => {
    const { runner, log, slot, focus } = make();
    shown(runner, [slot("a"), slot("b")]);
    pass(runner, PROBATION_MS + WATCH_MS, ["a", "b"]);
    expect(log).toEqual(["start a", "start b"]);
    focus.id = "a";
    pass(runner, STUCK_MS + WATCH_MS);
    expect(log).toContain("stop a culprit");
    expect(log).toContain("stop b stuck");
    expect(runner.isStuck("key-a")).toBe(true);
    expect(JSON.parse(window.localStorage.getItem("workbench.previews.stuck") ?? "[]")).toEqual([
      "key-a",
    ]);
    // b starts again by itself; a does not, until the person presses Run.
    expect(log.at(-1)).toBe("start b");
    runner.want("a", true);
    pass(runner, PROBATION_MS + WATCH_MS, ["b"]);
    expect(log.filter((l) => l === "start a")).toHaveLength(1);
    runner.started("a");
    expect(runner.isStuck("key-a")).toBe(false);
  });

  it("name the one given its source last when the person is in none", () => {
    const { runner, log, slot } = make();
    shown(runner, [slot("a"), slot("b")]);
    pass(runner, PROBATION_MS + WATCH_MS, ["a", "b"]);
    pass(runner, STUCK_MS + WATCH_MS);
    expect(log).toContain("stop b culprit");
    expect(log).toContain("stop a stuck");
  });

  it("never name one whose frame never got its source: it was put in the stuck process", () => {
    const { runner, log, slot, slow } = make();
    slow.add("b");
    shown(runner, [slot("a"), slot("b")]);
    pass(runner, PROBATION_MS + WATCH_MS, ["a"]);
    expect(log).toEqual(["start a", "start b"]);
    // a loops, and b's frame lands in a's process, so b is never ready.
    pass(runner, STUCK_MS + WATCH_MS);
    expect(log).toContain("stop a culprit");
    expect(log).toContain("stop b stuck");
  });

  it("hold the line while the one before has not got its source", () => {
    const { runner, log, slot, slow } = make();
    slow.add("a");
    shown(runner, [slot("a"), slot("b")]);
    pass(runner, PROBATION_MS * 3);
    expect(log).toEqual(["start a"]);
  });

  it("do not call a group stuck while its previews are still starting", () => {
    const { runner, log, slot, slow } = make();
    slow.add("a");
    shown(runner, [slot("a")]);
    pass(runner, STUCK_MS * 5);
    expect(log).toEqual(["start a"]);
  });

  it("do not call a group stuck across a gap of the page's own, or while the tab is hidden", () => {
    const { runner, log, slot } = make();
    shown(runner, [slot("a")]);
    pass(runner, WATCH_MS, ["a"]);
    // The page stalled (a long task, a machine asleep): its watch and their beat alike.
    clock += STUCK_MS * 4;
    runner.tick();
    expect(log).toEqual(["start a"]);
    // Hidden, the beat is throttled: still no loop.
    hidden = true;
    pass(runner, STUCK_MS * 2);
    hidden = false;
    runner.shown();
    pass(runner, WATCH_MS, ["a"]);
    expect(log).toEqual(["start a"]);
    // Shown again and silent: that is a loop.
    pass(runner, STUCK_MS + WATCH_MS);
    expect(log).toContain("stop a culprit");
  });

  it("forget every loop they remember at sign-out", () => {
    const { runner, slot } = make();
    shown(runner, [slot("a")]);
    pass(runner, STUCK_MS + WATCH_MS);
    expect(runner.isStuck("key-a")).toBe(true);
    runner.reset();
    expect(runner.isStuck("key-a")).toBe(false);
    expect(window.localStorage.getItem("workbench.previews.stuck")).toBe("[]");
  });
});
