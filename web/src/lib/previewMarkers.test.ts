import {
  markPreviewFinished,
  markPreviewSent,
  markPreviewStarted,
  previewWasUnfinished,
} from "./previewMarkers";

const STORE = "workbench.previews.unfinished";
const FIREFOX = "Mozilla/5.0 Gecko/20100101 Firefox/155.0";
const WEBKIT = "Mozilla/5.0 AppleWebKit/605.1.15 Version/26.6 Safari/605.1.15";

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(FIREFOX);
});

afterEach(() => vi.restoreAllMocks());

it.each([FIREFOX, WEBKIT])("keeps a start until its first run finishes in %s", (agent) => {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(agent);
  markPreviewStarted("busy");
  markPreviewStarted("other");
  expect(previewWasUnfinished("busy")).toBe(true);
  expect(JSON.parse(localStorage.getItem(STORE)!)).toEqual(["busy", "other"]);
  markPreviewFinished("busy");
  expect(previewWasUnfinished("busy")).toBe(false);
  expect(previewWasUnfinished("other")).toBe(true);
});

it("keeps only the 200 newest starts, moving a repeated start to the end", () => {
  for (let i = 0; i < 200; i++) markPreviewStarted(`preview-${i}`);
  markPreviewStarted("preview-0");
  markPreviewStarted("preview-200");
  expect(previewWasUnfinished("preview-0")).toBe(true);
  expect(previewWasUnfinished("preview-1")).toBe(false);
  expect(JSON.parse(localStorage.getItem(STORE)!)).toEqual([
    ...Array.from({ length: 198 }, (_, i) => `preview-${i + 2}`),
    "preview-0",
    "preview-200",
  ]);
});

it("pagehide clears only this page's begun markers", () => {
  localStorage.setItem(STORE, JSON.stringify(["previous-page"]));
  markPreviewStarted("begun");
  window.dispatchEvent(new Event("pagehide"));
  expect(JSON.parse(localStorage.getItem(STORE)!)).toEqual(["previous-page"]);
});

it("sent is memory only and survives pagehide", () => {
  markPreviewStarted("sent");
  const write = vi.spyOn(Storage.prototype, "setItem");
  markPreviewSent("sent");
  window.dispatchEvent(new Event("pagehide"));
  expect(write).not.toHaveBeenCalled();
  expect(previewWasUnfinished("sent")).toBe(true);
  markPreviewFinished("sent");
});

it("a restart keeps sent so pagehide retains the marker", () => {
  markPreviewStarted("retry");
  markPreviewSent("retry");
  markPreviewStarted("retry");
  window.dispatchEvent(new Event("pagehide"));
  expect(previewWasUnfinished("retry")).toBe(true);
  markPreviewFinished("retry");
});

it("finishing forgets the stage so pagehide leaves a later stored marker alone", () => {
  markPreviewStarted("finished");
  markPreviewFinished("finished");
  localStorage.setItem(STORE, JSON.stringify(["finished"]));
  window.dispatchEvent(new Event("pagehide"));
  expect(previewWasUnfinished("finished")).toBe(true);
});

it("does nothing when storage access, reads, or writes are blocked", () => {
  const blocked = () => {
    throw new DOMException("Blocked", "SecurityError");
  };
  for (const access of ["access", "read", "write"]) {
    const mock =
      access === "access"
        ? vi.spyOn(window, "localStorage", "get").mockImplementation(blocked)
        : vi
            .spyOn(Storage.prototype, access === "read" ? "getItem" : "setItem")
            .mockImplementation(blocked);
    expect(() => markPreviewStarted("busy")).not.toThrow();
    expect(previewWasUnfinished("busy")).toBe(false);
    expect(() => markPreviewFinished("busy")).not.toThrow();
    mock.mockRestore();
  }
});

it.each([
  "Mozilla/5.0 AppleWebKit/537.36 Chrome/150.0 Safari/537.36",
  "Mozilla/5.0 AppleWebKit/537.36 Chrome/150.0 Safari/537.36 Edg/150.0",
  "Mozilla/5.0 AppleWebKit/537.36 Chromium/150.0 Safari/537.36",
])("never reads or changes markers in Chromium: %s", (agent) => {
  localStorage.setItem(STORE, JSON.stringify(["busy"]));
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(agent);
  const read = vi.spyOn(Storage.prototype, "getItem");
  const write = vi.spyOn(Storage.prototype, "setItem");
  expect(previewWasUnfinished("busy")).toBe(false);
  markPreviewStarted("other");
  markPreviewSent("other");
  markPreviewFinished("busy");
  window.dispatchEvent(new Event("pagehide"));
  expect(read).not.toHaveBeenCalled();
  expect(write).not.toHaveBeenCalled();
});

it("recovers from malformed storage and bounds older stored lists too", () => {
  for (const raw of ["{", "null", "{}", '[null, 7, "old"]']) {
    localStorage.setItem(STORE, raw);
    expect(previewWasUnfinished("busy")).toBe(false);
    markPreviewStarted("busy");
    expect(previewWasUnfinished("busy")).toBe(true);
  }
  localStorage.setItem(STORE, JSON.stringify(Array.from({ length: 250 }, (_, i) => `${i}`)));
  expect(previewWasUnfinished("0")).toBe(false);
  expect(previewWasUnfinished("249")).toBe(true);
});
