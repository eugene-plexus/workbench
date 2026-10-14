/**
 * What a tool returned beyond its words (rich content RC3): each file fetched
 * with the session when shown and let go after, sounds and videos only when
 * asked for, an address named but never fetched, and what was not kept said.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { releaseAllFileUrls } from "../lib/api";
import { chatMarkdown } from "../lib/conveniences";
import type { ChatDetail, ContentBlock, ToolCall } from "../lib/types";
import { sizeWords, ToolContent } from "./ToolContent";

const PICTURE: ContentBlock = {
  id: "c:1",
  type: "image",
  fileId: "f-pic",
  name: "chart.png",
  mediaType: "image/png",
  size: 2048,
  width: 30,
  height: 20,
};
const SOUND: ContentBlock = {
  id: "c:2",
  type: "audio",
  fileId: "f-snd",
  name: "audio.wav",
  mediaType: "audio/wav",
  size: 800,
};
const PAGE: ContentBlock = {
  id: "c:3",
  type: "file",
  fileId: "f-page",
  name: "report.html",
  mediaType: "application/octet-stream",
  size: 41,
  declaredType: "text/html",
};
const BLOCKS: ContentBlock[] = [
  { id: "c:0", type: "text", text: "Here is the chart." },
  PICTURE,
  SOUND,
  PAGE,
  { id: "c:4", type: "link", uri: "https://example.com/a", name: "elsewhere" },
  { id: "c:5", type: "link", uri: "file:///etc/passwd", name: "a local file" },
  { id: "c:6", type: "unsupported", what: "a picture", reason: "it is larger than 5 MiB" },
  { id: "c:data", type: "data", data: { rows: 2 } },
];

let fetched: string[];
let made: number;
let revoked: string[];

beforeEach(() => {
  fetched = [];
  made = 0;
  revoked = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      fetched.push(url);
      return new Response(new Blob(["bytes"]), { status: 200 });
    }),
  );
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:made-${++made}`);
  vi.spyOn(URL, "revokeObjectURL").mockImplementation((url: string) => {
    revoked.push(url);
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("shows a picture fetched from Workbench, named, and lets it go when it is no longer shown", async () => {
  const { unmount } = render(<ToolContent blocks={BLOCKS} />);
  const picture = await screen.findByRole("img", { name: "chart.png" });
  expect(picture).toHaveAttribute("src", "blob:made-1");
  expect(fetched).toEqual(["/api/files/f-pic"]);
  expect(screen.getByText("image/png, 2 KB")).toBeInTheDocument();
  unmount();
  expect(revoked).toContain("blob:made-1");
});

it("loads a sound only when asked, and never plays it by itself", async () => {
  const { container } = render(<ToolContent blocks={[SOUND]} />);
  expect(fetched).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Load the sound audio.wav" }));
  await waitFor(() => expect(container.querySelector("audio")).not.toBeNull());
  const audio = container.querySelector("audio");
  expect(audio).toHaveAttribute("controls");
  expect(audio).not.toHaveAttribute("autoplay");
  expect(fetched).toEqual(["/api/files/f-snd"]);
});

it("offers a file the bytes did not bear out only to download, saying what it claimed", () => {
  render(<ToolContent blocks={[PAGE]} />);
  expect(
    screen.getByText("application/octet-stream, 41 bytes, said to be text/html"),
  ).toBeVisible();
  expect(screen.getByRole("button", { name: "Download report.html" })).toBeInTheDocument();
  expect(screen.queryByRole("img")).toBeNull();
  expect(fetched).toEqual([]);
});

it("downloads a file under its own name", async () => {
  const names: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    names.push(this.download);
  });
  render(<ToolContent blocks={[PAGE]} />);
  fireEvent.click(screen.getByRole("button", { name: "Download report.html" }));
  await waitFor(() => expect(names).toEqual(["report.html"]));
  expect(fetched).toEqual(["/api/files/f-page"]);
});

it("names an address without fetching it, and links only a web one", async () => {
  render(<ToolContent blocks={BLOCKS} />);
  await screen.findByRole("img", { name: "chart.png" });
  const web = screen.getByRole("link", { name: "elsewhere" });
  expect(web).toHaveAttribute("href", "https://example.com/a");
  expect(web).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.queryByRole("link", { name: "a local file" })).toBeNull();
  expect(screen.getByText("file:///etc/passwd")).toBeInTheDocument();
  expect(fetched.every((url) => url.startsWith("/api/files/"))).toBe(true);
});

it("says what was not kept and why, and keeps the data a click away", () => {
  render(<ToolContent blocks={BLOCKS} />);
  expect(screen.getByText("Not kept: a picture. It is larger than 5 MiB.")).toBeInTheDocument();
  expect(screen.getByText("Data")).toBeInTheDocument();
  expect(screen.getByText(/"rows": 2/)).toBeInTheDocument();
});

it("shows nothing for a result of words alone", () => {
  const { container } = render(<ToolContent blocks={[BLOCKS[0] as ContentBlock]} />);
  expect(container).toBeEmptyDOMElement();
});

it("lets every file's address go when the person signs out", async () => {
  render(<ToolContent blocks={[PICTURE]} />);
  await screen.findByRole("img", { name: "chart.png" });
  releaseAllFileUrls();
  expect(revoked).toContain("blob:made-1");
});

it("puts sizes as a person reads them", () => {
  expect([sizeWords(41), sizeWords(2048), sizeWords(5 * 1024 * 1024)]).toEqual([
    "41 bytes",
    "2 KB",
    "5 MB",
  ]);
});

it("exports a call's files and addresses by name, never by Workbench's address", () => {
  const call: ToolCall = {
    id: "call-1",
    serverId: "s",
    serverName: "Charts",
    tool: "plot",
    arguments: {},
    status: "done",
    result: "Here is the chart.",
    content: BLOCKS,
    contentVersion: 1,
  };
  const detail = {
    chat: { id: "c", title: "Charts" },
    messages: [
      {
        id: "m",
        role: "assistant",
        content: "Done.",
        reasoning: "",
        sources: [],
        status: "done",
        createdAt: 1,
        toolRounds: [{ calls: [call] }],
      },
    ],
  } as unknown as ChatDetail;
  const text = chatMarkdown(detail);
  expect(text).toContain("- Picture: chart.png (image/png, 2048 bytes)");
  expect(text).toContain("- Address named: elsewhere https://example.com/a");
  expect(text).toContain("- Not kept: a picture, it is larger than 5 MiB");
  expect(text).not.toContain("f-pic");
});
