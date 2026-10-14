/**
 * The diagram worker: Mermaid, off the page, so a diagram that takes too
 * long is stopped by ending the worker (diagramClient.ts) and the chat never
 * waits on it. It draws into a stand-in DOM (diagramDom.ts) whose text is
 * measured here with the browser's own fonts, says when it is ready, then
 * answers one diagram at a time with a tree the page reads through its
 * allowlist (diagramMarkup.ts).
 */

import { install, type Font, type Metrics } from "./diagramDom";

type Job = { id: number; source: string; palette: Record<string, string>; dark: boolean };

type Scope = {
  postMessage(message: unknown): void;
  onmessage: ((event: MessageEvent<Job>) => void) | null;
};

const scope = self as unknown as Scope;
const canvas = new OffscreenCanvas(1, 1).getContext("2d");

function measure(text: string, font: Font): Metrics {
  if (!canvas)
    return {
      width: text.length * font.size * 0.55,
      ascent: font.size * 0.9,
      descent: font.size * 0.22,
    };
  canvas.font = `${font.style} ${font.weight} ${font.size}px ${font.family}`;
  const m = canvas.measureText(text);
  return {
    width: m.width,
    ascent: m.fontBoundingBoxAscent ?? font.size * 0.9,
    descent: m.fontBoundingBoxDescent ?? font.size * 0.22,
  };
}

install(measure);
// After `install`: Mermaid's sanitizer looks for a window as it loads.
const { draw, reason, warm } = await import("./diagramRender");
await warm();

scope.onmessage = async (event) => {
  const { id, source, palette, dark } = event.data;
  let result;
  try {
    result = await draw(String(source), palette ?? {}, Boolean(dark));
  } catch (error) {
    result = { error: reason(error) };
  }
  scope.postMessage({ id, ...result });
};
scope.postMessage({ ready: true });
