/**
 * What a tool returned beyond its words (rich content RC3): each file fetched
 * with the session when shown and let go after, sounds and videos only when
 * asked for, an address named but never fetched, and what was not kept said.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { releaseAllFileUrls } from "../lib/api";
import { chatMarkdown } from "../lib/conveniences";
import type { ChatDetail, ContentBlock, ToolCall } from "../lib/types";
import { runner } from "../lib/previewRunner";
import { ArtifactScope, type Scope } from "./Artifact";
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
  runner.reset();
  fetched = [];
  made = 0;
  revoked = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      fetched.push(url);
      // Words, not a Blob: Node 22's Response cannot read the page's (jsdom's) Blob.
      return new Response("bytes", { status: 200 });
    }),
  );
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:made-${++made}`);
  vi.spyOn(URL, "revokeObjectURL").mockImplementation((url: string) => {
    revoked.push(url);
  });
});

const artifactScope: Scope = {
  chatId: "c",
  messageId: "m",
  artifacts: [],
  readOnly: false,
  writing: false,
  onKept() {},
};

function toolArtifact(block: ContentBlock, over: Partial<Scope> = {}) {
  return render(
    <ArtifactScope.Provider value={{ ...artifactScope, ...over }}>
      <ToolContent blocks={[block]} tool="pages/fetch_page" />
    </ArtifactScope.Provider>,
  );
}

it("tool_html_runs_inline_and_opens_as_an_artifact_with_its_origin", async () => {
  toolArtifact({
    id: "html",
    type: "text",
    text: '<h1>Page</h1><script>parent.document.title = "escaped"</script>',
  });
  const frame = await screen.findByTestId("artifact-frame");
  expect(frame).toHaveAttribute("sandbox", "allow-scripts allow-forms");
  expect(frame).toHaveAttribute("src", "/preview/frame");
  expect(document.title).not.toBe("escaped");
  expect(screen.getByText(/Revision 1, from fetch_page's result/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Open this HTML as an artifact" }));
  const dialog = await screen.findByTestId("artifact-dialog");
  expect(dialog.querySelector("option")).toHaveTextContent("from fetch_page's result");
});

it("tool_artifact_waits_for_run_when_the_chat_switch_is_off", async () => {
  toolArtifact(
    { id: "html", type: "text", text: "<!doctype html><h1>Page</h1>" },
    { runArtifacts: false },
  );
  const run = await screen.findByRole("button", { name: "Run" });
  await new Promise((done) => setTimeout(done, 50));
  expect(screen.queryByTestId("artifact-frame")).toBeNull();
  fireEvent.click(run);
  expect(await screen.findByTestId("artifact-frame")).toBeInTheDocument();
});

it.each([
  { id: "md", type: "text", text: "# Notes\n\n- one", mimeType: "text/markdown" },
  { id: "resource", type: "text", text: "Plain resource notes.", source: "file:///notes.md" },
] satisfies ContentBlock[])("tool_markdown_is_shown_as_an_artifact: $id", async (block) => {
  toolArtifact(block);
  expect(await screen.findByText(block.id === "md" ? "Notes" : block.text)).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Open this Markdown as an artifact" }),
  ).toBeInTheDocument();
  expect(screen.queryByTestId("artifact-frame")).toBeNull();
});

it("tool_svg_resource_is_a_picture_and_an_artifact", async () => {
  toolArtifact({
    id: "svg",
    type: "text",
    text: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><title>Dot</title><circle r="2"/></svg>',
    source: "file:///dot.svg",
  });
  expect(await screen.findByRole("img", { name: "Dot" })).toHaveAttribute(
    "src",
    expect.stringMatching(/^data:image\/svg\+xml/),
  );
  expect(screen.getByRole("button", { name: "Open this SVG as an artifact" })).toBeInTheDocument();
});

it.each([{ readOnly: true }, { writing: true }])(
  "tool_artifact_does_not_start_in_a_restricted_scope: %j",
  async (over) => {
    toolArtifact({ id: "html", type: "text", text: "<h1>Page</h1>" }, over);
    expect(await screen.findByRole("button", { name: "Run" })).toBeInTheDocument();
    await new Promise((done) => setTimeout(done, 50));
    expect(screen.queryByTestId("artifact-frame")).toBeNull();
  },
);

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
