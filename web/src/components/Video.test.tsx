import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { api, post } from "../lib/api";
import type { MediaItem, VideoModel } from "../lib/media";
import type { Me } from "../lib/types";
import { Media } from "./Media";

vi.mock("../lib/api", () => ({
  api: vi.fn(),
  post: vi.fn(),
  del: vi.fn(),
  fileUrl: vi.fn(() => Promise.resolve("blob:x")),
}));
vi.mock("../lib/events", () => ({ watchStream: () => ({ close: () => undefined }) }));

const me: Me = { sub: "p-ada", name: "Ada", username: "ada", owner: false, ownerReadsChats: false };
/** grok-imagine-video as the gateway lists it (§6.4, measured 2026-10-08). */
const grok: VideoModel = {
  id: "x-ai/grok-imagine-video",
  account: "openrouter",
  provider: "OpenRouter",
  locality: "external",
  ready: true,
  onDemand: false,
  durations: [1, 2, 3, 12, 15],
  sizes: ["1280x720", "480x854", "854x480"],
  firstFrame: true,
  prices: [
    { sku: "cents_per_image_input", per: "input_image", usd: 0.002 },
    {
      sku: "cents_per_video_output_second_480p",
      per: "second",
      usd: 0.05,
      resolution: "480p",
      sizes: ["480x854", "854x480"],
    },
    {
      sku: "cents_per_video_output_second_720p",
      per: "second",
      usd: 0.07,
      resolution: "720p",
      sizes: ["1280x720"],
    },
  ],
};
const seedance: VideoModel = {
  ...grok,
  id: "bytedance/seedance",
  durations: [4, 5],
  sizes: null,
  firstFrame: false,
  prices: null,
};
const running: MediaItem = {
  id: "v1",
  door: "video",
  kind: "made",
  model: grok.id,
  request: { model: grok.id, prompt: "a red ball", seconds: 1, size: "854x480" },
  status: "running",
  createdAt: Date.now() / 1000 - 18,
  finishedAt: null,
  served: { driver: "openrouter", latency_ms: 3250, attempts: 1 },
  units: null,
  text: null,
  error: null,
  job: { status: "queued", progress: 0, polls: 3, polledAt: 0, problem: null },
  files: [],
};

function serve(models: VideoModel[], items: MediaItem[] = []) {
  vi.mocked(api).mockImplementation((path: string) =>
    Promise.resolve(
      path === "/api/media/doors"
        ? { doors: { video: { models } } }
        : { items: path.includes("door=video") ? items : [], bytes: 0 },
    ),
  );
}

const show = () =>
  render(
    <Media
      me={me}
      door="video"
      focus={null}
      person={null}
      onClose={() => undefined}
      onDoor={() => undefined}
      onToChat={() => undefined}
      onTextToChat={() => undefined}
    />,
  );

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

it("says why there is nothing to make videos with", async () => {
  serve([]);
  show();
  expect(await screen.findByText("No model here makes videos yet.")).toBeInTheDocument();
  expect(
    screen.getByText("Video models that run on your own machines are coming later."),
  ).toBeInTheDocument();
});

it("asks first, with the price from the listing, and sends only when told to", async () => {
  serve([grok, seedance]);
  vi.mocked(post).mockResolvedValue(running);
  show();
  const picker = await screen.findByTestId("video-model");
  expect(picker).toHaveValue("");
  fireEvent.change(picker, { target: { value: grok.id } });
  // The least the model asks: its shortest length and its smallest size.
  expect(screen.getByTestId("video-seconds")).toHaveValue("1");
  expect(screen.getByTestId("video-size")).toHaveValue("854x480");
  fireEvent.change(screen.getByTestId("video-prompt"), { target: { value: "a red ball" } });
  fireEvent.change(screen.getByTestId("video-seconds"), { target: { value: "12" } });
  fireEvent.click(screen.getByTestId("make-video"));
  expect(screen.getByTestId("video-quote")).toHaveTextContent(
    "12 s at 480p. About $0.60, billed to OpenRouter.",
  );
  expect(post).not.toHaveBeenCalled();
  // A change asks again, so the price asked about is what is sent.
  fireEvent.change(screen.getByTestId("video-size"), { target: { value: "1280x720" } });
  expect(screen.queryByTestId("video-quote")).toBeNull();
  fireEvent.click(screen.getByTestId("make-video"));
  expect(screen.getByTestId("video-quote")).toHaveTextContent("12 s at 720p. About $0.84");
  fireEvent.click(screen.getByTestId("confirm-video"));
  await waitFor(() =>
    expect(post).toHaveBeenCalledWith("/api/media/video", {
      model: grok.id,
      prompt: "a red ball",
      seconds: 12,
      size: "1280x720",
    }),
  );
});

it("says when no price is listed, never that it is free", async () => {
  serve([seedance]);
  show();
  fireEvent.change(await screen.findByTestId("video-model"), { target: { value: seedance.id } });
  expect(screen.getByText("This model chooses its own size.")).toBeInTheDocument();
  expect(screen.queryByTestId("video-frame")).toBeNull();
  fireEvent.change(screen.getByTestId("video-prompt"), { target: { value: "waves" } });
  fireEvent.click(screen.getByTestId("make-video"));
  expect(screen.getByTestId("video-quote")).toHaveTextContent(
    "4 s, at the model's own size. No price is listed for this; OpenRouter bills it to that account.",
  );
});

it("lists long jobs with how long each has run, and what a finished one cost", async () => {
  const finished: MediaItem = {
    ...running,
    id: "v2",
    status: "done",
    units: { seconds: 1, size: "854x480", costUsd: 0.05 },
    job: null,
    files: [
      { id: "f1", name: "video.mp4", mediaType: "video/mp4", size: 9, width: null, height: null },
    ],
  };
  const stopped: MediaItem = { ...running, id: "v3", status: "stopped", job: null };
  serve([grok], [running, finished, stopped]);
  show();
  expect(
    await screen.findByRole("heading", { name: "Work orders · Long jobs" }),
  ).toBeInTheDocument();
  const statuses = await screen.findAllByTestId("media-status");
  expect(statuses[0]).toHaveTextContent(/^Working, 1[89] s so far\./);
  expect(screen.getByTestId("billed-words")).toHaveTextContent(
    "The provider billed $0.05 for this.",
  );
  expect(await screen.findByTestId("bin-video")).toBeInTheDocument();
  expect(statuses[1]).toHaveTextContent("Eugene cannot cancel a video job");
  // A video is sent again only through the form, which asks first.
  expect(screen.queryByRole("button", { name: /Again/ })).toBeNull();
  expect(screen.getAllByRole("button", { name: "Edit and send" })).toHaveLength(2);
  expect(screen.queryByRole("button", { name: /Send to a chat/ })).toBeNull();
});
