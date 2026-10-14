/**
 * Mermaid diagrams in an answer (rich-content RC2): drawn once their block
 * closes, once however often the answer renders, and a diagram that cannot
 * be drawn stays its source and says why. The worker is a stand-in here;
 * diagramRender.test.ts draws with Mermaid itself.
 */

import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  diagramWorkerEngine,
  readPalette,
  setDiagramEngine,
  type DiagramEngine,
  type Drawing,
} from "../lib/diagramClient";
import { fileName, pngScale } from "./MermaidDiagram";
import { Markdown } from "./Markdown";

const TREE = [
  "svg",
  { viewBox: "0 0 120 60", style: "font-family:Arial;fill:#333333" },
  [
    ["title", {}, ["Release steps"]],
    ["rect", { width: "40", height: "20", style: "fill:#2b353d" }, []],
    ["text", { x: "5", y: "15" }, ["Ship"]],
  ],
];

/** A stand-in worker, counting what it is asked to draw. */
function counting(answer: (source: string) => Drawing = () => ({ tree: TREE, kind: "Flowchart" })) {
  const asked: string[] = [];
  const engine: DiagramEngine = {
    async draw(source) {
      asked.push(source);
      return answer(source);
    },
  };
  return { engine, asked };
}

const FENCE = "```mermaid\nflowchart TD\n  a --> b\n```";

afterEach(() => setDiagramEngine(null));

it("draws a closed mermaid block as a picture named by its kind", async () => {
  const { engine, asked } = counting();
  setDiagramEngine(engine);
  const { container } = render(<Markdown text={`Here:\n\n${FENCE}`} />);
  const img = await screen.findByRole("img");
  expect(img).toHaveAttribute("alt", "Flowchart: Release steps. Its source is under Source.");
  expect(img.getAttribute("src")).toMatch(/^data:image\/svg\+xml;charset=utf-8,/);
  expect(img).toHaveAttribute("width", "120");
  expect(asked).toEqual(["flowchart TD\n  a --> b\n"]);
  // A keyboard reaches the picture.
  expect(container.querySelector(".diagram-view")).toHaveAttribute("tabindex", "0");
});

it("shows the source while the diagram is drawn", async () => {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => (release = resolve));
  setDiagramEngine({
    async draw() {
      await held;
      return { tree: TREE, kind: "Flowchart" };
    },
  });
  const { container } = render(<Markdown text={FENCE} />);
  expect(screen.getByText("Drawing the diagram…")).toBeInTheDocument();
  expect(container.querySelector("pre code")).toHaveTextContent("a --> b");
  release();
  await screen.findByRole("img");
  expect(screen.queryByText("Drawing the diagram…")).toBeNull();
});

it("leaves a block that has not closed as its code, and draws nothing", () => {
  const { engine, asked } = counting();
  setDiagramEngine(engine);
  const { container } = render(<Markdown text={"```mermaid\nflowchart TD\n  a -->"} />);
  expect(container.querySelector("pre code")).toHaveTextContent("a -->");
  expect(screen.queryByRole("img")).toBeNull();
  expect(asked).toEqual([]);
});

it("draws each diagram once while the answer is written, and the same one once", async () => {
  const { engine, asked } = counting();
  setDiagramEngine(engine);
  const text = `${FENCE}\n\nAnd again:\n\n${FENCE}\n\nMore`;
  const { rerender } = render(<Markdown text={text} />);
  for (const more of [" words", " words and", " words and more."]) {
    rerender(<Markdown text={text + more} />);
  }
  await waitFor(() => expect(screen.getAllByRole("img")).toHaveLength(2));
  expect(asked).toHaveLength(1);
});

it("keeps a diagram that cannot be drawn as its source, saying why", async () => {
  setDiagramEngine(counting(() => ({ error: "Parse error on line 2: got 'EOF'" })).engine);
  const { container } = render(<Markdown text={FENCE} />);
  expect(
    await screen.findByText("Diagram not shown: Parse error on line 2: got 'EOF'"),
  ).toBeInTheDocument();
  expect(container.querySelector("pre code")).toHaveTextContent("flowchart TD");
});

it("keeps a drawing the page refuses as its source, saying why", async () => {
  setDiagramEngine(
    counting(() => ({
      tree: ["svg", { viewBox: "0 0 1 1" }, [["script", {}, []]]],
      kind: "Flowchart",
    })).engine,
  );
  render(<Markdown text={FENCE} />);
  expect(
    await screen.findByText("Diagram not shown: its drawing has a <script> element"),
  ).toBeInTheDocument();
});

it("says so when this browser cannot draw diagrams", async () => {
  render(<Markdown text={FENCE} />);
  expect(
    await screen.findByText("Diagram not shown: this browser cannot draw diagrams"),
  ).toBeInTheDocument();
});

it("tries again when shown again after the worker, not the diagram, failed", async () => {
  let first = true;
  const asked: string[] = [];
  setDiagramEngine({
    async draw(source) {
      asked.push(source);
      if (first) {
        first = false;
        return { error: "the diagram worker stopped: out of memory", again: true };
      }
      return { tree: TREE, kind: "Flowchart" };
    },
  });
  const { unmount } = render(<Markdown text={FENCE} />);
  await screen.findByText(/the diagram worker stopped/);
  unmount();
  render(<Markdown text={FENCE} />);
  await screen.findByRole("img");
  expect(asked).toHaveLength(2);
});

describe("its tools", () => {
  beforeEach(() => setDiagramEngine(counting().engine));

  it("shows its source, and the picture again", async () => {
    render(<Markdown text={FENCE} />);
    await screen.findByRole("img");
    const source = screen.getByRole("button", { name: "Source" });
    fireEvent.click(source);
    expect(source).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("The diagram's source")).toHaveTextContent("a --> b");
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(source);
    expect(screen.getByRole("img")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the diagram's source" })).toBeInTheDocument();
  });

  it("opens the diagram on its own, to zoom, and closes it", async () => {
    render(<Markdown text={FENCE} />);
    await screen.findByRole("img");
    fireEvent.click(screen.getByRole("button", { name: "Expand the flowchart" }));
    const dialog = document.querySelector("dialog.diagram-dialog") as HTMLDialogElement;
    expect(dialog).toHaveAttribute("aria-label", "Flowchart: Release steps");
    const view = within(dialog).getByRole("img");
    const before = Number(view.getAttribute("width"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Zoom in" }));
    expect(Number(view.getAttribute("width"))).toBeGreaterThan(before);
    // A close that finds it open again (React's development run) is not the person's.
    act(() => {
      dialog.dispatchEvent(new Event("close"));
    });
    expect(document.querySelector("dialog.diagram-dialog")).not.toBeNull();
    act(() => {
      dialog.removeAttribute("open");
      dialog.dispatchEvent(new Event("close"));
    });
    expect(document.querySelector("dialog.diagram-dialog")).toBeNull();
  });

  it("downloads the drawing as SVG", async () => {
    const saved: Blob[] = [];
    const create = vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => {
      saved.push(blob as Blob);
      return "blob:saved";
    });
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      expect(this.download).toBe("release-steps.svg");
    });
    render(<Markdown text={FENCE} />);
    await screen.findByRole("img");
    fireEvent.click(screen.getByRole("button", { name: "Download the diagram as SVG" }));
    expect(click).toHaveBeenCalledTimes(1);
    expect(saved[0]?.type).toBe("image/svg+xml");
    expect(await saved[0]?.text()).toMatch(/^<svg xmlns="http:\/\/www.w3.org\/2000\/svg"/);
    for (const spy of [create, revoke, click]) spy.mockRestore();
  });
});

it("makes a PNG twice the diagram's size, within 4,096 pixels a side and 16.7 million in all", () => {
  expect(pngScale(500, 300)).toBe(2);
  expect(pngScale(4000, 100)).toBeCloseTo(4096 / 4000);
  expect(pngScale(4000, 4000) * 4000).toBeLessThanOrEqual(4096);
  expect(4096 * pngScale(4096, 4096) * 4096 * pngScale(4096, 4096)).toBeLessThanOrEqual(16_777_216);
});

it("names a file by the diagram's title, or its kind", () => {
  const picture = {
    svg: "",
    src: "",
    width: 1,
    height: 1,
    kind: "Sequence diagram",
    description: "",
  };
  expect(fileName({ ...picture, title: "Ask & answer!" })).toBe("ask-answer");
  expect(fileName({ ...picture, title: "" })).toBe("sequence-diagram");
});

it("takes only the page's hex colours for Mermaid's theme", () => {
  const css: Record<string, string> = {
    "--tint": "#2b353d",
    "--panel": "rgb(1 2 3)",
    "--muted": " #b3b9bf ",
  };
  const palette = readPalette((name) => css[name] ?? "");
  expect(palette.primaryColor).toBe("#2b353d");
  expect(palette.lineColor).toBe("#b3b9bf");
  expect(palette.background).toBeUndefined();
});

describe("the diagram worker's keeper", () => {
  class FakeWorker {
    static made: FakeWorker[] = [];
    posted: unknown[] = [];
    onmessage: ((event: MessageEvent) => void) | null = null;
    onerror: ((event: ErrorEvent) => void) | null = null;
    constructor() {
      FakeWorker.made.push(this);
    }
    postMessage(message: unknown): void {
      this.posted.push(message);
    }
    terminate(): void {}
    say(data: unknown): void {
      this.onmessage?.({ data } as MessageEvent);
    }
  }

  beforeEach(() => {
    FakeWorker.made = [];
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it("posts a diagram with its colours, and reads the drawing back", async () => {
    const engine = diagramWorkerEngine(() => new FakeWorker() as unknown as Worker);
    const drawn = engine.draw("pie\n  a: 1", { primaryColor: "#000000" }, true);
    const [worker] = FakeWorker.made;
    worker?.say({ ready: true });
    expect(worker?.posted).toEqual([
      { id: 1, source: "pie\n  a: 1", palette: { primaryColor: "#000000" }, dark: true },
    ]);
    worker?.say({ id: 1, tree: TREE, kind: "Pie chart" });
    await expect(drawn).resolves.toEqual({ tree: TREE, kind: "Pie chart" });
  });

  it("lets nothing an ended worker says reach the next diagram", async () => {
    const engine = diagramWorkerEngine(() => new FakeWorker() as unknown as Worker);
    const slow = engine.draw("slow", {}, false);
    const next = engine.draw("next", {}, false);
    const [old] = FakeWorker.made;
    old?.say({ ready: true });
    vi.advanceTimersByTime(4_000);
    await expect(slow).resolves.toEqual({ error: "it took longer than 4 s to draw" });
    const fresh = FakeWorker.made[1];
    fresh?.say({ ready: true });
    // The ended worker's error, late: the next diagram is unharmed.
    old?.onerror?.({ message: "late", preventDefault: () => undefined } as ErrorEvent);
    old?.say({ id: 2, error: "late" });
    fresh?.say({ id: 2, tree: TREE, kind: "Flowchart" });
    await expect(next).resolves.toEqual({ tree: TREE, kind: "Flowchart" });
  });

  it("stops a diagram that takes longer than 4 s, saying so", async () => {
    const engine = diagramWorkerEngine(() => new FakeWorker() as unknown as Worker);
    const drawn = engine.draw("flowchart TD", {}, false);
    FakeWorker.made[0]?.say({ ready: true });
    vi.advanceTimersByTime(4_000);
    await expect(drawn).resolves.toEqual({ error: "it took longer than 4 s to draw" });
  });
});
