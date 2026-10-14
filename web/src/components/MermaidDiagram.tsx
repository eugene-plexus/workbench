/**
 * A mermaid code block in an answer (workbench-rich-content-roadmap.md RC2): its
 * source while it is drawn, then the diagram as a picture, with its source,
 * Copy, a larger view and downloads. One that cannot be drawn stays its
 * source, saying why, and the answer around it is untouched.
 *
 * The picture is an `<img>` of an SVG the page wrote itself from what the
 * worker drew (lib/diagramMarkup.ts): it runs nothing and fetches nothing.
 */

import { Code, Download, Maximize2, X, ZoomIn, ZoomOut } from "lucide-react";
import { Component, memo, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useDiagram, type Picture } from "../lib/diagramClient";
import { Block } from "./Block";
import { CopyButton } from "./CopyButton";

/** A diagram that fails while it is shown is its source, not a blank answer. */
class DiagramBoundary extends Component<
  { fallback: ReactNode; children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** A PNG is at most this many pixels a side, and this many in all. */
export const PNG_SIDE = 4_096;
export const PNG_PIXELS = 16_777_216;

/** How much a PNG is scaled: twice the diagram's size, unless that passes the limits. */
export function pngScale(width: number, height: number): number {
  return Math.min(2, PNG_SIDE / Math.max(width, height), Math.sqrt(PNG_PIXELS / (width * height)));
}

/** A file name from the diagram's title, or its kind. */
export function fileName(picture: Picture): string {
  const words = (picture.title || picture.kind)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
  return words || "diagram";
}

function save(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

async function savePng(picture: Picture): Promise<void> {
  const image = new Image();
  image.src = picture.src;
  await image.decode();
  const scale = pngScale(picture.width, picture.height);
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.floor(picture.width * scale));
  canvas.height = Math.max(1, Math.floor(picture.height * scale));
  const context = canvas.getContext("2d");
  if (!context) throw new Error("this browser could not draw it");
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, "image/png"));
  if (!blob) throw new Error("this browser could not make it");
  save(blob, `${fileName(picture)}.png`);
}

const ZOOMS = [0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4];

/** The diagram on its own, as large as the window allows, to zoom and scroll. */
function DiagramDialog({
  picture,
  label,
  onClose,
}: {
  picture: Picture;
  label: string;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  // At first the whole diagram, at its own size or less, but no smaller than
  // half: a long one is read by scrolling, not squinting (Zoom out shows all).
  const [zoom, setZoom] = useState(() =>
    Math.max(
      0.5,
      Math.min(
        1,
        (window.innerWidth - 64) / picture.width,
        (window.innerHeight - 128) / picture.height,
      ),
    ),
  );
  useEffect(() => {
    const element = dialog.current;
    if (element && !element.open) {
      if (typeof element.showModal === "function") element.showModal();
      else element.setAttribute("open", "");
    }
    // The way out is the first stop, as Escape is.
    close.current?.focus();
    return () => {
      if (element?.open && typeof element.close === "function") element.close();
    };
  }, []);
  const smaller = [...ZOOMS].reverse().find((z) => z < zoom - 0.001);
  const larger = ZOOMS.find((z) => z > zoom + 0.001);
  return (
    <dialog
      ref={dialog}
      className="diagram-dialog"
      aria-label={label}
      // React's development run closes and opens it again at once; a close
      // that finds it open again was not the person's.
      onClose={() => {
        if (!dialog.current?.open) onClose();
      }}
    >
      <div className="diagram-dialog-head">
        <span className="diagram-dialog-title">{label}</span>
        <span className="diagram-tools">
          <button
            type="button"
            className="act"
            aria-label="Zoom out"
            disabled={smaller === undefined}
            onClick={() => smaller !== undefined && setZoom(smaller)}
          >
            <ZoomOut size={16} aria-hidden="true" />
          </button>
          <span className="diagram-zoom" aria-live="polite">
            {Math.round(zoom * 100)}%
          </span>
          <button
            type="button"
            className="act"
            aria-label="Zoom in"
            disabled={larger === undefined}
            onClick={() => larger !== undefined && setZoom(larger)}
          >
            <ZoomIn size={16} aria-hidden="true" />
          </button>
          <button ref={close} type="button" className="act" onClick={() => dialog.current?.close()}>
            <X size={16} aria-hidden="true" />
            Close
          </button>
        </span>
      </div>
      {/* A focusable scroller, so a keyboard can move around a large diagram. */}
      <div className="diagram-dialog-body" tabIndex={0} aria-label={`${label}, scrolls`}>
        <img
          src={picture.src}
          width={Math.round(picture.width * zoom)}
          height={Math.round(picture.height * zoom)}
          alt={label}
        />
      </div>
    </dialog>
  );
}

function Shown({ picture, source }: { picture: Picture; source: string }) {
  const [showSource, setShowSource] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [problem, setProblem] = useState("");
  const described = useId();
  const label = picture.title ? `${picture.kind}: ${picture.title}` : picture.kind;
  return (
    <div className="code-block diagram-block">
      <div className="code-head">
        <span className="code-lang">mermaid</span>
        <span className="diagram-tools">
          <button
            type="button"
            className="act"
            aria-pressed={showSource}
            onClick={() => setShowSource((shown) => !shown)}
          >
            <Code size={14} aria-hidden="true" />
            <span className="diagram-tool-label">Source</span>
          </button>
          <CopyButton text={source} label="the diagram's source" />
          <button
            type="button"
            className="act"
            aria-haspopup="dialog"
            aria-label={`Expand the ${picture.kind.toLowerCase()}`}
            onClick={() => setExpanded(true)}
          >
            <Maximize2 size={14} aria-hidden="true" />
            <span className="diagram-tool-label">Expand</span>
          </button>
          <button
            type="button"
            className="act"
            aria-label="Download the diagram as SVG"
            onClick={() => {
              setProblem("");
              save(new Blob([picture.svg], { type: "image/svg+xml" }), `${fileName(picture)}.svg`);
            }}
          >
            <Download size={14} aria-hidden="true" />
            <span className="diagram-tool-label">SVG</span>
          </button>
          <button
            type="button"
            className="act"
            aria-label="Download the diagram as PNG"
            onClick={() => {
              setProblem("");
              savePng(picture).catch((error: unknown) =>
                setProblem(
                  `The PNG could not be made: ${error instanceof Error ? error.message : String(error)}`,
                ),
              );
            }}
          >
            <Download size={14} aria-hidden="true" />
            <span className="diagram-tool-label">PNG</span>
          </button>
        </span>
      </div>
      {showSource ? (
        <pre tabIndex={0} aria-label="The diagram's source">
          <code className="language-mermaid">{source}</code>
        </pre>
      ) : (
        // A focusable box: a keyboard reaches the diagram, and its name says what it is.
        <div className="diagram-view" tabIndex={0} role="group" aria-label={label}>
          <img
            src={picture.src}
            width={picture.width}
            height={picture.height}
            alt={`${label}. Its source is under Source.`}
            aria-describedby={picture.description ? described : undefined}
          />
          {picture.description && (
            <span id={described} className="sr-only">
              {picture.description}
            </span>
          )}
        </div>
      )}
      {problem && (
        <p className="code-note" role="alert">
          {problem}
        </p>
      )}
      {expanded && (
        <DiagramDialog picture={picture} label={label} onClose={() => setExpanded(false)} />
      )}
    </div>
  );
}

/** The source as a code block, with a note under it. */
function sourceBlock(source: string, note: string): ReactNode {
  return <Block language="mermaid" source={source} open={false} note={note} />;
}

/** The nearest box that scrolls `element`: the chat's own scroller. */
function scrollerOf(element: HTMLElement): HTMLElement | null {
  for (let at = element.parentElement; at; at = at.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(at).overflowY)) return at;
  }
  return null;
}

/**
 * Whether the block is on screen or near it (within one and a half screens
 * of the chat's scroller), once and for good. A diagram far away is not
 * drawn yet: a long chat, or an answer with many slow diagrams, does not
 * fill the worker's queue ahead of the one being read.
 */
function useNear(): [boolean, (element: HTMLDivElement | null) => void] {
  const [near, setNear] = useState(() => typeof IntersectionObserver === "undefined");
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (near || !element) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setNear(true);
          observer.disconnect();
        }
      },
      { root: scrollerOf(element), rootMargin: "150% 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [near, element]);
  return [near, setElement];
}

function Drawn({ source }: { source: string }) {
  const [near, place] = useNear();
  const shown = useDiagram(source, near);
  // A picture already shown stays while the theme's is drawn (a dialog open
  // on it stays open).
  const last = useRef<Picture | null>(null);
  if (shown.state === "done") last.current = shown.picture;
  const picture =
    shown.state === "done" ? shown.picture : shown.state === "pending" ? last.current : null;
  let body: ReactNode;
  if (picture) {
    body = <Shown picture={picture} source={source} />;
  } else if (shown.state === "failed") {
    body = sourceBlock(source, `Diagram not shown: ${shown.reason}`);
  } else {
    body = sourceBlock(source, near ? "Drawing the diagram…" : "");
  }
  return (
    <div className="diagram-slot" ref={place}>
      {body}
    </div>
  );
}

/** Memoised on its source: a streamed answer renders on every token. */
export const MermaidDiagram = memo(function MermaidDiagram({ source }: { source: string }) {
  return (
    <DiagramBoundary fallback={sourceBlock(source, "Diagram not shown: it could not be drawn")}>
      <Drawn source={source} />
    </DiagramBoundary>
  );
});
